"""``host_vars/<machine>.yml`` from the answers, and back (BUILD_PROMPT §18.3).

The first file of a machine starts from ``example-machine.yml``, so every
setting keeps its explanation; later runs (modify mode) edit the existing file
in place with ruamel.yaml's round-trip mode, which keeps the operator's
comments and the order of the keys.
"""

from __future__ import annotations

import io
import re
from collections.abc import Mapping
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from pinka_installer import yamlio
from pinka_installer.answers import (
    Answers,
    Features,
    GpuArbiter,
    Retention,
    Source,
    TelemetryRetention,
    TrainingCaps,
)

HEADER = (
    "# Machine configuration of {machine} (BUILD_PROMPT §8.1), written by the pinka\n"
    "# installer from installer-answers/{machine}.yml. Run the installer again to\n"
    "# change it (modify mode), or edit it by hand: comments and order are kept.\n"
    "# Secrets are in group_vars/all/vault.yml, never here.\n"
)
# Keys the answers may leave unset: they are removed so the lab-wide default applies.
OPTIONAL_KEYS = (
    "expected",
    "cpuset_services",
    "cpuset_targets",
    "postgres_shared_buffers",
    "vllm_profile",
    "tailscale_tag",
    "inference_lab_node",
    "inference_remote_node",
    "pinka_source_known_hosts",
    "pinka_allowed_signers",
    "restore_snapshot",
    "firewall_close_listeners",
    "firewall_close_install_path",
)
# Comments for keys that example-machine.yml does not have.
COMMENTS = {
    "tailscale_tag": "Tailscale ACL tag (default: tag:pinka-lab; tag:pinka-qa on staging).",
    "tailscale_accept_dns": "Accept the tailnet's DNS settings (MagicDNS).",
    "inference_lab_node": "Inference node: the lab node it is paired with (docs/node-roles.md).",
    "inference_remote_node": "Lab node: the inference node it is paired with (docs/node-roles.md).",
    "incus_pool_device_force_wipe": "Wipe a pool device with signatures (typed WIPE confirmation).",
    "backup_schedule": "Daily restic backup (OnCalendar, UTC); the repository is in the vault.",
    "backup_retention": "Snapshots restic keeps.",
    "backup_include_models": "Back up model weights too (large; they can be downloaded again).",
    "backup_include_secrets": "Back up /etc/pinka/secrets (encrypted by restic; see docs).",
    "backup_secrets_acknowledged": "The operator read the warning about backing up secrets.",
    "platform": "The platform release (repo, ref, path, tag_prefix); {}: no platform.",
    "content": "One source per content package of the platform's manifest.",
    "pinka_source_known_hosts": "Host key lines of an SSH source other than GitHub.",
    "require_signed_tags": "Deploy only tags signed by pinka_allowed_signers.",
    "pinka_allowed_signers": "allowed_signers lines for tag signatures.",
    "platform_settings": "Non-secret settings per service, checked against its config schema.",
    "gpu_arbiter": "GPU arbiter: after_boot serve | idle; restore_after_training.",
    "training_caps": "Caps on training jobs (the GPU arbiter refuses runs over them).",
    "images_blueprints": "Golden image blueprints to build (images/<name>/).",
    "restore_snapshot": "restic snapshot that restore.yml restores (an ID or latest).",
    "firewall_close_listeners": "Pre-existing listeners closed with the owner's consent.",
    "firewall_close_install_path": "Close the install's access path once tailnet SSH works.",
    "incus_concurrent_targets": "Targets that run at once: the bench project's memory.",
    "inference": "Inference: auto (vLLM where the GPU is usable) | off.",
    "training": "Training jobs: auto (where the GPU is usable) | cpu | off.",
    "backup_pgdump_keep": "Local PostgreSQL dumps kept (one a day).",
    "observability_prometheus_retention": "Prometheus's retention by time.",
    "observability_prometheus_retention_size": "Prometheus's retention by size; 0: by time only.",
    "observability_loki_retention": "Loki's retention of logs.",
    "observability_tempo_retention": "Tempo's retention of traces (hours).",
}
ONCALENDAR_RE = re.compile(r"^\*-\*-\* ([0-2][0-9]):([0-5][0-9]):00$")


