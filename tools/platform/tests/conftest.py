"""Tests that need the pinned uv (V6): the ``uv`` fixture and its marker.

Owner decision with the "commit" for 3d.4b: in CI a skipped uv test fails
the job. Every test that uses the ``uv`` fixture, directly or through
another fixture, carries the ``uv`` marker. The CI skip list
(``pinka_repo_checks.skip_policy``) names no uv test, so in CI (``CI=true``)
a skip of one, whatever its reason, is reported as a failure.
"""

from __future__ import annotations

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
