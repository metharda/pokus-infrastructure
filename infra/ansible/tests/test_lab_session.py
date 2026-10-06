"""Tests for the lab_session role: health gate, session record, units and checks."""

from __future__ import annotations

import base64
import json
import socket
import struct
import subprocess
import threading
import time
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pokus_health_gate as gate
import pokus_session_start as session
import pytest

from ansible_testlib import ROLES, evaluate, load_yaml, render_template, role_vars

TASKS = ROLES / "lab_session" / "tasks" / "main.yml"


def done(
    argv: Sequence[str], rc: int = 0, out: str = "", err: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(list(argv), rc, out, err)


class FakeRunner:
    """Answers commands by their first two words."""

    def __init__(self, answers: dict[str, tuple[int, str]]) -> None:
        self.answers = answers
        self.calls: list[list[str]] = []

    def __call__(self, argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(argv))
        rc, out = self.answers.get(" ".join(argv[:2]), (127, ""))
        return done(argv, rc, out, "" if rc == 0 else "failed")


HEALTHY = {
    "timedatectl show": (0, "yes\n"),
    "docker inspect": (0, "healthy\n"),
    "incus query": (0, "{}"),
    "docker run": (0, "GPU 0: Example GPU (UUID: GPU-00000000)\n"),
}
CHECKS: dict[str, Any] = {
    "time": {"wsl": False, "ntp_reference": "", "max_offset": 1.0},
    "postgres": {"container": "pokus-postgres"},
    "collector": {"url": "http://127.0.0.1:18730/"},
    "incus": {},
    "gpu": {
        "image": "docker.io/nvidia/cuda@sha256:" + "0" * 64,
        "device_args": ["--device", "nvidia.com/gpu=all"],
    },
}


def no_sntp(server: str, timeout: float) -> float:
    raise AssertionError("no NTP query expected")


# --- checks ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "config", "offset", "ok", "detail"),
    [
        ((0, "yes\n"), {"wsl": False}, None, True, "NTP-synchronized"),
        ((0, "no\n"), {"wsl": False}, None, False, "not NTP-synchronized"),
        ((1, ""), {"wsl": False}, None, False, "not NTP-synchronized"),
        (
            (0, "yes\n"),
            {"wsl": True, "ntp_reference": "ntp.example.com", "max_offset": 1.0},
            0.2,
            True,
            "+0.200",
        ),
        (
            (0, "yes\n"),
            {"wsl": True, "ntp_reference": "ntp.example.com", "max_offset": 1.0},
            -3.0,
            False,
            "-3.000",
        ),
        ((0, "yes\n"), {"wsl": True, "ntp_reference": ""}, None, True, "NTP-synchronized"),
    ],
)
def test_time_check(
    answer: tuple[int, str], config: dict[str, Any], offset: float | None, ok: bool, detail: str
) -> None:
    runner = FakeRunner({"timedatectl show": answer})

    def sntp(server: str, timeout: float) -> float:
        assert offset is not None
        assert server == "ntp.example.com"
        return offset

    result = gate.check_time(config, runner, sntp)
    assert (result.ok, detail in result.detail) == (ok, True)
    assert runner.calls[0] == ["timedatectl", "show", "--property=NTPSynchronized", "--value"]


def test_unreachable_reference_skips_the_comparison() -> None:
    def sntp(server: str, timeout: float) -> float:
        raise OSError("timed out")

    config = {"wsl": True, "ntp_reference": "ntp.example.com"}
    result = gate.check_time(config, FakeRunner(HEALTHY), sntp)
    assert result.ok
    assert "not reachable" in result.detail


@pytest.mark.parametrize(
    ("answer", "ok"), [((0, "healthy\n"), True), ((0, "starting\n"), False), ((1, ""), False)]
)
def test_postgres_check(answer: tuple[int, str], ok: bool) -> None:
    runner = FakeRunner({"docker inspect": answer})
    assert gate.check_postgres({"container": "pokus-postgres"}, runner).ok is ok
    assert runner.calls[0][-1] == "pokus-postgres"


@pytest.mark.parametrize(("status", "ok"), [(200, True), (503, False), (0, False)])
def test_collector_check(status: int, ok: bool) -> None:
    assert gate.check_collector({"url": "http://127.0.0.1:1/"}, lambda url: status).ok is ok


def test_gpu_check_uses_the_pinned_image_without_pulling() -> None:
    runner = FakeRunner(HEALTHY)
    result = gate.check_gpu(CHECKS["gpu"], runner)
    assert result.ok
    assert runner.calls[0][:5] == ["docker", "run", "--rm", "--pull", "never"]
    assert runner.calls[0][-3:] == [CHECKS["gpu"]["image"], "nvidia-smi", "--list-gpus"]
    runner = FakeRunner({**HEALTHY, "docker run": (0, "No devices were found\n")})
    assert not gate.check_gpu(CHECKS["gpu"], runner).ok


