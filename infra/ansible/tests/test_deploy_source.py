"""Tests for the deploy_source helper: mirror, refs, signed tags, extraction, provenance."""

from __future__ import annotations

import datetime as dt
import grp
import json
import os
import pwd
import shutil
import stat
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pokus_deploy_source as ds
import pytest

from ansible_testlib import GROUP_VARS, ROLES, ansible_vars, evaluate, load_yaml

ME = pwd.getpwuid(os.getuid()).pw_name
MY_GROUP = grp.getgrgid(os.getgid()).gr_name
NOW = dt.datetime(2026, 1, 1, 12, 0, 0, tzinfo=dt.UTC)


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


@pytest.fixture
def source(tmp_path: Path) -> Path:
    repo = tmp_path / "src"
    (repo / "platform" / "pkg").mkdir(parents=True)
    (repo / "catalog" / "scenarios").mkdir(parents=True)
    (repo / "platform" / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    (repo / "platform" / "pkg" / "run.sh").write_text("#!/bin/sh\n")
    (repo / "platform" / "pkg" / "run.sh").chmod(0o755)
    (repo / "catalog" / "scenarios" / "s.yml").write_text("id: CONTROL-000\n")
    git(repo, "init", "--quiet", "--initial-branch=main")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "one")
    git(repo, "tag", "-a", "platform-v0.1.0", "-m", "platform 0.1.0")
    git(repo, "tag", "-a", "catalog-v0.1.0", "-m", "catalog 0.1.0")
    return repo


def prepare(tmp_path: Path, component: str, ref: str, **kwargs: Any) -> dict[str, Any]:
    params: dict[str, Any] = {
        "root": tmp_path / "deploy" / component,
        "repo": "file:///example",
        "owner": ME,
        "group": MY_GROUP,
        "dir_mode": 0o750 if component == "catalog" else 0o755,
        "file_mode": 0o640 if component == "catalog" else 0o644,
        "allowed_signers": None,
        "require_signed": False,
    }
    params.update(kwargs)
    return ds.prepare(
        ds.run_git, tmp_path / "mirror.git", component=component, ref=ref, now=lambda: NOW, **params
    )


@pytest.fixture
def mirror(tmp_path: Path, source: Path) -> Path:
    result = ds.fetch(ds.run_git, tmp_path / "mirror.git", str(source), {})
    assert result == {"changed": True}
    return tmp_path / "mirror.git"


def test_fetch_is_idempotent_and_follows_new_refs(mirror: Path, source: Path) -> None:
    assert ds.fetch(ds.run_git, mirror, str(source), {}) == {"changed": False}
    git(source, "tag", "-a", "platform-v0.2.0", "-m", "next")
    assert ds.fetch(ds.run_git, mirror, str(source), {}) == {"changed": True}
    tags = subprocess.run(
        ["git", "--git-dir", str(mirror), "tag"], capture_output=True, text=True, check=True
    ).stdout.split()
    assert "platform-v0.2.0" in tags
    assert not (mirror / "FETCH_HEAD").exists()


def test_ssh_environment() -> None:
    assert ds.ssh_env(None, None) == {}
    env = ds.ssh_env("/var/lib/pokus/git/deploy_key", "/var/lib/pokus/git/known_hosts")
    command = env["GIT_SSH_COMMAND"]
    assert "-o StrictHostKeyChecking=yes" in command
    assert "-o UserKnownHostsFile=/var/lib/pokus/git/known_hosts" in command
    assert "-o BatchMode=yes" in command
    with pytest.raises(ds.DeployError, match="pinned host keys"):
        ds.ssh_env("/key", None)


def test_prepare_a_component_tag(tmp_path: Path, mirror: Path, source: Path) -> None:
    result = prepare(tmp_path, "platform", "platform-v0.1.0")
    target = tmp_path / "deploy" / "platform" / "platform-v0.1.0"
    assert result["changed"] is True
    assert result["path"] == str(target)
    assert sorted(p.name for p in target.iterdir()) == [
        "DEPLOYED_FROM.json",
        "pkg",
        "pyproject.toml",
    ]
    commit = git(source, "rev-parse", "HEAD")
    provenance = json.loads((target / "DEPLOYED_FROM.json").read_text())
    assert provenance == {
        "schema_version": 1,
        "component": "platform",
        "source_repo": "file:///example",
        "requested_ref": "platform-v0.1.0",
        "tag": "platform-v0.1.0",
        "commit": commit,
        "subdirectory": "platform",
        "tree": git(source, "rev-parse", "HEAD:platform"),
        "signed_tag": {"required": False, "verified": False, "signer": None},
        "deployed_at": "2026-01-01T12:00:00Z",
    }
    assert stat.S_IMODE((target / "pkg" / "run.sh").stat().st_mode) == 0o755
    assert stat.S_IMODE((target / "pyproject.toml").stat().st_mode) == 0o644
    assert stat.S_IMODE((target / "DEPLOYED_FROM.json").stat().st_mode) == 0o644
    assert (target / "DEPLOYED_FROM.json").stat().st_uid == pwd.getpwnam(ME).pw_uid
    assert not list(target.rglob(".git"))
    assert not list((tmp_path / "deploy" / "platform").glob(".platform-v0.1.0.*"))


