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
import json
import os
import shutil
import subprocess
import sys
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
    role_names,
    role_vars,
)

TESTED_TEMPLATES = {
    ("base", "52pinka-unattended-upgrades.j2"),
    ("base", "dnf-automatic.conf.j2"),
    ("base", "journald.conf.j2"),
    ("base", "pinka-security-patch.service.j2"),
    ("base", "pinka-security-patch.timer.j2"),
    ("base", "pinka-services.slice.j2"),
    ("firewall", "docker-incus-compat.sh.j2"),
    ("firewall", "pinka-docker-incus-compat.service.j2"),
    ("firewall", "pinka-nftables.service.j2"),
    ("firewall", "pinka.nft.j2"),
    ("firewall", "pinka-telemetry-guard.nft.j2"),
    ("firewall", "pinka-telemetry-guard.service.j2"),
    ("firewall", "pinka-inference-guard.service.j2"),
    ("firewall", "pinka-split-guard.service.j2"),
    ("provider_baremetal", "pinka-sleep-inhibit.service.j2"),
    ("provider_wsl2", "wsl.conf.j2"),
    ("provider_wsl2", "wslconfig.recommended.j2"),
    ("ssh", "10-pinka.conf.j2"),
    # Tested in test_incus_role.py:
    ("docker", "daemon.json.j2"),
    ("nvidia_container_toolkit", "nvidia-cdi-refresh-pinka.conf.j2"),
    ("incus", "pinka-incus-events.service.j2"),
    # Tested in test_core.py:
    ("compose", "env.j2"),
    ("compose", "pinka-compose.service.j2"),
    ("postgres", "pgpass.j2"),
    ("postgres", "pinka-postgres.tmpfiles.conf.j2"),
    # Tested in test_observability.py:
    ("observability", "pinka-vm-otlp-address.service.j2"),
    ("observability", "pinka-otlp-agent.socket.j2"),
    ("observability", "pinka-otlp-agent.service.j2"),
    ("observability", "pinka-query.tmpfiles.conf.j2"),
    # Tested in test_gpu_arbiter.py:
    ("gpu_arbiter", "arbiter.json.j2"),
    ("gpu_arbiter", "pinka-gpu-arbiter.socket.j2"),
    ("gpu_arbiter", "pinka-gpu-arbiter@.service.j2"),
    ("gpu_arbiter", "pinka-gpu-arbiter.service.j2"),
    ("gpu_arbiter", "pinka-gpu-job@.service.j2"),
    # Tested in test_lab_session.py:
    ("lab_session", "pinka-lab.target.j2"),
    ("lab_session", "pinka-health-gate.service.j2"),
    ("lab_session", "pinka-session-start.service.j2"),
    # Tested in test_backup.py:
    ("backup", "backup-job.service.j2"),
    ("backup", "backup-job.timer.j2"),
    # Tested in test_run_network.py:
    ("incus", "pinka-run-network.socket.j2"),
    ("incus", "pinka-run-network@.service.j2"),
    # Tested in test_inference_control.py:
    ("inference_control", "pinka-inference-control.service.j2"),
    ("inference_control", "pinka-inference-forwarder.service.j2"),
    # Tested in test_docs_window.py:
    ("docs", "pinka-docs.service.j2"),
    ("lab_session", "pinka-window-expiry.service.j2"),
    ("lab_session", "pinka-window-expiry.timer.j2"),
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
    probe.write_text("table inet pinka_probe {\n}\n", encoding="utf-8")
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
        {"pinka_features": {"vm_targets": True}},
    ],
    ids=["default", "extra-interfaces-grafana", "vm-targets"],
)
def test_nft_ruleset_is_valid(tmp_path: Path, extra: dict[str, Any]) -> None:
    text = render_template("firewall", "pinka.nft.j2", firewall_vars(**extra))
    path = tmp_path / "pinka.nft"
    path.write_text(text, encoding="utf-8")
    result = nft_check(path)
    assert result.returncode == 0, result.stderr


def test_nft_ruleset_policy() -> None:
    text = render_template("firewall", "pinka.nft.j2", firewall_vars())
    lines = [line.strip() for line in text.splitlines()]
    # Only pinka's own table is (re)created.
    assert [line for line in lines if line.startswith(("table ", "delete table", "flush"))] == [
        "table inet pinka_filter",
        "delete table inet pinka_filter",
        "table inet pinka_filter {",
    ]
    assert "type filter hook input priority filter; policy drop;" in lines
    assert 'elements = { "r-*", "pinka-build0", "incusbr-*" }' in lines
    assert 'iifname "tailscale0" tcp dport { 22 } accept' in lines
    assert 'iifname "r-*" oifname != "r-*" drop' in lines
    assert 'oifname "r-*" iifname != "r-*" ct state new drop' in lines
    # SSH is never opened on all interfaces.
    assert not [line for line in lines if "dport 22" in line and "iifname" not in line]


