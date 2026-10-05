"""Lab-wide data in group_vars/all/main.yml (BUILD_PROMPT §6, contracts.md)."""

from __future__ import annotations

import re

from ansible_testlib import ANSIBLE, GROUP_VARS, INFRA, load_yaml

DATA = load_yaml(GROUP_VARS)
MODE_RE = re.compile(r"^[0-7]{4}$")
ROOT_ONLY_SECRETS = {"pokus-gt", "docker", "incus", "incus-admin"}


def test_groups_and_accounts() -> None:
    groups = {g["name"] for g in DATA["pokus_groups"]}
    assert groups == {
        "pokus",
        "pokus-admin",
        "pokus-gt",
        "pokus-svc",
        "pokus-exec-clients",
        "pokus-gw-clients",
    }
    accounts = {a["name"]: set(a["groups"]) for a in DATA["service_accounts"]}
    assert set(accounts) == {
        f"pokus-{s}" for s in ("orch", "agent", "gw", "exec", "inject", "verify", "eval")
    }
    for name, member_of in accounts.items():
        assert "pokus-svc" in member_of, name
        assert member_of <= groups, name
    # Safety invariant 4: the agent gets no catalog, Docker, Incus or database access.
    assert accounts["pokus-agent"] == {"pokus-svc", "pokus-exec-clients", "pokus-gw-clients"}
    assert not accounts["pokus-agent"] & ROOT_ONLY_SECRETS
    assert {n for n, g in accounts.items() if "pokus-gw-clients" in g} == {"pokus-agent"}
    assert {n for n, g in accounts.items() if "pokus-exec-clients" in g} == {
        "pokus-agent",
        "pokus-orch",
    }


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
        "": ("root", "pokus", "2775"),
        "deploy": ("root", "root", "0755"),
        "catalog": ("root", "pokus-gt", "0750"),
        "models": ("pokus-orch", "pokus", "0755"),
        "datasets": ("root", "pokus", "2775"),
        "artifacts": ("pokus-orch", "pokus", "0750"),
        "reports": ("pokus-eval", "pokus", "2775"),
        "images": ("root", "pokus-admin", "0750"),
        "shared": ("root", "pokus", "2770"),
        "users": ("root", "root", "0755"),
        "services": ("root", "pokus-admin", "0750"),
        "state": ("root", "root", "0755"),
        "backups": ("root", "root", "0700"),
    }
    layout = {e["path"]: (e["owner"], e["group"], e["mode"]) for e in DATA["pokus_layout"]}
    assert layout == expected
    acls = {e["path"]: e.get("default_acl") for e in DATA["pokus_layout"] if e.get("default_acl")}
    assert acls == {"datasets": ["group:pokus:rwX"], "shared": ["group:pokus:rwX"]}
    assert DATA["pokus_layout_user_mode"] == "0700"
    for entry in DATA["pokus_layout"] + DATA["pokus_system_layout"]:
        assert MODE_RE.match(entry["mode"]), entry
        assert entry["notes"], entry
    system = {e["path"]: (e["owner"], e["group"], e["mode"]) for e in DATA["pokus_system_layout"]}
    assert system["/etc/pokus"] == ("root", "root", "0755")
    assert system["/etc/pokus/secrets"] == ("root", "pokus-svc", "0750")


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
    ports = list(DATA["pokus_ports"].values())
    assert len(ports) == len(set(ports))
    assert set(ports) == contract_ports()


def test_bridge_patterns_match_the_contract() -> None:
    text = (INFRA / "docs" / "contracts.md").read_text(encoding="utf-8")
    assert "infra treats bridges matching `r-*` and `pokus-build0` as Incus bridges" in text
    assert DATA["pokus_incus_bridge_patterns"] == ["r-*", "pokus-build0"]


def test_example_host_vars_show_the_lab_wide_defaults() -> None:
    # Where group_vars has a default, the commented example shows that same value.
    example = load_yaml(ANSIBLE / "inventory" / "host_vars" / "example-machine.yml")
    shared = sorted(set(example) & set(DATA))
    assert {"machine_name", "lab_mode", "lab_provider", "power_policy", "firewall_backend"} <= set(
        shared
    )
    assert {name: example[name] for name in shared} == {name: DATA[name] for name in shared}
