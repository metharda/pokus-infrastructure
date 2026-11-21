"""The requirements plan of a machine.

Preflight computes what the operator's choices need from a plan
(``pinka-preflight --plan``, ``preflight/schema/plan.schema.json``).
``preflight/requirements.json`` carries the configured defaults, the Ansible
defaults compiled by ``make generate``; the plan overlays what
``group_vars/all/main.yml`` and the machine's ``host_vars`` set, as Ansible
reads them (host_vars win, a top-level key replaces the whole value, except
the incus role's limits and sizes mappings, which the role merges key by key
with their defaults and so does the plan), and the selected model's files
from ``models.lock.yml``. Bootstrap writes it before
preflight runs and passes the same file to the play.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable, Mapping
from typing import Any

from ruamel.yaml import YAML

from pinka_installer import validators
from pinka_installer.paths import Paths

GIB = 2**30


class PlanError(ValueError):
    """A setting the plan needs cannot be read."""


def _plain(name: str, value: object) -> str:
    text = str(value)
    if "{{" in text or "{%" in text:
        raise PlanError(f"{name} must be a plain value for the requirements, not a template")
    return text


def _checked(name: str, value: object, check: Callable[[str], str | None]) -> str:
    text = _plain(name, value)
    problem = check(text)
    if problem:
        raise PlanError(f"{name}: {problem} (got {text!r})")
    return text


def _model(variables: Mapping[str, Any], lock: Mapping[str, Any]) -> dict[str, Any] | None:
    deployment = variables.get("vllm_default_deployment")
    if not deployment:
        return None
    if not isinstance(deployment, Mapping) or "model_id" not in deployment:
        raise PlanError("vllm_default_deployment must be null or name a model_id")
    model_id = _plain("vllm_default_deployment.model_id", deployment["model_id"])
    entry = next((m for m in lock.get("models") or [] if m.get("id") == model_id), None)
    if entry is None:
        raise PlanError(f"vllm_default_deployment.model_id {validators.unpinned_model(model_id)}")
    sizes = [f.get("size") for f in entry.get("files") or []]
    if not sizes or not all(isinstance(s, int) and s >= 0 for s in sizes):
        raise PlanError(f"models.lock.yml: {model_id} has a file without its size; pin it again")
    return {
        "id": model_id,
        "hf_repo": str(entry["hf_repo"]),
        "revision": str(entry["revision"]),
        "size_bytes": sum(int(s) for s in sizes),
    }


def plan(  # noqa: PLR0912 - one branch per setting
    defaults: Mapping[str, Any], variables: Mapping[str, Any], lock: Mapping[str, Any]
) -> dict[str, Any]:
    """The plan of the settings Ansible will use.

    Args:
        defaults: The configured defaults (``requirements.json`` ``defaults``).
        variables: group_vars overlaid with host_vars.
        lock: ``models.lock.yml``.

    Returns:
        The plan, complete: every key the schema knows.

    Raises:
        PlanError: If a setting is a template or invalid, or the model is not pinned.
    """
    result: dict[str, Any] = copy.deepcopy(dict(defaults))
    if "node_role" in variables:
        role = _plain("node_role", variables["node_role"])
        if role not in ("full", "lab", "inference"):
            raise PlanError(f"node_role must be full, lab or inference (got {role!r})")
        result["node_role"] = role
    _switches(result, variables)
    for name, key in (("pinka_root", "srv_path"), ("docker_data_root", "docker_data_root")):
        if name in variables:
            result[key] = _checked(name, variables[name], validators.absolute_path)
    _lab(result, variables)
    if "incus_concurrent_targets" in variables:
        count = variables["incus_concurrent_targets"]
        problem = (
            validators.concurrent_targets(count) if isinstance(count, int) else "a whole number"
        )
        if problem:
            raise PlanError(f"incus_concurrent_targets: {problem} (got {count!r})")
        result["concurrent_targets"] = count
    if "vllm_default_deployment" in variables:
        result["model"] = _model(variables, lock)
    if "vllm_profile" in variables:
        result["vllm_profile"] = _plain("vllm_profile", variables["vllm_profile"])
    if "images_blueprints" in variables:
        result["images"] = [_plain("images_blueprints", b) for b in variables["images_blueprints"]]
    if "observability_gpu_exporter" in variables:
        exporter = _plain("observability_gpu_exporter", variables["observability_gpu_exporter"])
        if exporter not in ("nvidia_smi", "dcgm"):
            raise PlanError(f"observability_gpu_exporter must be nvidia_smi or dcgm ({exporter!r})")
        result["gpu_exporter"] = exporter
    caps = variables.get("training_caps")
    if isinstance(caps, Mapping):
        # A top-level key replaces the whole value: a memory_max left out is auto.
        memory = _checked(
            "training_caps.memory_max", caps.get("memory_max", "auto"), validators.memory_max
        )
        result["training"]["memory_max_gib"] = (
            None if memory == "auto" else validators.cap_size_gib(memory)
        )
        if "run_storage_max" in caps:
            value = _checked(
                "training_caps.run_storage_max", caps["run_storage_max"], validators.cap_size
            )
            result["training"]["run_storage_max_gib"] = validators.cap_size_gib(value)
    _retention(result["retention"], variables)
    return result


def _lab(result: dict[str, Any], variables: Mapping[str, Any]) -> None:
    """The Incus pool, the researchers and their personal limit into ``result``."""
    if "incus_pool_source" in variables:
        source = _plain("incus_pool_source", variables["incus_pool_source"])
        if source not in ("loop", "device"):
            raise PlanError(f"incus_pool_source must be loop or device (got {source!r})")
        result["pool"]["source"] = source
    if "incus_pool_size" in variables:
        size = _checked("incus_pool_size", variables["incus_pool_size"], validators.pool_size)
        result["pool"]["size_gib"] = None if size == "auto" else validators.size_bytes(size) / GIB
    if "incus_pool_device" in variables:
        result["pool"]["device"] = _plain("incus_pool_device", variables["incus_pool_device"])
    if "incus_pool_volume_size" in variables:
        size = _checked(
            "incus_pool_volume_size", variables["incus_pool_volume_size"], validators.pool_size
        )
        result["pool"]["volume_size_gib"] = (
            None if size == "auto" else validators.size_bytes(size) / GIB
        )
    if "researchers" in variables:
        people = variables["researchers"]
        if not isinstance(people, list):
            raise PlanError("researchers must be a list")
        result["researchers"] = len(people)
    _incus_disk(result, variables)


# The incus role's sizes mappings the pool's sum reads: (variable, key, plan key).
PROFILE_SIZES = (
    ("incus_target_vm", "root_size", "target_vm_root_gib"),
    ("incus_target_vm", "size_state", "target_vm_state_gib"),
    ("incus_target_container", "root_size", "target_container_root_gib"),
    ("incus_probe_client", "root_size", "probe_client_root_gib"),
    ("incus_build_instance", "root_size", "build_root_gib"),
    ("incus_build_instance", "size_state", "build_state_gib"),
)
# The projects' limits mappings whose limits.disk counts in the pool's sum,
# with the plan key it sets (None: personal_disk_gib, outside incus_disk).
PROJECT_LIMITS: tuple[tuple[str, str | None], ...] = (
    ("incus_bench_limits", "bench_limits_disk_gib"),
    ("incus_build_limits", "build_limits_disk_gib"),
    ("incus_user_limits", None),
)


def _mapping(name: str, variables: Mapping[str, Any]) -> Mapping[str, Any]:
    """A limits or sizes mapping of host_vars: only the keys it sets."""
    value = variables.get(name)
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise PlanError(f"{name} must be a mapping of the keys it changes (got {value!r})")
    return value


def _incus_disk(result: dict[str, Any], variables: Mapping[str, Any]) -> None:
    """The sizes the pool's sum reads, merged key by key as the incus role does.

    A key host_vars sets replaces the default; the others keep it. A
    limits.disk set to null or empty is refused: every project's limit
    counts in the pool's size.
    """
    sizes = result["incus_disk"]
    for name, key, plan_key in PROFILE_SIZES:
        mapping = _mapping(name, variables)
        if key in mapping:
            text = _checked(f"{name}.{key}", mapping[key], validators.size)
            sizes[plan_key] = validators.size_bytes(text) / GIB
    for name, limit_key in PROJECT_LIMITS:
        mapping = _mapping(name, variables)
        if "limits.disk" not in mapping:
            continue
        value = mapping["limits.disk"]
        if value is None or not str(value).strip():
            raise PlanError(
                f"{name}: limits.disk is null or empty; the Incus pool's size counts every "
                "project's limits.disk, so set a size or leave the key out to take the default"
            )
        text = _checked(f"{name}.limits.disk", value, validators.size)
        gib = validators.size_bytes(text) / GIB
        if limit_key is None:
            result["personal_disk_gib"] = gib
        else:
            sizes[limit_key] = gib


def _switches(result: dict[str, Any], variables: Mapping[str, Any]) -> None:
    """The inference and training switches into ``result``."""
    if "inference" in variables:
        inference = _plain("inference", variables["inference"])
        if inference not in ("auto", "on", "off"):
            raise PlanError(f"inference must be auto, on or off (got {inference!r})")
        result["inference"] = inference
    if result["node_role"] == "inference" and result["inference"] == "off":
        raise PlanError(validators.INFERENCE_NODE_OFF)
    if result["node_role"] == "lab" and result["inference"] == "on":
        raise PlanError(validators.LAB_ON)
    if "training" in variables:
        training = _plain("training", variables["training"])
        if training not in ("auto", "cpu", "off"):
            raise PlanError(f"training must be auto, cpu or off (got {training!r})")
        result["training"]["mode"] = training


def _retention(retention: dict[str, Any], variables: Mapping[str, Any]) -> None:
    """The retention settings into ``retention`` (days, GiB, dumps)."""
    for name, key, units in (
        ("observability_prometheus_retention", "prometheus_days", "hdwy"),
        ("observability_loki_retention", "loki_days", "hdwy"),
        ("observability_tempo_retention", "tempo_days", "h"),
    ):
        if name in variables:
            value = _plain(name, variables[name])
            problem = validators.retention_period(value, units)
            if problem:
                raise PlanError(f"{name}: {problem} (got {value!r})")
            retention[key] = validators.retention_days(value)
    if "observability_prometheus_retention_size" in variables:
        name = "observability_prometheus_retention_size"
        value = _checked(name, variables[name], validators.prometheus_size)
        retention["prometheus_size_gib"] = validators.prometheus_size_gib(value)
    if "backup_pgdump_keep" in variables:
        keep = variables["backup_pgdump_keep"]
        if not isinstance(keep, int) or isinstance(keep, bool) or keep < 1:
            raise PlanError(f"backup_pgdump_keep must be a whole number of dumps (got {keep!r})")
        retention["pg_dumps"] = keep


def _yaml(path: Any) -> dict[str, Any]:  # noqa: ANN401 - a Path
    if not path.exists():
        return {}
    loaded = YAML(typ="safe").load(path.read_text(encoding="utf-8"))
    return dict(loaded or {})


def machine_plan(paths: Paths, machine: str) -> dict[str, Any]:
    """The plan of a machine's configuration in the checkout.

    Args:
        paths: The installer's paths.
        machine: The machine name (``host_vars/<machine>.yml``).

    Returns:
        The plan.

    Raises:
        PlanError: If the host_vars file is missing or a setting cannot be used.
    """
    host_vars = paths.host_vars(machine)
    if not host_vars.exists():
        raise PlanError(f"{host_vars} does not exist")
    return plan_of(paths, _yaml(host_vars), _yaml(paths.infra / "models.lock.yml"))


def plan_of(paths: Paths, host_vars: Mapping[str, Any], lock: Mapping[str, Any]) -> dict[str, Any]:
    """The plan of host variables over the checkout's group_vars.

    Bootstrap's plan (:func:`machine_plan`, from the host_vars file) and the
    installer's capacity plan screen (from the host_vars of the answers being
    collected, before anything is written) both come from here.

    Args:
        paths: The installer's paths.
        host_vars: The machine's host variables.
        lock: ``models.lock.yml``.

    Returns:
        The plan.

    Raises:
        PlanError: If a setting cannot be used, or names a serving profile or
            a golden image blueprint that does not exist.
    """
    requirements = json.loads(
        (paths.infra / "preflight" / "requirements.json").read_text(encoding="utf-8")
    )
    variables = {**_yaml(paths.group_vars_main), **host_vars}
    found = plan(requirements["defaults"], variables, lock)
    profiles = requirements["serving"]["profiles"]
    if found["vllm_profile"] not in profiles:
        raise PlanError(validators.unknown_profile(found["vllm_profile"], profiles))
    blueprints = requirements["blueprints"]
    for name in found["images"]:
        if name not in blueprints:
            raise PlanError(validators.unknown_blueprint(name, blueprints))
    return found
