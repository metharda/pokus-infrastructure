"""Tests for the doctor role: the lab description /etc/pinka/lab.json it renders."""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Mapping
from typing import Any

import pytest
from pinka_lab.config import LabConfig

from ansible_testlib import GROUP_VARS, INFRA, ROLES, _templar, ansible_vars, evaluate, load_yaml

ROLE = ROLES / "doctor"
IMPORTED_ROLES = (
    "incus",
    "postgres",
    "backup",
    "tailscale",
    "firewall",
    "lab_session",
    "observability",
    "platform",
    "inference_control",
)
SOURCES = INFRA / "tools" / "pinka-lab" / "pinka_lab"
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
        "machine": "pinka-lab",
        "pinka_dir": str(INFRA),
        "pinka_provider": "baremetal",
        "pinka_features": {"gpu_inference": True, "vm_targets": False},
        "pinka_firewall_backend": "nftables",
        "pinka_bootstrap": {},
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
        evaluate("pinka_system_layout + pinka_layout", scope),
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
        "pinka-lab",
        "pinka-lab",
        "full",
    )
    assert config.runs_lab and config.serves_inference
    assert data["paths"]["preflight"] == "/usr/local/lib/pinka/preflight/pinka-preflight"
    assert data["paths"]["sessions"] == "/var/lib/pinka/sessions"
    assert data["paths"]["health_gate_status"] == "/run/pinka/health-gate.json"
    assert data["fingerprint"] == {
        "cache": "/var/lib/pinka/fingerprint/current.json",
        "versions_lock_sha256": "0" * 64,
        "host_vars_sha256": "1" * 64,
    }
    assert data["units"] == [
        "pinka-health-gate.service",
        "pinka-session-start.service",
        "pinka-core.service",
        "pinka-observability.service",
        "pinka-sleep-inhibit.service",
    ]
    assert data["gpu"]["device_args"] == ["--device", "nvidia.com/gpu=all"]
    assert re.fullmatch(r"[a-z0-9./-]+@sha256:[0-9a-f]{64}", data["gpu"]["image"])
    assert data["tailscale"] == {"enabled": True, "hostname": "pinka-lab", "tag": "tag:pinka-lab"}
    assert data["firewall"]["incus_bridge_patterns"][:2] == ["r-*", "pinka-build0"]
    assert data["opa"]["url"] == "http://127.0.0.1:8181"
    assert data["observability"]["loki"] == "http://127.0.0.1:18702"
    assert data["vllm"]["health_url"] == "http://127.0.0.1:18704/health"
    assert data["backup"]["helper"] == "/usr/local/libexec/pinka/backup"
    assert data["backup"]["configured"] is False
    assert data["images"]["files_helper"] == "/usr/local/libexec/pinka/image-files"
    assert data["images"]["export_root"] == "/srv/pinka/images"
    assert data["hardening"] == [] and data["sockets"] == []
    assert data["docs"] == {"dir": "/usr/local/share/pinka/docs", "port": 18706}


def test_every_key_the_cli_reads_is_rendered() -> None:
    data = render()
    missing = sorted(".".join(k) for k in keys_read_by_the_cli() if not lookup(data, k))
    assert missing == []


def test_containers_carry_their_pinned_digests() -> None:
    data = render()
    versions = ansible_vars(GROUP_VARS.parent / "versions.yml")["pinka_versions"]
    images = versions["containers"]["images"]
    assert data["containers"]["pinka-postgres"] == (
        f"{images['postgres']['image']}@{images['postgres']['digest']}"
    )
    assert data["containers"]["pinka-otel-journald"].endswith(images["otel_collector"]["digest"])
    assert "pinka-gpu-exporter" in data["containers"]
    assert all(re.search(r"@sha256:[0-9a-f]{64}$", ref) for ref in data["containers"].values())


@pytest.mark.parametrize(
    ("node_role", "gpu", "expected"),
    [
        ("lab", False, {"pinka-postgres", "pinka-opa", "pinka-loki"}),
        ("inference", True, {"pinka-gpu-exporter", "pinka-otel-gateway", "pinka-node-exporter"}),
    ],
)
def test_node_roles(node_role: str, gpu: bool, expected: set[str]) -> None:
    data = render(node_role=node_role, pinka_features={"gpu_inference": gpu, "vm_targets": True})
    assert expected <= set(data["containers"])
    if node_role == "inference":
        assert "pinka-postgres" not in data["containers"]
        # Only the inference stack; telemetry goes to the paired lab node (§19.10).
        stores = {"pinka-prometheus", "pinka-loki", "pinka-tempo", "pinka-grafana"}
        assert not stores & set(data["containers"])
        assert data["observability"] == {}
        assert "pinka-core.service" not in data["units"]
        assert data["incus"] == {}
        assert data["images"]["blueprints"] == []
        assert data["postgres"]["roles"] == []
        assert data["listeners"] == {
            "processes": data["listeners"]["processes"],
            "addresses": [],  # no VM listener without Incus
            "tailnet_ports": [18704, 18710],
        }
        assert [h["unit"] for h in data["hardening"]] == ["pinka-inference-control.service"]
    else:
        assert "pinka-gpu-exporter" not in data["containers"]
        assert data["listeners"]["tailnet_ports"] == []
        assert data["observability"]["loki"] == "http://127.0.0.1:18702"
        assert data["hardening"] == []  # the platform is not enabled


