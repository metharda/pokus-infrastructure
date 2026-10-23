"""Maintenance windows (BUILD_PROMPT §7.20)."""

from __future__ import annotations

import contextlib
import datetime as dt
import io
import json
import unittest
from collections.abc import Callable
from typing import Any

from labfakes import NOW, FakeSystem, context, lab

from pinka_lab import checks, commands, window
from pinka_lab.commands import CommandError

RECORD = window.RECORD
# NOW is 2026-01-01T12:00:00Z.
OPEN = {
    "start": "2026-01-01T11:00:00Z",
    "end": "2026-01-01T15:00:00Z",
    "created_at": "2026-01-01T10:00:00Z",
}
ENDED = {
    "start": "2026-01-01T08:00:00Z",
    "end": "2026-01-01T11:00:00Z",
    "created_at": "2026-01-01T07:00:00Z",
}
STAGING = {"lab_mode": "staging", "window": {"record": RECORD, "shared_hardware": True}}
SMI = "nvidia-smi --query-compute-apps=pid,process_name --format=csv,noheader"


def captured(call: Callable[[], int]) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = call()
    return code, out.getvalue()


def staging(**extra: Any) -> Any:
    return lab(**STAGING, ports={"grafana": 18700}, **extra)


class WindowRecordTest(unittest.TestCase):
    def test_times(self) -> None:
        self.assertEqual(
            window.parse_time("2026-10-12T18:00Z"),
            dt.datetime(2026, 10, 12, 18, 0, tzinfo=dt.timezone.utc),
        )
        self.assertEqual(window.parse_time("2026-10-12T18:00:30Z").second, 30)
        for bad in ("2026-10-12 18:00", "2026-10-12T18:00+02:00", "tomorrow"):
            with self.assertRaises(window.WindowError):
                window.parse_time(bad)

    def test_new_and_round_trip(self) -> None:
        opened = window.new("2026-01-01T13:00Z", "2026-01-01T14:00Z", NOW)
        self.assertEqual(
            json.loads(window.to_text(opened)),
            {
                "start": "2026-01-01T13:00:00Z",
                "end": "2026-01-01T14:00:00Z",
                "created_at": "2026-01-01T12:00:00Z",
            },
        )
        self.assertEqual(window.from_text(window.to_text(opened)), opened)
        self.assertFalse(opened.is_open(NOW))
        self.assertTrue(opened.is_open(opened.start))
        self.assertFalse(opened.is_open(opened.end))
        with self.assertRaisesRegex(window.WindowError, "end after it starts"):
            window.new("2026-01-01T14:00Z", "2026-01-01T13:00Z", NOW)
        with self.assertRaisesRegex(window.WindowError, "in the past"):
            window.new("2026-01-01T10:00Z", "2026-01-01T11:00Z", NOW)
        with self.assertRaisesRegex(window.WindowError, "not a maintenance window record"):
            window.from_text('{"start": "x"}')


class WindowCommandTest(unittest.TestCase):
    def test_open_on_production_drains_and_stops_first(self) -> None:
        system = FakeSystem({"systemctl stop pinka-lab.target": (0, "")})
        code, out = captured(
            lambda: commands.window_open(system, lab(), "2026-01-01T13:00Z", "2026-01-01T17:00Z")
        )
        self.assertEqual(code, 0)
        self.assertEqual(system.calls[0], ["systemctl", "stop", "pinka-lab.target"])
        text, mode = system.written[RECORD]
        self.assertEqual(mode, 0o644)
        self.assertEqual(json.loads(text)["end"], "2026-01-01T17:00:00Z")
        self.assertIn("production stays stopped, also after the end", out)

    def test_open_refuses(self) -> None:
        with self.assertRaisesRegex(CommandError, "root"):
            commands.window_open(
                FakeSystem(euid=1000), lab(), "2026-01-01T13:00Z", "2026-01-01T14:00Z"
            )
        recorded = FakeSystem(files={RECORD: json.dumps(OPEN)})
        with self.assertRaisesRegex(CommandError, "already recorded"):
            commands.window_open(recorded, lab(), "2026-01-01T13:00Z", "2026-01-01T14:00Z")
        with self.assertRaisesRegex(CommandError, "in the past"):
            commands.window_open(FakeSystem(), lab(), "2026-01-01T09:00Z", "2026-01-01T10:00Z")
        own_hardware = lab(lab_mode="staging", window={"record": RECORD, "shared_hardware": False})
        with self.assertRaisesRegex(CommandError, "needs no maintenance window"):
            commands.window_open(
                FakeSystem(), own_hardware, "2026-01-01T13:00Z", "2026-01-01T14:00Z"
            )

    def test_open_on_staging_only_records(self) -> None:
        system = FakeSystem()
        code, _ = captured(
            lambda: commands.window_open(
                system, staging(), "2026-01-01T13:00Z", "2026-01-01T14:00Z"
            )
        )
        self.assertEqual(code, 0)
        self.assertEqual(system.calls, [])  # staging is not running yet
        self.assertIn(RECORD, system.written)

    def test_close(self) -> None:
        system = FakeSystem(files={RECORD: json.dumps(OPEN)})
        code, out = captured(lambda: commands.window_close(system, lab()))
        self.assertEqual(code, 0)
        self.assertEqual(system.removed, [RECORD])
        self.assertIn("start production again with sudo pinka start", out)
        self.assertIn(
            "no maintenance window", captured(lambda: commands.window_close(FakeSystem(), lab()))[1]
        )
        # Staging runs only inside a window: closing it stops a running staging lab.
        running = FakeSystem(
            {
                "systemctl is-active pinka-lab.target": (0, "active\n"),
                "systemctl stop pinka-lab.target": (0, ""),
            },
            files={RECORD: json.dumps(OPEN)},
        )
        captured(lambda: commands.window_close(running, staging()))
        self.assertIn(["systemctl", "stop", "pinka-lab.target"], running.calls)
        self.assertEqual(running.removed, [RECORD])

    def test_expire_stops_staging_after_the_end(self) -> None:
        running = {
            "systemctl is-active pinka-lab.target": (0, "active\n"),
            "systemctl stop pinka-lab.target": (0, ""),
        }
        inside = FakeSystem(dict(running), files={RECORD: json.dumps(OPEN)})
        self.assertEqual(captured(lambda: commands.window_expire(inside, staging()))[0], 0)
        self.assertNotIn(["systemctl", "stop", "pinka-lab.target"], inside.calls)
        ended = FakeSystem(dict(running), files={RECORD: json.dumps(ENDED)})
        code, out = captured(lambda: commands.window_expire(ended, staging()))
        self.assertEqual(code, 0)
        self.assertIn("has ended: stopping the staging lab", out)
        self.assertIn(["systemctl", "stop", "pinka-lab.target"], ended.calls)
        # Production never stops on its own here.
        production = FakeSystem(dict(running), files={RECORD: json.dumps(ENDED)})
        captured(lambda: commands.window_expire(production, lab()))
        self.assertEqual(production.calls, [])


