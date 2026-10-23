"""Resume state (BUILD_PROMPT §18.1, §18.3).

``/var/lib/pinka/installer/state.json`` (0600 root) records where the
installer is: the screen, the answers so far (never secrets), the completed
install phases and timestamps. It is saved before every screen change and
before the WSL-restart and reboot instructions, so a terminated WSL
distribution, a reboot, a dropped SSH session or a crash resumes at the right
step when bootstrap starts the installer again.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

STATE_VERSION = 1


def utc_now() -> str:
    """The current time, UTC ISO 8601 with ``Z``.

    Returns:
        Such as ``2026-10-06T21:00:00Z``.
    """
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class State:
    """Where an installation stands.

    Attributes:
        screen: Name of the current screen.
        answers: The wizard's choices so far: the answers and the consents (no secrets).
        phases_done: Install phases that finished (``preflight``, ``ansible``, ...).
        signal: The bootstrap signal that interrupted the install (75, 76, 77), if any.
        started_at: When the installation started.
        updated_at: Last save.
        schema_version: Format of this file.
    """

    screen: str = "welcome"
    answers: dict[str, Any] = field(default_factory=dict)
    phases_done: list[str] = field(default_factory=list)
    signal: int | None = None
    started_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    schema_version: int = STATE_VERSION


def load(path: Path) -> State | None:
    """Read the resume state.

    Args:
        path: ``state.json``.

    Returns:
        The state, or None if there is none or it cannot be used.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema_version") != STATE_VERSION:
        return None
    known = {k: v for k, v in data.items() if k in State.__dataclass_fields__}
    try:
        return State(**known)
    except TypeError:
        return None


def save(state: State, path: Path) -> None:
    """Write the resume state atomically (0600, root-only directory).

    Args:
        state: The state.
        path: ``state.json``.
    """
    state.updated_at = utc_now()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(asdict(state), handle, indent=2, sort_keys=True)
        handle.write("\n")
    tmp.chmod(0o600)
    tmp.replace(path)


def clear(path: Path) -> None:
    """Forget the resume state (after a finished install or "Start over").

    Args:
        path: ``state.json``.
    """
    path.unlink(missing_ok=True)
