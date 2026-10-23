"""From answers to files (BUILD_PROMPT §18.3): plan, review, write.

The TUI's Review screen shows a :class:`Plan` (every file, a diff for modified
ones, only secret names for the vault) and writes it on Install; ``bootstrap.sh
--answers`` writes it without asking. Nothing is written before
:meth:`Plan.write`.
"""

from __future__ import annotations

import difflib
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ruamel.yaml import YAML

from pinka_installer import answers as answers_mod
from pinka_installer import groupvars, hostvars, keys, release, validators, vault
from pinka_installer.answers import Answers, Context
from pinka_installer.paths import Paths, write_file


@dataclass
class Change:
    """One file the installer writes.

    Attributes:
        path: The file.
        old: Current content, or None for a new file.
        new: Content to write.
        mode: File mode.
        secret: The content is encrypted; the review lists secret names only.
        summary: For secret files, what changes (names, never values).
        chown: Give the file to the invoking user (False for root-only files).
    """

    path: Path
    old: str | None
    new: str
    mode: int = 0o644
    secret: bool = False
    summary: list[str] = field(default_factory=list)
    chown: bool = True

    @property
    def changed(self) -> bool:
        """Whether writing changes the file."""
        return self.old != self.new

    def diff(self, root: Path) -> str:
        """A unified diff for review (secret files: the summary).

        Args:
            root: Paths are shown relative to it.

        Returns:
            Diff text.
        """
        name = (
            str(self.path.relative_to(root)) if self.path.is_relative_to(root) else str(self.path)
        )
        if self.secret:
            return "\n".join([f"{name} (encrypted)", *self.summary]) + "\n"
        return "".join(
            difflib.unified_diff(
                (self.old or "").splitlines(keepends=True),
                self.new.splitlines(keepends=True),
                fromfile=f"a/{name}" if self.old is not None else "/dev/null",
                tofile=f"b/{name}",
            )
        )


@dataclass
class Plan:
    """Every file of one installation.

    Attributes:
        changes: The files.
        warnings: Things the operator should know (missing key files, ...).
        shown_once: Generated secrets to show once (name, value).
    """

    changes: list[Change]
    warnings: list[str] = field(default_factory=list)
    shown_once: list[tuple[str, str]] = field(default_factory=list, repr=False)

    def write(self) -> list[Path]:
        """Write every changed file.

        Returns:
            The files written.
        """
        written = []
        for change in self.changes:
            if change.changed:
                write_file(change.path, change.new, change.mode, chown=change.chown)
                written.append(change.path)
        return written


def context(
    paths: Paths, cpu_count: int | None = None, interfaces: frozenset[str] | None = None
) -> Context:
    """What the cross-field checks need, read from the repository.

    Args:
        paths: Installer paths.
        cpu_count: CPUs of the host, if known.
        interfaces: Network interfaces of the host, if known.

    Returns:
        The context.
    """
    safe = YAML(typ="safe")
    lock = safe.load(paths.models_lock.read_text(encoding="utf-8")) or {}
    model_ids = frozenset(str(m["id"]) for m in lock.get("models") or [] if "id" in m)
    profiles = frozenset(p.stem for p in paths.vllm_profiles.glob("*.yml"))
    blueprints = frozenset(
        p.parent.name for p in paths.images_dir.glob("*/image.yaml") if p.parent.is_dir()
    )
    group_vars = safe.load(paths.group_vars_main.read_text(encoding="utf-8")) or {}
    accounts = frozenset(str(a["name"]) for a in group_vars.get("infra_accounts") or [])
    return Context(
        model_ids=model_ids,
        vllm_profiles=profiles,
        blueprints=blueprints,
        service_accounts=accounts,
        cpu_count=cpu_count,
        interfaces=interfaces,
    )


CPU_SYSFS = Path("/sys/devices/system/cpu")


