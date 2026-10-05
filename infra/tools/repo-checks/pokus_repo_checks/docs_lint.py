"""Documentation page lint (Pokus Documentation Specification §A.2, §A.4, §A.6).

Rules, with stable codes:

* ``FM001``-``FM005``: every page starts with valid front matter (title, type,
  audience, applies_to, last_verified); ``last_verified`` is either fully set
  or fully null (never fabricated).
* ``FM100`` (warning): the page has never been verified.
* ``FM101``: ``last_verified`` is older than the component's latest release
  covered by ``applies_to``. A warning in normal runs, an error with
  ``--release``.
* ``CB001``: every fenced code block has a language tag.
* ``QA001``: in tutorials and how-tos, every executable block (shell-like
  language) is marked ``<!-- qa:run env=E0..E3 -->`` or
  ``<!-- qa:skip reason=... -->`` on the line before it.
* ``QA002``: malformed ``qa:`` marker (unknown kind, environment label outside
  E0-E3, missing reason).
* ``QA003``: a ``qa:`` marker that is not followed by a fenced code block.
* ``PG001``: tutorials and how-tos start with a "Prerequisites" admonition.
* ``PG002``: the last second-level section of a how-to is the verification
  step (heading starts with "Verify" or "Verification").
* ``PG003``: every ``<placeholder>`` in a marked command block is listed in the
  text right after the block.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
import textwrap
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import jsonschema
import yaml
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

EXECUTABLE_LANGS = frozenset({"bash", "sh", "shell", "console", "zsh", "powershell", "pwsh"})
QA_ENVS = frozenset({"E0", "E1", "E2", "E3"})
QA_MARKER_RE = re.compile(r"^\s*<!--\s*qa:(?P<kind>[a-z-]+)(?P<attrs>.*?)-->\s*$")
FENCE_RE = re.compile(r"^(?P<indent>\s*)(?P<fence>`{3,}|~{3,})(?P<info>.*)$")
HEADING_RE = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<text>.+?)\s*#*\s*$")
PREREQ_RE = re.compile(r'^\s*(?:!!!|\?\?\?\+?)\s+[\w-]+\s+"Prerequisites"\s*$')
PLACEHOLDER_RE = re.compile(r"<([A-Za-z][A-Za-z0-9_.-]*)>")
SECTION_LEVEL = 2


@dataclass(frozen=True)
class Issue:
    """One documentation lint result.

    Attributes:
        path: Page path.
        line: 1-based line number (1 for page-level issues).
        code: Stable rule code.
        message: Human-readable description.
        warning: True for warnings, which do not fail the run.
    """

    path: Path
    line: int
    code: str
    message: str
    warning: bool = False


@dataclass
class CodeBlock:
    """A fenced code block.

    Attributes:
        start: 1-based line of the opening fence.
        end: 1-based line of the closing fence (or last line if unclosed).
        lang: First word of the info string, lower-cased; empty if absent.
        content: Lines between the fences.
        marker_line: Line number of a directly preceding ``qa:`` marker, if any.
        marker: The marker text, if any.
    """

    start: int
    end: int
    lang: str
    content: list[str] = field(default_factory=list)
    marker_line: int | None = None
    marker: str | None = None


@dataclass
class Page:
    """A parsed documentation page.

    Attributes:
        path: Page path.
        front_matter: Parsed front matter, or None if absent or invalid.
        body_offset: Number of lines before the body (front matter included).
        lines: All lines of the page.
    """

    path: Path
    front_matter: dict[str, Any] | None
    body_offset: int
    lines: list[str]


def _normalize(value: Any) -> Any:  # noqa: ANN401 - YAML values are untyped
    """Convert YAML dates to ISO strings, recursively.

    Args:
        value: A value produced by ``yaml.safe_load``.

    Returns:
        The same structure with ``date``/``datetime`` objects as strings.
    """
    if isinstance(value, dt.datetime | dt.date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    return value


def load_schema() -> dict[str, Any]:
    """Load the front-matter JSON Schema shipped with this package.

    Returns:
        The schema as a dictionary.
    """
    text = resources.files("pokus_repo_checks").joinpath("schema/front-matter.schema.json")
    schema: dict[str, Any] = json.loads(text.read_text(encoding="utf-8"))
    return schema


def parse_page(path: Path, text: str, issues: list[Issue]) -> Page:
    """Split a page into front matter and body.

    Args:
        path: Page path, for reporting.
        text: Page content.
        issues: List that receives parse issues.

    Returns:
        The parsed page.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        issues.append(Issue(path, 1, "FM001", "page does not start with front matter ('---')"))
        return Page(path, None, 0, lines)
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        issues.append(Issue(path, 1, "FM001", "front matter is not closed with '---'"))
        return Page(path, None, 0, lines)
    try:
        data = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError as exc:
        issues.append(Issue(path, 1, "FM002", f"front matter is not valid YAML: {exc}"))
        return Page(path, None, end + 1, lines)
    if not isinstance(data, dict):
        issues.append(Issue(path, 1, "FM002", "front matter is not a mapping"))
        return Page(path, None, end + 1, lines)
    return Page(path, _normalize(data), end + 1, lines)


