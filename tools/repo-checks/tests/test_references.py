"""The references lint (pinka_repo_checks.references).

Every sample is assembled from pieces (``_join``), so this file does not
match the patterns it tests.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from pinka_repo_checks import references


def _join(*parts: str) -> str:
    return "".join(parts)


SIGN = chr(0xA7)  # the section sign
KEPT = _join("BUILD", "_PROMPT")
KEPT_MD = _join("docs/", "DOCS", "_SPEC", ".md")
OWNER = _join("own", "er")
COMMIT_FOR = _join('with the "', 'commit" for')


@pytest.fixture(autouse=True)
def plain_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """git sees only the test's own directories and configuration, whatever the environment sets."""
    for name in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_COMMON_DIR",
        "GIT_CONFIG_PARAMETERS",
        "GIT_CONFIG_COUNT",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    monkeypatch.delenv("GIT_TEMPLATE_DIR", raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    # git's default global ignore file lives below HOME or XDG_CONFIG_HOME.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "home" / ".config"))


def found(line: str, resolutions: frozenset[str] = frozenset()) -> list[tuple[str, str]]:
    return [(name, text) for name, text, _column in references.scan_line(line, resolutions)]


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (
            _join("# The play (", KEPT, " ", SIGN, "7.14)."),
            [("kept-file", KEPT), ("section-citation", SIGN + "7")],
        ),
        (_join("see ", KEPT_MD), [("kept-file", _join("DOCS", "_SPEC"))]),
        (_join("(", SIGN, "A.6)"), [("section-citation", SIGN + "A.6")]),
        (_join("(", SIGN, SIGN, "7.13)"), [("section-citation", SIGN + SIGN + "7")]),
        (
            _join("read ", "AGE", "NTS", ".md first"),
            [("instruction-file", _join("AGE", "NTS", ".md"))],
        ),
        (_join("the build", " prompt says"), [("spec-name", _join("build", " prompt"))]),
        (_join("the Docs", " Specification"), [("spec-name", _join("Docs", " Specification"))]),
        (
            _join("until real-machine", " test 4.15"),
            [("spec-name", _join("real-machine", " test 4"))],
        ),
        (_join("the ", "spec", "s/ directory"), [("spec-name", _join("spec", "s/"))]),
        (_join("see x-code", "-prompt", ".md"), [("spec-name", _join("x-code", "-prompt", ".md"))]),
        (_join("the docs", "-spec page"), [("spec-name", _join("docs", "-spec"))]),
        (
            _join("as plan", " item ", "3", "b says"),
            [("plan-id", _join("plan", " item")), ("plan-id", _join("3", "b"))],
        ),
        (_join("verified in ", "3", "d.4"), [("plan-id", _join("3", "d.4"))]),
        (_join("verified in ", "3", "d.4."), [("plan-id", _join("3", "d.4"))]),
        (_join("as decided for ", "3", "d.4b:"), [("plan-id", _join("3", "d.4b"))]),
        (_join("decided on ", "3", "f-1;"), [("plan-id", _join("3", "f-1"))]),
        (_join("images (", "3", "c2)"), [("plan-id", _join("3", "c2"))]),
        (_join("the ", "3", "d.6-f step"), [("plan-id", _join("3", "d.6-f"))]),
        (_join("the ", "3", "d.4.1 item"), [("plan-id", _join("3", "d.4.1"))]),
        (_join("a ", "3", "c2-based rule"), [("plan-id", _join("3", "c2"))]),
        (_join("since ", "3", "f-1-A"), [("plan-id", _join("3", "f-1"))]),
        (_join("plan ", "3", "e: self-test"), [("plan-id", _join("3", "e"))]),
        (_join("since infra", " step 12"), [("plan-id", _join("infra", " step 1"))]),
        # The phrase is one match: its identifier is part of it.
        (
            _join("approved", " deviation ", "I1"),
            [("decision-id", _join("approved", " deviation"))],
        ),
        (_join("the deviation", " ", "AG1"), [("decision-id", _join("deviation", " ", "AG1"))]),
        (_join("scraped (", "E14)"), [("decision-id", "E14")]),
        (_join("(C5, ", "D3)"), [("decision-id", "D3")]),
        (
            _join("Decision", " 1 after ", "D1:"),
            [("decision-id", _join("Decision", " 1")), ("decision-id", _join("after ", "D1"))],
        ),
        (_join("an answer ", "E7 says"), [("decision-id", _join("answer ", "E7"))]),
        (_join("since the ", "D2", " review"), [("decision-id", _join("D2", " review"))]),
        (
            _join("Decision", " 1 After ", "D1."),
            [("decision-id", _join("Decision", " 1")), ("decision-id", _join("After ", "D1"))],
        ),
        (_join("Answer ", "E7 said"), [("decision-id", _join("Answer ", "E7"))]),
        (
            _join("(", OWNER, ", after ", "D2)"),
            [("attribution", "(" + OWNER), ("decision-id", _join("after ", "D2"))],
        ),
        (
            _join("(", OWNER, "'s choice in ", "3", "d.8)"),
            [("attribution", "(" + OWNER + "'s"), ("plan-id", _join("3", "d.8"))],
        ),
        (_join("Owner", " decision 3"), [("attribution", _join("Owner", " decision"))]),
        (
            _join("the ", OWNER, " approved it"),
            [("attribution", _join("the ", OWNER, " approved"))],
        ),
        (_join("kept (", COMMIT_FOR, " x)"), [("attribution", COMMIT_FOR)]),
        (_join("exporter (", OWNER), [("attribution", "(" + OWNER)]),
        (_join("the commit", " rule applies"), [("workflow-term", _join("commit", " rule"))]),
        (_join("the stop", " hook fires"), [("workflow-term", _join("stop", " hook"))]),
        # A specification named as kept elsewhere.
        (
            _join("the private", " specification"),
            [("spec-name", _join("private", " specification"))],
        ),
        # The section sign escaped as in JSON, in either case.
        (_join("(\\u00", "a7", "2.3)"), [("section-citation", _join("\\u00", "a72"))]),
        (_join("(\\u00", "A7 ", "A.6)"), [("section-citation", _join("\\u00", "A7 ", "A.6"))]),
        # A numbered invariant of a specification.
        (
            _join("only under safety", " invariant", " 1 (exact"),
            [("section-citation", _join("safety", " invariant", " 1"))],
        ),
        (_join("Invariants", " 21 and 22."), [("section-citation", _join("Invariants", " 21"))]),
        # A numbered interpretation, and a decision's number before what it
        # was decided after or on.
        (
            _join("ports (interpretation", " 11.13)"),
            [("decision-id", _join("interpretation", " 11.13"))],
        ),
        (
            _join("Interpretation", " 11.13 against"),
            [("decision-id", _join("Interpretation", " 11.13"))],
        ),
        (
            _join("decision", " 3 on ", "3", "f-1:"),
            [("decision-id", _join("decision", " 3")), ("plan-id", _join("3", "f-1"))],
        ),
        # An identifier proposed, a deviation in any case, an identifier that
        # opens a comment.
        (_join("(proposed", " V3)"), [("decision-id", _join("proposed", " V3"))]),
        (_join("Deviation", " AG1 holds"), [("decision-id", _join("Deviation", " AG1"))]),
        (
            _join("# Approved", " deviation St2-3."),
            [("decision-id", _join("Approved", " deviation"))],
        ),
        (_join("    # ", "Q7", ": exactly two"), [("decision-id", _join("Q", "7"))]),
        (_join('"""', "V5", ": the"), [("decision-id", _join("V", "5"))]),
        (_join("x = 1  # ", "V5", ": the"), [("decision-id", _join("V", "5"))]),
        (_join("// ", "V5", ": the"), [("decision-id", _join("V", "5"))]),
        (_join("-- ", "Q7", ":"), [("decision-id", _join("Q", "7"))]),
        # Who decided, in the forms beside the parenthesised ones.
        (
            _join("the ", OWNER, "'s condition for"),
            [("attribution", _join(OWNER, "'s condition"))],
        ),
        (_join("an ", OWNER, " confirmation"), [("attribution", _join(OWNER, " confirmation"))]),
        (
            _join("until the ", OWNER, " approves it"),
            [("attribution", _join("the ", OWNER, " approves"))],
        ),
        (
            _join("The ", OWNER, "'s one-time", " rewrite"),
            [("attribution", _join("The ", OWNER, "'s one-time", " rewrite"))],
        ),
        (
            _join("with the ", OWNER, "'s new", " plan"),
            [("attribution", _join("the ", OWNER, "'s new", " plan"))],
        ),
        (
            _join("# Own", "er, with the new", " plan after x"),
            [("attribution", _join("Own", "er,")), ("attribution", _join("new", " plan after"))],
        ),
        (
            _join("# Own", "er, ", COMMIT_FOR, " x"),
            [("attribution", _join("Own", "er,")), ("attribution", COMMIT_FOR)],
        ),
        # A step of a plan or of a specification.
        (_join("Deployment", " step 7 does"), [("plan-id", _join("Deployment", " step 7"))]),
        (_join("Deployment", " Step 7 does"), [("plan-id", _join("Deployment", " Step 7"))]),
        (_join("Contract", " V2 Steps"), [("plan-id", _join("Contract", " V2 Steps"))]),
        (_join("later contract", " v2 steps"), [("plan-id", _join("contract", " v2 steps"))]),
    ],
)
def test_each_kind_of_reference(line: str, expected: list[tuple[str, str]]) -> None:
    assert sorted(found(line)) == sorted(expected)


