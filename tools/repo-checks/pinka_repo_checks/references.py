"""References lint: the repository refers to no working material kept outside it.

The project's maintainers keep their working records, specifications and plans
outside this repository. Its files stand on their own: no file, comment or test
points to that material, cites its sections, or names the steps of its plans,
its decision identifiers or who decided what. The lint scans every file git
tracks or would track (untracked files that are not ignored included) for the
patterns of :data:`PATTERNS` and reports every match that ``references.json``
does not allow, each allowed match with its reason. An allowed entry covers a
match only when the match lies inside the entry's text on the line.

It reads the files git lists: the tracked ones (also those that match
``.gitignore``) and the untracked ones git does not ignore. Of these it skips
the files of :data:`EXCLUDED`, each with its reason; anything in the
third-party trees (:mod:`pinka_repo_checks.trees`), which no check reads;
symbolic links, which it does not follow (a link's target is read only when it
is itself such a file); what is not a regular file in the work tree (a tracked
file deleted there, a submodule); and binary files, those with a NUL byte
anywhere, which carry no text to cite (UTF-16 and UTF-32 texts have NUL bytes
too). Every other file is read as UTF-8, or as Latin-1 when it is not UTF-8
(every byte is a character there), so a text in a single-byte encoding is
still read; lines end at a line feed only.
It also reads ``docs/contracts.md`` for the identifiers its Resolutions table
defines. A file it cannot read, a git that cannot list the files and a
malformed ``references.json`` stop it with an error.

While matches remain, their number must equal the budget in ``references.json``:
more fails (a new reference), fewer fails until the budget is lowered to the
count. Its configuration decides what it sees: the budget and the allowlist,
the skips above, :data:`EXCLUDED`, :data:`CONVENTIONAL`, :data:`PATTERNS`, and
the contract's Resolutions table (an identifier the table defines is exempt in
parentheses and in lists everywhere). A change to any of them, or a file moved
where the lint does not read, can let a reference pass.
:mod:`pinka_repo_checks.references_budget` fails when the budget goes up or an
allowlist entry, an :data:`EXCLUDED` path, a :data:`CONVENTIONAL` name or a
Resolutions identifier is added, compared with the parent commits; review of
changes to the rest keeps the budget from going up.
Within the budget the lint passes with one warning; at zero it prints none.

Every pattern is assembled so that this file does not match it. This module
uses the Python standard library only.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pinka_repo_checks.trees import in_third_party_tree

DATA = Path(__file__).parent / "references.json"
# The contract, relative to the repository root: its Resolutions table.
CONTRACT = Path("docs") / "contracts.md"
SECTION_SIGN = chr(0xA7)  # the section sign
# All-caps Markdown files that are ordinary in any repository.
CONVENTIONAL = frozenset(
    {"README", "CONTRIBUTING", "SECURITY", "LICENSE", "CHANGELOG", "CODE_OF_CONDUCT", "NOTICE"}
)
# Files the lint does not read, and why.
EXCLUDED: tuple[tuple[str, str], ...] = (
    (
        "tools/repo-checks/pinka_repo_checks/references.json",
        "the lint's own allowlist, which quotes what it allows",
    ),
)
_KEPT_FILES = (
    r"\b(?:BUILD" r"_PROMPT|DOCS" r"_SPEC|IMPLEMENTATION" r"_NOTES|PROG" r"RESS(?:-ARCHIVE)?)\b"
)
PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("kept-file", re.compile(_KEPT_FILES), "names a file kept outside the repository"),
    (
        "instruction-file",
        re.compile(r"\b([A-Z][A-Z0-9_]*(?:-[A-Z0-9_]+)*)\.md\b"),
        "names an all-caps Markdown file that is not a conventional one",
    ),
    (
        "section-citation",
        re.compile(SECTION_SIGN + r"+\s?(?:\d|[A-Z]\.\d)"),
        "cites a section of a specification",
    ),
    (
        "spec-name",
        re.compile(
            r"\bbuild"
            r"\s+prompts?\b"
            r"|\bdocs?"
            r"[\s-]+spec(?:ification)?s?\b"
            r"|\bdocumentation"
            r"\s+specifications?\b"
            r"|\bqa"
            r"[\s-]+spec(?:ification)?s?\b"
            r"|\breal-machine"
            r"\s+tests?\s+\d"
            r"|(?<![\w./-])spec"
            r"s/"
            r"|\b[\w-]+-"
            r"(?:prompt|specification|spec)\.md\b",
            re.IGNORECASE,
        ),
        "names a specification kept outside the repository",
    ),
    (
        "plan-id",
        re.compile(
            r"\bplan"
            r"\s+(?:items?|steps?)\b"
            r"|\binfra"
            r"\s+steps?\s+\d"
            # The digit 3 and a letter from a to h, with a sub-step after one or
            # more dots, a hyphen or a digit, and a letter suffix. After the
            # letter e neither a digit, a sign nor a hyphenated number: that is
            # a number in scientific notation.
            r"|(?<![\w.-])3(?:[a-df-h](?:\d|(?:\.\d+)+[a-z]?|-\d+)?(?:-[a-z]{1,2})?(?![\w/]|\.\w)"
            r"|e(?:(?:\.\d+)+[a-z]?)?(?:-[a-z]{1,2})?(?![\w/+]|[.-]\w))"
            r"|\bsection"
            r"\s+J\b"
        ),
        "names a step of a plan",
    ),
    (
        "decision-id",
        re.compile(
            r"\bapproved"
            r"\s+deviations?\b"
            r"|\bdeviations?"
            r"\s+[A-Z]{1,3}\d+[a-z]?\b"
            r"|\binterpretations?"
            r"\s+for\s+the\s+owner\b"
            # An identifier in parentheses or a list in them, unless the
            # contract's Resolutions table defines it (scan_line's resolutions).
            r"|(?:(?<=\()|(?<=[,;] ))(?P<id>(?:AG|SSH|PL|TE|[ABDEHINPQV])\d{1,2}[a-z]?)(?=\s*[,;)])"
            # An identifier after a word that makes it a decision (after, answer,
            # decision, item), or before the word review.
            r"|\b(?i:after|answers?|decisions?|items?)"
            r"\s+(?:AG|SSH|PL|TE|[ABDEHINPQV])\d{1,2}[a-z]?\b"
            r"|\b(?:AG|SSH|PL|TE|[ABDEHINPQV])\d{1,2}[a-z]?(?:'s)?"
            r"\s+review\b"
        ),
        "names a decision or deviation by its identifier",
    ),
    (
        "attribution",
        re.compile(
            r"\bowner"
            r"(?:'s)?\s+(?:decisions?|answers?|approvals?|items?|choices?)\b"
            r"|(?<![\w.])\("
            r"owner(?:'s)?"
            r"(?=[,:;)]|\s*$|\s+(?:decisions?|answers?|rules?|conditions?|choices?|approvals?|items?)\b)"
            r"|\bthe\s+owner"
            r"\s+(?:approved|decided|chose|asked|answered)\b"
            r'|with\s+the\s+"commit'
            r'"\s+for',
            re.IGNORECASE,
        ),
        "says who decided something",
    ),
    (
        "workflow-term",
        re.compile(r"\bcommit" r"\s+rule\b|\bstop" r"\s+hooks?\b", re.IGNORECASE),
        "names a step of the maintainers' working process",
    ),
)
_MOVED_STEMS = re.compile(_KEPT_FILES)


@dataclass(frozen=True)
class Finding:
    """One reference to working material kept outside the repository."""

    path: str
    line: int
    pattern: str
    text: str
    line_text: str
    column: int = 0

    def __str__(self) -> str:
        """Render the finding for the terminal.

        Returns:
            ``path:line: pattern: matched text``.
        """
        # A file name that is not UTF-8 (os.fsdecode) prints with escapes.
        path = self.path.encode("utf-8", "surrogateescape").decode("utf-8", "backslashreplace")
        return f"{path}:{self.line}: {self.pattern}: {self.text}"


@dataclass(frozen=True)
class Allowed:
    """A match the lint allows: in files matching ``path``, inside ``text`` on a line."""

    path: str
    pattern: str
    text: str
    reason: str

    def covers(self, finding: Finding) -> bool:
        """Tell whether this entry allows the finding: its match lies inside ``text``."""
        if not fnmatch.fnmatch(finding.path, self.path) or finding.pattern != self.pattern:
            return False
        end = finding.column + len(finding.text)
        start = finding.line_text.find(self.text)
        while start != -1:
            if start <= finding.column and end <= start + len(self.text):
                return True
            start = finding.line_text.find(self.text, start + 1)
        return False


def resolution_ids(contracts: str) -> frozenset[str]:
    """The identifiers that the contract's Resolutions table defines.

    They are the contract's own public names, so a reference to one is not a
    reference to working material.

    Args:
        contracts: The text of ``docs/contracts.md``.

    Returns:
        The first-column identifiers of the table, a cell such as ``C2, C3``
        giving both.
    """
    section = contracts.split("\n## Resolutions\n", 1)[1:]
    if not section:
        return frozenset()
    ids: set[str] = set()
    for line in section[0].split("\n## ", 1)[0].splitlines():
        cells = line.strip().strip("|").split("|")
        if line.startswith("| ") and len(cells) > 1:
            ids.update(
                i.strip()
                for i in cells[0].split(",")
                if re.fullmatch(r"[A-Z]+\d+[a-z]?", i.strip())
            )
    return frozenset(ids)


def scan_line(
    line: str, resolutions: frozenset[str] = frozenset()
) -> Iterator[tuple[str, str, int]]:
    """Find the references in one line.

    Args:
        line: The line.
        resolutions: Identifiers the contract defines (:func:`resolution_ids`).

    Yields:
        ``(pattern, matched text, column)`` for every match.
    """
    for name, pattern, _what in PATTERNS:
        for match in pattern.finditer(line):
            if name == "instruction-file":
                stem = match.group(1)
                if stem in CONVENTIONAL or _MOVED_STEMS.fullmatch(stem):
                    continue
            if name == "decision-id" and match.group("id") in resolutions:
                continue
            yield name, match.group(0), match.start()


def scan_text(relative: str, text: str, resolutions: frozenset[str] = frozenset()) -> list[Finding]:
    """Find the references in one file's text.

    Args:
        relative: Path relative to the repository root, reported in findings.
        text: File content.
        resolutions: Identifiers the contract defines (:func:`resolution_ids`).

    Returns:
        One finding per match.
    """
    return [
        Finding(relative, number, name, matched, line, column)
        for number, line in enumerate(text.removesuffix("\n").split("\n"), start=1)
        for name, matched, column in scan_line(line, resolutions)
    ]


def excluded(relative: str) -> str | None:
    """Tell why a path is not read.

    Args:
        relative: Path relative to the repository root, with ``/``.

    Returns:
        The reason, or None if the path is scanned.
    """
    for pattern, reason in EXCLUDED:
        if fnmatch.fnmatch(relative, pattern):
            return reason
    return None


def files(root: Path) -> list[str]:
    """The files git tracks or would track below ROOT, relative to it.

    Args:
        root: The repository root.

    Returns:
        The paths, sorted.

    Raises:
        RuntimeError: git cannot list the files.
    """
    try:
        done = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],  # noqa: S607
            cwd=root,
            capture_output=True,
            check=False,
        )
    except OSError as err:
        raise RuntimeError(f"cannot run git in {root}: {err}") from err
    if done.returncode != 0:
        message = done.stderr.decode(errors="replace").strip() or f"exit status {done.returncode}"
        raise RuntimeError(f"git ls-files failed in {root}: {message}")
    return sorted({os.fsdecode(p) for p in done.stdout.split(b"\0") if p})


def scan(root: Path) -> list[Finding]:
    """Scan the repository.

    Args:
        root: The repository root.

    Returns:
        Every finding, allowed ones included.
    """
    contracts = root / CONTRACT
    resolutions: frozenset[str] = frozenset()
    try:
        if contracts.is_file():
            resolutions = resolution_ids(contracts.read_text(encoding="utf-8", errors="replace"))
    except OSError as err:
        raise RuntimeError(f"cannot read {CONTRACT.as_posix()}: {err}") from err
    findings: list[Finding] = []
    for relative in files(root):
        path = root / relative
        if excluded(relative) or in_third_party_tree(Path(relative)):
            continue
        if not path.is_file() or path.is_symlink():
            continue
        try:
            data = path.read_bytes()
        except OSError as err:
            raise RuntimeError(f"cannot read {relative}: {err}") from err
        if b"\0" in data:
            continue  # binary: no text to cite
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
        findings += scan_text(relative, text, resolutions)
    return findings


def load(data: Mapping[str, Any]) -> tuple[int, list[Allowed]]:
    """The budget and the allowlist of ``references.json``.

    Args:
        data: The parsed file.

    Returns:
        ``(budget, allowed)``.

    Raises:
        ValueError: A bad budget; an entry with other keys than path, pattern,
            text and reason, a field that is not a string, a blank text or
            reason, an unknown pattern, or the same path, pattern and text as
            an earlier entry.
        KeyError: A missing budget, allowlist or field.
        TypeError: An allowlist or an entry of the wrong type.
    """
    budget = data["budget"]
    if not isinstance(budget, int) or isinstance(budget, bool) or budget < 0:
        raise ValueError(f"budget must be a non-negative integer, not {budget!r}")
    names = {name for name, _pattern, _what in PATTERNS}
    allowed: list[Allowed] = []
    for entry in data["allowed"]:
        if set(entry) - {"path", "pattern", "text", "reason"}:
            raise ValueError(f"allowed entry {entry}: unknown keys")
        fields = [entry["path"], entry["pattern"], entry["text"], entry["reason"]]
        if not all(isinstance(field, str) for field in fields):
            raise ValueError(f"allowed entry {entry}: every field must be a string")
        item = Allowed(*fields)
        if any(
            (a.path, a.pattern, a.text) == (item.path, item.pattern, item.text) for a in allowed
        ):
            raise ValueError(f"allowed entry {entry}: listed twice")
        if item.pattern not in names:
            raise ValueError(f"allowed entry {entry}: unknown pattern {item.pattern!r}")
        if not item.reason.strip() or not item.text.strip():
            raise ValueError(f"allowed entry {entry}: needs a text and a reason")
        allowed.append(item)
    return budget, allowed


@dataclass(frozen=True)
class Result:
    """What the lint found against its budget."""

    remaining: list[Finding]
    allowed: list[Finding]
    stale: list[Allowed]
    budget: int

    @property
    def problems(self) -> list[str]:
        """Why the lint fails; empty when it passes."""
        out = [
            f"allowed entry no longer matches: {a.path} {a.pattern} {a.text!r}" for a in self.stale
        ]
        count = len(self.remaining)
        if count > self.budget:
            out.append(
                f"{count} reference(s), more than the budget of {self.budget}: remove the new ones"
            )
        elif count < self.budget:
            out.append(
                f"{count} reference(s), fewer than the budget of {self.budget}:"
                f" lower the budget in references.json to {count}"
            )
        return out


def check(findings: list[Finding], budget: int, allowed: list[Allowed]) -> Result:
    """Sort the findings into allowed and remaining ones.

    Args:
        findings: Every finding.
        budget: The number of remaining findings the repository may have.
        allowed: The allowlist.

    Returns:
        The result.
    """
    used: set[Allowed] = set()
    remaining, covered = [], []
    for finding in findings:
        entry = next((a for a in allowed if a.covers(finding)), None)
        if entry is None:
            remaining.append(finding)
        else:
            used.add(entry)
            covered.append(finding)
    stale = [a for a in allowed if a not in used]
    return Result(remaining, covered, stale, budget)


def run(root: Path, data_path: Path = DATA) -> int:
    """Run the lint and print every finding.

    Args:
        root: The repository root.
        data_path: The budget and allowlist file.

    Returns:
        0 within the budget, 1 otherwise.
    """
    try:
        budget, allowed = load(json.loads(data_path.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError) as err:
        print(f"references: {data_path.name}: {type(err).__name__}: {err}", file=sys.stderr)
        return 1
    try:
        findings = scan(root)
    except RuntimeError as err:
        print(f"references: {err}", file=sys.stderr)
        return 1
    result = check(findings, budget, allowed)
    for finding in result.remaining:
        print(finding)
    for finding in result.allowed:
        entry = next(a for a in allowed if a.covers(finding))
        print(f"{finding} (allowed: {entry.reason})")
    for problem in result.problems:
        print(f"references: {problem}", file=sys.stderr)
    if result.problems:
        return 1
    if result.remaining:
        kinds = Counter(f.pattern for f in result.remaining)
        summary = ", ".join(f"{name} {n}" for name, n in sorted(kinds.items()))
        print(
            f"warning: references: {len(result.remaining)} reference(s) remain, within the budget"
            f" ({summary}); {len(result.allowed)} allowed"
        )
        return 0
    print(
        "references: no references to material kept outside the repository;"
        f" {len(result.allowed)} allowed"
    )
    return 0
