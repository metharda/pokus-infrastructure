"""The quiet gate: ``make lint``, ``make test`` and ``make docs`` in one line each.

The Makefile runs these targets through this module unless ``VERBOSE=1``.
The gate runs ``make setup`` once, then every step of the target (a make
target of its own) with ``GATE_SETUP_DONE=1``, and writes each step's complete
output to ``<log-dir>/<gate>/<step>.log``. Every step runs, even after one
failed, so one run reports every failure.

* On success it prints one line: the counts of passed, failed and skipped
  items and of warnings, the number of steps and where the logs are.
* On failure it prints the complete output of every failing step, then the
  same line, marked ``FAILED``, and exits with 1. pytest runs with
  ``--tb=long`` (``PYTEST_ADDOPTS``), so every traceback is complete.

Nothing is filtered: the gate only decides which logs to print. Counting:

* a step whose output holds a pytest or unittest result counts its tests;
* any other step counts as one check, passed or failed by its exit status;
* a step that exits non-zero counts at least one failure, whatever it printed,
  and so does a step whose output reports a failed test;
* warnings are pytest's and ansible-lint's own counts plus every line that is
  a warning by its form: it starts with ``warning`` in any case (also after a
  box frame or a warning sign), or reads ``<file>:<line>[:<column>]:
  warning``, ``<file>:<line>: <Category>Warning:`` or yamllint's
  ``<line>:<column>  warning``. A tool that prints a warning and also counts
  it may be counted twice, never fewer.

Standard library only, like the privacy scan: the gate starts before setup.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

SETUP = "setup"
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
PYTEST_SUMMARY_RE = re.compile(r"^=+ (?P<body>.+?) in \d+(?:\.\d+)?s(?: \([0-9:]+\))? =+$")
PYTEST_PART_RE = re.compile(
    r"(?P<n>\d+) (?P<kind>passed|failed|errors?|skipped|deselected|xfailed|xpassed|warnings?)\b"
)
UNITTEST_RAN_RE = re.compile(r"^Ran (?P<n>\d+) tests? in ")
UNITTEST_RESULT_RE = re.compile(r"^(?P<status>OK|FAILED)(?: \((?P<detail>[^)]*)\))?$")
UNITTEST_PART_RE = re.compile(r"(?P<kind>[a-z ]+)=(?P<n>\d+)")
ANSIBLE_LINT_RE = re.compile(r"\b(?P<n>\d+) warning\(s\)")
WARNING_LINE_RE = re.compile(
    r"^[\s│|]*(?:⚠\s*)?warning\b"  # warning: ..., WARNING - ..., │ ⚠ Warning ...
    r"|^\S+?:\d+(?::\d+)?: warning\b"  # <file>:<line>[:<col>]: warning ...
    r"|^\S+?:\d+: \w*Warning: "  # Python's warnings module
    r"|^\s+\d+:\d+\s+warning\s",  # yamllint
    re.IGNORECASE,
)


@dataclass
class Counts:
    """What one step, or a whole gate, reports.

    Attributes:
        passed: Tests or checks that passed.
        failed: Tests or checks that failed (errors included).
        skipped: Tests that did not run (skipped, deselected, expected failures).
        warnings: Warnings, as counted in the module docstring.
        tests: Whether the numbers count tests rather than the step itself.
    """

    passed: int = 0
    failed: int = 0
    skipped: int = 0
    warnings: int = 0
    tests: bool = False

    def add(self, other: Counts) -> None:
        """Add another step's counts to these.

        Args:
            other: The counts to add.
        """
        self.passed += other.passed
        self.failed += other.failed
        self.skipped += other.skipped
        self.warnings += other.warnings
        self.tests = self.tests or other.tests


def _pytest(line: str, counts: Counts) -> bool:
    match = PYTEST_SUMMARY_RE.match(line)
    if not match:
        return False
    body = match.group("body")
    parts = PYTEST_PART_RE.findall(body)
    if not parts and body != "no tests ran":
        return False
    counts.tests = True
    for number, kind in parts:
        n = int(number)
        if kind == "passed":
            counts.passed += n
        elif kind in ("failed", "error", "errors", "xpassed"):
            counts.failed += n
        elif kind in ("skipped", "deselected", "xfailed"):
            counts.skipped += n
        else:
            counts.warnings += n
    return True


def _unittest(ran: int, detail: str | None, counts: Counts) -> None:
    parts = {kind.strip(): int(n) for kind, n in UNITTEST_PART_RE.findall(detail or "")}
    failed = parts.get("failures", 0) + parts.get("errors", 0)
    failed += parts.get("unexpected successes", 0)
    skipped = parts.get("skipped", 0) + parts.get("expected failures", 0)
    counts.tests = True
    counts.passed += max(ran - failed - skipped, 0)
    counts.failed += failed
    counts.skipped += skipped


def parse(output: str) -> Counts:
    """Count tests, skips and warnings in one step's output.

    Args:
        output: The step's complete output.

    Returns:
        The counts the output reports (``tests`` is False when it holds no
        test result).
    """
    counts = Counts()
    ran: int | None = None
    for raw in output.splitlines():
        line = ANSI_RE.sub("", raw).rstrip()
        if _pytest(line, counts):
            continue
        if match := UNITTEST_RAN_RE.match(line):
            ran = int(match.group("n"))
            continue
        if ran is not None and (match := UNITTEST_RESULT_RE.match(line)):
            _unittest(ran, match.group("detail"), counts)
            ran = None
            continue
        if match := ANSIBLE_LINT_RE.search(line):
            counts.warnings += int(match.group("n"))
        elif WARNING_LINE_RE.match(line):
            counts.warnings += 1
    return counts


def step_counts(output: str, status: int) -> Counts:
    """A step's counts, its exit status included.

    Args:
        output: The step's complete output.
        status: Its exit status.

    Returns:
        The counts; a failing step always counts at least one failure.
    """
    counts = parse(output)
    if not counts.tests:
        if status == 0:
            counts.passed += 1
        else:
            counts.failed += 1
    elif status != 0 and counts.failed == 0:
        counts.failed = 1
    return counts


def dry_run(makeflags: str) -> bool:
    """Whether make runs with ``-n`` (its single-letter flags come first).

    Args:
        makeflags: The value of ``MAKEFLAGS``.

    Returns:
        True for a dry run.
    """
    first = makeflags.split(" ", 1)[0]
    return bool(first) and not first.startswith("-") and "=" not in first and "n" in first


def summary(gate: str, counts: Counts, steps: int, logs: Path, failing: Sequence[str]) -> str:
    """The gate's one line.

    Args:
        gate: The target's name.
        counts: The counts of all steps.
        steps: How many steps ran (setup not included).
        logs: The directory of the step logs.
        failing: The steps that failed.

    Returns:
        The line, without a newline.
    """
    state = "FAILED: " if failing else ""
    where = f"failing: {', '.join(failing)}; " if failing else ""
    return (
        f"{gate}: {state}{counts.passed} passed, {counts.failed} failed, "
        f"{counts.skipped} skipped, {counts.warnings} warnings "
        f"({steps} steps; {where}logs: {logs})"
    )


def _run(command: Sequence[str], log: Path, env: Mapping[str, str]) -> tuple[int, str]:
    with log.open("wb") as out:
        done = subprocess.run(  # noqa: S603 - make with fixed arguments
            list(command), stdout=out, stderr=subprocess.STDOUT, env=dict(env), check=False
        )
    return done.returncode, log.read_text(encoding="utf-8", errors="replace")


def _show(out: TextIO, title: str, status: int, log: Path, output: str) -> None:
    out.write(f"----- {title}: exit status {status}; complete output ({log}) -----\n")
    out.write(output if output.endswith("\n") or not output else output + "\n")


def run(
    gate: str,
    steps: Iterable[str],
    *,
    make: str,
    log_dir: Path,
    environ: Mapping[str, str] | None = None,
    out: TextIO | None = None,
) -> int:
    """Run a gate's steps quietly.

    Args:
        gate: The target's name (``lint``, ``test``, ``docs``).
        steps: Its steps, make targets run one by one after setup.
        make: The make command (``$(MAKE)``).
        log_dir: Parent of the gate's log directory.
        environ: The environment (default: this process's).
        out: Where the gate writes (default: standard output).

    Returns:
        0 when every step passed, 1 otherwise.
    """
    out = sys.stdout if out is None else out
    env = dict(os.environ if environ is None else environ)
    names = list(steps)
    base = [*shlex.split(make), "--no-print-directory"]
    if dry_run(env.get("MAKEFLAGS", "")):
        for step in [SETUP, *names]:
            extra = [] if step == SETUP else ["GATE_SETUP_DONE=1"]
            out.write(shlex.join([*base, *extra, step]) + "\n")
        return 0
    logs = log_dir / gate
    shutil.rmtree(logs, ignore_errors=True)
    logs.mkdir(parents=True)
    env["PYTEST_ADDOPTS"] = f"{env.get('PYTEST_ADDOPTS', '')} --tb=long".strip()
    status, output = _run([*base, SETUP], logs / f"{SETUP}.log", env)
    if status != 0:
        _show(out, f"{gate}/{SETUP}", status, logs / f"{SETUP}.log", output)
        out.write(summary(gate, Counts(failed=1), 0, logs, [SETUP]) + "\n")
        return 1
    total = Counts(warnings=parse(output).warnings)
    failing = []
    for step in names:
        log = logs / f"{step}.log"
        status, output = _run([*base, "GATE_SETUP_DONE=1", step], log, env)
        counts = step_counts(output, status)
        total.add(counts)
        if status != 0 or counts.failed:
            failing.append(step)
            _show(out, f"{gate}/{step}", status, log, output)
    out.write(summary(gate, total, len(names), logs, failing) + "\n")
    return 1 if failing else 0