@pytest.mark.parametrize(
    "line",
    [
        "closed only with the " + OWNER + "'s consent",
        "the file " + OWNER + " and mode",
        "return info(" + OWNER + ", group, mode)",
        "system(" + OWNER + '="pinka-build")',
        "only root can change it (" + OWNER + " root, no group or other write)",
        "the migrator (" + OWNER + " of the schemas)",
        'detail = f"offset {offset:+.3f} s"',
        "lr = 3e-5, then 3e-4, 3e5 or 3e+5",
        "df --output=target -B1 /",
        "<!-- qa:run env=E2 -->",
        "press F1 for help",
        "see README.md, CONTRIBUTING.md and SECURITY.md",
        "Step 3: install the driver",
        "the vault password handover file",
        "the gate and the commit rules: CONTRIBUTING.md",
        "a resolution of the contract (C9c)",
        "tools/specs/x.md and specs.md",
        _join("the cell (V", "6)"),
        "the CDI specification; so the specification changes",
        "an allowlist (option b, decision 6); as in decision 2. A kernel test",
        "checks the invariants the schema cannot express; the invariant checks",
        "the "
        + OWNER
        + "'s consent, the "
        + OWNER
        + "'s zones and rules, the "
        + OWNER
        + "'s services",
        "the file " + OWNER + ", with mode 0644",
        "the role README's step 9; Step 2 of the tutorial",
        "status: proposed until reviewed",
        _join("Deviation", " V6 from the baseline"),
        _join("    # ", "V6", ": the agent's own package"),
        _join('{"E2', '": 1}'),
        _join("a = ", "V5", ": b"),
        # An identifier's shape opening a line that is no comment: a YAML key,
        # a list item, a string.
        _join("E", "2: 1"),
        _join("  A", "10: 24 GiB"),
        _join("- A", "10: 24 GiB"),
        _join('    "D', '1: x",'),
        _join("{E", "2: 1}"),
        "the sign \\u00a7 alone",
    ],
)
def test_product_language_is_no_reference(line: str) -> None:
    assert found(line, frozenset({"V6"})) == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (_join("# the build\n", "# prompt says\n"), [(1, _join("build", " prompt"))]),
        (_join("    the docs\n", "    specification of\n"), [(1, _join("docs", " specification"))]),
        (_join("// the QA\n", "// spec\n"), [(1, _join("QA", " spec"))]),
        (
            _join("x\n", "-- documentation\n", "-- specifications\n"),
            [(2, _join("documentation", " specifications"))],
        ),
        (_join("; the build\n", "; prompt\n"), [(1, _join("build", " prompt"))]),
        (_join(" * the docs\n", " * spec\n"), [(1, _join("docs", " spec"))]),
        (_join("{# the build\n", "{# prompts #}\n"), [(1, _join("build", " prompts"))]),
        (
            _join("<!-- the docs\n", "<!-- specification -->\n"),
            [(1, _join("docs", " specification"))],
        ),
        (_join("# a build\n", "# promptly\n"), []),
        (_join("# build\n", "x = prompt\n"), []),
        (_join("# build\n", "\n", "# prompt\n"), []),
        (_join("# the build", " prompt\n"), [(1, _join("build", " prompt"))]),
    ],
)
def test_a_name_split_over_two_lines(text: str, expected: list[tuple[int, str]]) -> None:
    findings = references.scan_text("a.py", text)
    assert [(f.line, f.text) for f in findings] == expected
    assert all(f.pattern == "spec-name" for f in findings)


