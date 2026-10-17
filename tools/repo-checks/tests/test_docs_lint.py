"""Tests for the documentation page lint."""

from __future__ import annotations

import datetime as dt
import textwrap
from pathlib import Path

import pytest
from packaging.version import Version

from pinka_repo_checks import docs_lint

UNVERIFIED = "last_verified: {date: null, rc: null, by: null}"
VERIFIED = 'last_verified: {date: 2026-10-01, rc: "infra@abc platform@def cases@012", by: run-1}'


def front(page_type: str = "reference", last_verified: str = UNVERIFIED) -> str:
    return textwrap.dedent(
        f"""\
        ---
        title: A test page
        type: {page_type}
        audience: [operator]
        applies_to: {{infra: ">=0.1"}}
        {last_verified}
        ---
        """
    )


def lint(text: str) -> list[docs_lint.Issue]:
    issues: list[docs_lint.Issue] = []
    page = docs_lint.parse_page(Path("p.md"), text, issues)
    docs_lint.validate_front_matter(page, docs_lint.load_schema(), issues)
    docs_lint.lint_page(page, issues)
    return issues


def codes(text: str, *, warnings: bool = False) -> list[str]:
    return [i.code for i in lint(text) if warnings or not i.warning]


HOWTO_OK = front("how-to") + textwrap.dedent(
    """\
    # How to do a thing

    !!! note "Prerequisites"
        A shell.

    ## Steps

    <!-- qa:run env=E2 -->
    ```bash
    pinka status --machine <machine>
    ```

    `<machine>` is the machine name.

    ```text title="Output"
    ok
    ```

    ## Verify the result

    You should now see `ok`.
    """
)


def test_valid_reference_page_has_no_errors() -> None:
    assert codes(front() + "# Title\n\nText.\n") == []


def test_unverified_page_is_a_warning() -> None:
    issues = lint(front() + "# Title\n")
    assert [(i.code, i.warning) for i in issues] == [("FM100", True)]


def test_verified_page_has_no_warning() -> None:
    assert codes(front(last_verified=VERIFIED) + "# T\n", warnings=True) == []


def test_missing_front_matter() -> None:
    assert codes("# Title\n") == ["FM001"]


def test_unclosed_front_matter() -> None:
    assert codes("---\ntitle: x\n") == ["FM001"]


def test_invalid_yaml() -> None:
    assert codes("---\ntitle: [\n---\n") == ["FM002"]


def test_unknown_type_is_schema_error() -> None:
    assert "FM003" in codes(front("guide") + "# T\n")


def test_unknown_key_is_schema_error() -> None:
    text = front().replace("audience:", "owner: x\naudience:")
    assert "FM003" in codes(text)


def test_invalid_specifier() -> None:
    text = front().replace('">=0.1"', '"=>0.1"')
    assert "FM004" in codes(text)


def test_partial_last_verified() -> None:
    text = front(last_verified="last_verified: {date: 2026-10-01, rc: null, by: null}")
    assert codes(text + "# T\n") == ["FM005"]


def test_howto_ok() -> None:
    assert codes(HOWTO_OK) == []


def test_block_without_language() -> None:
    assert codes(front() + "```\nx\n```\n") == ["CB001"]


def test_executable_block_without_marker() -> None:
    text = HOWTO_OK.replace("<!-- qa:run env=E2 -->\n", "")
    assert codes(text) == ["QA001"]


def test_reference_pages_do_not_need_markers() -> None:
    assert codes(front() + "```bash\nls\n```\n") == []


@pytest.mark.parametrize(
    "marker",
    [
        "<!-- qa:run env=E7 -->",
        "<!-- qa:run -->",
        "<!-- qa:skip -->",
        '<!-- qa:skip reason="" -->',
        "<!-- qa:maybe env=E1 -->",
    ],
)
def test_malformed_markers(marker: str) -> None:
    text = HOWTO_OK.replace("<!-- qa:run env=E2 -->", marker)
    assert codes(text) == ["QA002"]


def test_skip_with_reason_is_valid() -> None:
    text = HOWTO_OK.replace("<!-- qa:run env=E2 -->", '<!-- qa:skip reason="needs a GPU" -->')
    assert codes(text) == []


