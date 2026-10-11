"""Lab-wide data in group_vars/all/main.yml (BUILD_PROMPT §6, contracts.md)."""

from __future__ import annotations

import re
from typing import Any

from ansible_testlib import ANSIBLE, GROUP_VARS, INFRA, ansible_vars, evaluate, load_yaml

DATA = load_yaml(GROUP_VARS)
MODE_RE = re.compile(r"^[0-7]{4}$")
ROOT_ONLY_SECRETS = {"pinka-restricted", "docker", "incus", "incus-admin"}


def test_groups_and_accounts() -> None:
    # BUILD_PROMPT §6.1: infra's groups; the platform's accounts and groups are
    # rendered from its manifest (platform role), never listed here.
    groups = {g["name"]: g["system"] for g in DATA["pinka_groups"]}
    assert groups == {
        "pinka": False,
        "pinka-admin": False,
        "pinka-restricted": True,
        "pinka-datasets": True,
        "pinka-query": True,
    }
    assert "service_accounts" not in DATA
    assert DATA["inference_control_account"] == "pinka-inference"


def test_researchers_are_placeholders() -> None:
    # Privacy invariant: only placeholder names and key files that are never committed.
    for researcher in DATA["researchers"]:
        assert re.fullmatch(r"researcher\d+", researcher["name"])
        assert researcher["keys_file"] == f"users/{researcher['name']}.pub"
        assert not (INFRA / researcher["keys_file"]).exists()
    assert [r["name"] for r in DATA["researchers"] if r["admin"]] == ["researcher1"]
    assert DATA["break_glass_docker"] == []
    assert DATA["researchers_removed"] == []


def test_layout_matches_the_spec() -> None:
    # BUILD_PROMPT §6.2; state/<service>/ and users/<name>/ are created per service
    # and per researcher.
    expected = {
        "": ("root", "pinka", "2775"),
        "platforms": ("root", "root", "0755"),
        "restricted": ("root", "pinka-restricted", "0750"),
        "models": ("root", "pinka", "0755"),
        "models/adapters": ("root", "pinka", "0755"),
        "training": ("root", "root", "0755"),
        "training/datasets": ("root", "pinka-datasets", "0750"),
        "training/jobs": ("root", "root", "0755"),
        "datasets": ("root", "pinka", "2775"),
        "images": ("root", "pinka-admin", "0750"),
        "shared": ("root", "pinka", "2770"),
        "users": ("root", "root", "0755"),
        "services": ("root", "pinka-admin", "0750"),
        "state": ("root", "root", "0755"),
        "backups": ("root", "root", "0700"),
    }
    layout = {e["path"]: (e["owner"], e["group"], e["mode"]) for e in DATA["pinka_layout"]}
    assert layout == expected
    acls = {e["path"]: e.get("default_acl") for e in DATA["pinka_layout"] if e.get("default_acl")}
    assert acls == {"datasets": ["group:pinka:rwX"], "shared": ["group:pinka:rwX"]}
    assert DATA["pinka_layout_user_mode"] == "0700"
    for entry in DATA["pinka_layout"] + DATA["pinka_system_layout"]:
        assert MODE_RE.match(entry["mode"]), entry
        assert entry["notes"], entry
    system = {e["path"]: (e["owner"], e["group"], e["mode"]) for e in DATA["pinka_system_layout"]}
    assert system["/etc/pinka"] == ("root", "root", "0755")
    assert system["/etc/pinka/secrets"] == ("root", "root", "0711")
    # Deployment (BUILD_PROMPT §6.2, §7.14): root-only mirrors, staging and records.
    assert system["/etc/pinka/platform"] == ("root", "root", "0755")
    for root_only in ("/var/lib/pinka/git", "/var/lib/pinka/staging", "/var/lib/pinka/platforms"):
        assert system[root_only] == ("root", "root", "0700"), root_only
    assert system["/var/cache/pinka/uv"] == ("pinka-build", "pinka-build", "0700")


def test_infra_accounts() -> None:
    # BUILD_PROMPT §6.1: pinka-build runs platform builds; pinka-inference the
    # inference control endpoint (§19.10). Neither belongs to a group.
    assert DATA["infra_accounts"] == [
        {"name": "pinka-build", "groups": [], "description": "unprivileged platform builds"},
        {
            "name": "pinka-inference",
            "groups": [],
            "description": "the inference control endpoint of an inference node",
        },
        # §7.9: the query proxy's container runs as this account's UID.
        {
            "name": "pinka-query-proxy",
            "groups": [],
            "description": "the telemetry query proxy's container user (§7.9)",
        },
    ]


