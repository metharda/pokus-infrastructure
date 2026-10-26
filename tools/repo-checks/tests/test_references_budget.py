"""The references budget check (pinka_repo_checks.references_budget).

Each test builds a repository whose commits carry a ``references.json``, a
``references.py`` with ``EXCLUDED`` and ``CONVENTIONAL`` and a contract with a
Resolutions table, and checks the work tree or a range of commits against the
parents.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Mapping
from pathlib import Path

import pytest

from pinka_repo_checks import cli, references, references_budget
from pinka_repo_checks.references_budget import CODE, CONTRACT, DATA

ROOT = Path(__file__).resolve().parents[3]
ENTRY = {
    "path": "a.py",
    "pattern": "instruction-file",
    "text": "an upstream table",
    "reason": "upstream",
}
OTHER = {**ENTRY, "path": "b.py"}
MAKE = ("make", "-s", "-n", "--no-print-directory")
NOTHING_ADDED = (
    "no allowlist entry, excluded path, conventional name or Resolutions identifier added"
)


@pytest.fixture(autouse=True)
def plain_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """git sees only the test's own directories and configuration, whatever the environment sets."""
    for name in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_CONFIG_PARAMETERS",
        "GIT_CONFIG_COUNT",
        "GIT_TEMPLATE_DIR",
        "GIT_NO_REPLACE_OBJECTS",
        "GIT_REPLACE_REF_BASE",
        "GIT_GRAFT_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "home" / ".config"))
    # git's messages in English, whatever the environment's language.
    monkeypatch.setenv("LC_ALL", "C")
    monkeypatch.setenv("LANGUAGE", "C")
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "researcher1")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "researcher1@example.com")


def git(root: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)
    return done.stdout.strip()


def data(budget: int, *entries: dict[str, str]) -> str:
    return json.dumps({"budget": budget, "allowed": list(entries)}, indent=2) + "\n"


def code(
    excluded: tuple[str, ...] = ("x.json",), conventional: tuple[str, ...] = ("README",)
) -> str:
    pairs = "".join(f"    ({path!r}, 'a reason'),\n" for path in excluded)
    names = "{" + ", ".join(repr(n) for n in conventional) + "}" if conventional else ""
    return (
        f"CONVENTIONAL = frozenset({names})\nEXCLUDED: tuple[tuple[str, str], ...] = (\n{pairs})\n"
    )


def contract(*ids: str) -> str:
    rows = "".join(f"| {i} | a resolution |\n" for i in ids)
    return f"# Contract\n\n## Resolutions\n\n| ID | Resolution |\n| --- | --- |\n{rows}"


BASELINE = {DATA: data(5, ENTRY), CODE: code(), CONTRACT: contract("B2", "C2, C3")}


def write(root: Path, files: Mapping[str, str | None]) -> None:
    for path, text in files.items():
        file = root / path
        if text is None:
            file.unlink()
        else:
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(text, encoding="utf-8")


