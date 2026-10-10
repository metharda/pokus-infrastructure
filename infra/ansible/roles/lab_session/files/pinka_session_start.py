#!/opt/pinka/ansible-venv/bin/python3 -I
# Managed by pinka (ansible/roles/lab_session/files/pinka_session_start.py). Local changes will be overwritten.
"""Record the lab session of this boot (BUILD_PROMPT §7.16, contracts.md).

``pinka-session-start.service`` runs this once per boot. It writes
``/var/lib/pinka/sessions/<boot_id>.json`` (0644 root) with the boot ID, the
start time, the environment fingerprint and its hash (``pinka fingerprint
--json``, run as root, which also refreshes the fingerprint cache) and the
outcome of a fresh read-only preflight (``pinka preflight --json``); the
active platform's controller reads the records. A record that exists is never rewritten.
When a command fails, its field is ``null`` and the reason is recorded, so a
session is still recorded.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

BOOT_ID = Path("/proc/sys/kernel/random/boot_id")
COMMAND_TIMEOUT = 600
Runner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]


def run(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """Run a command without a shell.

    Args:
        argv: Command and arguments.

    Returns:
        The completed process (never raises for a non-zero exit).
    """
    try:
        return subprocess.run(  # noqa: S603 - fixed argv
            list(argv), capture_output=True, text=True, timeout=COMMAND_TIMEOUT, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(list(argv), 127, "", str(exc))


def json_of(argv: Sequence[str], runner: Runner, accept: Sequence[int]) -> tuple[Any, str | None]:
    """Run a command that prints JSON.

    Args:
        argv: Command.
        runner: Command runner.
        accept: Exit codes that still carry a valid document.

    Returns:
        The parsed document and None, or None and the reason it is missing.
    """
    done = runner(argv)
    if done.returncode not in accept:
        reason = done.stderr.strip().splitlines()[-1:] or [f"exit status {done.returncode}"]
        return None, f"{argv[0]} {argv[1]}: {reason[0]}"
    try:
        return json.loads(done.stdout), None
    except ValueError as exc:
        return None, f"{argv[0]} {argv[1]}: no JSON ({exc})"


def build_record(boot_id: str, started_at: str, pinka_lab: str, runner: Runner) -> dict[str, Any]:
    """Collect a session record.

    Args:
        boot_id: The kernel's boot ID.
        started_at: UTC time in ISO 8601 with ``Z``.
        pinka_lab: Path of the ``pinka`` CLI.
        runner: Command runner.

    Returns:
        The record.
    """
    errors: dict[str, str] = {}
    fingerprint, error = json_of([pinka_lab, "fingerprint", "--json"], runner, [0])
    if error:
        errors["fingerprint"] = error
    # Preflight exits 10/20/30 for degraded, unsupported and needs_action; its
    # report is valid in every case (BUILD_PROMPT §2.5).
    report, error = json_of([pinka_lab, "preflight", "--json"], runner, [0, 10, 20, 30])
    if error:
        errors["preflight"] = error
    outcome = report.get("outcome") if isinstance(report, dict) else None
    fingerprint_hash = fingerprint.get("hash") if isinstance(fingerprint, dict) else None
    return {
        "schema_version": 1,
        "boot_id": boot_id,
        "started_at": started_at,
        "fingerprint_hash": fingerprint_hash,
        "fingerprint": fingerprint,
        "preflight_outcome": outcome,
        "errors": errors,
    }


def write_record(directory: Path, record: dict[str, Any]) -> Path | None:
    """Write a record unless one exists for this boot.

    Args:
        directory: Sessions directory.
        record: The record.

    Returns:
        The written path, or None if a record already existed.
    """
    path = directory / f"{record['boot_id']}.json"
    if path.exists():
        return None
    directory.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    tmp.chmod(0o644)
    tmp.replace(path)
    return path


def main(argv: Sequence[str], runner: Runner = run) -> int:
    """Record the session of this boot.

    Args:
        argv: Arguments without the program name.
        runner: Command runner.

    Returns:
        0 when the record exists afterwards, 1 otherwise.
    """
    parser = argparse.ArgumentParser(prog="session-start", description=__doc__.splitlines()[0])
    parser.add_argument("--sessions", type=Path, default=Path("/var/lib/pinka/sessions"))
    parser.add_argument("--pinka", default="/usr/local/bin/pinka")
    parser.add_argument("--boot-id-file", type=Path, default=BOOT_ID)
    args = parser.parse_args(list(argv))
    try:
        boot_id = args.boot_id_file.read_text(encoding="ascii").strip()
    except OSError as exc:
        print(f"session-start: cannot read the boot ID: {exc}", file=sys.stderr)
        return 1
    if (args.sessions / f"{boot_id}.json").exists():
        print(f"session-start: session {boot_id} is already recorded", file=sys.stderr)
        return 0
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    record = build_record(boot_id, started_at, args.pinka, runner)
    try:
        path = write_record(args.sessions, record)
    except OSError as exc:
        print(f"session-start: {exc}", file=sys.stderr)
        return 1
    for field, reason in record["errors"].items():
        print(f"session-start: {field} not recorded: {reason}", file=sys.stderr)
    print(f"session-start: recorded {path or boot_id}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
