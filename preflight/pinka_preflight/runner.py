r"""Command, process and network access for detection.

Detection code never runs a program, resolves a name or opens a connection
itself; it asks a runner. :class:`RealRunner` talks to the real host.
:class:`FixtureRunner` answers from a fixture's ``commands.json``::

    {
      "commands": {"systemd-detect-virt --vm": {"rc": 0, "stdout": "kvm\\n"}},
      "which": {"docker": "/usr/bin/docker"},
      "cpu_count": 24,
      "machine": "x86_64",
      "euid": 0,
      "dns": {"download.docker.com": "192.0.2.10"},
      "https": {"https://download.docker.com/": 200},
      "now": "2026-10-05T09:12:44Z"
    }

Command lines are keyed by ``shlex.join(argv)``. Unknown commands behave like
missing programs (exit code 127), unknown names do not resolve and unknown URLs
are unreachable. A fixture runner never executes anything.
"""

from __future__ import annotations

import concurrent.futures
import datetime as dt
import json
import os
import platform
import shlex
import shutil
import socket
import subprocess
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass

EXIT_NOT_FOUND = 127
EXIT_TIMEOUT = 124
SYSTEM_DIRS = ("/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin", "/sbin", "/bin")
USER_AGENT = "pinka-preflight"
HTTP_SERVER_ERROR = 500


@dataclass(frozen=True)
class CommandResult:
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


@dataclass(frozen=True)
class ProbeResult:
    """Outcome of an HTTPS reachability probe.

    Attributes:
        status: HTTP status code if a response arrived, else None.
        error: Error text if no response arrived, else None.
    """

    status: int | None
    error: str | None

    @property
    def reachable(self) -> bool:
        """True if the server answered at all (any status below 500)."""
        return self.status is not None and self.status < HTTP_SERVER_ERROR


class Runner:
    """Interface of all runners."""

    def run(self, argv: Sequence[str], timeout: float = 10.0) -> CommandResult:
        """Run a command without a shell.

        Args:
            argv: Program and arguments.
            timeout: Seconds before the command is killed.

        Returns:
            The command result.
        """
        raise NotImplementedError

    def which(self, name: str) -> str | None:
        """Locate a program on ``PATH``.

        Args:
            name: Program name.

        Returns:
            The absolute path, or None.
        """
        raise NotImplementedError

    def cpu_count(self) -> int | None:
        """Return the number of logical CPUs (``os.cpu_count()``)."""
        raise NotImplementedError

    def machine(self) -> str:
        """Return the machine architecture (``platform.machine()``)."""
        raise NotImplementedError

    def euid(self) -> int:
        """Return the effective user ID."""
        raise NotImplementedError

    def resolve(self, host: str, timeout: float = 5.0) -> str | None:
        """Resolve a host name.

        Args:
            host: DNS name.
            timeout: Seconds to wait.

        Returns:
            One address, or None if the name does not resolve.
        """
        raise NotImplementedError

    def https_probe(self, url: str, timeout: float = 5.0) -> ProbeResult:
        """Check whether a URL answers.

        Args:
            url: URL to probe (``HEAD``, falling back to ``GET``).
            timeout: Seconds to wait.

        Returns:
            The probe result.
        """
        raise NotImplementedError

    def now(self) -> dt.datetime:
        """Return the current time in UTC."""
        raise NotImplementedError