def commit(root: Path, files: Mapping[str, str | None], message: str = "a change") -> str:
    write(root, files)
    git(root, "add", "-A")
    git(root, "commit", "-q", "--allow-empty", "-m", message)
    return git(root, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    commit(root, BASELINE, "the baseline")
    return root


def check_tree(root: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    status = references_budget.run(root)
    out, err = capsys.readouterr()
    return status, out, err


def check_range(
    root: Path, capsys: pytest.CaptureFixture[str], base: str, head: str, fallback: str = ""
) -> tuple[int, str, str]:
    status = references_budget.run(root, base, head, fallback)
    out, err = capsys.readouterr()
    return status, out, err


def link(root: Path, path: str) -> None:
    """Move PATH aside and put a symbolic link to it in its place."""
    target = root / "elsewhere"
    (root / path).rename(target)
    (root / path).symlink_to(target)


@pytest.mark.parametrize(
    "change",
    [
        {},
        {DATA: data(4, ENTRY)},
        {DATA: data(5)},
        {DATA: data(5, {**ENTRY, "reason": "a better reason"})},
        {CODE: code(excluded=())},
        {CODE: code(conventional=())},
        {CONTRACT: contract("B2")},
        {CONTRACT: "# Contract\n"},
        {CONTRACT: None},
    ],
    ids=[
        "unchanged",
        "budget-lowered",
        "entry-removed",
        "reason-changed",
        "exclusion-removed",
        "conventional-names-removed",
        "row-removed",
        "section-removed",
        "contract-gone",
    ],
)
def test_the_work_tree_may_lower_or_keep(
    repo: Path, capsys: pytest.CaptureFixture[str], change: dict[str, str | None]
) -> None:
    write(repo, change)
    status, out, err = check_tree(repo, capsys)
    assert (status, err) == (0, "")
    assert out.startswith("references-budget: the work tree against ")
    assert out.endswith(f" (5 before); {NOTHING_ADDED}\n")


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ({DATA: data(6, ENTRY)}, "the budget goes up from 5 to 6"),
        (
            {DATA: data(5, ENTRY, OTHER)},
            "allowlist entry added: b.py instruction-file 'an upstream table'",
        ),
        (
            {DATA: data(5, {**ENTRY, "path": "*.py"})},
            "allowlist entry added: *.py instruction-file 'an upstream table'",
        ),
        (
            {DATA: data(5, {**ENTRY, "text": "upstream"})},
            "allowlist entry added: a.py instruction-file 'upstream'",
        ),
        (
            {DATA: data(5, {**ENTRY, "pattern": "spec-name"})},
            "allowlist entry added: a.py spec-name 'an upstream table'",
        ),
        ({CODE: code(excluded=("x.json", "docs/*"))}, "excluded path added: docs/*"),
        ({CODE: code(excluded=("x*.json",))}, "excluded path added: x*.json"),
        ({CODE: code(conventional=("README", "TOOLS"))}, "conventional name added: TOOLS"),
        ({CONTRACT: contract("B2", "C2, C3", "E3")}, "Resolutions identifier added: E3"),
        ({CONTRACT: contract("B2", "C2, C3, C4")}, "Resolutions identifier added: C4"),
        ({DATA: None}, f"{DATA} is gone"),
        ({CODE: None}, f"{CODE} is gone"),
        ({DATA: "{"}, f"{DATA}: JSONDecodeError:"),
        ({DATA: '{"budget": "5", "allowed": []}'}, "the budget must be a non-negative integer"),
        ({DATA: '{"budget": 5, "allowed": [{"path": 1}]}'}, f"{DATA}: KeyError:"),
        ({DATA: "[" * 100000}, f"{DATA}: RecursionError:"),
        ({CODE: "def (\n"}, f"{CODE}: the source does not parse"),
        ({CODE: "PATHS = ()\n"}, f"{CODE}: EXCLUDED must be bound once, at the module's top"),
        ({CODE: "EXCLUDED = tuple(PATHS)\n"}, "EXCLUDED must be a tuple of (path, reason)"),
        ({CODE: "EXCLUDED = ('a',)\n"}, "EXCLUDED must be a tuple of (path, reason)"),
        ({CODE: "EXCLUDED = [('x.json', 'r')]\n"}, "EXCLUDED must be a tuple of (path, reason)"),
        ({CODE: code() + "EXCLUDED = (*EXCLUDED, ('docs/*', 'x'))\n"}, "EXCLUDED must be bound"),
        ({CODE: code() + "EXCLUDED += (('docs/*', 'x'),)\n"}, "EXCLUDED must be bound once"),
        ({CODE: code() + "if True:\n    EXCLUDED = ()\n"}, "EXCLUDED must be bound once"),
        ({CODE: code() + "from os import sep as EXCLUDED\n"}, "EXCLUDED must be bound once"),
        ({CODE: code() + "import EXCLUDED.x\n"}, "EXCLUDED must be bound once"),
        ({CODE: code() + "def f():\n    global EXCLUDED\n"}, "EXCLUDED must be bound once"),
        (
            {CODE: code() + "match x:\n    case {**EXCLUDED}:\n        pass\n"},
            "EXCLUDED must be bound once",
        ),
        ({CODE: code() + "from os import *\n"}, f"{CODE}: a star import may bind any name"),
        ({CODE: code() + "del CONVENTIONAL\n"}, "CONVENTIONAL must be bound once"),
        ({CODE: "frozenset = set\n" + code()}, "frozenset must not be bound in the module"),
        (
            {CODE: "import builtins\nbuiltins.frozenset = set\n" + code()},
            "frozenset must not be bound in the module",
        ),
        ({CODE: code() + "def f(EXCLUDED=()):\n    pass\n"}, "EXCLUDED must be bound once"),
        ({CODE: code() + "g = lambda CONVENTIONAL: 0\n"}, "CONVENTIONAL must be bound once"),
        ({CODE: code() + "def f[EXCLUDED]():\n    pass\n"}, "EXCLUDED must be bound once"),
        ({CODE: code() + "class C[frozenset]:\n    pass\n"}, "frozenset must not be bound"),
        ({CODE: code() + "type EXCLUDED = int\n"}, "EXCLUDED must be bound once"),
        (
            {CODE: code() + "try:\n    pass\nexcept OSError as EXCLUDED:\n    pass\n"},
            "EXCLUDED must be bound once",
        ),
        (
            {CODE: code() + "import sys\nsys.modules[__name__].EXCLUDED = ()\n"},
            "EXCLUDED must be bound once",
        ),
        (
            {CODE: code().replace("('x.json', 'a reason')", "(f(), 'a reason')")},
            f"{CODE}: EXCLUDED is not a literal",
        ),
        (
            {CODE: code().replace("frozenset({'README'})", "frozenset({f()})")},
            f"{CODE}: CONVENTIONAL is not a literal",
        ),
        (
            {CODE: code().replace("frozenset({'README'})", "frozenset(NAMES)")},
            "CONVENTIONAL must be frozenset({...}) of strings or frozenset()",
        ),
        (
            {CODE: code().replace("frozenset({'README'})", "{'README'}")},
            "CONVENTIONAL must be frozenset({...}) of strings or frozenset()",
        ),
        (
            {CODE: code().replace("frozenset({'README'})", "frozenset({1})")},
            "CONVENTIONAL must be frozenset({...}) of strings or frozenset()",
        ),
    ],
)
def test_the_work_tree_may_not_let_more_through(
    repo: Path, capsys: pytest.CaptureFixture[str], change: dict[str, str | None], problem: str
) -> None:
    write(repo, change)
    status, out, err = check_tree(repo, capsys)
    assert status == 1
    assert out == ""
    assert err.startswith("references-budget: the work tree against ")
    assert problem in err