def test_unknown_checks_fail() -> None:
    results = gate.run_checks({"disk": {}}, FakeRunner(HEALTHY), lambda url: 200, no_sntp)
    assert [(r.name, r.ok) for r in results] == [("disk", False)]


# --- gate -----------------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_gate_waits_until_every_check_passes() -> None:
    statuses = iter([0, 0, 200])
    clock = Clock()
    passed, results, rounds = gate.gate(
        {"timeout": 60, "interval": 5, "checks": CHECKS},
        runner=FakeRunner(HEALTHY),
        fetch=lambda url: next(statuses),
        sntp=no_sntp,
        sleep=clock.sleep,
        clock=clock,
    )
    assert (passed, rounds, clock.now) == (True, 3, 10.0)
    assert [r.name for r in results] == ["time", "postgres", "collector", "incus", "gpu"]


def test_gate_gives_up_at_the_timeout() -> None:
    clock = Clock()
    passed, results, rounds = gate.gate(
        {"timeout": 20, "interval": 5, "checks": {"incus": {}}},
        runner=FakeRunner({}),
        fetch=lambda url: 200,
        sntp=no_sntp,
        sleep=clock.sleep,
        clock=clock,
    )
    assert not passed
    assert rounds == 5
    assert not results[0].ok


def test_status_file(tmp_path: Path) -> None:
    path = tmp_path / "run" / "health-gate.json"
    gate.write_status(path, True, [gate.Result("incus", ok=True, detail="API answers")], 2)
    status = json.loads(path.read_text())
    assert status["passed"] is True
    assert status["checks"] == [{"name": "incus", "ok": True, "detail": "API answers"}]
    assert status["checked_at"].endswith("Z")
    assert path.stat().st_mode & 0o777 == 0o644


def test_main_reports_configuration_errors(tmp_path: Path) -> None:
    config = tmp_path / "gate.json"
    config.write_text('{"checks": []}')
    assert gate.main(["--config", str(config), "--status", str(tmp_path / "s.json")]) == 2
    assert gate.main(["--config", str(tmp_path / "missing.json")]) == 2


def test_main_once_with_a_failing_check(tmp_path: Path) -> None:
    config = tmp_path / "gate.json"
    config.write_text(json.dumps({"checks": {"collector": {"url": "http://127.0.0.1:9/"}}}))
    status = tmp_path / "s.json"
    assert gate.main(["--config", str(config), "--status", str(status), "--once"]) == 1
    assert json.loads(status.read_text())["passed"] is False


