"""Role templates render to valid files with the required content.

Templates are rendered like ``ansible.builtin.template`` with the variables
the role sees. Where a validator exists it is used: ``nft -c`` (in a new,
empty network namespace, so the check never touches the host's firewall),
ShellCheck and ``sh -n`` for scripts, ``visudo -c`` for the sudoers drop-in;
systemd units and INI-style files are parsed. Expected values come from
BUILD_PROMPT §3.1 (wsl.conf), §7.2 (updates), §7.3 (sshd) and §7.5 (firewall).
"""

from __future__ import annotations

import configparser
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from ansible_testlib import ROLES, evaluate, load_yaml, render_template, role_names, role_vars

TESTED_TEMPLATES = {
    ("base", "52pokus-unattended-upgrades.j2"),
    ("base", "dnf-automatic.conf.j2"),
    ("base", "journald.conf.j2"),
    ("base", "pokus-security-patch.service.j2"),
    ("base", "pokus-security-patch.timer.j2"),
    ("firewall", "docker-incus-compat.sh.j2"),
    ("firewall", "pokus-docker-incus-compat.service.j2"),
    ("firewall", "pokus-nftables.service.j2"),
    ("firewall", "pokus.nft.j2"),
    ("provider_baremetal", "pokus-sleep-inhibit.service.j2"),
    ("provider_wsl2", "wsl.conf.j2"),
    ("provider_wsl2", "wslconfig.recommended.j2"),
    ("ssh", "10-pokus.conf.j2"),
}


def test_every_template_is_tested() -> None:
    templates = {
        (role, p.name) for role in role_names() for p in (ROLES / role / "templates").glob("*.j2")
    }
    assert templates == TESTED_TEMPLATES


def ini(text: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, delimiters=("=",), strict=True)
    parser.optionxform = str  # type: ignore[assignment,method-assign]
    parser.read_string(text)
    return parser


# --- firewall --------------------------------------------------------------


def firewall_vars(**extra: Any) -> dict[str, Any]:
    return role_vars("firewall", **extra)


def nft_argv(path: Path) -> list[str] | None:
    nft = shutil.which("nft") or next(
        (p for p in ("/usr/sbin/nft", "/sbin/nft") if Path(p).exists()), None
    )
    unshare = shutil.which("unshare")
    if nft is None or unshare is None:
        return None
    # A new network namespace (user namespace too when unprivileged): the check
    # runs against an empty ruleset and cannot affect the host.
    return [unshare, "-n" if os.geteuid() == 0 else "-rn", nft, "-c", "-f", str(path)]


def nft_check(path: Path) -> subprocess.CompletedProcess[str]:
    probe = path.parent / "probe.nft"
    probe.write_text("table inet pokus_probe {\n}\n", encoding="utf-8")
    argv = nft_argv(probe)
    if argv is None:
        pytest.skip("nft or unshare is not installed")
    if subprocess.run(argv, capture_output=True, text=True, check=False).returncode != 0:
        pytest.skip("nft cannot check rulesets in a private network namespace here")
    argv = nft_argv(path)
    assert argv is not None
    return subprocess.run(argv, capture_output=True, text=True, check=False)


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"ssh_extra_allowed_interfaces": ["eth1", "enp3s0"], "grafana_tailscale_serve": True},
    ],
    ids=["default", "extra-interfaces-grafana"],
)
def test_nft_ruleset_is_valid(tmp_path: Path, extra: dict[str, Any]) -> None:
    text = render_template("firewall", "pokus.nft.j2", firewall_vars(**extra))
    path = tmp_path / "pokus.nft"
    path.write_text(text, encoding="utf-8")
    result = nft_check(path)
    assert result.returncode == 0, result.stderr


def test_nft_ruleset_policy() -> None:
    text = render_template("firewall", "pokus.nft.j2", firewall_vars())
    lines = [line.strip() for line in text.splitlines()]
    # Only Pokus's own table is (re)created.
    assert [line for line in lines if line.startswith(("table ", "delete table", "flush"))] == [
        "table inet pokus_filter",
        "delete table inet pokus_filter",
        "table inet pokus_filter {",
    ]
    assert "type filter hook input priority filter; policy drop;" in lines
    assert 'elements = { "r-*", "pokus-build0" }' in lines
    assert 'iifname "tailscale0" tcp dport { 22 } accept' in lines
    assert 'iifname "r-*" oifname != "r-*" drop' in lines
    assert 'oifname "r-*" iifname != "r-*" ct state new drop' in lines
    # SSH is never opened on all interfaces.
    assert not [line for line in lines if "dport 22" in line and "iifname" not in line]


def test_nft_ruleset_options() -> None:
    variables = firewall_vars(ssh_extra_allowed_interfaces=["eth1"], grafana_tailscale_serve=True)
    lines = [
        line.strip() for line in render_template("firewall", "pokus.nft.j2", variables).splitlines()
    ]
    assert 'iifname "tailscale0" tcp dport { 22, 443 } accept' in lines
    assert 'iifname { "eth1" } tcp dport 22 accept' in lines


