"""A fake host and a fake bootstrap for the TUI tests.

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

from ruamel.yaml import YAML

from pinka_installer import release
from pinka_installer.answers import Answers
from pinka_installer.paths import INFRA, Paths
from pinka_installer.tui.app import InstallerApp, build_context
from pinka_installer.tui.base import Context
from pinka_installer.tui.events import read_new
from pinka_installer.tui.host import CommandResult, Disk, GitInfo, Preflight
from pinka_installer.tui.runner import RunRequest, UninstallRun
from pinka_platform import config as config_mod
from pinka_platform import contract as contract_mod
from pinka_platform import deploy, validate

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


REFPLAT = INFRA / "tests" / "reference-platform"


def refplat_inspection(answers: Answers, *, full: bool) -> release.Inspection:
    """The reference platform's release, inspected as the platform tool would.

    The manifest, its summary, its secrets, config schemas and vault keys are
    the real ones; the settings are checked against the real schema when
    ``full``.

    Args:
        answers: The answers (their release source and settings).
        full: Check the settings too.

    Returns:
        The inspection.
    """
    manifest = YAML(typ="safe").load((REFPLAT / "platform-manifest.yaml").read_text())
    contract = contract_mod.load()
    if full:
        problems = config_mod.check(manifest, REFPLAT, answers.platform_settings)
        if problems:
            return release.Inspection(ok=False, problems=[str(p) for p in problems])
    summary = validate.summary(manifest, contract)
    source = answers.platform
    found = {
        "platform": {
            "name": manifest["name"],
            "version": manifest["version"],
            "runtime": manifest["runtime"],
        },
        "release": {
            "source": {"repo": source.repo, "ref": source.ref, "path": source.path},
            "commit": COMMIT,
            "tree": COMMIT,
            "tag": source.ref,
            "name": source.ref,
            "signed_tag": answers.source_trust.require_signed_tags,
        },
        "summary": summary,
        "content": summary["content"],
        "secrets": {
            s["name"]: list(s["secrets"]) for s in manifest["services"] if s.get("secrets")
        },
        "config": {
            s["name"]: json.loads((REFPLAT / s["config"]).read_text())
            for s in manifest["services"]
            if "config" in s
        },
        "vault_keys": deploy.vault_keys(manifest, contract, ()),
        "warnings": validate.caps_warnings(
            manifest,
            {
                "memory_max": answers.training_caps.memory_max,
                "cpus": answers.training_caps.cpus,
                "timeout_seconds": answers.training_caps.timeout_seconds,
            },
        ),
    }
    return release.Inspection(ok=True, found=found)


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
    refused: list[str] = field(default_factory=list)  # set: the release is refused
    inspections: list[tuple[bool, Answers]] = field(default_factory=list)
    # set: another platform is active; a full inspection without replacing it
    # is refused by the one-platform rule, as the platform tool does
    active_platform: str = ""
    replaces: list[str | None] = field(default_factory=list)

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

    def inspect_platform(
        self, answers: Answers, *, full: bool, replace: str | None = None
    ) -> release.Inspection:
        self.inspections.append((full, copy.deepcopy(answers)))
        self.replaces.append(replace)
        if self.refused:
            return release.Inspection(ok=False, problems=list(self.refused))
        if full and self.active_platform and replace != self.active_platform:
            problem = (
                f"platform-active: name: platform {self.active_platform!r} is active; a release "
                f"of 'refplat' replaces it only with --replace-platform {self.active_platform}"
            )
            return release.Inspection(ok=False, problems=[problem], active=self.active_platform)
        return refplat_inspection(answers, full=full)


@dataclass
class FakeRunner:
    """Replays recorded bootstrap runs, one per call.

    Uninstall's dry run answers with ``data/uninstall-plan.json`` (the module's
    plan of a full install) and its removal with ``data/uninstall-report.json``
    and ``uninstall_code``.
    """

    runs: list[tuple[str, int]]
    requests: list[RunRequest] = field(default_factory=list)
    cancelled: bool = False
    log_file: str = ""
    uninstall_calls: list[list[str]] = field(default_factory=list)
    uninstall_code: int = 81
    plan: dict[str, Any] = field(
        default_factory=lambda: json.loads((DATA / "uninstall-plan.json").read_text())
    )

    def uninstall(self, options: Sequence[str], on_output: Callable[[str], None]) -> UninstallRun:
        self.uninstall_calls.append(list(options))
        if "--dry-run" in options:
            return UninstallRun(0, dict(self.plan))
        on_output("bootstrap: replayed uninstall")
        report = json.loads((DATA / "uninstall-report.json").read_text())
        return UninstallRun(self.uninstall_code, report)

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
