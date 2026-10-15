"""The installer's state between screens (BUILD_PROMPT §18.1, §18.4).

:class:`Wizard` holds the answers and everything that must not reach a file
or the resume state: entered secrets, the vault password, pasted keys. Only
:meth:`Wizard.resume_data` is persisted, and it never contains a secret.
Suggestions (CPU split, Postgres memory, vLLM profile, fitting models) are
pure functions so the tests can check them.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from pinka_installer import answers as answers_mod
from pinka_installer import groupvars, hostvars, keys, validators, vault
from pinka_installer.answers import Answers
from pinka_installer.paths import Paths

# The wizard's screens (BUILD_PROMPT §18.4, screens 1-15); 16-18 follow the install.
STEPS = (
    ("welcome", "Welcome"),
    ("preflight", "Preflight"),
    ("mode", "Install mode"),
    ("machine", "Machine"),
    ("resources", "Resources"),
    ("storage", "Storage"),
    ("researchers", "Researchers"),
    ("remote", "Remote access"),
    ("gpu", "GPU & inference"),
    ("backups", "Data & backups"),
    ("restore", "Restore"),
    ("platform", "Platform & catalog"),
    ("images", "Golden images"),
    ("secrets", "Secrets & vault"),
    ("review", "Review"),
)
STEP_NAMES = tuple(name for name, _ in STEPS)
AFTER_STEPS = {"progress": "Install progress", "doctor": "Doctor", "finish": "Finish"}
CONSENTS = ("accept_degraded", "accept_experimental", "force_unsupported", "accept_mok_enrollment")
LOOP_RESERVE_GIB = 50
MIN_VAULT_PASSWORD = 12
# Strength meter: lengths and character classes (lower, upper, digit, other) that count.
GOOD_LENGTH = 16
LONG_LENGTH = 24
MIXED = 3


@dataclass
class Wizard:
    """Everything the screens collect.

    Attributes:
        answers: The non-secret choices (the answers file).
        profile: The host profile from preflight.
        preflight_exit: Preflight's exit code.
        consents: Overrides the operator granted (accept_degraded, ...).
        public_keys: Researchers' checked SSH keys (name to keys).
        provided: Secrets the operator entered (memory only).
        vault_password: The vault password (memory only).
        restic_acknowledged: The operator stored the generated restic password.
        mok_password: One-time MOK password (memory only; bootstrap reads it from its
            standard input and gives it to mokutil).
        nvidia_driver: The driver setting preflight judges with: empty until the
            Preflight or GPU screen decides (preflight then uses auto, like bootstrap).
        build_images_now: Build the chosen golden images during the install.
        installed: A lab is installed on this host (modify mode is offered).
        extra_files: Files the screens prepared besides the plan (models.lock.yml).
    """

    answers: Answers = field(default_factory=Answers)
    profile: dict[str, Any] = field(default_factory=dict)
    preflight_exit: int | None = None
    consents: set[str] = field(default_factory=set)
    public_keys: dict[str, keys.KeyCheck] = field(default_factory=dict)
    provided: vault.Provided = field(default_factory=vault.Provided)
    vault_password: str = field(default="", repr=False)
    restic_acknowledged: bool = False
    mok_password: str = field(default="", repr=False)
    nvidia_driver: str = ""
    build_images_now: bool = True
    installed: bool = False
    extra_files: dict[str, str] = field(default_factory=dict)

    # --- host facts ------------------------------------------------------------------

    @property
    def provider(self) -> str:
        """The detected provider (baremetal, vm, wsl2) or empty."""
        return str(self.profile.get("provider") or "")

    @property
    def is_wsl(self) -> bool:
        """Whether the host is WSL2."""
        return self.provider == "wsl2"

    @property
    def gpu(self) -> dict[str, Any]:
        """The profile's GPU section."""
        return dict(self.profile.get("gpu") or {})

    @property
    def has_gpu(self) -> bool:
        """Whether a GPU is present (usable or not)."""
        return bool(self.gpu.get("present"))

    @property
    def cpu_threads(self) -> int | None:
        """Effective CPU threads."""
        hardware = self.profile.get("hardware") or {}
        value = hardware.get("cpu_threads_effective") or hardware.get("cpu_threads")
        return int(value) if value else None

    @property
    def memory_gib(self) -> float | None:
        """Memory in GiB."""
        value = (self.profile.get("hardware") or {}).get("memory_gib")
        return float(value) if value else None

    @property
    def secure_boot(self) -> bool:
        """Whether Secure Boot is enabled."""
        return (self.profile.get("capabilities") or {}).get("secure_boot") == "enabled"

    @property
    def mok_enrollment_needed(self) -> bool:
        """Whether Secure Boot is on and the NVIDIA driver is missing (approved deviation I1).

        The driver can then only be installed with a Machine Owner Key enrolled
        at the next boot, which needs the operator's consent.
        """
        gpu = self.gpu
        return (
            self.secure_boot
            and not self.is_wsl
            and gpu.get("vendor") == "nvidia"
            and bool(gpu.get("present"))
            and gpu.get("driver_state") == "not_installed"
        )

    @property
    def storage_backend(self) -> str:
        """btrfs, lvm or empty, from preflight."""
        return str(self.profile.get("storage_backend") or "")

    # --- what bootstrap needs -------------------------------------------------------

    def preflight_args(self) -> list[str]:
        """Preflight options for the operator's driver choices so far.

        Returns:
            ``--nvidia-driver`` once a screen decided it, and
            ``--accept-mok-enrollment`` with that consent.
        """
        args = ["--nvidia-driver", self.nvidia_driver] if self.nvidia_driver else []
        if "accept_mok_enrollment" in self.consents:
            args.append("--accept-mok-enrollment")
        return args

    def bootstrap_options(self, passthrough: Iterable[str] = ()) -> list[str]:
        """Options of the non-interactive bootstrap the TUI runs.

        Args:
            passthrough: Options the operator gave the first bootstrap.

        Returns:
            Options, without duplicates.
        """
        options = list(passthrough)
        for consent in sorted(self.consents):
            flag = "--" + consent.replace("_", "-")
            if flag not in options:
                options.append(flag)
        if self.answers.mode == "restore" and "--restore" not in options:
            options.append("--restore")
        if self.mok_password and "--mok-password-stdin" not in options:
            options.append("--mok-password-stdin")
        if not self.build_images_now and self.answers.images_build:
            skip = [o for o in options if o.startswith("--skip-tags=")]
            if skip:
                options[options.index(skip[0])] = skip[0] + ",images"
            else:
                options.append("--skip-tags=images")
        return options

    def resume_data(self) -> dict[str, Any]:
        """What the resume state keeps: the answers and the consents, no secret.

        Returns:
            JSON-serializable data.
        """
        return {
            "answers": self.answers.to_dict(),
            "consents": sorted(self.consents),
            "build_images_now": self.build_images_now,
        }

    def restore(self, data: Mapping[str, Any]) -> None:
        """Continue from the resume state.

        Args:
            data: From :meth:`resume_data`.
        """
        loaded = data.get("answers")
        # answers saved mid-wizard are incomplete (no machine name yet, ...): only the
        # Review screen checks them against the schema
        if isinstance(loaded, dict):
            try:
                self.answers = Answers.from_dict(loaded)
            except (KeyError, TypeError, ValueError):
                self.answers = Answers()  # an unreadable state starts over
        self.consents = {c for c in data.get("consents", []) if c in CONSENTS}
        self.build_images_now = bool(data.get("build_images_now", True))


