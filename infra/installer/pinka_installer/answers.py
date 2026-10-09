"""The answers file (BUILD_PROMPT §18.6): model, loading, checks and serialization.

An answers file holds every non-secret choice of the installer. The TUI builds
one screen by screen; ``bootstrap.sh --answers`` replays one. JSON Schema
(``installer/schema/answers.schema.json``) checks the structure;
:func:`problems` adds the rules that need the repository or several fields
(the device confirmation, disjoint cpusets, known models and blueprints, at
least one admin).
"""

from __future__ import annotations

import copy
import io
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

import jsonschema
from ruamel.yaml import YAML

from pinka_installer import validators

SCHEMA_VERSION = 1
# How a secret comes to the vault (the answers record the choice, never the value).
GENERATE = "generate"
PROVIDED = "provided"
SCHEMA_FILE = Path(__file__).resolve().parents[1] / "schema" / "answers.schema.json"
ANSWERS_HEADER = (
    "# Installer answers for {machine} (BUILD_PROMPT §18.6), written by the pinka installer.\n"
    "# Replay: sudo ./bootstrap/bootstrap.sh --answers installer-answers/{machine}.yml \\\n"
    "#   --non-interactive --vault-password-file <file>\n"
    "# No secret values: provided secrets come from PINKA_SECRET_<NAME> variables.\n"
)


class AnswersError(ValueError):
    """An answers file that cannot be used, with every problem found."""

    def __init__(self, problems: list[str]) -> None:
        """Collect the problems.

        Args:
            problems: One message per problem (``path: message``).
        """
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass
class Machine:
    """Screen 4: the machine."""

    name: str = ""
    lab_mode: str = "production"
    node_role: str = "full"
    provider: str = "auto"
    power_policy: str = "lab_session_inhibit"
    srv_path: str = "/srv/pinka"
    docker_data_root: str = "/var/lib/docker"
    firewall_backend: str = "auto"
    allow_community_repos: bool = False


@dataclass
class Resources:
    """Screen 5: expected resources and the CPU split (empty: derived from the host)."""

    expected_memory_gib: int | None = None
    expected_cpu_threads: int | None = None
    cpuset_services: str = ""
    cpuset_targets: str = ""
    postgres_shared_buffers: str = ""


@dataclass
class Storage:
    """Screen 6: the Incus storage pool."""

    source: str = "loop"
    loop_size_gib: int = 300
    device: str = ""
    device_confirm: str = ""
    force_wipe: bool = False


@dataclass
class Researcher:
    """Screen 7: one researcher (SSH keys live in ``users/<name>.pub``)."""

    name: str
    admin: bool = False
    display_name: str = ""
    grafana: bool = True


@dataclass
class RemoteAccess:
    """Screen 8: Tailscale and SSH."""

    tailscale_login: str = "interactive"
    tailscale_tag: str = ""
    accept_dns: bool = False
    grafana_tailscale_serve: bool = False
    ssh_extra_allowed_interfaces: list[str] = field(default_factory=list)


@dataclass
class Gpu:
    """Screen 9: GPU and inference."""

    install_driver: str = "skip"
    accept_mok_enrollment: bool = False
    default_model: str = "none"
    vllm_profile: str = ""
    start_serving: bool = True


@dataclass
class Repository:
    """Screen 10: the restic repository (``type`` decides which fields count)."""

    type: str = "later"
    host: str = ""
    user: str = ""
    path: str = ""
    url: str = ""

    def url_for_restic(self) -> str:
        """The repository as restic names it, or an empty string for ``later``.

        Returns:
            ``sftp:user@host:/path``, ``rest:https://...`` or a local path.
        """
        if self.type == "sftp":
            return f"sftp:{self.user}@{self.host}:{self.path}"
        if self.type == "rest":
            return f"rest:{self.url}"
        if self.type == "local":
            return self.path
        return ""


@dataclass
class Retention:
    """How many daily, weekly and monthly snapshots restic keeps."""

    daily: int = 7
    weekly: int = 4
    monthly: int = 12