def test_platform_uid_range_matches_the_deployment_tool() -> None:
    from pinka_platform import layout  # noqa: PLC0415 - the tool's own constants

    assert DATA["pinka_platform_uids"] == {
        "min": layout.PLATFORM_UID_MIN,
        "max": layout.PLATFORM_UID_MAX,
    }
    # systemd's allocation table (UIDS-GIDS.md, v255): 60578..61183 is unused,
    # between the container host users and the dynamic service users.
    assert 60577 < DATA["pinka_platform_uids"]["min"] <= DATA["pinka_platform_uids"]["max"] < 61184


def contract_ports() -> set[int]:
    text = (INFRA / "docs" / "contracts.md").read_text(encoding="utf-8")
    section = text.split("\n## Ports\n", 1)[1].split("\n## ", 1)[0]
    ports: set[int] = set()
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip("|").split("|")]
        if line.startswith("|") and re.fullmatch(r"[\d, ]+", cells[0]):
            ports.update(int(p) for p in cells[0].split(","))
    return ports


def test_ports_match_the_contract() -> None:
    ports = list(DATA["pinka_default_ports"].values())
    assert len(ports) == len(set(ports))
    assert set(ports) == contract_ports()


def test_bridge_patterns_match_the_contract() -> None:
    text = (INFRA / "docs" / "contracts.md").read_text(encoding="utf-8")
    assert "infra treats bridges matching `r-*` and `pinka-build0` as Incus bridges" in text
    assert DATA["pinka_incus_bridge_patterns"] == ["r-*", "pinka-build0"]


def test_example_host_vars_show_the_lab_wide_defaults() -> None:
    # Where group_vars has a default, the commented example shows that same value.
    example = load_yaml(ANSIBLE / "inventory" / "host_vars" / "example-machine.yml")
    shared = sorted(set(example) & set(DATA))
    assert {"machine_name", "lab_mode", "lab_provider", "power_policy", "firewall_backend"} <= set(
        shared
    )
    assert {name: example[name] for name in shared} == {name: DATA[name] for name in shared}


def _platform_facts_check(name: str) -> list[str]:
    tasks = load_yaml(ANSIBLE / "roles" / "platform_facts" / "tasks" / "main.yml")
    task = next(t for t in tasks if t["name"] == name)
    that: list[str] = task["ansible.builtin.assert"]["that"]
    return that


def _holds(that: list[str], **changes: Any) -> bool:
    variables = ansible_vars(
        GROUP_VARS, ANSIBLE / "roles" / "platform_facts" / "vars" / "main.yml", **changes
    )
    return all(evaluate(condition, variables) for condition in that)


def test_port_overrides_make_the_effective_ports() -> None:
    # BUILD_PROMPT §12.11: overrides by name; endpoints.toml gets pinka_ports.
    variables = ansible_vars(GROUP_VARS, ports={"grafana": 18750})
    effective = evaluate("pinka_ports", variables)
    assert effective["grafana"] == 18750
    assert {k: v for k, v in effective.items() if k != "grafana"} == {
        k: v for k, v in DATA["pinka_default_ports"].items() if k != "grafana"
    }
    that = _platform_facts_check("Check the port overrides")
    assert _holds(that)
    assert _holds(that, ports={"grafana": 18750, "otlp_grpc": 14317})
    assert not _holds(that, ports={"graphana": 18750})  # unknown name
    assert not _holds(that, ports={"grafana": "18750"})  # not a number
    assert not _holds(that, ports={"grafana": 70000})
    assert not _holds(that, ports={"grafana": 18701})  # Prometheus's port
    assert not _holds(that, ports={"grafana": 18720})  # an internal port


def test_vm_collector_address_is_checked() -> None:
    that = _platform_facts_check("Check the address of the collector listener for VM targets")
    assert evaluate("pinka_vm_otlp.address", ansible_vars(GROUP_VARS)) == "198.18.0.1"
    assert _holds(that)
    assert _holds(that, vm_collector_address="198.19.255.1")
    for bad in ("127.0.0.2", "0.1.2.3", "198.18.0", "300.1.1.1", "2001:db8::1"):
        assert not _holds(that, vm_collector_address=bad), bad
