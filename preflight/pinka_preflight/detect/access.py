"""Pre-existing access (BUILD_PROMPT §2.2 "Pre-existing access", §7.5).

Preflight records the access to the host that existed before the install, so
the firewall keeps it as found (§7.5):

* **Listeners.** Every non-loopback TCP and UDP listener that is not the
  lab's (``/proc/net/tcp*`` in state LISTEN, ``/proc/net/udp*`` unconnected):
  protocol, address, port, process and its unit. A process is the lab's by
  :func:`pinka_preflight.detect.lab.lab_process`, or when it is the daemon of a
  component the install record names as pinka's (Tailscale, Docker, Incus).
  A socket systemd holds for socket activation (sshd on Ubuntu 24.04) is
  named by its socket unit and the service it activates
  (``systemctl list-sockets``). A listener is ``admitted`` when pinka's strict
  input policy accepts its traffic anyway on every interface: the DHCP and
  DHCPv6 clients (UDP 68 and 546) and Tailscale's WireGuard port.
* **Firewall manager.** ``firewalld``, ``ufw`` or ``none``.
* **Accounts.** The login accounts that are not pinka's: a UID in the login
  range of ``/etc/login.defs`` and a login shell, minus the accounts the
  install record names as pinka's.
* **Session.** The access path of the install: the server address and port
  of ``SSH_CONNECTION`` of the nearest process up the tree that has it (sudo
  drops it from its own environment), its login account, and whether the
  address is on the tailnet; or the console.

The host is **dedicated** when no listener exists besides sshd and the
admitted ones and every account is a configured researcher (bootstrap passes
their names); otherwise it is **shared**.

Standard library only and source-compatible with Python 3.9.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pinka_preflight.detect.lab import (
    TCP_LISTEN,
    TCP_TABLES,
    _address,
    _cgroup_units,
    _socket_owners,
    lab_process,
)
from pinka_preflight.fs import FileSystem
from pinka_preflight.runner import Runner

UDP_TABLES = ("/proc/net/udp", "/proc/net/udp6")
UDP_UNCONNECTED = "07"
PASSWD = "/etc/passwd"  # noqa: S105 - a path, not a password
LOGIN_DEFS = "/etc/login.defs"
UID_MIN = 1000
UID_MAX = 60000
NO_LOGIN_SHELLS = frozenset({"nologin", "false", "sync", "shutdown", "halt"})
SSHD_PROCESSES = frozenset({"sshd"})
SSHD_UNITS = frozenset({"ssh.service", "sshd.service", "ssh.socket", "sshd.socket"})
# pinka.nft.j2 admits the replies of the host's own DHCP and DHCPv6 clients on
# every interface (udp sport 67 dport 68, udp sport 547 dport 546).
DHCP_CLIENT_PORTS = (68, 546)
# Daemons of components pinka installs or joins, by component (§7.21).
COMPONENT_UNITS = {
    "tailscale": ("tailscaled.service",),
    "docker": ("docker.service",),
    "incus": ("incus.service", "incus.socket"),
}
TAILNET_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")
MAX_PARENTS = 64
SSH_CONNECTION_FIELDS = 4


@dataclass
class Listener:
    """One listening socket outside the lab.

    Attributes:
        proto: ``tcp`` or ``udp``.
        address: The local address (``0.0.0.0`` and ``::`` for every address).
        port: The local port.
        process: The process name, or None when preflight cannot see it.
        unit: Its systemd unit (the socket unit for socket activation).
        admitted: pinka's strict input policy accepts its traffic anyway.
    """

    proto: str
    address: str
    port: int
    process: str | None
    unit: str | None
    admitted: bool = False

    @property
    def sshd(self) -> bool:
        """Whether it is the SSH server."""
        return self.process in SSHD_PROCESSES or self.unit in SSHD_UNITS


@dataclass
class Access:
    """The access to the host that existed before the install.

    Attributes:
        host: ``dedicated`` or ``shared``.
        firewall_manager: ``firewalld``, ``ufw`` or ``none``.
        session: ``{"via": "ssh", "address", "port", "user", "tailnet"}`` or
            ``{"via": "console"}``.
        listeners: The listeners outside the lab.
        accounts: The login accounts that are not pinka's.
        reasons: Why the host is shared (empty for a dedicated host).
    """

    host: str = "dedicated"
    firewall_manager: str = "none"
    session: dict[str, Any] = field(default_factory=lambda: {"via": "console"})
    listeners: list[Listener] = field(default_factory=list)
    accounts: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """The access as it appears in the host profile."""
        return {
            "host": self.host,
            "firewall_manager": self.firewall_manager,
            "session": dict(self.session),
            "listeners": [
                {
                    "proto": item.proto,
                    "address": item.address,
                    "port": item.port,
                    "process": item.process,
                    "unit": item.unit,
                    "admitted": item.admitted,
                }
                for item in self.listeners
            ],
            "accounts": list(self.accounts),
            "reasons": list(self.reasons),
        }


def _loopback(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped.is_loopback
    return ip.is_loopback


def _tailnet(address: str, tailnet_v4: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv4Address):
        return ip in ipaddress.ip_network(tailnet_v4)
    return ip in TAILNET_V6


def parse_table(text: str, state: str) -> list[tuple[str, int, str]]:
    """The sockets of a ``/proc/net`` table in one state.

    Args:
        text: ``/proc/net/tcp``, ``tcp6``, ``udp`` or ``udp6``.
        state: The state field (``0A`` LISTEN for TCP, ``07`` for an
            unconnected UDP socket).

    Returns:
        ``(address, port, inode)`` of every such socket.
    """
    found: list[tuple[str, int, str]] = []
    for line in text.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 10 or fields[3] != state or ":" not in fields[1]:  # noqa: PLR2004
            continue
        address, port = fields[1].rsplit(":", 1)
        try:
            found.append((_address(address), int(port, 16), fields[9]))
        except ValueError:
            continue
    return found


def service_unit(units: Sequence[str]) -> str | None:
    """The unit a process runs in: the last service, socket or scope of its cgroup path.

    Args:
        units: The components of the process's cgroup path.

    Returns:
        The unit, or None.
    """
    services = [u for u in units if u.endswith((".service", ".socket", ".scope"))]
    return services[-1] if services else None


def socket_units(runner: Runner) -> dict[int, tuple[str, str]]:
    """The listening sockets systemd holds, by port.

    Args:
        runner: Command runner.

    Returns:
        ``port -> (socket unit, activated unit)`` from ``systemctl
        list-sockets`` (``[::]:22  ssh.socket  ssh.service``).
    """
    res = runner.run(["systemctl", "list-sockets", "--all", "--no-legend", "--full"])
    found: dict[int, tuple[str, str]] = {}
    for line in res.stdout.splitlines() if res.ok else []:
        fields = line.split()
        if len(fields) < 3 or ":" not in fields[0]:  # noqa: PLR2004
            continue
        port = fields[0].rsplit(":", 1)[1]
        if port.isdigit():
            found.setdefault(int(port), (fields[1], fields[2]))
    return found


def pinka_units(record: Mapping[str, Any] | None) -> set[str]:
    """The daemons of components the install record names as pinka's."""
    added = ((record or {}).get("added") or {}).get("components") or {}
    return {unit for name, units in COMPONENT_UNITS.items() if name in added for unit in units}


