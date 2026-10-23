"""The reference platform's isolation probe (BUILD_PROMPT §7.18).

It tries every action a platform account must not be able to do and reports
what it could do; it never relies on being stopped, it only measures. It
lists the restricted area and content, infra's secrets, the Git mirrors and
the datasets area; connects to the database, arbiter, run-network and query
sockets, to the telemetry stores and the inference API, to the platform's
sockets and to an address outside the host; and writes to the platform's
state and to its own environment. The agent runs it as itself
(``untrusted-agent``: every action must fail); infra's distribution tests run
it as a transient unit with each loopback-capable profile's hardening and
groups and compare the report with what that profile may reach. Standard
library only; the targets come from the files infra gives every platform
(``endpoints.toml``, ``principals.json``) and from infra's fixed paths.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import tomllib
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ENDPOINTS = Path("/etc/pinka/platform/endpoints.toml")
PRINCIPALS = Path("/etc/pinka/platform/principals.json")
TIMEOUT = 3.0
# Infra's paths no platform account may read (BUILD_PROMPT §7.18, §12.4).
INFRA_SECRETS = "/etc/pinka/secrets"
GIT_MIRRORS = "/var/lib/pinka/git"
DATASETS = "/srv/pinka/training/datasets"
RESTRICTED = "/srv/pinka/restricted"
RUN_NETWORK_SOCKET = "/run/pinka/run-network.sock"
# The routing table: the default gateway is the address outside the host the
# probe tries (its DNS port; an Incus bridge's dnsmasq answers there).
ROUTES = Path("/proc/net/route")
OUTSIDE_PORT = 53
# The probe's own Python environment: writing there must fail as well.
OWN_ENVIRONMENT = sys.prefix
PLATFORM = "refplat"


def default_endpoints(platform: str = PLATFORM) -> dict[str, Any]:
    """What refplat knows of the contract when ``endpoints.toml`` is out of sight.

    The agent sees none of /etc/pinka: contract v2's default ports and paths
    (§12.3, §12.4) stand in for the file.

    Args:
        platform: The platform's deployed name (the session's hello says it).

    Returns:
        The endpoints the probe needs.
    """
    return {
        "platform": platform,
        "postgres": {
            "socket_dir": "/run/pinka/postgres",
            "reader_socket_dir": "/run/pinka/postgres-reader",
        },
        "telemetry": {"query_socket": "/run/pinka/query.sock"},
        "gpu": {"arbiter_socket": "/run/pinka/gpu.sock"},
        "ports": {"prometheus": 18701, "loki": 18702, "tempo": 18703, "vllm": 18704},
    }


def default_principals(platform: str = PLATFORM) -> dict[str, Any]:
    """What refplat knows of its own services when ``principals.json`` is out of sight.

    Args:
        platform: The platform's deployed name.

    Returns:
        The controller's ``api`` socket (not the agent's) and its state.
    """
    state = f"/srv/pinka/platforms/{platform}/state/ctl"
    return {
        "platform": platform,
        "sockets": [
            {"service": "ctl", "name": "api", "path": "/run/pinka/platform/ctl/api.sock"},
        ],
        "services": [
            {
                "name": "ctl",
                "state": {"records": f"{state}/records", "datasets": f"{state}/datasets"},
            }
        ],
    }


@dataclass(frozen=True)
class Target:
    """One action the probe tries.

    Attributes:
        name: Stable name, used by the comparisons.
        kind: ``tcp``, ``unix``, ``http-unix``, ``list``, ``read`` or ``write``.
        address: ``host:port``, a socket path or a file system path.
    """

    name: str
    kind: str
    address: str


@dataclass(frozen=True)
class Outcome:
    """What one action did.

    Attributes:
        name: The target's name.
        kind: Its kind.
        address: Its address.
        reached: Whether the action succeeded.
        detail: The error, or what answered.
    """

    name: str
    kind: str
    address: str
    reached: bool
    detail: str


def default_gateway(routes: Path = ROUTES) -> str:
    """The IPv4 default gateway, or ``""`` when there is none.

    Args:
        routes: ``/proc/net/route``.

    Returns:
        The address.
    """
    try:
        lines = routes.read_text(encoding="ascii").splitlines()[1:]
    except OSError:
        return ""
    for line in lines:
        fields = line.split()
        if len(fields) > 2 and fields[1] == "00000000" and fields[2] != "00000000":
            return socket.inet_ntoa(int(fields[2], 16).to_bytes(4, "little"))
    return ""


def targets(endpoints: Mapping[str, Any], principals: Mapping[str, Any]) -> list[Target]:
    """Every action, from the files infra gives the platform.

    Args:
        endpoints: ``endpoints.toml``.
        principals: ``principals.json``.

    Returns:
        The targets, in a fixed order.
    """
    platform = str(endpoints.get("platform") or principals.get("platform") or "")
    ports = endpoints.get("ports", {})
    postgres = endpoints.get("postgres", {})
    telemetry = endpoints.get("telemetry", {})
    gpu = endpoints.get("gpu", {})
    found = [
        Target("restricted area", "list", RESTRICTED),
        Target("restricted content", "list", f"{RESTRICTED}/{platform}"),
        Target("database socket", "unix", f"{postgres.get('socket_dir', '')}/.s.PGSQL.5432"),
        Target(
            "database reader socket",
            "unix",
            f"{postgres.get('reader_socket_dir', '')}/.s.PGSQL.5432",
        ),
        Target("infra secrets", "list", INFRA_SECRETS),
        Target("git mirrors", "list", GIT_MIRRORS),
        Target("datasets area", "list", DATASETS),
        Target("gpu arbiter socket", "unix", str(gpu.get("arbiter_socket", ""))),
        Target("run-network helper socket", "unix", RUN_NETWORK_SOCKET),
        Target("query proxy", "http-unix", str(telemetry.get("query_socket", ""))),
    ]
    for store in ("prometheus", "loki", "tempo"):
        if store in ports:
            found.append(Target(f"{store} direct", "tcp", f"127.0.0.1:{ports[store]}"))
    if "vllm" in ports:
        found.append(Target("inference api", "tcp", f"127.0.0.1:{ports['vllm']}"))
    gateway = default_gateway()
    found.append(Target("outside tcp", "tcp", f"{gateway}:{OUTSIDE_PORT}" if gateway else ""))
    for item in principals.get("sockets", []):
        found.append(
            Target(
                f"socket {item.get('service')}/{item.get('name')}", "unix", str(item.get("path"))
            )
        )
    for item in principals.get("services", []):
        for state, path in sorted((item.get("state") or {}).items()):
            found.append(Target(f"write state {item.get('name')}/{state}", "write", str(path)))
    found.append(Target("write own environment", "write", OWN_ENVIRONMENT))
    return found


def _tcp(address: str) -> tuple[bool, str]:
    host, _, port = address.rpartition(":")
    with socket.create_connection((host, int(port)), timeout=TIMEOUT):
        return True, "connected"


def _unix(path: str) -> tuple[bool, str]:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(TIMEOUT)
        sock.connect(path)
        return True, "connected"


def _http_unix(path: str) -> tuple[bool, str]:
    """An HTTP request over a socket: reached when the answer is not a refusal."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(TIMEOUT)
        sock.connect(path)
        sock.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        status = sock.recv(64).split(b"\r\n", 1)[0].decode("ascii", "replace")
    parts = status.split()
    code = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    return code not in (0, 401, 403), status or "no answer"