def host_vars(answers: Answers) -> dict[str, Any]:
    """The host variables of the answers, in the order of the file.

    Args:
        answers: The answers.

    Returns:
        Variable name to value; optional settings that are not answered are absent.
    """
    values: dict[str, Any] = {}
    for section in (_machine, _storage, _gpu, _remote, _backups, _platform):
        values.update(section(answers))
    return values


def _machine(answers: Answers) -> dict[str, Any]:
    machine, resources = answers.machine, answers.resources
    values: dict[str, Any] = {
        "machine_name": machine.name,
        "lab_mode": machine.lab_mode,
        "lab_provider": machine.provider,
        "power_policy": machine.power_policy,
    }
    expected = {
        key: value
        for key, value in (
            ("memory_gib", resources.expected_memory_gib),
            ("cpu_threads", resources.expected_cpu_threads),
        )
        if value is not None
    }
    if expected:
        values["expected"] = expected
    for key in ("cpuset_services", "cpuset_targets"):
        if getattr(resources, key):
            values[key] = getattr(resources, key)
    return values


def _storage(answers: Answers) -> dict[str, Any]:
    machine, storage = answers.machine, answers.storage
    device = storage.source == "device"
    return {
        "incus_pool_source": storage.source,
        "incus_pool_size": f"{storage.loop_size_gib}GiB",
        "incus_pool_device": storage.device if device else "",
        "incus_pool_device_confirm": storage.device_confirm if device else "",
        "incus_pool_device_force_wipe": storage.force_wipe if device else False,
        "incus_concurrent_targets": answers.resources.concurrent_targets,
        "pinka_root": machine.srv_path,
        "docker_data_root": machine.docker_data_root,
        "node_role": machine.node_role,
        "inference": answers.features.inference,
        "training": answers.features.training,
        "firewall_backend": machine.firewall_backend,
        "install_nvidia_driver": answers.gpu.install_driver,
        "allow_community_repos": machine.allow_community_repos,
    }


def _gpu(answers: Answers) -> dict[str, Any]:
    gpu = answers.gpu
    values: dict[str, Any] = {}
    if gpu.vllm_profile:
        values["vllm_profile"] = gpu.vllm_profile
    values["vllm_default_deployment"] = (
        None
        if gpu.default_model == "none"
        else {"model_id": gpu.default_model, "start_after_install": gpu.start_serving}
    )
    if answers.resources.postgres_shared_buffers:
        values["postgres_shared_buffers"] = answers.resources.postgres_shared_buffers
    return values


def _remote(answers: Answers) -> dict[str, Any]:
    remote = answers.remote_access
    values: dict[str, Any] = {
        "grafana_tailscale_serve": remote.grafana_tailscale_serve,
        "tailscale_serve_consent": remote.tailscale_serve_consent,
        "ssh_extra_allowed_interfaces": list(remote.ssh_extra_allowed_interfaces),
    }
    # Pre-existing access (BUILD_PROMPT §7.5): written only when consented, so
    # group_vars' defaults (nothing closed) stand otherwise.
    if remote.close_listeners:
        values["firewall_close_listeners"] = list(remote.close_listeners)
    if remote.close_install_path:
        values["firewall_close_install_path"] = True
    if remote.tailscale_tag:
        values["tailscale_tag"] = remote.tailscale_tag
    values["tailscale_accept_dns"] = remote.accept_dns
    role, peer = answers.machine.node_role, answers.split_inference_peer
    if role == "inference" and peer:
        values["inference_lab_node"] = peer
    elif role == "lab" and peer:
        values["inference_remote_node"] = peer
    return values


