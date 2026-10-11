"""The docs service (BUILD_PROMPT §7.19) and maintenance windows (§7.20)."""

from __future__ import annotations

import configparser
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from ansible_testlib import (
    GROUP_VARS,
    ROLES,
    ansible_vars,
    evaluate,
    load_yaml,
    render_template,
    role_vars,
)

RELEASE = {"url": "https://example.com/releases/pinka-docs-v1.tar.gz", "sha256": "a" * 64}


def unit(text: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str  # type: ignore[assignment,method-assign]
    parser.read_string(text)
    return parser


def task(role: str, name: str, tasks: str = "main.yml") -> dict[str, Any]:
    found: dict[str, Any] = next(
        t for t in load_yaml(ROLES / role / "tasks" / tasks) if t["name"] == name
    )
    return found


def docs_vars(**extra: Any) -> dict[str, Any]:
    return role_vars("docs", **extra)


# --- docs ----------------------------------------------------------------------


def test_docs_unit_is_hardened_and_loopback_only() -> None:
    service = unit(render_template("docs", "pinka-docs.service.j2", docs_vars()))["Service"]
    assert service["ExecStart"] == (
        "/usr/local/bin/pinka docs serve --dir /usr/local/share/pinka/docs/current --port 18706"
    )
    assert service["DynamicUser"] == "yes"
    assert service["ReadOnlyPaths"] == "/usr/local/share/pinka/docs"
    assert (service["IPAddressDeny"], service["IPAddressAllow"]) == ("any", "localhost")
    assert service["SocketBindAllow"] == "tcp:18706"
    for key in ("NoNewPrivileges", "PrivateTmp", "PrivateDevices", "MemoryDenyWriteExecute"):
        assert service[key] == "yes", key
    assert service["CapabilityBoundingSet"] == ""
    hidden = service["InaccessiblePaths"].split()
    assert {"-/srv/pinka", "-/etc/pinka/secrets", "-/var/lib/pinka", "-/run/pinka"} <= set(hidden)


def test_docs_port_follows_the_overrides() -> None:
    text = render_template("docs", "pinka-docs.service.j2", docs_vars(ports={"docs_serve": 18756}))
    assert unit(text)["Service"]["SocketBindAllow"] == "tcp:18756"


@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="needs systemd-analyze")
def test_docs_and_window_units_pass_systemd_analyze(tmp_path: Path) -> None:
    system = tmp_path / "etc" / "systemd" / "system"
    system.mkdir(parents=True)
    shutil.copytree(
        "/usr/lib/systemd/system", tmp_path / "usr" / "lib" / "systemd" / "system", symlinks=True
    )
    (system / "pinka-lab.target").write_text("[Unit]\nDescription=stub\n")
    program = tmp_path / "usr/local/bin/pinka"
    program.parent.mkdir(parents=True)
    program.write_text("#!/bin/sh\n")
    program.chmod(0o755)
    variables = role_vars("lab_session", lab_mode="staging", staging_shared_hardware=True)
    units = {
        "pinka-docs.service": render_template("docs", "pinka-docs.service.j2", docs_vars()),
        "pinka-window-expiry.service": render_template(
            "lab_session", "pinka-window-expiry.service.j2", variables
        ),
        "pinka-window-expiry.timer": render_template(
            "lab_session", "pinka-window-expiry.timer.j2", variables
        ),
    }
    for name, text in units.items():
        (system / name).write_text(text)
    done = subprocess.run(
        ["systemd-analyze", f"--root={tmp_path}", "verify", "--man=no"]
        + [f"etc/systemd/system/{name}" for name in units],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert done.stderr.strip() == "", done.stderr


def test_docs_release_is_checked() -> None:
    check = task("docs", "Check the documentation release")
    that = check["ansible.builtin.assert"]["that"]

    def holds(release: Any) -> bool:
        variables = docs_vars(docs_release=release)
        return all(evaluate(condition, variables) for condition in that)

    assert holds(RELEASE)
    assert holds({"url": "/srv/pinka/shared/docs.tar.gz", "sha256": "b" * 64})
    assert not holds({"url": "http://example.com/docs.tar.gz", "sha256": "a" * 64})
    assert not holds({"url": "docs.tar.gz", "sha256": "a" * 64})
    assert not holds({"url": RELEASE["url"], "sha256": "A" * 64})
    assert not holds({"url": RELEASE["url"]})
    assert evaluate(check["when"], docs_vars()) is False  # null: nothing to check


def test_docs_release_is_installed_like_pinka_docs_install() -> None:
    install = task("docs", "Install the documentation release")
    variables = docs_vars(docs_release=RELEASE)
    words = [
        evaluate(w[3:-3], variables) if "{{" in w else w
        for w in install["ansible.builtin.command"]["argv"]
    ]
    assert words == [
        "/usr/local/bin/pinka",
        "docs",
        "install",
        "/var/cache/pinka/docs/pinka-docs-v1.tar.gz",
        "--sha256",
        "a" * 64,
    ]
    local = docs_vars(docs_release={"url": "/srv/pinka/shared/d.tgz", "sha256": "a" * 64})
    assert evaluate(install["ansible.builtin.command"]["argv"][3][3:-3], local) == (
        "/srv/pinka/shared/d.tgz"
    )
    service = task("docs", "Run the documentation service while a release is configured")
    module = service["ansible.builtin.systemd_service"]
    assert evaluate(module["state"][3:-3], docs_vars()) == "stopped"
    assert evaluate(module["state"][3:-3], variables) == "started"


def test_docs_on_the_tailnet_only_on_request() -> None:
    serve = task("tailscale", "Publish the documentation on the tailnet only", "funnel.yml")
    assert serve["ansible.builtin.command"]["argv"][:3] == ["tailscale", "serve", "--bg"]
    assert "funnel" not in " ".join(serve["ansible.builtin.command"]["argv"])
    firewall = "firewall_tailnet_tcp_ports"
    off = role_vars("firewall", node_role="full", inference_remote_node="")
    assert 10000 not in evaluate(firewall, off)
    on = role_vars(
        "firewall",
        node_role="full",
        inference_remote_node="",
        docs_tailscale_serve=True,
        docs_release=RELEASE,
    )
    assert 10000 in evaluate(firewall, on)


# --- maintenance windows ------------------------------------------------------------


def session_vars(**extra: Any) -> dict[str, Any]:
    role = ROLES / "lab_session"
    return ansible_vars(
        GROUP_VARS, role / "defaults" / "main.yml", role / "vars" / "main.yml", **extra
    )


def test_the_gate_reads_the_window_record() -> None:
    write = task("lab_session", "Write the health gate's configuration")
    content = write["ansible.builtin.copy"]["content"]
    assert "'window': {'record': lab_session_window_record" in content
    assert evaluate("lab_session_window_record", session_vars()) == (
        "/etc/pinka/maintenance-window.json"
    )


@pytest.mark.parametrize(
    ("mode", "shared", "expiry"),
    [
        ("production", False, False),
        ("production", True, False),
        ("staging", False, False),
        ("staging", True, True),
    ],
)
def test_the_expiry_timer_only_for_staging_on_shared_hardware(
    mode: str, shared: bool, expiry: bool
) -> None:
    variables = session_vars(lab_mode=mode, staging_shared_hardware=shared)
    assert evaluate("lab_session_window_expiry", variables) is expiry


def test_expiry_units() -> None:
    variables = role_vars("lab_session", lab_mode="staging", staging_shared_hardware=True)
    service = unit(render_template("lab_session", "pinka-window-expiry.service.j2", variables))
    assert service["Service"]["ExecStart"] == "/usr/local/bin/pinka window expire"
    assert service["Service"]["Type"] == "oneshot"
    timer = unit(render_template("lab_session", "pinka-window-expiry.timer.j2", variables))
    assert timer["Timer"]["OnCalendar"] == "minutely"