class RealRunner(Runner):
    """Runner for the real host."""

    def __init__(self) -> None:
        """Build a sanitized environment for child processes."""
        path_dirs = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d]
        for d in SYSTEM_DIRS:
            if d not in path_dirs:
                path_dirs.append(d)
        self._path = os.pathsep.join(path_dirs)
        self._env = {"PATH": self._path, "LC_ALL": "C", "LANG": "C"}

    def run(self, argv: Sequence[str], timeout: float = 10.0) -> CommandResult:  # noqa: D102
        args = tuple(argv)
        try:
            proc = subprocess.run(  # noqa: S603 - argv list, never a shell
                list(args),
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout,
                check=False,
                env=self._env,
                stdin=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            return CommandResult(args, EXIT_NOT_FOUND, "", f"{args[0]}: not found")
        except PermissionError as exc:
            return CommandResult(args, 126, "", str(exc))
        except subprocess.TimeoutExpired:
            return CommandResult(args, EXIT_TIMEOUT, "", f"timed out after {timeout}s")
        return CommandResult(args, proc.returncode, proc.stdout, proc.stderr)

    def which(self, name: str) -> str | None:  # noqa: D102
        return shutil.which(name, path=self._path)

    def cpu_count(self) -> int | None:  # noqa: D102
        return os.cpu_count()

    def machine(self) -> str:  # noqa: D102
        return platform.machine()

    def euid(self) -> int:  # noqa: D102
        return os.geteuid()

    def resolve(self, host: str, timeout: float = 5.0) -> str | None:  # noqa: D102
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = pool.submit(socket.getaddrinfo, host, 443, 0, socket.SOCK_STREAM)
        try:
            infos = future.result(timeout=timeout)
        except (OSError, concurrent.futures.TimeoutError):
            return None
        finally:
            pool.shutdown(wait=False)
        return str(infos[0][4][0]) if infos else None

    def https_probe(self, url: str, timeout: float = 5.0) -> ProbeResult:  # noqa: D102
        last_status: int | None = None
        for method in ("HEAD", "GET"):
            req = urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT})  # noqa: S310
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                    return ProbeResult(int(resp.status), None)
            except urllib.error.HTTPError as exc:
                last_status = int(exc.code)
                if method == "HEAD" and exc.code in (403, 405, 501):
                    continue
                return ProbeResult(last_status, None)
            except (urllib.error.URLError, OSError) as exc:
                return ProbeResult(None, str(getattr(exc, "reason", exc)))
        return ProbeResult(last_status, None)

    def now(self) -> dt.datetime:  # noqa: D102
        return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


class FixtureRunner(Runner):
    """Runner that answers from a fixture's ``commands.json``."""

    def __init__(self, data: dict[str, object]) -> None:
        """Create a runner from parsed fixture data.

        Args:
            data: Parsed ``commands.json``.
        """
        self._data = data

    @classmethod
    def from_file(cls, path: str) -> FixtureRunner:
        """Load a fixture runner; a missing file means "nothing is available".

        Args:
            path: Path of ``commands.json``.

        Returns:
            The runner.
        """
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError:
            data = {}
        if not isinstance(data, dict):
            raise ValueError(f"{path}: must contain a JSON object")
        return cls(data)

    def _section(self, key: str) -> dict[str, object]:
        value = self._data.get(key, {})
        return value if isinstance(value, dict) else {}

    def run(self, argv: Sequence[str], timeout: float = 10.0) -> CommandResult:  # noqa: D102
        args = tuple(argv)
        entry = self._section("commands").get(shlex.join(args))
        if not isinstance(entry, dict):
            return CommandResult(args, EXIT_NOT_FOUND, "", f"{args[0]}: not found")
        return CommandResult(
            args,
            int(entry.get("rc", 0)),
            str(entry.get("stdout", "")),
            str(entry.get("stderr", "")),
        )

    def which(self, name: str) -> str | None:  # noqa: D102
        value = self._section("which").get(name)
        return str(value) if value else None

    def cpu_count(self) -> int | None:  # noqa: D102
        value = self._data.get("cpu_count")
        return int(value) if isinstance(value, int) else None

    def machine(self) -> str:  # noqa: D102
        return str(self._data.get("machine", "x86_64"))

    def euid(self) -> int:  # noqa: D102
        value = self._data.get("euid", 0)
        return int(value) if isinstance(value, int) else 0

    def resolve(self, host: str, timeout: float = 5.0) -> str | None:  # noqa: D102
        value = self._section("dns").get(host)
        return str(value) if value else None

    def https_probe(self, url: str, timeout: float = 5.0) -> ProbeResult:  # noqa: D102
        value = self._section("https").get(url)
        if isinstance(value, int):
            return ProbeResult(value, None)
        return ProbeResult(None, str(value) if value else "unreachable (fixture)")

    def now(self) -> dt.datetime:  # noqa: D102
        value = self._data.get("now", "2026-10-05T09:12:44Z")
        return dt.datetime.strptime(str(value), "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc
        )
