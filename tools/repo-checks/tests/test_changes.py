"""Which CI lanes a change needs (pinka_repo_checks.changes)."""

from __future__ import annotations

import os
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


@pytest.mark.parametrize(
    ("paths", "docs", "tests"),
    [
        (["docs/how-to/connect.md"], True, False),
        (["ansible/roles/ssh/README.md"], True, False),
        (["mkdocs.yml"], True, False),
        (["package-lock.json"], True, False),  # mermaid-cli's pin
        (["ansible/roles/ssh/tasks/main.yml"], False, False),
        (["preflight/pinka_preflight/detect.py"], False, False),
        (["ansible/tests/test_ssh.py"], False, True),
        (["installer/tests/data/events.jsonl"], False, True),
        (["installer/tests/__snapshots__/test_tui_snapshots/x.svg"], False, True),
        (["tools/platform/tests/fixtures/manifests/refplat.yml"], False, True),
        (["installer/fixtures/answers.yml"], False, True),  # a fixture outside tests/
        (["tests/reference-platform/jobs/tune.py"], False, True),
        (["conftest.py"], False, True),
        (["tools/test-distros/conftest.py"], False, True),
        (["tools/repo-checks/pinka_repo_checks/gate.py"], True, True),
        (["Makefile"], True, True),
        (["pyproject.toml"], True, True),
        (["uv.lock"], True, True),
    ],
)
def test_what_a_local_change_needs(paths: list[str], docs: bool, tests: bool) -> None:
    assert changes.touches_docs(paths) is docs
    assert changes.touches_tests(paths) is tests


def test_a_local_tree_against_its_base(tmp_path: Path) -> None:
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "core.quotePath", "true")  # git's default, whatever the user's
    commit(tmp_path, "docs/old.md")
    (tmp_path / ".git" / "info" / "exclude").write_text("build/\n")
    commit(tmp_path, "ansible/site.yml")
    git(tmp_path, "checkout", "-qb", "work")
    commit(tmp_path, "ansible/roles/a/tasks/main.yml")  # committed on the branch
    git(tmp_path, "mv", "docs/old.md", "docs/new.md")  # staged rename: both names
    (tmp_path / "ansible/site.yml").write_text("changed\n")  # unstaged
    (tmp_path / "ansible/tests").mkdir()
    (tmp_path / "ansible/tests/test_new.py").write_text("")  # untracked
    (tmp_path / "build").mkdir()
    (tmp_path / "build/out.log").write_text("")  # ignored
    with pytest.MonkeyPatch.context() as patch:
        patch.chdir(tmp_path)
        paths = changes.local_changed("main")
        assert paths == [
            "ansible/roles/a/tasks/main.yml",
            "ansible/site.yml",
            "ansible/tests/test_new.py",
            "docs/new.md",
            "docs/old.md",
        ]
        assert changes.local_changed("no-such-branch") is None
        # Never an option of git's: such a base tells nothing.
        assert changes.local_changed("--independent") is None
        # A name git would quote is read as it is, committed or untracked.
        commit(tmp_path, "docs/naïve.md")
        (tmp_path / "docs/café.md").write_text("x\n")
        # One that is not UTF-8 at all is read too, not refused.
        (tmp_path / os.fsdecode(b"docs/\xff.md")).write_text("x\n")
        found = changes.local_changed("main") or []
        assert {"docs/naïve.md", "docs/café.md", os.fsdecode(b"docs/\xff.md")} <= set(found)
    steps = ["lint-python", "lint-mermaid", "docs-build", "test-random-order", "test-isolated"]
    assert changes.local_skips(steps, paths, "main") == {}
    skips = changes.local_skips(steps, ["ansible/site.yml"], "main")
    assert sorted(skips) == ["docs-build", "lint-mermaid", "test-isolated", "test-random-order"]
    assert skips["docs-build"] == (
        "no change of the docs or what checks them against main (1 changed path(s)); "
        "CI runs it on every push"
    )
    assert skips["test-isolated"].endswith("; CI runs it on every push (infra-isolated)")
    assert sorted(changes.local_skips(steps, ["docs/a.md"], "main")) == [
        "test-isolated",
        "test-random-order",
    ]
    # Nothing known: every step runs.
    assert changes.local_skips(steps, None, "main") == {}