def _backups(answers: Answers) -> dict[str, Any]:
    backups = answers.backups
    return {
        "backup_schedule": f"*-*-* {backups.schedule}:00",
        "backup_retention": {
            "daily": backups.retention.daily,
            "weekly": backups.retention.weekly,
            "monthly": backups.retention.monthly,
        },
        "backup_include_models": backups.include_models,
        "backup_include_secrets": backups.include_secrets,
        "backup_secrets_acknowledged": backups.include_secrets,
        "backup_pgdump_keep": backups.pg_dumps_kept,
        "observability_prometheus_retention": answers.telemetry_retention.prometheus,
        "observability_prometheus_retention_size": answers.telemetry_retention.prometheus_size,
        "observability_loki_retention": answers.telemetry_retention.loki,
        "observability_tempo_retention": answers.telemetry_retention.tempo,
    }


def _platform(answers: Answers) -> dict[str, Any]:
    values: dict[str, Any] = {
        "platform": _source(answers.platform) if answers.has_platform else {},
        "content": {name: _source(s) for name, s in sorted(answers.content.items())},
    }
    trust = answers.source_trust
    if trust.known_hosts:
        values["pinka_source_known_hosts"] = list(trust.known_hosts)
    values["require_signed_tags"] = trust.require_signed_tags
    if trust.allowed_signers:
        values["pinka_allowed_signers"] = list(trust.allowed_signers)
    values["platform_settings"] = dict(answers.platform_settings)
    values["gpu_arbiter"] = {
        "after_boot": answers.gpu_arbiter.after_boot,
        "restore_after_training": answers.gpu_arbiter.restore_after_training,
    }
    caps = answers.training_caps
    values["training_caps"] = {
        "timeout_seconds": caps.timeout_seconds,
        "memory_max": caps.memory_max,
        "cpus": caps.cpus,
        "dataset_max": caps.dataset_max,
        "run_storage_max": caps.run_storage_max,
        "tmp_max": caps.tmp_max,
    }
    values["images_blueprints"] = list(answers.images_build)
    if answers.mode == "restore":
        values["restore_snapshot"] = answers.restore_snapshot
    return values


def _source(source: Source) -> dict[str, str]:
    return {
        "repo": source.repo,
        "ref": source.ref,
        "path": source.path,
        "tag_prefix": source.tag_prefix,
    }


def _yaml() -> YAML:
    yaml = yamlio.round_trip()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.width = 4096  # never re-wrap the operator's lines
    return yaml


def _strip_header(text: str) -> str:
    """Drop the leading comment block of example-machine.yml (its own header)."""
    lines = text.splitlines(keepends=True)
    index = 0
    while index < len(lines) and lines[index].startswith("#"):
        index += 1
    while index < len(lines) and not lines[index].strip():
        index += 1
    return "".join(lines[index:])


def _node(value: object) -> object:
    if isinstance(value, Mapping):
        node = CommentedMap()
        for key, item in value.items():
            node[key] = _node(item)
        return node
    if isinstance(value, list):
        seq = CommentedSeq([_node(v) for v in value])
        seq.fa.set_flow_style()
        return seq
    return value


def _same(current: object, value: object) -> bool:
    if isinstance(value, Mapping):
        return isinstance(current, Mapping) and dict(current) == dict(value)
    if isinstance(value, list):
        return isinstance(current, list) and list(current) == value
    return current == value and type(current) is type(value)


def _set(doc: CommentedMap, key: str, value: object) -> None:
    # A mapping is updated in place: ruamel keeps the comment above the next
    # key with the last value before it, so replacing the node would drop it.
    current = doc[key]
    if isinstance(current, CommentedMap) and isinstance(value, Mapping) and value:
        for name in [k for k in current if k not in value]:
            del current[name]
        for name, item in value.items():
            if name not in current:
                current[name] = _node(item)
            elif not _same(current[name], item):
                _set(current, name, item)
        return
    doc[key] = _node(value)