def detect_listeners(
    fs: FileSystem,
    runner: Runner,
    *,
    docker_pools: Iterable[ipaddress.IPv4Network] = (),
    record: Mapping[str, Any] | None = None,
    admitted_udp: Iterable[int] = (),
) -> list[Listener]:
    """The non-loopback listeners outside the lab.

    Args:
        fs: Filesystem view.
        runner: Command runner (``systemctl list-sockets``).
        docker_pools: The lab's own Docker address pools (see ``lab_process``).
        record: The install record, if any.
        admitted_udp: UDP ports pinka's strict policy accepts on every
            interface besides the DHCP clients' (Tailscale's port).

    Returns:
        The listeners, sorted by protocol, port and address.
    """
    sockets = [
        ("tcp", address, port, inode)
        for table in TCP_TABLES
        for address, port, inode in parse_table(fs.read_text(table) or "", TCP_LISTEN)
    ] + [
        ("udp", address, port, inode)
        for table in UDP_TABLES
        for address, port, inode in parse_table(fs.read_text(table) or "", UDP_UNCONNECTED)
    ]
    sockets = [s for s in sockets if not _loopback(s[1])]
    owners = _socket_owners(fs, (inode for _, _, _, inode in sockets))
    systemd_sockets = (
        socket_units(runner) if any("1" in owners.get(s[3], []) for s in sockets) else {}
    )
    ours = pinka_units(record)
    admitted = set(DHCP_CLIENT_PORTS) | set(admitted_udp)
    found: dict[tuple[str, str, int], Listener] = {}
    for proto, address, port, inode in sockets:
        pids = owners.get(inode, [])
        if pids and all(lab_process(fs, pid, docker_pools) for pid in pids):
            continue
        process = unit = None
        if "1" in pids and port in systemd_sockets:
            unit, activates = systemd_sockets[port]
            process = activates.rsplit(".", 1)[0]
            if activates in SSHD_UNITS:
                process = "sshd"
        elif pids:
            pid = sorted(pids, key=int)[0]
            process = (fs.read_first_line(f"/proc/{pid}/comm") or "").strip() or None
            unit = service_unit(_cgroup_units(fs, pid))
        if unit in ours:
            continue
        key = (proto, address, port)
        if key not in found:
            found[key] = Listener(
                proto, address, port, process, unit, proto == "udp" and port in admitted
            )
    return sorted(found.values(), key=lambda item: (item.proto, item.port, item.address))


def _login_range(fs: FileSystem) -> tuple[int, int]:
    low, high = UID_MIN, UID_MAX
    for line in (fs.read_text(LOGIN_DEFS) or "").splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[1].isdigit():  # noqa: PLR2004
            if fields[0] == "UID_MIN":
                low = int(fields[1])
            elif fields[0] == "UID_MAX":
                high = int(fields[1])
    return low, high