def _list(path: str) -> tuple[bool, str]:
    names = list(Path(path).iterdir())
    return True, f"{len(names)} entries"


def _read(path: str) -> tuple[bool, str]:
    with Path(path).open("rb") as handle:
        handle.read(1)
    return True, "read"


def _write(path: str) -> tuple[bool, str]:
    probe = Path(path) / f".probe-{uuid.uuid4().hex}"
    probe.write_bytes(b"")
    probe.unlink()
    return True, "written"


ACTIONS: Mapping[str, Callable[[str], tuple[bool, str]]] = {
    "tcp": _tcp,
    "unix": _unix,
    "http-unix": _http_unix,
    "list": _list,
    "read": _read,
    "write": _write,
}


def attempt(target: Target) -> Outcome:
    """Try one action.

    Args:
        target: The action.

    Returns:
        What it did; any failure counts as not reached.
    """
    if not target.address or target.address.startswith(("/.s.PGSQL", ":")):
        return Outcome(target.name, target.kind, target.address, False, "not configured")
    try:
        reached, detail = ACTIONS[target.kind](target.address)
    except (OSError, ValueError) as exc:
        return Outcome(target.name, target.kind, target.address, False, type(exc).__name__)
    return Outcome(target.name, target.kind, target.address, reached, detail)


