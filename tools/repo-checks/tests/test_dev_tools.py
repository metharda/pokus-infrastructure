"""No formatter or linter reaches the third-party and generated trees.

After `ruff format` run on a wrong
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
    # Installed again from Galaxy or, when it is unreachable, from the pinned
    # and checksum-verified release tarballs; then verified the same way.
    install = recipe.index("$(LOCKCHECK) collections --requirements ansible/requirements.yml")
    assert recipe.index("$(COLLECTIONS_VERIFY)", install) > install
    env = makefile[makefile.index("ANSIBLE_ENV :=") :].split("\n#", 1)[0]
    assert "PYTHONDONTWRITEBYTECODE=1" in env  # no bytecode inside the collections


def fake_uv(directory: Path, version: str) -> Path:
    directory.mkdir(parents=True)
    uv = directory / "uv"
    uv.write_text(f"#!/bin/sh\necho uv {version}\n")
    uv.chmod(0o755)
    return directory


@pytest.mark.parametrize(("version", "pinned"), [("pinned", False), ("0.0.1", True), (None, True)])
def test_setup_bootstraps_the_pinned_uv(tmp_path: Path, version: str | None, pinned: bool) -> None:
    # The uv on PATH runs uv sync only when it is the pinned version; otherwise
    # (another version, or none) setup installs the pinned uv into build/tools/
    # with bootstrap's checksum-verified install_uv and continues with it.
    expected = re.search(
        r"^readonly PINKA_UV_VERSION=(\S+)$", read("bootstrap/lib/pins.sh"), re.MULTILINE
    )
    assert expected
    path = "/usr/bin:/bin"
    if version is not None:
        bin_dir = fake_uv(tmp_path / "bin", expected.group(1) if version == "pinned" else version)
        path = f"{bin_dir}:{path}"
    done = subprocess.run(
        ["make", "-n", "-B", "--no-print-directory", ".venv/.synced"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": path, "HOME": str(tmp_path)},
    )
    lines = done.stdout.splitlines()
    if pinned:
        install = [line for line in lines if 'install_uv "$1"' in line]
        assert install, lines
        assert install[0].endswith(f" install-uv {ROOT}/build/tools"), install  # one absolute dir
        assert f"{ROOT}/build/tools/uv sync --frozen" in lines
    else:
        assert not any("install_uv" in line for line in lines), lines
        assert "uv sync --frozen" in lines


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


LOCKS = {
    "bootstrap-lock": "bootstrap/requirements.lock",
    "models-lock": "ansible/roles/models/files/requirements.lock",
    "installer-lock": "installer/requirements.lock",
}


@pytest.mark.parametrize(("target", "lock"), LOCKS.items())
def test_a_lock_recompile_keeps_the_committed_pins(target: str, lock: str) -> None:
    # uv pip compile keeps the pins of the
    # file it writes. A lock compiled into build/ on a fresh clone started from
    # nothing and moved every transitive pin the change did not touch. The
    # compile's output must hold the committed lock (without its header) first.
    dry = subprocess.run(
        ["make", "-n", "-o", "setup", "--no-print-directory", target, "UV=uv"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    commands = dry.replace("\\\n", " ").splitlines()
    compile_at = next(i for i, c in enumerate(commands) if "uv pip compile" in c)
    words = commands[compile_at].split()
    output = words[words.index("-o") + 1]
    if output == lock:
        return
    header = len([x for x in read(lock).splitlines()[:2] if x.startswith("# ")])
    assert header == 2, lock
    assert f"tail -n +3 {lock} > {output}" in commands[:compile_at], commands
