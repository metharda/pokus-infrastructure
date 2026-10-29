"""The CI skip list: which tests may skip in CI, and why.

``ci_skips.json`` names every test that may skip in CI, with the run it skips
in (``pytest``, or ``py39`` for the unittest runs of ``make test-py39``) and its
reason. In CI (``CI=true``) any other skip fails the job. Every run names each
skipped test with its reason in one line of the form of :func:`line`, locally
too, so the gate and the CI logs list them.

Standard library only and source-compatible with Python 3.9: the unittest runs
use it on CPython 3.9.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path

LIST = Path(__file__).with_name("ci_skips.json")
LINE_PREFIX = "skipped test: "


def allowed(run: str, path: Path = LIST) -> dict[str, str]:
    """The tests that may skip in CI in one run.

    Args:
        run: ``pytest`` or ``py39``.
        path: The list.

    Returns:
        Test id to reason.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(e["test"]): str(e["reason"]) for e in data["skips"] if e["run"] == run}


def in_ci(environ: Mapping[str, str] | None = None) -> bool:
    """Whether this runs in CI (GitHub Actions sets ``CI=true``).

    Args:
        environ: The environment (default: this process's).

    Returns:
        True in CI.
    """
    return (os.environ if environ is None else environ).get("CI") == "true"


def line(test: str, reason: str) -> str:
    """The line that names a skipped test.

    Args:
        test: Its id (``path::Class::test``).
        reason: Why it skipped.

    Returns:
        ``skipped test: <id> (reason: <reason>)``.
    """
    return f"{LINE_PREFIX}{test} (reason: {' '.join(reason.split())})"


def refusal(test: str, reason: str) -> str:
    """The failure message of a skip in CI that the list does not name.

    Args:
        test: Its id.
        reason: Why it skipped.

    Returns:
        The message.
    """
    return (
        f"{test} skipped in CI, and the CI skip list "
        f"(tools/repo-checks/pinka_repo_checks/ci_skips.json) does not name it: {reason}"
    )