def probe(
    endpoints: Mapping[str, Any],
    principals: Mapping[str, Any],
    *,
    own_state: Sequence[str] = (),
) -> list[Outcome]:
    """Try every action.

    Args:
        endpoints: ``endpoints.toml``.
        principals: ``principals.json``.
        own_state: State directories of the account running the probe (writing
            there is allowed and not tried).

    Returns:
        The outcomes.
    """
    return [
        attempt(t)
        for t in targets(endpoints, principals)
        if not (t.kind == "write" and t.address in own_state)
    ]


def load(
    endpoints_path: Path = ENDPOINTS,
    principals_path: Path = PRINCIPALS,
    platform: str = PLATFORM,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    """Infra's files, or refplat's defaults where they cannot be read.

    Args:
        endpoints_path: ``endpoints.toml``.
        principals_path: ``principals.json``.
        platform: The deployed name, for the defaults.

    Returns:
        ``(endpoints, principals)``.
    """
    try:
        endpoints: Mapping[str, Any] = tomllib.loads(endpoints_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        endpoints = default_endpoints(platform)
    try:
        principals: Mapping[str, Any] = json.loads(principals_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        principals = default_principals(platform)
    return endpoints, principals


def report(outcomes: Sequence[Outcome], profile: str) -> dict[str, Any]:
    """The probe's JSON report.

    Args:
        outcomes: What the probe did.
        profile: The profile it ran with.

    Returns:
        ``profile``, ``uid``, ``reached`` (names) and every outcome.
    """
    return {
        "profile": profile,
        "uid": os.getuid(),
        "reached": [o.name for o in outcomes if o.reached],
        "outcomes": [asdict(o) for o in outcomes],
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Run the probe and print its report.

    Args:
        argv: Arguments without the program name.

    Returns:
        0 when nothing was reached that ``--allow`` does not name, 1 otherwise.
    """
    parser = argparse.ArgumentParser(prog="refplat-probe", description=__doc__.splitlines()[0])
    parser.add_argument("--profile", default="untrusted-agent")
    parser.add_argument("--platform", default=PLATFORM, help="the name the defaults use")
    parser.add_argument("--endpoints", type=Path, default=ENDPOINTS)
    parser.add_argument("--principals", type=Path, default=PRINCIPALS)
    parser.add_argument("--allow", action="append", default=[], help="a target it may reach")
    parser.add_argument("--own-state", action="append", default=[])
    args = parser.parse_args(argv)
    endpoints, principals = load(args.endpoints, args.principals, args.platform)
    outcomes = probe(endpoints, principals, own_state=args.own_state)
    document = report(outcomes, args.profile)
    sys.stdout.write(json.dumps(document, sort_keys=True) + "\n")
    return 1 if set(document["reached"]) - set(args.allow) else 0


if __name__ == "__main__":
    sys.exit(main())
