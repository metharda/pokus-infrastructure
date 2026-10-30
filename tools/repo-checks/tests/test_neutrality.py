"""The neutrality lint (BUILD_PROMPT §11, §12): infra's code names no platform.

The forbidden names are built from the lint's own lists, so this file names
none of them itself.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pinka_repo_checks import neutrality

SUBSTRING = neutrality.FORBIDDEN_SUBSTRING
TOKEN = sorted(neutrality.FORBIDDEN_TOKENS)[0]
PROJECT = neutrality.PROJECT
DISPLAY = neutrality.PROJECT_DISPLAY


@pytest.mark.parametrize(
    "line",
    [
        f"account = '{SUBSTRING}-ctl'",
        f"# {SUBSTRING.upper()} in a comment",
        f"x = {TOKEN}",
        f"the {TOKEN}.",
        f"path: /srv/{TOKEN}/x",
        f"{PROJECT}_lab = 1",
        f"name: {PROJECT}-lab",
        f"{DISPLAY.upper()} shouting",
        f"{DISPLAY}Lab",
    ],
)
def test_platform_names_are_found(line: str) -> None:
    assert neutrality.scan_line(line)


@pytest.mark.parametrize(
    "line",
    [
        "researcher1 owns /srv/pinka/shared",
        f"{TOKEN}_with_underscore = 1",
        f"file.{TOKEN}.yml",
        f"pre-{TOKEN}",
        f"{TOKEN}ue and {TOKEN}s",
        "a &gt; b",
        *neutrality.PROJECT_ALLOWED,
        *(f"repo: example-org/{form}.git" for form in neutrality.PROJECT_ALLOWED),
        f"{DISPLAY} is the display name",
    ],
)
def test_other_names_pass(line: str) -> None:
    assert neutrality.scan_line(line) == []


def test_every_finding_is_reported_with_its_line() -> None:
    text = f"ok\n{SUBSTRING} and {TOKEN}\n"
    findings = neutrality.scan_text(Path("x.py"), text)
    assert [(f.line, f.what) for f in findings] == [(2, SUBSTRING), (2, TOKEN)]
    assert str(findings[0]).startswith("x.py:2: names a particular platform")


def test_allowed_lines_are_exactly_one_listed_word() -> None:
    relative, entry = next(iter(neutrality.LISTS["allowed_lines"].items()))
    word = entry["words"][0]
    assert neutrality.scan_text(Path(relative), f'    "{word}",\n', relative) == []
    assert neutrality.scan_text(Path(relative), f'    x = "{word}",\n', relative)
    assert neutrality.scan_text(Path("other.py"), f'    "{word}",\n', "other.py")
    for entry in neutrality.LISTS["allowed_lines"].values():
        assert entry["reason"]


@pytest.mark.parametrize(
    ("relative", "scanned"),
    [
        ("docs/how-to/x.md", False),
        ("README.md", False),
        ("tools/platform/tests/fixtures/manifests/x.yaml", False),
        ("tools/platform/tests/golden/x.json", False),
        ("contract/reserved-names.yml", False),
        ("uv.lock", False),
        ("ansible/roles/platform/tasks/main.yml", True),
        ("tools/platform/tests/test_x.py", True),
        ("contract/profiles.yml", True),
    ],
)
def test_exclusions(relative: str, scanned: bool) -> None:
    assert (neutrality.excluded(relative) is None) is scanned
    for _, reason in neutrality.exclusions():
        assert reason


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def test_scan_covers_tracked_and_untracked_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    git(tmp_path, "init", "-q")
    (tmp_path / ".gitignore").write_text("ignored.txt\n")
    (tmp_path / "tracked.py").write_text("x = 1\n")
    git(tmp_path, "add", ".")
    (tmp_path / "untracked.py").write_text(f"y = '{SUBSTRING}'\n")
    (tmp_path / "ignored.txt").write_text(f"{SUBSTRING}\n")
    (tmp_path / "README.md").write_text(f"{SUBSTRING}\n")
    (tmp_path / "blob.bin").write_bytes(b"\xff\xfe" + SUBSTRING.encode())
    assert [f.path for f in neutrality.scan(tmp_path)] == [Path("untracked.py")]
    assert neutrality.run(tmp_path) == 1
    assert "neutrality: 1 finding(s)" in capsys.readouterr().err
    (tmp_path / "untracked.py").unlink()
    assert neutrality.run(tmp_path) == 0


def test_scan_without_git_walks_the_tree(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "x.py").write_text(f"{TOKEN} = 1\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "y.js").write_text(f"{SUBSTRING}\n")
    found = neutrality.scan(tmp_path)
    assert [str(f.path) for f in found] in ([], ["a/x.py"])  # [] only inside another repository


def test_the_repository_is_neutral() -> None:
    root = Path(__file__).resolve().parents[3]
    assert neutrality.scan(root) == []


def test_the_installer_and_its_answers_are_scanned() -> None:
    # No temporary exclusion is left: the installer and the CI answers are scanned.
    assert not [name for name in vars(neutrality) if name.startswith("TEMPORARY")]
    for path in ("installer/pinka_installer/app.py", "tests/answers/ci.yml"):
        assert neutrality.excluded(path) is None, path