@dataclass
class Backups:
    """Screen 10: data and backups."""

    repository: Repository = field(default_factory=Repository)
    password: str = GENERATE
    schedule: str = "03:00"
    retention: Retention = field(default_factory=Retention)
    include_models: bool = False
    include_secrets: bool = False


@dataclass
class Source:
    """Screen 12: the pokus-platform repository that platform and catalog come from."""

    repo: str = ""
    known_hosts: list[str] = field(default_factory=list)
    require_signed_tags: bool = False
    allowed_signers: list[str] = field(default_factory=list)


@dataclass
class Component:
    """Screen 12: platform or catalog."""

    enabled: bool = False
    ref: str = ""


@dataclass
class Secrets:
    """Screen 14: which secrets are generated and which are provided (never values)."""

    grafana_admin: str = "generate"
    hf_token: str = "none"  # noqa: S105 - none or provided, not a token
    store_vault_password: bool = False


@dataclass
class Answers:
    """Every non-secret choice of one installation."""

    mode: str = "fresh"
    machine: Machine = field(default_factory=Machine)
    resources: Resources = field(default_factory=Resources)
    storage: Storage = field(default_factory=Storage)
    researchers: list[Researcher] = field(default_factory=list)
    researchers_removed: list[str] = field(default_factory=list)
    remote_access: RemoteAccess = field(default_factory=RemoteAccess)
    split_inference_peer: str = ""
    gpu: Gpu = field(default_factory=Gpu)
    backups: Backups = field(default_factory=Backups)
    restore_snapshot: str = "latest"
    source: Source = field(default_factory=Source)
    platform: Component = field(default_factory=Component)
    catalog: Component = field(default_factory=Component)
    images_build: list[str] = field(default_factory=list)
    secrets: Secrets = field(default_factory=Secrets)

    @property
    def tailscale_tag(self) -> str:
        """The Tailscale tag: the chosen one, else the lab mode's default."""
        default = "tag:pinka-qa" if self.machine.lab_mode == "staging" else "tag:pinka-lab"
        return self.remote_access.tailscale_tag or default

    @property
    def needs_source(self) -> bool:
        """Whether platform or catalog is deployed (and needs the repository)."""
        return self.platform.enabled or self.catalog.enabled

    def normalized(self) -> Answers:
        """A copy without the values of choices that do not apply.

        The TUI keeps them while the operator switches back and forth; the
        file has only what applies (a device pool has no loop size, a full
        node no peer).

        Returns:
            The normalized answers.
        """
        clean = copy.deepcopy(self)
        if clean.storage.source == "device":
            clean.storage.loop_size_gib = Storage().loop_size_gib
        else:
            clean.storage.device = clean.storage.device_confirm = ""
            clean.storage.force_wipe = False
        if clean.machine.node_role == "full":
            clean.split_inference_peer = ""
        if clean.mode != "restore":
            clean.restore_snapshot = "latest"
        repository = clean.backups.repository
        kept = _REPOSITORY_FIELDS[repository.type]
        for name in ("host", "user", "path", "url"):
            if name not in kept:
                setattr(repository, name, "")
        if not clean.needs_source:
            clean.source = Source()
        for component in (clean.platform, clean.catalog):
            if not component.enabled:
                component.ref = ""
        if clean.gpu.install_driver != "auto":
            clean.gpu.accept_mok_enrollment = False
        return clean

    def to_dict(self) -> dict[str, Any]:
        """The answers in the file format (only what applies, see :meth:`normalized`).

        Returns:
            A mapping that satisfies the answers schema.
        """
        clean = self.normalized()
        data: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "mode": clean.mode}
        data["machine"] = asdict(clean.machine)
        resources = {k: v for k, v in asdict(clean.resources).items() if v not in (None, "")}
        if resources:
            data["resources"] = resources
        storage: dict[str, Any] = {"source": clean.storage.source}
        if clean.storage.source == "loop":
            storage["loop_size_gib"] = clean.storage.loop_size_gib
        else:
            storage["device"] = clean.storage.device
            storage["device_confirm"] = clean.storage.device_confirm
            storage["force_wipe"] = clean.storage.force_wipe
        data["storage"] = storage
        data["researchers"] = [_researcher_dict(r) for r in clean.researchers]
        if clean.researchers_removed:
            data["researchers_removed"] = list(clean.researchers_removed)
        remote = asdict(clean.remote_access)
        if not remote["tailscale_tag"]:
            del remote["tailscale_tag"]
        data["remote_access"] = remote
        if clean.machine.node_role in ("lab", "inference") and clean.split_inference_peer:
            data["split_inference"] = {"peer": clean.split_inference_peer}
        gpu = asdict(clean.gpu)
        if not gpu["vllm_profile"]:
            del gpu["vllm_profile"]
        if not gpu["accept_mok_enrollment"]:
            del gpu["accept_mok_enrollment"]
        data["gpu"] = gpu
        backups = asdict(clean.backups)
        backups["repository"] = _repository_dict(clean.backups.repository)
        data["backups"] = backups
        if clean.mode == "restore":
            data["restore"] = {"snapshot": clean.restore_snapshot}
        if clean.needs_source:
            data["source"] = asdict(clean.source)
        data["platform"] = _component_dict(clean.platform)
        data["catalog"] = _component_dict(clean.catalog)
        data["images"] = {"build": list(clean.images_build)}
        data["secrets"] = {"generate": "all", **asdict(clean.secrets)}
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Answers:
        """Build answers from a mapping that satisfies the schema.

        Args:
            data: A loaded answers file.

        Returns:
            The answers, with defaults for every optional field.
        """
        answers = cls(mode=str(data["mode"]))
        answers.machine = _build(Machine, data.get("machine", {}))
        answers.resources = _build(Resources, data.get("resources", {}))
        answers.storage = _build(Storage, data.get("storage", {}))
        answers.researchers = [_build(Researcher, r) for r in data.get("researchers", [])]
        answers.researchers_removed = list(data.get("researchers_removed", []))
        answers.remote_access = _build(RemoteAccess, data.get("remote_access", {}))
        answers.split_inference_peer = str((data.get("split_inference") or {}).get("peer", ""))
        answers.gpu = _build(Gpu, data.get("gpu", {}))
        backups = dict(data.get("backups", {}))
        repository = _build(Repository, backups.pop("repository", {}))
        retention = _build(Retention, backups.pop("retention", {}))
        answers.backups = _build(Backups, backups)
        answers.backups.repository = repository
        answers.backups.retention = retention
        answers.restore_snapshot = str((data.get("restore") or {}).get("snapshot", "latest"))
        answers.source = _build(Source, data.get("source", {}))
        answers.platform = _build(Component, data.get("platform", {}))
        answers.catalog = _build(Component, data.get("catalog", {}))
        answers.images_build = list((data.get("images") or {}).get("build", []))
        secrets = {k: v for k, v in (data.get("secrets") or {}).items() if k != "generate"}
        answers.secrets = _build(Secrets, secrets)
        return answers


