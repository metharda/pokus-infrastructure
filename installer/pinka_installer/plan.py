"""The requirements plan of a machine (BUILD_PROMPT §2.3).

Preflight computes what the operator's choices need from a plan
(``pinka-preflight --plan``, ``preflight/schema/plan.schema.json``).
``preflight/requirements.json`` carries the configured defaults, the Ansible
defaults compiled by ``make generate``; the plan overlays what
``group_vars/all/main.yml`` and the machine's ``host_vars`` set, as Ansible
reads them (host_vars win, a top-level key replaces the whole value), and the
selected model's files from ``models.lock.yml``. Bootstrap writes it before
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
        raise PlanError(
            f"vllm_default_deployment.model_id {model_id!r} is not in models.lock.yml "
            "(pin it with the installer, docs/how-to/add-a-model.md)"
        )
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
    for name, key in (("pinka_root", "srv_path"), ("docker_data_root", "docker_data_root")):
        if name in variables:
            result[key] = _checked(name, variables[name], validators.absolute_path)
    if "incus_pool_source" in variables:
        source = _plain("incus_pool_source", variables["incus_pool_source"])
        if source not in ("loop", "device"):
            raise PlanError(f"incus_pool_source must be loop or device (got {source!r})")
        result["pool"]["source"] = source
    if "incus_pool_size" in variables:
        size = _checked("incus_pool_size", variables["incus_pool_size"], validators.size)
        result["pool"]["size_gib"] = validators.size_bytes(size) / GIB
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
    if "images_blueprints" in variables:
        result["images"] = [_plain("images_blueprints", b) for b in variables["images_blueprints"]]
    if "observability_gpu_exporter" in variables:
        exporter = _plain("observability_gpu_exporter", variables["observability_gpu_exporter"])
        if exporter not in ("nvidia_smi", "dcgm"):
            raise PlanError(f"observability_gpu_exporter must be nvidia_smi or dcgm ({exporter!r})")
        result["gpu_exporter"] = exporter
    caps = variables.get("training_caps")
    if isinstance(caps, Mapping):
        for name, key in (
            ("memory_max", "memory_max_gib"),
            ("run_storage_max", "run_storage_max_gib"),
        ):
            if name in caps:
                value = _checked(f"training_caps.{name}", caps[name], validators.cap_size)
                result["training"][key] = validators.cap_size_gib(value)
    _retention(result["retention"], variables)
    return result


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
    requirements = json.loads(
        (paths.infra / "preflight" / "requirements.json").read_text(encoding="utf-8")
    )
    variables = {**_yaml(paths.group_vars_main), **_yaml(host_vars)}
    return plan(requirements["defaults"], variables, _yaml(paths.infra / "models.lock.yml"))