def test_nft_vm_listener_rule() -> None:
    def rules(**extra: Any) -> list[str]:
        text = render_template("firewall", "pinka.nft.j2", firewall_vars(**extra))
        return [line.strip() for line in text.splitlines() if "198.18.0.1" in line]

    assert rules() == []
    assert rules(pinka_features={"vm_targets": True}) == [
        'iifname "r-*" ip daddr 198.18.0.1 tcp dport { 4317, 4318 } accept'
    ]


def test_nft_ruleset_options() -> None:
    variables = firewall_vars(ssh_extra_allowed_interfaces=["eth1"], grafana_tailscale_serve=True)
    lines = [
        line.strip() for line in render_template("firewall", "pinka.nft.j2", variables).splitlines()
    ]
    assert 'iifname "tailscale0" tcp dport { 22, 443 } accept' in lines
    assert 'iifname { "eth1" } tcp dport 22 accept' in lines


# Sample UIDs as getent finds them: the stores' clients (root, the collector,
# Prometheus, Loki, Tempo, the query proxy) and the query port's (root, Grafana).
TELEMETRY_CLIENT_UIDS = [0, 991, 992, 993, 994, 996]
GRAFANA_UID = 995
QUERY_PORT_CLIENT_UIDS = [0, GRAFANA_UID]


def guard_vars() -> dict[str, Any]:
    return firewall_vars(
        firewall_telemetry_client_uids=TELEMETRY_CLIENT_UIDS,
        firewall_query_port_client_uids=QUERY_PORT_CLIENT_UIDS,
    )


def test_telemetry_guard_is_valid(tmp_path: Path) -> None:
    text = render_template("firewall", "pinka-telemetry-guard.nft.j2", guard_vars())
    path = tmp_path / "telemetry-guard.nft"
    path.write_text(text, encoding="utf-8")
    result = nft_check(path)
    assert result.returncode == 0, result.stderr


def test_telemetry_guard_allows_only_infras_clients() -> None:
    lines = [
        line.strip()
        for line in render_template(
            "firewall", "pinka-telemetry-guard.nft.j2", guard_vars()
        ).splitlines()
    ]
    # Only its own table, never one of Docker, Incus, firewalld or ufw.
    assert [line for line in lines if line.startswith(("table ", "delete table", "flush"))] == [
        "table inet pinka_telemetry_guard",
        "delete table inet pinka_telemetry_guard",
        "table inet pinka_telemetry_guard {",
    ]
    assert "type filter hook output priority filter; policy accept;" in lines
    assert "elements = { 0, 991, 992, 993, 994, 996 }" in lines
    assert "elements = { 0, 995 }" in lines
    # Sets of UIDs only: a group never admits anything.
    assert [line for line in lines if line.startswith("type ") and "hook" not in line] == [
        "type uid",
        "type uid",
    ]
    assert not any("skgid" in line for line in lines)
    # Prometheus, Loki, Tempo, their gRPC and OTLP ports and the collector's
    # metrics exporter; the query proxy's loopback port has its own rule.
    ports = "18701, 18702, 18703, 18720, 18727, 18728, 18729"
    assert (
        f'oifname "lo" tcp dport {{ {ports} }} meta skuid != @telemetry_clients '
        "reject with tcp reset"
    ) in lines
    assert (
        'oifname "lo" tcp dport 18733 meta skuid != @query_port_clients reject with tcp reset'
    ) in lines
    # The OTLP receivers stay open: platform services send telemetry there.
    assert "4317" not in ports and "4318" not in ports


def test_the_telemetry_clients_are_infras_own_accounts() -> None:
    # ADR-0016 as decided with the "commit" for 3d.4 and 3d.4b: an explicit
    # allowlist of infra's accounts, without Grafana, whose data sources go
    # through the query proxy; no image's default user (nobody, 10001, 472),
    # which other host processes may share.
    variables = firewall_vars()
    clients = [c["account"] for c in evaluate("pinka_telemetry_clients", variables)]
    query = [c["account"] for c in evaluate("pinka_query_port_clients", variables)]
    infra = {a["name"] for a in evaluate("infra_accounts", variables)}
    assert clients[0] == "root" and query[0] == "root"
    assert set(clients[1:]) | set(query[1:]) <= infra
    assert set(clients[1:]) == {
        "pinka-otelcol",
        "pinka-prometheus",
        "pinka-loki",
        "pinka-tempo",
        "pinka-query-proxy",
    }
    assert query[1:] == ["pinka-grafana"]
    tasks = (ROLES / "firewall" / "tasks" / "telemetry_guard.yml").read_text(encoding="utf-8")
    assert "pinka_telemetry_clients" in tasks and "pinka_query_port_clients" in tasks
    assert "getent" in tasks