def test_docker_incus_compat_script(tmp_path: Path) -> None:
    text = render_template("firewall", "docker-incus-compat.sh.j2", firewall_vars())
    path = tmp_path / "docker-incus-compat"
    path.write_text(text, encoding="utf-8")
    shellcheck = Path(sys.executable).parent / "shellcheck"
    result = subprocess.run(
        [str(shellcheck), "-s", "sh", str(path)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout
    assert subprocess.run(["sh", "-n", str(path)], check=False).returncode == 0
    # iptables writes interface wildcards with "+".
    for bridge in ("r-+", "pokus-build0"):
        assert f'-I DOCKER-USER -i "{bridge}" -j ACCEPT' in text
    assert "*" not in text.split("set -eu", 1)[1]


def test_firewall_units() -> None:
    compat = ini(
        render_template("firewall", "pokus-docker-incus-compat.service.j2", firewall_vars())
    )
    assert compat["Unit"]["After"] == "docker.service incus.service"
    assert compat["Service"]["ExecStart"] == "/usr/local/libexec/pokus/docker-incus-compat"
    nft = ini(render_template("firewall", "pokus-nftables.service.j2", firewall_vars()))
    assert nft["Service"]["ExecStart"] == "/usr/sbin/nft -f /etc/pokus/nftables/pokus.nft"
    assert nft["Service"]["ExecStop"] == "/usr/sbin/nft delete table inet pokus_filter"


# --- ssh, sudoers ------------------------------------------------------------


def test_sshd_drop_in() -> None:
    text = render_template("ssh", "10-pokus.conf.j2", role_vars("ssh"))
    directives = dict(
        line.split(" ", 1) for line in text.splitlines() if line and not line.startswith("#")
    )
    assert directives == {
        "PasswordAuthentication": "no",
        "KbdInteractiveAuthentication": "no",
        "PermitRootLogin": "no",
        "PubkeyAuthentication": "yes",
        "AllowGroups": "pokus",
        "X11Forwarding": "no",
        "AllowTcpForwarding": "local",
        "ClientAliveInterval": "60",
    }


def test_sudoers_drop_in_is_valid(tmp_path: Path) -> None:
    tasks = load_yaml(ROLES / "users_groups" / "tasks" / "main.yml")
    task = next(
        t
        for t in tasks
        if t.get("ansible.builtin.copy", {}).get("dest") == "/etc/sudoers.d/90-pokus-admin"
    )
    module = task["ansible.builtin.copy"]
    assert module["validate"] == "visudo -cf %s"
    assert module["mode"] == "0440"
    path = tmp_path / "90-pokus-admin"
    path.write_text(module["content"], encoding="utf-8")
    visudo = shutil.which("visudo") or "/usr/sbin/visudo"
    if not Path(visudo).exists():
        pytest.skip("visudo is not installed")
    result = subprocess.run([visudo, "-cf", str(path)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


# --- providers -----------------------------------------------------------------


def test_wsl_conf() -> None:
    conf = ini(
        render_template(
            "provider_wsl2", "wsl.conf.j2", role_vars("provider_wsl2", machine_name="lab-01")
        )
    )
    assert {section: dict(conf[section]) for section in conf.sections()} == {
        "boot": {"systemd": "true"},
        "automount": {"enabled": "false", "mountFsTab": "false"},
        "interop": {"enabled": "false", "appendWindowsPath": "false"},
        "network": {"hostname": "lab-01", "generateHosts": "true", "generateResolvConf": "true"},
    }


@pytest.mark.parametrize(
    ("expected", "memory", "processors"),
    [(None, "16GB", "8"), ({"memory_gib": 48, "cpu_threads": 20}, "48GB", "20")],
)
def test_wslconfig_recommendation(
    expected: dict[str, int] | None, memory: str, processors: str
) -> None:
    variables = role_vars("provider_wsl2", machine_name="lab-01")
    if expected is not None:
        variables["expected"] = expected
    conf = ini(render_template("provider_wsl2", "wslconfig.recommended.j2", variables))
    assert dict(conf["wsl2"]) == {
        "memory": memory,
        "processors": processors,
        "swap": "8GB",
        "nestedVirtualization": "true",
        "localhostForwarding": "false",
    }


def test_sleep_inhibitor_unit() -> None:
    unit = ini(
        render_template(
            "provider_baremetal", "pokus-sleep-inhibit.service.j2", role_vars("provider_baremetal")
        )
    )
    assert unit["Unit"]["PartOf"] == "pokus-lab.target"
    assert unit["Install"]["WantedBy"] == "pokus-lab.target"
    assert "systemd-inhibit --what=sleep:idle --mode=block" in unit["Service"]["ExecStart"]


# --- base ------------------------------------------------------------------------


def base_vars(family: str, **facts: Any) -> dict[str, Any]:
    defaults = {
        "pokus_provider": "baremetal",
        "pokus_release_model": "fixed",
        "allow_kernel_updates": False,
    }
    return role_vars("base", family, **{**defaults, **facts})


def blacklist(text: str) -> list[str]:
    block = text.split("Unattended-Upgrade::Package-Blacklist {", 1)[1].split("};", 1)[0]
    return [line.strip().strip('";') for line in block.splitlines() if line.strip()]


@pytest.mark.parametrize(
    ("group", "origin"),
    [
        ("ubuntu", '"origin=Ubuntu,archive=${distro_codename}-security,label=Ubuntu";'),
        ("debian", '"origin=Debian,codename=${distro_codename}-security,label=Debian-Security";'),
    ],
)
def test_unattended_upgrades_security_only(group: str, origin: str) -> None:
    text = render_template(
        "base", "52pokus-unattended-upgrades.j2", base_vars("debian", pokus_source_group=group)
    )
    origins = text.split("Unattended-Upgrade::Origins-Pattern {", 1)[1].split("};", 1)[0]
    assert [line.strip() for line in origins.splitlines() if line.strip()] == [origin]
    assert 'Unattended-Upgrade::Automatic-Reboot "false";' in text
    held = blacklist(text)
    for pattern in ("docker-ce", "containerd.io", "nvidia-", "incus", "tailscale", "linux-image-"):
        assert pattern in held


@pytest.mark.parametrize(
    ("facts", "kernel_held"),
    [
        ({}, True),
        ({"allow_kernel_updates": True}, False),
        ({"pokus_provider": "wsl2"}, False),
    ],
)
def test_kernel_hold(facts: dict[str, Any], kernel_held: bool) -> None:
    variables = base_vars("debian", pokus_source_group="ubuntu", **facts)
    debian = render_template("base", "52pokus-unattended-upgrades.j2", variables)
    assert ("linux-image-" in blacklist(debian)) is kernel_held
    redhat = ini(render_template("base", "dnf-automatic.conf.j2", base_vars("redhat", **facts)))
    assert (redhat["base"].get("exclude") == "kernel*") is kernel_held


def test_dnf_automatic() -> None:
    conf = ini(render_template("base", "dnf-automatic.conf.j2", base_vars("redhat")))
    assert dict(conf["commands"]) == {
        "upgrade_type": "security",
        "download_updates": "yes",
        "apply_updates": "yes",
        "reboot": "never",
    }


def test_journald_drop_in() -> None:
    conf = ini(render_template("base", "journald.conf.j2", base_vars("debian")))
    assert dict(conf["Journal"]) == {
        "Storage": "persistent",
        "SystemMaxUse": "2G",
        "SystemKeepFree": "4G",
    }


def test_security_patch_units() -> None:
    service = ini(render_template("base", "pokus-security-patch.service.j2", base_vars("suse")))
    assert (
        service["Service"]["ExecStart"]
        == "/usr/bin/zypper --non-interactive patch --category security"
    )
    timer = ini(render_template("base", "pokus-security-patch.timer.j2", base_vars("suse")))
    assert timer["Timer"]["OnCalendar"] == "daily"
    assert timer["Install"]["WantedBy"] == "timers.target"


def test_auto_updates_only_on_fixed_releases() -> None:
    assert evaluate("base_auto_updates_effective | bool", base_vars("debian")) is True
    assert (
        evaluate(
            "base_auto_updates_effective | bool", base_vars("redhat", pokus_release_model="fast")
        )
        is True
    )
    rolling = base_vars("archlinux", pokus_release_model="rolling")
    assert evaluate("base_auto_updates_effective | bool", rolling) is False
    off = base_vars("debian", base_auto_updates=False)
    assert evaluate("base_auto_updates_effective | bool", off) is False


# --- expressions in task files -------------------------------------------------


def test_apt_pin_block_expression() -> None:
    task = load_yaml(ROLES / "pkg" / "tasks" / "hold_apt_preferences.yml")[0]
    expression = task["ansible.builtin.blockinfile"]["block"].strip("{} ")
    variables = {
        "pkg_versions": {"docker-ce": "5:29.0.0-1", "docker-ce-cli": "5:29.0.0-1"},
        "pkg_hold_packages": ["docker-ce"],
    }
    assert (
        evaluate(expression, variables)
        == "Package: docker-ce\nPin: version 5:29.0.0-1\nPin-Priority: 1001"
    )


def test_tailnet_ports() -> None:
    assert evaluate("firewall_tailnet_tcp_ports", firewall_vars()) == [22]
    assert evaluate("firewall_tailnet_tcp_ports", firewall_vars(grafana_tailscale_serve=True)) == [
        22,
        443,
    ]
