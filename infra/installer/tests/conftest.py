"""Fixtures of the installer tests: a copy of the files the installer reads."""

from __future__ import annotations

from pathlib import Path

import pytest
from installer_testlib import copy_repo

from pinka_installer.paths import Paths


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Paths:
    """A copy of the repository files the installer reads, and host paths below tmp_path."""
    for name in ("SUDO_UID", "SUDO_GID"):
        monkeypatch.delenv(name, raising=False)
    return copy_repo(tmp_path)