# What may stand before an identifier that opens a comment, and what may not.
@pytest.mark.parametrize(
    ("before", "counted"),
    [
        ("# ", True),
        ("    ## ", True),  # a Markdown heading opens the same way and counts
        ("// ", True),
        ("-- ", True),
        ("/* ", True),
        ("/** ", True),
        (" * ", True),
        ("; ", True),
        ("<!-- ", True),
        ("{# ", True),
        ("{#- ", True),
        ('"""', True),
        ("    '''", True),
        ("# - ", True),
        ("// * ", True),
        ("x = 1  # ", True),
        ("SELECT 1 -- ", True),
        ("", False),
        ("    ", False),
        ("- ", False),
        ("  - ", False),
        ('    "', False),
        ("{", False),
        ("x = 1; ", False),
        ("the first; ", False),
        ("&amp; ", False),
        ("a = ", False),
    ],
)
def test_an_identifier_counts_where_it_opens_a_comment(before: str, counted: bool) -> None:
    line = _join(before, "V", "5: the step")
    assert found(line) == ([("decision-id", _join("V", "5"))] if counted else [])


def test_resolution_ids_come_from_the_contract_s_table() -> None:
    contracts = (
        "# Contract\n\n## Resolutions\n\nText.\n\n"
        "| ID | Resolution | Status |\n| --- | --- | --- |\n"
        "| B2 | a | b |\n| C2, C3 | c | d |\n| V6 | e | f |\n| P3a | g | h |\n| V7 | i | j |\n"
        "\n## Next\n\n| X9 | not | this |\n"
    )
    ids = references.resolution_ids(contracts)
    assert ids == {"B2", "C2", "C3", "V6", "P3a", "V7"}
    assert found(_join("the agent (V", "6)"), ids) == []
    assert found(_join("the agent (V", "5)"), ids) == [("decision-id", _join("V", "5"))]
    # A defined identifier after a word that makes it a decision still counts.
    assert found(_join("as decided after ", "V6"), ids) == [("decision-id", _join("after ", "V6"))]
    # So does one proposed; one after the word deviation or opening a comment
    # does not.
    assert found(_join("(proposed ", "V6)"), ids) == [("decision-id", _join("proposed ", "V6"))]
    assert found(_join("Deviation ", "V6 from it"), ids) == []
    assert found(_join("the deviation ", "V6 holds"), ids) == []
    assert found(_join("# ", "P3a", ": cpu<n>"), ids) == []
    assert found(_join("# ", "V7", ": compute capability"), ids) == []
    assert found(_join("# ", "V6", ": the agent"), ids) == []
    assert found(_join("# ", "V5", ": the agent"), ids) == [("decision-id", _join("V", "5"))]
    assert references.resolution_ids("# no table\n") == frozenset()


