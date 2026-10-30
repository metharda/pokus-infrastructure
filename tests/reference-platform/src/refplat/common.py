"""What every part of refplat shares: infra's paths and the socket protocol.

The paths are the ones contract v2 gives every platform: the
endpoints and principals files, the state, config and secrets of a service,
the restricted content and the arbiter's socket. Paths that hold the
platform's name are built from the name ``endpoints.toml`` gives, so a copy
deployed under another name runs unchanged. The protocol of refplat's own
sockets is one JSON line per request and one per answer, like the arbiter's.
"""

from __future__ import annotations

import json
import os
import socket
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

PLATFORM_ETC = Path("/etc/pinka/platform")
ENDPOINTS = PLATFORM_ETC / "endpoints.toml"
PRINCIPALS = PLATFORM_ETC / "principals.json"
CONFIG = PLATFORM_ETC / "config" / "ctl.json"
DB_SECRETS = Path("/etc/pinka/secrets/postgres")
PLATFORMS = Path("/srv/pinka/platforms")
RESTRICTED = Path("/srv/pinka/restricted")
ADAPTERS = Path("/srv/pinka/models/adapters")
SOCKETS = Path("/run/pinka/platform/ctl")
API_SOCKET = SOCKETS / "api.sock"
SESSION_SOCKET = SOCKETS / "session.sock"
BROKER_SOCKET = Path("/run/pinka/platform/broker/infer.sock")
MAX_LINE = 1 << 20
TIMEOUT = 30.0


class RefplatError(RuntimeError):
    """A request failed; the message says why."""


def state_dir(platform: str) -> Path:
    """The controller's state (``/srv/pinka/platforms/<platform>/state/ctl``)."""
    return PLATFORMS / platform / "state" / "ctl"


def cases_dir(platform: str) -> Path:
    """The active version of the restricted content package ``cases``."""
    return RESTRICTED / platform / "content" / "cases" / "current"


def read_json(path: Path, default: Any = None) -> Any:
    """A JSON file, or ``default`` when it is missing.

    Args:
        path: The file.
        default: What a missing file reads as.

    Returns:
        The document.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def endpoints(path: Path = ENDPOINTS) -> dict[str, Any]:
    """``endpoints.toml``, the addresses and paths infra gives the platform."""
    return tomllib.loads(path.read_text(encoding="utf-8"))


def settings(path: Path = CONFIG) -> dict[str, Any]:
    """The controller's settings (``platform_settings.ctl``), ``{}`` by default."""
    value = read_json(path, {})
    return value if isinstance(value, dict) else {}


def listen_sockets(environ: Mapping[str, str] | None = None) -> dict[str, socket.socket]:
    """The sockets systemd hands over by socket activation, by name.

    Args:
        environ: The environment (default: this process's).

    Returns:
        ``{fd_name: socket}``; empty when nothing was handed over.
    """
    env = os.environ if environ is None else environ
    if env.get("LISTEN_PID") not in (None, str(os.getpid())):
        return {}
    count = int(env.get("LISTEN_FDS", "0") or 0)
    names = (env.get("LISTEN_FDNAMES") or "").split(":")
    found = {}
    for index in range(count):
        name = names[index] if index < len(names) and names[index] else f"fd{index}"
        found[name] = socket.socket(fileno=3 + index)
    return found


def read_line(conn: socket.socket) -> dict[str, Any]:
    """One JSON object line from a connection.

    Raises:
        RefplatError: On a missing, oversized or malformed line.
    """
    data = b""
    while not data.endswith(b"\n"):
        chunk = conn.recv(65536)
        if not chunk:
            break
        data += chunk
        if len(data) > MAX_LINE:
            raise RefplatError("request too long")
    try:
        value = json.loads(data)
    except ValueError:
        raise RefplatError("not one JSON line") from None
    if not isinstance(value, dict):
        raise RefplatError("not a JSON object")
    return value


def send_line(conn: socket.socket, value: Mapping[str, Any]) -> None:
    """Write one JSON object line."""
    conn.sendall((json.dumps(value, sort_keys=True) + "\n").encode("utf-8"))


def request(path: Path, value: Mapping[str, Any], timeout: float = TIMEOUT) -> dict[str, Any]:
    """Send one request over a Unix socket and read the answer.

    Args:
        path: The socket.
        value: The request.
        timeout: Seconds for connecting and answering.

    Returns:
        The answer.

    Raises:
        RefplatError: If the socket cannot be reached or answers nothing usable.
    """
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(timeout)
            conn.connect(str(path))
            send_line(conn, value)
            conn.shutdown(socket.SHUT_WR)
            return read_line(conn)
    except OSError as exc:
        raise RefplatError(f"{path}: {exc}") from exc


def hook_output(value: Mapping[str, Any]) -> None:
    """A hook's one JSON line on standard output (contract v2)."""
    print(json.dumps(value, sort_keys=True), flush=True)
