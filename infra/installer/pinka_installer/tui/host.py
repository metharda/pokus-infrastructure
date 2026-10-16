"""What the TUI asks the host, behind one interface the tests replace (BUILD_PROMPT §18.9).

Everything here reads; the only programs it starts are preflight (``--no-write``),
``lsblk``, ``git`` (read-only), ``ssh-keygen`` for a deploy key the operator
asks for, ``git ls-remote`` and ``restic snapshots`` for the Test actions, and
TCP connections for reachability checks. Provisioning happens only in the
non-interactive bootstrap that :mod:`pinka_installer.tui.runner` starts.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from pinka_installer import release
from pinka_installer.answers import Answers
from pinka_installer.paths import Paths

COMMAND_TIMEOUT = 60
BY_ID = Path("/dev/disk/by-id")
STATE_FILE = Path("/etc/pinka/bootstrap-state.json")


@dataclass(frozen=True)
class Disk:
    """A whole disk the Storage screen offers (BUILD_PROMPT §18.4 screen 6).

    Attributes:
        name: Kernel name, such as ``nvme1n1``.
        by_id: Stable path below ``/dev/disk/by-id`` (empty if none).
        model: Model name.
        size_bytes: Size.
        serial: Serial number.
        signatures: File systems or partition tables found on the disk or its partitions.
        mountpoints: Where it or its partitions are mounted.
    """

    name: str
    by_id: str
    model: str
    size_bytes: int
    serial: str = ""
    signatures: tuple[str, ...] = ()
    mountpoints: tuple[str, ...] = ()

    @property
    def usable(self) -> str | None:
        """Why the disk cannot be the pool, or None if it can."""
        if self.mountpoints:
            return f"mounted at {', '.join(self.mountpoints)}"
        if not self.by_id:
            return "no /dev/disk/by-id path"
        return None


@dataclass(frozen=True)
class GitInfo:
    """The checkout bootstrap runs from (Welcome screen).

    Attributes:
        commit: Full commit of HEAD.
        ref: Branch or tag name, or ``HEAD`` when detached.
        dirty: ``infra/`` has uncommitted changes.
    """

    commit: str
    ref: str
    dirty: bool


@dataclass
class CommandResult:
    """A finished command.

    Attributes:
        returncode: Exit status (124 after a timeout, 127 if missing).
        stdout: Output.
        stderr: Errors.
    """

    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass
class Preflight:
    """A preflight run.

    Attributes:
        exit_code: Its exit code.
        profile: The host profile (empty if preflight failed).
        stderr: Its error output.
    """

    exit_code: int
    profile: dict[str, Any] = field(default_factory=dict)
    stderr: str = ""


class Host(Protocol):
    """What the TUI asks the host."""

    def preflight(self, args: Sequence[str]) -> Preflight:
        """Run preflight read-only.

        Args:
            args: Extra options (``--accept-degraded``, ...).
        """
        ...

    def git_info(self) -> GitInfo:
        """The checkout."""
        ...

    def disks(self) -> list[Disk]:
        """Whole disks."""
        ...

    def interfaces(self) -> list[str]:
        """Network interfaces (without lo and tailscale0)."""
        ...

    def free_gib(self, path: str) -> tuple[float, str]:
        """Free space and file system type of the nearest existing directory.

        Args:
            path: A path that may not exist yet.
        """
        ...

    def smt_cores(self) -> list[frozenset[int]]:
        """The CPUs of each core with more than one thread (SMT siblings)."""
        ...

    def run(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        timeout: float = COMMAND_TIMEOUT,
        stdin: str | None = None,
    ) -> CommandResult:
        """Run a command.

        Args:
            argv: The command.
            env: Extra environment.
            timeout: Seconds before it is stopped.
            stdin: Text for its standard input (secrets reach mokutil only this way).
        """
        ...

    def reachable(self, host: str, port: int) -> bool:
        """Whether a TCP connection opens.

        Args:
            host: Host name or address.
            port: Port.
        """
        ...

    def installed_machine(self) -> str:
        """The machine name of the lab installed here (bootstrap state), or empty."""
        ...

    def invoking_user(self) -> str:
        """The user who ran sudo."""
        ...

    def inspect_platform(
        self, answers: Answers, *, full: bool, replace: str | None = None
    ) -> release.Inspection:
        """Inspect the answers' platform release with the platform tool.

        Args:
            answers: The answers (a platform source set).
            full: Also the content packages and the settings; otherwise the
                release alone.
            replace: The active platform the release may replace; None: the
                one bootstrap was started with (``--replace-platform``).
        """
        ...


class RealHost:
    """The host the installer runs on."""

    def __init__(self, paths: Paths, *, replace: str = "") -> None:
        """Bind to the checkout.

        Args:
            paths: Installer paths.
            replace: The active platform bootstrap may replace (``--replace-platform``).
        """
        self.paths = paths
        self.replace = replace

    def run(  # noqa: D102
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        timeout: float = COMMAND_TIMEOUT,
        stdin: str | None = None,
    ) -> CommandResult:
        try:
            done = subprocess.run(  # noqa: S603 - argv built by the installer
                list(argv),
                capture_output=True,
                text=True,
                timeout=timeout,
                env={**os.environ, **(env or {})},
                check=False,
                input=stdin if stdin is not None else "",
            )
        except FileNotFoundError:
            return CommandResult(127, "", f"{argv[0]}: not installed")
        except subprocess.TimeoutExpired:
            return CommandResult(124, "", f"timed out after {timeout:g} s")
        return CommandResult(done.returncode, done.stdout, done.stderr)

    def preflight(self, args: Sequence[str]) -> Preflight:  # noqa: D102
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "profile.json"
            done = self.run(
                [str(self.paths.preflight), "--no-write", "--quiet", "--output", str(output), *args]
            )
            try:
                profile = json.loads(output.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                profile = {}
        return Preflight(done.returncode, profile, done.stderr)

    def git_info(self) -> GitInfo:  # noqa: D102
        git = ["git", "-C", str(self.paths.infra)]
        commit = self.run([*git, "rev-parse", "HEAD"]).stdout.strip() or "unknown"
        ref = self.run([*git, "rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip() or "HEAD"
        dirty = bool(self.run([*git, "status", "--porcelain", "--", "."]).stdout.strip())
        return GitInfo(commit, ref, dirty)

    def disks(self) -> list[Disk]:  # noqa: D102
        columns = "NAME,TYPE,MODEL,SIZE,SERIAL,FSTYPE,PTTYPE,MOUNTPOINTS"
        done = self.run(["lsblk", "--json", "--bytes", "-o", columns])
        try:
            devices = json.loads(done.stdout).get("blockdevices", [])
        except ValueError:
            return []
        by_id = _by_id_names()
        return [_disk(d, by_id) for d in devices if d.get("type") == "disk"]

    def interfaces(self) -> list[str]:  # noqa: D102
        try:
            names = sorted(p.name for p in Path("/sys/class/net").iterdir())
        except OSError:
            return []
        return [n for n in names if n not in {"lo", "tailscale0"}]

    def smt_cores(self) -> list[frozenset[int]]:  # noqa: D102
        from pinka_installer.apply import read_smt_cores  # noqa: PLC0415 - Textual-free import

        return read_smt_cores()

    def free_gib(self, path: str) -> tuple[float, str]:  # noqa: D102
        existing = Path(path)
        while not existing.exists() and existing != existing.parent:
            existing = existing.parent
        usage = shutil.disk_usage(existing)
        fstype = self.run(["stat", "-f", "-c", "%T", str(existing)]).stdout.strip()
        return usage.free / 2**30, fstype

    def reachable(self, host: str, port: int) -> bool:  # noqa: D102
        try:
            with socket.create_connection((host, port), timeout=5):
                return True
        except OSError:
            return False

    def installed_machine(self) -> str:  # noqa: D102
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ""
        return str(data.get("machine", "")) if isinstance(data, dict) else ""

    def invoking_user(self) -> str:  # noqa: D102
        return os.environ.get("SUDO_USER", "")

    def inspect_platform(  # noqa: D102
        self, answers: Answers, *, full: bool, replace: str | None = None
    ) -> release.Inspection:
        replace = self.replace if replace is None else replace
        return release.inspect(answers, self.paths.versions_lock, full=full, replace=replace)


def _by_id_names() -> dict[str, str]:
    """Kernel name to the preferred /dev/disk/by-id path (not wwn-, not partitions)."""
    found: dict[str, str] = {}
    try:
        links = sorted(BY_ID.iterdir())
    except OSError:
        return found
    for link in links:
        if "-part" in link.name:
            continue
        target = link.resolve().name
        current = found.get(target, "")
        better = current.startswith(f"{BY_ID}/wwn-") and not link.name.startswith("wwn-")
        if not current or better:
            found[target] = str(link)
    return found


def _disk(device: Mapping[str, Any], by_id: Mapping[str, str]) -> Disk:
    signatures: list[str] = []
    mountpoints: list[str] = []
    for node in [device, *(device.get("children") or [])]:
        for key in ("fstype", "pttype"):
            if node.get(key):
                signatures.append(str(node[key]))
        mountpoints += [m for m in node.get("mountpoints") or [] if m]
    name = str(device.get("name", ""))
    return Disk(
        name=name,
        by_id=by_id.get(name, ""),
        model=str(device.get("model") or "").strip(),
        size_bytes=int(device.get("size") or 0),
        serial=str(device.get("serial") or "").strip(),
        signatures=tuple(dict.fromkeys(signatures)),
        mountpoints=tuple(dict.fromkeys(mountpoints)),
    )
