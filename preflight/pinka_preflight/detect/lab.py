"""Lab ports and the VM collector address (BUILD_PROMPT §2.2, §12.11).

* **Lab port in use.** A configured lab port (the defaults of
  ``lab-defaults.json`` with the ``ports:`` overrides bootstrap passes as
  ``--port``) on which a process outside the lab listens is ``needs_action``;
  the remediation names the process and the override. Listening sockets come
  from ``/proc/net/tcp`` and ``/proc/net/tcp6``, their processes from the
  socket links in ``/proc/<pid>/fd``. A process belongs to the lab when its
  cgroup is the lab services' slice or a ``pinka-`` unit, when it is a
  ``docker-proxy`` (it runs in ``docker.service``) forwarding to a container
  in the lab's own Docker address pool, or when it is the Docker or Incus
  daemon the lab manages. A listener whose process preflight cannot see (it
  does not run as root) is a warning.
* **VM collector address conflict.** When VM targets are possible (KVM is
  usable), ``vm_collector_address`` must not be assigned to or routed by
  another interface (``ip -j addr``, ``ip -j route``), nor fall inside the
  Docker address pools, an Incus network or the tailnet range
  ``100.64.0.0/10``; otherwise ``needs_action``. The address on the lab's own
  dummy interface is not a conflict.
"""

from __future__ import annotations

import ipaddress
import json
import os
from collections.abc import Iterable, Mapping
from typing import Any

from pinka_preflight.detect.conflicts import MANAGED_MARKER, Conflict
from pinka_preflight.fs import FileSystem
from pinka_preflight.runner import Runner

LAB_DEFAULTS_JSON = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "lab-defaults.json",
)
TCP_TABLES = ("/proc/net/tcp", "/proc/net/tcp6")
TCP_LISTEN = "0A"
SOCKET_LINK_PREFIX = "socket:["
LAB_SLICE = "pinka-services.slice"
LAB_UNIT_PREFIX = "pinka-"
MANAGED_INCUS = "/etc/pinka/managed-incus"
DOCKER_DAEMON_JSON = "/etc/docker/daemon.json"
IPV4_WORD_HEX = 8
IPV6_HEX = 32


def load_defaults(path: str = LAB_DEFAULTS_JSON) -> dict[str, Any]:
    """Load the lab defaults generated from group_vars.

    Args:
        path: ``lab-defaults.json``.

    Returns:
        ``ports``, ``vm_collector``, ``docker_address_pools`` and ``tailnet``.
    """
    with open(path, encoding="utf-8") as handle:
        data: dict[str, Any] = json.load(handle)
    return data


def _address(hex_address: str) -> str:
    """Decode an address of ``/proc/net/tcp*`` (host byte order per 32-bit word)."""
    raw = bytes.fromhex(hex_address)
    words = b"".join(raw[i : i + 4][::-1] for i in range(0, len(raw), 4))
    if len(hex_address) == IPV4_WORD_HEX:
        return str(ipaddress.IPv4Address(words))
    if len(hex_address) == IPV6_HEX:
        return str(ipaddress.IPv6Address(words))
    return hex_address


def parse_tcp_table(text: str) -> list[tuple[str, int, str]]:
    """Parse the listening sockets of ``/proc/net/tcp`` or ``/proc/net/tcp6``.

    Args:
        text: File content (a header line, then one socket per line).

    Returns:
        ``(address, port, inode)`` of every socket in state LISTEN.
    """
    found: list[tuple[str, int, str]] = []
    for line in text.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 10 or fields[3] != TCP_LISTEN or ":" not in fields[1]:  # noqa: PLR2004
            continue
        address, port = fields[1].rsplit(":", 1)
        try:
            found.append((_address(address), int(port, 16), fields[9]))
        except ValueError:
            continue
    return found


def _socket_owners(fs: FileSystem, inodes: Iterable[str]) -> dict[str, list[str]]:
    """Map socket inodes to the PIDs that hold them."""
    wanted = set(inodes)
    owners: dict[str, list[str]] = {}
    for link in fs.glob("/proc/[0-9]*/fd/*"):
        target = fs.readlink(link) or ""
        if target.startswith(SOCKET_LINK_PREFIX) and target.endswith("]"):
            inode = target[len(SOCKET_LINK_PREFIX) : -1]
            if inode in wanted:
                owners.setdefault(inode, []).append(link.split("/")[2])
    return owners