def render(answers: Answers, existing: str | None, example: str) -> str:
    """The host_vars file for the answers.

    Args:
        answers: The answers.
        existing: The current file (modify mode), or None for a new machine.
        example: ``example-machine.yml``.

    Returns:
        YAML text.
    """
    yaml = _yaml()
    if existing is None:
        doc = yaml.load(_strip_header(example)) or CommentedMap()
        header = HEADER.format(machine=answers.machine.name) + "\n"
    else:
        doc = yaml.load(existing) or CommentedMap()
        header = ""
    wanted = host_vars(answers)
    for key in OPTIONAL_KEYS:
        if key in doc and key not in wanted:
            del doc[key]
    for key, value in wanted.items():
        if key in doc:
            if not _same(doc[key], value):
                _set(doc, key, value)
            continue
        doc[key] = _node(value)
        comment = COMMENTS.get(key)
        if comment:
            doc.yaml_set_comment_before_after_key(key, before="\n" + comment)
    stream = io.StringIO()
    yaml.dump(doc, stream)
    return header + stream.getvalue()


def load(text: str) -> dict[str, Any]:
    """Load a host_vars file.

    Args:
        text: YAML.

    Returns:
        The variables.
    """
    loaded = YAML(typ="safe").load(text)
    return dict(loaded or {})


def to_answers(values: Mapping[str, Any], base: Answers) -> Answers:
    """Fill answers from an existing host_vars file (modify mode without an answers file).

    Args:
        values: The loaded host_vars.
        base: Answers to start from (researchers, secrets and the rest stay).

    Returns:
        New answers with every setting host_vars carries.
    """
    answers = Answers.from_dict(base.to_dict())
    for section in (
        _machine_from,
        _resources_from,
        _storage_from,
        _gpu_from,
        _remote_from,
        _backups_from,
        _platform_from,
    ):
        section(values, answers)
    return answers


def _machine_from(values: Mapping[str, Any], answers: Answers) -> None:
    machine = answers.machine
    machine.name = str(values.get("machine_name", machine.name))
    machine.lab_mode = str(values.get("lab_mode", machine.lab_mode))
    machine.provider = str(values.get("lab_provider", machine.provider))
    machine.power_policy = str(values.get("power_policy", machine.power_policy))
    machine.srv_path = str(values.get("pinka_root", machine.srv_path))
    machine.docker_data_root = str(values.get("docker_data_root", machine.docker_data_root))
    machine.node_role = str(values.get("node_role", machine.node_role))
    answers.features = Features(
        inference=str(values.get("inference", answers.features.inference)),
        training=str(values.get("training", answers.features.training)),
    )
    machine.firewall_backend = str(values.get("firewall_backend", machine.firewall_backend))
    machine.allow_community_repos = bool(values.get("allow_community_repos", False))


def _resources_from(values: Mapping[str, Any], answers: Answers) -> None:
    expected = values.get("expected") or {}
    resources = answers.resources
    resources.expected_memory_gib = expected.get("memory_gib")
    resources.expected_cpu_threads = expected.get("cpu_threads")
    resources.cpuset_services = str(values.get("cpuset_services", ""))
    resources.cpuset_targets = str(values.get("cpuset_targets", ""))
    resources.postgres_shared_buffers = str(values.get("postgres_shared_buffers", ""))
    resources.concurrent_targets = int(
        values.get("incus_concurrent_targets", resources.concurrent_targets)
    )


def _storage_from(values: Mapping[str, Any], answers: Answers) -> None:
    storage = answers.storage
    storage.source = str(values.get("incus_pool_source", storage.source))
    size = str(values.get("incus_pool_size", f"{storage.loop_size_gib}GiB"))
    if size.endswith("GiB") and size[:-3].isdigit():
        storage.loop_size_gib = int(size[:-3])
    storage.device = str(values.get("incus_pool_device", ""))
    storage.device_confirm = str(values.get("incus_pool_device_confirm", ""))
    storage.force_wipe = bool(values.get("incus_pool_device_force_wipe", False))


def _gpu_from(values: Mapping[str, Any], answers: Answers) -> None:
    gpu = answers.gpu
    gpu.install_driver = str(values.get("install_nvidia_driver", gpu.install_driver))
    gpu.vllm_profile = str(values.get("vllm_profile", ""))
    deployment = values.get("vllm_default_deployment")
    gpu.default_model = str(deployment["model_id"]) if deployment else "none"
    gpu.start_serving = bool(deployment.get("start_after_install", True)) if deployment else True


