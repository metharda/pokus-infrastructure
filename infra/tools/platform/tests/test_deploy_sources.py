"""Source settings (§7.14 step 1): transports, paths, refs, packages and overlaps."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from pinka_platform import sources

GOOD = {"repo": "https://git.example.com/example-org/platform.git", "ref": "main"}


@pytest.mark.parametrize(
    ("repo", "transport"),
    [
        ("git@github.com:example-org/pokus-platform.git", "ssh"),
        ("ssh://git@git.example.com/example-org/pokus-platform.git", "ssh"),
        ("ssh://git@git.example.com:2222/example-org/pokus-platform.git", "ssh"),
        ("https://git.example.com/example-org/pokus-platform.git", "https"),
        ("https://git.example.com:8443/pokus-platform.git", "https"),
        ("file:///srv/git/pokus-platform.git", "file"),
        ("https://user:token@git.example.com/pokus-platform.git", None),
        ("https://token@git.example.com/pokus-platform.git", None),
        ("ssh://user:secret@git.example.com/pokus-platform.git", None),
        ("http://git.example.com/pokus-platform.git", None),
        ("file://relative/path", None),
        ("file:///srv/../etc/repo", None),
        ("/srv/git/repo", None),
        ("ftp://git.example.com/repo", None),
    ],
)
def test_transports(repo: str, transport: str | None) -> None:
    assert sources.transport(repo) == transport


@pytest.mark.parametrize(
    ("path", "normal"),
    [
        ("", ""),
        (".", ""),
        ("platform", "platform"),
        ("./platform/", "platform"),
        ("a//b/./c", "a/b/c"),
        ("/platform", None),
        ("../platform", None),
        ("a/../b", None),
    ],
)
def test_paths_are_normalized(path: str, normal: str | None) -> None:
    assert sources.normalize_path(path) == normal


def codes(platform: Any, content: Any = None) -> list[str]:
    return [p.code for p in sources.parse(platform, content)[2]]


def test_a_complete_setting() -> None:
    release, packages, problems = sources.parse(
        {**GOOD, "path": "./platform/", "tag_prefix": "platform-v"},
        {"catalog": {**GOOD, "path": "catalog"}},
    )
    assert problems == []
    assert release == sources.Source(None, GOOD["repo"], "main", "platform", "platform-v")
    assert packages["catalog"].path == "catalog"
    assert packages["catalog"].label == "content.catalog"
    assert release is not None
    assert release.as_dict() == {
        "repo": GOOD["repo"],
        "ref": "main",
        "path": "platform",
        "tag_prefix": "platform-v",
    }


@pytest.mark.parametrize(
    ("platform", "code"),
    [
        ({"ref": "main"}, "source-setting"),
        ({"repo": GOOD["repo"]}, "source-setting"),
        ({**GOOD, "branch": "x"}, "source-setting"),
        ({**GOOD, "ref": "../etc"}, "source-setting"),
        ({**GOOD, "ref": "-x"}, "source-setting"),
        ({**GOOD, "path": "/etc"}, "source-setting"),
        ({**GOOD, "tag_prefix": "-v"}, "source-setting"),
        ({**GOOD, "repo": "http://git.example.com/x.git"}, "source-transport"),
        ("not a mapping", "source-setting"),
    ],
)
def test_bad_platform_settings(platform: Any, code: str) -> None:
    assert code in codes(platform)


def test_bad_content_settings() -> None:
    assert codes(GOOD, ["catalog"]) == ["source-setting"]
    assert codes(GOOD, {"Bad_Name": GOOD}) == ["source-setting"]
    assert codes(GOOD, {"catalog": {"repo": GOOD["repo"]}}) == ["source-setting"]
    assert codes(GOOD, None) == []


@pytest.mark.parametrize(
    ("release_path", "package_path", "overlaps"),
    [("platform", "catalog", False), ("platform", "", True), ("platform", "platform/x", True)],
)
def test_overlaps_in_one_repository(release_path: str, package_path: str, overlaps: bool) -> None:
    found = codes(
        {**GOOD, "path": release_path},
        {"catalog": {**GOOD, "repo": GOOD["repo"].removesuffix(".git"), "path": package_path}},
    )
    assert found == (["source-overlap"] if overlaps else [])


def test_mirror_names() -> None:
    a = sources.mirror_name("git@github.com:example-org/pokus-platform.git")
    assert a == sources.mirror_name("git@github.com:example-org/pokus-platform")
    assert a.startswith("pokus-platform-")
    assert a.endswith(".git")
    assert a != sources.mirror_name("git@github.com:example-org/other.git")
    assert sources.mirror_name("file:///").endswith(".git")


def test_file_repositories_must_be_safe(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir(mode=0o755)
    uid = os.getuid()
    url = f"file://{repo}"
    assert sources.file_repo_problem(url, uid) is None
    repo.chmod(0o775)
    assert "writable by group" in str(sources.file_repo_problem(url, uid))
    repo.chmod(0o755)
    assert "does not exist" in str(sources.file_repo_problem(f"file://{tmp_path}/none", uid))
    (tmp_path / "file").write_text("x")
    assert "not a directory" in str(sources.file_repo_problem(f"file://{tmp_path}/file", uid))
    if uid != 0:
        assert "not owned by root" in str(sources.file_repo_problem(url, 0))
