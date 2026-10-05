"""Shared helpers for the preflight tests (standard library only)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from collections.abc import Mapping
from typing import Any

from pokus_preflight.fs import FileSystem
from pokus_preflight.runner import FixtureRunner

HERE = os.path.dirname(os.path.abspath(__file__))
PREFLIGHT_DIR = os.path.dirname(HERE)
FIXTURES_DIR = os.path.join(HERE, "fixtures")
SNAPSHOTS_DIR = os.path.join(HERE, "snapshots")


def runner(commands: Mapping[str, Any] | None = None, **data: Any) -> FixtureRunner:
    """Build a fixture runner.

    Args:
        commands: Command line to stdout text, or to ``(stdout, rc)``.
        **data: Other ``commands.json`` sections.

    Returns:
        The runner.
    """
    table: dict[str, dict[str, Any]] = {}
    for line, answer in (commands or {}).items():
        if isinstance(answer, tuple):
            table[line] = {"stdout": answer[0], "rc": answer[1]}
        else:
            table[line] = {"stdout": answer, "rc": 0}
    return FixtureRunner({"commands": table, **data})


class TempRootCase(unittest.TestCase):
    """Test case with a temporary fixture root."""

    def setUp(self) -> None:
        """Create the root."""
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name

    def tearDown(self) -> None:
        """Remove the root."""
        self._tmp.cleanup()

    def put(self, path: str, content: str | bytes = "") -> None:
        """Create a file below the root.

        Args:
            path: Target path such as ``/etc/os-release``.
            content: File content.
        """
        target = os.path.join(self.root, path.lstrip("/"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        mode = "wb" if isinstance(content, bytes) else "w"
        with open(target, mode) as handle:
            handle.write(content)

    def mkdir(self, path: str) -> None:
        """Create a directory below the root.

        Args:
            path: Target path.
        """
        os.makedirs(os.path.join(self.root, path.lstrip("/")), exist_ok=True)

    def meta(self, **meta: Any) -> None:
        """Write ``fixture.json``.

        Args:
            **meta: Metadata keys.
        """
        with open(os.path.join(self.root, "fixture.json"), "w", encoding="utf-8") as handle:
            json.dump(meta, handle)

    def fs(self) -> FileSystem:
        """Return a filesystem view of the root."""
        return FileSystem(self.root)
