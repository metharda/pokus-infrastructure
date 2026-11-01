"""Maintenance windows.

A window lets a staging lab use the production lab's hardware for a fixed
time. Each side keeps its own record, ``/etc/pinka/maintenance-window.json``
(root:root 0644), with only ``start``, ``end`` and ``created_at`` (UTC); the
admin opens the window on both. Python 3.9 compatible, standard library only.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from typing import Any

RECORD = "/etc/pinka/maintenance-window.json"
TIME_FORMATS = ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%MZ")
STAMP = "%Y-%m-%dT%H:%M:%SZ"


class WindowError(Exception):
    """A window cannot be opened, or its record is broken."""


def parse_time(text: str) -> dt.datetime:
    """Parse a UTC time such as ``2026-10-12T18:00Z`` or ``2026-10-12T18:00:00Z``.

    Args:
        text: The time.

    Returns:
        An aware UTC datetime.

    Raises:
        WindowError: For any other form.
    """
    for form in TIME_FORMATS:
        try:
            return dt.datetime.strptime(text, form).replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
    raise WindowError(f"{text!r} is not a UTC time like 2026-10-12T18:00Z")


@dataclass(frozen=True)
class Window:
    """A maintenance window.

    Attributes:
        start: When it opens.
        end: When it ends.
        created_at: When it was recorded.
    """

    start: dt.datetime
    end: dt.datetime
    created_at: dt.datetime

    def is_open(self, now: dt.datetime) -> bool:
        """Whether ``now`` lies inside the window (start inclusive, end exclusive)."""
        return self.start <= now < self.end

    def as_dict(self) -> dict[str, str]:
        """The record's content."""
        return {
            "start": self.start.strftime(STAMP),
            "end": self.end.strftime(STAMP),
            "created_at": self.created_at.strftime(STAMP),
        }

    def describe(self) -> str:
        """``from <start> until <end>``."""
        return f"from {self.start.strftime(STAMP)} until {self.end.strftime(STAMP)}"


def new(start: str, end: str, now: dt.datetime) -> Window:
    """A window to record.

    Args:
        start: ``--start``.
        end: ``--end``.
        now: The current time.

    Returns:
        The window.

    Raises:
        WindowError: If the times are malformed, the end is not after the start
            or lies in the past.
    """
    opens, closes = parse_time(start), parse_time(end)
    if closes <= opens:
        raise WindowError("the window must end after it starts")
    if closes <= now:
        raise WindowError(f"the window ends in the past ({end})")
    return Window(opens, closes, now.replace(microsecond=0))


def from_text(text: str) -> Window:
    """Read a record.

    Args:
        text: The record's content.

    Returns:
        The window.

    Raises:
        WindowError: If the record is not a window.
    """
    try:
        data: Any = json.loads(text)
        return Window(
            parse_time(str(data["start"])),
            parse_time(str(data["end"])),
            parse_time(str(data["created_at"])),
        )
    except (ValueError, KeyError, TypeError, WindowError) as exc:
        raise WindowError(f"{RECORD} is not a maintenance window record: {exc}") from exc


def to_text(window: Window) -> str:
    """Serialize a record.

    Args:
        window: The window.

    Returns:
        JSON text.
    """
    return json.dumps(window.as_dict(), indent=2, sort_keys=True) + "\n"
