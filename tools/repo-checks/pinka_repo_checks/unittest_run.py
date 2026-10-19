"""unittest with the CI skip list: the Python 3.9 runs of ``make test-py39``.

``python -m pinka_repo_checks.unittest_run --prefix <dir> discover -s <start>
-t <top>`` or ``... --prefix <dir> <module> ...`` runs the tests like ``python
-m unittest`` (the same output, which the gate counts), then names every
skipped test with its reason (:func:`pinka_repo_checks.ci_skips.line`). Test
ids take the form of pytest's (``<dir>/<module>.py::<Class>::<test>``), so the
CI skip list names a test the same way for both runs. In CI (``CI=true``) a
skip that the list does not name for the ``py39`` run fails the run.

``--junit-xml <file>`` also writes every test's result as JUnit XML, with the
class names and test names pytest's ``--junitxml`` gives the same test, so CI
keeps each test's name and result as an artifact.

Standard library only and source-compatible with Python 3.9.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
import unittest
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pinka_repo_checks import ci_skips

RUN = "py39"
FIXTURE_RE = re.compile(r"^(?P<fixture>\w+) \((?P<owner>[\w.]+)\)$")


def test_id(test: unittest.TestCase, prefix: str) -> str:
    """A test's id in pytest's form.

    Args:
        test: The test (a subtest counts as its test).
        prefix: The test directory relative to the repository root.

    Returns:
        ``<prefix>/<module path>.py::<Class>::<method>``.
    """
    case = getattr(test, "test_case", test)
    method = getattr(case, "_testMethodName", None)
    if method is None:
        return f"{prefix}::{case.id()}"
    cls = type(case)
    module = cls.__module__.replace(".", "/")
    return f"{prefix}/{module}.py::{cls.__qualname__}::{method}"


def junit_names(test: unittest.TestCase, prefix: str) -> tuple[str, str]:
    """A test's class name and name in the form of pytest's JUnit XML.

    Args:
        test: The test (a subtest counts as its test), or the holder unittest
            makes for a failed class or module fixture
            (``setUpClass (module.Class)``).
        prefix: The test directory relative to the repository root.

    Returns:
        ``(<prefix with dots>.<module>.<Class>, <method>)``, like pytest's
        ``classname`` and ``name``; for a fixture, its class or module and the
        fixture's name.
    """
    case = getattr(test, "test_case", test)
    dotted = prefix.replace("/", ".")
    if getattr(case, "_testMethodName", None) is None:
        match = FIXTURE_RE.match(case.id())
        if match is None:
            return dotted, case.id()
        return f"{dotted}.{match.group('owner')}", match.group("fixture")
    cls = type(case)
    return f"{dotted}.{cls.__module__}.{cls.__qualname__}", str(case._testMethodName)


def flatten(suite: unittest.TestSuite) -> list[unittest.TestCase]:
    """The tests of a suite, in order (before it runs: a run empties it)."""
    tests: list[unittest.TestCase] = []
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            tests += flatten(item)
        else:
            tests.append(item)
    return tests


class TimedResult(unittest.TextTestResult):
    """The text result, with each test's duration."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401 - durations from 3.12
        """Like :class:`unittest.TextTestResult` (Python 3.12 adds ``durations``)."""
        super().__init__(*args, **kwargs)
        self.seconds: dict[str, float] = {}
        self._start = 0.0

    def startTest(self, test: unittest.TestCase) -> None:  # noqa: N802 - unittest's name
        """Note the start."""
        self._start = time.monotonic()
        super().startTest(test)

    def stopTest(self, test: unittest.TestCase) -> None:  # noqa: N802 - unittest's name
        """Note the duration."""
        self.seconds[test.id()] = time.monotonic() - self._start
        super().stopTest(test)


