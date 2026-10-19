"""Every pytest file in a process of its own (``make test-isolated``).

The usual run collects every test file into one pytest process, so a test can
pass only because an earlier file left something in the process: a module's
state, a class attribute, a registered representer (the installer's YAML
writers did, until ``yamlio``). This runner collects the suites once, then
runs each collected test file in a pytest process of its own, one after the
other in collection order, so such a test fails here. Every process reads
``pyproject.toml`` like ``make test-pytest`` (the CI skip list included) and
uses no cache (``-p no:cacheprovider``), so nothing passes between them.

A file also fails when it runs a different number of tests alone than in the
whole collection (a parametrization that depends on another file).

Quiet by default: the complete output of every failing file, one line per
skipped test (``skipped test: <id> (reason: <reason>)``), then one line with
the counts; ``--verbose`` prints every file's output. Each file's output is
written to ``<log-dir>/<path>.log``, and with ``--junit-dir`` its JUnit XML to
``<junit-dir>/<path>.xml``.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from pinka_repo_checks import gate

PYTEST = (sys.executable, "-m", "pytest", "-p", "no:cacheprovider")
NODE_RE = re.compile(r"^(?P<path>[^\s:]+\.py)::")


@dataclass
class FileRun:
    """One test file's process.

    Attributes:
        path: The file, as pytest names it.
        expected: How many of its tests the whole collection holds.
        status: The process's exit status.
        output: Its complete output.
        counts: Its counts (:func:`pinka_repo_checks.gate.parse`).
    """

    path: str
    expected: int
    status: int
    output: str
    counts: gate.Counts

    @property
    def ran(self) -> int:
        """How many tests the process reported."""
        return self.counts.passed + self.counts.failed + self.counts.skipped

    @property
    def problem(self) -> str | None:
        """Why the file failed, or None."""
        if self.status != 0:
            return f"exit status {self.status}"
        if self.ran != self.expected:
            return f"ran {self.ran} test(s) alone, {self.expected} in the whole collection"
        return None


def collect(paths: Sequence[str]) -> tuple[dict[str, int], gate.Counts, str]:
    """The test files in collection order, with their numbers of tests.

    Args:
        paths: What to collect (pytest's testpaths when empty).

    Returns:
        The files and their numbers of tests, the collection's skips (a module
        skipped as a whole), and the collection's output when it failed
        (empty otherwise).
    """
    done = subprocess.run(  # noqa: S603 - pytest with fixed arguments
        [*PYTEST, "--collect-only", "-q", *paths], capture_output=True, text=True, check=False
    )
    output = done.stdout + done.stderr
    files: dict[str, int] = {}
    for line in done.stdout.splitlines():
        if match := NODE_RE.match(line):
            files[match.group("path")] = files.get(match.group("path"), 0) + 1
    skips = gate.Counts(skipped_tests=gate.parse(output).skipped_tests)
    skips.skipped = len(skips.skipped_tests)
    return files, skips, output if done.returncode != 0 else ""


def run_file(path: str, expected: int, logs: Path, junit: Path | None = None) -> FileRun:
    """Run one test file in a pytest process of its own.

    Args:
        path: The file.
        expected: How many of its tests the whole collection holds.
        logs: The directory of the logs.
        junit: The directory of the JUnit XML files, or None for none.

    Returns:
        The run; its output is also in ``<logs>/<path>.log``.
    """
    report = [] if junit is None else [f"--junitxml={junit / path}.xml"]
    done = subprocess.run(  # noqa: S603 - pytest with fixed arguments
        [*PYTEST, "--tb=long", *report, path],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    log = logs / f"{path}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(done.stdout, encoding="utf-8")
    return FileRun(path, expected, done.returncode, done.stdout, gate.parse(done.stdout))


def summary(
    files: int, counts: gate.Counts, failing: Sequence[str], seconds: float, logs: Path
) -> str:
    """The run's one line.

    Args:
        files: How many files ran.
        counts: The counts of all files.
        failing: The files that failed.
        seconds: How long the run took.
        logs: The directory of the logs.

    Returns:
        The line, without a newline.
    """
    state = "FAILED: " if failing else ""
    where = f"failing: {', '.join(failing)}; " if failing else ""
    return (
        f"test-isolated: {state}{counts.passed} passed, {counts.failed} failed, "
        f"{counts.skipped} skipped, {counts.warnings} warnings "
        f"({files} files, each in a process of its own, in {seconds:.0f}s; {where}logs: {logs})"
    )


def _show(out: TextIO, title: str, output: str) -> None:
    out.write(f"----- {title} -----\n")
    out.write(output if output.endswith("\n") or not output else output + "\n")


def main(argv: Sequence[str] | None = None, out: TextIO = sys.stdout) -> int:
    """Run every collected test file in a process of its own.

    Args:
        argv: ``--log-dir DIR``, ``--junit-dir DIR``, ``--verbose`` and what
            to collect.
        out: Where to write the report.

    Returns:
        0 when every file passed alone.
    """
    parser = argparse.ArgumentParser(prog="isolated")
    parser.add_argument("--log-dir", type=Path, default=Path("build/isolated"))
    parser.add_argument("--junit-dir", type=Path, help="write each file's JUnit XML here")
    parser.add_argument("--verbose", action="store_true", help="print every file's output")
    parser.add_argument("paths", nargs="*", help="what to collect (default: pytest's testpaths)")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    start = time.monotonic()
    files, total, failed_collection = collect(args.paths)
    if failed_collection or not files:
        _show(
            out, "collection failed; complete output", failed_collection or "no tests collected\n"
        )
        return 1
    failing: list[str] = []
    for path, expected in files.items():
        file_run = run_file(path, expected, args.log_dir, args.junit_dir)
        problem = file_run.problem
        if problem is not None:
            failing.append(path)
            file_run.counts.failed = max(file_run.counts.failed, 1)
        total.add(file_run.counts)
        if args.verbose or problem is not None:
            title = f"{path}: {problem or 'passed'}; complete output"
            _show(out, f"{title} ({args.log_dir / path}.log)", file_run.output)
    out.write(summary(len(files), total, failing, time.monotonic() - start, args.log_dir) + "\n")
    for line in total.skipped_tests:
        out.write(f"  {line}\n")
    return 1 if failing else 0


if __name__ == "__main__":
    sys.exit(main())