def _build[T](kind: type[T], values: Mapping[str, Any]) -> T:
    names = {f.name for f in fields(kind)}  # type: ignore[arg-type]
    return kind(**{k: copy.deepcopy(v) for k, v in values.items() if k in names})


def _researcher_dict(researcher: Researcher) -> dict[str, Any]:
    data: dict[str, Any] = {"name": researcher.name}
    if researcher.display_name:
        data["display_name"] = researcher.display_name
    data["admin"] = researcher.admin
    data["grafana"] = researcher.grafana
    return data


_REPOSITORY_FIELDS = {
    "sftp": ("host", "user", "path"),
    "rest": ("url",),
    "local": ("path",),
    "later": (),
}


def _repository_dict(repository: Repository) -> dict[str, Any]:
    keys = _REPOSITORY_FIELDS[repository.type]
    return {"type": repository.type, **{k: getattr(repository, k) for k in keys}}


def _component_dict(component: Component) -> dict[str, Any]:
    data: dict[str, Any] = {"enabled": component.enabled}
    if component.ref:
        data["ref"] = component.ref
    return data


def schema() -> dict[str, Any]:
    """The answers JSON Schema.

    Returns:
        The schema.
    """
    loaded: dict[str, Any] = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    return loaded


def schema_problems(data: object) -> list[str]:
    """Check a loaded answers file against the schema.

    Args:
        data: The loaded YAML.

    Returns:
        One ``path: message`` per violation.
    """
    validator = jsonschema.Draft202012Validator(schema())
    found = []
    for error in sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path)):
        where = "/".join(str(p) for p in error.absolute_path) or "(top)"
        found.append(f"{where}: {error.message}")
    return found


