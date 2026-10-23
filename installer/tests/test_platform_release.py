"""The installer's inspection of a platform release (BUILD_PROMPT §18.4 screens 12-13).

The inspection is the platform tool's own code; these tests run it on the
tool's test repository (the allprof release and its content packages in a
``file://`` repository) the way the TUI and the answers replay do.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from deploy_testlib import git, make_repo
from platform_testlib import manifest

from pinka_installer import answers as answers_mod
from pinka_installer import release
from pinka_installer.paths import INFRA
from pinka_platform import deploy, names

LOCK = INFRA / "versions.lock.yml"


def answers_for(repo: Path, **content: answers_mod.Source) -> answers_mod.Answers:
    answers = answers_mod.Answers()
    answers.machine.name = "pinka-lab"
    url = f"file://{repo}"
    answers.platform = answers_mod.Source(url, "allprof-v1.2.3", "platform", "allprof-v")
    answers.content = dict(content)
    return answers


def inspect(answers: answers_mod.Answers, root: Path, *, full: bool) -> release.Inspection:
    return release.inspect(
        answers,
        LOCK,
        full=full,
        paths=deploy.Paths.under(root),
        host=lambda _: names.HostFacts(),
        owner_uid=os.getuid(),
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return make_repo(tmp_path)


def test_the_release_alone_then_every_answer(repo: Path, tmp_path: Path) -> None:
    alone = inspect(answers_for(repo), tmp_path / "host", full=False)
    assert alone.ok, alone.problems or alone.error
    data = manifest("allprof")
    assert [c["name"] for c in alone.found["content"]] == [c["name"] for c in data["content"]]
    assert alone.needs.secrets == ("ctl.token",)
    assert "allprof_ctl" in alone.needs.roles
    url = f"file://{repo}"
    full = inspect(
        answers_for(
            repo,
            cases=answers_mod.Source(url, "cases-v1", "cases", "cases-v"),
            guides=answers_mod.Source(url, "main", "guides"),
        ),
        tmp_path / "host",
        full=True,
    )
    assert full.ok, full.problems or full.error
    assert full.found["summary"]["name"] == "allprof"
    assert full.found["content_sources"]["cases"]["restricted"] is True
    assert full.needs == alone.needs
    assert not list((tmp_path / "host" / "var" / "lib" / "pinka" / "staging").glob("run-*"))


def test_a_refusal_and_an_error_are_reported(repo: Path, tmp_path: Path) -> None:
    # A required package without a source is the deployment's own refusal.
    refused = inspect(answers_for(repo), tmp_path / "host", full=True)
    assert not refused.ok
    assert any("cases" in p for p in refused.problems), refused.problems
    gone = answers_for(repo)
    gone.platform.ref = "allprof-v9.9.9"
    missing = inspect(gone, tmp_path / "host", full=False)
    assert not missing.ok and missing.problems
    git(repo, "tag", "-d", "allprof-v1.2.3")
    elsewhere = answers_for(tmp_path / "no-such-repo")
    assert not inspect(elsewhere, tmp_path / "host", full=False).ok


def test_ssh_sources_get_the_deploy_key_and_pinned_host_keys(tmp_path: Path) -> None:
    answers = answers_mod.Answers()
    answers.platform = answers_mod.Source("git@git.example.com:example-org/x.git", "v1")
    answers.content = {
        "cases": answers_mod.Source("https://git.example.com/example-org/c.git", "v1")
    }
    answers.source_trust.known_hosts = ["git.example.com ssh-ed25519 AAAAexample"]
    answers.source_trust.allowed_signers = ["researcher1@example.com ssh-ed25519 AAAAexample"]
    settings = release.tool_settings(answers, tmp_path, LOCK, deploy_key=tmp_path / "key")
    assert settings["ssh_key"] == str(tmp_path / "key")
    assert (tmp_path / "known_hosts").read_text() == "git.example.com ssh-ed25519 AAAAexample\n"
    assert (tmp_path / "allowed_signers").read_text().startswith("researcher1@example.com")
    env = release.access_env(answers, tmp_path, LOCK, deploy_key=tmp_path / "key")
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert str(tmp_path / "key") in env["GIT_SSH_COMMAND"]
    assert release.ssh_repos(answers) == ["git@git.example.com:example-org/x.git"]
    answers.platform.repo = "https://git.example.com/example-org/x.git"
    assert release.tool_settings(answers, tmp_path, LOCK)["ssh_key"] == ""


def test_github_keys_are_pinned_only_when_verified(tmp_path: Path) -> None:
    lock = tmp_path / "versions.lock.yml"
    lock.write_text(
        "github_ssh_host_keys:\n  keys:\n"
        '    ssh-ed25519: {public_key: "AAAAverified", fingerprint: "x"}\n'
        '    ssh-rsa: {public_key: "TODO(verify)", fingerprint: "TODO(verify)"}\n'
    )
    assert release.github_known_hosts(lock) == ["github.com ssh-ed25519 AAAAverified"]
    answers = answers_mod.Answers()
    answers.platform = answers_mod.Source("git@github.com:example-org/x.git", "v1")
    assert release.known_host_lines(answers, lock) == ["github.com ssh-ed25519 AAAAverified"]


def test_a_review_follows_the_platform_answers(repo: Path) -> None:
    answers = answers_for(repo)
    key = release.review_key(answers)
    answers.platform_settings = {"ctl": {"x": 1}}
    assert release.review_key(answers) != key
    answers.platform_settings = {}
    answers.gpu.default_model = "something-else"  # not a platform answer
    assert release.review_key(answers) == key


def test_another_active_platform_needs_the_replace_option(repo: Path, tmp_path: Path) -> None:
    # bootstrap --replace-platform reaches the inspection (answers replay and TUI).
    paths = deploy.Paths.under(tmp_path / "host")
    paths.active.parent.mkdir(parents=True)
    paths.active.write_text('{"name": "other"}\n')
    url = f"file://{repo}"
    answers = answers_for(
        repo,
        cases=answers_mod.Source(url, "cases-v1", "cases", "cases-v"),
        guides=answers_mod.Source(url, "main", "guides"),
    )

    def run(replace: str) -> release.Inspection:
        return release.inspect(
            answers,
            LOCK,
            full=True,
            paths=paths,
            host=lambda _: names.HostFacts(),
            owner_uid=os.getuid(),
            replace=replace,
        )

    refused = run("")
    assert not refused.ok
    assert any("--replace-platform other" in p for p in refused.problems), refused.problems
    replaced = run("other")
    assert replaced.ok, replaced.problems or replaced.error
