"""unittest with the CI skip list: the Python 3.9 runs of ``make test-py39``.

``python -m pinka_repo_checks.unittest_run --prefix <dir> discover -s <start>
-t <top>`` or ``... --prefix <dir> <module> ...`` runs the tests like ``python
-m unittest`` (the same output, which the gate counts), then names every
skipped test with its reason (:func:`pinka_repo_checks.ci_skips.line`). Test
ids take the form of pytest's (``<dir>/<module>.py::<Class>::<test>``), so the
CI skip list names a test the same way for both runs. In CI (``CI=true``) a
skip that the list does not name for the ``py39`` run fails the run.

Standard library only and source-compatible with Python 3.9.
"""

from __future__ import annotations

import argparse
import sys
import unittest
from collections.abc import Sequence
from pathlib import Path

from pinka_repo_checks import ci_skips

RUN = "py39"


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
    parser.add_argument(
        "names", nargs=argparse.REMAINDER, help="discover -s START -t TOP, or test names"
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if not args.names:
        parser.error("name the tests: discover -s START -t TOP, or test names")
    result = unittest.TextTestRunner(verbosity=1).run(load(args.names))
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