def _cgroup_units(fs: FileSystem, pid: str) -> list[str]:
    units: list[str] = []
    for line in (fs.read_text(f"/proc/{pid}/cgroup") or "").splitlines():
        path = line.split(":", 2)[-1]
        units.extend(part for part in path.split("/") if part)
    return units


def _container_ip(fs: FileSystem, pid: str) -> ipaddress.IPv4Address | None:
    """The ``-container-ip`` of a ``docker-proxy`` process, if it has one."""
    raw = fs.read_bytes(f"/proc/{pid}/cmdline") or b""
    args = [a.decode("utf-8", "replace") for a in raw.split(b"\0")]
    for flag, value in zip(args, args[1:]):
        if flag == "-container-ip":
            try:
                return ipaddress.IPv4Address(value)
            except ValueError:
                return None
    return None


def lab_process(
    fs: FileSystem, pid: str, docker_pools: Iterable[ipaddress.IPv4Network] = ()
) -> bool:
    """Tell whether a process belongs to the lab.

    Args:
        fs: Filesystem view.
        pid: Process ID.
        docker_pools: The lab's own Docker address pools: a ``docker-proxy``
            forwarding to a container in them serves a lab port, also on a
            Docker that existed before pinka.

    Returns:
        True for the lab's services and containers, and for the Docker and
        Incus daemons the lab manages.
    """
    units = _cgroup_units(fs, pid)
    if LAB_SLICE in units or any(u.startswith(LAB_UNIT_PREFIX) for u in units):
        return True
    if "docker.service" in units:
        ip = _container_ip(fs, pid)
        if ip is not None and any(ip in pool for pool in docker_pools):
            return True
        return MANAGED_MARKER in (fs.read_text(DOCKER_DAEMON_JSON) or "")
    if "incus.service" in units:
        return fs.exists(MANAGED_INCUS)
    return False


def _process_name(fs: FileSystem, pid: str) -> str:
    return (fs.read_first_line(f"/proc/{pid}/comm") or "unknown").strip() or "unknown"


def port_conflicts(
    fs: FileSystem,
    ports: Mapping[str, int],
    docker_pools: Iterable[ipaddress.IPv4Network] = (),
) -> list[Conflict]:
    """Report configured lab ports that a process outside the lab listens on.

    Args:
        fs: Filesystem view.
        ports: Effective lab ports by name.
        docker_pools: The lab's own Docker address pools (see ``lab_process``).

    Returns:
        One conflict per port in use (``needs_action``; ``warning`` when the
        process cannot be seen).
    """
    by_port: dict[int, list[str]] = {}
    for name, port in ports.items():
        by_port.setdefault(int(port), []).append(name)
    listeners = [
        entry
        for table in TCP_TABLES
        for entry in parse_tcp_table(fs.read_text(table) or "")
        if entry[1] in by_port
    ]
    if not listeners:
        return []
    owners = _socket_owners(fs, (inode for _, _, inode in listeners))
    conflicts: list[Conflict] = []
    reported: set[int] = set()
    for address, port, inode in listeners:
        if port in reported:
            continue
        names = ", ".join(sorted(by_port[port]))
        override = f"ports: {{{sorted(by_port[port])[0]}: <free port>}}"
        pids = owners.get(inode, [])
        if not pids:
            reported.add(port)
            conflicts.append(
                Conflict(
                    "lab_port_unknown_owner",
                    "warning",
                    f"Port {port} ({names}) is listened on at {address} by a process preflight "
                    "cannot see",
                    "Run preflight as root to see the process; if it is not the lab's, stop it "
                    f"or move the lab's port in host_vars ({override}).",
                )
            )
            continue
        outside = [pid for pid in pids if not lab_process(fs, pid, docker_pools)]
        if not outside:
            continue
        reported.add(port)
        who = ", ".join(f"{_process_name(fs, pid)} (PID {pid})" for pid in sorted(outside))
        conflicts.append(
            Conflict(
                "lab_port_in_use",
                "needs_action",
                f"Lab port {port} ({names}) is in use by {who} at {address}",
                f"Stop {who} or move the lab's port in host_vars ({override}), then re-run "
                "bootstrap.",
            )
        )
    return conflicts


