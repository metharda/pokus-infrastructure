"""Compile YAML sources to the JSON files that preflight reads."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import yaml

VERSIONS_LOCK_PREFIX = "@versions.lock:"


def load_yaml(path: Path) -> Any:  # noqa: ANN401 - YAML document
    """Load a YAML file.

    Args:
        path: File path.

    Returns:
        The parsed document.
    """
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def jsonable(value: Any) -> Any:  # noqa: ANN401 - YAML value
    """Convert YAML dates to ISO strings, recursively.

    Args:
        value: Parsed YAML.

    Returns:
        A JSON-compatible structure.
    """
    if isinstance(value, dt.date | dt.datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [jsonable(v) for v in value]
    return value


def resolve_lock_refs(value: Any, lock: Any) -> Any:  # noqa: ANN401 - YAML value
    """Replace ``"@versions.lock:<dotted.key>"`` strings with values from the lock file.

    Args:
        value: Parsed YAML.
        lock: Parsed ``versions.lock.yml``.

    Returns:
        The structure with references resolved.

    Raises:
        KeyError: If a reference does not exist.
    """
    if isinstance(value, str) and value.startswith(VERSIONS_LOCK_PREFIX):
        node = lock
        for part in value[len(VERSIONS_LOCK_PREFIX) :].split("."):
            node = node[part]
        return node
    if isinstance(value, dict):
        return {k: resolve_lock_refs(v, lock) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_lock_refs(v, lock) for v in value]
    return value


def dump_json(data: Any, source: str) -> str:  # noqa: ANN401 - JSON document
    """Serialize generated JSON with a provenance key.

    Args:
        data: Document.
        source: Source file the JSON is generated from.

    Returns:
        Indented JSON with a trailing newline.
    """
    doc = {"_generated_from": f"{source} by tools/gen (make generate); do not edit", **data}
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def support_matrix_json(root: Path) -> str:
    """Compile ``preflight/support-matrix.yml``.

    Args:
        root: Component root.

    Returns:
        JSON text.
    """
    lock = load_yaml(root / "versions.lock.yml")
    matrix = jsonable(load_yaml(root / "preflight/support-matrix.yml"))
    resolved = resolve_lock_refs(matrix, jsonable(lock))
    if not isinstance(resolved, dict):
        raise TypeError("support matrix must be a mapping")
    return dump_json(resolved, "preflight/support-matrix.yml")


def endpoints_json(root: Path) -> str:
    """Compile ``preflight/endpoints.yml``.

    Args:
        root: Component root.

    Returns:
        JSON text.
    """
    data = jsonable(load_yaml(root / "preflight/endpoints.yml"))
    if not isinstance(data, dict):
        raise TypeError("endpoints file must be a mapping")
    return dump_json(data, "preflight/endpoints.yml")


# The tailnet range a VM collector address must not fall into (BUILD_PROMPT §2.2).
TAILNET_RANGE = "100.64.0.0/10"


def nvidia_gpus_json(root: Path) -> str:
    """Compile the GPU lists of the pinned NVIDIA driver releases for preflight (§2.2, §10.1).

    Args:
        root: Component root.

    Returns:
        JSON text: per driver release, the PCI device IDs its open kernel
        module supports and those it supports only with its proprietary
        module (``nvidia_driver_gpus.releases.<version>`` of versions.lock.yml).
    """
    lock = load_yaml(root / "versions.lock.yml")
    data = {
        "schema_version": 1,
        "drivers": {
            str(version): {
                name: sorted(str(i) for i in entry[name])
                for name in ("open_module", "proprietary_only")
            }
            for version, entry in sorted(lock["nvidia_driver_gpus"]["releases"].items())
        },
    }
    return dump_json(data, "versions.lock.yml (nvidia_driver_gpus)")


def lab_defaults_json(root: Path) -> str:
    """Compile the lab's default ports and addresses for preflight (§2.2, §12.11).

    Args:
        root: Component root.

    Returns:
        JSON text with the default ports (``pinka_default_ports``), the VM
        collector address and interface, the Docker address pools, the
        tailnet range, the Tailscale tags pinka gives a node it joins and
        the names of the Incus bridges pinka creates (exact names and
        prefixes, for the clash check against LXD, §7.7), and the UDP ports
        pinka's strict input policy accepts on every interface besides the
        DHCP clients' (Tailscale's WireGuard port; the pre-existing access of
        §2.2).
    """
    group_vars = load_yaml(root / "ansible/group_vars/all/main.yml")
    docker = load_yaml(root / "ansible/roles/docker/defaults/main.yml")
    firewall = load_yaml(root / "ansible/roles/firewall/defaults/main.yml")
    patterns = [
        str(p)
        for p in group_vars["pinka_incus_bridge_patterns"]
        + group_vars["pinka_incus_personal_bridge_patterns"]
    ]
    data = {
        "schema_version": 1,
        "ports": jsonable(group_vars["pinka_default_ports"]),
        "vm_collector": {
            "address": str(group_vars["vm_collector_address"]),
            "interface": str(group_vars["pinka_vm_otlp"]["interface"]),
        },
        "docker_address_pools": jsonable(docker["docker_address_pools"]),
        "tailnet": TAILNET_RANGE,
        "tailscale_tags": sorted(str(t) for t in group_vars["pinka_tailscale_tags"].values()),
        "incus_bridges": {
            "names": sorted(p for p in patterns if "*" not in p),
            "prefixes": sorted(p.removesuffix("*") for p in patterns if p.endswith("*")),
        },
        "admitted_udp_ports": [int(firewall["firewall_tailscale_udp_port"])],
    }
    return dump_json(
        data,
        "ansible/group_vars/all/main.yml, ansible/roles/docker/defaults/main.yml and"
        " ansible/roles/firewall/defaults/main.yml",
    )


GIB = 1024**3
# Sizes as Incus (300GiB, 2GB) and Docker (24G) write them; Docker's and the
# Prometheus units without "i" are powers of 2 as well, Incus's KB-TB are not.
SIZE_UNITS = {
    "B": 1,
    "KiB": 1024,
    "MiB": 1024**2,
    "GiB": 1024**3,
    "TiB": 1024**4,
    "K": 1024,
    "M": 1024**2,
    "G": 1024**3,
    "T": 1024**4,
}
DECIMAL_UNITS = {"KB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4}
DAY_UNITS = {"h": 1 / 24, "d": 1, "w": 7, "y": 365}


def size_gib(value: Any, *, decimal: bool = True) -> float:  # noqa: ANN401 - YAML value
    """A size setting in GiB.

    Args:
        value: Bytes, or a number with a unit (``300GiB``, ``24G``, ``50GB``).
        decimal: KB-TB are powers of 10 (Incus); false reads them as powers of
            2 (Prometheus).

    Returns:
        The size in GiB.

    Raises:
        ValueError: If the size is malformed.
    """
    text = str(value).strip()
    number = text.rstrip("BKMGTi")
    unit = text[len(number) :]
    factors = {**SIZE_UNITS, **(DECIMAL_UNITS if decimal else {})}
    if not decimal:
        factors.update({k: SIZE_UNITS[k[0]] for k in DECIMAL_UNITS})
    if unit and unit not in factors:
        raise ValueError(f"not a size: {value!r}")
    return float(number) * factors.get(unit, 1) / GIB


def days(value: Any) -> float:  # noqa: ANN401 - YAML value
    """A retention setting in days.

    Args:
        value: Such as ``90d``, ``2160h`` or ``1w``.

    Returns:
        The number of days.

    Raises:
        ValueError: If the duration is malformed.
    """
    text = str(value).strip()
    if not text or text[-1] not in DAY_UNITS or not text[:-1].isdigit():
        raise ValueError(f"not a retention period: {value!r}")
    return float(int(text[:-1]) * DAY_UNITS[text[-1]])


def plan_defaults(root: Path) -> dict[str, Any]:
    """The requirements plan of the configured defaults (§2.3).

    Args:
        root: Component root.

    Returns:
        The plan preflight uses without ``--plan``, from group_vars and the
        roles' defaults.
    """
    roles = root / "ansible/roles"
    group_vars = load_yaml(root / "ansible/group_vars/all/main.yml")
    incus = load_yaml(roles / "incus/defaults/main.yml")
    observability = load_yaml(roles / "observability/defaults/main.yml")
    backup = load_yaml(roles / "backup/defaults/main.yml")
    images = load_yaml(roles / "images/defaults/main.yml")
    docker = load_yaml(roles / "docker/defaults/main.yml")
    vllm = load_yaml(roles / "vllm/defaults/main.yml")
    if vllm["vllm_default_deployment"] is not None:
        raise ValueError("the default plan has no model: vllm_default_deployment must be null")
    caps = group_vars["training_caps"]
    return {
        "schema_version": 1,
        "node_role": str(group_vars["node_role"]),
        "srv_path": str(group_vars["pinka_root"]),
        "docker_data_root": str(docker["docker_data_root"]),
        "pool": {
            "source": str(incus["incus_pool_source"]),
            "size_gib": size_gib(incus["incus_pool_size"]),
        },
        "concurrent_targets": int(incus["incus_concurrent_targets"]),
        "model": None,
        "images": [str(name) for name in images["images_blueprints"]],
        "gpu_exporter": str(observability["observability_gpu_exporter"]),
        "training": {
            "memory_max_gib": size_gib(caps["memory_max"]),
            "run_storage_max_gib": size_gib(caps["run_storage_max"]),
        },
        "retention": {
            "prometheus_days": days(observability["observability_prometheus_retention"]),
            "prometheus_size_gib": size_gib(
                observability["observability_prometheus_retention_size"], decimal=False
            ),
            "loki_days": days(observability["observability_loki_retention"]),
            "tempo_days": days(observability["observability_tempo_retention"]),
            "pg_dumps": int(backup["backup_pgdump_keep"]),
        },
    }


def requirements_json(root: Path) -> str:
    """Compile ``preflight/requirements.yml`` for the requirements computation (§2.3).

    Args:
        root: Component root.

    Returns:
        JSON text: the source with, per container image, its reference and
        compressed size per platform (versions.lock.yml), per golden image
        blueprint its version and estimated size (``images/*/image.yaml``), the
        memory of the target profiles (the incus role) and the default plan.

    Raises:
        ValueError: If the images listed and the lock's images differ.
    """
    data = jsonable(load_yaml(root / "preflight/requirements.yml"))
    lock = jsonable(load_yaml(root / "versions.lock.yml"))["containers"]["images"]
    if set(data["images"]) != set(lock):
        raise ValueError(
            "preflight/requirements.yml images must list every image of versions.lock.yml "
            f"containers.images: missing {sorted(set(lock) - set(data['images']))}, "
            f"unknown {sorted(set(data['images']) - set(lock))}"
        )
    data["images"] = {
        name: {
            **spec,
            "image": lock[name]["image"],
            "digest": lock[name]["digest"],
            "sizes": lock[name]["sizes"],
        }
        for name, spec in sorted(data["images"].items())
    }
    blueprints = {}
    for path in sorted((root / "images").glob("*/image.yaml")):
        blueprint = load_yaml(path)
        blueprints[str(blueprint["name"])] = {
            "version": str(blueprint["version"]),
            "estimated_size_gib": float(blueprint["estimated_size_gib"]),
        }
    data["blueprints"] = blueprints
    incus = load_yaml(root / "ansible/roles/incus/defaults/main.yml")
    data["disk"]["pool_file"] = f"{data['disk']['incus_dir']}/disks/{incus['incus_pool_name']}.img"
    data["target_memory_gib"] = {
        name: size_gib(incus[f"incus_{key}"]["memory"])
        for name, key in (
            ("container", "target_container"),
            ("vm", "target_vm"),
            ("probe_client", "probe_client"),
            ("build", "build_instance"),
        )
    }
    data["defaults"] = plan_defaults(root)
    return dump_json(
        data,
        "preflight/requirements.yml, versions.lock.yml, images/*/image.yaml and the"
        " Ansible defaults",
    )
