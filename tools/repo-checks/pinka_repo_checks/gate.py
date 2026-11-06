"""The quiet gate: ``make lint``, ``make test`` and ``make docs`` in one line each.

The Makefile runs these targets through this module unless ``VERBOSE=1``, and
``make gate`` runs them and the ``order`` gate (the random order and the
isolated files) through it always. The gate runs ``make setup`` once, then
every step (a make target of its own) with ``GATE_SETUP_DONE=1``, and writes
each step's complete
output to ``<log-dir>/<gate>/<step>.log``. Every step runs, even after one
failed, so one run reports every failure.

* On success it prints one line: the counts of passed, failed and skipped
  items and of warnings, the number of steps and where the logs are. Below
  it, one line per skipped test, as the test runs name them
  (``skipped test: <id> (reason: <reason>)``, :mod:`pinka_repo_checks.ci_skips`):
  every gate report names each skipped test, not only their count.
* On failure it prints the complete output of every failing step, then the
  same line, marked ``FAILED``, and exits with 1. pytest runs with
  ``--tb=long`` (``PYTEST_ADDOPTS``), so every traceback is complete.

Nothing is filtered: the gate only decides which logs to print. Counting:

* a step whose output holds a pytest or unittest result counts its tests;
  ``test-isolated`` counts by its own line, which holds every file's;
* any other step counts as one check, passed or failed by its exit status;
* a step that exits non-zero counts at least one failure, whatever it printed,
  and so does a step whose output reports a failed test;
* warnings are pytest's and ansible-lint's own counts plus every line that is
  a warning by its form: it starts with ``warning`` in any case (also after a
  box frame or a warning sign), or reads ``<file>:<line>[:<column>]:
  warning``, ``<file>:<line>: <Category>Warning:`` or yamllint's
  ``<line>:<column>  warning``. A tool that prints a warning and also counts
  it may be counted twice, never fewer.

With ``--local`` (``make gate``, never in CI) a step that the change
classifier finds the local tree does not need (:mod:`pinka_repo_checks.changes`)
does not run: the gate names it below its line (``skipped step: <gate>/<step>
(<why>)``). With ``CI=true`` every step runs.

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
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

SETUP = "setup"
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
PYTEST_SUMMARY_RE = re.compile(r"^=+ (?P<body>.+?) in \d+(?:\.\d+)?s(?: \([0-9:]+\))? =+$")
PYTEST_PART_RE = re.compile(
    r"(?P<n>\d+) (?P<kind>passed|failed|errors?|skipped|deselected|xfailed|xpassed|warnings?)\b"
)
UNITTEST_RAN_RE = re.compile(r"^Ran (?P<n>\d+) tests? in ")
ISOLATED_RE = re.compile(  # pinka_repo_checks.isolated.summary
    r"^test-isolated: (?:FAILED: )?(?P<passed>\d+) passed, (?P<failed>\d+) failed, "
    r"(?P<skipped>\d+) skipped, (?P<warnings>\d+) warnings \("
)
UNITTEST_RESULT_RE = re.compile(r"^(?P<status>OK|FAILED)(?: \((?P<detail>[^)]*)\))?$")
UNITTEST_PART_RE = re.compile(r"(?P<kind>[a-z ]+)=(?P<n>\d+)")
ANSIBLE_LINT_RE = re.compile(r"\b(?P<n>\d+) warning\(s\)")
SKIPPED_TEST_PREFIX = "skipped test: "  # pinka_repo_checks.ci_skips.line
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
        skipped_tests: The lines that name a skipped test, in order.
    """

    passed: int = 0
    failed: int = 0
    skipped: int = 0
    warnings: int = 0
    tests: bool = False
    skipped_tests: list[str] = field(default_factory=list)

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
        self.skipped_tests += other.skipped_tests


def _isolated(match: re.Match[str], counts: Counts) -> None:
    """test-isolated's own total: its tests and their warnings.

    It replaces the tests and the skipped-test lines its printed files'
    output showed (their pytest results are in the total, and the complete
    list of skipped tests follows the line). The warning lines counted
    before it stay, so a warning line in a printed file's output counts
    twice: never fewer (the module's rule).
    """
    counts.passed, counts.failed = int(match["passed"]), int(match["failed"])
    counts.skipped = int(match["skipped"])
    counts.warnings += int(match["warnings"])
    counts.skipped_tests = []
    counts.tests = True


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