def _passwd(fs: FileSystem) -> list[list[str]]:
    return [
        line.split(":")
        for line in (fs.read_text(PASSWD) or "").splitlines()
        if line.count(":") >= 6  # noqa: PLR2004
    ]


def detect_accounts(fs: FileSystem, record: Mapping[str, Any] | None = None) -> list[str]:
    """The login accounts that are not pinka's.

    Args:
        fs: Filesystem view.
        record: The install record, if any (its ``added.accounts`` are pinka's).

    Returns:
        Account names, sorted.
    """
    low, high = _login_range(fs)
    added = ((record or {}).get("added") or {}).get("accounts") or []
    pinka = {str(a.get("name") if isinstance(a, Mapping) else a) for a in added}
    found = []
    for fields in _passwd(fs):
        name, uid, shell = fields[0], fields[2], fields[6].strip()
        shell_name = shell.rsplit("/", 1)[-1]
        if not uid.isdigit() or not low <= int(uid) <= high:
            continue
        if not shell or shell_name in NO_LOGIN_SHELLS or name in pinka:
            continue
        found.append(name)
    return sorted(found)


def _environ(fs: FileSystem, pid: str) -> dict[str, str]:
    found = {}
    for entry in (fs.read_bytes(f"/proc/{pid}/environ") or b"").split(b"\0"):
        key, sep, value = entry.decode("utf-8", "replace").partition("=")
        if sep:
            found[key] = value
    return found


def _status(fs: FileSystem, pid: str, key: str) -> str | None:
    for line in (fs.read_text(f"/proc/{pid}/status") or "").splitlines():
        name, _, value = line.partition(":")
        if name == key:
            return value.split()[0] if value.split() else None
    return None


def detect_session(fs: FileSystem, tailnet_v4: str) -> dict[str, Any]:
    """The access path of the current session.

    Args:
        fs: Filesystem view (``/proc/self`` and its parents).
        tailnet_v4: The tailnet's IPv4 range.

    Returns:
        ``{"via": "ssh", "address", "port", "user", "tailnet"}`` from the
        nearest ``SSH_CONNECTION`` up the process tree, else
        ``{"via": "console"}``.
    """
    pid = "self"
    users = {f[2]: f[0] for f in _passwd(fs)}
    for _ in range(MAX_PARENTS):
        value = _environ(fs, pid).get("SSH_CONNECTION", "").split()
        if len(value) == SSH_CONNECTION_FIELDS and value[3].isdigit():
            uid = _status(fs, pid, "Uid")
            return {
                "via": "ssh",
                "address": value[2],
                "port": int(value[3]),
                "user": users.get(uid or ""),
                "tailnet": _tailnet(value[2], tailnet_v4),
            }
        parent = _status(fs, pid, "PPid")
        if not parent or parent in ("0", pid):
            break
        pid = parent
    return {"via": "console"}


def classify(
    listeners: Sequence[Listener], accounts: Sequence[str], researchers: Iterable[str]
) -> tuple[str, list[str]]:
    """Dedicated or shared (§2.2).

    Args:
        listeners: The listeners outside the lab.
        accounts: The login accounts that are not pinka's.
        researchers: The configured researchers' names.

    Returns:
        ``(host, reasons)``: ``dedicated`` with no reasons, or ``shared`` with
        what makes it shared.
    """
    allowed = set(researchers)
    reasons = [
        f"{item.proto}/{item.port} on {item.address} ({item.process or 'unknown process'})"
        for item in listeners
        if not item.sshd and not item.admitted
    ]
    reasons += [
        f"login account {name} is not a researcher" for name in accounts if name not in allowed
    ]
    return ("shared" if reasons else "dedicated"), reasons


def detect_access(
    fs: FileSystem,
    runner: Runner,
    *,
    firewall_manager: str | None,
    researchers: Iterable[str],
    tailnet_v4: str,
    docker_pools: Iterable[ipaddress.IPv4Network] = (),
    record: Mapping[str, Any] | None = None,
    admitted_udp: Iterable[int] = (),
) -> Access:
    """Everything of the pre-existing access.

    Args:
        fs: Filesystem view.
        runner: Command runner.
        firewall_manager: The active firewall manager (conflicts detection).
        researchers: The configured researchers' names.
        tailnet_v4: The tailnet's IPv4 range.
        docker_pools: The lab's own Docker address pools.
        record: The install record, if any.
        admitted_udp: UDP ports the strict policy accepts besides DHCP.

    Returns:
        The access.
    """
    listeners = detect_listeners(
        fs, runner, docker_pools=docker_pools, record=record, admitted_udp=admitted_udp
    )
    accounts = detect_accounts(fs, record)
    host, reasons = classify(listeners, accounts, researchers)
    return Access(
        host=host,
        firewall_manager=firewall_manager or "none",
        session=detect_session(fs, tailnet_v4),
        listeners=listeners,
        accounts=accounts,
        reasons=reasons,
    )