# Run in a private network namespace as root: load the guard, listen on the
# guarded ports and an open port, connect as each UID and GID, print what
# connected.
GUARD_PROBE = """
import fcntl, json, os, socket, struct, subprocess, sys
nft, ruleset, spec = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
flags = struct.unpack("16sh22x", fcntl.ioctl(s, 0x8913, struct.pack("16sh22x", b"lo", 0)))[1]
fcntl.ioctl(s, 0x8914, struct.pack("16sh22x", b"lo", flags | 1))
subprocess.run([nft, "-f", ruleset], check=True)
listeners = []
for port in spec["ports"]:
    sock = socket.socket()
    sock.bind(("127.0.0.1", port))
    sock.listen(64)
    listeners.append(sock)
results = {}
for name, (uid, gid) in spec["accounts"].items():
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(r)
        os.setgroups([gid])
        os.setgid(gid)
        os.setuid(uid)
        out = {}
        for port in spec["ports"]:
            c = socket.socket()
            c.settimeout(3)
            try:
                c.connect(("127.0.0.1", port))
                out[str(port)] = True
            except OSError:
                out[str(port)] = False
            c.close()
        os.write(w, json.dumps(out).encode())
        os._exit(0)
    os.close(w)
    data = os.read(r, 65536)
    os.waitpid(pid, 0)
    results[name] = json.loads(data)
print(json.dumps(results))
"""


def privileged() -> list[str]:
    """How to run as root: directly, or with sudo -n in CI; skip otherwise."""
    if os.geteuid() == 0:
        return []
    sudo = shutil.which("sudo")
    if sudo and subprocess.run([sudo, "-n", "true"], check=False).returncode == 0:
        return [sudo, "-n"]
    if os.environ.get("CI") == "true":
        pytest.fail("the telemetry guard test needs root or sudo -n in CI")
    pytest.skip("needs root or sudo -n; allowed to skip only outside CI")