def test_this_lint_and_its_tests_have_no_references() -> None:
    for path in (Path(references.__file__), Path(__file__)):
        assert references.scan_text(path.name, path.read_text(encoding="utf-8")) == []


def test_the_allowlist_file_is_not_scanned() -> None:
    assert references.excluded("tools/repo-checks/pinka_repo_checks/references.json")
    assert references.excluded("README.md") is None


def finding(path: str, line: str) -> references.Finding:
    (name, text, column), *_ = references.scan_line(line)
    return references.Finding(path, 1, name, text, line, column)


UPSTREAM = _join("upstream build", " prompt")
ALLOWED = references.Allowed("docs/*", "spec-name", UPSTREAM, "an upstream document")


def test_allowed_and_remaining_findings() -> None:
    upstream = finding("docs/a.md", _join("the upstream build", " prompt"))
    other = finding("docs/b.md", _join("the build", " prompt"))
    elsewhere = finding("README.md", _join("the upstream build", " prompt"))
    result = references.check([upstream, other, elsewhere], 2, [ALLOWED])
    assert result.allowed == [upstream]
    assert result.remaining == [other, elsewhere]
    assert result.problems == []


def test_an_entry_covers_only_a_match_inside_its_text() -> None:
    upstream_name = _join("UIDS-GIDS", ".md")
    entry = references.Allowed("*", "instruction-file", upstream_name, "an upstream document")
    line = _join(upstream_name, " and ", "AGE", "NTS", ".md")
    upstream, other = references.scan_text("x.py", line)
    assert entry.covers(upstream)
    assert not entry.covers(other)
    result = references.check([upstream, other], 1, [entry])
    assert result.remaining == [other]
    assert result.problems == []


