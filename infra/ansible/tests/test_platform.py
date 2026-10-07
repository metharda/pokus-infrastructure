"""Tests for the catalog and platform roles: units, sockets, configuration, helpers."""

from __future__ import annotations

import json
import os
import shutil
import socket
import stat
import subprocess
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pokus_platform_config_check as config_check
import pokus_socket_perms as socket_perms
import pytest

import pokus
from ansible_testlib import (
    GROUP_VARS,
    ROLES,
    ansible_vars,
    evaluate,
    load_yaml,
    render_template,
)

ROLE = ROLES / "platform"
CONTRACT_SCRIPTS = {
    "pokus-orchestrator",
    "pokus-reconcile",
    "pokus-agent-runner",
    "pokus-llm-gateway",
    "pokus-executor",
    "pokus-injector",
    "pokus-verifier",
    "pokus-evaluator",
}
AGENT_DENIED = {
    "/srv/pokus/catalog",
    "/srv/pokus/artifacts",
    "/srv/pokus/services",
    "/etc/pokus/secrets",
    "/etc/pokus/incus",
    "/var/lib/incus",
    "/run/docker.sock",
    "/var/lib/pokus/git",
    "/run/pokus/postgres",
    "/run/pokus/postgres-reader",
    "/run/pokus/inject",
    "/run/pokus/verify",
    "/run/pokus/vllm-deploy.sock",
    "/run/pokus/orch/api.sock",
    "/run/pokus/gw/svc.sock",
}


def variables(**extra: Any) -> dict[str, Any]:
    base = {
        "machine_name": "pokus-lab",
        "node_role": "full",
        "pokus_features": {"gpu_inference": True, "vm_targets": False},
        "platform_socket_acls": True,
        "incus_https_address": "127.0.0.1:8443",
        "ansible_facts": {"architecture": "x86_64"},
    }
    base.update(extra)
    return ansible_vars(
        GROUP_VARS,
        GROUP_VARS.parent / "versions.yml",
        ROLE / "defaults" / "main.yml",
        ROLE / "vars" / "main.yml",
        **base,
    )


def units() -> dict[str, dict[str, Any]]:
    return {u["unit"]: u for u in evaluate("platform_units", variables())}


def render_unit(name: str, **extra: Any) -> dict[str, list[str]]:
    scope = variables(**extra)
    scope["platform_unit"] = units()[name]
    text = render_template("platform", "pokus-platform.service.j2", scope)
    assert text.startswith("# Managed by pokus-infra (ansible/roles/platform/templates/")
    parsed: dict[str, list[str]] = {}
    for line in text.splitlines():
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            parsed.setdefault(key, []).append(value)
    return parsed


# --- units ------------------------------------------------------------------------


def test_units_match_the_contract() -> None:
    table = units()
    assert {u["script"] for u in table.values()} == CONTRACT_SCRIPTS
    accounts = {u["script"]: u["account"] for u in table.values()}
    assert accounts["pokus-orchestrator"] == accounts["pokus-reconcile"] == "pokus-orch"
    assert accounts["pokus-llm-gateway"] == "pokus-gw"
    assert accounts["pokus-agent-runner"] == "pokus-agent"
    service_accounts = {a["name"]: a for a in load_yaml(GROUP_VARS)["service_accounts"]}
    for unit in table.values():
        assert unit["groups"] == service_accounts[unit["account"]]["groups"], unit["unit"]


def test_hardening_baseline_of_every_unit() -> None:
    for name in units():
        unit = render_unit(name)
        for key, value in {
            "NoNewPrivileges": "yes",
            "ProtectSystem": "strict",
            "ProtectHome": "yes",
            "PrivateTmp": "yes",
            "PrivateDevices": "yes",
            "ProtectKernelTunables": "yes",
            "ProtectKernelModules": "yes",
            "ProtectControlGroups": "yes",
            "RestrictSUIDSGID": "yes",
            "LockPersonality": "yes",
            "RestrictRealtime": "yes",
            "CapabilityBoundingSet": "",
            "AmbientCapabilities": "",
            "RestrictAddressFamilies": "AF_UNIX AF_INET AF_INET6",
            "UMask": "0077",
            "WantedBy": "pokus-lab.target",
        }.items():
            assert unit[key] == [value], (name, key)
        assert unit["Requires"][0].startswith("pokus-health-gate.service"), name
        assert "pokus-health-gate.service" in unit["After"][0], name
        assert unit["PartOf"][0].startswith("pokus-lab.target"), name
        assert unit["ExecStart"][0].startswith("/srv/pokus/deploy/platform/current/.venv/bin/")
        denied = {p.lstrip("-") for p in unit["InaccessiblePaths"][0].split()}
        assert {"/var/lib/pokus/git", "/run/docker.sock", "/var/lib/incus"} <= denied


