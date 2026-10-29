#!/opt/pinka/ansible-venv/bin/python3 -I
# Managed by pinka (ansible/roles/lab_session/files/pinka_health_gate.py). Local changes will be overwritten.
"""Startup gate of the lab (contracts.md, C9h).

``pinka-health-gate.service`` runs this once per start of ``pinka-lab.target``;
the platform's services require that unit, so none starts before every check
passed. The clock is always checked (synchronized and, on WSL, within a
threshold of an NTP reference when one answers); then exactly the facilities
that the active platform's manifest lists in ``requires`` (read from
``rendered.json`` at every start), each healthy: ``postgres`` (the container
reports healthy), ``incus`` (the API answers), ``telemetry`` (the collector
gateway's health check), ``inference`` (the inference API the platform is
told in ``endpoints.toml``), ``opa`` (its health endpoint) and ``gpu`` (a
container sees the GPU). A facility the platform requires that this node does
not run fails the gate. Without a platform only the clock is checked. Checks
are repeated until all pass or the timeout expires. A maintenance window
fails the gate at once: on production while its record exists, on
staging with shared hardware unless its window is open now. The result is
written as JSON for doctor and operators. Standard library only.
"""

from __future__ import annotations

import argparse
import calendar
import json
import socket
import struct
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

NTP_EPOCH_OFFSET = 2208988800  # seconds from 1900-01-01 to 1970-01-01
NTP_PORT = 123
COMMAND_TIMEOUT = 60
# The facilities a manifest may require (`requires`), in
# the order the gate checks them.
FACILITIES = ("postgres", "incus", "telemetry", "inference", "opa", "gpu")
HTTP_OK = 200


@dataclass(frozen=True)
class Result:
    """Outcome of one check.

    Attributes:
        name: Check name.
        ok: True if it passed (a skipped part counts as passed).
        detail: What was found.
    """

    name: str
    ok: bool
    detail: str


Runner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]
Fetch = Callable[[str], int]
Sntp = Callable[[str, float], float]


