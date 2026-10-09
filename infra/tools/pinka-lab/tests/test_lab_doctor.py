"""Tests for the doctor framework: running checks, exit codes, reports."""

from __future__ import annotations

import json
import unittest

from labfakes import FakeSystem, context

from pinka_lab.doctor import (
    EXIT_FAILURES,
    EXIT_OK,
    EXIT_WARNINGS,
    Check,
    Context,
    Outcome,
    exit_code,
    failed,
    passed,
    render_json,
    render_text,
    run_checks,
    skipped,
)


def check(check_id: str, severity: str, outcome: Outcome) -> Check:
    return Check(check_id, f"{check_id} holds.", severity, f"fix {check_id}", lambda _ctx: outcome)


def broken(_ctx: Context) -> Outcome:
    raise ValueError("boom")


class RunTest(unittest.TestCase):
    def test_findings_keep_order_and_remediation_only_on_failure(self) -> None:
        findings = run_checks(
            [
                check("a.ok", "error", passed("fine")),
                check("b.bad", "error", failed("broken")),
                check("c.skip", "warning", skipped("not here")),
            ],
            context(FakeSystem()),
        )
        self.assertEqual([f.id for f in findings], ["a.ok", "b.bad", "c.skip"])
        self.assertEqual([f.result for f in findings], ["pass", "fail", "skip"])
        self.assertEqual([f.remediation for f in findings], ["", "fix b.bad", ""])

    def test_exception_fails_the_check_only(self) -> None:
        findings = run_checks(
            [Check("x.broken", "d", "warning", "r", broken), check("y.ok", "error", passed(""))],
            context(FakeSystem()),
        )
        self.assertEqual(findings[0].result, "fail")
        self.assertIn("ValueError: boom", findings[0].detail)
        self.assertEqual(findings[1].result, "pass")


class ExitCodeTest(unittest.TestCase):
    def codes(self, *outcomes: tuple[str, Outcome]) -> int:
        checks = [check(f"c.{i}", sev, out) for i, (sev, out) in enumerate(outcomes)]
        return exit_code(run_checks(checks, context(FakeSystem())))

    def test_codes(self) -> None:
        self.assertEqual(self.codes(("error", passed("")), ("error", skipped(""))), EXIT_OK)
        self.assertEqual(self.codes(("warning", failed("")), ("error", passed(""))), EXIT_WARNINGS)
        self.assertEqual(self.codes(("warning", failed("")), ("error", failed(""))), EXIT_FAILURES)
        self.assertEqual((EXIT_OK, EXIT_WARNINGS, EXIT_FAILURES), (0, 1, 2))


class RenderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.findings = run_checks(
            [
                check("a.ok", "error", passed("fine")),
                check("b.warn", "warning", failed("old")),
                check("c.fail", "error", failed("broken")),
                check("d.skip", "error", skipped("absent")),
            ],
            context(FakeSystem()),
        )

    def test_text(self) -> None:
        text = render_text(self.findings)
        lines = text.splitlines()
        self.assertTrue(lines[0].startswith("ok    a.ok"))
        self.assertTrue(lines[1].startswith("warn  b.warn"))
        self.assertEqual(lines[2].strip(), "-> fix b.warn")
        self.assertTrue(lines[3].startswith("FAIL  c.fail"))
        self.assertIn("skip  d.skip", text)
        self.assertEqual(lines[-1], "doctor: 1 passed, 2 failed, 1 skipped")

    def test_json(self) -> None:
        report = json.loads(render_json(self.findings, "pinka-lab", "2026-01-01T12:00:00Z"))
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["machine"], "pinka-lab")
        self.assertEqual(report["exit_code"], 2)
        self.assertEqual(
            sorted(report["checks"][0]),
            ["description", "detail", "id", "remediation", "result", "severity"],
        )


if __name__ == "__main__":
    unittest.main()
