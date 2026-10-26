"""Which CI lanes a change needs (pinka_repo_checks.changes)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pinka_repo_checks import changes


@pytest.mark.parametrize(
    ("paths", "only_docs"),
    [
        (["docs/how-to/connect.md"], True),
        (
            ["SECURITY.md", "docs/explanation/capacity-planning.md", "ansible/roles/ssh/README.md"],
            True,
        ),
        (["docs/assets/installer/remote.svg", "mkdocs.yml", "project-words.txt"], True),
        (["installer-answers/README.md"], True),
        (["docs/how-to/connect.md", "ansible/roles/ssh/defaults/main.yml"], False),
        (["Makefile"], False),
        (["tools/gen/pinka_gen/pages.py"], False),
        ([], False),  # nothing known: every lane
    ],
)
def test_docs_only(paths: list[str], only_docs: bool) -> None:
    assert changes.docs_only(paths) is only_docs


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return done.stdout.strip()


def commit(repo: Path, path: str) -> str:
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text(path + "\n", encoding="utf-8")
    git(repo, "add", path)
    git(repo, "-c", "user.name=ci", "-c", "user.email=ci@example.com", "commit", "-qm", path)
    return git(repo, "rev-parse", "HEAD")


def test_the_output_for_a_range(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    git(tmp_path, "init", "-q")
    first = commit(tmp_path, "README.md")
    docs = commit(tmp_path, "docs/a.md")
    code = commit(tmp_path, "ansible/site.yml")
    cases = [
        ((first, docs), "ansible=false"),
        ((first, code), "ansible=true"),
        (("0" * 40, docs), "ansible=true"),  # a new branch
        (("", docs), "ansible=true"),  # a schedule or a manual run
        (("f" * 40, docs), "ansible=true"),  # a commit git does not know (force push)
        (("--output=x", docs), "ansible=true"),  # not a commit ID: never passed to git
    ]
    for (base, head), expected in cases:
        with pytest.MonkeyPatch.context() as patch:
            patch.chdir(tmp_path)
            assert changes.main([f"--base={base}", f"--head={head}"]) == 0
        assert capsys.readouterr().out.strip() == expected, (base, head)