def validate_front_matter(page: Page, schema: dict[str, Any], issues: list[Issue]) -> bool:
    """Validate front matter against the schema and specifier syntax.

    Args:
        page: Parsed page with front matter.
        schema: Front-matter JSON Schema.
        issues: List that receives issues.

    Returns:
        True if the front matter is valid.
    """
    fm = page.front_matter
    if fm is None:
        return False
    ok = True
    validator = jsonschema.Draft202012Validator(schema)
    for err in sorted(validator.iter_errors(fm), key=lambda e: list(e.absolute_path)):
        where = "/".join(str(p) for p in err.absolute_path) or "(root)"
        issues.append(Issue(page.path, 1, "FM003", f"front matter {where}: {err.message}"))
        ok = False
    for comp, spec in (fm.get("applies_to") or {}).items():
        if not isinstance(spec, str):
            continue
        try:
            SpecifierSet(spec)
        except InvalidSpecifier:
            issues.append(
                Issue(page.path, 1, "FM004", f"applies_to.{comp}: invalid specifier {spec!r}")
            )
            ok = False
    lv = fm.get("last_verified")
    if isinstance(lv, dict):
        values = [lv.get("date"), lv.get("rc"), lv.get("by")]
        if any(v is None for v in values) and not all(v is None for v in values):
            issues.append(
                Issue(page.path, 1, "FM005", "last_verified must be fully set or fully null")
            )
            ok = False
        elif all(v is None for v in values):
            issues.append(
                Issue(page.path, 1, "FM100", "page has never been verified", warning=True)
            )
    return ok


def find_code_blocks(lines: Sequence[str], offset: int) -> list[CodeBlock]:
    """Find fenced code blocks and their directly preceding ``qa:`` markers.

    Args:
        lines: All page lines.
        offset: Index of the first body line (front matter is skipped).

    Returns:
        Code blocks in page order.
    """
    blocks: list[CodeBlock] = []
    i = offset
    while i < len(lines):
        m = FENCE_RE.match(lines[i])
        if not m:
            i += 1
            continue
        fence, indent = m.group("fence"), len(m.group("indent"))
        info = m.group("info").strip()
        lang = info.split()[0].lower().lstrip("{.").rstrip("}") if info else ""
        block = CodeBlock(start=i + 1, end=len(lines), lang=lang)
        j = i - 1
        while j >= offset and not lines[j].strip():
            j -= 1
        if j >= offset and QA_MARKER_RE.match(lines[j]):
            block.marker_line, block.marker = j + 1, lines[j].strip()
        k = i + 1
        while k < len(lines):
            close = FENCE_RE.match(lines[k])
            if (
                close
                and close.group("fence")[0] == fence[0]
                and len(close.group("fence")) >= len(fence)
                and not close.group("info").strip()
                and len(close.group("indent")) <= indent + 3
            ):
                block.end = k + 1
                break
            block.content.append(lines[k])
            k += 1
        blocks.append(block)
        i = k + 1
    return blocks


def check_marker(path: Path, line: int, text: str, issues: list[Issue]) -> None:
    """Validate the syntax of one ``qa:`` marker.

    Args:
        path: Page path.
        line: Line number of the marker.
        text: Marker text.
        issues: List that receives issues.
    """
    m = QA_MARKER_RE.match(text)
    if not m:
        return
    kind, attrs = m.group("kind"), m.group("attrs").strip()
    if kind == "run":
        env = re.fullmatch(r"env=(\S+)", attrs)
        if not env or env.group(1) not in QA_ENVS:
            issues.append(
                Issue(path, line, "QA002", "qa:run needs exactly env=E0, env=E1, env=E2 or env=E3")
            )
    elif kind == "skip":
        reason = re.fullmatch(r"reason=(?:\"([^\"]*)\"|(.+))", attrs)
        if not reason or not (reason.group(1) or reason.group(2) or "").strip():
            issues.append(Issue(path, line, "QA002", "qa:skip needs a non-empty reason=..."))
    else:
        issues.append(Issue(path, line, "QA002", f"unknown marker qa:{kind}"))


