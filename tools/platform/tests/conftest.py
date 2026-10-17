"""Tests that need the pinned uv (V6): the ``uv`` fixture and the CI rule.

Owner decision with the "commit" for 3d.4b: in CI a skipped uv test fails
the job. Every test that uses the ``uv`` fixture, directly or through
another fixture, carries the ``uv`` marker, and in CI (``CI=true``) a skip
of such a test, whatever its reason, is reported as a failure.
"""

from __future__ import annotations

import os
from collections.abc import Generator

import pytest
from agent_testlib import uv_binary


@pytest.fixture(scope="session")
def uv() -> str:
    """The uv of ``versions.lock.yml``."""
    return uv_binary()


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Mark every test that needs uv."""
    for item in items:
        if "uv" in getattr(item, "fixturenames", ()):
            item.add_marker(pytest.mark.uv)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    """In CI, report a skipped uv test as failed."""
    report = yield
    if report.skipped and os.environ.get("CI") == "true" and item.get_closest_marker("uv"):
        reason = report.longrepr[2] if isinstance(report.longrepr, tuple) else report.longrepr
        report.outcome = "failed"
        report.longrepr = f"a test that needs uv was skipped in CI: {reason}"
    return report
