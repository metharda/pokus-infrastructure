"""Every action a workflow uses is pinned to a full commit SHA, its version in a comment."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = ROOT / ".github" / "workflows"
USES_RE = re.compile(r"^\s*(?:-\s+)?uses:\s*(?P<ref>\S+)(?P<rest>.*)$")
PINNED_RE = re.compile(r"^[\w.-]+/[\w.-]+(?:/[\w./-]+)?@[0-9a-f]{40}$")
VERSION_RE = re.compile(r"^\s+# v\d+\.\d+\.\d+$")


def test_every_action_is_pinned_to_a_commit_with_its_version() -> None:
    found = []
    for workflow in sorted(WORKFLOWS.glob("*.y*ml")):
        for number, line in enumerate(workflow.read_text(encoding="utf-8").splitlines(), 1):
            match = USES_RE.match(line)
            if match is None:
                continue
            where = f"{workflow.name}:{number}: {line.strip()}"
            found.append(where)
            # A local action or a container would be pinned another way; none is used.
            assert PINNED_RE.match(match.group("ref")), where
            assert VERSION_RE.match(match.group("rest")), where
    assert found  # the workflows use actions, and every one was checked
