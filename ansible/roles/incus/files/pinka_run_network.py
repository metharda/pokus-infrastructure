#!/opt/pinka/ansible-venv/bin/python3 -I
# Managed by pinka (ansible/roles/incus/files/pinka_run_network.py). Local changes will be overwritten.
"""The run-network helper (BUILD_PROMPT §7.7, §12.6).

Installed as ``/usr/local/libexec/pinka/run-network`` and run as root by
``pinka-run-network@.service``, one instance per connection on
``/run/pinka/run-network.sock`` (``pinka-run-network.socket``, ``Accept=yes``,
0600, owned by the active platform's controller account). Run networks live
in Incus's ``default`` project, where a certificate restricted to ``bench``
can create a network but neither change nor delete it, so the controller
creates and deletes them only through this helper.

It accepts exactly two requests, one line each: ``create r-<short>`` and
``delete r-<short>`` (``<short>``: 12 lowercase letters and digits). The peer
must be the controller account named in ``/etc/pinka/platform/rendered.json``
or root (``SO_PEERCRED``). ``create`` makes a bridge with fixed settings (no
NAT, no IPv6, no uplink, the ACL ``pinka-run-default``) and nothing else;
``delete`` removes a run network. Any other name, request or setting is
refused. The answer is one JSON line: ``{"ok": true, "network": ...,
"changed": bool}`` or ``{"ok": false, "error": ...}``. Every operation (time,
request, network, peer, outcome) is appended to
``/var/lib/pinka/run-networks/operations.jsonl`` and printed to the journal.

``unknown`` (root only) lists the Incus networks that neither infra created
nor this helper's log records, whatever their names; doctor and the
platform's integrity check flag them.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import http.client
import json
import os
import pwd
import re
import socket
import struct
import sys
from collections.abc import Callable, Iterable, Mapping
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import quote

NAME_RE = re.compile(r"^r-[a-z0-9]{12}$")
REQUEST_RE = re.compile(r"^(?P<request>create|delete) (?P<network>\S+)$")
MAX_REQUEST = 256
ACL = "pinka-run-default"
DESCRIPTION = "pinka run network (created by pinka-run-network)"
# The fixed settings of every run network: Incus's bridge driver, IPv4 for
# the instances' DHCP and DNS, no NAT, no IPv6, the run ACL. No uplink
# (bridge.external_interfaces) is ever set.
NETWORK_TYPE = "bridge"
NETWORK_CONFIG = {
    "ipv4.address": "auto",
    "ipv4.nat": "false",
    "ipv6.address": "none",
    "security.acls": ACL,
}
# Query: (method, path, body) -> metadata; raises IncusError (status 404: missing).
Query = Callable[[str, str, Any], Any]


class HelperError(Exception):
    """A request the helper refuses or cannot carry out."""


class IncusError(Exception):
    """The Incus API answered with an error.

    Attributes:
        status: The HTTP status (404: the object does not exist).
    """

    def __init__(self, message: str, status: int) -> None:
        """Create the error.

        Args:
            message: Incus's error text.
            status: The HTTP status.
        """
        super().__init__(message)
        self.status = status


@dataclasses.dataclass(frozen=True)
class Settings:
    """Paths (tests point them at a temporary tree).

    Attributes:
        log: The operation log.
        rendered: What infra rendered for the active platform.
        incus_socket: The Incus API socket.
    """

    log: Path = Path("/var/lib/pinka/run-networks/operations.jsonl")
    rendered: Path = Path("/etc/pinka/platform/rendered.json")
    incus_socket: Path = Path("/var/lib/incus/unix.socket")


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float) -> None:
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._path)
        self.sock = sock


def incus_query(settings: Settings, timeout: float = 60.0) -> Query:
    """A client of the local Incus API.

    Args:
        settings: Paths.
        timeout: Seconds per request.

    Returns:
        The query function; it waits for background operations.
    """

    def query(method: str, path: str, body: Any) -> Any:  # noqa: ANN401 - JSON
        connection = _UnixConnection(str(settings.incus_socket), timeout)
        try:
            data = None if body is None else json.dumps(body).encode()
            headers = {"Content-Type": "application/json"} if data else {}
            connection.request(method, path, body=data, headers=headers)
            response = connection.getresponse()
            payload = json.loads(response.read() or b"{}")
        finally:
            connection.close()
        if payload.get("type") == "error" or response.status >= 400:  # noqa: PLR2004
            status = int(payload.get("error_code") or response.status)
            raise IncusError(str(payload.get("error") or response.reason), status)
        if payload.get("type") == "async":
            operation = payload.get("operation", "")
            return query("GET", f"{operation}/wait?timeout={int(timeout)}", None)
        return payload.get("metadata")

    return query


def controller_uid(settings: Settings) -> int | None:
    """The UID of the active platform's controller account.

    Args:
        settings: Paths.

    Returns:
        The UID, or None without an active platform.
    """
    try:
        rendered = json.loads(settings.rendered.read_text(encoding="utf-8"))
        return pwd.getpwnam(str(rendered["controller"])).pw_uid
    except (OSError, ValueError, KeyError, TypeError):
        return None


def peer_uid(fd: int = 0) -> int:
    """The UID of the process on the other end of a Unix socket.

    Args:
        fd: Socket file descriptor (stdin under ``Accept=yes``).

    Returns:
        The peer's UID.
    """
    with socket.socket(fileno=os.dup(fd)) as sock:
        creds = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    _, uid, _ = struct.unpack("3i", creds)
    return int(uid)


def parse(line: str) -> tuple[str, str]:
    """Parse one request line.

    Args:
        line: ``create r-<short>`` or ``delete r-<short>``.

    Returns:
        ``(request, network)``.

    Raises:
        HelperError: Any other request or name.
    """
    match = REQUEST_RE.match(line.rstrip("\n"))
    if not match:
        raise HelperError("the request must be 'create r-<short>' or 'delete r-<short>'")
    network = match.group("network")
    if not NAME_RE.match(network):
        raise HelperError(
            "a run network is named r-<short>, <short> 12 lowercase letters and digits"
        )
    return match.group("request"), network


def _get(query: Query, network: str) -> dict[str, Any] | None:
    try:
        found = query("GET", f"/1.0/networks/{quote(network)}", None)
    except IncusError as exc:
        if exc.status == 404:  # noqa: PLR2004
            return None
        raise
    return dict(found or {})


def _fixed(found: Mapping[str, Any]) -> bool:
    config = found.get("config") or {}
    return (
        found.get("type") == NETWORK_TYPE
        and all(str(config.get(k)) == v for k, v in NETWORK_CONFIG.items())
        and not config.get("bridge.external_interfaces")
    )


def create(query: Query, network: str) -> bool:
    """Create a run network with the fixed settings.

    Args:
        query: The Incus API.
        network: ``r-<short>``.

    Returns:
        True if it was created; False if it already exists with them.

    Raises:
        HelperError: It exists with other settings.
    """
    found = _get(query, network)
    if found is not None:
        if _fixed(found):
            return False
        raise HelperError(f"{network} exists with other settings")
    body = {
        "name": network,
        "type": NETWORK_TYPE,
        "description": DESCRIPTION,
        "config": dict(NETWORK_CONFIG),
    }
    query("POST", "/1.0/networks", body)
    return True


def delete(query: Query, network: str) -> bool:
    """Delete a run network.

    Args:
        query: The Incus API.
        network: ``r-<short>``.

    Returns:
        True if it was deleted; False if it did not exist.
    """
    if _get(query, network) is None:
        return False
    query("DELETE", f"/1.0/networks/{quote(network)}", None)
    return True


def record(settings: Settings, entry: Mapping[str, Any], journal: TextIO) -> None:
    """Append one operation to the log and print it to the journal.

    Args:
        settings: Paths.
        entry: The operation.
        journal: Where the journal line goes (stderr under systemd).
    """
    line = json.dumps(dict(entry), sort_keys=True)
    settings.log.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(settings.log, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    journal.write(f"pinka-run-network: {line}\n")
    journal.flush()


def _peer_name(uid: int) -> str:
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


def serve(
    settings: Settings,
    stdin: TextIO,
    stdout: TextIO,
    *,
    uid: int,
    allowed_uid: int | None,
    query: Query,
    journal: TextIO,
    now: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
) -> int:
    """Answer one request.

    Args:
        settings: Paths.
        stdin: The connection, readable.
        stdout: The connection, writable.
        uid: The peer's UID.
        allowed_uid: The controller's UID (None: no active platform).
        query: The Incus API.
        journal: The journal stream.
        now: Clock.

    Returns:
        0 on success, 1 otherwise.
    """
    entry: dict[str, Any] = {
        "at": now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "peer_uid": uid,
        "peer": _peer_name(uid),
        "request": None,
        "network": None,
    }
    try:
        if uid not in {0, allowed_uid}:
            raise HelperError("only the active platform's controller and root may use this socket")
        line = stdin.readline(MAX_REQUEST + 1)
        if len(line) > MAX_REQUEST:
            raise HelperError("the request is too long")
        entry["request"], entry["network"] = request, network = parse(line)
        if request == "create":
            changed = create(query, network)
            entry["outcome"] = "created" if changed else "exists"
        else:
            changed = delete(query, network)
            entry["outcome"] = "deleted" if changed else "absent"
    except (HelperError, IncusError, OSError) as exc:
        entry["outcome"] = "refused" if isinstance(exc, HelperError) else "failed"
        entry["error"] = str(exc)
        record(settings, entry, journal)
        stdout.write(json.dumps({"ok": False, "error": str(exc)}) + "\n")
        stdout.flush()
        return 1
    record(settings, entry, journal)
    stdout.write(json.dumps({"ok": True, "network": network, "changed": changed}) + "\n")
    stdout.flush()
    return 0


def recorded_networks(log: Iterable[str]) -> set[str]:
    """The networks the helper's log records as created.

    Args:
        log: Lines of the operation log.

    Returns:
        Network names with a ``created`` or ``exists`` outcome.
    """
    found: set[str] = set()
    for line in log:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("outcome") in {"created", "exists"}:
            found.add(str(entry.get("network")))
    return found


def unknown(
    networks: Iterable[Mapping[str, Any]], infra: Iterable[str], log: Iterable[str]
) -> list[str]:
    """The managed Incus networks that neither infra created nor the log records.

    Args:
        networks: The networks of the ``default`` project (``/1.0/networks?recursion=1``).
        infra: Names or shell patterns of the networks infra creates or allows
            (the researchers' personal bridges).
        log: Lines of the operation log.

    Returns:
        Sorted names; whatever the name, a network outside both lists is unknown.
    """
    patterns = list(infra)
    recorded = recorded_networks(log)
    return sorted(
        str(n["name"])
        for n in networks
        if n.get("managed", True)
        and str(n["name"]) not in recorded
        and not any(fnmatchcase(str(n["name"]), p) for p in patterns)
    )


def main(argv: list[str]) -> int:
    """Run a subcommand.

    Args:
        argv: Arguments without the program name.

    Returns:
        Exit status.
    """
    parser = argparse.ArgumentParser(prog="run-network", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve", help="answer one request on stdin/stdout (socket activation)")
    unknown_cmd = sub.add_parser(
        "unknown", help="list networks neither infra nor the helper created"
    )
    unknown_cmd.add_argument(
        "--infra", action="append", default=[], help="a network (or pattern) infra creates"
    )
    args = parser.parse_args(argv)
    settings = Settings()
    query = incus_query(settings)
    if args.command == "serve":
        return serve(
            settings,
            sys.stdin,
            sys.stdout,
            uid=peer_uid(0),
            allowed_uid=controller_uid(settings),
            query=query,
            journal=sys.stderr,
        )
    try:
        log = settings.log.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        log = []
    networks = query("GET", "/1.0/networks?recursion=1", None) or []
    print(json.dumps({"unknown": unknown(networks, args.infra, log)}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
