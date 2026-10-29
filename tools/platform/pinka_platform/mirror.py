"""Root-only bare mirrors, refs, signed tags and staging extraction.

One bare mirror per repository URL under ``/var/lib/pinka/git`` (0700). A
source's ref is resolved there, its signed tag checked when required, and
``git archive <commit>:<path>`` is extracted into the staging directory with
Python's ``data`` filter: no absolute paths, no links that leave the tree, no
devices, and no ``.git`` entry.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tarfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SIGNER_RE = re.compile(r'Good "git" signature for (\S+) with')
FETCH_REFSPECS = ("+refs/heads/*:refs/heads/*", "+refs/tags/*:refs/tags/*")
GIT = shutil.which("git") or "/usr/bin/git"


class SourceError(Exception):
    """A source cannot be deployed as requested; the message says why.

    Attributes:
        code: Problem code (a refusal of the release, exit code 65).
    """

    def __init__(self, code: str, message: str) -> None:
        """Create the error.

        Args:
            code: Problem code.
            message: Why.
        """
        super().__init__(message)
        self.code = code


class FetchError(Exception):
    """Fetching a repository failed (network, credentials, host keys)."""


@dataclass(frozen=True)
class Done:
    """Outcome of one command.

    Attributes:
        returncode: Exit status.
        stdout: Standard output.
        stderr: Standard error.
    """

    returncode: int
    stdout: str
    stderr: str


Git = Callable[[Sequence[str], Mapping[str, str]], Done]


def run_git(argv: Sequence[str], env: Mapping[str, str]) -> Done:
    """Run git without a shell, a terminal prompt or the system configuration.

    Args:
        argv: Arguments after ``git``.
        env: Extra environment variables.

    Returns:
        The outcome.
    """
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LC_ALL": "C",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "HOME": os.environ.get("PINKA_GIT_HOME", "/var/lib/pinka/git"),
        **env,
    }
    done = subprocess.run(  # noqa: S603 - argv list
        [GIT, *argv], capture_output=True, text=True, env=environment, check=False
    )
    return Done(done.returncode, done.stdout, done.stderr)


def ssh_env(ssh_key: str | None, known_hosts: str | None) -> dict[str, str]:
    """The environment that makes git use the host's deploy key and the pinned host keys.

    Args:
        ssh_key: Private key file, or None for HTTPS and file:// sources.
        known_hosts: Pinned host keys.

    Returns:
        ``GIT_SSH_COMMAND``, or nothing without a key.

    Raises:
        FetchError: A key without pinned host keys.
    """
    if not ssh_key:
        return {}
    if not known_hosts:
        raise FetchError("an SSH source needs pinned host keys (known_hosts)")
    return {
        "GIT_SSH_COMMAND": (
            f"ssh -i {ssh_key} -o IdentitiesOnly=yes -o BatchMode=yes "
            f"-o StrictHostKeyChecking=yes -o UserKnownHostsFile={known_hosts} "
            "-o GlobalKnownHostsFile=/dev/null"
        )
    }


def _git(git: Git, mirror: Path, *args: str, env: Mapping[str, str] | None = None) -> Done:
    return git(["--git-dir", str(mirror), *args], env or {})


def fetch(git: Git, mirror: Path, repo: str, env: Mapping[str, str]) -> bool:
    """Create the bare mirror if needed and fetch every branch and tag.

    Args:
        git: Command runner.
        mirror: Bare repository.
        repo: Source URL.
        env: SSH environment.

    Returns:
        Whether a ref changed (or the mirror is new).

    Raises:
        FetchError: If git fails.
    """
    created = False
    if not (mirror / "HEAD").is_file():
        mirror.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        done = git(["init", "--quiet", "--bare", str(mirror)], {})
        if done.returncode != 0:
            raise FetchError(f"git init failed: {done.stderr.strip()}")
        created = True
    current = _git(git, mirror, "config", "--get", "remote.origin.url").stdout.strip()
    if current != repo:
        verb = "set-url" if current else "add"
        done = _git(git, mirror, "remote", verb, "origin", repo)
        if done.returncode != 0:
            raise FetchError(f"cannot set the source URL: {done.stderr.strip()}")
    before = _git(git, mirror, "for-each-ref", "--format=%(objectname) %(refname)").stdout
    done = _git(
        git,
        mirror,
        "fetch",
        "--quiet",
        "--prune",
        "--no-write-fetch-head",
        "origin",
        *FETCH_REFSPECS,
        env=env,
    )
    if done.returncode != 0:
        raise FetchError(f"fetching {repo} failed: {done.stderr.strip()}")
    after = _git(git, mirror, "for-each-ref", "--format=%(objectname) %(refname)").stdout
    return created or before != after


@dataclass(frozen=True)
class Resolved:
    """A source's ref resolved in its mirror.

    Attributes:
        commit: Full commit ID.
        tree: Tree of the source's path at that commit.
        tag: The release tag (with the source's prefix) the commit carries, or None.
        name: Directory name of the deployment: the tag when the ref is that
            tag, otherwise the full commit.
    """

    commit: str
    tree: str
    tag: str | None
    name: str


def resolve(git: Git, mirror: Path, ref: str, path: str, tag_prefix: str) -> Resolved:
    """Resolve a ref: a tag, a branch or a full commit.

    Args:
        git: Command runner.
        mirror: Bare repository.
        ref: Requested ref (checked by the source settings).
        path: Subdirectory ("" for the root).
        tag_prefix: Prefix of the source's release tags.

    Returns:
        The resolution.

    Raises:
        SourceError: Unknown ref, or no such directory at that commit.
    """
    commit = None
    for candidate in (f"refs/tags/{ref}", f"refs/heads/{ref}"):
        done = _git(git, mirror, "rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}")
        if done.returncode == 0:
            commit = done.stdout.strip()
            break
    if commit is None and COMMIT_RE.match(ref):
        done = _git(git, mirror, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        commit = done.stdout.strip() if done.returncode == 0 else None
    if commit is None and COMMIT_RE.match(ref):
        raise SourceError("source-ref", f"the mirror does not have the commit {ref}")
    if commit is None:
        raise SourceError(
            "source-ref", f"{ref!r} is neither a tag, a branch nor a full commit of the source"
        )
    spec = f"{commit}:{path}" if path else f"{commit}^{{tree}}"
    tree = _git(git, mirror, "rev-parse", "--verify", "--quiet", spec)
    if tree.returncode != 0 or _git(git, mirror, "cat-file", "-t", spec).stdout.strip() != "tree":
        raise SourceError("source-path", f"commit {commit} has no directory {path!r}")
    tags = _git(
        git,
        mirror,
        "tag",
        "--points-at",
        commit,
        "--sort=-version:refname",
        "--list",
        f"{tag_prefix}*",
    ).stdout.split()
    tag = ref if ref in tags else (tags[0] if tags else None)
    name = ref if ref in tags else commit
    return Resolved(commit, tree.stdout.strip(), tag, name)


def verify_tag(git: Git, mirror: Path, tag: str, allowed_signers: Path) -> str:
    """Check an SSH-signed tag against the allowed signers.

    Args:
        git: Command runner.
        mirror: Bare repository.
        tag: Tag name.
        allowed_signers: ``ssh-keygen`` allowed signers file.

    Returns:
        The signer's principal.

    Raises:
        SourceError: No allowed signers, or no valid signature of one.
    """
    if not allowed_signers.is_file() or not allowed_signers.read_text(encoding="utf-8").strip():
        raise SourceError(
            "tag-signature", "require_signed_tags is set but no allowed signers are configured"
        )
    done = git(
        [
            "--git-dir",
            str(mirror),
            "-c",
            "gpg.format=ssh",
            "-c",
            f"gpg.ssh.allowedSignersFile={allowed_signers}",
            "verify-tag",
            f"refs/tags/{tag}",
        ],
        {},
    )
    match = SIGNER_RE.search(done.stdout + done.stderr)
    if done.returncode != 0 or match is None:
        detail = (done.stderr or done.stdout).strip().splitlines()
        raise SourceError(
            "tag-signature",
            f"tag {tag} has no valid signature of an allowed signer"
            + (f": {detail[-1]}" if detail else ""),
        )
    return match.group(1)


def _no_git_entries(member: tarfile.TarInfo, dest: str) -> tarfile.TarInfo | None:
    if ".git" in Path(member.name).parts:
        raise SourceError("source-git-entry", f"the tree contains a .git entry: {member.name}")
    return tarfile.data_filter(member, dest)


def extract(mirror: Path, commit: str, path: str, target: Path) -> None:
    """Extract ``git archive <commit>:<path>`` into an empty directory.

    The archive is streamed from git into Python's tar reader, so this does
    not go through the ``Git`` runner.

    Args:
        mirror: Bare repository.
        commit: Commit.
        path: Subdirectory ("" for the root).
        target: Existing empty directory.

    Raises:
        SourceError: A ``.git`` entry, or an entry the ``data`` filter refuses
            (absolute path, link leaving the tree, device).
        FetchError: git archive failed.
    """
    spec = f"{commit}:{path}" if path else commit
    proc = subprocess.Popen(  # noqa: S603 - argv list
        [GIT, "--git-dir", str(mirror), "archive", "--format=tar", spec],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C"},
    )
    assert proc.stdout is not None  # noqa: S101 - Popen with PIPE
    try:
        with tarfile.open(fileobj=proc.stdout, mode="r|") as archive:
            archive.extractall(target, filter=_no_git_entries)  # noqa: S202 - data filter
    except tarfile.FilterError as exc:
        _stop(proc)
        raise SourceError("source-unsafe-entry", str(exc)) from exc
    except SourceError:
        _stop(proc)
        raise
    except tarfile.TarError as exc:
        # git archive refuses some trees itself (a .git entry is an "invalid
        # path") and then writes no archive at all.
        stderr = _stop(proc)
        if "invalid path" in stderr:
            raise SourceError("source-git-entry", stderr) from exc
        raise FetchError(f"git archive failed: {stderr or exc}") from exc
    stderr = proc.stderr.read().decode() if proc.stderr else ""
    if proc.wait() != 0:
        raise FetchError(f"git archive failed: {stderr.strip()}")


def _stop(proc: subprocess.Popen[bytes]) -> str:
    proc.kill()
    _, stderr = proc.communicate()
    return stderr.decode(errors="replace").strip() if stderr else ""