def parse(output: str, *, isolated: bool = False) -> Counts:
    """Count tests, skips and warnings in one step's output.

    Args:
        output: The step's complete output.
        isolated: The output is test-isolated's: its own line counts, and the
            pytest results of its failing files are part of it.

    Returns:
        The counts the output reports (``tests`` is False when it holds no
        test result).
    """
    counts = Counts()
    ran: int | None = None
    for raw in output.splitlines():
        line = ANSI_RE.sub("", raw).rstrip()
        if isolated and (match := ISOLATED_RE.match(line)):
            _isolated(match, counts)
            continue
        if isolated and PYTEST_SUMMARY_RE.match(line):
            continue  # a failing file's result: in the run's own line
        if _pytest(line, counts):
            continue
        if line.lstrip().startswith(SKIPPED_TEST_PREFIX):  # test-isolated indents them
            counts.skipped_tests.append(line.lstrip())
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


def step_counts(output: str, status: int, step: str = "") -> Counts:
    """A step's counts, its exit status included.

    Args:
        output: The step's complete output.
        status: Its exit status.
        step: Its name (``test-isolated`` reports its own counts).

    Returns:
        The counts; a failing step always counts at least one failure.
    """
    counts = parse(output, isolated=step == "test-isolated")
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
        gate: The gate's name.
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
    skip: Mapping[str, str] | None = None,
) -> int:
    """Run a gate's steps quietly.

    Args:
        gate: The gate's name (``lint``, ``test``, ``docs``, or ``order`` for the random
            order and the isolated files in ``make gate``).
        steps: Its steps, make targets run one by one after setup.
        make: The make command (``$(MAKE)``).
        log_dir: Parent of the gate's log directory.
        environ: The environment (default: this process's).
        out: Where the gate writes (default: standard output).
        skip: Steps not to run, each with why (the local gate's); every one
            is named below the gate's line.

    Returns:
        0 when every step passed, 1 otherwise.
    """
    out = sys.stdout if out is None else out
    env = dict(os.environ if environ is None else environ)
    skip = dict(skip or {})
    names = [s for s in steps if s not in skip]
    skipped = [f"skipped step: {gate}/{s} ({why})" for s, why in skip.items()]
    base = [*shlex.split(make), "--no-print-directory"]
    if dry_run(env.get("MAKEFLAGS", "")):
        for step in [SETUP, *names]:
            extra = [] if step == SETUP else ["GATE_SETUP_DONE=1"]
            out.write(shlex.join([*base, *extra, step]) + "\n")
        for line in skipped:
            out.write(f"  {line}\n")
        return 0
    logs = log_dir / gate
    shutil.rmtree(logs, ignore_errors=True)
    logs.mkdir(parents=True)
    env["PYTEST_ADDOPTS"] = f"{env.get('PYTEST_ADDOPTS', '')} --tb=long".strip()
    status, output = _run([*base, SETUP], logs / f"{SETUP}.log", env)
    if status != 0:
        _show(out, f"{gate}/{SETUP}", status, logs / f"{SETUP}.log", output)
        out.write(summary(gate, Counts(failed=1), 0, logs, [SETUP]) + "\n")
        for line in skipped:
            out.write(f"  {line}\n")
        return 1
    total = Counts(warnings=parse(output).warnings)
    failing = []
    for step in names:
        log = logs / f"{step}.log"
        status, output = _run([*base, "GATE_SETUP_DONE=1", step], log, env)
        counts = step_counts(output, status, step)
        total.add(counts)
        if status != 0 or counts.failed:
            failing.append(step)
            _show(out, f"{gate}/{step}", status, log, output)
    out.write(summary(gate, total, len(names), logs, failing) + "\n")
    for line in [*skipped, *total.skipped_tests]:
        out.write(f"  {line}\n")
    return 1 if failing else 0
