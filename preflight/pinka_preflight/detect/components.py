"""Components that may exist before pinka (BUILD_PROMPT §2.2, §7.4, §7.6, §7.7, §7.21).

Docker Engine, Tailscale, LXD, Podman, Incus and the NVIDIA driver are
detected with their version and state. The install record
(``/var/lib/pinka/install-record.json``, written by the ``install_record``
role) tells which of them pinka installed or joined: a component present
before pinka's first install is ``pre-existing``, and pinka uses it as it is
and never changes it. A role records a component before it installs or joins it,
so on a host with a record everything else is pre-existing. On a host
without a record (none yet, or a legacy record of a host pinka provisioned
before the record existed) a component counts as pinka's only when it
carries pinka's marks: Docker's ``daemon.json`` with the managed label, a
Tailscale node with a pinka tag, Incus with ``/etc/pinka/managed-incus``;
an NVIDIA driver is then pre-existing.

``component_conflicts`` turns what the lab cannot run with into
``needs_action`` conflicts: no override continues past them, and nothing on
the host changes before they are resolved.
"""

from __future__ import annotations

import ipaddress
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pinka_preflight.detect.conflicts import MANAGED_MARKER, Conflict
from pinka_preflight.fs import FileSystem
from pinka_preflight.runner import Runner

INSTALL_RECORD = "/var/lib/pinka/install-record.json"
DOCKER_DAEMON_JSON = "/etc/docker/daemon.json"
MANAGED_INCUS = "/etc/pinka/managed-incus"
DOCKER_SOCKETS = ("/var/run/docker.sock", "/run/docker.sock")
# Docker Engine enables CDI by default from 28.2 unless features.cdi is false
# (moby docker-v29.8.2, daemon/command/daemon.go: cdiEnabled).
CDI_DEFAULT_SINCE = (28, 2)
PRE_EXISTING = "pre-existing"
PINKA = "pinka"
VERSION_RE = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")
UDP_TABLES = ("/proc/net/udp", "/proc/net/udp6")
DNS_DHCP_PORTS = (53, 67)


@dataclass
class Component:
    """One component and where it came from.

    Attributes:
        installed: The component is on the host.
        version: Its version, if it could be read.
        running: Its daemon is active.
        origin: ``pinka``, ``pre-existing``, or None when not installed.
        details: Component-specific facts (see each detector).
    """

    installed: bool = False
    version: str | None = None
    running: bool = False
    origin: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """The component as it appears in the host profile."""
        return {
            "installed": self.installed,
            "version": self.version,
            "running": self.running,
            "origin": self.origin,
            **self.details,
        }


@dataclass
class Components:
    """Every component preflight knows.

    Attributes:
        record: The install record, or None when the host has none.
        docker: Docker Engine (``cdi``, ``compose``, ``buildx``).
        tailscale: Tailscale (``logged_in``, ``hostname``, ``tags``,
            ``key_expiry``, ``funnel``, ``ssh``).
        lxd: LXD (``networks``, ``storage_sources``).
        podman: Podman (``docker_compat``: Podman provides Docker's command
            or socket).
        incus: Incus.
        nvidia_driver: The NVIDIA kernel driver.
    """

    record: dict[str, Any] | None = None
    docker: Component = field(default_factory=Component)
    tailscale: Component = field(default_factory=Component)
    lxd: Component = field(default_factory=Component)
    podman: Component = field(default_factory=Component)
    incus: Component = field(default_factory=Component)
    nvidia_driver: Component = field(default_factory=Component)

    def as_dict(self) -> dict[str, Any]:
        """The ``components`` section of the host profile."""
        return {
            "install_record": self.record is not None,
            "docker": self.docker.as_dict(),
            "tailscale": self.tailscale.as_dict(),
            "lxd": self.lxd.as_dict(),
            "podman": self.podman.as_dict(),
            "incus": self.incus.as_dict(),
            "nvidia_driver": self.nvidia_driver.as_dict(),
        }


