#!/opt/pinka/ansible-venv/bin/python3 -I
# Managed by pinka (ansible/roles/platform/files/pinka_socket_acl.py). Local changes will be overwritten.
"""Give the human classes their ACL entries on a platform socket (§12.3).

Installed as ``/usr/local/libexec/pinka/socket-acl`` and run as root from the
``ExecStartPost=`` of a platform socket unit, after systemd has created the
socket with its owner (the serving account), group (the socket's client
group) and mode 0660. It adds ``group:<group>:rw-`` for each human class that
may use the socket (``pinka`` for researchers, ``pinka-admin`` for admins);
``setfacl`` recalculates the mask, so the group bits ``ls`` shows become the
mask.

Without ACL support on ``/run`` the entries cannot be set; then, as the
contract says, the platform's peer-credential check alone separates the
classes: the socket becomes 0666 and a warning goes to the journal. Any other
failure fails the socket unit, so the service does not start.
"""

from __future__ import annotations

import argparse
import re
import stat
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

PATH_RE = re.compile(
    r"^/run/pinka/platform/[a-z][a-z0-9]{0,9}/[a-z]([a-z0-9-]{0,14}[a-z0-9])?\.sock$"
)
HUMAN_GROUPS = ("pinka", "pinka-admin")
FALLBACK_MODE = 0o666
NOT_SUPPORTED = "not supported"
Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


class SocketError(Exception):
    """The socket is missing or its ACL cannot be set."""


def _run(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(argv), check=False, capture_output=True, text=True)  # noqa: S603


def apply(path: Path, groups: Sequence[str], run: Runner = _run) -> str:
    """Add the ACL entries of the human groups.

    Args:
        path: The socket.
        groups: Human-class groups.
        run: Runs setfacl.

    Returns:
        ``acl`` when the entries were set, ``fallback`` when the file system
        has no ACL support and the socket became 0666.

    Raises:
        SocketError: The path is no socket, or setfacl failed otherwise.
    """
    mode = path.lstat().st_mode
    if not stat.S_ISSOCK(mode):
        raise SocketError(f"{path} is not a socket")
    entries = ",".join(f"group:{g}:rw-" for g in groups)
    result = run(["setfacl", "--modify", entries, str(path)])
    if result.returncode == 0:
        return "acl"
    if NOT_SUPPORTED in result.stderr.lower():
        path.chmod(FALLBACK_MODE)
        return "fallback"
    raise SocketError(f"setfacl failed on {path}: {result.stderr.strip()}")


def main(argv: Sequence[str], run: Runner = _run) -> int:
    """Set the ACL entries of a platform socket.

    Args:
        argv: The socket path, then the human-class groups.
        run: Runs setfacl.

    Returns:
        0 on success, 1 on failure, 2 on usage errors.
    """
    parser = argparse.ArgumentParser(prog="socket-acl", description=__doc__.splitlines()[0])
    parser.add_argument("path", type=Path)
    parser.add_argument("groups", nargs="+", choices=HUMAN_GROUPS)
    args = parser.parse_args(list(argv))
    if not PATH_RE.match(str(args.path)):
        parser.error(f"not a platform socket path: {args.path}")
    try:
        outcome = apply(args.path, args.groups, run)
    except (SocketError, OSError) as exc:
        sys.stderr.write(f"socket-acl: {exc}\n")
        return 1
    if outcome == "fallback":
        sys.stderr.write(
            f"socket-acl: warning: no ACL support for {args.path}; the socket is 0666 and the"
            " platform's peer-credential check alone decides who may use it\n"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