def test_telemetry_guard_per_account_class(tmp_path: Path) -> None:
    # One connection per account class against the kernel's rules (ADR-0016):
    # only root and infra's store clients reach the stores, only root and
    # Grafana the query proxy's port; researchers, the platform's services,
    # the agent, infra's other accounts, the images' default users and an
    # account in group 0 are reset. The collector's OTLP port stays open.
    argv = nft_argv(tmp_path / "x")
    if argv is None:
        if os.environ.get("CI") == "true":
            pytest.fail("nft and unshare are required in CI")
        pytest.skip("nft or unshare is not installed")
    nft, unshare = argv[2], argv[0]
    prefix = privileged()
    ruleset = tmp_path / "guard.nft"
    ruleset.write_text(
        render_template("firewall", "pinka-telemetry-guard.nft.j2", guard_vars()), encoding="utf-8"
    )
    probe = tmp_path / "probe.py"
    probe.write_text(GUARD_PROBE, encoding="utf-8")
    variables = guard_vars()
    stores = [int(p) for p in evaluate("pinka_telemetry_guard_ports", variables)]
    query = int(evaluate("pinka_internal_ports.query_proxy", variables))
    otlp = int(evaluate("pinka_ports.otlp_http", variables))
    accounts = {
        "root (doctor, the health gate)": (0, 0),
        **{f"store client {uid}": (uid, uid) for uid in TELEMETRY_CLIENT_UIDS[1:]},
        "Grafana": (GRAFANA_UID, 0),
        "another infra account": (990, 990),
        "researcher": (1000, 1000),
        "an account in group 0": (1234, 0),
        "platform service": (60578, 60578),
        "untrusted agent": (60580, 60580),
        "nobody (an image's default)": (65534, 65534),
        "Loki's image default": (10001, 10001),
        "Grafana's image default": (472, 0),
    }
    spec = {"ports": [*stores, query, otlp], "accounts": accounts}
    done = subprocess.run(
        [
            *prefix,
            unshare,
            "-n",
            sys.executable,
            "-I",
            str(probe),
            nft,
            str(ruleset),
            json.dumps(spec),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0 and "Operation not permitted" in done.stderr and not prefix:
        pytest.skip("this container cannot load nftables rules in a namespace")
    assert done.returncode == 0, done.stderr
    results = json.loads(done.stdout)
    store_clients = {"root (doctor, the health gate)"} | {
        f"store client {u}" for u in TELEMETRY_CLIENT_UIDS[1:]
    }
    query_clients = {"root (doctor, the health gate)", "Grafana"}
    for name, reached in results.items():
        assert reached[str(otlp)] is True, f"{name}: the OTLP receiver must stay open"
        expected = name in store_clients
        assert {p: reached[str(p)] for p in stores} == dict.fromkeys(stores, expected), name
        assert reached[str(query)] is (name in query_clients), name


# --- the inference guard (§7.11, ADR-0016) ------------------------------------------

INFERENCE_UIDS = {
    "root": 0,
    "pinka-otelcol": 991,
    "pinka-prometheus": 992,
    "pinka-inference": 997,
    "demo-ctl": 60578,
    "demo-brk": 60579,
    "demo-sbx": 60580,
    "demo-agent": 60581,
}
RENDERED = {"controller": "demo-ctl", "inference_clients": ["demo-brk", "demo-ctl"]}


def inference_spec(node_role: str) -> dict[str, Any]:
    variables = firewall_vars(node_role=node_role)
    tasks = load_yaml(ROLES / "firewall" / "tasks" / "inference_guard.yml")
    describe = tasks[0]["ansible.builtin.set_fact"]["firewall_inference_guard"]
    return {
        k: evaluate(str(v).strip("{} ").strip(), variables)
        if isinstance(v, str)
        else [int(evaluate(str(p).strip("{} ").strip(), variables)) for p in v]
        for k, v in describe.items()
    }


@pytest.mark.parametrize(
    ("node_role", "clients", "control"),
    [
        ("full", ["root", "pinka-prometheus", "pinka-otelcol"], []),
        ("lab", ["root"], [18710]),
        ("inference", ["root", "pinka-otelcol", "pinka-inference"], []),
    ],
)
def test_inference_guard_clients_per_node_role(
    node_role: str, clients: list[str], control: list[int]
) -> None:
    spec = inference_spec(node_role)
    assert spec == {"clients": clients, "ports": [18704], "control_ports": control}
    # Infra's own accounts only; the platform's come from rendered.json.
    infra = {a["name"] for a in evaluate("infra_accounts", firewall_vars())}
    assert set(clients) - {"root"} <= infra


def test_inference_guard_ruleset(tmp_path: Path) -> None:
    import pinka_inference_guard as guard  # noqa: PLC0415 - the role's own file

    text, missing = guard.build(inference_spec("lab"), RENDERED, INFERENCE_UIDS.get)
    lines = [line.strip() for line in text.splitlines()]
    assert missing == []
    assert [line for line in lines if line.startswith(("table ", "delete table"))] == [
        "table inet pinka_inference_guard",
        "delete table inet pinka_inference_guard",
        "table inet pinka_inference_guard {",
    ]
    assert "elements = { 0, 60578, 60579 }" in lines  # root, the broker, the controller
    assert "elements = { 0, 60578 }" in lines  # the control port: root and the controller
    assert not any("skgid" in line for line in lines)
    assert (
        'oifname "lo" tcp dport { 18704 } meta skuid != @inference_clients reject with tcp reset'
    ) in lines
    assert (
        'oifname "lo" tcp dport { 18710 } meta skuid != @inference_control_clients '
        "reject with tcp reset"
    ) in lines
    full, _ = guard.build(inference_spec("full"), None, INFERENCE_UIDS.get)
    assert "inference_control_clients" not in full
    assert "elements = { 0, 991, 992 }" in [line.strip() for line in full.splitlines()]
    _, missing = guard.build(inference_spec("full"), RENDERED, {"root": 0}.get)
    assert missing == ["pinka-prometheus", "pinka-otelcol", "demo-brk", "demo-ctl"]
    path = tmp_path / "inference-guard.nft"
    path.write_text(text, encoding="utf-8")
    result = nft_check(path)
    assert result.returncode == 0, result.stderr


def test_inference_guard_main_prints_the_ruleset(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import pinka_inference_guard as guard  # noqa: PLC0415 - the role's own file

    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"clients": ["root"], "ports": [18704], "control_ports": []}))
    argv = ["--spec", str(spec), "--rendered", str(tmp_path / "none.json"), "--print"]
    assert guard.main(argv) == 0
    assert "table inet pinka_inference_guard {" in capsys.readouterr().out
    assert guard.main(["--spec", str(tmp_path / "missing.json"), "--print"]) == 1