class StartRulesTest(unittest.TestCase):
    def test_production_does_not_start_while_a_record_exists(self) -> None:
        system = FakeSystem(
            {"systemctl start pinka-lab.target": (0, "")}, files={RECORD: json.dumps(ENDED)}
        )
        with self.assertRaisesRegex(
            CommandError, "production stays stopped until sudo pinka window close"
        ):
            commands.start(system, lab())
        self.assertEqual(system.calls, [])

    def test_staging_starts_only_inside_its_window(self) -> None:
        start = {"systemctl start pinka-lab.target": (0, ""), SMI: (0, "")}
        with self.assertRaisesRegex(CommandError, "only inside an open maintenance window"):
            commands.start(FakeSystem(dict(start)), staging())
        with self.assertRaisesRegex(CommandError, "recorded: from 2026-01-01T08:00:00Z"):
            commands.start(FakeSystem(dict(start), files={RECORD: json.dumps(ENDED)}), staging())
        inside = FakeSystem(dict(start), files={RECORD: json.dumps(OPEN)})
        self.assertEqual(captured(lambda: commands.start(inside, staging()))[0], 0)
        self.assertEqual(inside.checked_ports, [{"grafana": 18700}])

    def test_staging_needs_an_idle_gpu_and_free_ports(self) -> None:
        busy_gpu = FakeSystem({SMI: (0, "4242, python3\n")}, files={RECORD: json.dumps(OPEN)})
        with self.assertRaisesRegex(CommandError, "GPU is in use by python3 \\(PID 4242\\)"):
            commands.start(busy_gpu, staging())
        ports = FakeSystem({SMI: (0, "")}, files={RECORD: json.dumps(OPEN)})
        ports.busy_ports = ["Lab port 18700 (grafana) is in use by grafana (PID 7) at 127.0.0.1"]
        with self.assertRaisesRegex(CommandError, "lab ports are not free: Lab port 18700"):
            commands.start(ports, staging())
        # Without nvidia-smi there is no GPU to share.
        no_gpu = FakeSystem(
            {"systemctl start pinka-lab.target": (0, "")}, files={RECORD: json.dumps(OPEN)}
        )
        self.assertEqual(captured(lambda: commands.start(no_gpu, staging()))[0], 0)

    def test_staging_on_its_own_hardware_starts_freely(self) -> None:
        system = FakeSystem({"systemctl start pinka-lab.target": (0, "")})
        own = lab(lab_mode="staging", window={"record": RECORD, "shared_hardware": False})
        self.assertEqual(captured(lambda: commands.start(system, own))[0], 0)


class WindowCheckTest(unittest.TestCase):
    def test_doctor_reports_the_window(self) -> None:
        def result(system: FakeSystem, **changes: Any) -> tuple[str, str]:
            outcome = checks.check_window(context(system, **changes))
            return outcome.result, outcome.detail

        self.assertEqual(result(FakeSystem()), ("pass", "no maintenance window"))
        recorded = FakeSystem(files={RECORD: json.dumps(OPEN)})
        state, detail = result(recorded)
        self.assertEqual(state, "fail")
        self.assertIn("production stays stopped", detail)
        self.assertEqual(result(recorded, **STAGING)[0], "pass")
        self.assertEqual(
            result(FakeSystem(files={RECORD: json.dumps(ENDED)}), **STAGING)[0], "fail"
        )
        self.assertIn("may not start", result(FakeSystem(), **STAGING)[1])
        check = next(c for c in checks.CHECKS if c.id == "lab.window")
        self.assertEqual(check.severity, "warning")


if __name__ == "__main__":
    unittest.main()
