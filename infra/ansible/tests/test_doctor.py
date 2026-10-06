"""Tests for the doctor role: the lab description /etc/pokus/lab.json it renders."""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Mapping
from typing import Any

import pytest
from pokus_lab.config import LabConfig

from ansible_testlib import GROUP_VARS, INFRA, ROLES, _templar, ansible_vars, evaluate

ROLE = ROLES / "doctor"
IMPORTED_ROLES = (
    "incus",
    "postgres",
    "backup",
    "tailscale",
    "firewall",
    "lab_session",
    "observability",
)
SOURCES = INFRA / "tools" / "pokus-lab" / "pokus_lab"
CONFIG_READ = re.compile(r"config\.get\(((?:\s*\"[a-z_]+\",?)+)")


def tasks() -> list[dict[str, Any]]:
    from ansible.parsing.dataloader import DataLoader  # noqa: PLC0415 - heavy import

    loaded: list[dict[str, Any]] = DataLoader().load_from_file(
        str(ROLE / "tasks" / "main.yml"), trusted_as_template=True
    )
    return loaded


def task(name: str) -> dict[str, Any]:
    return next(t for t in tasks() if t["name"] == name)


def variables(**facts: Any) -> dict[str, Any]:
    """What the role sees in site.yml: group vars, the imported roles' defaults and vars."""
    paths = [GROUP_VARS, GROUP_VARS.parent / "versions.yml"]
    for role in (*IMPORTED_ROLES, "doctor"):
        for kind in ("defaults", "vars"):
            if (ROLES / role / kind / "main.yml").is_file():
                paths.append(ROLES / role / kind / "main.yml")
    base = {
        "machine": "pokus-lab",
        "pokus_infra_dir": str(INFRA),
        "pokus_provider": "baremetal",
        "pokus_features": {"gpu_inference": True, "vm_targets": False},
        "pokus_firewall_backend": "nftables",
        "pokus_bootstrap": {},
        "host_profile": {"schema_version": 1, "storage_backend": "btrfs"},
        "node_role": "full",
        "doctor_images_defaults": ansible_vars(ROLES / "images" / "defaults" / "main.yml"),
        "doctor_images_vars": ansible_vars(ROLES / "images" / "vars" / "main.yml"),
        "doctor_config_files": {
            "results": [{"stat": {"checksum": "0" * 64}}, {"stat": {"checksum": "1" * 64}}]
        },
        "doctor_daemon_json": {
            "content": base64.b64encode(json.dumps({"features": {"cdi": True}}).encode()).decode()
        },
    }
    base.update(facts)
    return ansible_vars(*paths, **base)


def set_fact_loop(name: str, fact: str, items: list[Any], scope: dict[str, Any]) -> Any:
    """Run a looped set_fact task the way Ansible does."""
    spec = task(name)
    for item in items:
        local = dict(scope, item=item, **(spec.get("vars") or {}))
        scope[fact] = _templar(local).template(spec["ansible.builtin.set_fact"][fact])
    return scope.get(fact)


def render(**facts: Any) -> dict[str, Any]:
    scope = variables(**facts)
    blueprints = evaluate("doctor_images_blueprints if doctor_runs_lab else []", scope)
    set_fact_loop("Describe the blueprints", "doctor_blueprints", list(blueprints), scope)
    set_fact_loop(
        "Describe the layout (absolute paths)",
        "doctor_layout",
        evaluate("pokus_system_layout + pokus_layout", scope),
        scope,
    )
    write = task("Write the lab description")
    local = dict(scope, **write["vars"])
    text = _templar(local).template(write["ansible.builtin.copy"]["content"])
    result: dict[str, Any] = json.loads(text)
    return result


def keys_read_by_the_cli() -> set[tuple[str, ...]]:
    found = set()
    for path in SOURCES.glob("*.py"):
        for match in CONFIG_READ.finditer(path.read_text(encoding="utf-8")):
            found.add(tuple(re.findall(r'"([a-z_]+)"', match.group(1))))
    return found