@dataclass(frozen=True)
class Context:
    """What the cross-field checks need from the repository and the host.

    Attributes:
        model_ids: Ids in ``models.lock.yml``.
        vllm_profiles: Profile names in ``vllm/profiles/``.
        blueprints: Golden image blueprints in ``images/``.
        service_accounts: The lab's service account names.
        cpu_count: CPUs of the host, if known.
        interfaces: Network interfaces of the host, if known.
    """

    model_ids: frozenset[str] = frozenset()
    vllm_profiles: frozenset[str] = frozenset()
    blueprints: frozenset[str] = frozenset()
    service_accounts: frozenset[str] = frozenset()
    cpu_count: int | None = None
    interfaces: frozenset[str] | None = None


def _check(found: list[str], where: str, message: str | None) -> None:
    if message:
        found.append(f"{where}: {message}")


def problems(answers: Answers, context: Context) -> list[str]:
    """The rules that JSON Schema cannot express.

    Args:
        answers: The answers.
        context: Repository and host data.

    Returns:
        One ``path: message`` per problem.
    """
    found: list[str] = []
    machine = answers.machine
    _check(found, "machine/name", validators.hostname(machine.name))
    _check(found, "machine/srv_path", validators.absolute_path(machine.srv_path))
    _check(found, "machine/docker_data_root", validators.absolute_path(machine.docker_data_root))
    _resource_problems(found, answers.resources, context)
    _storage_problems(found, answers.storage)
    _researcher_problems(found, answers, context)
    remote = answers.remote_access
    _check(found, "remote_access/tailscale_tag", validators.tailscale_tag(answers.tailscale_tag))
    for name in remote.ssh_extra_allowed_interfaces:
        where = "remote_access/ssh_extra_allowed_interfaces"
        _check(found, where, validators.interface(name))
        if context.interfaces is not None and name not in context.interfaces:
            found.append(f"{where}: this host has no interface {name!r}")
    if machine.node_role in ("lab", "inference") and answers.split_inference_peer:
        _check(found, "split_inference/peer", validators.hostname(answers.split_inference_peer))
        if answers.split_inference_peer == machine.name:
            found.append("split_inference/peer: the other node, not this machine")
    _gpu_problems(found, answers, context)
    _backup_problems(found, answers)
    if answers.mode == "restore":
        _check(found, "restore/snapshot", validators.snapshot(answers.restore_snapshot))
    _source_problems(found, answers)
    for name in answers.images_build:
        if context.blueprints and name not in context.blueprints:
            found.append(f"images/build: no blueprint {name!r} in images/")
    return found


def _resource_problems(found: list[str], resources: Resources, context: Context) -> None:
    for name in ("cpuset_services", "cpuset_targets"):
        value = getattr(resources, name)
        if value:
            _check(found, f"resources/{name}", validators.cpuset(value, context.cpu_count))
    if resources.cpuset_services and resources.cpuset_targets:
        _check(
            found,
            "resources/cpuset_targets",
            validators.cpusets_disjoint(resources.cpuset_services, resources.cpuset_targets),
        )


def _storage_problems(found: list[str], storage: Storage) -> None:
    if storage.source != "device":
        return
    if storage.device_confirm != storage.device:
        found.append(
            "storage/device_confirm: must repeat storage/device exactly; "
            "the disk is formatted only then"
        )