def read_record(fs: FileSystem) -> dict[str, Any] | None:
    """Read the install record.

    Args:
        fs: Filesystem view.

    Returns:
        The record, or None when it is missing or not a JSON object.
    """
    text = fs.read_text(INSTALL_RECORD)
    if text is None:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _recorded(record: Mapping[str, Any] | None, name: str) -> str | None:
    """The origin the install record gives a present component.

    A role records a component in ``added.components`` before it installs or
    joins it, so a component the record does not name there is not pinka's.
    A legacy record (pinka provisioned the host before the record existed)
    decides nothing: pinka's marks do.
    """
    if record is None or record.get("legacy"):
        return None
    added = (record.get("added") or {}).get("components") or {}
    return PINKA if added.get(name) else PRE_EXISTING


def _version(text: str) -> str | None:
    match = VERSION_RE.search(text)
    return match.group(0) if match else None


def _named(name: str, version: str | None) -> str:
    return f"{name} {version}" if version else name


def _version_tuple(version: str | None) -> tuple[int, int]:
    match = VERSION_RE.search(version or "")
    return (int(match.group(1)), int(match.group(2))) if match else (0, 0)


def _active(runner: Runner, unit: str) -> bool:
    return runner.run(["systemctl", "is-active", unit]).stdout.strip() == "active"


def _json(runner: Runner, argv: list[str]) -> Any:  # noqa: ANN401 - JSON document
    res = runner.run(argv, timeout=20.0)
    if not res.ok:
        return None
    try:
        return json.loads(res.stdout)
    except ValueError:
        return None


def _podman_docker(fs: FileSystem, runner: Runner) -> str | None:
    """Tell whether Podman provides Docker's command or socket."""
    if runner.which("docker"):
        res = runner.run(["docker", "--version"])
        if "podman" in (res.stdout + res.stderr).lower():
            return "the docker command is Podman's (podman-docker)"
    for sock in DOCKER_SOCKETS:
        target = fs.readlink(sock) or ""
        if "podman" in target:
            return f"{sock} links to Podman's socket ({target})"
    return None


def detect_docker(fs: FileSystem, runner: Runner, record: Mapping[str, Any] | None) -> Component:
    """Docker Engine: version, state, CDI, the Compose and buildx plugins, origin.

    CDI is read from ``docker info`` (``CDISpecDirs`` is empty when CDI is
    off); without a running daemon it follows ``daemon.json`` and the
    version: Docker 28.2 and later enable CDI unless ``features.cdi`` is
    false.
    """
    comp = Component()
    if not runner.which("dockerd"):
        return comp
    comp.installed = True
    comp.version = _version(runner.run(["dockerd", "--version"]).stdout)
    comp.running = _active(runner, "docker.service")
    daemon_text = fs.read_text(DOCKER_DAEMON_JSON)
    try:
        daemon = json.loads(daemon_text or "{}")
    except ValueError:
        daemon = {}
    feature = (daemon.get("features") or {}).get("cdi") if isinstance(daemon, dict) else None
    cdi: bool | None = None
    if comp.running:
        # An empty list or null: CDI is off. No answer: judged as if stopped.
        res = runner.run(["docker", "info", "--format", "{{json .CDISpecDirs}}"], timeout=20.0)
        if res.ok:
            try:
                cdi = bool(json.loads(res.stdout))
            except ValueError:
                cdi = None
    if cdi is None:
        cdi = feature is True or (
            feature is not False and _version_tuple(comp.version) >= CDI_DEFAULT_SINCE
        )
    comp.details = {
        "cdi": cdi,
        "compose": runner.run(["docker", "compose", "version"]).ok,
        "buildx": runner.run(["docker", "buildx", "version"]).ok,
    }
    origin = _recorded(record, "docker")
    if origin is None:
        origin = PINKA if MANAGED_MARKER in (daemon_text or "") else PRE_EXISTING
    comp.origin = origin
    return comp


