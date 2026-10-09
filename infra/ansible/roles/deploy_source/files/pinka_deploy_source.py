#!/opt/pinka/ansible-venv/bin/python3 -I
# Managed by pinka (ansible/roles/deploy_source/files/pinka_deploy_source.py). Local changes will be overwritten.
"""Deploy one subtree of the pokus-platform repository from a root-only bare mirror.

Installed as ``/usr/local/libexec/pinka/deploy-source`` by the deploy_source
role and run as root by the catalog and platform roles (contracts.md,
"Deployment of platform and catalog"; resolutions B2, B4, E3, owner answer
E16):

* ``fetch``: create the bare mirror if needed and fetch every branch and tag
  of ``pinka_source_repo`` (SSH with the host's deploy key and pinned host
  keys, or anonymous HTTPS).
* ``prepare``: resolve a ref to a commit, check its signed tag when required,
  and extract ``git archive <commit>:<component>`` into ``<root>/<name>/``
  (``<name>`` is the component tag, otherwise the full commit) with
  ``DEPLOYED_FROM.json``; an existing deployment of the same commit and tree
  is left alone.
* ``activate``: point ``<root>/current`` at a prepared deployment atomically.

Every command prints one JSON object with ``changed`` and its result. The
mirror never leaves ``/var/lib/pinka/git``; no deployed path ever contains a
``.git`` directory.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import grp
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

COMPONENTS = ("platform", "catalog")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
SIGNER_RE = re.compile(r'Good "git" signature for (\S+) with')
PROVENANCE = "DEPLOYED_FROM.json"
FETCH_REFSPECS = ("+refs/heads/*:refs/heads/*", "+refs/tags/*:refs/tags/*")
GIT = shutil.which("git") or "/usr/bin/git"


class DeployError(Exception):
    """The deployment cannot be done; the message says why."""


@dataclass(frozen=True)
class Done:
    """Outcome of one git command.

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
    """Run git without a shell.

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
        "HOME": "/var/lib/pinka/git",
        **env,
    }
    done = subprocess.run(  # noqa: S603 - argv list
        [GIT, *argv], capture_output=True, text=True, env=environment, check=False
    )
    return Done(done.returncode, done.stdout, done.stderr)


def ssh_env(ssh_key: str | None, known_hosts: str | None) -> dict[str, str]:
    """The environment that makes git use the deploy key and the pinned host keys.

    Args:
        ssh_key: Private key file, or None for HTTPS.
        known_hosts: Pinned host keys.

    Returns:
        ``GIT_SSH_COMMAND``, or nothing for HTTPS.
    """
    if not ssh_key:
        return {}
    if not known_hosts:
        raise DeployError("an SSH source needs pinned host keys (known_hosts)")
    return {
        "GIT_SSH_COMMAND": (
            f"ssh -i {ssh_key} -o IdentitiesOnly=yes -o BatchMode=yes "
            f"-o StrictHostKeyChecking=yes -o UserKnownHostsFile={known_hosts} "
            "-o GlobalKnownHostsFile=/dev/null"
        )
    }


def _git(git: Git, mirror: Path, *args: str, env: Mapping[str, str] | None = None) -> Done:
    return git(["--git-dir", str(mirror), *args], env or {})


def fetch(git: Git, mirror: Path, repo: str, env: Mapping[str, str]) -> dict[str, Any]:
    """Create the bare mirror if needed and fetch branches and tags.

    Args:
        git: Command runner.
        mirror: Bare repository.
        repo: Source URL.
        env: SSH environment.

    Returns:
        ``{"changed": bool}``.

    Raises:
        DeployError: If git fails.
    """
    created = False
    if not (mirror / "HEAD").is_file():
        mirror.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        done = git(["init", "--quiet", "--bare", str(mirror)], {})
        if done.returncode != 0:
            raise DeployError(f"git init failed: {done.stderr.strip()}")
        created = True
    current = _git(git, mirror, "config", "--get", "remote.origin.url").stdout.strip()
    if current != repo:
        verb = "set-url" if current else "add"
        done = _git(git, mirror, "remote", verb, "origin", repo)
        if done.returncode != 0:
            raise DeployError(f"cannot set the source URL: {done.stderr.strip()}")
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
        raise DeployError(f"fetching {repo} failed: {done.stderr.strip()}")
    after = _git(git, mirror, "for-each-ref", "--format=%(objectname) %(refname)").stdout
    return {"changed": created or before != after}


@dataclass(frozen=True)
class Resolved:
    """A ref resolved in the mirror.

    Attributes:
        commit: Full commit ID.
        tree: Tree of the component's subdirectory at that commit.
        tag: Component tag carried by the commit, or None.
        name: Deployment directory name.
    """

    commit: str
    tree: str
    tag: str | None
    name: str


def resolve(git: Git, mirror: Path, component: str, ref: str) -> Resolved:
    """Resolve a ref: a component tag, another tag, a branch or a full commit.

    Args:
        git: Command runner.
        mirror: Bare repository.
        component: ``platform`` or ``catalog``.
        ref: Requested ref.

    Returns:
        The resolution.

    Raises:
        DeployError: If the ref is invalid, unknown, or lacks the component.
    """
    if not REF_RE.match(ref) or ".." in ref:
        raise DeployError(f"not a valid ref: {ref!r}")
    commit = None
    for candidate in (f"refs/tags/{ref}", f"refs/heads/{ref}"):
        done = _git(git, mirror, "rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}")
        if done.returncode == 0:
            commit = done.stdout.strip()
            break
    if commit is None and COMMIT_RE.match(ref):
        done = _git(git, mirror, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        commit = done.stdout.strip() if done.returncode == 0 else None
    if commit is None:
        raise DeployError(f"{ref!r} is neither a tag, a branch nor a full commit of the source")
    tree_done = _git(git, mirror, "rev-parse", "--verify", "--quiet", f"{commit}:{component}")
    if tree_done.returncode != 0:
        raise DeployError(f"commit {commit} has no {component}/ directory")
    prefix = f"{component}-v"
    tags = _git(
        git, mirror, "tag", "--points-at", commit, "--sort=-version:refname", "--list", f"{prefix}*"
    ).stdout.split()
    tag = ref if ref in tags else (tags[0] if tags else None)
    name = ref if ref.startswith(prefix) and ref in tags else commit
    return Resolved(commit, tree_done.stdout.strip(), tag, name)


def verify_tag(git: Git, mirror: Path, tag: str, allowed_signers: Path) -> str:
    """Check an SSH-signed tag against the allowed signers (owner answer E16).

    Args:
        git: Command runner.
        mirror: Bare repository.
        tag: Tag name.
        allowed_signers: ``ssh-keygen`` allowed signers file.

    Returns:
        The signer's principal.

    Raises:
        DeployError: If the signature is missing or not from an allowed signer.
    """
    if not allowed_signers.is_file() or not allowed_signers.read_text(encoding="utf-8").strip():
        raise DeployError("require_signed_tags is set but no allowed signers are configured")
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
        raise DeployError(
            f"tag {tag} has no valid signature of an allowed signer"
            + (f": {detail[-1]}" if detail else "")
        )
    return match.group(1)


def _ids(owner: str, group: str) -> tuple[int, int]:
    return pwd.getpwnam(owner).pw_uid, grp.getgrnam(group).gr_gid


def _apply_modes(top: Path, uid: int, gid: int, dir_mode: int, file_mode: int) -> None:
    exec_bits = dir_mode & 0o111
    for root, dirs, files in os.walk(top):
        for name in dirs:
            path = Path(root, name)
            os.lchown(path, uid, gid)
            path.chmod(dir_mode)
        for name in files:
            path = Path(root, name)
            os.lchown(path, uid, gid)
            if path.is_symlink():
                continue
            executable = path.stat().st_mode & 0o100
            path.chmod(file_mode | (exec_bits if executable else 0))
    os.lchown(top, uid, gid)
    top.chmod(dir_mode)


def _extract(git: Git, mirror: Path, commit: str, component: str, target: Path) -> None:
    proc = subprocess.Popen(  # noqa: S603 - argv list
        [GIT, "--git-dir", str(mirror), "archive", "--format=tar", f"{commit}:{component}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C"},
    )
    assert proc.stdout is not None  # noqa: S101 - Popen with PIPE
    with tarfile.open(fileobj=proc.stdout, mode="r|") as archive:
        # The "data" filter refuses absolute paths, links leaving the tree and devices.
        archive.extractall(target, filter="data")
    stderr = proc.stderr.read().decode() if proc.stderr else ""
    if proc.wait() != 0:
        raise DeployError(f"git archive failed: {stderr.strip()}")
    if any(p.name == ".git" for p in target.rglob(".git")):
        raise DeployError(f"{component}/ contains a .git entry; refusing to deploy it")


def prepare(
    git: Git,
    mirror: Path,
    *,
    component: str,
    ref: str,
    root: Path,
    repo: str,
    owner: str,
    group: str,
    dir_mode: int,
    file_mode: int,
    allowed_signers: Path | None,
    require_signed: bool,
    now: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
) -> dict[str, Any]:
    """Extract a component at a ref into its deployment directory.

    Args:
        git: Command runner.
        mirror: Bare repository.
        component: ``platform`` or ``catalog``.
        ref: Requested ref.
        root: Deployment root (``/srv/pinka/deploy/platform``, ``/srv/pinka/catalog``).
        repo: Source URL, recorded in the provenance.
        owner: Owner of the deployed tree.
        group: Group of the deployed tree.
        dir_mode: Mode of directories (and of executable files).
        file_mode: Mode of other files.
        allowed_signers: Allowed signers file for SSH-signed tags.
        require_signed: Refuse anything but a validly signed component tag.
        now: Clock.

    Returns:
        ``{"changed", "name", "path", "provenance"}``.

    Raises:
        DeployError: On an unknown component or ref, a missing signature, or a
            deployment directory that holds something else.
    """
    if component not in COMPONENTS:
        raise DeployError(f"unknown component {component!r}")
    resolved = resolve(git, mirror, component, ref)
    signed: dict[str, Any] = {"required": require_signed, "verified": False, "signer": None}
    if require_signed:
        if resolved.tag is None or resolved.tag != ref:
            raise DeployError(
                f"require_signed_tags: {ref!r} must be a {component}-v tag, not a branch or commit"
            )
        signed.update(verified=True, signer=verify_tag(git, mirror, ref, allowed_signers or Path()))
    elif resolved.tag and allowed_signers and allowed_signers.is_file():
        with contextlib.suppress(DeployError):
            signed.update(
                verified=True, signer=verify_tag(git, mirror, resolved.tag, allowed_signers)
            )
    target = root / resolved.name
    if target.exists():
        existing = _read_provenance(target)
        if existing.get("commit") != resolved.commit or existing.get("tree") != resolved.tree:
            raise DeployError(
                f"{target} exists but holds commit {existing.get('commit')}, not "
                f"{resolved.commit}; a moved tag is never redeployed in place"
            )
        return {
            "changed": False,
            "name": resolved.name,
            "path": str(target),
            "provenance": existing,
        }
    provenance = {
        "schema_version": 1,
        "component": component,
        "source_repo": repo,
        "requested_ref": ref,
        "tag": resolved.tag,
        "commit": resolved.commit,
        "subdirectory": component,
        "tree": resolved.tree,
        "signed_tag": signed,
        "deployed_at": now().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{resolved.name}.", dir=root))
    try:
        _extract(git, mirror, resolved.commit, component, staging)
        uid, gid = _ids(owner, group)
        _apply_modes(staging, uid, gid, dir_mode, file_mode)
        record = staging / PROVENANCE
        record.write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
        # Owned like the deployment: root in both roles (contracts.md: 0644 root).
        os.chown(record, uid, gid)
        record.chmod(0o644)
        staging.rename(target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {"changed": True, "name": resolved.name, "path": str(target), "provenance": provenance}


def _read_provenance(directory: Path) -> dict[str, Any]:
    try:
        data = json.loads((directory / PROVENANCE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def activate(root: Path, name: str) -> dict[str, Any]:
    """Point ``<root>/current`` at a prepared deployment, atomically.

    Args:
        root: Deployment root.
        name: Deployment directory name.

    Returns:
        ``{"changed": bool, "current": name}``.

    Raises:
        DeployError: If the deployment does not exist or has no provenance.
    """
    if "/" in name or name in ("", ".", "..") or not (root / name / PROVENANCE).is_file():
        raise DeployError(f"{root / name} is not a prepared deployment")
    link = root / "current"
    if link.is_symlink() and str(link.readlink()) == name:
        return {"changed": False, "current": name}
    staging = root / ".current.tmp"
    if staging.is_symlink() or staging.exists():
        staging.unlink()
    staging.symlink_to(name)
    staging.replace(link)
    return {"changed": True, "current": name}


def main(argv: Sequence[str], git: Git = run_git) -> int:
    """Run a command and print its JSON result.

    Args:
        argv: Arguments without the program name.
        git: Command runner.

    Returns:
        0 on success, 1 on a refused or failed deployment, 2 on usage errors.
    """
    parser = argparse.ArgumentParser(prog="deploy-source", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    fetch_cmd = sub.add_parser("fetch", help="create or update the bare mirror")
    fetch_cmd.add_argument("--mirror", type=Path, required=True)
    fetch_cmd.add_argument("--repo", required=True)
    fetch_cmd.add_argument("--ssh-key")
    fetch_cmd.add_argument("--known-hosts")
    prep = sub.add_parser("prepare", help="extract a component at a ref")
    prep.add_argument("--mirror", type=Path, required=True)
    prep.add_argument("--component", choices=COMPONENTS, required=True)
    prep.add_argument("--ref", required=True)
    prep.add_argument("--root", type=Path, required=True)
    prep.add_argument("--repo", required=True)
    prep.add_argument("--owner", default="root")
    prep.add_argument("--group", default="root")
    prep.add_argument("--dir-mode", type=lambda v: int(v, 8), default=0o755)
    prep.add_argument("--file-mode", type=lambda v: int(v, 8), default=0o644)
    prep.add_argument("--allowed-signers", type=Path)
    prep.add_argument("--require-signed", action="store_true")
    act = sub.add_parser("activate", help="point current at a prepared deployment")
    act.add_argument("--root", type=Path, required=True)
    act.add_argument("--name", required=True)
    args = parser.parse_args(list(argv))
    try:
        if args.command == "fetch":
            result = fetch(git, args.mirror, args.repo, ssh_env(args.ssh_key, args.known_hosts))
        elif args.command == "prepare":
            result = prepare(
                git,
                args.mirror,
                component=args.component,
                ref=args.ref,
                root=args.root,
                repo=args.repo,
                owner=args.owner,
                group=args.group,
                dir_mode=args.dir_mode,
                file_mode=args.file_mode,
                allowed_signers=args.allowed_signers,
                require_signed=args.require_signed,
            )
        else:
            result = activate(args.root, args.name)
    except (DeployError, OSError, KeyError, tarfile.TarError) as exc:
        print(json.dumps({"changed": False, "error": str(exc)}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
