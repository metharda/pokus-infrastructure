"""Time synchronization state."""

from __future__ import annotations

import re
from dataclasses import dataclass

from pinka_preflight.runner import Runner

# TODO(verify): output formats of `timedatectl timesync-status` ("Offset: +1.2ms")
# and `chronyc -n tracking` ("System time : 0.000012 seconds fast of NTP time")
# against current systemd and chrony documentation.
_TIMESYNC_OFFSET = re.compile(
    r"^\s*Offset:\s*([+-]?[\d.]+)\s*(ns|us|µs|ms|s|min)\s*$", re.MULTILINE
)
_CHRONY_OFFSET = re.compile(r"^System time\s*:\s*([\d.]+) seconds (fast|slow)", re.MULTILINE)
_UNIT = {"ns": 1e-9, "us": 1e-6, "µs": 1e-6, "ms": 1e-3, "s": 1.0, "min": 60.0}


@dataclass
class TimeSync:
    """Clock synchronization facts.

    Attributes:
        ntp_synchronized: ``NTPSynchronized`` from ``timedatectl show``.
        ntp_service: ``NTP`` (whether a sync service is enabled).
        time_usec: ``TimeUSec`` as printed.
        offset_s: Clock offset in seconds (positive = local clock ahead), if known.
        source: ``timesyncd``, ``chrony`` or None.
    """

    ntp_synchronized: bool | None
    ntp_service: bool | None
    time_usec: str | None
    offset_s: float | None
    source: str | None


def parse_key_values(text: str) -> dict[str, str]:
    """Parse ``KEY=value`` lines (``timedatectl show`` output).

    Args:
        text: Command output.

    Returns:
        Mapping of keys to values.
    """
    result: dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip()
    return result


def _yes(value: str | None) -> bool | None:
    if value is None:
        return None
    return value.lower() in ("yes", "true", "1")


def detect_timesync(runner: Runner) -> TimeSync:
    """Read clock synchronization state.

    Args:
        runner: Command runner.

    Returns:
        Time synchronization facts.
    """
    show = runner.run(["timedatectl", "show"])
    values = parse_key_values(show.stdout) if show.ok else {}
    offset: float | None = None
    source: str | None = None
    status = runner.run(["timedatectl", "timesync-status"])
    m = _TIMESYNC_OFFSET.search(status.stdout) if status.ok else None
    if m:
        offset, source = float(m.group(1)) * _UNIT[m.group(2)], "timesyncd"
    else:
        tracking = runner.run(["chronyc", "-n", "tracking"])
        c = _CHRONY_OFFSET.search(tracking.stdout) if tracking.ok else None
        if c:
            sign = 1.0 if c.group(2) == "fast" else -1.0
            offset, source = sign * float(c.group(1)), "chrony"
    return TimeSync(
        ntp_synchronized=_yes(values.get("NTPSynchronized")),
        ntp_service=_yes(values.get("NTP")),
        time_usec=values.get("TimeUSec"),
        offset_s=offset,
        source=source,
    )