def detect_tailscale(
    runner: Runner, record: Mapping[str, Any] | None, pinka_tags: Iterable[str]
) -> Component:
    """Tailscale: version, login, host name, tags, key expiry, Funnel, Tailscale SSH, origin.

    TODO(verify): with the pinned Tailscale, that ``tailscale serve status
    --json`` names Funnel in ``AllowFunnel`` (as the tailscale role reads it)
    and that ``tailscale debug prefs`` prints the preferences as JSON with
    ``RunSSH``.
    """
    comp = Component()
    if not runner.which("tailscale"):
        return comp
    comp.installed = True
    comp.version = _version(runner.run(["tailscale", "version"]).stdout)
    comp.running = _active(runner, "tailscaled.service")
    status = _json(runner, ["tailscale", "status", "--json"]) or {}
    me = status.get("Self") or {}
    logged_in = status.get("BackendState") == "Running"
    serve = _json(runner, ["tailscale", "serve", "status", "--json"]) or {}
    prefs = _json(runner, ["tailscale", "debug", "prefs"]) or {}
    tags = [str(t) for t in me.get("Tags") or []]
    comp.details = {
        "logged_in": logged_in,
        "hostname": me.get("HostName") if logged_in else None,
        "tags": tags if logged_in else [],
        "key_expiry": me.get("KeyExpiry") if logged_in else None,
        "funnel": bool(isinstance(serve, dict) and serve.get("AllowFunnel")),
        "ssh": bool(isinstance(prefs, dict) and prefs.get("RunSSH")),
    }
    origin = _recorded(record, "tailscale")
    if origin is None and logged_in:
        origin = PINKA if set(tags) & set(pinka_tags) else PRE_EXISTING
    comp.origin = origin if logged_in else None
    return comp


def detect_lxd(fs: FileSystem, runner: Runner) -> Component:
    """LXD: version, its managed networks with their IPv4 subnets, its storage sources.

    TODO(verify): the JSON of ``lxc network list --format json`` and ``lxc
    storage list --format json`` of the LXD releases in use (snap 5.21 and 6).
    """
    comp = Component()
    snap = runner.run(["snap", "list", "lxd"])
    if not (snap.ok or runner.which("lxd") or fs.exists("/sys/class/net/lxdbr0")):
        return comp
    comp.installed = True
    if snap.ok:
        lines = snap.stdout.splitlines()
        comp.version = lines[1].split()[1] if len(lines) > 1 and len(lines[1].split()) > 1 else None
    else:
        comp.version = _version(runner.run(["lxd", "--version"]).stdout)
    networks = _json(runner, ["lxc", "network", "list", "--format", "json"])
    comp.running = networks is not None
    found = []
    for net in networks or []:
        if not net.get("managed"):
            continue
        config = net.get("config") or {}
        found.append({"name": str(net.get("name", "")), "ipv4": config.get("ipv4.address")})
    sources = []
    for pool in _json(runner, ["lxc", "storage", "list", "--format", "json"]) or []:
        source = (pool.get("config") or {}).get("source")
        if source:
            sources.append(str(source))
    comp.details = {"networks": found, "storage_sources": sources}
    comp.origin = PRE_EXISTING
    return comp


def detect_podman(fs: FileSystem, runner: Runner) -> Component:
    """Podman: version, its socket, and whether it provides Docker's command or socket."""
    comp = Component()
    compat = _podman_docker(fs, runner)
    if not runner.which("podman") and compat is None:
        return comp
    comp.installed = True
    comp.version = _version(runner.run(["podman", "--version"]).stdout)
    comp.running = _active(runner, "podman.socket") or _active(runner, "podman.service")
    comp.details = {"docker_compat": compat}
    comp.origin = PRE_EXISTING
    return comp


def detect_incus(fs: FileSystem, runner: Runner, record: Mapping[str, Any] | None) -> Component:
    """Incus: version, state and origin (the managed marker or the install record)."""
    comp = Component()
    if not runner.which("incus"):
        return comp
    comp.installed = True
    comp.version = _version(runner.run(["incus", "--version"]).stdout)
    comp.running = _active(runner, "incus.service")
    origin = _recorded(record, "incus")
    if origin is None:
        origin = PINKA if fs.exists(MANAGED_INCUS) else PRE_EXISTING
    comp.origin = origin
    return comp


def detect_nvidia_driver(
    record: Mapping[str, Any] | None, *, installed: bool, version: str | None
) -> Component:
    """The NVIDIA driver, from the GPU detector's findings, with its origin."""
    comp = Component(installed=installed, version=version, running=installed)
    if installed:
        comp.origin = _recorded(record, "nvidia_driver") or PRE_EXISTING
    return comp