def _text_after(lines: Sequence[str], block: CodeBlock) -> str:
    """Return the prose after a block, up to the next heading or fence."""
    out: list[str] = []
    for line in lines[block.end :]:
        if HEADING_RE.match(line) or FENCE_RE.match(line):
            break
        out.append(line)
    return "\n".join(out)


def _lint_markers(page: Page, blocks: Sequence[CodeBlock], issues: list[Issue]) -> None:
    """Check marker syntax and that every marker precedes a code block."""
    marker_lines = {b.marker_line for b in blocks if b.marker_line}
    in_block = {n for b in blocks for n in range(b.start, b.end + 1)}
    for idx in range(page.body_offset, len(page.lines)):
        lineno = idx + 1
        if lineno in in_block or not QA_MARKER_RE.match(page.lines[idx]):
            continue
        check_marker(page.path, lineno, page.lines[idx], issues)
        if lineno not in marker_lines:
            issues.append(
                Issue(page.path, lineno, "QA003", "qa marker is not followed by a code block")
            )


def _lint_blocks(
    page: Page, blocks: Sequence[CodeBlock], *, procedural: bool, issues: list[Issue]
) -> None:
    """Check language tags, required markers and listed placeholders."""
    for b in blocks:
        if not b.lang:
            issues.append(
                Issue(page.path, b.start, "CB001", "fenced code block without a language tag")
            )
        if not procedural:
            continue
        if b.lang in EXECUTABLE_LANGS and b.marker is None:
            message = f"{b.lang} block needs a qa:run or qa:skip marker"
            issues.append(Issue(page.path, b.start, "QA001", message))
        if b.marker is not None:
            after = _text_after(page.lines, b)
            for name in sorted(set(PLACEHOLDER_RE.findall("\n".join(b.content)))):
                if f"<{name}>" not in after:
                    message = f"placeholder <{name}> is not listed after the block"
                    issues.append(Issue(page.path, b.start, "PG003", message))


def _second_level_headings(page: Page, blocks: Sequence[CodeBlock]) -> list[str]:
    """Return the texts of all '##' headings outside code blocks."""
    in_block = {n for b in blocks for n in range(b.start, b.end + 1)}
    headings: list[str] = []
    for idx in range(page.body_offset, len(page.lines)):
        m = HEADING_RE.match(page.lines[idx])
        if m and idx + 1 not in in_block and len(m.group("hashes")) == SECTION_LEVEL:
            headings.append(m.group("text"))
    return headings


def lint_page(page: Page, issues: list[Issue]) -> None:
    """Apply the code-block, marker and page-structure rules to one page.

    Args:
        page: Parsed page.
        issues: List that receives issues.
    """
    page_type = (page.front_matter or {}).get("type")
    procedural = page_type in {"tutorial", "how-to"}
    blocks = find_code_blocks(page.lines, page.body_offset)
    _lint_markers(page, blocks, issues)
    _lint_blocks(page, blocks, procedural=procedural, issues=issues)
    if procedural and not any(PREREQ_RE.match(x) for x in page.lines[page.body_offset :]):
        message = f'{page_type} needs a "Prerequisites" admonition'
        issues.append(Issue(page.path, 1, "PG001", message))
    if page_type == "how-to":
        h2 = _second_level_headings(page, blocks)
        if not h2 or not h2[-1].lower().startswith(("verify", "verification")):
            message = "how-to must end with a '## Verify ...' section"
            issues.append(Issue(page.path, 1, "PG002", message))


def latest_release(component: str) -> tuple[Version, dt.date] | None:
    """Find the component's latest release tag and its commit date.

    Args:
        component: Component name; tags are ``<component>-vX.Y.Z``.

    Returns:
        The version and UTC commit date, or None if no release exists.
    """
    try:
        tags = subprocess.run(  # noqa: S603 - fixed argv
            ["git", "tag", "--list", f"{component}-v*", "--sort=-v:refname"],  # noqa: S607
            check=True,
            capture_output=True,
            text=True,
        ).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        return None
    for tag in tags:
        try:
            version = Version(tag.removeprefix(f"{component}-v"))
        except InvalidVersion:
            continue
        date = subprocess.run(  # noqa: S603 - fixed argv
            ["git", "log", "-1", "--format=%cs", tag],  # noqa: S607
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, "TZ": "UTC"},
        ).stdout.strip()
        return version, dt.date.fromisoformat(date)
    return None


