"""Portability check: only ``platform_facts`` may branch on the distribution.

Distribution differences live in the support matrix,
the component source matrix, per-family ``vars/<family>.yml`` files and small
``tasks/<family>.yml`` files selected through the facts ``platform_facts``
sets. No other role may read Ansible's distribution facts directly. This
check enforces that for every playbook, role, template and plugin under the
Ansible directory, comments included (a commented-out branch is one edit away
from being live).

Flagged: ``ansible_distribution*``, ``ansible_os_family``, ``ansible_pkg_mgr``
and ``ansible_lsb``, written as injected variables or read from
``ansible_facts`` (``ansible_facts.distribution``,
``ansible_facts['os_family']``, ``hostvars[...]['ansible_facts']['pkg_mgr']``).

This module uses the Python standard library only.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

SUFFIXES = (".yml", ".yaml", ".j2", ".py")
# Relative to the Ansible directory.
DEFAULT_ALLOWED = ("roles/platform_facts/", "filter_plugins/")
FACT_RE = re.compile(
    r"\bansible_(?:facts(?:['\"]\])?\s*(?:\.|\[\s*['\"]))?"
    r"(?P<fact>distribution\w*|os_family|pkg_mgr|lsb)\b"
)


@dataclass(frozen=True)
class Finding:
    """One use of a distribution fact outside the allowed places."""

    path: Path
    line: int
    fact: str

    def __str__(self) -> str:
        """Render the finding for the terminal.

        Returns:
            ``path:line: message``.
        """
        return (
            f"{self.path}:{self.line}: reads the distribution fact '{self.fact}'; "
            "use the pinka_* facts of platform_facts instead"
        )


def scan_text(path: Path, text: str) -> list[Finding]:
    """Find distribution facts in one file's text.

    Args:
        path: Path reported in findings.
        text: File content.

    Returns:
        One finding per match.
    """
    return [
        Finding(path, number, match.group("fact"))
        for number, line in enumerate(text.splitlines(), start=1)
        for match in FACT_RE.finditer(line)
    ]


def _files(root: Path, allowed: Sequence[str]) -> Iterator[Path]:
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if not path.is_file() or path.suffix not in SUFFIXES:
            continue
        if any(relative.startswith(prefix) for prefix in allowed):
            continue
        yield path


def scan(root: Path, allowed: Sequence[str] = DEFAULT_ALLOWED) -> list[Finding]:
    """Scan an Ansible directory.

    Args:
        root: The Ansible directory (contains ``roles/``).
        allowed: Path prefixes, relative to ``root``, that may read the facts.

    Returns:
        All findings, in path order.
    """
    findings: list[Finding] = []
    for path in _files(root, allowed):
        findings += scan_text(path, path.read_text(encoding="utf-8"))
    return findings


def run(root: Path, allowed: Sequence[str] = DEFAULT_ALLOWED) -> int:
    """Run the check and print the findings.

    Args:
        root: The Ansible directory.
        allowed: Path prefixes that may read the facts.

    Returns:
        0 without findings, 1 otherwise, 2 if ``root`` is not a directory.
    """
    if not root.is_dir():
        sys.stderr.write(f"portability: {root} is not a directory\n")
        return 2
    findings = scan(root, allowed)
    for finding in findings:
        print(finding)
    print(f"portability: {len(findings)} finding(s)", file=sys.stderr)
    return 1 if findings else 0
