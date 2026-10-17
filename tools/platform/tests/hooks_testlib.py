"""Shared helpers of the hook tests: a stand-in for systemd-run."""

from __future__ import annotations

import itertools
import json
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pinka_platform import hooks


def properties(argv: Sequence[str]) -> dict[str, list[str]]:
    """The ``--property`` settings of a systemd-run command.

    Args:
        argv: The command.

    Returns:
        Values by property name.
    """
    found: dict[str, list[str]] = {}
    for flag, value in itertools.pairwise(argv):
        if flag == "--property":
            key, _, rest = value.partition("=")
            found.setdefault(key, []).append(rest)
    return found


def command(argv: Sequence[str]) -> list[str]:
    """The unit's command: everything after ``--``.

    Args:
        argv: The systemd-run command.

    Returns:
        The command.
    """
    return list(argv[argv.index("--") + 1 :])


class FakeSystemd:
    """Stands in for systemd-run: writes what the unit would print.

    Attributes:
        line: What the hook prints (None: the unit leaves no result).
        returncode: systemd-run's exit code.
        calls: Every command run.
        timeouts: The caller's timeout of each systemd-run.
        result_path: The last result file.
    """

    def __init__(self, line: str | None = '{"ok": true}', returncode: int = 0) -> None:
        """Create the stand-in.

        Args:
            line: What the hook prints (None: no result).
            returncode: systemd-run's exit code.
        """
        self.line = line
        self.returncode = returncode
        self.calls: list[list[str]] = []
        self.timeouts: list[float] = []
        self.result_path: Path | None = None

    def __call__(self, argv: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        """Run one command."""
        self.calls.append(list(argv))
        if argv[0] == "systemd-run":
            self.timeouts.append(kwargs["timeout"])
            target = properties(argv)["StandardOutput"][0].removeprefix("file:")
            self.result_path = Path(target)
            if self.line is not None:
                hook = command(argv)[3]
                inner = hooks.judge(hook, 0, self.line)
                Path(target).write_text(json.dumps(inner.as_dict()) + "\n", encoding="utf-8")
        return subprocess.CompletedProcess(list(argv), self.returncode, "", "boom")
