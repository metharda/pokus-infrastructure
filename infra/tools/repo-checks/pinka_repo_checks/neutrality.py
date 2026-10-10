"""Neutrality lint: infra's code names no particular platform (BUILD_PROMPT §11, §12).

Infra deploys any platform through contract v2, so its roles, tools and
templates must not name the Pokus Platform, its services or its units. The
lint scans every file of the repository that git tracks or would track
(untracked files that are not ignored included) and reports, with the lists
of ``neutrality.json``:

* the Pokus Platform's name (``substring``) anywhere, in any case;
* its service and unit names (``tokens``) as whole tokens: a token is a run of
  letters, digits, ``_``, ``.`` and ``-``, so a listed name inside a longer
  name (a file name with dots, an identifier with underscores) never
  matches; a sentence's trailing dot is not part of a token, and HTML
  entities are not tokens;
* the project name (``project``) outside the allowed forms: the repository
  names and the display names (``allowed``, ``display``).

Test fixtures, the documentation and the reserved-name list are excluded
(§11), and each further exclusion in :data:`EXCLUDED` carries its reason. The
allowed forms apply to this lint over repository code only: target images, the
platform's trace-cleanup scan and its ticket lint allow none of these names.

This module uses the Python standard library only.
"""

from __future__ import annotations

import fnmatch
import json
import re
import subprocess
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

LISTS = json.loads((Path(__file__).parent / "neutrality.json").read_text(encoding="utf-8"))
FORBIDDEN_SUBSTRING: str = LISTS["substring"]
FORBIDDEN_TOKENS = frozenset(LISTS["tokens"])
PROJECT: str = LISTS["project"]
PROJECT_ALLOWED: tuple[str, ...] = tuple(LISTS["allowed"])
PROJECT_DISPLAY: str = LISTS["display"]
# Excluded paths (fnmatch patterns relative to the repository root) and why.
EXCLUDED: tuple[tuple[str, str], ...] = (
    ("docs/*", "documentation (§11)"),
    ("*.md", "documentation (§11): READMEs and notes"),
    ("*/tests/fixtures/*", "test fixtures (§11)"),
    ("*/tests/golden/*", "test fixtures (§11): expected output of the fixtures"),
    ("*/__snapshots__/*", "test fixtures (§11): expected screens"),
    ("contract/reserved-names.yml", "the reserved-name list (§11)"),
    ("contract/platform-manifest.schema.json", "the reserved-name list (§11), generated into it"),
    (
        "ansible/roles/images/defaults/main.yml",
        "the leakage lint's patterns, which must name the project and the platform (§7.13)",
    ),
    (
        "ansible/roles/images/meta/argument_specs.yml",
        "the leakage lint's default patterns, which must name the project and the platform (§7.13)",
    ),
    ("project-words.txt", "the spelling dictionary of the documentation"),
    ("tools/repo-checks/pinka_repo_checks/neutrality.json", "the neutrality lint's own lists"),
    (
        "tools/repo-checks/pinka_repo_checks/schema/front-matter.schema.json",
        "the documentation specification's front matter, shared by every Pokus repository",
    ),
    ("*.lock", "generated lock files: third-party names and hashes"),
    ("package-lock.json", "generated lock file: third-party names and hashes"),
    (
        "installer/*",
        "until plan step 3d.7: the installer's sources screen and answers schema still have "
        "contract v1's components of the release and its content (BUILD_PROMPT §16 step 21)",
    ),
    (
        "tests/answers/*",
        "until plan step 3d.7: answers files of the installer's contract v1 schema",
    ),
)
# Lines that may name the project or the platform, by file: a line that is
# exactly one of the listed quoted words (``"word",``); the reasons are in
# neutrality.json.
ALLOWED_LINES: dict[str, re.Pattern[str]] = {
    path: re.compile(r'^\s*"(?:' + "|".join(map(re.escape, entry["words"])) + r')",\s*$')
    for path, entry in LISTS["allowed_lines"].items()
}
_TOKEN = re.compile(r"&[A-Za-z]+;|[A-Za-z0-9_.-]+")
_PROJECT = re.compile(re.escape(PROJECT), re.IGNORECASE)
_WORD_CHAR = re.compile(r"[A-Za-z0-9_-]")


@dataclass(frozen=True)
class Finding:
    """One platform-specific name in infra's code."""

    path: Path
    line: int
    what: str

    def __str__(self) -> str:
        """Render the finding for the terminal.

        Returns:
            ``path:line: message``.
        """
        return (
            f"{self.path}:{self.line}: names a particular platform ({self.what}); infra "
            "takes every platform name from the manifest (BUILD_PROMPT §11, §12)"
        )


