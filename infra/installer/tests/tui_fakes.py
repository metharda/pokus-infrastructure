"""A fake host and a fake bootstrap for the TUI tests (BUILD_PROMPT §18.9).

Host profiles are real: preflight runs read-only on its own synthetic fixtures
(``preflight/tests/fixtures``). Bootstrap runs are recorded event streams
(``data/events-*.jsonl``, see ``data/record_events.py``) replayed by
:class:`FakeRunner`. Doctor reports come from pinka's own JSON renderer.
"""

from __future__ import annotations

import copy
import functools
import json
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pinka_installer.paths import INFRA, Paths
from pinka_installer.tui.app import InstallerApp, build_context
from pinka_installer.tui.base import Context
from pinka_installer.tui.events import read_new
from pinka_installer.tui.host import CommandResult, Disk, GitInfo, Preflight
from pinka_installer.tui.runner import RunRequest

DATA = Path(__file__).resolve().parent / "data"
FIXTURES = INFRA / "preflight" / "tests" / "fixtures"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
DISK_BY_ID = "/dev/disk/by-id/nvme-EXAMPLE_SSD_0000000000"
USED_BY_ID = "/dev/disk/by-id/nvme-EXAMPLE_SSD_1111111111"
DISKS = (
    Disk("nvme1n1", DISK_BY_ID, "Example SSD 2TB", 2 * 2**40, "0000000000"),
    Disk(
        "nvme0n1",
        USED_BY_ID,
        "Example SSD 1TB",
        2**40,
        "1111111111",
        ("gpt", "ext4"),
        ("/",),
    ),
    Disk("sda", "/dev/disk/by-id/ata-EXAMPLE_HDD_2222", "Example HDD", 4 * 2**40, "2222", ("xfs",)),
)


@functools.cache
def _profile_text(fixture: str, args: tuple[str, ...] = ()) -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "profile.json"
        done = subprocess.run(
            [
                str(INFRA / "preflight" / "pinka-preflight"),
                "--root",
                str(FIXTURES / fixture),
                "--no-write",
                "--offline",
                "--output",
                str(out),
                *args,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        return done.returncode, out.read_text(encoding="utf-8")


def preflight(fixture: str, args: Sequence[str] = ()) -> Preflight:
    """Preflight's result on one of its fixtures.

    Args:
        fixture: A directory of ``preflight/tests/fixtures``.
        args: Preflight options (the operator's choices).

    Returns:
        The exit code and the host profile.
    """
    code, text = _profile_text(fixture, tuple(args))
    return Preflight(code, json.loads(text))


def doctor_report(warnings: int = 1, failures: int = 0) -> str:
    """A ``pinka doctor --json`` report rendered by pinka itself."""
    sys.path.insert(0, str(INFRA / "tools" / "pinka-lab"))
    from pinka_lab.doctor import Finding, render_json  # noqa: PLC0415

    findings = [
        Finding("services.units", "The lab's units are active", "error", "pass", "12 active", ""),
        Finding("security.ssh", "sshd allows keys only", "error", "pass", "keys only", ""),
    ]
    findings += [
        Finding(
            f"backups.recent{i or ''}",
            "A backup finished in the last 26 hours",
            "warning",
            "fail",
            "no backup yet",
            "Run sudo systemctl start pinka-backup.service",
        )
        for i in range(warnings)
    ]
    findings += [
        Finding(
            f"storage.pool{i or ''}",
            "The Incus pool has free space",
            "error",
            "fail",
            "95 % used",
            "Free space in the pool",
        )
        for i in range(failures)
    ]
    return render_json(findings, "pinka-lab", "2026-01-01T00:00:00Z")


@dataclass
class FakeHost:
    """The host, as the TUI sees it."""

    result: Preflight
    disks_found: Sequence[Disk] = DISKS
    nics: Sequence[str] = ("enp5s0", "wlp3s0")
    free: tuple[float, str] = (500.0, "ext4")
    cores: Sequence[frozenset[int]] = ()
    installed: str = ""
    user: str = "researcher1"
    replies: dict[str, CommandResult] = field(default_factory=dict)
    calls: list[tuple[list[str], dict[str, str], str | None]] = field(default_factory=list)
    preflight_calls: int = 0
    fixture: str = ""  # set: preflight runs on this fixture with the TUI's options
    preflight_args: list[list[str]] = field(default_factory=list)

    def preflight(self, args: Sequence[str]) -> Preflight:
        self.preflight_calls += 1
        self.preflight_args.append(list(args))
        if self.fixture:
            return preflight(self.fixture, args)
        return copy.deepcopy(self.result)

    def git_info(self) -> GitInfo:
        return GitInfo(COMMIT, "main", False)

    def disks(self) -> list[Disk]:
        return list(self.disks_found)

    def interfaces(self) -> list[str]:
        return list(self.nics)

    def free_gib(self, path: str) -> tuple[float, str]:
        return self.free

    def smt_cores(self) -> list[frozenset[int]]:
        return list(self.cores)

    def run(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        timeout: float = 60,
        stdin: str | None = None,
    ) -> CommandResult:
        command = list(argv)
        self.calls.append((command, dict(env or {}), stdin))
        for prefix in sorted(self.replies, key=len, reverse=True):
            if " ".join(command).startswith(prefix):
                return self.replies[prefix]
        if command[:2] == ["pinka", "doctor"]:
            return CommandResult(1, doctor_report())
        return CommandResult(0)

    def reachable(self, host: str, port: int) -> bool:
        return host.endswith("example.com")

    def installed_machine(self) -> str:
        return self.installed

    def invoking_user(self) -> str:
        return self.user


@dataclass
class FakeRunner:
    """Replays recorded bootstrap runs, one per call."""

    runs: list[tuple[str, int]]
    requests: list[RunRequest] = field(default_factory=list)
    cancelled: bool = False
    log_file: str = ""

    def run(
        self,
        request: RunRequest,
        on_events: Callable[[list[dict[str, Any]]], None],
        on_output: Callable[[str], None],
    ) -> int:
        self.requests.append(request)
        name, code = self.runs.pop(0) if len(self.runs) > 1 else self.runs[0]
        events, _ = read_new(DATA / name, 0)
        for start in range(0, len(events), 25):
            on_events(events[start : start + 25])
        on_output(f"bootstrap: replayed {name}")
        return code

    def cancel(self) -> None:
        self.cancelled = True


def context(
    paths: Paths,
    host: FakeHost,
    runner: FakeRunner | None = None,
    *,
    machine: str = "",
    answers: Path | None = None,
    bootstrap_args: Sequence[str] = (),
) -> Context:
    """The context of a test run, with a fixed clock."""
    ctx = build_context(
        paths,
        host,
        runner or FakeRunner([("events-success.jsonl", 1)]),
        machine=machine,
        answers=answers,
        bootstrap_args=bootstrap_args,
    )
    ctx.clock = lambda: 0.0
    return ctx


def app(ctx: Context, first: str = "welcome", theme: str = "") -> InstallerApp:
    """The installer application on a test context."""
    return InstallerApp(ctx, first=first, theme=theme)