def test_catalog_modes_and_no_platform_content(tmp_path: Path, mirror: Path) -> None:
    prepare(tmp_path, "catalog", "catalog-v0.1.0")
    target = tmp_path / "deploy" / "catalog" / "catalog-v0.1.0"
    assert stat.S_IMODE(target.stat().st_mode) == 0o750
    assert stat.S_IMODE((target / "scenarios").stat().st_mode) == 0o750
    assert stat.S_IMODE((target / "scenarios" / "s.yml").stat().st_mode) == 0o640
    assert not (target / "pyproject.toml").exists()
    platform = prepare(tmp_path, "platform", "platform-v0.1.0")
    assert not list(Path(platform["path"]).rglob("s.yml"))


def test_branches_and_commits_use_the_commit_as_name(
    tmp_path: Path, mirror: Path, source: Path
) -> None:
    commit = git(source, "rev-parse", "HEAD")
    by_branch = prepare(tmp_path, "platform", "main")
    assert by_branch["name"] == commit
    assert by_branch["provenance"]["tag"] == "platform-v0.1.0"
    assert by_branch["provenance"]["requested_ref"] == "main"
    by_commit = prepare(tmp_path, "platform", commit)
    assert by_commit == {**by_branch, "changed": False}


def test_prepare_is_idempotent(tmp_path: Path, mirror: Path) -> None:
    first = prepare(tmp_path, "platform", "platform-v0.1.0")
    second = prepare(tmp_path, "platform", "platform-v0.1.0")
    assert second["changed"] is False
    assert second["provenance"] == first["provenance"]


def test_a_moved_tag_is_refused(tmp_path: Path, mirror: Path, source: Path) -> None:
    prepare(tmp_path, "platform", "platform-v0.1.0")
    (source / "platform" / "new.txt").write_text("x")
    git(source, "add", ".")
    git(source, "commit", "--quiet", "-m", "two")
    git(source, "tag", "-f", "-a", "platform-v0.1.0", "-m", "moved")
    ds.fetch(ds.run_git, mirror, str(source), {})
    with pytest.raises(ds.DeployError, match="moved tag is never redeployed"):
        prepare(tmp_path, "platform", "platform-v0.1.0")


@pytest.mark.parametrize(
    ("ref", "message"),
    [
        ("nope", "neither a tag, a branch nor a full commit"),
        ("../etc", "not a valid ref"),
        ("-x", "not a valid ref"),
        ("0" * 40, "neither a tag"),
    ],
)
def test_bad_refs(tmp_path: Path, mirror: Path, ref: str, message: str) -> None:
    with pytest.raises(ds.DeployError, match=message):
        prepare(tmp_path, "platform", ref)


def test_missing_component_directory(tmp_path: Path, mirror: Path, source: Path) -> None:
    git(source, "checkout", "--quiet", "--orphan", "empty")
    git(source, "rm", "--quiet", "-r", "--cached", ".")
    (source / "other.txt").write_text("x")
    git(source, "add", "other.txt")
    git(source, "commit", "--quiet", "-m", "empty")
    ds.fetch(ds.run_git, mirror, str(source), {})
    with pytest.raises(ds.DeployError, match="has no platform/ directory"):
        prepare(tmp_path, "platform", "empty")


def test_links_leaving_the_tree_are_refused(tmp_path: Path, mirror: Path, source: Path) -> None:
    (source / "platform" / "escape").symlink_to("/etc/passwd")
    git(source, "add", ".")
    git(source, "commit", "--quiet", "-m", "link")
    git(source, "tag", "-a", "platform-v0.3.0", "-m", "bad")
    ds.fetch(ds.run_git, mirror, str(source), {})
    with pytest.raises(Exception, match=r"absolute|outside"):
        prepare(tmp_path, "platform", "platform-v0.3.0")
    assert not (tmp_path / "deploy" / "platform" / "platform-v0.3.0").exists()
    assert not list((tmp_path / "deploy" / "platform").glob(".platform-v0.3.0.*"))


def test_required_signature_rules(tmp_path: Path, mirror: Path) -> None:
    with pytest.raises(ds.DeployError, match="must be a platform-v tag"):
        prepare(tmp_path, "platform", "main", require_signed=True)
    with pytest.raises(ds.DeployError, match="no allowed signers"):
        prepare(tmp_path, "platform", "platform-v0.1.0", require_signed=True)
    signers = tmp_path / "allowed_signers"
    signers.write_text("releases@example.com ssh-ed25519 AAAAC3Nza\n")
    with pytest.raises(ds.DeployError, match="no valid signature"):
        prepare(
            tmp_path,
            "platform",
            "platform-v0.1.0",
            require_signed=True,
            allowed_signers=signers,
        )