def _remote_from(values: Mapping[str, Any], answers: Answers) -> None:
    remote = answers.remote_access
    remote.grafana_tailscale_serve = bool(values.get("grafana_tailscale_serve", False))
    remote.tailscale_serve_consent = bool(values.get("tailscale_serve_consent", False))
    remote.ssh_extra_allowed_interfaces = list(values.get("ssh_extra_allowed_interfaces", []))
    remote.close_listeners = [str(n) for n in values.get("firewall_close_listeners") or []]
    remote.close_install_path = bool(values.get("firewall_close_install_path", False))
    remote.tailscale_tag = str(values.get("tailscale_tag", ""))
    remote.accept_dns = bool(values.get("tailscale_accept_dns", False))
    answers.split_inference_peer = str(
        values.get("inference_lab_node") or values.get("inference_remote_node") or ""
    )


def _backups_from(values: Mapping[str, Any], answers: Answers) -> None:
    backups = answers.backups
    match = ONCALENDAR_RE.fullmatch(str(values.get("backup_schedule", "")))
    if match:
        backups.schedule = f"{match.group(1)}:{match.group(2)}"
    retention = values.get("backup_retention") or {}
    backups.retention = Retention(
        **{k: int(v) for k, v in retention.items() if k in ("daily", "weekly", "monthly")}
    )
    backups.include_models = bool(values.get("backup_include_models", False))
    backups.include_secrets = bool(values.get("backup_include_secrets", False))
    backups.pg_dumps_kept = int(values.get("backup_pgdump_keep", backups.pg_dumps_kept))
    default = answers.telemetry_retention
    answers.telemetry_retention = TelemetryRetention(
        prometheus=str(values.get("observability_prometheus_retention", default.prometheus)),
        prometheus_size=str(
            values.get("observability_prometheus_retention_size", default.prometheus_size)
        ),
        loki=str(values.get("observability_loki_retention", default.loki)),
        tempo=str(values.get("observability_tempo_retention", default.tempo)),
    )


def _platform_from(values: Mapping[str, Any], answers: Answers) -> None:
    def source(raw: object) -> Source:
        data = raw if isinstance(raw, Mapping) else {}
        return Source(**{k: str(data.get(k) or "") for k in ("repo", "ref", "path", "tag_prefix")})

    answers.platform = source(values.get("platform"))
    answers.content = {
        str(name): source(raw) for name, raw in (values.get("content") or {}).items()
    }
    trust = answers.source_trust
    trust.known_hosts = list(values.get("pinka_source_known_hosts", trust.known_hosts))
    trust.require_signed_tags = bool(values.get("require_signed_tags", False))
    trust.allowed_signers = list(values.get("pinka_allowed_signers", trust.allowed_signers))
    answers.platform_settings = dict(values.get("platform_settings") or {})
    arbiter = values.get("gpu_arbiter") or {}
    answers.gpu_arbiter = GpuArbiter(
        after_boot=str(arbiter.get("after_boot", answers.gpu_arbiter.after_boot)),
        restore_after_training=bool(
            arbiter.get("restore_after_training", answers.gpu_arbiter.restore_after_training)
        ),
    )
    caps = dict(values.get("training_caps") or {})
    default = answers.training_caps
    answers.training_caps = TrainingCaps(
        timeout_seconds=int(caps.get("timeout_seconds", default.timeout_seconds)),
        memory_max=str(caps.get("memory_max", default.memory_max)),
        cpus=int(caps.get("cpus", default.cpus)),
        dataset_max=str(caps.get("dataset_max", default.dataset_max)),
        run_storage_max=str(caps.get("run_storage_max", default.run_storage_max)),
        tmp_max=str(caps.get("tmp_max", default.tmp_max)),
    )
    answers.images_build = list(values.get("images_blueprints", answers.images_build))
    answers.restore_snapshot = str(values.get("restore_snapshot", answers.restore_snapshot))