def test_split_inference_pairing() -> None:
    unpaired = render(node_role="inference")
    assert unpaired["inference"] == {
        "dir": "/etc/pinka/inference",
        "control_port": 18710,
        "peer": "",
        "renew_days": 30,
    }
    assert "pinka-inference-control.service" not in unpaired["units"]
    paired = render(node_role="inference", inference_lab_node="pinka-lab")
    assert paired["inference"]["peer"] == "pinka-lab"
    assert paired["units"][-1] == "pinka-inference-control.service"
    lab = render(node_role="lab", inference_remote_node="pinka-gpu")
    assert lab["inference"]["peer"] == "pinka-gpu"
    assert lab["listeners"]["tailnet_ports"] == [4317]
    assert render()["inference"]["peer"] == ""


def test_platform_checks_wait_for_the_switch() -> None:
    # The platform's units, sockets and hardening are checked once the switch
    # with the hooks starts them (plan step 3d.4).
    rendered = {"accounts": [{"name": "demo-ctl"}, {"name": "demo-agt"}], "service_units": []}
    data = render(active_platform=rendered)
    assert data["hardening"] == []
    assert data["sockets"] == []
    assert data["accounts"]["platform_accounts"] == ["demo-ctl", "demo-agt"]
    assert data["paths"]["active_platform"] == "/etc/pinka/platform/active.json"


def test_the_database_check_follows_the_platform() -> None:
    db = {
        "database": "demo",
        "reader": "demo_reader",
        "grafana": "demo_grafana",
        "migrator": "demo_migrator",
        "logins": [{"role": "demo_ctl"}],
    }
    data = render(active_platform={"database": db})
    assert data["postgres"]["database"] == "demo"
    assert data["postgres"]["restricted_roles"] == ["demo_reader", "demo_grafana"]
    assert data["postgres"]["roles"] == [
        "demo_ctl",
        "demo_migrator",
        "demo_reader",
        "demo_grafana",
        "pinka_monitor",
    ]
    standalone = render()
    assert standalone["postgres"]["roles"] == ["pinka_monitor"]
    assert standalone["postgres"]["restricted_roles"] == []


def test_incus_and_images() -> None:
    data = render(pinka_features={"gpu_inference": False, "vm_targets": True})
    incus = data["incus"]
    assert incus["pool"] == "pinka-targets"
    assert incus["backend"] == "btrfs"
    assert incus["projects"] == ["bench", "build"]
    assert {"name": "target-vm", "project": "bench"} in incus["profiles"]
    assert incus["cert_dir"] == "/etc/pinka/incus"
    assert [c["short"] for c in incus["certificates"]] == ["metrics"]
    assert [c["name"] for c in incus["certificates"]] == ["pinka-metrics"]
    certificate = {
        "account": "demo-ctl",
        "short": "demo-ctl",
        "name": "demo-ctl",
        "projects": ["bench"],
    }
    with_platform = render(
        pinka_features={"gpu_inference": False, "vm_targets": True},
        active_platform={"incus_certificates": [certificate]},
    )
    assert [c["name"] for c in with_platform["incus"]["certificates"]] == [
        "demo-ctl",
        "pinka-metrics",
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
    assert layout["/srv/pinka"]["mode"] == "2775"
    assert layout["/srv/pinka/shared"]["default_acl"] == ["group:pinka:rwX"]
    assert (layout["/etc/pinka/secrets"]["group"], layout["/etc/pinka/secrets"]["mode"]) == (
        "root",
        "0711",
    )
    assert all(path.startswith("/") and not path.endswith("/") for path in layout)


def test_wsl_and_runtime_gpu() -> None:
    data = render(
        pinka_provider="wsl2",
        expected={"memory_gib": 22, "cpu_threads": 20},
        doctor_daemon_json={"content": base64.b64encode(b"{}").decode()},
    )
    assert data["wsl"] == {"memory_gib": 22, "cpus": 20, "kvm_expected": False}
    assert data["gpu"]["nvidia_smi"] == "/usr/lib/wsl/lib/nvidia-smi"
    assert data["gpu"]["device_args"] == ["--runtime", "nvidia", "--gpus", "all"]
    assert "pinka-sleep-inhibit.service" not in data["units"]


def test_module_lists() -> None:
    scope = variables()
    copy = task("List the modules to install")
    results = [
        {
            "item": {"src": "tools/pinka-lab/pinka_lab", "dest": "pinka-lab/pinka_lab"},
            "files": [
                {"path": f"{INFRA}/tools/pinka-lab/pinka_lab/cli.py"},
                {"path": f"{INFRA}/tools/pinka-lab/pinka_lab/sub/x.py"},
            ],
        }
    ]
    for item in results:
        local = dict(scope, item=item)
        for fact in ("doctor_modules", "doctor_module_sources"):
            scope[fact] = _templar(local).template(copy["ansible.builtin.set_fact"][fact])
    assert scope["doctor_modules"] == ["pinka-lab/pinka_lab/cli.py", "pinka-lab/pinka_lab/sub/x.py"]
    assert scope["doctor_module_sources"]["pinka-lab/pinka_lab/sub/x.py"].endswith(
        "pinka_lab/sub/x.py"
    )
    stale = task("Remove modules that are no longer shipped")["loop"]
    installed = {
        "results": [
            {
                "files": [
                    {"path": "/usr/local/lib/pinka/pinka-lab/pinka_lab/cli.py"},
                    {"path": "/usr/local/lib/pinka/pinka-lab/pinka_lab/old.py"},
                ]
            }
        ]
    }
    removed = _templar(dict(scope, doctor_installed=installed)).template(stale)
    assert removed == ["/usr/local/lib/pinka/pinka-lab/pinka_lab/old.py"]


def test_firewall_check_knows_the_zones_of_the_firewall_role() -> None:
    from pinka_lab import checks  # noqa: PLC0415 - next to the role data it is compared with

    tasks = load_yaml(ROLES / "firewall" / "tasks" / "firewalld.yml")
    created = next(t for t in tasks if t["name"] == "Create the pinka zones")["loop"]
    assert tuple(created) == checks.FIREWALLD_ZONES