def test_budget_and_stale_entries() -> None:
    one = finding("docs/b.md", _join("the build", " prompt"))
    assert references.check([one], 1, []).problems == []
    assert references.check([one], 0, []).problems == [
        "1 reference(s), more than the budget of 0: remove the new ones"
    ]
    assert references.check([one], 2, []).problems == [
        "1 reference(s), fewer than the budget of 2: lower the budget in references.json to 1"
    ]
    stale = references.check([], 0, [ALLOWED])
    assert stale.problems == [f"allowed entry no longer matches: docs/* spec-name {UPSTREAM!r}"]


def test_refuses_a_duplicate_entry() -> None:
    entry = {"path": "x.py", "pattern": "spec-name", "text": "x", "reason": "r"}
    for other in (dict(entry), {**entry, "reason": "another reason"}):
        with pytest.raises(ValueError, match="listed twice"):
            references.load({"budget": 0, "allowed": [entry, other]})


@pytest.mark.parametrize(
    ("change", "message"),
    [({"text": "  "}, "needs a text and a reason"), ({"note": "x"}, "unknown keys")],
)
def test_refuses_a_blank_text_and_unknown_keys(change: dict[str, str], message: str) -> None:
    entry = {"path": "x.py", "pattern": "spec-name", "text": "x", "reason": "r", **change}
    with pytest.raises(ValueError, match=message):
        references.load({"budget": 0, "allowed": [entry]})


def test_lines_end_at_a_line_feed_only() -> None:
    text = _join("a\u0085b\x0cc\r\n", "see ", SIGN, "7.5\n")
    (citation,) = references.scan_text("x.md", text)
    assert citation.line == 2