def test_agent_runner_isolation() -> None:
    unit = render_unit("pokus-agent-runner.service")
    denied = {p.lstrip("-") for p in unit["InaccessiblePaths"][0].split()}
    assert denied >= AGENT_DENIED
    assert unit["User"] == ["pokus-agent"]
    assert unit["SupplementaryGroups"] == ["pokus-svc pokus-exec-clients pokus-gw-clients"]
    assert "ReadWritePaths" not in unit
    assert "pokus-orchestrator.service" in unit["PartOf"][0]
    assert "pokus-llm-gateway.service" in unit["PartOf"][0]
    for service in ("pokus-injector.service", "pokus-verifier.service", "pokus-executor.service"):
        assert service in unit["After"][0]


def test_reconcile_runs_before_the_orchestrator() -> None:
    reconcile = render_unit("pokus-reconcile.service")
    assert reconcile["Type"] == ["oneshot"]
    assert reconcile["Before"] == ["pokus-orchestrator.service"]
    assert "RuntimeDirectory" not in reconcile
    orchestrator = render_unit("pokus-orchestrator.service")
    assert "pokus-reconcile.service" in orchestrator["Requires"][0]
    assert orchestrator["Restart"] == ["on-failure"]
    assert orchestrator["RuntimeDirectory"] == ["pokus/orch"]
    assert orchestrator["ReadWritePaths"] == ["/srv/pokus/artifacts"]


def test_evaluator_may_generate_code() -> None:
    assert render_unit("pokus-evaluator.service")["MemoryDenyWriteExecute"] == ["no"]
    assert render_unit("pokus-executor.service")["MemoryDenyWriteExecute"] == ["yes"]
    assert render_unit("pokus-evaluator.service")["ReadWritePaths"] == ["/srv/pokus/reports"]


@pytest.mark.parametrize(
    ("acls", "expected"),
    [
        (
            True,
            "--path /run/pokus/gw/svc.sock --owner pokus-gw --group pokus-gw --mode 0600 "
            "--acl user:pokus-orch:rw-",
        ),
        (False, "--path /run/pokus/gw/svc.sock --owner pokus-gw --group pokus-svc --mode 0660"),
    ],
)
def test_socket_permissions_in_the_unit(acls: bool, expected: str) -> None:
    unit = render_unit("pokus-llm-gateway.service", platform_socket_acls=acls)
    posts = unit["ExecStartPost"]
    assert len(posts) == 2
    assert posts[1] == f"+/usr/local/libexec/pokus/socket-perms {expected}"


def test_sockets_cover_the_contract() -> None:
    specs = evaluate("platform_socket_expectations", variables())
    by_path = {s["path"]: s for s in specs}
    assert set(by_path) == {
        "/run/pokus/orch/api.sock",
        "/run/pokus/orch/svc.sock",
        "/run/pokus/exec/api.sock",
        "/run/pokus/gw/agent.sock",
        "/run/pokus/gw/svc.sock",
        "/run/pokus/inject/api.sock",
        "/run/pokus/verify/api.sock",
    }
    assert by_path["/run/pokus/inject/api.sock"]["expect_mode"] == "0660"
    assert by_path["/run/pokus/inject/api.sock"]["expect_acl"] == [
        "user:pokus-orch:rw-",
        "group::---",
        "mask::rw-",
    ]
    fallback = {
        s["path"]: s
        for s in evaluate("platform_socket_expectations", variables(platform_socket_acls=False))
    }
    assert fallback["/run/pokus/inject/api.sock"]["group"] == "pokus-gt"
    assert fallback["/run/pokus/inject/api.sock"]["expect_acl"] == []


def test_socket_specs_filter() -> None:
    sockets: list[dict[str, Any]] = [
        {
            "path": "/run/pokus/a/x.sock",
            "owner": "a",
            "group": "a",
            "mode": "0600",
            "acl": ["user:b:rw-"],
            "fallback": {"group": "c", "mode": "0660"},
        },
        {
            "path": "/run/pokus/a/y.sock",
            "owner": "a",
            "group": "g",
            "mode": "0640",
            "acl": ["group:h:r--"],
        },
    ]
    with_acl = pokus.socket_specs(sockets, True)
    assert with_acl[0]["expect_mode"] == "0660"
    assert with_acl[1]["expect_mode"] == "0640"
    assert with_acl[1]["expect_acl"] == ["group:h:r--", "group::r--", "mask::r--"]
    assert "fallback" not in with_acl[0]
    without = pokus.socket_specs(sockets, False)
    assert (without[0]["group"], without[0]["mode"], without[0]["acl"]) == ("c", "0660", [])
    assert without[1]["acl"] == []


