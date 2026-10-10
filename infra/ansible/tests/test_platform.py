"""Tests for the platform role: the deployment settings, and the contract v1 units,
sockets, configuration and helpers that rendering from the profiles replaces
in plan step 3d.3."""

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

import pinka_platform_config_check as config_check
import pinka_socket_perms as socket_perms
import pytest

import pinka
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
    "potka-orchestrator",
    "potka-reconcile",
    "potka-agent-runner",
    "potka-llm-gateway",
    "potka-executor",
    "potka-injector",
    "potka-verifier",
    "potka-evaluator",
}
AGENT_DENIED = {
    "/srv/pinka/catalog",
    "/srv/pinka/artifacts",
    "/srv/pinka/services",
    "/etc/pinka/secrets",
    "/etc/pinka/incus",
    "/var/lib/incus",
    "/run/docker.sock",
    "/var/lib/pinka/git",
    "/run/pinka/postgres",
    "/run/pinka/postgres-reader",
    "/run/pinka/inject",
    "/run/pinka/verify",
    "/run/pinka/vllm-deploy.sock",
    "/run/pinka/orch/api.sock",
    "/run/pinka/gw/svc.sock",
}


def variables(**extra: Any) -> dict[str, Any]:
    base = {
        "machine_name": "pinka-lab",
        "node_role": "full",
        "pinka_features": {"gpu_inference": True, "vm_targets": False},
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
    text = render_template("platform", "potka-platform.service.j2", scope)
    assert text.startswith("# Managed by pinka (ansible/roles/platform/templates/")
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
    assert accounts["potka-orchestrator"] == accounts["potka-reconcile"] == "potka-orch"
    assert accounts["potka-llm-gateway"] == "potka-gw"
    assert accounts["potka-agent-runner"] == "potka-agent"
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
            "WantedBy": "pinka-lab.target",
        }.items():
            assert unit[key] == [value], (name, key)
        assert unit["Requires"][0].startswith("pinka-health-gate.service"), name
        assert "pinka-health-gate.service" in unit["After"][0], name
        assert unit["PartOf"][0].startswith("pinka-lab.target"), name
        assert unit["ExecStart"][0].startswith("/srv/pinka/deploy/platform/current/.venv/bin/")
        denied = {p.lstrip("-") for p in unit["InaccessiblePaths"][0].split()}
        assert {"/var/lib/pinka/git", "/run/docker.sock", "/var/lib/incus"} <= denied


def test_agent_runner_isolation() -> None:
    unit = render_unit("potka-agent-runner.service")
    denied = {p.lstrip("-") for p in unit["InaccessiblePaths"][0].split()}
    assert denied >= AGENT_DENIED
    assert unit["User"] == ["potka-agent"]
    assert unit["SupplementaryGroups"] == ["potka-svc potka-exec-clients potka-gw-clients"]
    assert "ReadWritePaths" not in unit
    assert "potka-orchestrator.service" in unit["PartOf"][0]
    assert "potka-llm-gateway.service" in unit["PartOf"][0]
    for service in ("potka-injector.service", "potka-verifier.service", "potka-executor.service"):
        assert service in unit["After"][0]


def test_reconcile_runs_before_the_orchestrator() -> None:
    reconcile = render_unit("potka-reconcile.service")
    assert reconcile["Type"] == ["oneshot"]
    assert reconcile["Before"] == ["potka-orchestrator.service"]
    assert "RuntimeDirectory" not in reconcile
    orchestrator = render_unit("potka-orchestrator.service")
    assert "potka-reconcile.service" in orchestrator["Requires"][0]
    assert orchestrator["Restart"] == ["on-failure"]
    assert orchestrator["RuntimeDirectory"] == ["pinka/orch"]
    assert orchestrator["ReadWritePaths"] == ["/srv/pinka/artifacts"]


def test_evaluator_may_generate_code() -> None:
    assert render_unit("potka-evaluator.service")["MemoryDenyWriteExecute"] == ["no"]
    assert render_unit("potka-executor.service")["MemoryDenyWriteExecute"] == ["yes"]
    assert render_unit("potka-evaluator.service")["ReadWritePaths"] == ["/srv/pinka/reports"]


@pytest.mark.parametrize(
    ("acls", "expected"),
    [
        (
            True,
            "--path /run/pinka/gw/svc.sock --owner potka-gw --group potka-gw --mode 0600 "
            "--acl user:potka-orch:rw-",
        ),
        (False, "--path /run/pinka/gw/svc.sock --owner potka-gw --group potka-svc --mode 0660"),
    ],
)
def test_socket_permissions_in_the_unit(acls: bool, expected: str) -> None:
    unit = render_unit("potka-llm-gateway.service", platform_socket_acls=acls)
    posts = unit["ExecStartPost"]
    assert len(posts) == 2
    assert posts[1] == f"+/usr/local/libexec/pinka/socket-perms {expected}"