def existing_answers(paths: Paths, machine: str) -> Answers:
    """The answers of an installed machine (modify mode, BUILD_PROMPT §18.1).

    The answers file is the base; host_vars and the researchers of
    ``group_vars/all/main.yml`` win where they differ, since an operator may
    have edited them by hand since.

    Args:
        paths: Installer paths.
        machine: The installed machine.

    Returns:
        The answers, in modify mode.
    """
    try:
        loaded = answers_mod.load(paths.answers(machine))
    except (OSError, answers_mod.AnswersError):
        loaded = Answers()
        loaded.machine.name = machine
    host_vars = paths.host_vars(machine)
    if host_vars.exists():
        loaded = hostvars.to_answers(hostvars.load(host_vars.read_text(encoding="utf-8")), loaded)
    if paths.group_vars_main.exists():
        found, removed = groupvars.researchers(paths.group_vars_main.read_text(encoding="utf-8"))
        if found:
            loaded.researchers, loaded.researchers_removed = found, removed
    loaded.mode = "modify"
    return loaded


# --- suggestions ------------------------------------------------------------------------


def suggest_cpusets(threads: int | None, cores: Iterable[Iterable[int]] = ()) -> tuple[str, str]:
    """Split the CPUs between lab services and Incus targets (about 30 % for services).

    With the host's SMT siblings the split takes whole cores, lowest first, so
    no core is shared between the two sets.

    Args:
        threads: CPU threads.
        cores: The CPUs of each core with more than one thread.

    Returns:
        ``(cpuset_services, cpuset_targets)``; empty strings when unknown or too few CPUs.
    """
    minimum = 4
    if not threads or threads < minimum:
        return "", ""
    wanted = max(2, round(threads * 0.3))
    every = set(range(threads))
    groups = {cpu: frozenset([cpu]) for cpu in every}
    for core in cores:
        group = frozenset(core) & every
        for cpu in group:
            groups[cpu] = group
    services: set[int] = set()
    for group in sorted(set(groups.values()), key=min):
        if len(services) >= wanted:
            break
        services |= group
    if len(services) >= threads:
        return "", ""
    return validators.format_cpuset(services), validators.format_cpuset(every - services)


def suggest_postgres(memory_gib: float | None) -> str:
    """Postgres shared_buffers: 64 MiB per GiB of memory, between 128 MB and 4 GB.

    Args:
        memory_gib: Memory.

    Returns:
        Such as ``2048MB``; empty when unknown (the role derives it then).
    """
    if not memory_gib:
        return ""
    return f"{min(max(int(memory_gib * 64), 128), 4096)}MB"