def _json(runner: Runner, argv: list[str]) -> Any:  # noqa: ANN401 - JSON document
    res = runner.run(argv, timeout=20.0)
    if not res.ok:
        return None
    try:
        return json.loads(res.stdout)
    except ValueError:
        return None


def _networks(values: Iterable[Any]) -> list[ipaddress.IPv4Network]:
    found: list[ipaddress.IPv4Network] = []
    for value in values:
        try:
            network = ipaddress.ip_network(str(value), strict=False)
        except ValueError:
            continue
        if isinstance(network, ipaddress.IPv4Network):
            found.append(network)
    return found


def _docker_pools(fs: FileSystem, defaults: Mapping[str, Any]) -> list[ipaddress.IPv4Network]:
    pools = [p["base"] for p in defaults.get("docker_address_pools", [])]
    try:
        daemon = json.loads(fs.read_text(DOCKER_DAEMON_JSON) or "{}")
    except ValueError:
        daemon = {}
    if isinstance(daemon, dict):
        pools += [p.get("base") for p in daemon.get("default-address-pools") or [] if p]
        if daemon.get("bip"):
            pools.append(daemon["bip"])
    return _networks(pools)


def _host_findings(runner: Runner, ip: ipaddress.IPv4Address, own: str) -> list[str]:
    """Interfaces, routes and Incus networks that already hold ``ip``."""
    found: list[str] = []
    for link in _json(runner, ["ip", "-j", "addr"]) or []:
        name = str(link.get("ifname", ""))
        inet = [i for i in link.get("addr_info", []) if i.get("family") == "inet" and "local" in i]
        for info in inet if name != own else []:
            subnet = f"{info['local']}/{info.get('prefixlen', 32)}"
            if ip in ipaddress.ip_network(subnet, strict=False):
                found.append(f"interface {name} has {subnet}")
    for route in _json(runner, ["ip", "-j", "route"]) or []:
        dst, dev = str(route.get("dst", "")), str(route.get("dev", ""))
        if dst != "default" and dev != own and any(ip in n for n in _networks([dst])):
            found.append(f"route {dst} via {dev or 'a gateway'}")
    if runner.which("incus"):
        for net in _json(runner, ["incus", "network", "list", "--format", "json"]) or []:
            config = net.get("config") or {}
            for network in _networks([config.get("ipv4.address", "")]):
                if ip in network:
                    found.append(f"Incus network {net.get('name')} ({network})")
    return found


def vm_address_conflicts(
    fs: FileSystem, runner: Runner, address: str, defaults: Mapping[str, Any]
) -> list[Conflict]:
    """Report a ``vm_collector_address`` the host already uses.

    Args:
        fs: Filesystem view.
        runner: Command runner.
        address: The configured ``vm_collector_address``.
        defaults: Lab defaults (dummy interface, Docker pools, tailnet range).

    Returns:
        At most one ``needs_action`` conflict, listing every finding.
    """
    try:
        ip = ipaddress.IPv4Address(address)
    except ValueError:
        return [
            Conflict(
                "vm_collector_address_conflict",
                "needs_action",
                f"vm_collector_address {address} is not an IPv4 address",
                "Set vm_collector_address in host_vars to an unused IPv4 address.",
            )
        ]
    own = str(defaults["vm_collector"]["interface"])
    found = _host_findings(runner, ip, own)
    found += [f"Docker address pool {n}" for n in _docker_pools(fs, defaults) if ip in n]
    tailnet = ipaddress.ip_network(str(defaults["tailnet"]))
    if ip in tailnet:
        found.append(f"the tailnet range {tailnet}")
    if not found:
        return []
    return [
        Conflict(
            "vm_collector_address_conflict",
            "needs_action",
            f"vm_collector_address {address} is already in use: {'; '.join(found)}",
            "Choose another address in host_vars (vm_collector_address) that no interface, "
            "route, Docker address pool, Incus network or the tailnet uses, then re-run "
            "bootstrap.",
        )
    ]
