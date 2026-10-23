"""Doctor: the lab's health and security checks (BUILD_PROMPT §9).

Every check has an ID, a description, a severity (``error`` or
``warning``), a result (``pass``, ``fail`` or ``skip``), a detail and a
remediation. Doctor exits 0 when nothing failed, 1 when only warnings failed
and 2 when an error failed.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from pinka_lab.config import LabConfig
from pinka_lab.system import System

SEVERITIES = ("error", "warning")
RESULTS = ("pass", "fail", "skip")
EXIT_OK = 0
EXIT_WARNINGS = 1
EXIT_FAILURES = 2


@dataclass(frozen=True)
class Outcome:
    """What one check found.

    Attributes:
        result: ``pass``, ``fail`` or ``skip``.
        detail: What was found, or why the check was skipped.
    """

    result: str
    detail: str


def passed(detail: str) -> Outcome:
    """A passing outcome.

    Args:
        detail: What was found.

    Returns:
        The outcome.
    """
    return Outcome("pass", detail)


def failed(detail: str) -> Outcome:
    """A failing outcome.

    Args:
        detail: What is wrong.

    Returns:
        The outcome.
    """
    return Outcome("fail", detail)


def skipped(reason: str) -> Outcome:
    """A skipped check.

    Args:
        reason: Why it does not apply.

    Returns:
        The outcome.
    """
    return Outcome("skip", reason)


@dataclass
class Context:
    """What checks see.

    Attributes:
        system: Host access.
        config: The lab description.
        memo: Results shared between checks (such as one preflight run).
    """

    system: System
    config: LabConfig
    memo: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Check:
    """One doctor check.

    Attributes:
        id: Stable identifier (``area.name``), used in docs and remediation links.
        description: What the check asserts.
        severity: ``error`` or ``warning``.
        remediation: What to do when it fails.
        run: The check itself.
    """

    id: str
    description: str
    severity: str
    remediation: str
    run: Callable[[Context], Outcome]


@dataclass(frozen=True)
class Finding:
    """A check's row in the report.

    Attributes:
        id: Check ID.
        description: What the check asserts.
        severity: ``error`` or ``warning``.
        result: ``pass``, ``fail`` or ``skip``.
        detail: What was found.
        remediation: What to do (empty unless the check failed).
    """

    id: str
    description: str
    severity: str
    result: str
    detail: str
    remediation: str


def run_checks(checks: Sequence[Check], context: Context) -> list[Finding]:
    """Run checks; a check that raises fails with the exception as detail.

    Args:
        checks: Checks to run, in order.
        context: What they see.

    Returns:
        One finding per check.
    """
    findings = []
    for check in checks:
        try:
            outcome = check.run(context)
        except Exception as exc:  # a broken check must not hide the others
            outcome = failed(f"the check itself failed: {type(exc).__name__}: {exc}")
        remediation = check.remediation if outcome.result == "fail" else ""
        findings.append(
            Finding(
                check.id,
                check.description,
                check.severity,
                outcome.result,
                outcome.detail,
                remediation,
            )
        )
    return findings


def exit_code(findings: Sequence[Finding]) -> int:
    """Doctor's exit code.

    Args:
        findings: The report.

    Returns:
        2 if an error check failed, 1 if only warning checks failed, else 0.
    """
    severities = {f.severity for f in findings if f.result == "fail"}
    if "error" in severities:
        return EXIT_FAILURES
    return EXIT_WARNINGS if severities else EXIT_OK


def render_text(findings: Sequence[Finding]) -> str:
    """Human-readable report.

    Args:
        findings: The report.

    Returns:
        One line per check, remediations under failures, and a summary.
    """
    marks = {"pass": "ok  ", "skip": "skip", "fail": "FAIL"}
    lines = []
    for f in findings:
        mark = marks[f.result] if f.result != "fail" or f.severity == "error" else "warn"
        lines.append(f"{mark}  {f.id:<28} {f.detail}")
        if f.remediation:
            lines.append(f"      -> {f.remediation}")
    counts = {r: sum(1 for f in findings if f.result == r) for r in RESULTS}
    lines.append(
        f"doctor: {counts['pass']} passed, {counts['fail']} failed, {counts['skip']} skipped"
    )
    return "\n".join(lines) + "\n"


def render_json(findings: Sequence[Finding], machine: str, checked_at: str) -> str:
    """Machine-readable report.

    Args:
        findings: The report.
        machine: Machine name.
        checked_at: UTC time of the run.

    Returns:
        JSON text.
    """
    report = {
        "schema_version": 1,
        "machine": machine,
        "checked_at": checked_at,
        "exit_code": exit_code(findings),
        "checks": [asdict(f) for f in findings],
    }
    return json.dumps(report, indent=1) + "\n"