def test_dangling_marker() -> None:
    text = HOWTO_OK.replace(
        "## Verify the result", "<!-- qa:run env=E1 -->\n\n## Verify the result"
    )
    assert codes(text) == ["QA003"]


def test_marker_inside_code_block_is_ignored() -> None:
    body = "~~~markdown\n<!-- qa:run env=E9 -->\n~~~\n"
    assert codes(front() + body) == []


def test_missing_prerequisites() -> None:
    text = HOWTO_OK.replace('!!! note "Prerequisites"\n    A shell.\n', "")
    assert codes(text) == ["PG001"]


def test_howto_must_end_with_verification() -> None:
    text = HOWTO_OK + "\n## Troubleshooting\n\nNone.\n"
    assert codes(text) == ["PG002"]


def test_tutorial_does_not_need_verify_section() -> None:
    text = HOWTO_OK.replace("type: how-to", "type: tutorial").replace(
        "## Verify the result", "## Done"
    )
    assert codes(text) == []


def test_unlisted_placeholder() -> None:
    text = HOWTO_OK.replace("`<machine>` is the machine name.", "Run it.")
    assert codes(text) == ["PG003"]


def test_heredoc_is_not_a_placeholder() -> None:
    text = HOWTO_OK.replace("pinka status --machine <machine>", "cat <<'EOF'\nx\nEOF")
    assert codes(text) == []


def test_find_code_blocks_handles_indent_and_tildes() -> None:
    lines = [
        "!!! note",
        "    ```yaml",
        "    a: 1",
        "    ```",
        "~~~~text",
        "```",
        "~~~~",
    ]
    blocks = docs_lint.find_code_blocks(lines, 0)
    assert [(b.lang, b.start, b.end) for b in blocks] == [("yaml", 2, 4), ("text", 5, 7)]
    assert blocks[1].content == ["```"]


def test_staleness_warning_and_release_error() -> None:
    issues: list[docs_lint.Issue] = []
    page = docs_lint.parse_page(Path("p.md"), front() + "# T\n", issues)
    release = (Version("0.1.0"), dt.date(2026, 10, 2))
    docs_lint.check_staleness(page, "infra", release, strict=False, issues=issues)
    docs_lint.check_staleness(page, "infra", release, strict=True, issues=issues)
    assert [(i.code, i.warning) for i in issues] == [("FM101", True), ("FM101", False)]


def test_staleness_ignores_verified_and_other_components() -> None:
    issues: list[docs_lint.Issue] = []
    page = docs_lint.parse_page(Path("p.md"), front(last_verified=VERIFIED) + "# T\n", issues)
    docs_lint.check_staleness(
        page, "infra", (Version("0.1.0"), dt.date(2026, 9, 1)), strict=True, issues=issues
    )
    docs_lint.check_staleness(
        page, "platform", (Version("9.0"), dt.date(2027, 1, 1)), strict=True, issues=issues
    )
    assert issues == []


def test_run_and_extract_mermaid(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    docs = tmp_path / "docs"
    (docs / "sub").mkdir(parents=True)
    (docs / "index.md").write_text(
        front() + "```mermaid\ngraph TD\n  A-->B\n```\n", encoding="utf-8"
    )
    (docs / "sub" / "x.md").write_text(front() + "# X\n", encoding="utf-8")
    (docs / "BUILD_PROMPT.md").write_text("# not a site page\n", encoding="utf-8")
    assert docs_lint.run(docs, excludes=["BUILD_PROMPT.md"], component="infra", release=False) == 0
    assert "2 pages, 0 errors, 2 warnings" in capsys.readouterr().err
    out = tmp_path / "mmd"
    assert docs_lint.extract_mermaid(docs, excludes=["BUILD_PROMPT.md"], out=out) == 0
    assert (out / "index__1.mmd").read_text(encoding="utf-8") == "graph TD\n  A-->B\n"
    assert (out / "index.tsv").read_text(encoding="utf-8").startswith("index__1.mmd\t")


def test_run_fails_on_errors(tmp_path: Path) -> None:
    (tmp_path / "bad.md").write_text("# no front matter\n", encoding="utf-8")
    assert docs_lint.run(tmp_path, excludes=[], component="infra", release=False) == 1