def lookup(data: Mapping[str, Any], keys: tuple[str, ...]) -> bool:
    value: Any = data
    for key in keys:
        if not isinstance(value, Mapping) or key not in value:
            return False
        value = value[key]
    return True


def test_full_node_description() -> None:
    data = render()
    config = LabConfig(data)
    assert data["schema_version"] == 1
    assert (config.machine, config.host_vars, config.node_role) == (
        "pokus-lab",
        "pokus-lab",
        "full",
    )
    assert config.runs_lab and config.serves_inference
    assert data["paths"]["preflight"] == "/usr/local/lib/pokus/preflight/pokus-preflight"
    assert data["paths"]["sessions"] == "/var/lib/pokus/sessions"
    assert data["paths"]["health_gate_status"] == "/run/pokus/health-gate.json"
    assert data["fingerprint"] == {
        "cache": "/var/lib/pokus/fingerprint/current.json",
        "versions_lock_sha256": "0" * 64,
        "host_vars_sha256": "1" * 64,
    }
    assert data["units"] == [
        "pokus-health-gate.service",
        "pokus-session-start.service",
        "pokus-core.service",
        "pokus-observability.service",
        "pokus-sleep-inhibit.service",
    ]
    assert data["gpu"]["device_args"] == ["--device", "nvidia.com/gpu=all"]
    assert re.fullmatch(r"[a-z0-9./-]+@sha256:[0-9a-f]{64}", data["gpu"]["image"])
    assert data["tailscale"] == {"enabled": True, "hostname": "pokus-lab", "tag": "tag:pokus-lab"}
    assert data["firewall"]["incus_bridge_patterns"][:2] == ["r-*", "pokus-build0"]
    assert data["opa"]["url"] == "http://127.0.0.1:8181"
    assert data["observability"]["loki"] == "http://127.0.0.1:18702"
    assert data["vllm"]["health_url"] == "http://127.0.0.1:18704/health"
    assert data["backup"]["helper"] == "/usr/local/libexec/pokus/backup"
    assert data["backup"]["configured"] is False
    assert data["images"]["files_helper"] == "/usr/local/libexec/pokus/image-files"
    assert data["images"]["export_root"] == "/srv/pokus/images"
    assert data["hardening"] == [] and data["sockets"] == []


def test_every_key_the_cli_reads_is_rendered() -> None:
    data = render()
    missing = sorted(".".join(k) for k in keys_read_by_the_cli() if not lookup(data, k))
    assert missing == []


def test_containers_carry_their_pinned_digests() -> None:
    data = render()
    versions = ansible_vars(GROUP_VARS.parent / "versions.yml")["pokus_versions"]
    images = versions["containers"]["images"]
    assert data["containers"]["pokus-postgres"] == (
        f"{images['postgres']['image']}@{images['postgres']['digest']}"
    )
    assert data["containers"]["pokus-otel-journald"].endswith(images["otel_collector"]["digest"])
    assert "pokus-gpu-exporter" in data["containers"]
    assert all(re.search(r"@sha256:[0-9a-f]{64}$", ref) for ref in data["containers"].values())


@pytest.mark.parametrize(
    ("node_role", "gpu", "expected"),
    [
        ("lab", False, {"pokus-postgres", "pokus-opa", "pokus-loki"}),
        ("inference", True, {"pokus-gpu-exporter", "pokus-loki"}),
    ],
)
def test_node_roles(node_role: str, gpu: bool, expected: set[str]) -> None:
    data = render(node_role=node_role, pokus_features={"gpu_inference": gpu, "vm_targets": False})
    assert expected <= set(data["containers"])
    if node_role == "inference":
        assert "pokus-postgres" not in data["containers"]
        assert "pokus-core.service" not in data["units"]
        assert data["incus"] == {}
        assert data["images"]["blueprints"] == []
        assert data["postgres"]["roles"] == []
    else:
        assert "pokus-gpu-exporter" not in data["containers"]