def test_refuses_a_field_that_is_not_a_string() -> None:
    entry = {"path": "x.py", "pattern": "spec-name", "text": ["x"], "reason": "r"}
    with pytest.raises(ValueError, match="every field must be a string"):
        references.load({"budget": 0, "allowed": [entry]})


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"budget": -1, "allowed": []}, "non-negative integer"),
        ({"budget": True, "allowed": []}, "non-negative integer"),
        (
            {
                "budget": 0,
                "allowed": [{"path": "*", "pattern": "nope", "text": "x", "reason": "r"}],
            },
            "unknown pattern",
        ),
        (
            {
                "budget": 0,
                "allowed": [{"path": "*", "pattern": "spec-name", "text": "x", "reason": " "}],
            },
            "needs a text and a reason",
        ),
        (
            {
                "budget": 0,
                "allowed": [{"path": "*", "pattern": "spec-name", "text": "", "reason": "r"}],
            },
            "needs a text and a reason",
        ),
    ],
)
def test_refuses_a_bad_data_file(data: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        references.load(data)


def repo(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    return root


def data_file(tmp_path: Path, budget: int) -> Path:
    path = tmp_path / "references.json"
    path.write_text(json.dumps({"budget": budget, "allowed": []}), encoding="utf-8")
    return path


def test_run_within_the_budget_warns(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = repo(
        tmp_path, {"a.md": _join("see ", SIGN, "7.5\n"), "node_modules/x.md": _join(SIGN, "1\n")}
    )
    assert references.run(root, data_file(tmp_path, 1)) == 0
    out = capsys.readouterr().out
    assert _join("a.md:1: section-citation: ", SIGN, "7") in out
    assert "node_modules" not in out
    assert out.splitlines()[-1].startswith(
        "warning: references: 1 reference(s) remain, within the budget"
    )


def test_run_at_zero_prints_no_warning(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = repo(tmp_path, {"a.md": "plain text\n"})
    assert references.run(root, data_file(tmp_path, 0)) == 0
    out = capsys.readouterr().out
    assert "warning" not in out
    assert "references: no references to material kept outside the repository" in out


def test_run_fails_above_the_budget(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = repo(tmp_path, {"a.md": _join("see ", SIGN, "7.5 and ", KEPT, "\n")})
    assert references.run(root, data_file(tmp_path, 1)) == 1
    assert "2 reference(s), more than the budget of 1" in capsys.readouterr().err


def test_run_outside_a_git_checkout(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    assert references.run(plain, data_file(tmp_path, 0)) == 1
    err = capsys.readouterr().err
    assert f"references: git ls-files failed in {plain}:" in err
    assert "not a git repository" in err


def test_scan_skips_what_it_documents(tmp_path: Path) -> None:
    cite = _join("see ", SIGN, "7.5\n")
    root = repo(
        tmp_path,
        {
            "a.md": cite,
            "node_modules/x.md": cite,
            "tools/repo-checks/pinka_repo_checks/references.json": cite,
        },
    )
    (root / "binary.dat").write_bytes(b"\0\xff" + cite.encode())
    (root / "latin1.txt").write_bytes(_join("caf\u00e9 ", cite).encode("latin-1"))
    (root / "link.md").symlink_to(root / "a.md")
    (root / "gone.md").write_text(cite, encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "gone.md"], check=True)
    (root / "gone.md").unlink()
    listed = set(references.files(root))
    skipped = {"node_modules/x.md", "binary.dat", "link.md", "gone.md"}
    assert skipped | {"tools/repo-checks/pinka_repo_checks/references.json"} <= listed
    assert [f.path for f in references.scan(root)] == ["a.md", "latin1.txt"]


def test_scan_stops_on_a_file_it_cannot_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = repo(tmp_path, {"a.md": "plain\n"})
    real = Path.read_bytes

    def refuse(self: Path) -> bytes:
        if self.name == "a.md":
            raise PermissionError(13, "Permission denied")
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", refuse)
    with pytest.raises(RuntimeError, match=r"cannot read a\.md"):
        references.scan(root)


def test_scan_exempts_the_contract_s_own_ids(tmp_path: Path) -> None:
    table = "# Contract\n\n## Resolutions\n\n| ID | Resolution |\n| --- | --- |\n" + _join(
        "| V", "6 | x |\n"
    )
    root = repo(
        tmp_path,
        {"docs/contracts.md": table, "a.md": _join("(V", "6) and (V", "5)\n")},
    )
    assert [(f.path, f.text) for f in references.scan(root)] == [("a.md", _join("V", "5"))]


@pytest.mark.parametrize(
    ("content", "error"),
    [
        ("{", "JSONDecodeError"),
        ('{"allowed": []}', "KeyError"),
        (
            '{"budget": 0, "allowed": [{"path": 1, "pattern": "p", "text": "t", "reason": "r"}]}',
            "ValueError: allowed entry {'path': 1, 'pattern': 'p', 'text': 't', 'reason': 'r'}:"
            " every field must be a string",
        ),
    ],
)
def test_run_reports_a_malformed_data_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], content: str, error: str
) -> None:
    root = repo(tmp_path, {"a.md": "plain\n"})
    bad = tmp_path / "bad.json"
    bad.write_text(content, encoding="utf-8")
    assert references.run(root, bad) == 1
    assert f"references: bad.json: {error}" in capsys.readouterr().err


def test_run_reports_an_unreadable_file_and_a_missing_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = repo(tmp_path, {"a.md": "plain\n"})
    real = Path.read_bytes

    def refuse(self: Path) -> bytes:
        if self.name == "a.md":
            raise PermissionError(13, "Permission denied")
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", refuse)
    assert references.run(root, data_file(tmp_path, 0)) == 1
    err = capsys.readouterr().err
    assert "references: cannot read a.md:" in err
    assert "git ls-files" not in err
    monkeypatch.setenv("PATH", str(tmp_path / "no-tools"))
    assert references.run(root, data_file(tmp_path, 0)) == 1
    assert "references: cannot run git in" in capsys.readouterr().err


def test_run_prints_a_file_name_that_is_not_utf_8(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = repo(tmp_path, {})
    Path(os.fsdecode(os.fsencode(root) + b"/caf\xe9.md")).write_bytes(
        _join("see ", SIGN, "7.5\n").encode()
    )
    assert references.run(root, data_file(tmp_path, 1)) == 0
    assert _join("caf\\xe9.md:1: section-citation: ", SIGN, "7") in capsys.readouterr().out


def test_run_reports_a_missing_root_and_an_unreadable_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "missing"
    assert references.run(missing, data_file(tmp_path, 0)) == 1
    assert f"references: cannot run git in {missing}" in capsys.readouterr().err
    root = repo(tmp_path, {"docs/contracts.md": "# Contract\n"})
    real = Path.read_text

    def refuse(self: Path, *args: object, **kwargs: object) -> str:
        if self.name == "contracts.md":
            raise PermissionError(13, "Permission denied")
        return real(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", refuse)
    assert references.run(root, data_file(tmp_path, 0)) == 1
    assert "references: cannot read docs/contracts.md:" in capsys.readouterr().err