def test_sockets_cover_the_contract() -> None:
    specs = evaluate("platform_socket_expectations", variables())
    by_path = {s["path"]: s for s in specs}
    assert set(by_path) == {
        "/run/pinka/orch/api.sock",
        "/run/pinka/orch/svc.sock",
        "/run/pinka/exec/api.sock",
        "/run/pinka/gw/agent.sock",
        "/run/pinka/gw/svc.sock",
        "/run/pinka/inject/api.sock",
        "/run/pinka/verify/api.sock",
    }
    assert by_path["/run/pinka/inject/api.sock"]["expect_mode"] == "0660"
    assert by_path["/run/pinka/inject/api.sock"]["expect_acl"] == [
        "user:potka-orch:rw-",
        "group::---",
        "mask::rw-",
    ]
    fallback = {
        s["path"]: s
        for s in evaluate("platform_socket_expectations", variables(platform_socket_acls=False))
    }
    assert fallback["/run/pinka/inject/api.sock"]["group"] == "pinka-restricted"
    assert fallback["/run/pinka/inject/api.sock"]["expect_acl"] == []


def test_socket_specs_filter() -> None:
    sockets: list[dict[str, Any]] = [
        {
            "path": "/run/pinka/a/x.sock",
            "owner": "a",
            "group": "a",
            "mode": "0600",
            "acl": ["user:b:rw-"],
            "fallback": {"group": "c", "mode": "0660"},
        },
        {
            "path": "/run/pinka/a/y.sock",
            "owner": "a",
            "group": "g",
            "mode": "0640",
            "acl": ["group:h:r--"],
        },
    ]
    with_acl = pinka.socket_specs(sockets, True)
    assert with_acl[0]["expect_mode"] == "0660"
    assert with_acl[1]["expect_mode"] == "0640"
    assert with_acl[1]["expect_acl"] == ["group:h:r--", "group::r--", "mask::r--"]
    assert "fallback" not in with_acl[0]
    without = pinka.socket_specs(sockets, False)
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
                "deploy_helper_socket": "/run/pinka/vllm-deploy.sock",
            },
        ),
        (
            {"node_role": "lab", "inference_remote_node": "pinka-gpu"},
            "remote",
            {
                "api_url": "https://pinka-gpu:18704",
                "control_url": "https://pinka-gpu:18710",
                "tls_dir": "/etc/pinka/inference",
            },
        ),
        ({"pinka_features": {"gpu_inference": False}}, "remote", None),
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
        "platform_endpoints | pinka_to_toml(platform_toml_header ~ 'endpoints.toml')", scope
    )
    assert text.startswith("# Managed by pinka (ansible/roles/platform).")
    document = tomllib.loads(text)
    assert document["inference"]["mode"] == mode
    assert set(document["inference"]) == {"mode", mode}
    if table:
        assert document["inference"][mode] == table
    assert document["lab"] == {
        "machine_name": "pinka-lab",
        "lab_mode": "production",
        "node_role": extra.get("node_role", "full"),
    }
    assert document["query"]["loki_url"] == "http://127.0.0.1:18702"
    assert document["opa"] == {
        "url": "http://127.0.0.1:8181",
        "decision_path": "/v1/data/potka/action/decision",
    }
    assert document["incus"] == {"url": "https://127.0.0.1:8443"}
    assert document["gateway"] == {"adhoc_listen": "127.0.0.1:18705"}
    assert document["telemetry"] == {"otlp_grpc": "127.0.0.1:4317", "otlp_http": "127.0.0.1:4318"}


def test_endpoints_carry_the_targets_cpuset() -> None:
    expression = "platform_endpoints | pinka_to_toml(platform_toml_header ~ 'endpoints.toml')"
    pinned = tomllib.loads(evaluate(expression, variables(cpuset_targets="6-19")))
    assert pinned["incus"] == {"url": "https://127.0.0.1:8443", "cpuset_targets": "6-19"}
    unpinned = tomllib.loads(evaluate(expression, variables()))
    assert "cpuset_targets" not in unpinned["incus"]