def _researcher_problems(found: list[str], answers: Answers, context: Context) -> None:
    names = [r.name for r in answers.researchers]
    for researcher in answers.researchers:
        _check(
            found,
            f"researchers/{researcher.name}",
            validators.username(researcher.name, context.service_accounts),
        )
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        found.append(f"researchers: listed twice: {', '.join(duplicates)}")
    if not any(r.admin for r in answers.researchers):
        found.append("researchers: at least one researcher must be an admin")
    removed_active = sorted(set(names) & set(answers.researchers_removed))
    if removed_active:
        found.append(f"researchers_removed: still active: {', '.join(removed_active)}")


def _gpu_problems(found: list[str], answers: Answers, context: Context) -> None:
    gpu = answers.gpu
    if gpu.default_model != "none" and gpu.default_model not in context.model_ids:
        found.append(
            f"gpu/default_model: {gpu.default_model!r} is not in models.lock.yml "
            "(add it there first, docs/how-to/add-a-model.md)"
        )
    if gpu.vllm_profile and context.vllm_profiles and gpu.vllm_profile not in context.vllm_profiles:
        found.append(f"gpu/vllm_profile: no profile {gpu.vllm_profile!r} in vllm/profiles/")
    if gpu.accept_mok_enrollment and gpu.install_driver != "auto":
        found.append(
            "gpu/accept_mok_enrollment: bootstrap installs the driver only with "
            "install_driver: auto"
        )
    if answers.machine.node_role == "lab" and gpu.default_model != "none":
        found.append("gpu/default_model: a lab node serves no model (the inference node does)")


def _backup_problems(found: list[str], answers: Answers) -> None:
    backups = answers.backups
    repository = backups.repository
    if repository.type != "later":
        _check(
            found,
            "backups/repository",
            validators.restic_repository(repository.url_for_restic()),
        )
    _check(found, "backups/schedule", validators.time_of_day(backups.schedule))
    if answers.mode == "restore" and repository.type == "later":
        found.append("backups/repository: restore mode needs the repository to restore from")


def _source_problems(found: list[str], answers: Answers) -> None:
    if answers.needs_source:
        _check(found, "source/repo", validators.source_repo(answers.source.repo))
    for name, component in (("platform", answers.platform), ("catalog", answers.catalog)):
        if not component.enabled:
            continue
        _check(found, f"{name}/ref", validators.git_ref(component.ref))
        if answers.machine.node_role == "inference":
            found.append(f"{name}/enabled: an inference node runs neither platform nor catalog")


def _yaml() -> YAML:
    yaml = YAML(typ="rt")
    yaml.default_flow_style = False
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.width = 100
    return yaml


def load(path: Path, context: Context | None = None) -> Answers:
    """Load and check an answers file.

    Args:
        path: The file.
        context: Repository and host data for the cross-field checks.

    Returns:
        The answers.

    Raises:
        AnswersError: With every schema violation and problem.
    """
    data = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    found = schema_problems(data)
    if found:
        raise AnswersError(found)
    answers = Answers.from_dict(data)
    found = problems(answers, context or Context())
    if found:
        raise AnswersError(found)
    return answers


def dumps(answers: Answers) -> str:
    """Serialize answers with the managed header.

    Args:
        answers: The answers.

    Returns:
        YAML text that satisfies the schema.
    """
    stream = io.StringIO()
    _yaml().dump(answers.to_dict(), stream)
    return ANSWERS_HEADER.format(machine=answers.machine.name) + stream.getvalue()


def secret_names(answers: Answers) -> list[str]:
    """The ``PINKA_SECRET_<NAME>`` variables a non-interactive replay reads.

    Args:
        answers: The answers.

    Returns:
        Names of the provided (not generated) secrets.
    """
    names = []
    if answers.remote_access.tailscale_login == "auth_key":
        names.append("TAILSCALE_AUTHKEY")
    backups = answers.backups
    if backups.password == "provided" and backups.repository.type != "later":  # noqa: S105
        names.append("RESTIC_PASSWORD")
    if answers.secrets.grafana_admin == "provided":
        names.append("GRAFANA_ADMIN_PASSWORD")
    if answers.secrets.hf_token == "provided":  # noqa: S105 - the choice, not a token
        names.append("HF_TOKEN")
    return names
