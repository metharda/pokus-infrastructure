"""The principals and endpoints files infra writes for the active platform (§12.3)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pinka_platform import layout, names, toml
from pinka_platform.contract import Contract

SCHEMA_VERSION = 1
PRINCIPALS_HEADER = (
    "Managed by pinka (principals.json, contract v2 §12.3). Local changes will be overwritten."
)
ENDPOINTS_HEADER = (
    "Managed by pinka (endpoints.toml, contract v2 §12.3). Local changes will be overwritten.\n"
    "The addresses and paths infra provides to the platform, with the effective ports."
)
INFERENCE_MODES = ("local", "remote", "scripted")


def _clients(
    socket: Mapping[str, Any], manifest: Mapping[str, Any], contract: Contract
) -> dict[str, Any]:
    p = manifest["name"]
    profiles = [c for c in socket["clients"] if c in contract.profiles]
    accounts = sorted(
        names.account(p, s["name"]) for s in manifest["services"] if s["profile"] in profiles
    )
    humans = [c for c in socket["clients"] if c in contract.human_classes]
    return {
        "profiles": profiles,
        "accounts": accounts,
        "humans": humans,
        "human_groups": [contract.human_classes[h]["group"] for h in humans],
        "root": "root" in socket["clients"],
    }


def principals(
    manifest: Mapping[str, Any], contract: Contract, uids: Mapping[str, int]
) -> dict[str, Any]:
    """Build ``/etc/pinka/platform/principals.json``.

    Args:
        manifest: A valid manifest.
        contract: The contract.
        uids: UID of every service and job account.

    Returns:
        The principals document.
    """
    p = manifest["name"]

    def member(entry: Mapping[str, Any]) -> dict[str, Any]:
        account = names.account(p, entry["name"])
        return {
            "name": entry["name"],
            "profile": entry["profile"],
            "account": account,
            "uid": uids[account],
        }

    def service(entry: Mapping[str, Any]) -> dict[str, Any]:
        account = names.account(p, entry["name"])
        return {
            **member(entry),
            "config": layout.config_file(entry["name"]) if "config" in entry else None,
            "secrets_dir": f"{layout.SECRETS_DIR}/{account}" if entry.get("secrets") else None,
            "state": {d: layout.state_dir(p, entry["name"], d) for d in entry.get("state", [])},
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "contract_version": contract.version,
        "platform": p,
        "groups": {h: contract.human_classes[h]["group"] for h in sorted(contract.human_classes)},
        "services": [service(s) for s in manifest["services"]],
        "jobs": [member(j) for j in manifest.get("jobs", [])],
        "sockets": [
            {
                "service": s["service"],
                "name": s["name"],
                "path": layout.socket_path(s["service"], s["name"]),
                "owner": names.account(p, s["service"]),
                "group": names.client_group(p, s["service"], s["name"]),
                "mode": "0660",
                "fd_name": s["name"],
                "agent_dedicated": bool(s.get("untrusted_clients", False)),
                "clients": _clients(s, manifest, contract),
            }
            for s in manifest.get("sockets", [])
        ],
    }


def principals_json(document: Mapping[str, Any]) -> str:
    """Serialize the principals document.

    Args:
        document: The output of :func:`principals`.

    Returns:
        JSON text with a stable key order.
    """
    return (
        json.dumps({"_comment": PRINCIPALS_HEADER, **document}, indent=2, ensure_ascii=False) + "\n"
    )


@dataclass(frozen=True)
class LabFacts:
    """The host settings the endpoints file reports.

    Attributes:
        machine_name: The machine's name.
        lab_mode: ``production`` or ``staging``.
        node_role: ``full``, ``lab`` or ``inference``.
        ports: Effective ports (§12.11), by the keys of ``pinka_ports``.
        inference_mode: ``local``, ``remote`` or ``scripted``.
        scripted_api_url: The OpenAI-compatible endpoint for ``scripted``.
        vm_collector_address: Address of the collector listener for VM targets.
        cpuset_targets: CPUs of the target instances, when set.
    """

    machine_name: str
    lab_mode: str
    node_role: str
    ports: Mapping[str, int]
    inference_mode: str = "local"
    scripted_api_url: str = ""
    vm_collector_address: str = "198.18.0.1"
    cpuset_targets: str = ""


def endpoints(manifest: Mapping[str, Any], facts: LabFacts) -> dict[str, Any]:
    """Build ``/etc/pinka/platform/endpoints.toml``.

    Facilities appear only when the manifest requires them; the lab facts,
    the signing key, the attestation directories and the session records are
    always there.

    Args:
        manifest: A valid manifest.
        facts: The host settings.

    Returns:
        The endpoints document.

    Raises:
        ValueError: An unknown inference mode, or ``scripted`` outside staging.
    """
    p = manifest["name"]
    requires = set(manifest.get("requires", []))
    ports = facts.ports
    loopback = "127.0.0.1"
    doc: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "platform": p,
        "lab": {
            "machine": facts.machine_name,
            "lab_mode": facts.lab_mode,
            "node_role": facts.node_role,
        },
    }
    if "postgres" in requires:
        doc["postgres"] = {
            "socket_dir": layout.POSTGRES_SOCKET_DIR,
            "reader_socket_dir": layout.POSTGRES_READER_SOCKET_DIR,
            "database": p,
            "migrator_role": names.role(p, "migrator"),
            "reader_role": names.role(p, "reader"),
            "grafana_role": names.role(p, "grafana"),
            "roles": {
                s["name"]: names.role(p, s["name"])
                for s in manifest["services"]
                if s["profile"] != "untrusted-agent"
            },
        }
    if "telemetry" in requires:
        doc["telemetry"] = {
            "otlp_grpc": f"{loopback}:{ports['otlp_grpc']}",
            "otlp_http": f"{loopback}:{ports['otlp_http']}",
            "vm_collector_grpc": f"{facts.vm_collector_address}:{ports['otlp_grpc']}",
            "vm_collector_http": f"{facts.vm_collector_address}:{ports['otlp_http']}",
            "agent_otlp_socket": layout.AGENT_OTLP_SOCKET,
            "query_socket": layout.QUERY_SOCKET,
        }
    if "inference" in requires:
        doc["inference"] = _inference(facts)
    if "opa" in requires:
        opa: dict[str, Any] = {"url": f"http://{loopback}:{ports['opa']}"}
        if "policy" in manifest:
            opa["decision_path"] = "/v1/data/" + manifest["policy"]["decision_path"]
        doc["opa"] = opa
    if "incus" in requires:
        incus: dict[str, Any] = {
            "url": f"https://{loopback}:{ports['incus_https']}",
            "cert_dir": layout.INCUS_CERT_DIR,
            "projects": ["bench", "build"],
            "run_network_socket": layout.RUN_NETWORK_SOCKET,
        }
        if facts.cpuset_targets:
            incus["cpuset_targets"] = facts.cpuset_targets
        doc["incus"] = incus
    if "gpu" in requires:
        doc["gpu"] = {"arbiter_socket": layout.GPU_SOCKET}
    doc["signing"] = {
        "key_file": f"{layout.SIGNING_DIR}/{p}/{layout.SIGNING_KEY_NAME}",
        "public_key_file": f"{layout.SIGNING_DIR}/{p}/{layout.SIGNING_PUB_NAME}",
        "published_keys_dir": layout.SIGNING_KEYS_PUBLIC_DIR,
    }
    doc["attestations"] = {
        "trusted_keys_dir": layout.QA_TRUSTED_KEYS_DIR,
        "attestations_dir": layout.QA_ATTESTATIONS_DIR,
    }
    doc["sessions"] = {
        "records_dir": layout.SESSIONS_DIR,
        "fingerprint_command": list(layout.FINGERPRINT_COMMAND),
    }
    doc["ports"] = {k: ports[k] for k in sorted(ports)}
    return doc


def _inference(facts: LabFacts) -> dict[str, Any]:
    mode = facts.inference_mode
    ports = facts.ports
    loopback = "127.0.0.1"
    if mode not in INFERENCE_MODES:
        raise ValueError(f"unknown inference mode {mode!r}")
    out: dict[str, Any] = {
        "mode": mode,
        "researcher_endpoint": f"{loopback}:{ports['gateway_adhoc']}",
    }
    if mode == "local":
        out["api_url"] = f"http://{loopback}:{ports['vllm']}"
    elif mode == "remote":
        # The inference forwarder on this node's loopback (§12.8).
        out["api_url"] = f"http://{loopback}:{ports['vllm']}"
        out["control_url"] = f"http://{loopback}:{ports['inference_control']}"
    else:
        if facts.lab_mode != "staging":
            raise ValueError("scripted inference only on lab_mode: staging")
        out["api_url"] = facts.scripted_api_url
    return out


def endpoints_toml(document: Mapping[str, Any]) -> str:
    """Serialize the endpoints document.

    Args:
        document: The output of :func:`endpoints`.

    Returns:
        TOML text.
    """
    return toml.dumps(document, ENDPOINTS_HEADER)
