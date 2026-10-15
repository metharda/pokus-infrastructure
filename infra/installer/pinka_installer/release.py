"""The platform release of the answers, inspected with the platform tool (BUILD_PROMPT §18.4).

Screen 12 knows only the release's source at first: :func:`inspect` with
``full=False`` fetches and validates the release alone and reports what it
needs (the content packages to name sources for, the secrets the manifest
names, each service's config schema and the vault keys). With every answer
given, ``full=True`` runs the deployment's own checks (``deploy prepare
--inspect``: the content packages, the settings against their schemas, the
one-platform rule) and gives the summary of the Platform review screen.

Both run the platform tool's code (``infra/tools/platform``, the same rules
as bootstrap's platform role) with the settings the role would write: the
host's read-only deploy key for SSH sources, the pinned host keys (GitHub's
from ``versions.lock.yml`` and the answers' own lines) and the allowed
signers, both written to a root-only temporary directory. They fetch into the
role's root-only mirrors and staging directory and delete what they staged;
no release code runs and nothing else on the host changes.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from pinka_installer.answers import Answers
from pinka_platform import contract as contract_mod
from pinka_platform import deploy, mirror, names, sources

# The platform role's read-only deploy key (ansible/roles/platform/vars/main.yml).
DEPLOY_KEY = Path("/var/lib/pinka/git/deploy_key")
POSTGRES_PREFIX = "vault_postgres_passwords."
SECRETS_PREFIX = "vault_platform_secrets."


@dataclass(frozen=True)
class Needs:
    """What the release needs from the vault.

    Attributes:
        roles: Database roles that get a generated password.
        secrets: ``<service>.<name>`` of the secrets the manifest names.
    """

    roles: tuple[str, ...] = ()
    secrets: tuple[str, ...] = ()


@dataclass
class Inspection:
    """The outcome of an inspection.

    Attributes:
        ok: The release passed.
        found: The platform tool's report (release, summary, content,
            secrets, config, vault_keys, warnings).
        problems: Why it was refused, one line each.
        error: Why it could not be inspected at all (no access, no key).
    """

    ok: bool
    found: dict[str, Any] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def needs(self) -> Needs:
        """The vault keys the release needs."""
        needed = [str(k) for k in (self.found.get("vault_keys") or {}).get("needed", [])]
        return Needs(
            roles=tuple(k[len(POSTGRES_PREFIX) :] for k in needed if k.startswith(POSTGRES_PREFIX)),
            secrets=tuple(k[len(SECRETS_PREFIX) :] for k in needed if k.startswith(SECRETS_PREFIX)),
        )


def ssh_repos(answers: Answers) -> list[str]:
    """The answers' SSH sources (they need the deploy key and pinned host keys).

    Args:
        answers: The answers.

    Returns:
        Repository URLs.
    """
    repos = [answers.platform.repo, *(s.repo for s in answers.content.values())]
    return [r for r in repos if r and sources.transport(r) == "ssh"]


def github_known_hosts(versions_lock: Path) -> list[str]:
    """GitHub's pinned host keys as known_hosts lines (only verified ones).

    Args:
        versions_lock: ``versions.lock.yml``.

    Returns:
        ``github.com <type> <key>`` lines.
    """
    lock = YAML(typ="safe").load(versions_lock.read_text(encoding="utf-8")) or {}
    keys = (lock.get("github_ssh_host_keys") or {}).get("keys") or {}
    return [
        f"github.com {kind} {entry['public_key']}"
        for kind, entry in keys.items()
        if isinstance(entry, Mapping) and "TODO" not in str(entry.get("public_key", "TODO"))
    ]


def _ssh_host(repo: str) -> str:
    if repo.startswith("ssh://"):
        rest = repo[len("ssh://") :].split("/", 1)[0]
        return rest.rsplit("@", 1)[-1].split(":", 1)[0]
    return repo.split("@", 1)[-1].split(":", 1)[0]


def known_host_lines(answers: Answers, versions_lock: Path) -> list[str]:
    """The pinned host keys for the answers' SSH sources, as the platform role pins them.

    Args:
        answers: The answers.
        versions_lock: ``versions.lock.yml``.

    Returns:
        known_hosts lines.
    """
    hosts = {_ssh_host(r) for r in ssh_repos(answers)}
    github = github_known_hosts(versions_lock) if "github.com" in hosts else []
    return [*github, *answers.source_trust.known_hosts]


def tool_settings(
    answers: Answers,
    directory: Path,
    versions_lock: Path,
    *,
    deploy_key: Path = DEPLOY_KEY,
    replace: str = "",
) -> dict[str, Any]:
    """The settings file the platform role would write, for the answers.

    Args:
        answers: The answers.
        directory: A root-only directory for the host keys and signers files.
        versions_lock: ``versions.lock.yml``.
        deploy_key: The host's deploy key.
        replace: The active platform bootstrap may replace (``--replace-platform``).

    Returns:
        The settings, as ``deploy.Settings.from_json`` reads them.
    """
    ssh = bool(ssh_repos(answers))
    known_hosts = directory / "known_hosts"
    signers = directory / "allowed_signers"
    if ssh:
        known_hosts.write_text("\n".join(known_host_lines(answers, versions_lock)) + "\n")
    signers.write_text("\n".join(answers.source_trust.allowed_signers) + "\n")
    return {
        "platform": asdict(answers.platform),
        "content": {name: asdict(s) for name, s in answers.content.items()},
        "require_signed_tags": answers.source_trust.require_signed_tags,
        "allowed_signers": str(signers),
        "ssh_key": str(deploy_key) if ssh else "",
        "known_hosts": str(known_hosts) if ssh else "",
        "platform_settings": dict(answers.platform_settings),
        "replace": replace,
        "lab": {
            "machine_name": answers.machine.name,
            "lab_mode": answers.machine.lab_mode,
            "node_role": answers.machine.node_role,
            "training_caps": asdict(answers.training_caps),
        },
    }


def inspect(
    answers: Answers,
    versions_lock: Path,
    *,
    full: bool,
    paths: deploy.Paths | None = None,
    git: mirror.Git = mirror.run_git,
    host: Callable[[str], names.HostFacts | None] | None = None,
    deploy_key: Path = DEPLOY_KEY,
    contract_dir: Path = contract_mod.DEFAULT_DIR,
    owner_uid: int | None = None,
    replace: str = "",
) -> Inspection:
    """Inspect the answers' release (the platform tool's own checks).

    Args:
        answers: The answers (a platform source set).
        versions_lock: ``versions.lock.yml``.
        full: Also the content packages and the settings (``prepare
            --inspect``); otherwise the release alone.
        paths: Host paths (default: the host's).
        git: Command runner.
        host: Host facts for a platform name (default: this host's).
        deploy_key: The host's deploy key.
        contract_dir: The contract.
        owner_uid: Accepted owner of ``file://`` repositories besides root (tests).
        replace: The active platform bootstrap may replace (``--replace-platform``);
            without it, another active platform refuses the release.

    Returns:
        The inspection.
    """
    paths = paths or deploy.Paths()
    facts = host or (lambda name: names.host_facts(name, paths.etc_platform))
    contract = contract_mod.load(contract_dir)
    with tempfile.TemporaryDirectory(prefix="pinka-installer-") as scratch:
        data = tool_settings(
            answers, Path(scratch), versions_lock, deploy_key=deploy_key, replace=replace
        )
        settings = deploy.Settings.from_json(json.loads(json.dumps(data)))
        try:
            if full:
                plan = deploy.prepare(
                    settings,
                    paths,
                    contract,
                    git=git,
                    host=facts,
                    owner_uid=owner_uid,
                    inspect=True,
                )
                found = {
                    key: plan[key]
                    for key in ("platform", "release", "summary", "warnings", "vault_keys")
                }
                found["content"] = plan["summary"]["content"]
                found["content_sources"] = plan["content"]
            else:
                found = deploy.inspect_release(
                    settings, paths, contract, git=git, host=facts, owner_uid=owner_uid
                )
        except deploy.Refused as exc:
            return Inspection(ok=False, problems=[str(p) for p in exc.problems])
        except (mirror.FetchError, deploy.DeployError, OSError, ValueError) as exc:
            return Inspection(ok=False, error=str(exc))
    return Inspection(ok=True, found=found)


def access_env(
    answers: Answers, directory: Path, versions_lock: Path, *, deploy_key: Path = DEPLOY_KEY
) -> dict[str, str]:
    """The environment of ``git ls-remote`` against the answers' release (Test access).

    The same key and pinned host keys a deployment uses; never a prompt.

    Args:
        answers: The answers.
        directory: A root-only directory for the host keys file.
        versions_lock: ``versions.lock.yml``.
        deploy_key: The host's deploy key.

    Returns:
        Environment variables.
    """
    data = tool_settings(answers, directory, versions_lock, deploy_key=deploy_key)
    env = {"GIT_TERMINAL_PROMPT": "0"}
    env.update(mirror.ssh_env(data["ssh_key"] or None, data["known_hosts"] or None))
    return env


def review_key(answers: Answers) -> str:
    """What a review of the platform depends on (a change asks for a new review).

    Args:
        answers: The answers.

    Returns:
        A stable text of the platform answers.
    """
    data = answers.to_dict()
    keys = ("platform", "content", "source_trust", "platform_settings", "training_caps")
    return json.dumps({k: data.get(k) for k in keys}, sort_keys=True)