# --- configuration --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("extra", "mode", "table"),
    [
        (
            {},
            "local",
            {
                "api_url": "http://127.0.0.1:18704",
                "deploy_helper_socket": "/run/pokus/vllm-deploy.sock",
            },
        ),
        (
            {"node_role": "lab", "inference_remote_node": "pokus-gpu"},
            "remote",
            {
                "api_url": "https://pokus-gpu:18704",
                "control_url": "https://pokus-gpu:18710",
                "tls_dir": "/etc/pokus/inference",
            },
        ),
        ({"pokus_features": {"gpu_inference": False}}, "remote", None),
        (
            {
                "platform_inference_mode": "scripted",
                "platform_scripted_api_url": "http://127.0.0.1:18799",
            },
            "scripted",
            {"api_url": "http://127.0.0.1:18799"},
        ),
    ],
)
def test_endpoints_toml(extra: dict[str, Any], mode: str, table: dict[str, str] | None) -> None:
    scope = variables(**extra)
    text = evaluate(
        "platform_endpoints | pokus_to_toml(platform_toml_header ~ 'endpoints.toml')", scope
    )
    assert text.startswith("# Managed by pokus-infra (ansible/roles/platform).")
    document = tomllib.loads(text)
    assert document["inference"]["mode"] == mode
    assert set(document["inference"]) == {"mode", mode}
    if table:
        assert document["inference"][mode] == table
    assert document["lab"] == {
        "machine_name": "pokus-lab",
        "lab_mode": "production",
        "node_role": extra.get("node_role", "full"),
    }
    assert document["query"]["loki_url"] == "http://127.0.0.1:18702"
    assert document["opa"] == {
        "url": "http://127.0.0.1:8181",
        "decision_path": "/v1/data/pokus/action/decision",
    }
    assert document["incus"] == {"url": "https://127.0.0.1:8443"}
    assert document["gateway"] == {"adhoc_listen": "127.0.0.1:18705"}
    assert document["telemetry"] == {"otlp_grpc": "127.0.0.1:4317", "otlp_http": "127.0.0.1:4318"}


def test_endpoints_carry_the_targets_cpuset() -> None:
    expression = "platform_endpoints | pokus_to_toml(platform_toml_header ~ 'endpoints.toml')"
    pinned = tomllib.loads(evaluate(expression, variables(cpuset_targets="6-19")))
    assert pinned["incus"] == {"url": "https://127.0.0.1:8443", "cpuset_targets": "6-19"}
    unpinned = tomllib.loads(evaluate(expression, variables()))
    assert "cpuset_targets" not in unpinned["incus"]


def test_to_toml_round_trip() -> None:
    doc = {"a": 1, "b": {"c": 'x"y', "d": [1.5, True], "e": {"f": "ü"}}, "g h": {"i": 0}}
    text = pokus.to_toml(doc, "header")
    assert text.startswith("# header\n")
    assert tomllib.loads(text) == doc
    with pytest.raises(TypeError):
        pokus.to_toml({"a": None})


SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "properties": {"workers": {"type": "integer", "minimum": 1}, "log": {"type": "string"}},
    "required": ["workers"],
}


