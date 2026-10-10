"""Shared helpers of the deployment tests: source repositories and a host below tmp_path."""

from __future__ import annotations

import datetime as dt
import grp
import json
import os
import pwd
import socket
import stat
import subprocess
from pathlib import Path
from typing import Any

from platform_testlib import manifest, write_release

from pinka_platform import deploy, names

ME = pwd.getpwuid(os.getuid()).pw_name
MY_GROUP = grp.getgrgid(os.getgid()).gr_name
NOW = dt.datetime(2026, 1, 1, 12, 0, 0, tzinfo=dt.UTC)
OWNERS = {
    "user": ME,
    "group": MY_GROUP,
    "restricted_group": MY_GROUP,
    "build_user": ME,
    "build_group": MY_GROUP,
}
CONTRACT_RECORDS = Path(deploy.__file__).resolve().parents[3] / "contract" / "records"


def git(cwd: Path, *args: str) -> str:
    """Run git in a working tree with a fixed identity.

    Args:
        cwd: Working directory.
        *args: Arguments.

    Returns:
        Standard output, stripped.
    """
    done = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def add_release(repo: Path, data: dict[str, Any], subdir: str = "platform") -> Path:
    """Write a release (every file its manifest names) into a working tree.

    Args:
        repo: Working tree.
        data: The manifest.
        subdir: Directory of the release in the tree.

    Returns:
        The release directory.
    """
    staged = write_release(repo / ".tmp", data)
    target = repo / subdir
    if target.exists():
        subprocess.run(["rm", "-rf", str(target)], check=True)
    staged.rename(target)
    (repo / ".tmp").rmdir()
    # git keeps no empty directories.
    for directory in [target, *target.rglob("*")]:
        if directory.is_dir() and not any(directory.iterdir()):
            keep = "board.json" if directory.name == "dashboards" else ".keep"
            (directory / keep).write_text("{}\n" if keep.endswith(".json") else "")
    return target


def make_repo(root: Path) -> Path:
    """A repository with the allprof release, its restricted package and a guide.

    Tags: ``allprof-v1.2.3`` and ``cases-v1``; branch ``main``.

    Args:
        root: Directory for the repository.

    Returns:
        The working tree (also the repository).
    """
    repo = root / "src"
    repo.mkdir()
    add_release(repo, manifest("allprof"))
    (repo / "cases" / "scenarios").mkdir(parents=True)
    (repo / "cases" / "scenarios" / "s1.yml").write_text("id: case-1\nanswer: secret\n")
    (repo / "cases" / "run.sh").write_text("#!/bin/sh\n")
    (repo / "cases" / "run.sh").chmod(0o755)
    (repo / "guides").mkdir()
    (repo / "guides" / "intro.md").write_text("# guide\n")
    git(repo, "init", "--quiet", "--initial-branch=main")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "one")
    git(repo, "tag", "-a", "allprof-v1.2.3", "-m", "allprof 1.2.3")
    git(repo, "tag", "-a", "cases-v1", "-m", "cases 1")
    return repo


def settings(repo: Path, **changes: Any) -> deploy.Settings:
    """Settings that deploy allprof with both packages from one repository.

    Args:
        repo: The source repository.
        **changes: Settings fields to replace.

    Returns:
        The settings.
    """
    url = f"file://{repo}"
    data: dict[str, Any] = {
        "platform": {
            "repo": url,
            "ref": "allprof-v1.2.3",
            "path": "platform",
            "tag_prefix": "allprof-v",
        },
        "content": {
            "cases": {"repo": url, "ref": "cases-v1", "path": "cases", "tag_prefix": "cases-v"},
            "guides": {"repo": url, "ref": "main", "path": "guides"},
        },
        "owners": OWNERS,
    }
    data.update(changes)
    return deploy.Settings.from_json(data)


def prepare(paths: deploy.Paths, s: deploy.Settings, **kwargs: Any) -> dict[str, Any]:
    """Run prepare with an empty host and a fixed clock.

    Args:
        paths: Host paths.
        s: Settings.
        **kwargs: Further arguments of :func:`deploy.prepare`.

    Returns:
        The plan.
    """
    from platform_testlib import CONTRACT  # noqa: PLC0415 - loaded once by the test library

    return deploy.prepare(
        s,
        paths,
        CONTRACT,
        host=lambda _: names.HostFacts(),
        owner_uid=os.getuid(),
        now=lambda: NOW,
        **kwargs,
    )


def schema(name: str) -> dict[str, Any]:
    """A record schema of ``contract/records``.

    Args:
        name: File name without ``.schema.json``.

    Returns:
        The schema.
    """
    data: dict[str, Any] = json.loads(
        (CONTRACT_RECORDS / f"{name}.schema.json").read_text(encoding="utf-8")
    )
    return data


# The kinds of entry a release and its build output must never contain, with
# the problem code each one raises (owner decision on 3d.2).
SPECIAL_CASES = {
    "chardev": "special-file",
    "blockdev": "special-file",
    "fifo": "special-file",
    "socket": "special-file",
    "setuid": "setid-bit",
    "setgid": "setid-bit",
}
FAKED_DEVICES = {"chardev": stat.S_IFCHR, "blockdev": stat.S_IFBLK}


def make_special(path: Path, kind: str) -> None:
    """Create an entry of one of SPECIAL_CASES.

    Device files need root, so a regular file stands in for them and
    ``fake_devices`` makes ``lstat`` report it as a device.

    Args:
        path: Where to create it.
        kind: A key of SPECIAL_CASES.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if kind == "fifo":
        os.mkfifo(path)
    elif kind == "socket":
        cwd = Path.cwd()
        os.chdir(path.parent)  # a socket path is limited to 108 bytes
        try:
            with socket.socket(socket.AF_UNIX) as sock:
                sock.bind(path.name)
        finally:
            os.chdir(cwd)
    else:
        path.write_text("#!/bin/sh\n")
        path.chmod({"setuid": 0o4755, "setgid": 0o2755}.get(kind, 0o644))


def fake_devices(kind: str) -> Any:
    """An ``lstat`` that reports entries named ``planted`` as the device ``kind``.

    Args:
        kind: A key of SPECIAL_CASES.

    Returns:
        The replacement.
    """
    real = Path.lstat

    def lstat(path: Path) -> os.stat_result:
        info = real(path)
        if path.name == "planted" and kind in FAKED_DEVICES:
            fields = list(info)
            fields[0] = FAKED_DEVICES[kind] | 0o644
            return os.stat_result(fields)
        return info

    return lstat