def check_staleness(
    page: Page,
    component: str,
    release: tuple[Version, dt.date],
    *,
    strict: bool,
    issues: list[Issue],
) -> None:
    """Flag pages not verified since the latest release they apply to.

    Args:
        page: Parsed page with valid front matter.
        component: Component whose releases are checked.
        release: Latest release version and date.
        strict: Report an error instead of a warning (release time).
        issues: List that receives issues.
    """
    fm = page.front_matter or {}
    spec = (fm.get("applies_to") or {}).get(component)
    if not isinstance(spec, str) or not SpecifierSet(spec).contains(release[0], prereleases=True):
        return
    date = (fm.get("last_verified") or {}).get("date")
    if date is None or dt.date.fromisoformat(date) < release[1]:
        issues.append(
            Issue(
                page.path,
                1,
                "FM101",
                f"last_verified is older than release {component}-v{release[0]}",
                warning=not strict,
            )
        )


def iter_pages(docs_dir: Path, excludes: Iterable[str]) -> list[Path]:
    """List the site pages of a docs directory.

    Args:
        docs_dir: MkDocs ``docs_dir``.
        excludes: Page paths relative to ``docs_dir`` that are not built.

    Returns:
        Sorted Markdown files that belong to the site.
    """
    excluded = {Path(e) for e in excludes}
    return sorted(p for p in docs_dir.rglob("*.md") if p.relative_to(docs_dir) not in excluded)


def run(docs_dir: Path, *, excludes: Sequence[str], component: str, release: bool) -> int:
    """Lint every page of a docs directory and print the results.

    Args:
        docs_dir: MkDocs ``docs_dir``.
        excludes: Page paths relative to ``docs_dir`` that are not built.
        component: Component name for release tags.
        release: Release mode (staleness becomes an error).

    Returns:
        0 when there are no errors, 1 otherwise.
    """
    schema = load_schema()
    issues: list[Issue] = []
    latest = latest_release(component)
    pages = iter_pages(docs_dir, excludes)
    for path in pages:
        page = parse_page(path, path.read_text(encoding="utf-8"), issues)
        valid = validate_front_matter(page, schema, issues)
        lint_page(page, issues)
        if valid and latest is not None:
            check_staleness(page, component, latest, strict=release, issues=issues)
    in_ci = os.environ.get("GITHUB_ACTIONS") == "true"
    for i in issues:
        level = "warning" if i.warning else "error"
        print(f"{i.path}:{i.line}: {level}: {i.code} {i.message}")
        if in_ci:
            print(f"::{level} file={i.path},line={i.line}::{i.code} {i.message}")
    errors = sum(1 for i in issues if not i.warning)
    warnings = len(issues) - errors
    print(f"docs lint: {len(pages)} pages, {errors} errors, {warnings} warnings", file=sys.stderr)
    return 1 if errors else 0


def extract_mermaid(docs_dir: Path, *, excludes: Sequence[str], out: Path) -> int:
    """Write every Mermaid block to ``out`` for validation with ``mmdc``.

    Each diagram becomes ``<page path with / replaced by __>__<n>.mmd``;
    ``index.tsv`` maps file names back to page and line.

    Args:
        docs_dir: MkDocs ``docs_dir``.
        excludes: Page paths relative to ``docs_dir`` that are not built.
        out: Output directory (created if missing).

    Returns:
        Always 0; syntax errors are reported by ``mmdc``.
    """
    out.mkdir(parents=True, exist_ok=True)
    index: list[str] = []
    for path in iter_pages(docs_dir, excludes):
        lines = path.read_text(encoding="utf-8").splitlines()
        stem = str(path.relative_to(docs_dir)).replace("/", "__").removesuffix(".md")
        for n, block in enumerate(
            (b for b in find_code_blocks(lines, 0) if b.lang == "mermaid"), start=1
        ):
            name = f"{stem}__{n}.mmd"
            body = textwrap.dedent("\n".join(block.content))
            (out / name).write_text(body + "\n", encoding="utf-8")
            index.append(f"{name}\t{path}:{block.start}")
    (out / "index.tsv").write_text("\n".join(index) + ("\n" if index else ""), encoding="utf-8")
    print(f"mermaid: {len(index)} diagrams extracted to {out}", file=sys.stderr)
    return 0