def test_config_check(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    schema = tmp_path / "orch.schema.json"
    schema.write_text(json.dumps(SCHEMA))
    good = tmp_path / "good.toml"
    good.write_text("workers = 2\n")
    assert config_check.main(["--schema", str(schema), str(good)]) == 0
    bad = tmp_path / "bad.toml"
    bad.write_text("workers = 0\nextra = 1\n")
    assert config_check.main(["--schema", str(schema), str(bad)]) == 1
    err = capsys.readouterr().err
    assert "workers: 0 is less than the minimum of 1" in err
    assert "Additional properties are not allowed" in err
    broken = tmp_path / "broken.toml"
    broken.write_text("workers = \n")
    assert config_check.main(["--schema", str(schema), str(broken)]) == 1
    assert config_check.main(["--schema", str(tmp_path / "none.json"), str(good)]) == 2


# --- socket permissions helper ---------------------------------------------------------


def test_wait_for_socket(tmp_path: Path) -> None:
    path = tmp_path / "api.sock"
    clock = iter([0.0, 0.1, 0.2, 5.0])
    with pytest.raises(socket_perms.SocketError, match="did not appear"):
        socket_perms.wait_for_socket(path, 1.0, sleep=lambda _s: None, clock=lambda: next(clock))
    path.write_text("x")
    with pytest.raises(socket_perms.SocketError, match="not a socket"):
        socket_perms.wait_for_socket(path, 1.0)
    path.unlink()
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(str(path))
        socket_perms.wait_for_socket(path, 1.0)


def test_apply_sets_mode_then_acl(tmp_path: Path) -> None:
    import grp  # noqa: PLC0415 - only here
    import pwd  # noqa: PLC0415

    me = pwd.getpwuid(os.getuid()).pw_name
    group = grp.getgrgid(os.getgid()).gr_name
    path = tmp_path / "api.sock"
    calls: list[Sequence[str]] = []

    def record(argv: Sequence[str]) -> int:
        calls.append(argv)
        return 0

    with socket.socket(socket.AF_UNIX) as server:
        server.bind(str(path))
        socket_perms.apply(path, me, group, 0o600, ["user:root:rw-"], record)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert calls == [["setfacl", "--modify", "user:root:rw-", str(path)]]
        with pytest.raises(socket_perms.SocketError, match="setfacl failed"):
            socket_perms.apply(path, me, group, 0o600, ["user:root:rw-"], lambda _a: 1)
        calls.clear()
        socket_perms.apply(path, me, group, 0o660, [], record)
        assert calls == []


@pytest.mark.parametrize(
    "argv",
    [
        ["--path", "/tmp/x.sock", "--owner", "a", "--group", "b", "--mode", "0600"],  # noqa: S108
        ["--path", "/run/pokus/gw/svc.sock", "--owner", "a", "--group", "b", "--mode", "4777"],
        [
            "--path",
            "/run/pokus/gw/svc.sock",
            "--owner",
            "a",
            "--group",
            "b",
            "--mode",
            "0600",
            "--acl",
            "user:root:rwx,other::rwx",
        ],
    ],
)
def test_socket_perms_refuses_bad_arguments(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        socket_perms.main(argv, lambda _a: 0)
    assert raised.value.code == 2


def test_socket_perms_reports_a_missing_socket(capsys: pytest.CaptureFixture[str]) -> None:
    argv = [
        "--path",
        "/run/pokus/zz/none.sock",
        "--owner",
        "root",
        "--group",
        "root",
        "--mode",
        "0600",
        "--timeout",
        "0",
    ]
    assert socket_perms.main(argv, lambda _a: 0) == 1
    assert "did not appear" in capsys.readouterr().err


# --- role data ---------------------------------------------------------------------------


def test_catalog_root_and_defaults_are_off() -> None:
    assert load_yaml(ROLES / "catalog" / "defaults" / "main.yml")["catalog_enabled"] is False
    assert load_yaml(ROLE / "defaults" / "main.yml")["platform_enabled"] is False
    catalog = ansible_vars(GROUP_VARS, ROLES / "catalog" / "vars" / "main.yml")
    assert evaluate("catalog_root", catalog) == "/srv/pokus/catalog"


def test_quarto_asset_is_pinned() -> None:
    asset = evaluate("platform_quarto_asset", variables())
    assert asset["url"].endswith("quarto-1.10.18-linux-amd64.tar.gz")
    assert len(asset["sha256"]) == 64
    assert evaluate("platform_quarto_dir", variables()) == "/opt/pokus/quarto/1.10.18"


@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="needs systemd-analyze")
def test_units_pass_systemd_analyze(tmp_path: Path) -> None:
    """systemd itself parses every unit without a warning (unknown keys, bad values)."""
    system = tmp_path / "etc" / "systemd" / "system"
    system.mkdir(parents=True)
    shutil.copytree(
        "/usr/lib/systemd/system", tmp_path / "usr" / "lib" / "systemd" / "system", symlinks=True
    )
    for stub in ("pokus-health-gate.service", "pokus-core.service"):
        (system / stub).write_text("[Service]\nType=oneshot\nExecStart=/bin/true\n")
    (system / "pokus-lab.target").write_text("[Unit]\nDescription=stub\n")
    executables = [f"srv/pokus/deploy/platform/current/.venv/bin/{s}" for s in CONTRACT_SCRIPTS]
    for rel in [*executables, "usr/local/libexec/pokus/socket-perms"]:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\n")
        path.chmod(0o755)
    for name, unit in units().items():
        scope = variables()
        scope["platform_unit"] = unit
        (system / name).write_text(render_template("platform", "pokus-platform.service.j2", scope))
    for name in units():
        done = subprocess.run(
            [
                "systemd-analyze",
                f"--root={tmp_path}",
                "verify",
                "--man=no",
                f"etc/systemd/system/{name}",
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )
        assert done.returncode == 0, (name, done.stderr)
        assert done.stderr.strip() == "", (name, done.stderr)