def test_to_toml_round_trip() -> None:
    doc = {"a": 1, "b": {"c": 'x"y', "d": [1.5, True], "e": {"f": "ü"}}, "g h": {"i": 0}}
    text = pinka.to_toml(doc, "header")
    assert text.startswith("# header\n")
    assert tomllib.loads(text) == doc
    with pytest.raises(TypeError):
        pinka.to_toml({"a": None})


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
        ["--path", "/run/pinka/gw/svc.sock", "--owner", "a", "--group", "b", "--mode", "4777"],
        [
            "--path",
            "/run/pinka/gw/svc.sock",
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
        "/run/pinka/zz/none.sock",
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


def test_no_platform_is_deployed_by_default() -> None:
    data = load_yaml(GROUP_VARS)
    assert data["platform"] == {}
    assert data["content"] == {}
    assert evaluate("platform_deploy", variables()) is False
    assert evaluate("platform_sources", variables()) == []
    assert evaluate("platform_replace", variables()) == ""
    assert evaluate("platform_inspect", variables()) is False


def source(repo: str, **extra: str) -> dict[str, str]:
    return {"repo": repo, "ref": "platform-v0.1.0", "path": "platform", **extra}


@pytest.mark.parametrize(
    ("repo", "host"),
    [
        ("git@github.com:example-org/pokus-platform.git", "github.com"),
        ("ssh://git@git.example.com/example-org/pokus-platform.git", "git.example.com"),
        (
            "ssh://git@git.example.com:2222/example-org/pokus-platform.git",
            "[git.example.com]:2222",
        ),
        ("https://git.example.com/example-org/pokus-platform.git", None),
        ("file:///srv/git/pokus-platform.git", None),
    ],
)
def test_ssh_sources_and_hosts(repo: str, host: str | None) -> None:
    scope = variables(platform=source(repo))
    assert evaluate("platform_deploy", scope) is True
    assert evaluate("platform_ssh_repos", scope) == ([repo] if host else [])
    assert evaluate("platform_ssh_hosts", scope) == ([host] if host else [])


def test_content_sources_count_too() -> None:
    scope = variables(
        platform=source("https://git.example.com/example-org/platform.git"),
        content={"catalog": source("git@git.example.com:example-org/catalog.git", path="")},
    )
    assert evaluate("platform_ssh_hosts", scope) == ["git.example.com"]
    settings = evaluate("platform_tool_settings", scope)
    assert settings["ssh_key"] == "/var/lib/pinka/git/deploy_key"
    assert settings["known_hosts"] == "/var/lib/pinka/git/known_hosts"
    assert settings["content"]["catalog"]["repo"].startswith("git@")


def test_known_hosts_pin_only_verified_github_keys() -> None:
    github = variables(platform=source("git@github.com:example-org/pokus-platform.git"))
    lock = load_yaml(GROUP_VARS.parent / "versions.yml")["pinka_versions"]["github_ssh_host_keys"]
    verified = {k: v for k, v in lock["keys"].items() if "TODO" not in v["public_key"]}
    lines = evaluate("platform_known_host_lines", github)
    assert lines == [f"github.com {k} {v['public_key']}" for k, v in verified.items()]
    extra = ["[git.example.com]:2222 ssh-ed25519 AAAAC3Nza"]
    other = variables(
        platform=source("ssh://git@git.example.com:2222/example-org/pokus-platform.git"),
        pinka_source_known_hosts=extra,
    )
    assert evaluate("platform_known_host_lines", other) == extra


def test_tool_settings_for_https_and_bootstrap_options() -> None:
    scope = variables(
        platform=source("https://git.example.com/example-org/platform.git"),
        pinka_bootstrap={"replace_platform": "old", "inspect_platform": True},
    )
    settings = evaluate("platform_tool_settings", scope)
    assert settings["ssh_key"] == ""
    assert settings["known_hosts"] == ""
    assert settings["replace"] == "old"
    assert settings["require_signed_tags"] is False
    assert settings["owners"] == {
        "user": "root",
        "group": "root",
        "restricted_group": "pinka-restricted",
        "build_user": "pinka-build",
        "build_group": "pinka-build",
    }
    assert evaluate("platform_inspect", scope) is True


def test_the_tool_files_exist() -> None:
    from ansible_testlib import INFRA  # noqa: PLC0415 - only here

    for entry in evaluate("platform_tool_files", variables()):
        assert list((INFRA / entry["src"]).glob(entry["patterns"][0])), entry
    wrapper = (ROLE / "files" / "pinka-platform").read_text(encoding="utf-8")
    assert 'runpy.run_module("pinka_platform"' in wrapper
    assert "/opt/pinka/ansible-venv/bin/python3" in wrapper


def test_quarto_asset_is_pinned() -> None:
    asset = evaluate("platform_quarto_asset", variables())
    assert asset["url"].endswith("quarto-1.10.18-linux-amd64.tar.gz")
    assert len(asset["sha256"]) == 64
    assert evaluate("platform_quarto_dir", variables()) == "/opt/pinka/quarto/1.10.18"


@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="needs systemd-analyze")
def test_units_pass_systemd_analyze(tmp_path: Path) -> None:
    """systemd itself parses every unit without a warning (unknown keys, bad values)."""
    system = tmp_path / "etc" / "systemd" / "system"
    system.mkdir(parents=True)
    shutil.copytree(
        "/usr/lib/systemd/system", tmp_path / "usr" / "lib" / "systemd" / "system", symlinks=True
    )
    for stub in ("pinka-health-gate.service", "pinka-core.service"):
        (system / stub).write_text("[Service]\nType=oneshot\nExecStart=/bin/true\n")
    (system / "pinka-lab.target").write_text("[Unit]\nDescription=stub\n")
    executables = [f"srv/pinka/deploy/platform/current/.venv/bin/{s}" for s in CONTRACT_SCRIPTS]
    for rel in [*executables, "usr/local/libexec/pinka/socket-perms"]:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\n")
        path.chmod(0o755)
    for name, unit in units().items():
        scope = variables()
        scope["platform_unit"] = unit
        (system / name).write_text(render_template("platform", "potka-platform.service.j2", scope))
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
