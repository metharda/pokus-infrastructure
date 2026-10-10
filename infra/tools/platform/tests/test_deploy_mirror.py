"""Mirrors, refs, signed tags and extraction (§7.14 steps 2-3; ported from contract v1)."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
from deploy_testlib import git, make_repo

from pinka_platform import mirror


@pytest.fixture
def source(tmp_path: Path) -> Path:
    return make_repo(tmp_path)


@pytest.fixture
def bare(tmp_path: Path, source: Path) -> Path:
    path = tmp_path / "git" / "mirror.git"
    assert mirror.fetch(mirror.run_git, path, str(source), {}) is True
    return path


def test_fetch_is_idempotent_and_follows_new_refs(bare: Path, source: Path) -> None:
    assert mirror.fetch(mirror.run_git, bare, str(source), {}) is False
    git(source, "tag", "-a", "allprof-v2.0.0", "-m", "next")
    assert mirror.fetch(mirror.run_git, bare, str(source), {}) is True
    tags = git(bare, "tag").split()
    assert "allprof-v2.0.0" in tags
    assert not (bare / "FETCH_HEAD").exists()
    assert oct((bare.parent.stat().st_mode) & 0o777) == oct(0o700)


def test_fetch_failure(tmp_path: Path) -> None:
    with pytest.raises(mirror.FetchError, match="fetching"):
        mirror.fetch(mirror.run_git, tmp_path / "m.git", str(tmp_path / "nothing"), {})


def test_ssh_environment() -> None:
    assert mirror.ssh_env(None, None) == {}
    env = mirror.ssh_env("/var/lib/pinka/git/deploy_key", "/var/lib/pinka/git/known_hosts")
    command = env["GIT_SSH_COMMAND"]
    assert "-o StrictHostKeyChecking=yes" in command
    assert "-o UserKnownHostsFile=/var/lib/pinka/git/known_hosts" in command
    assert "-o BatchMode=yes" in command
    assert "-o GlobalKnownHostsFile=/dev/null" in command
    with pytest.raises(mirror.FetchError, match="pinned host keys"):
        mirror.ssh_env("/key", None)


def test_resolve_a_release_tag(bare: Path, source: Path) -> None:
    resolved = mirror.resolve(mirror.run_git, bare, "allprof-v1.2.3", "platform", "allprof-v")
    assert resolved == mirror.Resolved(
        git(source, "rev-parse", "HEAD"),
        git(source, "rev-parse", "HEAD:platform"),
        "allprof-v1.2.3",
        "allprof-v1.2.3",
    )


def test_branches_and_commits_are_named_by_the_commit(bare: Path, source: Path) -> None:
    commit = git(source, "rev-parse", "HEAD")
    by_branch = mirror.resolve(mirror.run_git, bare, "main", "platform", "allprof-v")
    assert by_branch.name == commit
    assert by_branch.tag == "allprof-v1.2.3"  # the release tag the commit carries
    assert mirror.resolve(mirror.run_git, bare, commit, "platform", "allprof-v") == by_branch
    other_prefix = mirror.resolve(mirror.run_git, bare, "main", "cases", "cases-v")
    assert other_prefix.tag == "cases-v1"


def test_the_root_of_the_repository(bare: Path, source: Path) -> None:
    resolved = mirror.resolve(mirror.run_git, bare, "main", "", "")
    assert resolved.tree == git(source, "rev-parse", "HEAD^{tree}")


@pytest.mark.parametrize(
    ("ref", "path", "code"),
    [
        ("nope", "platform", "source-ref"),
        ("0" * 40, "platform", "source-ref"),
        ("main", "missing", "source-path"),
        ("main", "guides/intro.md", "source-path"),
    ],
)
def test_bad_refs_and_paths(bare: Path, ref: str, path: str, code: str) -> None:
    with pytest.raises(mirror.SourceError) as exc:
        mirror.resolve(mirror.run_git, bare, ref, path, "")
    assert exc.value.code == code


def test_extract(tmp_path: Path, bare: Path, source: Path) -> None:
    target = tmp_path / "out"
    target.mkdir()
    mirror.extract(bare, git(source, "rev-parse", "HEAD"), "cases", target)
    assert sorted(p.name for p in target.iterdir()) == ["run.sh", "scenarios"]
    assert (target / "run.sh").stat().st_mode & 0o100
    assert not list(target.rglob(".git"))


def test_extract_refuses_links_leaving_the_tree(tmp_path: Path, bare: Path, source: Path) -> None:
    (source / "cases" / "escape").symlink_to("/etc/passwd")
    git(source, "add", ".")
    git(source, "commit", "--quiet", "-m", "link")
    mirror.fetch(mirror.run_git, bare, str(source), {})
    target = tmp_path / "out"
    target.mkdir()
    with pytest.raises(mirror.SourceError) as exc:
        mirror.extract(bare, git(source, "rev-parse", "HEAD"), "cases", target)
    assert exc.value.code == "source-unsafe-entry"


def test_the_tar_filter_refuses_git_entries() -> None:
    import tarfile  # noqa: PLC0415 - only here

    with pytest.raises(mirror.SourceError) as exc:
        mirror._no_git_entries(tarfile.TarInfo("sub/.git/config"), "/tmp/out")  # noqa: S108
    assert exc.value.code == "source-git-entry"


def test_verify_tag_reads_the_signer() -> None:
    def fake(argv: Sequence[str], env: Mapping[str, str]) -> mirror.Done:
        assert "gpg.format=ssh" in argv
        assert argv[-1] == "refs/tags/allprof-v1.2.3"
        return mirror.Done(
            0, "", 'Good "git" signature for releases@example.com with ED25519 key SHA256:abc\n'
        )

    signers = Path(__file__)  # any non-empty file
    assert mirror.verify_tag(fake, Path("/m"), "allprof-v1.2.3", signers) == "releases@example.com"


def test_verify_tag_needs_signers_and_a_signature(tmp_path: Path, bare: Path) -> None:
    with pytest.raises(mirror.SourceError, match="no allowed signers"):
        mirror.verify_tag(mirror.run_git, bare, "allprof-v1.2.3", tmp_path / "none")
    signers = tmp_path / "allowed_signers"
    signers.write_text("releases@example.com ssh-ed25519 AAAAC3Nza\n")
    with pytest.raises(mirror.SourceError, match="no valid signature"):
        mirror.verify_tag(mirror.run_git, bare, "allprof-v1.2.3", signers)


def require_ssh_keygen() -> None:
    """Skip without ssh-keygen locally; in CI (CI=true) it must be there.

    Owner decision on 3d.2.
    """
    if shutil.which("ssh-keygen") is not None:
        return
    if os.environ.get("CI", "").lower() == "true":
        pytest.fail("ssh-keygen is missing in CI; the signed-tag test must run there")
    pytest.skip("needs ssh-keygen (OpenSSH); allowed to skip only outside CI")


def test_ssh_signed_tags_end_to_end(tmp_path: Path, source: Path) -> None:
    require_ssh_keygen()
    key = tmp_path / "signing"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "", "-f", str(key)], check=True
    )
    public = (tmp_path / "signing.pub").read_text().split()
    signers = tmp_path / "allowed_signers"
    signers.write_text(f"releases@example.com {public[0]} {public[1]}\n")
    git(
        source,
        "-c",
        "gpg.format=ssh",
        "-c",
        f"user.signingkey={key}",
        "tag",
        "-s",
        "allprof-v1.2.4",
        "-m",
        "signed",
    )
    bare = tmp_path / "m.git"
    mirror.fetch(mirror.run_git, bare, str(source), {})
    assert mirror.verify_tag(mirror.run_git, bare, "allprof-v1.2.4", signers) == (
        "releases@example.com"
    )
    with pytest.raises(mirror.SourceError, match="no valid signature"):
        mirror.verify_tag(mirror.run_git, bare, "allprof-v1.2.3", signers)


def test_the_signed_tag_test_only_skips_outside_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.setenv("CI", "true")
    with pytest.raises(pytest.fail.Exception, match="ssh-keygen is missing in CI"):
        require_ssh_keygen()
    monkeypatch.delenv("CI")
    with pytest.raises(pytest.skip.Exception):
        require_ssh_keygen()