def test_inference_guard_per_account_class(tmp_path: Path) -> None:
    # One connection per account class against the kernel's rules (§7.11):
    # only the controller and the inference broker of the platform reach the
    # inference API, besides root and infra's scrapers; only root and the
    # controller reach the forwarder's control port of a lab node. A group,
    # GID 0 included, admits nothing.
    import pinka_inference_guard as guard  # noqa: PLC0415 - the role's own file

    argv = nft_argv(tmp_path / "x")
    if argv is None:
        if os.environ.get("CI") == "true":
            pytest.fail("nft and unshare are required in CI")
        pytest.skip("nft or unshare is not installed")
    nft, unshare = argv[2], argv[0]
    prefix = privileged()
    spec = {"clients": ["root", "pinka-prometheus"], "ports": [18704], "control_ports": [18710]}
    text, _ = guard.build(spec, RENDERED, INFERENCE_UIDS.get)
    ruleset = tmp_path / "guard.nft"
    ruleset.write_text(text, encoding="utf-8")
    probe = tmp_path / "probe.py"
    probe.write_text(GUARD_PROBE, encoding="utf-8")
    otlp = 4318
    accounts = {
        "root": (0, 0),
        "Prometheus": (992, 992),
        "controller": (60578, 60578),
        "inference broker": (60579, 60579),
        "sandbox operator": (60580, 60580),
        "untrusted agent": (60581, 60581),
        "researcher": (1000, 1000),
        "an account in group 0": (1234, 0),
        "the collector (not a client on this node)": (991, 991),
    }
    done = subprocess.run(
        [
            *prefix,
            unshare,
            "-n",
            sys.executable,
            "-I",
            str(probe),
            nft,
            str(ruleset),
            json.dumps({"ports": [18704, 18710, otlp], "accounts": accounts}),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0 and "Operation not permitted" in done.stderr and not prefix:
        pytest.skip("this container cannot load nftables rules in a namespace")
    assert done.returncode == 0, done.stderr
    results = json.loads(done.stdout)
    api = {"root", "Prometheus", "controller", "inference broker"}
    control = {"root", "controller"}
    for name, reached in results.items():
        assert reached[str(otlp)] is True, name
        assert reached["18704"] is (name in api), name
        assert reached["18710"] is (name in control), name


def test_inference_guard_unit() -> None:
    unit = ini(render_template("firewall", "pinka-inference-guard.service.j2", firewall_vars()))
    assert unit["Service"]["ExecStart"] == "/usr/local/libexec/pinka/inference-guard"
    assert unit["Service"]["ExecStop"] == "/usr/sbin/nft delete table inet pinka_inference_guard"
    assert unit["Unit"]["Before"] == "network-pre.target"
    switch = load_yaml(ROLES / "platform" / "tasks" / "switch.yml")
    reload = next(
        t for t in switch if t["name"] == "Load the inference guard with the platform's clients"
    )
    assert reload["ansible.builtin.systemd_service"]["state"] == "restarted"


def split_guard_spec(node_role: str) -> dict[str, Any]:
    variables = ansible_vars(
        GROUP_VARS,
        ROLES / "firewall" / "defaults" / "main.yml",
        ROLES / "firewall" / "vars" / "main.yml",
        node_role=node_role,
    )
    spec = evaluate("firewall_split_guard", variables)
    assert isinstance(spec, dict)
    return spec


@pytest.mark.parametrize(
    ("node_role", "ports"), [("inference", [18704, 18710]), ("lab", [4317]), ("full", [])]
)
def test_split_guard_ports_per_node_role(node_role: str, ports: list[int]) -> None:
    spec = split_guard_spec(node_role)
    assert spec == {
        "interface": "tailscale0",
        "ports": ports,
        "peer_record": "/etc/pinka/inference/peer.json",
    }
    main = load_yaml(ROLES / "firewall" / "tasks" / "main.yml")
    include = next(t for t in main if t.get("ansible.builtin.include_tasks") == "split_guard.yml")
    assert include["when"] == "node_role in ['lab', 'inference']"


def test_split_guard_ruleset(tmp_path: Path) -> None:
    import pinka_split_guard as guard  # noqa: PLC0415 - the role's own file

    spec = split_guard_spec("inference")
    paired = guard.build(spec, {"node": "pinka-lab", "address": "100.64.0.7"})
    lines = [line.strip() for line in paired.splitlines()]
    assert [line for line in lines if line.startswith(("table ", "delete table"))] == [
        "table inet pinka_split_guard",
        "delete table inet pinka_split_guard",
        "table inet pinka_split_guard {",
    ]
    assert "elements = { 100.64.0.7 }" in lines
    assert 'iifname "tailscale0" tcp dport @pairing accept' in lines
    assert 'iifname "tailscale0" tcp dport { 18704, 18710 } ip saddr @peer accept' in lines
    assert lines[-3] == 'iifname "tailscale0" tcp dport { 18704, 18710 } drop'
    # Before a pairing the set is empty: nobody reaches the ports.
    unpaired = guard.build(spec, None)
    assert "elements" not in unpaired
    # A record without an address (or with something else) is refused.
    for record in ({"node": "pinka-lab"}, {"address": "fd7a:115c:a1e0::1"}, {"address": "x"}):
        with pytest.raises(guard.GuardError, match="pair again"):
            guard.build(spec, record)
    with pytest.raises(guard.GuardError, match="interface and a list of ports"):
        guard.build({"interface": "tailscale0", "ports": ["18704"]}, None)
    for name, text in (("paired", paired), ("unpaired", unpaired)):
        path = tmp_path / f"{name}.nft"
        path.write_text(text, encoding="utf-8")
        result = nft_check(path)
        assert result.returncode == 0, result.stderr


def test_split_guard_fails_closed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import pinka_split_guard as guard  # noqa: PLC0415 - the role's own file

    record = tmp_path / "peer.json"
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps({"interface": "tailscale0", "ports": [18704], "peer_record": str(record)})
    )
    argv = ["--spec", str(spec), "--print"]
    assert guard.main(argv) == 0  # not paired: closed, and that is fine
    assert "elements" not in capsys.readouterr().out
    record.write_text(json.dumps({"node": "pinka-lab"}))
    assert guard.main(argv) == 1  # an old record: closed, and it says so
    captured = capsys.readouterr()
    assert "elements" not in captured.out
    assert "pair again" in captured.err
    record.write_text(json.dumps({"node": "pinka-lab", "address": "100.64.0.7"}))
    assert guard.main(argv) == 0
    assert "elements = { 100.64.0.7 }" in capsys.readouterr().out
    assert guard.main(["--spec", str(tmp_path / "missing.json"), "--print"]) == 1


SPLIT_PROBE = """
import fcntl, json, socket, struct, subprocess, sys, time
nft, unpaired, paired, spec = sys.argv[1], sys.argv[2], sys.argv[3], json.loads(sys.argv[4])
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
flags = struct.unpack("16sh22x", fcntl.ioctl(s, 0x8913, struct.pack("16sh22x", b"lo", 0)))[1]
fcntl.ioctl(s, 0x8914, struct.pack("16sh22x", b"lo", flags | 1))
listeners = []
for port in spec["ports"]:
    sock = socket.socket()
    sock.bind(("127.0.0.1", port))
    sock.listen(64)
    listeners.append(sock)

def reached():
    out = {}
    for source in spec["sources"]:
        for port in spec["ports"]:
            c = socket.socket()
            c.settimeout(0.5)
            c.bind((source, 0))
            try:
                c.connect(("127.0.0.1", port))
                out[f"{source}:{port}"] = True
            except OSError:
                out[f"{source}:{port}"] = False
            c.close()
    return out

def run(*args):
    subprocess.run([nft, *args], check=True)

window = ["inet", "pinka_split_guard", "pairing"]
control = spec["ports"][-1]
results = {}
run("-f", unpaired)
results["not paired"] = reached()
run("add", "element", *window, "{ %d timeout 60s }" % control)
results["pairing"] = reached()
run("delete", "element", *window, "{ %d }" % control)
run("-f", paired)
results["paired"] = reached()
run("add", "element", *window, "{ %d timeout 1s }" % control)
time.sleep(2)
results["window expired"] = reached()
print(json.dumps(results))
"""


def test_split_guard_admits_only_the_paired_peer(tmp_path: Path) -> None:
    # Interpretation 11.13 against the kernel's rules: before a pairing nobody
    # reaches the split-inference ports; while pinka pair show waits, the
    # pairing (control) port is open to every tailnet address and nothing
    # else is; once paired, only the peer's address gets in and an unpaired
    # tailnet address is refused on every port, also after a pairing window
    # expired on its own. The loopback stands in for tailscale0, 127.0.0.2
    # for the paired peer and 127.0.0.3 for another tailnet node.
    import pinka_split_guard as guard  # noqa: PLC0415 - the role's own file

    argv = nft_argv(tmp_path / "x")
    if argv is None:
        if os.environ.get("CI") == "true":
            pytest.fail("nft and unshare are required in CI")
        pytest.skip("nft or unshare is not installed")
    nft, unshare = argv[2], argv[0]
    prefix = privileged()
    api, control = 18704, 18710
    spec = {"interface": "lo", "ports": [api, control]}
    unpaired, paired = tmp_path / "unpaired.nft", tmp_path / "paired.nft"
    unpaired.write_text(guard.build(spec, None), encoding="utf-8")
    paired.write_text(guard.build(spec, {"address": "127.0.0.2"}), encoding="utf-8")
    probe = tmp_path / "probe.py"
    probe.write_text(SPLIT_PROBE, encoding="utf-8")
    sources = {"peer": "127.0.0.2", "other": "127.0.0.3"}
    done = subprocess.run(
        [
            *prefix,
            unshare,
            "-n",
            sys.executable,
            "-I",
            str(probe),
            nft,
            str(unpaired),
            str(paired),
            json.dumps({"ports": [api, control], "sources": list(sources.values())}),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0 and "Operation not permitted" in done.stderr and not prefix:
        pytest.skip("this container cannot load nftables rules in a namespace")
    assert done.returncode == 0, done.stderr
    results = json.loads(done.stdout)

    def gets_in(phase: str, who: str, port: int) -> bool:
        return bool(results[phase][f"{sources[who]}:{port}"])

    for who in sources:
        assert not gets_in("not paired", who, api), who
        assert not gets_in("not paired", who, control), who
        assert gets_in("pairing", who, control), who
        assert not gets_in("pairing", who, api), who
    assert gets_in("paired", "peer", api) and gets_in("paired", "peer", control)
    # The unpaired tailnet address is refused after the pairing.
    assert not gets_in("paired", "other", api)
    assert not gets_in("paired", "other", control)
    assert not gets_in("window expired", "other", control)
    assert gets_in("window expired", "peer", control)


def test_split_guard_unit() -> None:
    unit = ini(render_template("firewall", "pinka-split-guard.service.j2", firewall_vars()))
    assert unit["Service"]["ExecStart"] == "/usr/local/libexec/pinka/split-guard"
    assert unit["Service"]["ExecReload"] == "/usr/local/libexec/pinka/split-guard"
    assert unit["Service"]["ExecStop"] == "/usr/sbin/nft delete table inet pinka_split_guard"
    # Loaded before the network: the ports are never open to the tailnet first.
    assert unit["Unit"]["Before"] == "network-pre.target"


def test_telemetry_guard_unit() -> None:
    unit = ini(render_template("firewall", "pinka-telemetry-guard.service.j2", firewall_vars()))
    assert (
        unit["Service"]["ExecStart"] == "/usr/sbin/nft -f /etc/pinka/nftables/telemetry-guard.nft"
    )
    assert unit["Service"]["ExecStop"] == "/usr/sbin/nft delete table inet pinka_telemetry_guard"
    assert unit["Unit"]["Before"] == "network-pre.target"


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
    for bridge in ("r-+", "pinka-build0", "incusbr-+"):
        assert f'-I DOCKER-USER -i "{bridge}" -j ACCEPT' in text
    assert "*" not in text.split("set -eu", 1)[1]


def test_firewall_units() -> None:
    compat = ini(
        render_template("firewall", "pinka-docker-incus-compat.service.j2", firewall_vars())
    )
    assert compat["Unit"]["After"] == "docker.service incus.service"
    assert compat["Service"]["ExecStart"] == "/usr/local/libexec/pinka/docker-incus-compat"
    nft = ini(render_template("firewall", "pinka-nftables.service.j2", firewall_vars()))
    assert nft["Service"]["ExecStart"] == "/usr/sbin/nft -f /etc/pinka/nftables/pinka.nft"
    assert nft["Service"]["ExecStop"] == "/usr/sbin/nft delete table inet pinka_filter"


# --- ssh, sudoers ------------------------------------------------------------


def test_sshd_drop_in() -> None:
    text = render_template("ssh", "10-pinka.conf.j2", role_vars("ssh"))
    directives = dict(
        line.split(" ", 1) for line in text.splitlines() if line and not line.startswith("#")
    )
    assert directives == {
        "PasswordAuthentication": "no",
        "KbdInteractiveAuthentication": "no",
        "PermitRootLogin": "no",
        "PubkeyAuthentication": "yes",
        "AllowGroups": "pinka",
        "X11Forwarding": "no",
        "AllowTcpForwarding": "local",
        "ClientAliveInterval": "60",
    }


def test_sudoers_drop_in_is_valid(tmp_path: Path) -> None:
    tasks = load_yaml(ROLES / "users_groups" / "tasks" / "main.yml")
    task = next(
        t
        for t in tasks
        if t.get("ansible.builtin.copy", {}).get("dest") == "/etc/sudoers.d/90-pinka-admin"
    )
    module = task["ansible.builtin.copy"]
    assert module["validate"] == "visudo -cf %s"
    assert module["mode"] == "0440"
    path = tmp_path / "90-pinka-admin"
    path.write_text(module["content"], encoding="utf-8")
    visudo = shutil.which("visudo") or "/usr/sbin/visudo"
    if not Path(visudo).exists():
        pytest.skip("visudo is not installed")
    result = subprocess.run([visudo, "-cf", str(path)], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("name", "clash"),
    [
        ("researcher1", False),
        ("pinka-build", True),
        ("pinka-admin", True),
        ("demo-ctl", True),
        ("demo-db", True),
        ("pinka-restricted", True),
    ],
)
def test_researchers_never_take_an_account_of_the_lab(name: str, clash: bool) -> None:
    platform = {
        "accounts": [{"name": "demo-ctl"}, {"name": "demo-agent"}],
        "groups": ["demo-db", "demo-ctl-api"],
        "infra_groups": ["pinka-restricted"],
    }
    variables = ansible_vars(
        GROUP_VARS,
        ROLES / "users_groups" / "vars" / "main.yml",
        researchers=[{"name": name}],
        active_platform=platform,
    )
    assert evaluate("users_groups_clashes", variables) == ([name] if clash else [])
    tasks = load_yaml(ROLES / "users_groups" / "tasks" / "main.yml")
    names = [t["name"] for t in tasks]
    assert names.index("Refuse researchers named like an account or group of the lab") < (
        names.index("Create the researchers")
    )


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
            "provider_baremetal", "pinka-sleep-inhibit.service.j2", role_vars("provider_baremetal")
        )
    )
    assert unit["Unit"]["PartOf"] == "pinka-lab.target"
    assert unit["Install"]["WantedBy"] == "pinka-lab.target"
    assert "systemd-inhibit --what=sleep:idle --mode=block" in unit["Service"]["ExecStart"]


# --- base ------------------------------------------------------------------------


def base_vars(family: str, **facts: Any) -> dict[str, Any]:
    defaults = {
        "pinka_provider": "baremetal",
        "pinka_release_model": "fixed",
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
        "base", "52pinka-unattended-upgrades.j2", base_vars("debian", pinka_source_group=group)
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
        ({"pinka_provider": "wsl2"}, False),
    ],
)
def test_kernel_hold(facts: dict[str, Any], kernel_held: bool) -> None:
    variables = base_vars("debian", pinka_source_group="ubuntu", **facts)
    debian = render_template("base", "52pinka-unattended-upgrades.j2", variables)
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
    service = ini(render_template("base", "pinka-security-patch.service.j2", base_vars("suse")))
    assert (
        service["Service"]["ExecStart"]
        == "/usr/bin/zypper --non-interactive patch --category security"
    )
    timer = ini(render_template("base", "pinka-security-patch.timer.j2", base_vars("suse")))
    assert timer["Timer"]["OnCalendar"] == "daily"
    assert timer["Install"]["WantedBy"] == "timers.target"


