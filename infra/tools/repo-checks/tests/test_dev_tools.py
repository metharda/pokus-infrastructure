"""No formatter or linter reaches the third-party and generated trees.

Owner decision with the "commit" for 3d.3, after `ruff format` run on a wrong
path list reformatted the vendored lint collections: every tool names the
trees of `pinka_repo_checks.trees` explicitly, and `make setup` verifies the
collections against the lock and installs them again when they differ.
"""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml

from pinka_repo_checks import neutrality, privacy
from pinka_repo_checks.trees import THIRD_PARTY_TREES, in_third_party_tree

ROOT = Path(__file__).resolve().parents[3]


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("tree", THIRD_PARTY_TREES)
def test_ruff_never_touches_the_tree(tree: str) -> None:
    ruff = tomllib.loads(read("pyproject.toml"))["tool"]["ruff"]
    assert tree in ruff["extend-exclude"]
    assert ruff["force-exclude"] is True  # also when a path inside is passed


@pytest.mark.parametrize("tree", THIRD_PARTY_TREES)
def test_yamllint_ignores_the_tree(tree: str) -> None:
    config = yaml.safe_load(read(".yamllint.yml"))
    assert ".lint-ignore" in config["ignore-from-file"]
    assert f"/{tree}/" in read(".lint-ignore").splitlines()


@pytest.mark.parametrize("tree", THIRD_PARTY_TREES)
def test_ansible_lint_excludes_the_tree(tree: str) -> None:
    assert f"{tree}/" in yaml.safe_load(read(".ansible-lint"))["exclude_paths"]


@pytest.mark.parametrize("tree", THIRD_PARTY_TREES)
def test_markdownlint_and_cspell_ignore_the_tree(tree: str) -> None:
    assert f"{tree}/**" in yaml.safe_load(read(".markdownlint-cli2.yaml"))["ignores"]
    assert f"{tree}/**" in json.loads(read("cspell.json"))["ignorePaths"]


@pytest.mark.parametrize("tree", THIRD_PARTY_TREES)
def test_pre_commit_passes_nothing_from_the_tree(tree: str) -> None:
    pattern = yaml.safe_load(read(".pre-commit-config.yaml"))["exclude"]
    assert re.match(pattern, f"{tree}/x/y.py")
    assert not re.match(pattern, "ansible/roles/platform/tasks/main.yml")


@pytest.mark.parametrize("tree", THIRD_PARTY_TREES)
def test_the_makefile_file_list_excludes_the_tree(tree: str) -> None:
    makefile = read("Makefile")
    git_files = makefile[makefile.index("GIT_FILES =") :].split("\n\n", 1)[0]
    assert f"':!:{tree}'" in git_files


def test_the_lists_of_python_sources_and_mypy_name_no_tree() -> None:
    makefile = read("Makefile")
    sources = makefile[makefile.index("PY_SOURCES :=") : makefile.index("PLAYBOOKS :=")]
    lint_python = makefile[makefile.index("lint-python:") :].split("\n\n", 1)[0]
    for tree in THIRD_PARTY_TREES:
        assert not re.search(rf"(^|\s){re.escape(tree)}(/|\s|$)", sources + lint_python)


def test_setup_verifies_the_collections_against_the_lock() -> None:
    makefile = read("Makefile")
    assert re.search(r"^setup: .*\bdev-collections\b", makefile, re.MULTILINE)
    recipe = makefile[makefile.index("dev-collections: ") :].split("\n\n", 1)[0]
    verify = makefile[makefile.index("COLLECTIONS_VERIFY =") :].split("\n\n", 1)[0]
    assert "collection verify --offline" in verify
    assert "-r ansible/requirements.yml" in verify
    assert "collection install --force" in recipe
    env = makefile[makefile.index("ANSIBLE_ENV :=") :].split("\n#", 1)[0]
    assert "PYTHONDONTWRITEBYTECODE=1" in env  # no bytecode inside the collections


def test_git_ignores_every_tree() -> None:
    for tree in THIRD_PARTY_TREES:
        done = subprocess.run(
            ["git", "check-ignore", "-q", "--no-index", f"{tree}/x"], cwd=ROOT, check=False
        )
        assert done.returncode == 0, tree


@pytest.mark.parametrize("tree", THIRD_PARTY_TREES)
def test_the_scans_skip_the_tree_outside_git(tmp_path: Path, tree: str) -> None:
    (tmp_path / tree / "pkg").mkdir(parents=True)
    (tmp_path / tree / "pkg" / "x.py").write_text("x = 1\n")
    (tmp_path / "kept.py").write_text("y = 2\n")
    assert in_third_party_tree(Path(tree) / "pkg" / "x.py")
    assert not in_third_party_tree(Path("ansible") / tree / "x.py")
    names = [p.name for p in privacy.list_files([tmp_path])]
    assert names == ["kept.py"]
    assert neutrality._walk(tmp_path) == ["kept.py"]
