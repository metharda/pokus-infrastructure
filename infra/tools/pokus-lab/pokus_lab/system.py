"""Access to the host for the pokus-lab commands.

Commands never run a program, read a file or open a socket themselves; they
ask a :class:`System`. :class:`RealSystem` talks to the host; the tests use a
fake. Python 3.9 compatible, standard library only.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import functools
import grp
import http.client
import http.server
import json
import os
import pwd
import socket
import stat
import subprocess
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

EXIT_NOT_FOUND = 127
EXIT_TIMEOUT = 124


@dataclass(frozen=True)
class Result:
    """Outcome of one command.

    Attributes:
        argv: The command line.
        returncode: Exit code; 127 if the program is missing, 124 on timeout.
        stdout: Captured standard output.
        stderr: Captured standard error.
    """

    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        """True if the command exited with status 0."""
        return self.returncode == 0

    def json(self) -> Any:  # noqa: ANN401 - parsed JSON
        """Parse standard output as JSON.

        Returns:
            The document, or None if the output is not JSON.
        """
        try:
            return json.loads(self.stdout)
        except ValueError:
            return None


@dataclass(frozen=True)
class FileInfo:
    """Ownership and mode of a path.

    Attributes:
        owner: Owner name (or the numeric UID if unknown).
        group: Group name (or the numeric GID if unknown).
        mode: Permission bits including setuid/setgid/sticky (``0o2775``).
        is_dir: True for a directory.
        is_link: True for a symbolic link (not followed).
        size: Size in bytes.
    """

    owner: str
    group: str
    mode: int
    is_dir: bool
    is_link: bool
    size: int


@dataclass(frozen=True)
class HttpResponse:
    """An HTTP answer.

    Attributes:
        status: Status code, 0 if no answer arrived.
        body: Response body (decoded as UTF-8), or the error text.
    """

    status: int
    body: str

    def json(self) -> Any:  # noqa: ANN401 - parsed JSON
        """Parse the body as JSON.

        Returns:
            The document, or None if the body is not JSON.
        """
        try:
            return json.loads(self.body)
        except ValueError:
            return None


class System:
    """Interface of host access; every method has a fake in the tests."""

    def run(
        self,
        argv: Sequence[str],
        timeout: float = 60.0,
        stdin: str | None = None,
        env: dict[str, str] | None = None,
    ) -> Result:
        """Run a command without a shell."""
        raise NotImplementedError

    def interactive(self, argv: Sequence[str], env: dict[str, str] | None = None) -> int:
        """Run a command attached to the terminal and return its exit code."""
        raise NotImplementedError

    def read_text(self, path: str) -> str | None:
        """Read a file, or None if it cannot be read."""
        raise NotImplementedError

    def write_text(self, path: str, text: str, mode: int) -> None:
        """Write a file atomically with the given mode."""
        raise NotImplementedError

    def info(self, path: str) -> FileInfo | None:
        """Ownership and mode of a path (not following links), or None."""
        raise NotImplementedError

    def listdir(self, path: str) -> list[str]:
        """Names in a directory (empty if it cannot be read)."""
        raise NotImplementedError

    def walk_dirs(self, path: str, max_depth: int) -> list[str]:
        """Directories below a path, top-down; links to directories are listed, not followed."""
        raise NotImplementedError

    def http(
        self, method: str, url: str, body: bytes | None = None, timeout: float = 10.0
    ) -> HttpResponse:
        """An HTTP request to a loopback URL."""
        raise NotImplementedError

    def unix_http(
        self,
        socket_path: str,
        method: str,
        path: str,
        body: bytes | None = None,
        timeout: float = 10.0,
    ) -> HttpResponse:
        """An HTTP request over a Unix socket."""
        raise NotImplementedError

    def tcp_connect(self, host: str, port: int, timeout: float = 3.0) -> bool:
        """Whether a TCP connection succeeds."""
        raise NotImplementedError

    def euid(self) -> int:
        """Effective user ID."""
        raise NotImplementedError

    def now(self) -> dt.datetime:
        """Current time, UTC."""
        raise NotImplementedError

    def group_members(self, group: str) -> list[str] | None:
        """Members of a group (secondary members), or None if it does not exist."""
        raise NotImplementedError

    def serve_static(self, directory: str, address: str, port: int) -> None:
        """Serve a directory of static files read-only over HTTP until interrupted."""
        raise NotImplementedError

    def user_groups(self, user: str) -> list[str] | None:
        """Groups of a user including the primary group, or None if it does not exist."""
        raise NotImplementedError


class RealSystem(System):
    """The real host."""

    def run(  # noqa: D102
        self,
        argv: Sequence[str],
        timeout: float = 60.0,
        stdin: str | None = None,
        env: dict[str, str] | None = None,
    ) -> Result:
        environment = dict(os.environ, LC_ALL="C.UTF-8", LANG="C.UTF-8")
        environment.update(env or {})
        try:
            done = subprocess.run(  # noqa: S603 - argv lists, no shell
                list(argv),
                input=stdin,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=environment,
                check=False,
            )
        except FileNotFoundError as exc:
            return Result(tuple(argv), EXIT_NOT_FOUND, "", str(exc))
        except subprocess.TimeoutExpired:
            return Result(tuple(argv), EXIT_TIMEOUT, "", f"timed out after {timeout:g} s")
        except OSError as exc:
            return Result(tuple(argv), EXIT_NOT_FOUND, "", str(exc))
        return Result(tuple(argv), done.returncode, done.stdout, done.stderr)

    def interactive(self, argv: Sequence[str], env: dict[str, str] | None = None) -> int:  # noqa: D102
        environment = dict(os.environ)
        environment.update(env or {})
        try:
            return subprocess.call(list(argv), env=environment)  # noqa: S603 - argv list, no shell
        except OSError:
            return EXIT_NOT_FOUND

    def read_text(self, path: str) -> str | None:  # noqa: D102
        try:
            with open(path, encoding="utf-8") as handle:
                return handle.read()
        except (OSError, UnicodeDecodeError):
            return None

    def write_text(self, path: str, text: str, mode: int) -> None:  # noqa: D102
        directory = os.path.dirname(path)
        os.makedirs(directory, exist_ok=True)
        tmp = os.path.join(directory, f".{os.path.basename(path)}.tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)

    def info(self, path: str) -> FileInfo | None:  # noqa: D102
        try:
            st = os.lstat(path)
        except OSError:
            return None
        try:
            owner = pwd.getpwuid(st.st_uid).pw_name
        except KeyError:
            owner = str(st.st_uid)
        try:
            group = grp.getgrgid(st.st_gid).gr_name
        except KeyError:
            group = str(st.st_gid)
        return FileInfo(
            owner,
            group,
            stat.S_IMODE(st.st_mode),
            stat.S_ISDIR(st.st_mode),
            stat.S_ISLNK(st.st_mode),
            st.st_size,
        )

    def listdir(self, path: str) -> list[str]:  # noqa: D102
        try:
            return sorted(os.listdir(path))
        except OSError:
            return []

    def walk_dirs(self, path: str, max_depth: int) -> list[str]:  # noqa: D102
        found: list[str] = []
        base = path.rstrip("/").count("/")
        for root, dirs, _ in os.walk(path):
            depth = root.rstrip("/").count("/") - base
            if depth >= max_depth:
                dirs[:] = []
            found.extend(os.path.join(root, d) for d in dirs)
        return found

    def http(  # noqa: D102
        self, method: str, url: str, body: bytes | None = None, timeout: float = 10.0
    ) -> HttpResponse:
        request = urllib.request.Request(url, data=body, method=method)  # noqa: S310 - loopback URLs only
        if body is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                return HttpResponse(
                    int(response.status), response.read().decode("utf-8", "replace")
                )
        except urllib.error.HTTPError as exc:
            return HttpResponse(int(exc.code), exc.read().decode("utf-8", "replace"))
        except (OSError, ValueError) as exc:
            return HttpResponse(0, str(exc))

    def unix_http(  # noqa: D102
        self,
        socket_path: str,
        method: str,
        path: str,
        body: bytes | None = None,
        timeout: float = 10.0,
    ) -> HttpResponse:
        connection = _UnixHTTPConnection(socket_path, timeout)
        try:
            headers = {"Content-Type": "application/json"} if body is not None else {}
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            return HttpResponse(response.status, response.read().decode("utf-8", "replace"))
        except OSError as exc:
            return HttpResponse(0, str(exc))
        finally:
            connection.close()

    def tcp_connect(self, host: str, port: int, timeout: float = 3.0) -> bool:  # noqa: D102
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False

    def euid(self) -> int:  # noqa: D102
        return os.geteuid()

    def now(self) -> dt.datetime:  # noqa: D102
        return dt.datetime.now(dt.timezone.utc)

    def group_members(self, group: str) -> list[str] | None:  # noqa: D102
        try:
            return sorted(grp.getgrnam(group).gr_mem)
        except KeyError:
            return None

    def serve_static(self, directory: str, address: str, port: int) -> None:  # noqa: D102
        with (
            static_server(directory, address, port) as server,
            contextlib.suppress(KeyboardInterrupt),
        ):
            server.serve_forever()

    def user_groups(self, user: str) -> list[str] | None:  # noqa: D102
        try:
            entry = pwd.getpwnam(user)
        except KeyError:
            return None
        groups = {g.gr_name for g in grp.getgrall() if user in g.gr_mem}
        with contextlib.suppress(KeyError):
            groups.add(grp.getgrgid(entry.pw_gid).gr_name)
        return sorted(groups)


def static_server(directory: str, address: str, port: int) -> http.server.ThreadingHTTPServer:
    """An HTTP server that answers GET and HEAD with the files of a directory.

    Args:
        directory: Directory to serve.
        address: Address to listen on.
        port: Port to listen on (0 picks a free one).

    Returns:
        The server, already listening.
    """
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=directory)
    return http.server.ThreadingHTTPServer((address, port), handler)


class _UnixHTTPConnection(http.client.HTTPConnection):
    """HTTP over a Unix socket (the platform's sockets)."""

    def __init__(self, socket_path: str, timeout: float) -> None:
        super().__init__("localhost", timeout=timeout)
        self.socket_path = socket_path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(self.timeout)
            sock.connect(self.socket_path)
        except OSError:
            sock.close()
            raise
        self.sock = sock