def test_every_problem_is_reported(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write(
        repo,
        {
            DATA: data(9, ENTRY, OTHER),
            CODE: code(excluded=("x.json", "y/*"), conventional=("README", "TOOLS")),
            CONTRACT: contract("B2", "C2, C3", "D1"),
        },
    )
    status, _out, err = check_tree(repo, capsys)
    assert status == 1
    assert [line.split(": ", 2)[2] for line in err.splitlines()] == [
        "the budget goes up from 5 to 9",
        "allowlist entry added: b.py instruction-file 'an upstream table'",
        "excluded path added: y/*",
        "conventional name added: TOOLS",
        "Resolutions identifier added: D1",
    ]


def test_before_the_budget_there_is_nothing_to_compare_with(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A history without references.json had no lint: its contract's rows
    # count only from the commit that brings the budget.
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    commit(root, {"README.md": "a project\n", CONTRACT: contract("B2")})
    for change in ({CONTRACT: contract("B2", "E3")}, BASELINE):
        write(root, change)
        status, out, _err = check_tree(root, capsys)
        assert status == 0
        assert out.endswith(": nothing to compare with: no references budget before\n")


def test_a_removed_file_is_compared_with_its_last_version(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Gone at HEAD, back in the work tree with more: references.json and
    # references.py are compared with their last version before they were
    # removed; the contract with HEAD's, which has none, so every row counts.
    commit(repo, {DATA: None, CODE: None, CONTRACT: None}, "remove them")
    commit(repo, {"README.md": "later\n"})
    write(
        repo,
        {
            DATA: data(7, ENTRY),
            CODE: code(excluded=("x.json", "z")),
            CONTRACT: contract("B2", "V9"),
        },
    )
    status, _out, err = check_tree(repo, capsys)
    assert status == 1
    assert [line.split(": ", 2)[2] for line in err.splitlines()] == [
        "the budget goes up from 5 to 7",
        "excluded path added: z",
        "Resolutions identifier added: B2",
        "Resolutions identifier added: V9",
    ]


def test_an_unreadable_version_is_passed_over(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    commit(repo, {DATA: "{", CODE: "EXCLUDED = f()\n"}, "break them")
    write(repo, {DATA: data(6, ENTRY), CODE: code(excluded=("x.json", "z"))})
    status, _out, err = check_tree(repo, capsys)
    assert status == 1
    assert [line.split(": ", 2)[2] for line in err.splitlines()] == [
        "the budget goes up from 5 to 6",
        "excluded path added: z",
    ]


def test_a_contract_section_put_back_adds_its_identifiers(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The parent's contract as it has it: without the section, no identifiers.
    commit(repo, {CONTRACT: contract("B2", "C2, C3").replace("## Resolutions", "## Renamed")})
    write(repo, {CONTRACT: contract("B2", "C2, C3")})
    status, _out, err = check_tree(repo, capsys)
    assert status == 1
    assert [line.split(": ", 2)[2] for line in err.splitlines()] == [
        "Resolutions identifier added: B2",
        "Resolutions identifier added: C2",
        "Resolutions identifier added: C3",
    ]


def test_the_parent_is_read_without_the_lint_s_own_checks(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # An entry of a pattern the lint no longer knows, or with a blank reason,
    # still counts as the parent's.
    unknown = {**ENTRY, "pattern": "no-such-pattern", "reason": " "}
    commit(repo, {DATA: data(5, unknown)})
    write(repo, {DATA: data(5, unknown, OTHER)})
    status, _out, err = check_tree(repo, capsys)
    assert status == 1
    assert err.splitlines()[-1].endswith(
        "allowlist entry added: b.py instruction-file 'an upstream table'"
    )
    assert "no-such-pattern" not in err


@pytest.mark.parametrize(
    ("path", "named"), [(DATA, DATA), (CODE, CODE), (CONTRACT, CONTRACT), ("docs", CONTRACT)]
)
def test_a_symbolic_link_in_the_work_tree_fails(
    repo: Path, capsys: pytest.CaptureFixture[str], path: str, named: str
) -> None:
    # The lint would follow it: a link to a contract with more rows would let
    # their identifiers through.
    link(repo, path)
    status, _out, err = check_tree(repo, capsys)
    assert status == 1
    assert err.endswith(f": {named} is a symbolic link or lies below one\n")


def history(repo: Path) -> list[str]:
    """Commits after the baseline: one that keeps, one that raises, one that lowers."""
    return [
        commit(repo, {"README.md": "a\n"}, "keep"),
        commit(repo, {DATA: data(6, ENTRY), CONTRACT: contract("B2", "C2, C3", "E3")}, "raise"),
        commit(repo, {DATA: data(4, ENTRY)}, "lower"),
    ]


def test_every_commit_of_a_range_and_the_range_as_a_whole(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = git(repo, "rev-parse", "HEAD")
    keep, rise, lower = history(repo)
    status, out, err = check_range(repo, capsys, base, lower)
    assert status == 1
    assert out.splitlines() == [
        f"references-budget: {keep[:12]} against {base[:12]}: budget 5 (5 before); "
        + NOTHING_ADDED,
        f"references-budget: {lower[:12]} against {rise[:12]}: budget 4 (6 before); "
        + NOTHING_ADDED,
    ]
    assert err.splitlines() == [
        f"references-budget: {rise[:12]} against {keep[:12]}: the budget goes up from 5 to 6",
        f"references-budget: {rise[:12]} against {keep[:12]}: Resolutions identifier added: E3",
        # The head against the merge base: the row the range brings in.
        f"references-budget: {lower[:12]} against the merge base {base[:12]}:"
        " Resolutions identifier added: E3",
    ]
    # The range after the raise passes: only its own commits count.
    assert check_range(repo, capsys, rise, lower)[0] == 0


@pytest.mark.parametrize("base", ["", "0" * 40, "f" * 40, "no-such-branch"])
def test_without_a_known_base_the_fallback_or_the_head_alone(
    repo: Path, capsys: pytest.CaptureFixture[str], base: str
) -> None:
    baseline = git(repo, "rev-parse", "HEAD")
    _keep, rise, lower = history(repo)
    unknown = f"references-budget: base {base or '(none)'} is no known commit"
    status, out, _err = check_range(repo, capsys, base, lower)
    assert status == 0
    assert out.splitlines()[0] == f"{unknown}: the head is compared with its parents alone"
    assert f"{lower[:12]} against {rise[:12]}" in out
    assert check_range(repo, capsys, base, rise)[0] == 1
    status, out, _err = check_range(repo, capsys, base, lower, baseline)
    assert status == 1
    assert out.splitlines()[0] == f"{unknown}: the range starts after {baseline} instead"
    status, out, _err = check_range(repo, capsys, base, lower, "no-such-fallback")
    assert status == 0
    assert out.splitlines()[0] == (
        f"{unknown}, nor is the fallback no-such-fallback: the head is compared with its"
        " parents alone"
    )


def test_an_empty_range_checks_the_head_and_a_head_behind_passes(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    keep, rise, lower = history(repo)
    status, out, err = check_range(repo, capsys, rise, rise)
    assert status == 1
    assert out.splitlines()[0] == (
        f"references-budget: {rise[:12]}..{rise[:12]} is empty: the head is compared with its"
        " parents"
    )
    assert err.splitlines()[0].endswith("the budget goes up from 5 to 6")
    # A fallback that is the head itself (a force push to the main branch):
    # the same, with both notes.
    status, out, alone = check_range(repo, capsys, "", rise, rise)
    assert (status, alone) == (1, err)
    assert out.splitlines()[:2] == [
        f"references-budget: base (none) is no known commit: the range starts after {rise} instead",
        f"references-budget: {rise[:12]}..{rise[:12]} is empty: the head is compared with its"
        " parents",
    ]
    # A head behind its base (a branch moved back) is compared with its own
    # parents only: their merge base is the head itself.
    status, out, err = check_range(repo, capsys, lower, keep)
    assert (status, err) == (0, "")
    assert f"{lower[:12]}..{keep[:12]} is empty" in out


def test_a_pull_request_behind_its_base_passes(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The base branch lowered the budget after the branch point; the pull
    # request's own commit raises nothing against its parent.
    branch_point = git(repo, "rev-parse", "HEAD")
    main_tip = commit(repo, {DATA: data(4)}, "lower on main")
    git(repo, "checkout", "-q", "-b", "contribution", branch_point)
    head = commit(repo, {"README.md": "a fix\n"}, "a fix")
    status, out, err = check_range(repo, capsys, main_tip, head)
    assert (status, err) == (0, "")
    assert out.splitlines() == [
        f"references-budget: {head[:12]} against {branch_point[:12]}: budget 5 (5 before); "
        + NOTHING_ADDED
    ]


def test_a_root_commit_has_no_parent_to_compare_with(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root_commit = git(repo, "rev-list", "--max-parents=0", "HEAD")
    status, out, _err = check_range(repo, capsys, "", root_commit)
    assert status == 0
    assert out.splitlines()[1] == (
        f"references-budget: {root_commit[:12]}: a root commit, no parent to compare with"
    )
    # An unrelated root with a known base has no merge base either; its merge
    # into the base's branch is compared with the base (the merge tests).
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "--orphan", "unrelated")
    orphan = commit(repo, {DATA: data(99, ENTRY, OTHER)}, "an unrelated root")
    assert check_range(repo, capsys, base, orphan) == (
        0,
        f"references-budget: {orphan[:12]}: a root commit, no parent to compare with\n",
        "",
    )


def test_a_merge_is_compared_with_each_of_its_parents(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "-b", "side")
    side = commit(repo, {DATA: data(3)}, "lower on the side")
    git(repo, "checkout", "-q", "main")
    main_tip = commit(repo, {"README.md": "main\n"}, "main moves on")
    git(repo, "merge", "-q", "--no-edit", "side")
    merge = git(repo, "rev-parse", "HEAD")
    status, out, _err = check_range(repo, capsys, main_tip, merge)
    assert status == 0
    assert f"{merge[:12]} against {main_tip[:12]}: budget 3 (5 before)" in out
    assert f"{merge[:12]} against {side[:12]}: budget 3 (3 before)" in out
    assert check_range(repo, capsys, base, merge)[0] == 0
    # The same merge keeping the entry the side removed: more than the side.
    git(repo, "reset", "-q", "--hard", main_tip)
    git(repo, "merge", "-q", "--no-edit", "-s", "ours", "side")
    write(repo, {DATA: data(3, ENTRY)})
    git(repo, "commit", "-q", "--amend", "-a", "--no-edit")
    merge = git(repo, "rev-parse", "HEAD")
    status, _out, err = check_range(repo, capsys, main_tip, merge)
    assert status == 1
    assert err.splitlines() == [
        f"references-budget: {merge[:12]} against {side[:12]}: allowlist entry added:"
        " a.py instruction-file 'an upstream table'"
    ]


def test_a_merge_cannot_choose_a_first_parent_that_lets_more_through(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    older = commit(repo, {DATA: data(9, ENTRY)}, "an older, higher budget")
    tip = commit(repo, {DATA: data(5, ENTRY)}, "the tip")
    # A merge whose first parent is the older commit and whose second is the
    # tip: a fast-forward of the tip that raises the budget again.
    git(repo, "checkout", "-q", older)
    git(repo, "merge", "-q", "--no-ff", "--no-commit", "-s", "ours", tip)
    git(repo, "commit", "-q", "-m", "merge")
    merge = git(repo, "rev-parse", "HEAD")
    status, _out, err = check_range(repo, capsys, tip, merge)
    assert status == 1
    assert err.splitlines() == [
        f"references-budget: {merge[:12]} against {tip[:12]}: the budget goes up from 5 to 9"
    ]
    # A head built on the older commit and an unrelated root is a branch
    # behind the tip: it passes, and its merge into the tip's branch is
    # compared with the tip.
    git(repo, "checkout", "-q", "--orphan", "unrelated")
    git(repo, "rm", "-rqf", ".")
    git(repo, "commit", "-q", "--allow-empty", "-m", "a root")
    unrelated = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", older)
    git(repo, "merge", "-q", "--no-commit", "--allow-unrelated-histories", "-s", "ours", unrelated)
    git(repo, "commit", "-q", "-m", "merge an unrelated root")
    head = git(repo, "rev-parse", "HEAD")
    status, out, _err = check_range(repo, capsys, tip, head)
    assert status == 0
    assert f"{unrelated[:12]}: a root commit, no parent to compare with" in out
    git(repo, "checkout", "-q", tip)
    git(repo, "merge", "-q", "--no-ff", "--no-commit", "-s", "ours", head)
    write(repo, {DATA: data(9, ENTRY)})
    git(repo, "commit", "-q", "-a", "-m", "merge it into the tip's branch")
    into = git(repo, "rev-parse", "HEAD")
    status, _out, err = check_range(repo, capsys, tip, into)
    assert status == 1
    assert (
        f"references-budget: {into[:12]} against {tip[:12]}: the budget goes up from 5 to 9"
        in err.splitlines()
    )


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ({DATA: None}, f"{DATA} is gone"),
        ({CODE: "EXCLUDED = f()\n"}, f"{CODE}: EXCLUDED must be a tuple of (path, reason)"),
        ({DATA: '{"budget": -1, "allowed": []}'}, "a non-negative integer, not -1"),
    ],
)
def test_a_commit_of_a_range_that_lets_more_through_or_cannot_be_read_fails(
    repo: Path, capsys: pytest.CaptureFixture[str], change: dict[str, str | None], problem: str
) -> None:
    parent = git(repo, "rev-parse", "HEAD")
    head = commit(repo, change)
    status, _out, err = check_range(repo, capsys, parent, head)
    assert status == 1
    assert err.startswith(f"references-budget: {head[:12]} against {parent[:12]}: ")
    assert problem in err


@pytest.mark.parametrize(
    ("path", "named"), [(DATA, DATA), (CODE, CODE), (CONTRACT, CONTRACT), ("docs", CONTRACT)]
)
def test_a_symbolic_link_in_a_commit_fails(
    repo: Path, capsys: pytest.CaptureFixture[str], path: str, named: str
) -> None:
    parent = git(repo, "rev-parse", "HEAD")
    link(repo, path)
    head = commit(repo, {})
    status, _out, err = check_range(repo, capsys, parent, head)
    assert status == 1
    assert err == (
        f"references-budget: {head[:12]} against {parent[:12]}: {named} is a symbolic link"
        " or lies below one\n"
    )


def test_a_symbolic_link_beside_the_files_is_not_refused(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    parent = git(repo, "rev-parse", "HEAD")
    for path in (DATA, CONTRACT):
        (repo / path).with_name("a-link").symlink_to("elsewhere")
    head = commit(repo, {})
    assert check_range(repo, capsys, parent, head)[0] == 0
    assert check_tree(repo, capsys)[0] == 0


@pytest.mark.parametrize("path", [DATA, CODE, "tools/repo-checks/pinka_repo_checks"])
def test_a_symbolic_link_in_a_parent_is_no_readable_version(
    repo: Path, capsys: pytest.CaptureFixture[str], path: str
) -> None:
    # Passed over for the last regular version.
    link(repo, path)
    linked = commit(repo, {}, "a link")
    (repo / path).unlink()
    head = commit(repo, {DATA: data(6, ENTRY), CODE: code(excluded=("x.json", "z"))}, "files again")
    status, _out, err = check_range(repo, capsys, linked, head)
    assert status == 1
    assert [line.split(": ", 2)[2] for line in err.splitlines()] == [
        "the budget goes up from 5 to 6",
        "excluded path added: z",
    ]


@pytest.mark.parametrize("path", [CONTRACT, "docs"])
def test_a_contract_link_in_a_parent_defines_no_identifiers(
    repo: Path, capsys: pytest.CaptureFixture[str], path: str
) -> None:
    link(repo, path)
    linked = commit(repo, {}, "a link")
    (repo / path).unlink()
    head = commit(repo, {CONTRACT: contract("B2")}, "the file again")
    status, _out, err = check_range(repo, capsys, linked, head)
    assert status == 1
    assert [line.split(": ", 2)[2] for line in err.splitlines()] == [
        "Resolutions identifier added: B2"
    ]


def test_a_range_compares_a_removed_and_an_unreadable_file_with_its_last_version(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = git(repo, "rev-parse", "HEAD")
    commit(repo, {DATA: None}, "remove")
    unreadable = commit(repo, {DATA: "{"}, "unreadable")
    head = commit(repo, {DATA: data(8, ENTRY)}, "back with more")
    status, _out, err = check_range(repo, capsys, base, head)
    assert status == 1
    assert (
        f"references-budget: {head[:12]} against {unreadable[:12]}:"
        " the budget goes up from 5 to 8" in err.splitlines()
    )


def test_replace_refs_and_grafts_do_not_change_the_parents(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    keep, rise, _lower = history(repo)
    git(repo, "replace", "--graft", rise)  # rise without parents, for git's other commands
    status, _out, err = check_range(repo, capsys, "", rise)
    assert status == 1
    assert err.splitlines() == [
        f"references-budget: {rise[:12]} against {keep[:12]}: the budget goes up from 5 to 6",
        f"references-budget: {rise[:12]} against {keep[:12]}: Resolutions identifier added: E3",
    ]
    grafts = repo / ".git" / "info" / "grafts"
    grafts.parent.mkdir(exist_ok=True)
    grafts.write_text(f"{rise}\n", encoding="utf-8")
    assert check_range(repo, capsys, "", rise)[2] == (
        "references-budget: grafts change the parents: remove the graft file .git/info/grafts\n"
    )
    grafts.unlink()
    elsewhere = tmp_path / "grafts"
    elsewhere.write_text(f"{rise}\n", encoding="utf-8")
    monkeypatch.setenv("GIT_GRAFT_FILE", str(elsewhere))
    assert check_range(repo, capsys, "", rise)[2] == (
        f"references-budget: grafts change the parents: remove the graft file {elsewhere}\n"
    )


def test_a_repository_it_cannot_inspect_fails(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    real = Path.stat

    def refuse(self: Path, *args: object, **kwargs: object) -> os.stat_result:
        if self.name == "shallow":
            raise PermissionError(13, "Permission denied")
        return real(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "stat", refuse)
    assert references_budget.run(repo) == 1
    assert capsys.readouterr().err == (
        f"references-budget: cannot inspect the repository at {repo}:"
        " [Errno 13] Permission denied\n"
    )


def test_a_missing_parent_fails(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _keep, rise, lower = history(repo)
    (repo / ".git" / "objects" / rise[:2] / rise[2:]).unlink()
    status, _out, err = check_range(repo, capsys, "", lower)
    assert status == 1
    assert err.startswith(f"references-budget: git rev-list --parents {lower[:12]} failed: ")


@pytest.mark.parametrize(
    ("revisions", "message"),
    [
        (("HEAD", "no-such-commit", ""), "unknown commit: no-such-commit"),
        (("--all", "HEAD", ""), "not a revision: '--all'"),
        (("HEAD", "--output=x", ""), "not a revision: '--output=x'"),
        (("", "HEAD", "--all"), "not a revision: '--all'"),
    ],
)
def test_unknown_revisions_of_a_range_fail(
    repo: Path, capsys: pytest.CaptureFixture[str], revisions: tuple[str, str, str], message: str
) -> None:
    assert check_range(repo, capsys, *revisions) == (1, "", f"references-budget: {message}\n")


@pytest.mark.parametrize(
    ("base", "message"),
    [("no-such-base", "unknown commit: no-such-base"), ("--all", "not a revision: '--all'")],
)
def test_unknown_revisions_of_the_work_tree_fail(
    repo: Path, capsys: pytest.CaptureFixture[str], base: str, message: str
) -> None:
    assert references_budget.run(repo, base) == 1
    assert capsys.readouterr().err == f"references-budget: {message}\n"


def test_a_wrong_root_fails_and_a_path_that_is_not_utf_8_works(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert references_budget.run(repo / "docs") == 1
    err = capsys.readouterr().err
    assert err == f"references-budget: {repo / 'docs'} is not the top level of its git work tree\n"
    odd = Path(os.fsdecode(os.fsencode(tmp_path) + b"/r\xe9po"))
    repo.rename(odd)
    assert references_budget.run(odd) == 0
    assert capsys.readouterr().err == ""


def test_a_shallow_clone_no_checkout_and_no_git_fail(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    history(repo)
    shallow = tmp_path / "shallow"
    git(tmp_path, "clone", "-q", "--depth=1", repo.as_uri(), str(shallow))
    for head in ("HEAD", None):
        assert references_budget.run(shallow, "", head) == 1
        assert capsys.readouterr().err == (
            "references-budget: a shallow clone may lack the parents: fetch the whole history\n"
        )
    plain = tmp_path / "plain"
    plain.mkdir()
    assert references_budget.run(plain) == 1
    err = capsys.readouterr().err
    assert err.startswith(f"references-budget: git rev-parse in {plain} failed: ")
    assert "not a git repository" in err
    monkeypatch.setenv("PATH", str(tmp_path / "no-tools"))
    assert references_budget.run(repo) == 1
    assert capsys.readouterr().err.startswith(f"references-budget: cannot run git in {repo}: ")


@pytest.mark.parametrize("command", ["ls-tree", "log", "rev-list", "merge-base"])
def test_a_failing_git_command_fails(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], command: str
) -> None:
    base = git(repo, "rev-parse", "HEAD")
    head = commit(repo, {DATA: None}, "remove references.json")  # its parent has one
    real = references_budget.git

    def failing(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
        if args[0] == command:
            return subprocess.CompletedProcess(["git", *args], 128, b"", b"fatal: broken")
        return real(root, *args)

    monkeypatch.setattr(references_budget, "git", failing)
    write(repo, {DATA: data(5, ENTRY)})  # the work tree has it again: HEAD has none
    if command in ("ls-tree", "log"):  # HEAD's last version is looked up
        assert references_budget.run(repo) == 1
        err = capsys.readouterr().err
        assert err.startswith(f"references-budget: git {command} {head[:12]} ")
        assert err.endswith(" failed: fatal: broken\n")
    if command != "log":  # the parent has a version of its own
        assert references_budget.run(repo, base, head) == 1
        assert capsys.readouterr().err.endswith(" failed: fatal: broken\n")


def test_a_work_tree_file_it_cannot_read_fails(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    real = Path.read_text

    def refuse(self: Path, *args: object, **kwargs: object) -> str:
        if self.name == "references.json":
            raise PermissionError(13, "Permission denied")
        return real(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", refuse)
    assert references_budget.run(repo) == 1
    assert capsys.readouterr().err.startswith(f"references-budget: cannot read {DATA}: ")


def test_the_command_line(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    baseline = git(repo, "rev-parse", "HEAD")
    _keep, _rise, lower = history(repo)
    tree = ["references-budget", "--root", str(repo)]
    write(repo, {DATA: data(6, ENTRY)})
    assert cli.main(tree) == 1
    assert "the budget goes up from 4 to 6" in capsys.readouterr().err
    assert cli.main([*tree, "--base", "HEAD^"]) == 0  # 6 against the raise's 6
    argv = [*tree, "--base", "", "--head", lower]
    assert cli.main(argv) == 0  # the head alone
    capsys.readouterr()
    # The fallback is passed on: the range from the baseline has the raise.
    assert cli.main([*argv, "--fallback-base", baseline]) == 1
    assert "the budget goes up from 5 to 6" in capsys.readouterr().err
    assert cli.main([*tree, "--fallback-base", baseline]) == 2
    assert capsys.readouterr().err == "references-budget: --fallback-base needs --head\n"


def test_the_make_target_passes_the_range_on_and_ignores_the_environment() -> None:
    # make -n prints the recipe. The settings of a make this test runs under
    # (make test) or of the shell are left out.
    settings = ("MAKEFLAGS", "MFLAGS", "MAKELEVEL", "GNUMAKEFLAGS", "MAKEFILES")
    clean = {k: v for k, v in os.environ.items() if k not in settings}

    def recipe(*args: str, env: dict[str, str] | None = None) -> str:
        done = subprocess.run(
            [*MAKE, "-C", str(ROOT), "lint-references-budget", *args],
            check=True,
            capture_output=True,
            text=True,
            env={**clean, **(env or {})},
        )
        return " ".join(done.stdout.replace("\\\n", " ").split())

    command = "python3 -m pinka_repo_checks references-budget --root ."
    assert recipe().endswith(f'{command} --base "HEAD"')
    assert recipe("BUDGET_BASE=b", "BUDGET_HEAD=h", "BUDGET_FALLBACK=origin/main").endswith(
        f'{command} --base "b" --head "h" --fallback-base "origin/main"'
    )
    # A fallback without a head reaches the command, which refuses it.
    assert recipe("BUDGET_FALLBACK=f").endswith(f'{command} --base "HEAD" --fallback-base "f"')
    leaked = {"BUDGET_BASE": "b", "BUDGET_HEAD": "h", "BUDGET_FALLBACK": "f"}
    assert recipe(env=leaked).endswith(f'{command} --base "HEAD"')


def test_the_paths_are_the_repository_s_and_read_as_the_lint_reads_them() -> None:
    for path in (DATA, CODE, CONTRACT):
        assert (ROOT / path).is_file(), path
    assert Path(references.__file__).resolve() == (ROOT / CODE).resolve()
    assert references.DATA.resolve() == (ROOT / DATA).resolve()
    assert references.CONTRACT.as_posix() == CONTRACT
    source = (ROOT / CODE).read_text(encoding="utf-8")
    assert references_budget.parse_excluded(source) == {p for p, _ in references.EXCLUDED}
    assert references_budget.parse_conventional(source) == references.CONVENTIONAL
    budget, allowed = references.load(json.loads((ROOT / DATA).read_text(encoding="utf-8")))
    parsed = references_budget.parse_data((ROOT / DATA).read_text(encoding="utf-8"))
    assert parsed.budget == budget
    assert parsed.allowed == {(a.path, a.pattern, a.text) for a in allowed}
    contract_text = (ROOT / CONTRACT).read_text(encoding="utf-8")
    reader = references_budget.work_tree_reader(ROOT)
    assert references_budget.contract_ids(reader) == references.resolution_ids(contract_text)


def test_this_check_and_its_tests_have_no_references() -> None:
    for path in (Path(references_budget.__file__), Path(__file__)):
        assert references.scan_text(path.name, path.read_text(encoding="utf-8")) == []
