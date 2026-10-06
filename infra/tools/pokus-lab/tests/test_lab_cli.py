"""Tests for the pokus-lab command line."""

from __future__ import annotations

import contextlib
import io
import json
import unittest

from labfakes import FakeSystem, lab_data

from pokus_lab import __version__
from pokus_lab.cli import build_parser, main

CONFIG = "/etc/pokus/lab.json"
CACHE = "/var/lib/pokus/fingerprint/current.json"


def call(argv: list[str], system: FakeSystem) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv, system)
    return code, out.getvalue(), err.getvalue()


def configured(**kwargs: object) -> FakeSystem:
    system = FakeSystem(**kwargs)  # type: ignore[arg-type]
    system.files[CONFIG] = json.dumps(lab_data())
    return system


class ParserTest(unittest.TestCase):
    def test_subcommands(self) -> None:
        parser = build_parser()
        self.assertEqual(parser.parse_args(["doctor", "--json", "--smoke"]).smoke, True)
        self.assertEqual(parser.parse_args(["stop", "--timeout", "60"]).timeout, 60.0)
        self.assertEqual(
            parser.parse_args(["images", "build", "_base", "--rebuild"]).blueprint, "_base"
        )
        self.assertEqual(
            parser.parse_args(["restore", "--", "--non-interactive"]).bootstrap_options,
            ["--non-interactive"],
        )
        args = parser.parse_args(["user", "add", "researcher3", "--key-file", "k.pub", "--admin"])
        self.assertEqual((args.user_action, args.name, args.admin), ("add", "researcher3", True))
        self.assertEqual(parser.parse_args(["--config", "/x.json", "status"]).config, "/x.json")

    def test_version_and_usage_errors(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as raised:
            build_parser().parse_args(["--version"])
        self.assertEqual(raised.exception.code, 0)
        self.assertEqual(out.getvalue().strip(), f"pokus-lab {__version__}")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            build_parser().parse_args(["images"])
        self.assertEqual(raised.exception.code, 2)


class MainTest(unittest.TestCase):
    def test_missing_config(self) -> None:
        code, _, err = call(["status"], FakeSystem())
        self.assertEqual(code, 1)
        self.assertIn(f"{CONFIG} does not exist", err)
        self.assertEqual(call(["doctor"], FakeSystem())[0], 2)

    def test_doctor_needs_root(self) -> None:
        code, _, err = call(["doctor"], configured(euid=1000))
        self.assertEqual(code, 2)
        self.assertIn("doctor needs root", err)

    def test_doctor_reports_every_check(self) -> None:
        system = configured()
        code, out, _ = call(["doctor", "--json", "--smoke"], system)
        report = json.loads(out)
        self.assertEqual(code, 2)
        self.assertEqual(report["exit_code"], 2)
        self.assertEqual(report["machine"], "pokus-lab")
        ids = [c["id"] for c in report["checks"]]
        self.assertIn("preflight.outcome", ids)
        self.assertEqual(ids[-2:], ["smoke._base", "smoke.web-nginx-flask"])
        code, text, _ = call(["doctor"], configured())
        self.assertEqual(code, 2)
        self.assertRegex(text.splitlines()[-1], r"^doctor: \d+ passed, \d+ failed, \d+ skipped$")

    def test_fingerprint_for_other_users(self) -> None:
        system = configured(euid=1000)
        system.files[CACHE] = json.dumps({"hash": "sha256:" + "0" * 64, "computed_at": "t"})
        code, out, _ = call(["fingerprint"], system)
        self.assertEqual((code, out), (0, "sha256:" + "0" * 64 + "  (computed t)\n"))
        code, out, _ = call(["fingerprint", "--json"], system)
        self.assertEqual(json.loads(out)["computed_at"], "t")
        del system.files[CACHE]
        code, _, err = call(["fingerprint"], system)
        self.assertEqual(code, 1)
        self.assertIn("no cached fingerprint", err)

    def test_command_errors(self) -> None:
        code, _, err = call(["start"], configured(euid=1000))
        self.assertEqual(code, 1)
        self.assertIn("start needs root", err)
        code, _, err = call(["images", "build", "nope"], configured())
        self.assertEqual(code, 1)
        self.assertIn("unknown blueprint", err)

    def test_dispatch(self) -> None:
        system = configured(commands={"systemctl stop pokus-lab.target": (0, "")})
        self.assertEqual(call(["stop", "--timeout", "0"], system)[0], 0)
        self.assertEqual(call(["wsl-helpers"], configured())[0], 1)
        system = configured()
        call(["images", "build"], system)
        call(["restore", "--", "--non-interactive"], system)
        self.assertEqual(
            [argv[-1] for argv, _ in system.interactive_calls], ["images", "--non-interactive"]
        )


if __name__ == "__main__":
    unittest.main()