@pytest.fixture
def ntp_server() -> Iterator[tuple[int, float]]:
    """A one-shot SNTP server on loopback whose clock is 2.5 s ahead."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    ahead = 2.5

    def serve() -> None:
        data, peer = sock.recvfrom(512)
        assert len(data) == 48
        now = time.time() + ahead + gate.NTP_EPOCH_OFFSET
        stamp = struct.pack("!II", int(now), int((now % 1) * 2**32))
        reply = bytes([0x24]) + bytes(31) + stamp + stamp  # version 4, mode 4 (server)
        sock.sendto(reply, peer)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    yield sock.getsockname()[1], ahead
    thread.join(timeout=5)
    sock.close()


def test_sntp_offset_against_a_local_server(ntp_server: tuple[int, float]) -> None:
    port, ahead = ntp_server
    assert gate.sntp_offset("127.0.0.1", 2.0, port) == pytest.approx(ahead, abs=0.2)


# --- session record -------------------------------------------------------------

FINGERPRINT = {"hash": "sha256:" + "a" * 64, "fingerprint": {"fingerprint_schema_version": 1}}


def session_runner(fingerprint: tuple[int, str], preflight: tuple[int, str]) -> FakeRunner:
    return FakeRunner(
        {
            "/usr/local/bin/pokus-lab fingerprint": fingerprint,
            "/usr/local/bin/pokus-lab preflight": preflight,
        }
    )


def test_record_with_fingerprint_and_degraded_preflight() -> None:
    runner = session_runner((0, json.dumps(FINGERPRINT)), (10, '{"outcome": "degraded"}'))
    record = session.build_record(
        "b00t", "2026-10-06T12:00:00Z", "/usr/local/bin/pokus-lab", runner
    )
    assert record == {
        "schema_version": 1,
        "boot_id": "b00t",
        "started_at": "2026-10-06T12:00:00Z",
        "fingerprint_hash": FINGERPRINT["hash"],
        "fingerprint": FINGERPRINT,
        "preflight_outcome": "degraded",
        "errors": {},
    }


def test_record_without_pokus_lab() -> None:
    runner = session_runner((127, ""), (127, ""))
    record = session.build_record(
        "b00t", "2026-10-06T12:00:00Z", "/usr/local/bin/pokus-lab", runner
    )
    assert record["fingerprint"] is None
    assert record["fingerprint_hash"] is None
    assert record["preflight_outcome"] is None
    assert sorted(record["errors"]) == ["fingerprint", "preflight"]


def test_record_with_broken_output() -> None:
    runner = session_runner((0, "not json"), (40, ""))
    record = session.build_record(
        "b00t", "2026-10-06T12:00:00Z", "/usr/local/bin/pokus-lab", runner
    )
    assert "no JSON" in record["errors"]["fingerprint"]
    assert record["preflight_outcome"] is None


def test_one_record_per_boot(tmp_path: Path) -> None:
    boot_id = tmp_path / "boot_id"
    boot_id.write_text("00000000-0000-0000-0000-000000000000\n")
    sessions = tmp_path / "sessions"
    args = ["--sessions", str(sessions), "--boot-id-file", str(boot_id)]
    runner = session_runner((0, json.dumps(FINGERPRINT)), (0, '{"outcome": "supported"}'))
    assert session.main(args, runner) == 0
    path = sessions / "00000000-0000-0000-0000-000000000000.json"
    record = json.loads(path.read_text())
    assert record["preflight_outcome"] == "supported"
    assert record["started_at"].endswith("Z")
    assert path.stat().st_mode & 0o777 == 0o644
    calls = len(runner.calls)
    assert session.main(args, runner) == 0
    assert len(runner.calls) == calls  # nothing collected again
    assert json.loads(path.read_text()) == record


def test_missing_boot_id(tmp_path: Path) -> None:
    assert (
        session.main(["--boot-id-file", str(tmp_path / "none")], session_runner((0, ""), (0, "")))
        == 1
    )


# --- role -----------------------------------------------------------------------


def checks_for(**facts: Any) -> dict[str, Any]:
    describe = next(
        t for t in load_yaml(TASKS) if t["name"] == "Describe the checks of the health gate"
    )
    variables = role_vars("lab_session", **facts)
    variables.update(
        {
            k: v
            for k, v in load_yaml(ROLES / "lab_session" / "vars" / "main.yml").items()
            if not k.startswith("lab_session_gpu")
        }
    )
    variables["lab_session_collector_health"] = "http://127.0.0.1:18730/"
    from ansible.template import trust_as_template  # noqa: PLC0415 - heavy import

    variables.update({k: trust_as_template(v) for k, v in describe["vars"].items()})
    expression = describe["ansible.builtin.set_fact"]["lab_session_checks"].strip()[2:-2]
    result: dict[str, Any] = evaluate(expression, variables)
    return result


GPU_FACTS = {"lab_session_gpu_image": "docker.io/nvidia/cuda@sha256:" + "0" * 64}


@pytest.mark.parametrize(
    ("node_role", "gpu", "daemon", "expected", "device_args"),
    [
        (
            "full",
            True,
            {"features": {"cdi": True}},
            ["collector", "gpu", "incus", "postgres", "time"],
            ["--device", "nvidia.com/gpu=all"],
        ),
        ("lab", False, {}, ["collector", "incus", "postgres", "time"], None),
        (
            "inference",
            True,
            {"runtimes": {"nvidia": {}}},
            ["collector", "gpu", "time"],
            ["--runtime", "nvidia", "--gpus", "all"],
        ),
    ],
)
def test_checks_per_node_role(
    node_role: str,
    gpu: bool,
    daemon: dict[str, Any],
    expected: list[str],
    device_args: list[str] | None,
) -> None:

    checks = checks_for(
        node_role=node_role,
        pokus_provider="wsl2",
        lab_session_gpu=gpu,
        lab_session_daemon_json={"content": base64.b64encode(json.dumps(daemon).encode()).decode()},
        **GPU_FACTS,
    )
    assert sorted(checks) == expected
    assert checks["time"] == {"wsl": True, "ntp_reference": "pool.ntp.org", "max_offset": 1.0}
    if device_args:
        assert checks["gpu"]["device_args"] == device_args


def test_units_render() -> None:
    variables = role_vars("lab_session")
    variables.update(load_yaml(ROLES / "lab_session" / "vars" / "main.yml"))
    target = render_template("lab_session", "pokus-lab.target.j2", variables)
    assert "Wants=pokus-health-gate.service pokus-session-start.service" in target
    assert "WantedBy=multi-user.target" in target
    unit = render_template("lab_session", "pokus-health-gate.service.j2", variables)
    assert (
        "ExecStart=/usr/local/libexec/pokus/health-gate --config /etc/pokus/health-gate.json"
        in unit
    )
    assert "TimeoutStartSec=780" in unit
    assert "PartOf=pokus-lab.target" in unit
    record = render_template("lab_session", "pokus-session-start.service.j2", variables)
    assert "After=pokus-health-gate.service" in record
    assert "--sessions /var/lib/pokus/sessions" in record
