"""Conflicting software and host state (BUILD_PROMPT §2.2).

Each conflict has a severity: ``blocking`` (the host is unsupported),
``needs_action`` (the operator can fix it, then re-run) or ``warning``.
Informational findings (an active firewall manager, the MAC system) are
recorded separately and are not conflicts.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pokus_preflight.fs import FileSystem
from pokus_preflight.runner import Runner

MANAGED_MARKER = "Managed by pokus-infra"
VM_REMEDIATION = (
    "Run a supported distribution in a VM on this host (vm provider) and install the lab "
    "inside it; see docs/providers/vm.md."
)
PKG_PROCESSES = frozenset(
    {"apt", "apt-get", "dpkg", "unattended-upgr", "dnf", "dnf5", "yum", "rpm", "zypper", "pacman"}
)


@dataclass
class Conflict:
    """One conflict.

    Attributes:
        id: Stable identifier.
        severity: ``blocking``, ``needs_action`` or ``warning``.
        message: What was found.
        remediation: What the operator should do.
    """

    id: str
    severity: str
    message: str
    remediation: str


@dataclass
class ConflictReport:
    """Conflicts and informational findings.

    Attributes:
        conflicts: Conflicts in detection order.
        firewall_manager: Active firewall manager (``firewalld`` or ``ufw``), if any.
        firewall_backend: Backend the firewall role will use.
        infos: Informational findings.
    """

    conflicts: list[Conflict] = field(default_factory=list)
    firewall_manager: str | None = None
    firewall_backend: str = "nftables"
    infos: list[str] = field(default_factory=list)


def _active(runner: Runner, unit: str) -> bool:
    res = runner.run(["systemctl", "is-active", unit])
    return res.stdout.strip() == "active"


def _docker_desktop(fs: FileSystem, runner: Runner) -> str | None:
    if fs.glob("/mnt/wsl/docker-desktop*"):
        return "/mnt/wsl/docker-desktop* exists"
    docker = runner.which("docker")
    if docker and docker.startswith("/mnt/wsl/"):
        return f"docker on PATH resolves into /mnt/wsl ({docker})"
    if docker:
        res = runner.run(["docker", "context", "ls", "--format", "{{.Name}}"])
        if res.ok and "desktop-linux" in res.stdout.split():
            return "docker context desktop-linux exists"
    target = fs.readlink("/var/run/docker.sock")
    if target and target.startswith("/mnt/wsl"):
        return "/var/run/docker.sock links into /mnt/wsl"
    return None


def _immutable(fs: FileSystem, os_id: str) -> str | None:
    if fs.exists("/run/ostree-booted"):
        return "image-based OS (/run/ostree-booted)"
    if fs.exists("/etc/NIXOS"):
        return "NixOS (/etc/NIXOS)"
    if os_id == "ubuntu-core" or (fs.is_dir("/writable") and os_id.startswith("ubuntu-core")):
        return "Ubuntu Core"
    if fs.exists("/usr/sbin/transactional-update"):
        for line in (fs.read_text("/proc/mounts") or "").splitlines():
            parts = line.split()
            if len(parts) >= 4 and parts[1] == "/" and "ro" in parts[3].split(","):  # noqa: PLR2004
                return "transactional-update with a read-only root"
    return None


def _musl(fs: FileSystem, runner: Runner) -> bool:
    if fs.glob("/lib/ld-musl-*"):
        return True
    res = runner.run(["ldd", "--version"])
    return "musl" in (res.stdout + res.stderr).lower()


def _package_manager_busy(fs: FileSystem) -> str | None:
    # TODO(verify): detection of an incomplete dnf transaction (BUILD_PROMPT
    # §2.2 "dnf history incomplete") needs the current dnf/dnf5 history output
    # format; until verified, only running package-manager processes are found.
    if fs.listdir("/var/lib/dpkg/updates"):
        return "dpkg was interrupted (/var/lib/dpkg/updates is not empty)"
    if fs.exists("/var/lib/pacman/db.lck"):
        return "pacman database lock /var/lib/pacman/db.lck exists"
    pid = fs.read_first_line("/run/zypp.pid")
    if pid and pid.isdigit() and fs.is_dir(f"/proc/{pid}"):
        return f"zypper is running (pid {pid})"
    for entry in fs.listdir("/proc"):
        if entry.isdigit():
            comm = fs.read_first_line(f"/proc/{entry}/comm")
            if comm in PKG_PROCESSES:
                return f"package manager process {comm} is running (pid {entry})"
    return None


def detect_conflicts(  # noqa: PLR0912 - one branch per documented conflict
    fs: FileSystem,
    runner: Runner,
    *,
    env_class: str,
    os_id: str,
    systemd_pid1: bool,
    gpu_linux_driver_in_wsl: bool,
    adopt_incus: bool,
) -> ConflictReport:
    """Detect every conflict of the BUILD_PROMPT §2.2 table.

    Args:
        fs: Filesystem view.
        runner: Command runner.
        env_class: Environment class.
        os_id: ``ID`` from os-release.
        systemd_pid1: PID 1 is systemd.
        gpu_linux_driver_in_wsl: A Linux NVIDIA driver exists inside WSL.
        adopt_incus: ``--adopt-incus`` was given.

    Returns:
        Conflicts and informational findings.
    """
    report = ConflictReport()
    add = report.conflicts.append
    found = _docker_desktop(fs, runner)
    if found:
        add(
            Conflict(
                "docker_desktop_wsl",
                "blocking",
                f"Docker Desktop WSL integration detected: {found}",
                "Turn off Docker Desktop's WSL integration for this distribution in Docker "
                "Desktop's settings, then re-run preflight.",
            )
        )
    if runner.run(["snap", "list", "docker"]).ok:
        add(
            Conflict(
                "snap_docker",
                "blocking",
                "Docker is installed as a snap",
                "Remove the snap ('snap remove docker'); the lab installs Docker Engine itself.",
            )
        )
    if (
        runner.run(["snap", "list", "lxd"]).ok
        or runner.which("lxd")
        or fs.exists("/sys/class/net/lxdbr0")
    ):
        add(
            Conflict(
                "lxd_installed",
                "blocking",
                "LXD is installed",
                "Remove LXD; migrating LXD to Incus is out of scope for the lab.",
            )
        )
    for unit in ("firewalld", "ufw"):
        if report.firewall_manager is None and _active(runner, unit):
            report.firewall_manager = unit
            report.firewall_backend = unit
            report.infos.append(f"{unit} is active; the firewall role integrates with it")
    immutable = _immutable(fs, os_id)
    if immutable:
        add(
            Conflict(
                "immutable_os",
                "blocking",
                f"Unsupported image-based OS: {immutable}",
                VM_REMEDIATION,
            )
        )
    if not systemd_pid1 and env_class != "wsl2":
        add(Conflict("non_systemd_init", "blocking", "PID 1 is not systemd", VM_REMEDIATION))
    if _musl(fs, runner):
        add(Conflict("musl_libc", "blocking", "The C library is musl, not glibc", VM_REMEDIATION))
    busy = _package_manager_busy(fs)
    if busy:
        add(
            Conflict(
                "package_manager_busy",
                "needs_action",
                busy,
                "Wait for the running package operation to finish or complete the interrupted one "
                "(for example 'dpkg --configure -a'), then re-run preflight.",
            )
        )
    if gpu_linux_driver_in_wsl:
        add(
            Conflict(
                "linux_nvidia_driver_in_wsl",
                "blocking",
                "A Linux NVIDIA kernel driver is installed inside WSL",
                "Remove the Linux NVIDIA driver packages from this WSL distribution; WSL uses the "
                "Windows NVIDIA driver through /dev/dxg. Never install a Linux NVIDIA driver "
                "in WSL.",
            )
        )
    if _active(runner, "podman.socket"):
        add(
            Conflict(
                "podman_socket",
                "warning",
                "podman.socket is active",
                "Stop and disable podman.socket unless you need it; the lab uses Docker Engine.",
            )
        )
    daemon_json = fs.read_text("/etc/docker/daemon.json")
    if daemon_json is not None and MANAGED_MARKER not in daemon_json:
        add(
            Conflict(
                "unmanaged_docker_config",
                "warning",
                "/etc/docker/daemon.json exists and is not managed by pokus-infra",
                "It will be replaced by the docker role; a backup is taken first.",
            )
        )
    if (
        runner.which("incus")
        and fs.listdir("/var/lib/incus")
        and not fs.exists("/etc/pokus/managed-incus")
    ):
        add(
            Conflict(
                "unmanaged_incus",
                "warning" if adopt_incus else "blocking",
                "Incus is installed with an existing configuration not managed by pokus-infra",
                "Re-run with --adopt-incus to let pokus-infra take over this Incus installation, "
                "or remove it first.",
            )
        )
    return report