def test_auto_updates_only_on_fixed_releases() -> None:
    assert evaluate("base_auto_updates_effective | bool", base_vars("debian")) is True
    assert (
        evaluate(
            "base_auto_updates_effective | bool", base_vars("redhat", pinka_release_model="fast")
        )
        is True
    )
    rolling = base_vars("archlinux", pinka_release_model="rolling")
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
    inference = firewall_vars(node_role="inference")
    assert evaluate("firewall_tailnet_tcp_ports", inference) == [22, 18704, 18710]
    assert evaluate("firewall_tailnet_tcp_ports", firewall_vars(node_role="lab")) == [22]
    paired = firewall_vars(node_role="lab", inference_remote_node="pinka-gpu")
    assert evaluate("firewall_tailnet_tcp_ports", paired) == [22, 4317]

    features = {"vm_targets": True}
    assert evaluate("firewall_vm_otlp_ports", firewall_vars(pinka_features=features)) == [
        4317,
        4318,
    ]
    no_targets = firewall_vars(pinka_features=features, node_role="inference")
    assert evaluate("firewall_vm_otlp_ports", no_targets) == []


@pytest.mark.parametrize(("cpuset", "allowed"), [("0-3,8", "0,1,2,3,8"), ("", None)])
def test_services_slice(cpuset: str, allowed: str | None) -> None:
    text = render_template(
        "base", "pinka-services.slice.j2", role_vars("base", cpuset_services=cpuset)
    )
    unit = ini(text)
    assert unit["Unit"]["Before"] == "slices.target"
    assert unit["Slice"].get("AllowedCPUs") == allowed


def test_lab_units_run_in_the_services_slice() -> None:
    for path in sorted(ROLES.glob("*/templates/*.service.j2")):
        text = path.read_text(encoding="utf-8")
        lab_unit = "PartOf=pinka-lab.target" in text or path.name in {
            "backup-job.service.j2",
            "pinka-incus-events.service.j2",
            "pinka-gpu-arbiter@.service.j2",
            "pinka-gpu-job@.service.j2",
            "pinka-run-network@.service.j2",
            "pinka-window-expiry.service.j2",
        }
        assert ("Slice={{ pinka_services_slice }}" in text) == lab_unit, path
