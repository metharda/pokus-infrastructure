"""Tests for the report, the schema validator, the summary and the CLI."""

from __future__ import annotations

import contextlib
import datetime as dt
import io
import json
import os
import stat
import tempfile
import unittest

from helpers import FIXTURES_DIR

from pokus_preflight import cli, render, report, schema_check

FIXTURE = os.path.join(FIXTURES_DIR, "ubuntu2404-wsl2-gpu-nokvm")


def run_cli(*args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(args))
    return code, out.getvalue(), err.getvalue()


class SchemaCheckTest(unittest.TestCase):
    SCHEMA = {
        "type": "object",
        "required": ["a"],
        "additionalProperties": False,
        "properties": {
            "a": {"type": "integer", "minimum": 1, "maximum": 3},
            "b": {"type": ["string", "null"], "pattern": "^x", "minLength": 2},
            "c": {
                "type": "array",
                "items": {"$ref": "#/$defs/e"},
                "minItems": 1,
                "uniqueItems": True,
            },
            "d": {"type": "object", "additionalProperties": {"enum": ["y"]}},
            "k": {"const": 1},
            "n": {"type": "number"},
        },
        "$defs": {"e": {"enum": ["p", "q"]}},
    }

    def test_valid(self) -> None:
        doc = {"a": 2, "b": "xy", "c": ["p", "q"], "d": {"z": "y"}, "k": 1, "n": 1.5}
        self.assertEqual(schema_check.validate(doc, self.SCHEMA), [])
        self.assertEqual(schema_check.validate({"a": 1, "b": None}, self.SCHEMA), [])

    def test_errors(self) -> None:
        doc = {"b": "y", "c": ["p", "p", "r"], "d": {"z": "n"}, "extra": 1, "k": 2, "n": True}
        errors = schema_check.validate(doc, self.SCHEMA)
        joined = "\n".join(errors)
        for needle in (
            "missing 'a'",
            "does not match",
            "shorter than",
            "not unique",
            "'r' is not one of",
            "unexpected property 'extra'",
            "expected 1",
            "expected number",
        ):
            self.assertIn(needle, joined)
        self.assertTrue(schema_check.validate({"a": 9}, self.SCHEMA))
        self.assertTrue(schema_check.validate({"a": True}, self.SCHEMA))
        self.assertTrue(schema_check.validate({"a": 1, "c": []}, self.SCHEMA))

    def test_unsupported_keywords_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            schema_check.validate(1, {"oneOf": []})
        with self.assertRaises(ValueError):
            schema_check.validate(1, {"$ref": "other.json#/x"})

    def test_host_profile_schema_uses_only_supported_keywords(self) -> None:
        def walk(node: object) -> None:
            if isinstance(node, dict):
                if (
                    "type" in node
                    or "enum" in node
                    or "$ref" in node
                    or "const" in node
                    or "properties" in node
                ):
                    self.assertFalse(set(node) - schema_check.SUPPORTED, node)
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(report.load_schema())


class ReportTest(unittest.TestCase):
    def test_utc_iso(self) -> None:
        when = dt.datetime(2026, 10, 5, 11, 12, 44, tzinfo=dt.timezone(dt.timedelta(hours=2)))
        self.assertEqual(report.utc_iso(when), "2026-10-05T09:12:44Z")

    def test_write_profile_is_atomic_and_world_readable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            profile = os.path.join(tmp, "etc", "pokus", "host-profile.json")
            archive_dir = os.path.join(tmp, "var", "preflight")
            when = dt.datetime(2026, 10, 5, 9, 12, 44, tzinfo=dt.timezone.utc)
            written = report.write_profile({"x": 1}, when, (profile, archive_dir))
            self.assertEqual(written, [profile, os.path.join(archive_dir, "20261005T091244Z.json")])
            for path in written:
                self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o644)
                with open(path, encoding="utf-8") as handle:
                    self.assertEqual(json.load(handle), {"x": 1})
            self.assertEqual(sorted(os.listdir(os.path.dirname(profile))), ["host-profile.json"])


class CliTest(unittest.TestCase):
    def test_fixture_run_prints_json_and_summary(self) -> None:
        code, out, err = run_cli("--root", FIXTURE, "--offline", "--color", "never")
        self.assertEqual(code, 10)
        doc = json.loads(out)
        self.assertEqual(doc["outcome"], "degraded")
        self.assertTrue(doc["network"]["offline"])
        self.assertIsNone(doc["repo_commit"])
        self.assertIn("Pokus preflight: DEGRADED (exit 10)", err)
        self.assertNotIn("Profile", err)

    def test_output_file_quiet_and_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "profile.json")
            code, out, err = run_cli(
                "--root",
                FIXTURE,
                "--offline",
                "--quiet",
                "--output",
                target,
                "--accept-degraded",
                "--force-unsupported",
                "--allow-dirty",
                "--repo-commit",
                "0" * 40,
            )
            self.assertEqual((code, out, err), (10, "", ""))
            with open(target, encoding="utf-8") as handle:
                doc = json.load(handle)
        self.assertTrue(doc["overrides"]["accept_degraded"])
        self.assertTrue(doc["overrides"]["force_unsupported"])
        self.assertTrue(doc["overrides"]["allow_dirty"])
        self.assertFalse(doc["overrides"]["adopt_incus"])
        self.assertEqual(doc["repo_commit"], "0" * 40)

    def test_detection_failure_is_preflight_error(self) -> None:
        code, out, err = run_cli("--root", FIXTURE, "--offline", "--matrix", "/nonexistent.json")
        self.assertEqual(code, 40)
        self.assertEqual(out, "")
        self.assertIn("preflight_error", err)
        self.assertIn("Traceback", err)

    def test_color_choice(self) -> None:
        os.environ["NO_COLOR"] = "1"
        try:
            self.assertFalse(cli._use_color("auto"))
            self.assertTrue(cli._use_color("always"))
        finally:
            del os.environ["NO_COLOR"]
        self.assertFalse(cli._use_color("never"))

    def test_version(self) -> None:
        with self.assertRaises(SystemExit) as exc, contextlib.redirect_stdout(io.StringIO()):
            cli.main(["--version"])
        self.assertEqual(exc.exception.code, 0)


class RenderTest(unittest.TestCase):
    def test_color_and_sections(self) -> None:
        _, out, _ = run_cli("--root", FIXTURE, "--offline", "--quiet")
        doc = json.loads(out)
        plain = render.render(doc)
        colored = render.render(doc, color=True)
        self.assertNotIn("\033[", plain)
        self.assertIn("\033[1;33m", colored)
        self.assertIn("Disabled features", plain)
        self.assertIn("vm_targets", plain)


if __name__ == "__main__":
    unittest.main()
