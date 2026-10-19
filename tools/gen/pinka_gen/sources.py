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
