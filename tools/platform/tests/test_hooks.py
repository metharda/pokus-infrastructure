"""The platform's hooks (§12.9): judging, the wrapper, the transient units, the runs."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from hooks_testlib import FakeSystemd, command, properties
from platform_testlib import CONTRACT, manifest

from pinka_platform import accounts, cli, hooks, layout, names, units

# The one port refplat's units read: the broker's researcher endpoint.
PORTS = {"gateway_adhoc": 18705}

PACKAGE = Path(hooks.__file__).resolve().parents[1]


def rendered_accounts(name: str) -> dict[str, dict[str, Any]]:
    return {a["name"]: a for a in accounts.render(manifest(name), CONTRACT)["accounts"]}


def argv_for(name: str, hook: str, **extra: Any) -> list[str]:
    return hooks.systemd_run_argv(
        manifest(name),
        CONTRACT,
        hook,
        release="/srv/pinka/platforms/x/releases/r1",
        rendered_accounts=rendered_accounts(name),
        timeout=extra.pop("timeout", 30),
        result_path="/run/pinka/hooks/x.json",
        suffix="0123456789ab",
        **extra,
    )


# --- judging the one JSON line --------------------------------------------------------


@pytest.mark.parametrize(
    ("hook", "code", "stdout", "ok", "error"),
    [
        ("health", 0, '{"ok": true, "detail": {}}\n', True, None),
        ("migrate", 0, '\n{"ok": true}\n\n', True, None),
        ("drain", 0, '{"drained": true, "active": 0}\n', True, None),
        ("drain", 0, '{"ok": true}\n', False, '"drained" is not true'),
        ("health", 0, '{"ok": false}\n', False, '"ok" is not true'),
        ("health", 0, '{"ok": "true"}\n', False, '"ok" is not true'),
        ("health", 3, '{"ok": true}\n', False, "exit code 3"),
        ("health", -9, '{"ok": true}\n', False, "killed by signal 9"),
        ("health", 0, "", False, "printed 0 lines on stdout; one JSON line is expected"),
        ("health", 0, '{"ok": true}\n{"ok": true}\n', False, "printed 2 lines"),
        ("health", 0, "ok\n", False, "stdout is not JSON"),
        ("health", 0, "[true]\n", False, "stdout is not a JSON object"),
    ],
)
def test_judge(hook: str, code: int, stdout: str, ok: bool, error: str | None) -> None:
    result = hooks.judge(hook, code, stdout)
    assert result.ok is ok
    if error is None:
        assert result.error is None
    else:
        assert result.error is not None
        assert result.error.startswith(error)


def test_a_result_survives_its_json_form() -> None:
    result = hooks.HookResult("drain", ok=False, exit_code=2, output={"drained": False}, error="x")
    assert hooks.HookResult.from_dict("drain", json.loads(json.dumps(result.as_dict()))) == result
    # The hook that ran wins over what the file claims.
    assert hooks.HookResult.from_dict("health", {"hook": "drain", "ok": True}).hook == "health"
    assert hooks.HookResult.from_dict("health", {"ok": 1, "exit_code": True}).ok is False


# --- hook-exec, the wrapper inside the unit -----------------------------------------------


def python(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def wrapped(hook: str, argv: Sequence[str]) -> tuple[int, dict[str, Any]]:
    out: list[str] = []

    class Out:
        def write(self, text: str) -> None:
            out.append(text)

        def flush(self) -> None:
            pass

    code = hooks.exec_hook(hook, argv, out=Out())  # type: ignore[arg-type]
    lines = "".join(out).splitlines()
    assert len(lines) == 1
    return code, json.loads(lines[0])


def test_the_wrapper_judges_the_hook() -> None:
    code, result = wrapped("health", python('print(\'{"ok": true, "detail": {"db": 1}}\')'))
    assert code == 0
    assert result["ok"] is True
    assert result["output"] == {"ok": True, "detail": {"db": 1}}
    code, result = wrapped("drain", python("print('{\"drained\": false}'); raise SystemExit(4)"))
    assert code == 1
    assert result["exit_code"] == 4


def test_the_hooks_stderr_is_not_its_result(capfd: pytest.CaptureFixture[str]) -> None:
    code, result = wrapped(
        "health", python('import sys; print("noise", file=sys.stderr); print(\'{"ok": true}\')')
    )
    assert code == 0
    assert result["ok"] is True
    assert "noise" in capfd.readouterr().err  # inherited: the unit's journal


def test_the_wrapper_bounds_the_output() -> None:
    code, result = wrapped("health", python(f"print('x' * {hooks.MAX_OUTPUT_BYTES + 10})"))
    assert code == 1
    assert "more than" in result["error"]


def test_the_wrapper_reports_a_missing_command(tmp_path: Path) -> None:
    code, result = wrapped("health", [str(tmp_path / "missing")])
    assert code == 1
    assert result["error"].startswith("cannot run the hook")
    code, result = wrapped("upgrade", python("pass"))
    assert result["error"] == "no such hook"


def test_the_wrapper_reports_a_stop_at_the_timeout(tmp_path: Path) -> None:
    # systemd sends SIGTERM to every process of the unit (KillMode=control-group).
    ready = tmp_path / "ready"
    hook = python(f"import pathlib, time; pathlib.Path({str(ready)!r}).touch(); time.sleep(30)")
    script = (
        "import sys; from pinka_platform import hooks; "
        f"sys.exit(hooks.exec_hook('drain', {hook!r}))"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": str(PACKAGE)},
        start_new_session=True,
    )
    deadline = time.monotonic() + 20
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    os.killpg(child.pid, signal.SIGTERM)
    stdout, _ = child.communicate(timeout=20)
    result = json.loads(stdout)
    assert child.returncode == 1
    assert result["error"] == "stopped by systemd at the hook's timeout"


def test_cli_hook_exec(capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        cli.main(["hook-exec", "--hook", "health", "--", *python("print('{\"ok\": true}')")]) == 0
    )
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert cli.main(["hook-exec", "--hook", "health"]) == cli.EXIT_USAGE


# --- the transient unit -------------------------------------------------------------------


@pytest.mark.parametrize("name", ["refplat", "allprof", "pokus-platform"])
@pytest.mark.parametrize("hook", ["migrate", "drain", "health"])
def test_every_property_is_a_verified_transient_property(name: str, hook: str) -> None:
    assert set(properties(argv_for(name, hook))) <= hooks.TRANSIENT_PROPERTIES


@pytest.mark.parametrize("hook", ["drain", "health"])
def test_controller_hooks_run_with_the_controllers_hardening(hook: str) -> None:
    data = manifest("refplat")
    controller = names.account("refplat", "ctl")
    argv = argv_for("refplat", hook)
    found = properties(argv)
    _, service = units.service_sections(
        data, data["services"][0], CONTRACT, rendered_accounts("refplat"), {}
    )
    for key, value in service:
        if key not in units.HOOK_DROPPED:
            assert found[key] == [value], key
    assert found["User"] == [controller]
    assert found["WorkingDirectory"] == ["/srv/pinka/platforms/x/releases/r1"]
    for dropped in ("Type", "Restart", "RuntimeDirectory", "ExecStart"):
        assert dropped not in found
    assert found["RuntimeMaxSec"] == ["30"]
    assert found["TimeoutStopSec"] == [str(hooks.STOP_GRACE_SECONDS)]
    assert found["StandardOutput"] == ["file:/run/pinka/hooks/x.json"]
    assert found["StandardError"] == ["journal"]
    assert "--pipe" not in argv  # it would override StandardError (systemd-run.c)
    assert f"--unit=pinka-hook@refplat-{hook}-0123456789ab.service" in argv
    assert command(argv)[:5] == [layout.PLATFORM_TOOL, "hook-exec", "--hook", hook, "--"]


def test_the_release_command_follows_the_wrapper() -> None:
    argv = argv_for("refplat", "drain", extra=["--timeout", "30"])
    assert command(argv)[5:] == [
        "/srv/pinka/platforms/x/releases/r1/.venv/bin/refplat-admin",
        "drain",
        "--timeout",
        "30",
    ]


def test_migrate_runs_as_the_migrate_account_without_network() -> None:
    found = properties(argv_for("allprof", "migrate"))
    account = names.account("allprof", "migrate")
    assert found["User"] == [account]
    assert found["Group"] == [account]
    assert found["SupplementaryGroups"] == [accounts.db_group("allprof")]
    assert found["PrivateNetwork"] == ["yes"]
    assert found["RestrictAddressFamilies"] == ["AF_UNIX"]
    assert found["IPAddressDeny"] == ["any"]
    assert found["MemoryDenyWriteExecute"] == ["yes"]
    hidden = {p.lstrip("-") for p in found["InaccessiblePaths"][0].split()}
    for path in (
        layout.RESTRICTED_DIR,
        layout.POSTGRES_READER_SOCKET_DIR,
        layout.SIGNING_DIR,
        layout.INCUS_CERT_DIR,
        layout.GPU_SOCKET,
        layout.RUN_NETWORK_SOCKET,
        *layout.INFRA_INTERNALS,
    ):
        assert path in hidden
    for key, value in units.BASELINE:
        assert found[key] == [value], key


def test_migrate_without_postgres_has_no_database_group() -> None:
    data = manifest("allprof")
    data["requires"] = [r for r in data["requires"] if r != "postgres"]
    entries = dict(hooks.migrate_entries(data))
    assert entries["SupplementaryGroups"] == ""


# --- one run: systemd-run, the result file ------------------------------------------------


def run(fake: Any, tmp_path: Path, hook: str = "health", **extra: Any) -> hooks.HookResult:
    return hooks.run_hook(
        manifest("refplat"),
        CONTRACT,
        hook,
        release="/srv/pinka/platforms/refplat/releases/r1",
        rendered_accounts=rendered_accounts("refplat"),
        runner=fake,
        results_dir=tmp_path / "hooks",
        **extra,
    )


def test_a_run_reads_and_removes_its_result(tmp_path: Path) -> None:
    fake = FakeSystemd()
    result = run(fake, tmp_path)
    assert result.ok is True
    assert result.output == {"ok": True}
    assert fake.result_path is not None
    assert fake.result_path.parent == tmp_path / "hooks"
    assert not fake.result_path.exists()
    assert (tmp_path / "hooks").stat().st_mode & 0o777 == 0o700
    assert properties(fake.calls[0])["RuntimeMaxSec"] == ["30"]  # the manifest's timeout


def test_drain_gets_its_timeout_as_an_argument(tmp_path: Path) -> None:
    fake = FakeSystemd('{"drained": true, "active": 0}')
    assert run(fake, tmp_path, "drain").ok
    assert command(fake.calls[0])[-2:] == ["--timeout", "600"]
    fake = FakeSystemd('{"drained": true, "active": 0}')
    assert run(fake, tmp_path, "drain", timeout=45).ok
    assert command(fake.calls[0])[-2:] == ["--timeout", "45"]
    assert properties(fake.calls[0])["RuntimeMaxSec"] == ["45"]
    assert fake.timeouts == [45 + hooks.STOP_GRACE_SECONDS + hooks.RUNNER_SLACK_SECONDS]


def test_a_failed_hook_is_reported(tmp_path: Path) -> None:
    result = run(FakeSystemd('{"ok": false}', returncode=1), tmp_path)
    assert result.ok is False
    assert result.error == '"ok" is not true'


def test_a_unit_without_a_result_is_a_failure(tmp_path: Path) -> None:
    fake = FakeSystemd(None, returncode=1)
    result = run(fake, tmp_path)
    assert result.ok is False
    assert result.error is not None
    assert "without a result" in result.error
    assert "boom" in result.error


def test_systemd_run_past_its_deadline_stops_the_unit(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def slow(argv: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(list(argv))
        if argv[0] == "systemd-run":
            raise subprocess.TimeoutExpired(list(argv), kwargs["timeout"])
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    result = run(slow, tmp_path)
    assert result.ok is False
    unit = next(a for a in calls[0] if a.startswith("--unit=")).removeprefix("--unit=")
    assert calls[1] == ["systemctl", "stop", unit]


def test_undeclared_and_unknown_hooks(tmp_path: Path) -> None:
    data = manifest("refplat")
    del data["hooks"]["drain"]
    fake = FakeSystemd()
    result = hooks.run_hook(
        data,
        CONTRACT,
        "drain",
        release="/r",
        rendered_accounts=rendered_accounts("refplat"),
        runner=fake,
        results_dir=tmp_path,
    )
    assert result.ok is True
    assert result.skipped is True
    assert fake.calls == []
    assert run(fake, tmp_path, "upgrade").error == "no such hook"


def test_wait_healthy_retries_until_ok() -> None:
    now = [0.0]
    answers = iter([False, False, True])
    slept: list[float] = []

    def once() -> hooks.HookResult:
        return hooks.HookResult("health", ok=next(answers))

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    result = hooks.wait_healthy(once, 60, interval=5, clock=lambda: now[0], sleep=sleep)
    assert result.ok is True
    assert slept == [5, 5]


def test_wait_healthy_gives_up_at_the_deadline() -> None:
    now = [0.0]

    def sleep(seconds: float) -> None:
        now[0] += seconds

    result = hooks.wait_healthy(
        lambda: hooks.HookResult("health", ok=False, error="db down"),
        12,
        interval=5,
        clock=lambda: now[0],
        sleep=sleep,
    )
    assert result.ok is False
    assert now[0] == 10  # runs at 0, 5 and 10; 15 would pass the deadline


# --- the reconcile unit -------------------------------------------------------------------


def test_reconcile_runs_before_the_services_through_the_wrapper() -> None:
    data = manifest("refplat")
    texts = units.render(data, CONTRACT, rendered_accounts("refplat"), PORTS)
    text = texts[units.reconcile_unit("refplat")]
    current = layout.release_current("refplat")
    assert "Type=oneshot\n" in text
    assert "RemainAfterExit=yes\n" in text
    assert "TimeoutStartSec=120\n" in text
    assert (
        f"ExecStart={layout.PLATFORM_TOOL} hook-exec --hook reconcile -- "
        f"{current}/.venv/bin/refplat-admin reconcile\n"
    ) in text
    assert f"User={names.account('refplat', 'ctl')}\n" in text
    assert "RuntimeDirectory=" not in text
    assert "Before=refplat-ctl.service refplat-agent.service refplat-broker.service\n" in text
    for service in ("ctl", "broker", "agent"):
        unit = texts[units.service_unit("refplat", service)]
        assert "refplat-reconcile.service" in unit.split("Requires=", 1)[1].split("\n", 1)[0]
        assert "refplat-reconcile.service" in unit.split("After=", 1)[1].split("\n", 1)[0]


def test_without_a_reconcile_hook_there_is_no_reconcile_unit() -> None:
    data = manifest("refplat")
    del data["hooks"]["reconcile"]
    texts = units.render(data, CONTRACT, rendered_accounts("refplat"), PORTS)
    assert units.reconcile_unit("refplat") not in texts
    assert all("reconcile" not in text for text in texts.values())
