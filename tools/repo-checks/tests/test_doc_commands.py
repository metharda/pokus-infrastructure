"""Every documented ``PYTHONPATH=... python -m <package>`` command starts.

Found by the infra self-test (plan item 3e): the command of "How to test the
distributions" lacked ``tools/lockcheck``, which the harness imports, the
same gap the CI of e525a1e showed in the Makefile. Each documented command
line runs here with ``--help`` and its own ``PYTHONPATH``.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
COMMAND = re.compile(r"PYTHONPATH=(\S+) \S*python\S* -m ([A-Za-z_][\w.]*)")


def documented_commands() -> list[tuple[str, str, str]]:
    """(page, PYTHONPATH, module) for every such command of the docs and the README."""
    pages = sorted((ROOT / "docs").rglob("*.md"))
    found = []
    for page in [*pages, ROOT / "README.md"]:
        for match in COMMAND.finditer(page.read_text(encoding="utf-8")):
            found.append((str(page.relative_to(ROOT)), match.group(1), match.group(2)))
    return found


def test_the_docs_have_such_commands() -> None:
    modules = {module for _, _, module in documented_commands()}
    assert {"pinka_platform", "pinka_test_distros"} <= modules


@pytest.mark.parametrize(("page", "path", "module"), documented_commands())
def test_a_documented_command_finds_every_package_it_imports(
    page: str, path: str, module: str
) -> None:
    done = subprocess.run(
        [sys.executable, "-m", module, "--help"],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": path},
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, f"{page}: PYTHONPATH={path} -m {module}: {done.stderr}"
