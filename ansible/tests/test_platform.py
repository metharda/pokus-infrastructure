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
        "pinka_training": "gpu",
        "pinka_training_caps": {"timeout_seconds": 86400, "memory_max": "18G", "cpus": 8},
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


API = "/run/pinka/platform/ctl/api.sock"
SVC = "/run/pinka/platform/ctl/svc.sock"


@pytest.fixture
def rendered(tmp_path: Path) -> Path:
    path = tmp_path / "rendered.json"
    path.write_text(
        json.dumps({"platform": "refplat", "sockets": [{"path": API}, {"path": SVC}]}),
        encoding="utf-8",
    )
    return path


def fallback(path: Path) -> list[str] | None:
    data: dict[str, list[str]] = json.loads(path.read_text(encoding="utf-8"))
    return data.get(socket_acl.FALLBACK_KEY)


def test_a_fallback_is_recorded_in_the_rendering(rendered: Path) -> None:
    assert socket_acl.record(rendered, Path(SVC), fell_back=True) is True
    assert socket_acl.record(rendered, Path(API), fell_back=True) is True
    assert fallback(rendered) == [API, SVC]
    assert socket_acl.record(rendered, Path(API), fell_back=True) is False  # unchanged
    assert rendered.stat().st_mode & 0o777 == 0o644
    lock = rendered.with_name(".rendered.json.lock")
    assert lock.stat().st_mode & 0o777 == 0o600


def test_acl_support_removes_the_record(rendered: Path) -> None:
    socket_acl.record(rendered, Path(API), fell_back=True)
    assert socket_acl.record(rendered, Path(API), fell_back=False) is True
    assert fallback(rendered) is None  # left out while empty
    assert socket_acl.record(rendered, Path(API), fell_back=False) is False


def test_only_rendered_sockets_are_recorded(rendered: Path, tmp_path: Path) -> None:
    assert socket_acl.record(rendered, Path("/run/pinka/platform/x/y.sock"), True) is False
    assert fallback(rendered) is None
    assert socket_acl.record(tmp_path / "missing.json", Path(API), fell_back=True) is False


def test_the_helper_records_each_outcome(
    rendered: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    outcomes = iter(["fallback", "acl"])
    monkeypatch.setattr(socket_acl, "apply", lambda *_: next(outcomes))
    argv = [API, "pinka", "--rendered", str(rendered)]
    assert socket_acl.main(argv, Setfacl()) == 0
    assert fallback(rendered) == [API]
    assert "0666" in capsys.readouterr().err
    assert socket_acl.main(argv, Setfacl()) == 0
    assert fallback(rendered) is None


# --- the tasks that apply the rendering (§7.14 step 7) --------------------------------


def test_render_runs_between_retire_and_the_switch() -> None:
    names = [t["name"] for t in flat(tasks("deploy.yml"))]
    assert names.index("Retire the platform this release replaces") < names.index(
        "Render the platform from its profiles"
    )
    assert names.index("Render the platform from its profiles") < names.index(
        "Switch to the release"
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


# --- the switch with the hooks (§7.14 step 8, §12.9) -----------------------------------


def names_of(name: str) -> list[str]:
    return [t["name"] for t in flat(tasks(name))]


def argv_of(task: dict[str, Any]) -> list[str]:
    argv: list[str] = task["ansible.builtin.command"]["argv"]
    return argv


def test_a_new_release_stops_the_running_one_before_anything_changes() -> None:
    names = names_of("render.yml")
    stop = names.index("Drain and stop the running release before it changes")
    assert (
        names.index("Remember the rendering") < stop < names.index("Create the platform's groups")
    )
    assert stop < names.index("Stop the units this release no longer has")
    task = next(t for t in tasks("render.yml") if t["name"].startswith("Drain and stop"))
    assert task["ansible.builtin.include_tasks"] == "stop.yml"
    assert "platform_switch.release_changed or platform_switch.content_changed" in task["when"]
    # A replaced platform was drained by retire.yml already.
    assert "platform_switch.previous" in task["vars"]["platform_drain_first"]


def test_stop_drains_first_and_never_fails_on_the_drain() -> None:
    entries = tasks("stop.yml")
    names = [t["name"] for t in entries]
    assert names.index("Run the active platform's drain hook") < names.index("Stop the platform")
    drain = entries[0]
    assert argv_of(drain) == ["{{ platform_tool }}", "hook", "drain"]
    assert drain["failed_when"] is False  # infra stops the services after the timeout
    stop = next(t for t in entries if t["name"] == "Stop the platform")
    assert stop["ansible.builtin.systemd_service"] == {
        "name": "pinka-platform.target",
        "state": "stopped",
    }


def test_the_switch_migrates_checks_and_then_switches() -> None:
    entries = tasks("switch.yml")
    names = [t["name"] for t in entries]
    order = [
        "Drain and stop the running release when its rendering changed",
        "Run the new release's migrate hook",
        "Check schema restricted after the migration",
        "Switch current to the release and record it",
        "Enable the platform's units in pinka-platform.target",
    ]
    assert [n for n in names if n in order] == order
    migrate = entries[1]
    assert argv_of(migrate)[1:3] == ["deploy", "migrate"]
    assert "failed_when" not in migrate  # a failed migration ends the deployment
    check = entries[2]["ansible.builtin.include_role"]
    assert check == {"name": "postgres", "tasks_from": "restricted_check"}
    assert (ROLES / "postgres" / "tasks" / "restricted_check.yml").is_file()
    restart = entries[0]["when"][-1]
    for changed in ("accounts", "config", "secrets", "units"):
        assert f"platform_{changed}_written is changed" in restart
    assert "platform_principals is changed" in restart


def test_the_platform_is_never_started_before_the_end_of_the_play() -> None:
    for name in ("deploy.yml", "render.yml", "switch.yml", "retire.yml", "stop.yml"):
        for task in flat(tasks(name)):
            assert task.get("ansible.builtin.systemd_service", {}).get("state") != "started", name


def test_retire_drains_the_replaced_platform_first() -> None:
    names = names_of("retire.yml")
    assert names.index("Drain and stop the replaced platform") < names.index(
        "Stop and disable the replaced platform's units"
    )


def test_start_waits_for_the_health_hook_and_only_warns() -> None:
    entries = flat(tasks("start.yml"))
    start = next(t for t in entries if t["name"] == "Start the platform")
    assert start["ansible.builtin.systemd_service"]["state"] == "started"
    health = next(t for t in entries if t["name"].startswith("Wait until"))
    assert argv_of(health)[1:4] == ["hook", "health", "--until-healthy"]
    assert health["failed_when"] is False  # start reports "unhealthy"; doctor warns
    site = load_yaml(ROLES.parent / "site.yml")
    roles = [t.get("name") for play in site for t in play.get("tasks", [])]
    assert roles.index("Start the platform") > roles.index(
        "Lab session (target, health gate, session record)"
    )
    assert roles.index("Start the platform") > roles.index("Open Policy Agent")


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
        "training_caps": evaluate("pinka_training_caps", scope),
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
