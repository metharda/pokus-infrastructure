"""Inside a test VM: the harness's helpers for the reference platform.

Run by the harness with the Ansible venv's Python, as a script, never
imported by the lab: ``python -I guest.py
<command> ...``; every command prints one JSON line.

The harness enables, replaces and restores the platform through the
installer's answers (``bootstrap.sh --answers``); this helper only waits:

* ``wait-report``, ``wait-run`` and ``wait-checkpoint`` wait for the agent's
  probe report, for a training run's end and for a run's checkpoint.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

CLI = "/usr/local/bin/refplat"
JOBS = Path("/srv/pinka/training/jobs")
# The reference platform's final run statuses (refplat.training.FINAL).
FINAL = ("succeeded", "failed", "interrupted", "ended")
INTERVAL = 5.0


def cli(*args: str) -> dict[str, Any] | None:
    """One answer of the reference platform's CLI as root, or None if it gave none."""
    done = subprocess.run([CLI, *args], capture_output=True, text=True, check=False)  # noqa: S603
    try:
        value = json.loads(done.stdout)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def poll(
    probe: Callable[[], dict[str, Any] | None],
    timeout: float,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any] | None:
    """Call ``probe`` until it returns something or the time is up."""
    deadline = time.monotonic() + timeout
    while True:
        found = probe()
        if found is not None or time.monotonic() >= deadline:
            return found
        sleep(INTERVAL)


def agent_report() -> dict[str, Any] | None:
    """The agent's latest probe report, once it sent one."""
    answer = cli("probe-report") or {}
    report = answer.get("report") or {}
    return report if report.get("outcomes") else None


def run_end(run: str) -> dict[str, Any] | None:
    """A run's record in the controller once it is final."""
    item = ((cli("runs") or {}).get("runs") or {}).get(run)
    return {"run": run, **item} if item and item.get("status") in FINAL else None


def checkpoint(run: str, step: int, jobs: Path = JOBS) -> dict[str, Any] | None:
    """A run's checkpoint once it reached ``step``."""
    try:
        saved = json.loads((jobs / run / "checkpoints" / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return saved if isinstance(saved, dict) and int(saved.get("step", 0)) >= step else None


def emit(value: Mapping[str, Any] | None, what: str) -> int:
    """Print the result line; 1 when nothing came."""
    if value is None:
        sys.stdout.write(json.dumps({"ok": False, "error": f"no {what} in time"}) + "\n")
        return 1
    sys.stdout.write(json.dumps({"ok": True, **value}, sort_keys=True) + "\n")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run a command; prints one JSON line."""
    parser = argparse.ArgumentParser(prog="guest.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    report = sub.add_parser("wait-report")
    report.add_argument("--timeout", type=float, default=600)
    run = sub.add_parser("wait-run")
    run.add_argument("run")
    run.add_argument("--timeout", type=float, default=1800)
    step = sub.add_parser("wait-checkpoint")
    step.add_argument("run")
    step.add_argument("--step", type=int, required=True)
    step.add_argument("--timeout", type=float, default=600)
    args = parser.parse_args(argv)
    if args.command == "wait-report":
        return emit(poll(agent_report, args.timeout), "probe report of the agent")
    if args.command == "wait-run":
        ended = poll(lambda: run_end(args.run), args.timeout)
        code = emit(ended, f"end of run {args.run}")
        return code if code or (ended or {}).get("status") == "succeeded" else 1
    found = poll(lambda: checkpoint(args.run, args.step), args.timeout)
    return emit(found, f"checkpoint of run {args.run} at step {args.step}")


if __name__ == "__main__":
    sys.exit(main())