def detect_components(
    fs: FileSystem,
    runner: Runner,
    *,
    pinka_tags: Iterable[str],
    nvidia_installed: bool,
    nvidia_version: str | None,
) -> Components:
    """Detect every component and its origin.

    Args:
        fs: Filesystem view.
        runner: Command runner.
        pinka_tags: Tailscale tags pinka gives a node it joins.
        nvidia_installed: The GPU detector found an NVIDIA driver.
        nvidia_version: Its version.

    Returns:
        The components.
    """
    record = read_record(fs)
    return Components(
        record=record,
        docker=detect_docker(fs, runner, record),
        tailscale=detect_tailscale(runner, record, pinka_tags),
        lxd=detect_lxd(fs, runner),
        podman=detect_podman(fs, runner),
        incus=detect_incus(fs, runner, record),
        nvidia_driver=detect_nvidia_driver(
            record, installed=nvidia_installed, version=nvidia_version
        ),
    )


def _udp_wildcards(fs: FileSystem) -> list[int]:
    """DNS and DHCP ports bound to every address (``/proc/net/udp*``)."""
    found: list[int] = []
    for table in UDP_TABLES:
        for line in (fs.read_text(table) or "").splitlines()[1:]:
            fields = line.split()
            if len(fields) < 2 or ":" not in fields[1]:  # noqa: PLR2004
                continue
            address, port_hex = fields[1].rsplit(":", 1)
            port = int(port_hex, 16)
            if port in DNS_DHCP_PORTS and set(address) == {"0"} and port not in found:
                found.append(port)
    return found


def _overlaps(value: str | None, networks: Iterable[ipaddress.IPv4Network]) -> list[str]:
    try:
        own = ipaddress.ip_network(str(value), strict=False)
    except ValueError:
        return []
    if not isinstance(own, ipaddress.IPv4Network):
        return []
    return [str(n) for n in networks if own.overlaps(n)]


def lxd_clashes(
    fs: FileSystem,
    lxd: Component,
    *,
    bridge_prefixes: Iterable[str],
    bridge_names: Iterable[str],
    networks: Iterable[ipaddress.IPv4Network],
    pool_device: str | None,
    docker_preexisting: bool,
) -> list[str]:
    """What would clash if Incus were installed next to this LXD (§7.7).

    Args:
        fs: Filesystem view.
        lxd: The detected LXD.
        bridge_prefixes: Prefixes of the bridges pinka's Incus creates.
        bridge_names: Exact names of the bridges pinka's Incus creates.
        networks: Subnets pinka uses (its Docker pool, its Incus networks).
        pool_device: The configured pool device (``incus_pool_source: device``).
        docker_preexisting: Docker Engine is on the host already, so pinka
            does not install it.

    Returns:
        One sentence per clash; empty when Incus fits next to LXD.
    """
    if not lxd.installed:
        return []
    prefixes, names, nets = tuple(bridge_prefixes), set(bridge_names), list(networks)
    clashes: list[str] = []
    for net in lxd.details.get("networks", []):
        name = str(net.get("name", ""))
        if name in names or name.startswith(prefixes):
            clashes.append(f"LXD's network {name} has a name pinka's Incus uses")
        overlap = _overlaps(net.get("ipv4"), nets)
        if overlap:
            clashes.append(
                f"LXD's network {name} ({net.get('ipv4')}) overlaps pinka's {', '.join(overlap)}"
            )
    for port in _udp_wildcards(fs):
        service = "DNS" if port == DNS_DHCP_PORTS[0] else "DHCP"
        clashes.append(
            f"a {service} server listens on UDP port {port} of every address, which the dnsmasq "
            "of Incus's bridges needs"
        )
    if pool_device:
        for source in lxd.details.get("storage_sources", []):
            if source == pool_device or fs.readlink(pool_device) == source:
                clashes.append(f"the pool device {pool_device} is LXD's storage ({source})")
    if not docker_preexisting and lxd.details.get("networks"):
        clashes.append(
            "Docker Engine, which pinka would install, sets the FORWARD policy to DROP and "
            "would stop forwarding on LXD's bridges"
        )
    return clashes