def run(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """Run a command without a shell.

    Args:
        argv: Command and arguments.

    Returns:
        The completed process (never raises for a non-zero exit).
    """
    try:
        return subprocess.run(  # noqa: S603 - fixed argv from the configuration
            list(argv), capture_output=True, text=True, timeout=COMMAND_TIMEOUT, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(list(argv), 127, "", str(exc))


def http_status(url: str) -> int:
    """Return the HTTP status of a GET request (0 if it failed).

    Args:
        url: Loopback URL of a health endpoint.

    Returns:
        The status code.
    """
    try:
        with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310 - configured loopback URL
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except (OSError, ValueError):
        return 0


def sntp_offset(server: str, timeout: float, port: int = NTP_PORT) -> float:
    """Ask an NTP server for the clock offset (RFC 4330 client).

    Args:
        server: Host name or address.
        timeout: Seconds to wait for the answer.
        port: UDP port of the server.

    Returns:
        Offset of the local clock in seconds (positive: the local clock is behind).

    Raises:
        OSError: If the server cannot be reached or answers nonsense.
    """
    request = b"\x23" + 47 * b"\0"  # LI 0, version 4, mode 3 (client)
    infos = socket.getaddrinfo(server, port, type=socket.SOCK_DGRAM)
    family, kind, proto, _, address = infos[0]
    with socket.socket(family, kind, proto) as sock:
        sock.settimeout(timeout)
        sent = time.time()
        sock.sendto(request, address)
        data, _ = sock.recvfrom(512)
        received = time.time()
    if len(data) < 48 or data[0] & 0x07 != 4:  # noqa: PLR2004 - packet size, mode 4 (server)
        raise OSError("not an NTP server answer")
    seconds, fraction = struct.unpack("!II", data[32:40])  # receive timestamp
    server_received = seconds - NTP_EPOCH_OFFSET + fraction / 2**32
    seconds, fraction = struct.unpack("!II", data[40:48])  # transmit timestamp
    server_sent = seconds - NTP_EPOCH_OFFSET + fraction / 2**32
    return float(((server_received - sent) + (server_sent - received)) / 2)


def check_time(config: Mapping[str, Any], runner: Runner, sntp: Sntp) -> Result:
    """The clock is synchronized; on WSL also close to an NTP reference.

    Args:
        config: ``wsl``, ``max_offset`` (seconds), ``ntp_reference`` (empty: none).
        runner: Command runner.
        sntp: NTP offset function.

    Returns:
        The result.
    """
    shown = runner(["timedatectl", "show", "--property=NTPSynchronized", "--value"])
    if shown.returncode != 0 or shown.stdout.strip() != "yes":
        return Result("time", ok=False, detail="the system clock is not NTP-synchronized")
    if not config.get("wsl") or not config.get("ntp_reference"):
        return Result("time", ok=True, detail="NTP-synchronized")
    try:
        offset = sntp(str(config["ntp_reference"]), 2.0)
    except OSError as exc:
        return Result("time", ok=True, detail=f"NTP-synchronized; reference not reachable ({exc})")
    limit = float(config.get("max_offset", 1.0))
    detail = f"offset {offset:+.3f} s to {config['ntp_reference']} (limit {limit} s)"
    return Result("time", ok=abs(offset) <= limit, detail=detail)


def check_postgres(config: Mapping[str, Any], runner: Runner) -> Result:
    """The PostgreSQL container reports itself healthy.

    Args:
        config: ``container``.
        runner: Command runner.

    Returns:
        The result.
    """
    shown = runner(
        ["docker", "inspect", "--format", "{{.State.Health.Status}}", str(config["container"])]
    )
    status = shown.stdout.strip() if shown.returncode == 0 else "missing"
    return Result("postgres", ok=status == "healthy", detail=f"container health: {status}")


def check_telemetry(config: Mapping[str, Any], fetch: Fetch) -> Result:
    """The collector gateway's health check answers 200.

    Args:
        config: ``url``.
        fetch: HTTP status function.

    Returns:
        The result.
    """
    status = fetch(str(config["url"]))
    return Result("telemetry", ok=status == HTTP_OK, detail=f"HTTP {status or 'no answer'}")


def check_opa(config: Mapping[str, Any], fetch: Fetch) -> Result:
    """OPA's health endpoint answers 200.

    Args:
        config: ``url``.
        fetch: HTTP status function.

    Returns:
        The result.
    """
    status = fetch(str(config["url"]))
    return Result("opa", ok=status == HTTP_OK, detail=f"HTTP {status or 'no answer'}")


def check_inference(config: Mapping[str, Any], fetch: Fetch) -> Result:
    """The inference API the platform is told in ``endpoints.toml`` answers.

    The local server and the forwarder of a lab node answer ``/health`` with
    200; a scripted backend (staging) only has to answer at all.

    Args:
        config: ``endpoints``: the platform's endpoints file.
        fetch: HTTP status function.

    Returns:
        The result.
    """
    try:
        with Path(str(config["endpoints"])).open("rb") as handle:
            inference = tomllib.load(handle).get("inference") or {}
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return Result("inference", ok=False, detail=f"no inference endpoint: {exc}")
    url = str(inference.get("api_url", ""))
    if not url:
        return Result("inference", ok=False, detail="endpoints.toml names no inference API")
    if inference.get("mode") == "scripted":
        status = fetch(url)
        return Result("inference", ok=status != 0, detail=f"scripted backend: HTTP {status}")
    status = fetch(f"{url.rstrip('/')}/health")
    return Result(
        "inference", ok=status == HTTP_OK, detail=f"/health: HTTP {status or 'no answer'}"
    )


def check_incus(config: Mapping[str, Any], runner: Runner) -> Result:
    """The Incus API answers.

    Args:
        config: Unused.
        runner: Command runner.

    Returns:
        The result.
    """
    del config
    shown = runner(["incus", "query", "/1.0"])
    if shown.returncode != 0:
        return Result("incus", ok=False, detail=shown.stderr.strip() or "no answer")
    return Result("incus", ok=True, detail="API answers")


def check_gpu(config: Mapping[str, Any], runner: Runner) -> Result:
    """A container sees the GPU (the way the lab's services get it).

    Args:
        config: ``image`` (by digest, already pulled) and ``device_args``.
        runner: Command runner.

    Returns:
        The result.
    """
    argv = ["docker", "run", "--rm", "--pull", "never", *config["device_args"]]
    shown = runner([*argv, str(config["image"]), "nvidia-smi", "--list-gpus"])
    gpus = [line for line in shown.stdout.splitlines() if line.startswith("GPU ")]
    if shown.returncode != 0 or not gpus:
        return Result("gpu", ok=False, detail=shown.stderr.strip() or "no GPU visible")
    return Result("gpu", ok=True, detail=f"{len(gpus)} GPU(s) visible")


def run_checks(checks: Mapping[str, Any], runner: Runner, fetch: Fetch, sntp: Sntp) -> list[Result]:
    """Run every configured check once.

    Args:
        checks: Check name to its configuration.
        runner: Command runner.
        fetch: HTTP status function.
        sntp: NTP offset function.

    Returns:
        One result per check, in a fixed order.
    """
    table: dict[str, Callable[[Mapping[str, Any]], Result]] = {
        "time": lambda c: check_time(c, runner, sntp),
        "postgres": lambda c: check_postgres(c, runner),
        "incus": lambda c: check_incus(c, runner),
        "telemetry": lambda c: check_telemetry(c, fetch),
        "inference": lambda c: check_inference(c, fetch),
        "opa": lambda c: check_opa(c, fetch),
        "gpu": lambda c: check_gpu(c, runner),
    }
    unknown = sorted(set(checks) - set(table))
    results = [Result(name, ok=False, detail="unknown check") for name in unknown]
    return results + [table[name](checks[name]) for name in table if name in checks]


def platform_requires(path: str | None) -> list[str]:
    """The facilities the active platform's manifest requires.

    Args:
        path: ``rendered.json`` (None: no platform on this node).

    Returns:
        The ``requires`` list; empty without a platform.
    """
    if not path:
        return []
    try:
        rendered = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    requires = rendered.get("requires") if isinstance(rendered, dict) else None
    return [str(r) for r in requires] if isinstance(requires, list) else []


WINDOW_TIME = "%Y-%m-%dT%H:%M:%SZ"


class WindowRecordError(ValueError):
    """The maintenance window record is not a window."""


def read_window(path: Path) -> tuple[str, str, float, float] | None:
    """Read a maintenance window record.

    Args:
        path: The record.

    Returns:
        ``(start, end, start_seconds, end_seconds)``, or None without a record.

    Raises:
        WindowRecordError: If the record cannot be read as a window.
    """
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        start, end = str(record["start"]), str(record["end"])
        return (
            start,
            end,
            calendar.timegm(time.strptime(start, WINDOW_TIME)),
            calendar.timegm(time.strptime(end, WINDOW_TIME)),
        )
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise WindowRecordError(f"{path} is not a maintenance window record") from exc


def check_window(config: Mapping[str, Any] | None, now: float) -> Result | None:
    """A maintenance window that keeps the lab stopped.

    Args:
        config: ``record`` (the window record), ``mode`` (``production`` or
            ``staging``) and ``shared`` (staging on production's hardware);
            None: no window rules on this node.
        now: The wall clock, seconds since the epoch.

    Returns:
        A result when a window decides this start, else None.
    """
    if not config:
        return None
    staging = config.get("mode") == "staging"
    if staging and not config.get("shared"):
        return None  # staging on its own hardware needs no window
    try:
        window = read_window(Path(str(config["record"])))
    except WindowRecordError as exc:
        return Result("window", ok=False, detail=str(exc))
    if not staging:
        return (
            None
            if window is None
            else Result(
                "window",
                ok=False,
                detail=f"maintenance window {window[0]}-{window[1]} recorded: production "
                "stays stopped until pinka window close",
            )
        )
    if window is None or not window[2] <= now < window[3]:
        return Result(
            "window",
            ok=False,
            detail="staging on shared hardware starts only inside an open maintenance window",
        )
    return Result("window", ok=True, detail=f"maintenance window open until {window[1]}")


def select_checks(config: Mapping[str, Any]) -> tuple[dict[str, Any], list[Result]]:
    """The checks of this start: ``checks`` (the clock) and the required facilities.

    Args:
        config: ``checks``, ``facilities`` (what this node runs) and
            ``rendered`` (the active platform's ``rendered.json``).

    Returns:
        The checks to run, and a failed result for each required facility
        this node does not run.
    """
    checks = dict(config["checks"])
    facilities = config.get("facilities") or {}
    missing = []
    for name in platform_requires(config.get("rendered")):
        if name in facilities:
            checks[name] = facilities[name]
        else:
            missing.append(
                Result(
                    name,
                    ok=False,
                    detail="the platform's manifest requires it; this node does not run it",
                )
            )
    return checks, missing


def gate(
    config: Mapping[str, Any],
    *,
    runner: Runner = run,
    fetch: Fetch = http_status,
    sntp: Sntp = sntp_offset,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    wall: Callable[[], float] = time.time,
) -> tuple[bool, list[Result], int]:
    """Repeat the checks until all pass or the timeout expires.

    Args:
        config: ``timeout``, ``interval`` (seconds), ``checks``, ``facilities``
            and ``rendered`` (:func:`select_checks`).
        runner: Command runner.
        fetch: HTTP status function.
        sntp: NTP offset function.
        sleep: Sleep function.
        clock: Monotonic clock.
        wall: Wall clock (the maintenance window).

    Returns:
        Whether all passed, the last results and the number of rounds.
    """
    window = check_window(config.get("window"), wall())
    if window is not None and not window.ok:
        return False, [window], 1
    deadline = clock() + float(config.get("timeout", 600))
    interval = float(config.get("interval", 5))
    checks, missing = select_checks(config)
    if window is not None:
        missing = [window, *missing]
    rounds = 0
    while True:
        rounds += 1
        results = missing + run_checks(checks, runner, fetch, sntp)
        if all(r.ok for r in results):
            return True, results, rounds
        if clock() + interval > deadline:
            return False, results, rounds
        sleep(interval)


def write_status(path: Path, passed: bool, results: Sequence[Result], rounds: int) -> None:
    """Write the gate's result atomically.

    Args:
        path: Status file.
        passed: Overall result.
        results: Results of the last round.
        rounds: Number of rounds.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    status = {
        "schema_version": 1,
        "passed": passed,
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "rounds": rounds,
        "checks": [asdict(r) for r in results],
    }
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(status, indent=1) + "\n", encoding="utf-8")
    tmp.chmod(0o644)
    tmp.replace(path)


def main(argv: Sequence[str]) -> int:
    """Run the gate.

    Args:
        argv: Arguments without the program name.

    Returns:
        0 when every check passed, 1 otherwise, 2 on a configuration error.
    """
    parser = argparse.ArgumentParser(prog="health-gate", description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=Path("/etc/pinka/health-gate.json"))
    parser.add_argument("--status", type=Path, default=Path("/run/pinka/health-gate.json"))
    parser.add_argument("--once", action="store_true", help="check once, do not wait")
    args = parser.parse_args(list(argv))
    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        if not isinstance(config.get("checks"), dict):
            raise ValueError("checks must be a mapping")
    except (OSError, ValueError) as exc:
        print(f"health-gate: {args.config}: {exc}", file=sys.stderr)
        return 2
    if args.once:
        config = {**config, "timeout": 0}
    passed, results, rounds = gate(config)
    for r in results:
        print(f"health-gate: {r.name}: {'ok' if r.ok else 'FAILED'}: {r.detail}", file=sys.stderr)
    write_status(args.status, passed, results, rounds)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
