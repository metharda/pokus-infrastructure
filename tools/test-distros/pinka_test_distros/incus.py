"""The ``incus`` command line, through an injectable runner."""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class Result:
    """A finished command.

    Attributes:
        argv: The command.
        returncode: Exit status (124 after a timeout).
        stdout: Standard output.
        stderr: Standard error.
    """

    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        """Whether the command exited 0."""
        return self.returncode == 0


class Runner(Protocol):
    """Runs one command."""

    def __call__(self, argv: Sequence[str], *, timeout: float | None = None) -> Result:
        """Run a command.

        Args:
            argv: The command.
            timeout: Seconds before it is killed.
        """
        ...


def run_command(argv: Sequence[str], *, timeout: float | None = None) -> Result:
    """Run a command without a shell.

    Args:
        argv: The command.
        timeout: Seconds before it is killed.

    Returns:
        The result; a timeout gives exit status 124.
    """
    try:
        done = subprocess.run(  # noqa: S603 - argv built by this package
            list(argv), capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        return Result(tuple(argv), 124, out, f"timed out after {timeout:g} s")
    return Result(tuple(argv), done.returncode, done.stdout, done.stderr)


class IncusError(RuntimeError):
    """An incus command failed."""


class Incus:
    """The instances of one Incus project."""

    def __init__(
        self, run: Runner, project: str, sleep: Callable[[float], None] = time.sleep
    ) -> None:
        """Bind a runner to a project.

        Args:
            run: Command runner.
            project: Incus project of the test VMs.
            sleep: Sleep function (tests replace it).
        """
        self._run = run
        self.project = project
        self._sleep = sleep

    def _incus(self, *args: str, timeout: float | None = 600) -> Result:
        return self._run(["incus", *args], timeout=timeout)

    def _check(self, result: Result) -> Result:
        if not result.ok:
            message = result.stderr.strip() or f"exit status {result.returncode}"
            raise IncusError(f"{' '.join(result.argv[:4])}: {message}")
        return result

    def ensure_project(self) -> None:
        """Create the project if missing; it shares the default project's images and profiles."""
        listed = self._check(self._incus("project", "list", "--format", "json"))
        if any(p.get("name") == self.project for p in json.loads(listed.stdout or "[]")):
            return
        self._check(
            self._incus(
                "project",
                "create",
                self.project,
                "--config",
                "features.images=false",
                "--config",
                "features.profiles=false",
            )
        )

    def launch(
        self, name: str, image: str, *, profile: str, cpus: int, memory: str, disk: str
    ) -> None:
        """Launch a VM.

        Args:
            name: Instance name.
            image: ``<remote>:<alias>``.
            profile: Profile with the network and the root disk.
            cpus: vCPUs.
            memory: Memory limit.
            disk: Root disk size.
        """
        self._check(
            self._incus(
                "launch",
                image,
                name,
                "--vm",
                "--project",
                self.project,
                "--profile",
                profile,
                "--config",
                f"limits.cpu={cpus}",
                "--config",
                f"limits.memory={memory}",
                "--device",
                f"root,size={disk}",
                timeout=1800,
            )
        )

    def base_image(self, name: str) -> str:
        """Fingerprint of the image an instance was created from.

        Args:
            name: Instance name.

        Returns:
            ``volatile.base_image``, or an empty string.
        """
        result = self._check(self._incus("query", f"/1.0/instances/{name}?project={self.project}"))
        config = (json.loads(result.stdout or "{}") or {}).get("config") or {}
        return str(config.get("volatile.base_image", ""))

    def exec(self, name: str, argv: Sequence[str], *, timeout: float | None = 600) -> Result:
        """Run a command in an instance.

        Args:
            name: Instance name.
            argv: The command.
            timeout: Seconds before it is killed.

        Returns:
            The result (not checked).
        """
        return self._incus("exec", "--project", self.project, name, "--", *argv, timeout=timeout)

    def wait_ready(self, name: str, timeout: float = 900, interval: float = 5) -> None:
        """Wait until the VM agent answers, then for cloud-init.

        Args:
            name: Instance name.
            timeout: Seconds to wait for the agent.
            interval: Seconds between attempts.

        Raises:
            IncusError: If the agent never answers.
        """
        attempts = max(1, int(timeout // interval))
        for _ in range(attempts):
            if self.exec(name, ["true"], timeout=30).ok:
                break
            self._sleep(interval)
        else:
            raise IncusError(f"{name}: the VM agent did not answer within {timeout:g} s")
        waited = self.exec(
            name,
            ["sh", "-c", "command -v cloud-init >/dev/null || exit 0; cloud-init status --wait"],
            timeout=timeout,
        )
        # 2: done with recoverable errors (as for golden image builds).
        if waited.returncode not in (0, 2):
            raise IncusError(f"{name}: cloud-init failed: {waited.stdout.strip()[-400:]}")

    def restart(self, name: str, timeout: float = 900) -> None:
        """Reboot an instance cleanly (its services are stopped as on any reboot).

        Args:
            name: Instance name.
            timeout: Seconds before the command is killed.
        """
        self._check(self._incus("restart", "--project", self.project, name, timeout=timeout))

    def push(self, name: str, local: Path, remote: str, mode: str = "0600") -> None:
        """Copy a file into an instance.

        Args:
            name: Instance name.
            local: Local file.
            remote: Absolute path in the instance.
            mode: File mode.
        """
        self._check(
            self._incus(
                "file",
                "push",
                "--project",
                self.project,
                "--create-dirs",
                "--mode",
                mode,
                str(local),
                f"{name}{remote}",
            )
        )

    def pull(self, name: str, remote: str, local: Path) -> bool:
        """Copy a file out of an instance.

        Args:
            name: Instance name.
            remote: Absolute path in the instance.
            local: Local destination.

        Returns:
            Whether the file was copied.
        """
        local.parent.mkdir(parents=True, exist_ok=True)
        return self._incus(
            "file", "pull", "--project", self.project, f"{name}{remote}", str(local)
        ).ok

    def stop(self, name: str) -> None:
        """Stop an instance and keep it (a VM kept for inspection).

        Args:
            name: Instance name.
        """
        self._incus("stop", "--project", self.project, "--force", name)

    def delete(self, name: str) -> None:
        """Delete an instance, stopping it first.

        Args:
            name: Instance name.
        """
        self._incus("delete", "--project", self.project, "--force", name)