def test_incus_and_images() -> None:
    data = render(pokus_features={"gpu_inference": False, "vm_targets": True})
    incus = data["incus"]
    assert incus["pool"] == "pokus-targets"
    assert incus["backend"] == "btrfs"
    assert incus["projects"] == ["bench", "build"]
    assert {"name": "target-vm", "project": "bench"} in incus["profiles"]
    assert incus["cert_dir"] == "/etc/pokus/incus"
    assert [c["short"] for c in incus["certificates"]] == [
        "orch",
        "exec",
        "inject",
        "verify",
        "metrics",
    ]
    assert data["listeners"]["addresses"] == ["198.18.0.1"]
    blueprints = {b["name"]: b for b in data["images"]["blueprints"]}
    assert next(iter(blueprints)) == "_base"
    assert blueprints["web-nginx-flask"] == {
        "name": "web-nginx-flask",
        "type": "container",
        "nesting": False,
        "services": ["nginx", "webapp"],
        "base_blueprint": "_base",
    }
    assert blueprints["_base"]["base_blueprint"] is None
    assert blueprints["docker-host"]["nesting"] is True


def test_layout_is_absolute() -> None:
    layout = {e["path"]: e for e in render()["layout"]}
    assert layout["/srv/pokus"]["mode"] == "2775"
    assert layout["/srv/pokus/shared"]["default_acl"] == ["group:pokus:rwX"]
    assert layout["/etc/pokus/secrets"]["group"] == "pokus-svc"
    assert all(path.startswith("/") and not path.endswith("/") for path in layout)


def test_wsl_and_runtime_gpu() -> None:
    data = render(
        pokus_provider="wsl2",
        expected={"memory_gib": 22, "cpu_threads": 20},
        doctor_daemon_json={"content": base64.b64encode(b"{}").decode()},
    )
    assert data["wsl"] == {"memory_gib": 22, "cpus": 20, "kvm_expected": False}
    assert data["gpu"]["nvidia_smi"] == "/usr/lib/wsl/lib/nvidia-smi"
    assert data["gpu"]["device_args"] == ["--runtime", "nvidia", "--gpus", "all"]
    assert "pokus-sleep-inhibit.service" not in data["units"]


def test_module_lists() -> None:
    scope = variables()
    copy = task("List the modules to install")
    results = [
        {
            "item": {"src": "tools/pokus-lab/pokus_lab", "dest": "pokus-lab/pokus_lab"},
            "files": [
                {"path": f"{INFRA}/tools/pokus-lab/pokus_lab/cli.py"},
                {"path": f"{INFRA}/tools/pokus-lab/pokus_lab/sub/x.py"},
            ],
        }
    ]
    for item in results:
        local = dict(scope, item=item)
        for fact in ("doctor_modules", "doctor_module_sources"):
            scope[fact] = _templar(local).template(copy["ansible.builtin.set_fact"][fact])
    assert scope["doctor_modules"] == ["pokus-lab/pokus_lab/cli.py", "pokus-lab/pokus_lab/sub/x.py"]
    assert scope["doctor_module_sources"]["pokus-lab/pokus_lab/sub/x.py"].endswith(
        "pokus_lab/sub/x.py"
    )
    stale = task("Remove modules that are no longer shipped")["loop"]
    installed = {
        "results": [
            {
                "files": [
                    {"path": "/usr/local/lib/pokus/pokus-lab/pokus_lab/cli.py"},
                    {"path": "/usr/local/lib/pokus/pokus-lab/pokus_lab/old.py"},
                ]
            }
        ]
    }
    removed = _templar(dict(scope, doctor_installed=installed)).template(stale)
    assert removed == ["/usr/local/lib/pokus/pokus-lab/pokus_lab/old.py"]