def component_conflicts(
    fs: FileSystem,
    comps: Components,
    *,
    gpu_usable: bool,
    lxd_clash_args: Mapping[str, Any],
) -> tuple[list[Conflict], list[str]]:
    """Conflicts and notes about pre-existing components (§2.2).

    Args:
        fs: Filesystem view.
        comps: The detected components.
        gpu_usable: The host has a usable NVIDIA GPU (a GPU feature will be on).
        lxd_clash_args: The keyword arguments of :func:`lxd_clashes` other
            than the filesystem, LXD and ``docker_preexisting``.

    Returns:
        ``(conflicts, notes)``.
    """
    conflicts: list[Conflict] = []
    notes: list[str] = []
    docker, tailscale, lxd, podman = comps.docker, comps.tailscale, comps.lxd, comps.podman
    if docker.installed and docker.origin == PRE_EXISTING:
        notes.append(
            f"{_named('Docker Engine', docker.version)} is installed already: pinka uses it as it "
            "is and never changes its configuration or restarts it"
        )
        if not docker.running:
            conflicts.append(
                Conflict(
                    "docker_not_running",
                    "needs_action",
                    "Docker Engine is installed but docker.service is not running",
                    "Start and enable it ('systemctl enable --now docker.service'); pinka does "
                    "not start a Docker it did not install. Then re-run bootstrap.",
                )
            )
        missing = [p for p in ("compose", "buildx") if not docker.details.get(p)]
        if missing:
            conflicts.append(
                Conflict(
                    "docker_plugins_missing",
                    "needs_action",
                    f"The installed Docker Engine lacks the {' and '.join(missing)} plugin",
                    "Install the plugins from the same source as Docker (for Docker's own "
                    "repositories: docker-compose-plugin and docker-buildx-plugin), then re-run "
                    "bootstrap. pinka does not change a Docker it did not install.",
                )
            )
        if gpu_usable and docker.details.get("cdi") is False:
            add = (
                'remove "features": {"cdi": false}'
                if _version_tuple(docker.version) >= CDI_DEFAULT_SINCE
                else 'add "features": {"cdi": true}'
            )
            conflicts.append(
                Conflict(
                    "docker_cdi_off",
                    "needs_action",
                    "CDI is off in the installed Docker Engine; the lab's GPU containers need it",
                    f"In /etc/docker/daemon.json {add}, then restart Docker "
                    "('systemctl restart docker.service') and re-run bootstrap. pinka does not "
                    "change a Docker it did not install.",
                )
            )
    if tailscale.installed and tailscale.origin == PRE_EXISTING:
        notes.append(
            f"Tailscale is logged in already as {tailscale.details.get('hostname')}: pinka uses "
            "the node as it is (no new login, name, tags or settings)"
        )
        if tailscale.details.get("funnel"):
            conflicts.append(
                Conflict(
                    "tailscale_funnel_on",
                    "needs_action",
                    "Tailscale Funnel is on for this node; the lab never runs with Funnel",
                    "Turn Funnel off ('tailscale funnel reset'), then re-run bootstrap.",
                )
            )
        if tailscale.details.get("ssh"):
            conflicts.append(
                Conflict(
                    "tailscale_ssh_on",
                    "needs_action",
                    "Tailscale SSH is on for this node; it would bypass sshd and the "
                    "researchers' keys",
                    "Turn it off ('tailscale set --ssh=false'), then re-run bootstrap.",
                )
            )
    if podman.installed:
        notes.append(f"{_named('Podman', podman.version)} is installed: pinka leaves it as it is")
        compat = podman.details.get("docker_compat")
        if compat:
            conflicts.append(
                Conflict(
                    "podman_docker",
                    "needs_action",
                    f"Podman provides Docker's command or socket: {compat}",
                    "Docker Engine needs the docker command and socket. Remove podman-docker "
                    "(or the link to Podman's socket), then re-run bootstrap; Podman itself "
                    "can stay.",
                )
            )
    if lxd.installed:
        notes.append(f"{_named('LXD', lxd.version)} is installed: pinka leaves it as it is")
        clashes = lxd_clashes(
            fs,
            lxd,
            docker_preexisting=docker.installed and docker.origin == PRE_EXISTING,
            **lxd_clash_args,
        )
        if clashes:
            conflicts.append(
                Conflict(
                    "lxd_incus_clash",
                    "needs_action",
                    "Incus cannot be installed next to LXD on this host: " + "; ".join(clashes),
                    "Change the LXD or host setting named above (for an overlapping subnet or "
                    "the pool device, docker_address_pools or incus_pool_device in host_vars "
                    "will do), then re-run bootstrap. pinka never changes LXD.",
                )
            )
    return conflicts, notes