def excluded(relative: str) -> str | None:
    """Tell why a path is excluded.

    Args:
        relative: Path relative to the repository root, with ``/``.

    Returns:
        The reason, or None if the path is scanned.
    """
    for pattern, reason in EXCLUDED:
        if fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(f"./{relative}", pattern):
            return reason
    return None


def _project_allowed(line: str, start: int) -> bool:
    for form in PROJECT_ALLOWED:
        offset = form.lower().find(PROJECT)
        begin = start - offset
        if begin >= 0 and line[begin : begin + len(form)] == form:
            after = line[begin + len(form) : begin + len(form) + 1]
            before = line[begin - 1 : begin] if begin else ""
            if not _WORD_CHAR.match(after or " ") and not _WORD_CHAR.match(before or " "):
                return True
    after = line[start + len(PROJECT_DISPLAY) : start + len(PROJECT_DISPLAY) + 1]
    before = line[start - 1 : start] if start else ""
    return (
        line[start : start + len(PROJECT_DISPLAY)] == PROJECT_DISPLAY
        and not _WORD_CHAR.match(after or " ")
        and not _WORD_CHAR.match(before or " ")
    )


def scan_line(line: str) -> list[str]:
    """Find platform-specific names in one line.

    Args:
        line: The line.

    Returns:
        What was found, one entry per match.
    """
    found = []
    if FORBIDDEN_SUBSTRING in line.lower():
        found.append(FORBIDDEN_SUBSTRING)
    for match in _TOKEN.finditer(line):
        token = match.group(0)
        if token.startswith("&"):
            continue
        if token.rstrip(".").lower() in FORBIDDEN_TOKENS:
            found.append(token.rstrip("."))
    for match in _PROJECT.finditer(line):
        if not _project_allowed(line, match.start()):
            found.append(PROJECT)
    return found


def scan_text(path: Path, text: str, relative: str = "") -> list[Finding]:
    """Find platform-specific names in one file's text.

    Args:
        path: Path reported in findings.
        text: File content.
        relative: Path relative to the repository root (for the allowed lines).

    Returns:
        One finding per match.
    """
    allowed = ALLOWED_LINES.get(relative)
    findings = []
    for number, line in enumerate(text.splitlines(), start=1):
        if allowed is not None and allowed.match(line):
            continue
        findings += [Finding(path, number, what) for what in scan_line(line)]
    return findings


def _git_files(root: Path) -> list[str] | None:
    try:
        done = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],  # noqa: S607
            cwd=root,
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    if done.returncode != 0:
        return None
    return sorted({p for p in done.stdout.decode().split("\0") if p})


_SKIP_DIRS = {".git", ".venv", "node_modules", "site", "build", "__pycache__", ".pytest_cache"}


def _walk(root: Path) -> list[str]:
    out = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in _SKIP_DIRS or part.startswith(".ansible") for part in relative.parts):
            continue
        if path.is_file():
            out.append(relative.as_posix())
    return out


def files(root: Path) -> Iterator[tuple[Path, str]]:
    """The files the lint scans.

    Args:
        root: The repository (infra) root.

    Yields:
        ``(path, relative)`` for every scanned file.
    """
    listed = _git_files(root)
    for relative in listed if listed is not None else _walk(root):
        path = root / relative
        if excluded(relative) is None and path.is_file() and not path.is_symlink():
            yield path, relative


def scan(root: Path) -> list[Finding]:
    """Scan the repository.

    Args:
        root: The repository (infra) root.

    Returns:
        Every finding.
    """
    findings: list[Finding] = []
    for path, relative in files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # binary files carry no names
        findings += scan_text(path.relative_to(root), text, relative)
    return findings


def run(root: Path) -> int:
    """Run the lint and print every finding.

    Args:
        root: The repository (infra) root.

    Returns:
        0 when infra is neutral, 1 otherwise.
    """
    findings = scan(root)
    for finding in findings:
        print(finding, file=sys.stderr)
    if findings:
        print(f"neutrality: {len(findings)} finding(s)", file=sys.stderr)
        return 1
    print("neutrality: no platform-specific names in infra's code")
    return 0


def exclusions() -> Sequence[tuple[str, str]]:
    """The excluded paths with their reasons (for the documentation)."""
    return EXCLUDED
