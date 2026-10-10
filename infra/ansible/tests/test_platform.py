"""The platform role (BUILD_PROMPT §7.14, contract v2): its data, the socket ACL helper
and the tasks that apply what pinka-platform renders from the profiles."""

from __future__ import annotations

import json
import socket
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pinka_socket_acl as socket_acl
import pytest

from ansible_testlib import (
    GROUP_VARS,
    ROLES,
    ansible_vars,
    evaluate,
    load_yaml,
)

ROLE = ROLES / "platform"


def variables(**extra: Any) -> dict[str, Any]:
    base = {
        "machine_name": "pinka-lab",
        "node_role": "full",
        "lab_mode": "production",
        "pinka_features": {"gpu_inference": True, "vm_targets": False},
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


def tasks(name: str) -> list[dict[str, Any]]:
    data = load_yaml(ROLE / "tasks" / name)
    assert isinstance(data, list)
    return data


def flat(entries: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for entry in entries:
        out.append(entry)
        out += flat(entry.get("block", []))
    return out


# --- the socket ACL helper (§12.3) ------------------------------------------------------


class Setfacl:
    """A fake setfacl with a given outcome."""

    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        self.returncode = returncode
        self.stderr = stderr
        self.calls: list[list[str]] = []

    def __call__(self, argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(list(argv), self.returncode, "", self.stderr)


@pytest.fixture
def sock(tmp_path: Path) -> Path:
    path = tmp_path / "api.sock"
    server = socket.socket(socket.AF_UNIX)
    server.bind(str(path))
    server.close()
    path.chmod(0o660)
    return path


def test_acl_entries_for_the_human_groups(sock: Path) -> None:
    setfacl = Setfacl()
    assert socket_acl.apply(sock, ["pinka", "pinka-admin"], setfacl) == "acl"
    assert setfacl.calls == [
        ["setfacl", "--modify", "group:pinka:rw-,group:pinka-admin:rw-", str(sock)]
    ]
    assert sock.stat().st_mode & 0o777 == 0o660


def test_without_acl_support_the_socket_falls_back_to_0666(sock: Path) -> None:
    setfacl = Setfacl(1, "setfacl: api.sock: Operation not supported\n")
    assert socket_acl.apply(sock, ["pinka"], setfacl) == "fallback"
    assert sock.stat().st_mode & 0o777 == 0o666


def test_other_setfacl_failures_fail_the_socket(sock: Path) -> None:
    with pytest.raises(socket_acl.SocketError, match="setfacl failed"):
        socket_acl.apply(sock, ["pinka"], Setfacl(1, "setfacl: invalid argument\n"))
    assert sock.stat().st_mode & 0o777 == 0o660


def test_a_file_that_is_no_socket_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "x.sock"
    path.write_text("")
    with pytest.raises(socket_acl.SocketError, match="not a socket"):
        socket_acl.apply(path, ["pinka"], Setfacl())


@pytest.mark.parametrize(
    "argv",
    [
        ["/run/pinka/platform/ctl/api.sock"],
        ["/run/pinka/platform/ctl/api.sock", "docker"],
        ["/run/pinka/postgres/.s.PGSQL.5432", "pinka"],
        ["/run/pinka/platform/../gpu.sock", "pinka"],
        ["/tmp/api.sock", "pinka"],  # noqa: S108 - a refused path
    ],
)
def test_the_helper_refuses_other_arguments(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        socket_acl.main(argv, Setfacl())
    assert exc.value.code == 2


def test_the_helper_reports_a_missing_socket(capsys: pytest.CaptureFixture[str]) -> None:
    assert socket_acl.main(["/run/pinka/platform/ctl/missing.sock", "pinka"], Setfacl()) == 1
    assert "socket-acl:" in capsys.readouterr().err


# --- the tasks that apply the rendering (§7.14 step 7) --------------------------------


def test_render_runs_between_retire_and_the_switch() -> None:
    names = [t["name"] for t in flat(tasks("deploy.yml"))]
    assert names.index("Retire the platform this release replaces") < names.index(
        "Render the platform from its profiles (§7.14 step 7)"
    )
    assert names.index("Render the platform from its profiles (§7.14 step 7)") < names.index(
        "Switch to the release (§7.14 step 8)"
    )


def test_render_applies_every_part_of_the_rendering() -> None:
    entries = flat(tasks("render.yml"))
    text = (ROLE / "tasks" / "render.yml").read_text(encoding="utf-8")
    for key in (
        "groups",
        "accounts",
        "directories",
        "config_files",
        "secrets",
        "units",
        "stale_units",
        "incus_certificates",
        "database",
        "run_network",
        "cli",
    ):
        assert f"active_platform.{key}" in text, key
    accounts = next(t for t in entries if t["name"] == "Create the platform's accounts")
    user = accounts["ansible.builtin.user"]
    assert user["append"] is False  # the rendered groups are the only ones
    assert user["password"] == "!"  # noqa: S105 - the locked-password marker
    assert user["create_home"] is False
    secrets = next(t for t in entries if t["name"] == "Write the services' secrets")
    assert secrets["no_log"] is True
    commands = [
        t["ansible.builtin.command"]["argv"] for t in entries if "ansible.builtin.command" in t
    ]
    assert [
        "{{ platform_tool }}",
        "deploy",
        "render",
        "--run",
        "{{ platform_plan.run_dir }}",
    ] in commands
    assert [
        "{{ platform_tool }}",
        "deploy",
        "principals",
        "--run",
        "{{ platform_plan.run_dir }}",
    ] in commands
    # Units are installed, not started: the switch with the hooks comes later.
    assert not [
        t for t in entries if t.get("ansible.builtin.systemd_service", {}).get("state") == "started"
    ]


def test_the_database_and_certificates_come_from_their_roles() -> None:
    entries = flat(tasks("render.yml"))
    includes = {
        (t["ansible.builtin.include_role"]["name"], t["ansible.builtin.include_role"]["tasks_from"])
        for t in entries
        if "ansible.builtin.include_role" in t
    }
    assert includes == {
        ("incus", "certificates"),
        ("postgres", "platform"),
        ("incus", "run_network"),
    }


def test_missing_vault_keys_stop_bootstrap_with_77() -> None:
    entry = next(t for t in tasks("prepare.yml") if t["name"].startswith("Ask for the vault keys"))
    assert entry["vars"]["bootstrap_signal_code"] == 77
    assert entry["when"] == "platform_plan.vault_keys.missing | length > 0"


def test_signing_key_follows_the_rendering() -> None:
    text = (ROLE / "tasks" / "signing.yml").read_text(encoding="utf-8")
    assert "active_platform.signing.owner" in text
    assert 'mode: "0400"' in text
    assert "published_prefix" in text


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
        content={"cases": source("git@git.example.com:example-org/cases.git", path="")},
    )
    assert evaluate("platform_ssh_hosts", scope) == ["git.example.com"]
    settings = evaluate("platform_tool_settings", scope)
    assert settings["ssh_key"] == "/var/lib/pinka/git/deploy_key"
    assert settings["known_hosts"] == "/var/lib/pinka/git/known_hosts"
    assert settings["content"]["cases"]["repo"].startswith("git@")


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
    assert settings["lab"] == {
        "machine_name": "pinka-lab",
        "lab_mode": "production",
        "node_role": "full",
        "ports": evaluate("pinka_ports", scope),
        "inference_mode": "local",
        "scripted_api_url": "",
        "vm_collector_address": "198.18.0.1",
        "cpuset_targets": "",
    }
    assert settings["vault_keys"] == []
    assert settings["platform_settings"] == {}


def test_the_tool_learns_the_vault_keys_but_never_their_values() -> None:
    scope = variables(
        platform=source("https://git.example.com/example-org/platform.git"),
        vault_postgres_passwords={"demo_ctl": "secret-1"},
        vault_platform_secrets={"ctl": {"token": "secret-2"}},
    )
    settings = evaluate("platform_tool_settings", scope)
    assert settings["vault_keys"] == [
        "vault_platform_secrets.ctl.token",
        "vault_postgres_passwords.demo_ctl",
    ]
    assert "secret-1" not in json.dumps(settings)
    assert "secret-2" not in json.dumps(settings)


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