def junit(
    tests: Sequence[unittest.TestCase], result: TimedResult, prefix: str, seconds: float
) -> ET.ElementTree:
    """The run as JUnit XML, in the form of pytest's ``--junitxml``.

    Args:
        tests: The suite's tests, in order.
        result: The run's result.
        prefix: The test directory relative to the repository root.
        seconds: How long the run took.

    Returns:
        ``<testsuites><testsuite>`` with one ``<testcase>`` per test. An error
        outside a test (a class or module fixture) is a test case of its own,
        and a test that never started because of it is an error, not a pass.
    """
    outcomes: dict[str, list[tuple[str, str, str]]] = {}
    extra: list[unittest.TestCase] = []
    known = {test.id() for test in tests}

    def note(test: unittest.TestCase, kind: str, message: str, text: str) -> None:
        key = getattr(test, "test_case", test).id()
        if key not in known and key not in outcomes:
            extra.append(test)
        outcomes.setdefault(key, []).append((kind, message, text))

    for test, text in result.errors:
        # Like pytest: an exception in a test is a failure, one in a fixture an error.
        in_test = getattr(getattr(test, "test_case", test), "_testMethodName", None) is not None
        kind = "failure" if in_test else "error"
        note(test, kind, text.strip().splitlines()[-1] if text.strip() else kind, text)
    for test, text in result.failures:
        note(test, "failure", text.strip().splitlines()[-1] if text.strip() else "failure", text)
    for test, reason in result.skipped:
        note(test, "skipped", reason, "")
    for test, text in result.expectedFailures:
        note(test, "skipped", "expected failure", text)
    for test in result.unexpectedSuccesses:
        note(test, "failure", "unexpected success", "")
    for test in tests:
        if test.id() not in result.seconds and test.id() not in outcomes:
            note(test, "error", "not run: a class or module fixture failed", "")
    suite = ET.Element("testsuite", name=f"{RUN} {prefix}", time=f"{seconds:.3f}")
    counts = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for test in [*tests, *extra]:
        classname, name = junit_names(test, prefix)
        case = ET.SubElement(
            suite,
            "testcase",
            classname=classname,
            name=name,
            time=f"{result.seconds.get(test.id(), 0.0):.3f}",
        )
        counts["tests"] += 1
        kinds = outcomes.get(getattr(test, "test_case", test).id(), [])
        for kind, message, text in kinds:
            element = ET.SubElement(case, kind, message=message)
            element.text = text or None
        if kinds:
            worst = {kind for kind, _, _ in kinds}
            key = "errors" if "error" in worst else "failures" if "failure" in worst else "skipped"
            counts[key] += 1
    for key, value in counts.items():
        suite.set(key, str(value))
    root = ET.Element("testsuites")
    root.append(suite)
    return ET.ElementTree(root)


def load(names: Sequence[str]) -> unittest.TestSuite:
    """The suite of ``discover -s START -t TOP`` or of test names.

    Args:
        names: Like ``python -m unittest``'s arguments.

    Returns:
        The suite.
    """
    loader = unittest.TestLoader()
    if names and names[0] == "discover":
        parser = argparse.ArgumentParser(prog="unittest_run discover")
        parser.add_argument("-s", dest="start", default=".")
        parser.add_argument("-t", dest="top", default=None)
        args = parser.parse_args(list(names[1:]))
        return loader.discover(args.start, top_level_dir=args.top)
    return loader.loadTestsFromNames(list(names))


def main(argv: Sequence[str] | None = None) -> int:
    """Run the tests.

    Args:
        argv: ``--prefix DIR`` and the test selection.

    Returns:
        0 when the tests passed and, in CI, every skip is on the list.
    """
    parser = argparse.ArgumentParser(prog="unittest_run")
    parser.add_argument("--prefix", required=True, help="the test directory, from the root")
    parser.add_argument("--skip-list", type=Path, default=ci_skips.LIST, help="the CI skip list")
    parser.add_argument("--junit-xml", type=Path, help="also write every result as JUnit XML")
    parser.add_argument(
        "names", nargs=argparse.REMAINDER, help="discover -s START -t TOP, or test names"
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if not args.names:
        parser.error("name the tests: discover -s START -t TOP, or test names")
    suite = load(args.names)
    tests = flatten(suite)
    start = time.monotonic()
    result = unittest.TextTestRunner(verbosity=1, resultclass=TimedResult).run(suite)
    if args.junit_xml is not None:
        assert isinstance(result, TimedResult)  # noqa: S101 - the runner's result class
        args.junit_xml.parent.mkdir(parents=True, exist_ok=True)
        tree = junit(tests, result, args.prefix, time.monotonic() - start)
        tree.write(args.junit_xml, encoding="utf-8", xml_declaration=True)
    skipped = [(test_id(test, args.prefix), reason) for test, reason in result.skipped]
    for test, reason in skipped:
        print(ci_skips.line(test, reason))
    refused = []
    if ci_skips.in_ci():
        allowed = ci_skips.allowed(RUN, args.skip_list)
        refused = [(test, reason) for test, reason in skipped if test not in allowed]
    for test, reason in refused:
        print(f"FAILED: {ci_skips.refusal(test, reason)}")
    return 0 if result.wasSuccessful() and not refused else 1


if __name__ == "__main__":
    sys.exit(main())