def test_verify_tag_reads_the_signer() -> None:
    def fake(argv: Sequence[str], env: Any) -> ds.Done:
        assert "gpg.format=ssh" in argv
        assert argv[-1] == "refs/tags/platform-v0.1.0"
        return ds.Done(
            0,
            "",
            'Good "git" signature for releases@example.com with ED25519 key SHA256:abc\n',
        )

    signers = Path(__file__)  # any non-empty file
    assert ds.verify_tag(fake, Path("/m"), "platform-v0.1.0", signers) == "releases@example.com"


@pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="needs ssh-keygen (OpenSSH)")
def test_ssh_signed_tags_end_to_end(tmp_path: Path, source: Path) -> None:
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
        "platform-v0.4.0",
        "-m",
        "signed",
    )
    ds.fetch(ds.run_git, tmp_path / "mirror.git", str(source), {})
    result = prepare(
        tmp_path, "platform", "platform-v0.4.0", require_signed=True, allowed_signers=signers
    )
    assert result["provenance"]["signed_tag"] == {
        "required": True,
        "verified": True,
        "signer": "releases@example.com",
    }
    with pytest.raises(ds.DeployError, match="no valid signature"):
        prepare(
            tmp_path, "platform", "platform-v0.1.0", require_signed=True, allowed_signers=signers
        )


def test_activate(tmp_path: Path, mirror: Path) -> None:
    root = tmp_path / "deploy" / "platform"
    prepare(tmp_path, "platform", "platform-v0.1.0")
    assert ds.activate(root, "platform-v0.1.0") == {
        "changed": True,
        "current": "platform-v0.1.0",
    }
    assert str((root / "current").readlink()) == "platform-v0.1.0"
    assert ds.activate(root, "platform-v0.1.0")["changed"] is False
    for bad in ("nope", "../x", ""):
        with pytest.raises(ds.DeployError, match="not a prepared deployment"):
            ds.activate(root, bad)


def test_main_prints_json(tmp_path: Path, source: Path, capsys: pytest.CaptureFixture[str]) -> None:
    mirror = tmp_path / "m.git"
    assert ds.main(["fetch", "--mirror", str(mirror), "--repo", str(source)]) == 0
    assert json.loads(capsys.readouterr().out) == {"changed": True}
    root = tmp_path / "out"
    args = ["prepare", "--mirror", str(mirror), "--component", "catalog", "--ref", "nope"]
    args += ["--root", str(root), "--repo", "x", "--owner", ME, "--group", MY_GROUP]
    assert ds.main(args) == 1
    assert "neither a tag" in json.loads(capsys.readouterr().out)["error"]
    args[args.index("nope")] = "catalog-v0.1.0"
    args += ["--dir-mode", "0750", "--file-mode", "0640"]
    assert ds.main(args) == 0
    assert json.loads(capsys.readouterr().out)["name"] == "catalog-v0.1.0"
    assert ds.main(["activate", "--root", str(root), "--name", "catalog-v0.1.0"]) == 0


# --- role expressions ----------------------------------------------------------

ROLE_VARS = ROLES / "deploy_source" / "vars" / "main.yml"


def role_values(repo: str, **extra: Any) -> dict[str, Any]:
    variables = ansible_vars(
        GROUP_VARS, GROUP_VARS.parent / "versions.yml", ROLE_VARS, pokus_source_repo=repo, **extra
    )
    names = ("deploy_source_transport", "deploy_source_ssh_host", "deploy_source_known_host_lines")
    return {name: evaluate(name, variables) for name in names}


@pytest.mark.parametrize(
    ("repo", "transport", "host"),
    [
        ("git@github.com:example-org/pokus.git", "ssh", "github.com"),
        ("ssh://git@git.example.com/example-org/pokus.git", "ssh", "git.example.com"),
        ("ssh://git@git.example.com:2222/example-org/pokus.git", "ssh", "[git.example.com]:2222"),
        ("https://git.example.com/example-org/pokus.git", "https", None),
        ("https://user:token@git.example.com/pokus.git", "invalid", None),
        ("file:///srv/pokus.git", "invalid", None),
        ("http://git.example.com/pokus.git", "invalid", None),
    ],
)
def test_transport_and_host(repo: str, transport: str, host: str | None) -> None:
    values = role_values(repo)
    assert values["deploy_source_transport"] == transport
    if host:
        assert values["deploy_source_ssh_host"] == host


def test_known_hosts_pin_only_verified_github_keys() -> None:
    unverified = role_values("git@github.com:example-org/pokus.git")
    lock = load_yaml(GROUP_VARS.parent / "versions.yml")["pokus_versions"]["github_ssh_host_keys"]
    verified = [k for k, v in lock["keys"].items() if "TODO" not in v["public_key"]]
    assert len(unverified["deploy_source_known_host_lines"]) == len(verified)
    extra = ["[git.example.com]:2222 ssh-ed25519 AAAAC3Nza"]
    other = role_values(
        "ssh://git@git.example.com:2222/example-org/pokus.git", pokus_source_known_hosts=extra
    )
    assert other["deploy_source_known_host_lines"] == extra