def read_smt_cores(root: Path = CPU_SYSFS) -> list[frozenset[int]]:
    """The cores with more than one thread, from ``topology/thread_siblings_list``.

    Args:
        root: ``/sys/devices/system/cpu`` (tests: a copy).

    Returns:
        The CPUs of each such core; empty when the topology cannot be read.
    """
    cores: set[frozenset[int]] = set()
    for path in sorted(root.glob("cpu[0-9]*/topology/thread_siblings_list")):
        try:
            siblings = frozenset(validators.parse_cpuset(path.read_text(encoding="utf-8").strip()))
        except (OSError, ValueError):
            continue
        if len(siblings) > 1:
            cores.add(siblings)
    return sorted(cores, key=min)


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def plan(
    answers: Answers,
    paths: Paths,
    provided: vault.Provided,
    vault_password: str,
    public_keys: Mapping[str, keys.KeyCheck] | None = None,
    *,
    smt_cores: Sequence[Iterable[int]] = (),
    needs: release.Needs = release.Needs(),  # noqa: B008 - a frozen dataclass
) -> Plan:
    """Plan the files of the answers.

    Args:
        answers: Checked answers.
        paths: Installer paths.
        provided: Secrets entered in the TUI or passed in the environment.
        vault_password: Password of the vault (new or existing).
        public_keys: Researchers' keys entered in the TUI (headless: none; the
            existing ``users/<name>.pub`` files are used).
        smt_cores: The host's cores with more than one thread
            (:func:`read_smt_cores`): a warning when the CPU sets split one.
        needs: The platform release's database roles and secrets (its inspection).

    Returns:
        The plan.

    Raises:
        vault.VaultError: For a wrong vault password or a missing provided secret.
    """
    machine = answers.machine.name
    changes: list[Change] = []
    warnings: list[str] = []
    resources = answers.resources
    split = validators.smt_split(resources.cpuset_services, resources.cpuset_targets, smt_cores)
    if split:
        warnings.append(split)
    host_vars_path = paths.host_vars(machine)
    current = _read(host_vars_path)
    example = paths.example_host_vars.read_text(encoding="utf-8")
    changes.append(Change(host_vars_path, current, hostvars.render(answers, current, example)))
    group_text = paths.group_vars_main.read_text(encoding="utf-8")
    changes.append(
        Change(
            paths.group_vars_main,
            group_text,
            groupvars.render(group_text, answers.researchers, answers.researchers_removed),
        )
    )
    for researcher in answers.researchers:
        check = (public_keys or {}).get(researcher.name)
        key_path = paths.public_keys(researcher.name)
        if check is not None and check.keys:
            changes.append(
                Change(key_path, _read(key_path), keys.render(researcher.name, check.keys))
            )
        elif not key_path.exists():
            warnings.append(
                f"no SSH key file users/{researcher.name}.pub: {researcher.name} gets no SSH access"
            )
    vault_file, shown_once = _vault_change(
        answers, paths, provided, vault_password, warnings, needs=needs
    )
    changes.append(vault_file)
    answers_path = paths.answers(machine)
    changes.append(Change(answers_path, _read(answers_path), answers_mod.dumps(answers)))
    if answers.secrets.store_vault_password:
        stored = _read(paths.stored_vault_pass)
        changes.append(
            Change(paths.stored_vault_pass, stored, vault_password + "\n", 0o600, True, chown=False)
        )
    return Plan(changes, warnings, shown_once)


def _vault_change(
    answers: Answers,
    paths: Paths,
    provided: vault.Provided,
    password: str,
    warnings: list[str],
    *,
    needs: release.Needs,
) -> tuple[Change, list[tuple[str, str]]]:
    current = _read(paths.vault)
    existing = vault.decrypt(current, password) if current is not None else None
    roles = [*vault.postgres_roles(paths.postgres_defaults), *needs.roles]
    built = vault.build(answers, existing, provided, roles, platform_secrets=needs.secrets)
    if existing is not None and existing == built.data:
        text = current or ""
    else:
        text = vault.encrypt(built.data, password)
    before = set(vault.keys_of(existing or {}))
    after = vault.keys_of(built.data)
    summary = [f"+ {name} (generated)" for name in built.generated]
    summary += [f"+ {name}" for name in after if name not in before and name not in built.generated]
    summary += [f"- {name}" for name in sorted(before - set(after))]
    if not summary:
        summary = ["no secret changes"]
    if answers.backups.repository.type == "later":
        warnings.append("no backup repository yet: doctor warns until one is configured")
    return Change(paths.vault, current, text, 0o600, True, summary), built.shown_once


def read_password_file(path: Path) -> str:
    """Read a vault password file like Ansible does (first line, no newline).

    Args:
        path: The file.

    Returns:
        The password.

    Raises:
        vault.VaultError: If the file is empty or readable by others.
    """
    mode = path.stat().st_mode
    if mode & 0o077 and not str(path).startswith("/dev/fd/"):
        raise vault.VaultError(f"{path} must not be readable by group or others (chmod 600)")
    password = path.read_text(encoding="utf-8").splitlines()[0] if path.stat().st_size else ""
    if not password:
        raise vault.VaultError(f"{path} is empty")
    return password


def environment_secrets(platform_secrets: Sequence[str] = ()) -> vault.Provided:
    """The provided secrets of a non-interactive run.

    Args:
        platform_secrets: ``<service>.<name>`` of the secrets the platform's
            manifest names.

    Returns:
        ``PINKA_SECRET_<NAME>`` values.
    """
    return vault.Provided.from_environment(os.environ, platform_secrets)