def loop_size_limit(free_gib: float) -> int:
    """The largest loop file that leaves the reserve free.

    Args:
        free_gib: Free space where the loop file goes.

    Returns:
        GiB.
    """
    return max(int(free_gib) - LOOP_RESERVE_GIB, 0)


def load_profiles(directory: Path) -> dict[str, dict[str, Any]]:
    """The vLLM serving profiles.

    Args:
        directory: ``vllm/profiles/``.

    Returns:
        Name to profile.
    """
    safe = YAML(typ="safe")
    return {
        p.stem: safe.load(p.read_text(encoding="utf-8")) for p in sorted(directory.glob("*.yml"))
    }


def suggest_profile(gpu: Mapping[str, Any], profiles: Mapping[str, Mapping[str, Any]]) -> str:
    """The serving profile that fits the GPU best (largest memory requirement that fits).

    Args:
        gpu: The profile's GPU section.
        profiles: From :func:`load_profiles`.

    Returns:
        The profile name, or empty when none fits.
    """
    vram_mib = float(gpu.get("vram_gib") or 0) * 1024
    capability = str(gpu.get("compute_capability") or "")
    fitting = []
    for name, profile in profiles.items():
        need = profile.get("gpu") or {}
        if vram_mib < float(need.get("min_memory_mib", 0)):
            continue
        if (
            capability
            and need.get("compute_capability")
            and capability != str(need["compute_capability"])
        ):
            continue
        fitting.append((float(need.get("min_memory_mib", 0)), name))
    return max(fitting)[1] if fitting else ""


@dataclass(frozen=True)
class ModelChoice:
    """A model the GPU screen offers.

    Attributes:
        id: Catalog or lock id.
        hf_repo: Repository.
        license: License.
        gated: Needs a token.
        fits: Why it does not fit the profile, or empty if it fits.
        pinned: Already in models.lock.yml.
        entry: The catalog entry.
    """

    id: str
    hf_repo: str
    license: str
    gated: bool
    fits: str
    pinned: bool
    entry: Mapping[str, Any]


def model_choices(
    catalog: Mapping[str, Any],
    lock_ids: Iterable[str],
    profile: str,
    vram_gib: float | None,
) -> list[ModelChoice]:
    """The catalog's models with whether they fit the profile and the GPU.

    Args:
        catalog: ``models.catalog.yml``.
        lock_ids: Ids already in ``models.lock.yml``.
        profile: The chosen serving profile.
        vram_gib: GPU memory.

    Returns:
        Fitting, non-gated models first.
    """
    pinned = set(lock_ids)
    choices = []
    for entry in catalog.get("models") or []:
        fit = (entry.get("profiles") or {}).get(profile)
        if fit is None:
            reason = f"no settings for profile {profile}"
        elif vram_gib and float(fit.get("expected_vram_gib", 0)) > vram_gib:
            reason = f"needs {fit['expected_vram_gib']} GiB"
        else:
            reason = ""
        choices.append(
            ModelChoice(
                id=str(entry["id"]),
                hf_repo=str(entry.get("hf_repo", "")),
                license=str(entry.get("license", "")),
                gated=bool(entry.get("gated", False)),
                fits=reason,
                pinned=str(entry["id"]) in pinned,
                entry=entry,
            )
        )
    return sorted(choices, key=lambda c: (bool(c.fits), c.gated, c.id))


def password_strength(password: str) -> tuple[int, str]:
    """A rough strength meter for the vault password.

    Args:
        password: The password.

    Returns:
        A score from 0 to 4 and a word for it.
    """
    classes = sum(
        any(test(c) for c in password)
        for test in (str.islower, str.isupper, str.isdigit, lambda c: not c.isalnum())
    )
    length = len(password)
    score = (
        0
        if length < MIN_VAULT_PASSWORD
        else min(4, 1 + (length >= GOOD_LENGTH) + (length >= LONG_LENGTH) + (classes >= MIXED))
    )
    return score, ("too short", "weak", "fair", "good", "strong")[score]


def vault_password_problem(first: str, second: str) -> str | None:
    """Check a new vault password entered twice.

    Args:
        first: The password.
        second: Its repetition.

    Returns:
        The problem, or None.
    """
    if len(first) < MIN_VAULT_PASSWORD:
        return f"at least {MIN_VAULT_PASSWORD} characters"
    if first != second:
        return "the two entries differ"
    return None


def acl_snippet(tag: str) -> str:
    """The tailnet ACL lines the lab needs (shown with a copy action on screen 8).

    Args:
        tag: The machine's tag.

    Returns:
        A HuJSON snippet for the tailnet policy file.
    """
    return (
        "// Tailnet policy: let the researchers' devices reach the lab (SSH and the serve port).\n"
        f'"tagOwners": {{"{tag}": ["autogroup:admin"]}},\n'
        '"acls": [\n'
        f'  {{"action": "accept", "src": ["autogroup:member"], "dst": ["{tag}:22", "{tag}:443"]}}\n'
        "]"
    )


def interface_problems(names: Iterable[str]) -> list[str]:
    """Check interface names (multi-select of the remote-access screen).

    Args:
        names: Interface names.

    Returns:
        One message per bad name.
    """
    return [f"{n}: {e}" for n in names if (e := validators.interface(n))]
