#!/opt/pinka/ansible-venv/bin/python3 -I
# Managed by pinka (ansible/roles/platform/files/pinka_socket_perms.py). Local changes will be overwritten.
"""Give a platform socket the owner, group, mode and ACL of the contract (C8).

Installed as ``/usr/local/libexec/pinka/socket-perms`` and run as root from
each platform unit's ``ExecStartPost=+``: it waits until the service has
created its socket (the unit's ``UMask=0077`` keeps it reachable by the
service's own account only until then), sets owner, group and mode, then adds
the named ACL entries with ``setfacl`` (which recalculates the mask, so the
group bits ``ls`` shows become the mask). A missing socket after the timeout,
or a failing ``setfacl``, fails the unit.
"""

from __future__ import annotations

import argparse
import grp
import os
import pwd
import re
import stat
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path

ACL_RE = re.compile(r"^(user|group):[a-z_][a-z0-9_-]{0,31}:[r-][w-][x-]$")
PATH_RE = re.compile(r"^/run/pinka/[a-z]+/[a-z]+\.sock$")


class SocketError(Exception):
    """The socket is missing or cannot be given its permissions."""


def wait_for_socket(
    path: Path,
    timeout: float,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Wait until a Unix socket exists at a path.

    Args:
        path: Socket path.
        timeout: Seconds to wait.
        sleep: Sleep function.
        clock: Monotonic clock.

    Raises:
        SocketError: If no socket appears in time, or something else is there.
    """
    deadline = clock() + timeout
    while True:
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            mode = None
        if mode is not None:
            if not stat.S_ISSOCK(mode):
                raise SocketError(f"{path} exists but is not a socket")
            return
        if clock() >= deadline:
            raise SocketError(f"{path} did not appear within {timeout:g} s")
        sleep(0.2)


def apply(  # noqa: PLR0917 - the permissions are positional by nature
    path: Path,
    owner: str,
    group: str,
    mode: int,
    acl: Sequence[str],
    run: Callable[[Sequence[str]], int],
) -> None:
    """Set owner, group, mode and ACL entries.

    Args:
        path: Socket path.
        owner: Owner account.
        group: Group.
        mode: Permission bits before the ACL entries.
        acl: Entries such as ``user:potka-orch:rw-``.
        run: Runs setfacl and returns its exit status.

    Raises:
        SocketError: If setfacl fails.
    """
    os.chown(path, pwd.getpwnam(owner).pw_uid, grp.getgrnam(group).gr_gid, follow_symlinks=False)
    path.chmod(mode)
    if acl and run(["setfacl", "--modify", ",".join(acl), str(path)]) != 0:
        raise SocketError(f"setfacl failed on {path}")


def _setfacl(argv: Sequence[str]) -> int:
    return subprocess.run(list(argv), check=False).returncode  # noqa: S603 - fixed argv


def main(argv: Sequence[str], run: Callable[[Sequence[str]], int] = _setfacl) -> int:
    """Wait for a socket and give it its permissions.

    Args:
        argv: Arguments without the program name.
        run: setfacl runner.

    Returns:
        0 on success, 1 on failure, 2 on usage errors.
    """
    parser = argparse.ArgumentParser(prog="socket-perms", description=__doc__.splitlines()[0])
    parser.add_argument("--path", type=Path, required=True)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--group", required=True)
    parser.add_argument("--mode", type=lambda v: int(v, 8), required=True)
    parser.add_argument("--acl", action="append", default=[])
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args(list(argv))
    if not PATH_RE.match(str(args.path)):
        parser.error(f"not a platform socket path: {args.path}")
    bad = [entry for entry in args.acl if not ACL_RE.match(entry)]
    if bad or args.mode & ~0o777:
        parser.error(f"invalid ACL entries or mode: {bad or oct(args.mode)}")
    try:
        wait_for_socket(args.path, args.timeout)
        apply(args.path, args.owner, args.group, args.mode, args.acl, run)
    except (SocketError, KeyError, OSError) as exc:
        sys.stderr.write(f"socket-perms: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
