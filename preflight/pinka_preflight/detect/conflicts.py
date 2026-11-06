"""Conflicting software and host state.

Each conflict has a severity: ``blocking`` (the host is unsupported),
``needs_action`` (the operator can fix it, then re-run) or ``warning``.
Informational findings (an active firewall manager, the MAC system) are
recorded separately and are not conflicts. Components that may exist before
pinka (Docker Engine, Tailscale, LXD, Podman) are judged in ``components``.
"""

from __future__ import annotations

import json
import posixpath
from dataclasses import dataclass, field

from pinka_preflight.fs import FileSystem
from pinka_preflight.runner import Runner

MANAGED_MARKER = "Managed by pinka"
VM_REMEDIATION = (
    "Run a supported distribution in a VM on this host (vm provider) and install the lab "
    "inside it; see docs/providers/vm.md."
)
# What an uninstall without --purge-data kept with the lab's data
# (the uninstall module's NAMES["kept_accounts"]).
KEPT_ACCOUNTS = "/var/lib/pinka/kept-accounts.json"
PATH_MAX = 4096  # Linux: a longer path is not resolved
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
    # TODO(verify): detection of an incomplete dnf transaction
    # needs the current dnf/dnf5 history output
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


def detect_conflicts(
    fs: FileSystem,
    runner: Runner,
    *,
    env_class: str,
    os_id: str,
    systemd_pid1: bool,
    gpu_linux_driver_in_wsl: bool,
    adopt_incus: bool,
) -> ConflictReport:
    """Detect every conflict and informational finding.

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
    if (
        runner.which("incus")
        and fs.listdir("/var/lib/incus")
        and not fs.exists("/etc/pinka/managed-incus")
    ):
        add(
            Conflict(
                "unmanaged_incus",
                "warning" if adopt_incus else "blocking",
                "Incus is installed with an existing configuration not managed by pinka",
                "Re-run with --adopt-incus to let pinka take over this Incus installation, "
                "or remove it first.",
            )
        )
    return report


def _within(path: str, root: str) -> bool:
    """Tell whether a normalized path is ``root`` or lies below it."""
    return path == root or path.startswith(root.rstrip("/") + "/")


def _resolved(fs: FileSystem, path: str, max_links: int = 40) -> str:
    """The path with every symbolic link in it followed, as far as the parts exist.

    Args:
        fs: Filesystem view.
        path: Absolute target path.
        max_links: Links followed at most (a loop ends there).

    Returns:
        The resolved path, or the normalized path when there are more links.
    """
    if len(path) > PATH_MAX:
        return fs.normalize(path)
    rest = [p for p in path.split("/") if p not in ("", ".")]
    done, links = "/", 0
    while rest:
        here = fs.normalize(posixpath.join(done, rest.pop(0)))
        target = fs.readlink(here) if fs.is_symlink(here) else None
        if target is None:
            done = here
            continue
        links += 1
        if links > max_links:
            return fs.normalize(path)
        rest = [p for p in target.split("/") if p not in ("", ".")] + rest
        if target.startswith("/"):
            done = "/"
    return done


def _relation(kept: str, srv: str) -> str | None:
    """How a research space lies to a kept lab root (both normalized), or None."""
    if kept == srv:
        return "is that directory"
    if _within(srv, kept):
        return "lies inside it"
    if _within(kept, srv):
        return "holds it"
    return None


def kept_lab(fs: FileSystem, srv_path: str) -> list[Conflict]:
    """A lab an uninstall kept where this install would put its research space.

    An uninstall without ``--purge-data`` keeps the lab's data and its
    accounts, locks the lab root to root, and names it in the kept-accounts
    file. An install into it would start on part of the old data
    (PostgreSQL's data directory) with new secrets, and nothing takes kept
    data over yet; a research space inside it or above it mixes the two
    labs, and a later ``--purge-data`` of the new lab would delete the kept
    one. The paths are compared as written and with their symbolic links
    followed (uninstall locks a linked lab root where it leads).

    Args:
        fs: Filesystem view.
        srv_path: The research space this install would use.

    Returns:
        A ``kept_lab`` conflict (needs action), or nothing.
    """
    text = fs.read_text(KEPT_ACCOUNTS)
    try:
        note = json.loads(text) if text is not None else None
    except (ValueError, RecursionError):
        return []
    lab_root = note.get("lab_root") if isinstance(note, dict) else None
    if not isinstance(lab_root, str) or not lab_root.startswith("/"):
        return []
    if not srv_path.startswith("/"):
        return []
    data = _resolved(fs, lab_root)  # uninstall locks a linked lab root where it leads
    if not (fs.exists(lab_root) or fs.exists(data)):
        return []
    kept, srv = fs.normalize(lab_root), fs.normalize(srv_path)
    where = _relation(kept, srv)
    if where is None:
        real_srv = _resolved(fs, srv_path)
        where = _relation(data, real_srv)
        if where is None:
            return []
        where += (
            f" with symbolic links followed (the research space is {real_srv}, "
            f"the kept lab root {data})"
        )
    message = _kept_lab_message(kept, srv, where)
    link = None if not fs.is_symlink(lab_root) else (data if fs.exists(data) else "")
    remediation = _kept_lab_remediation(kept, srv, link)
    return [Conflict("kept_lab", "needs_action", message, remediation)]


def _kept_lab_message(kept: str, srv: str, where: str) -> str:
    """The ``kept_lab`` message.

    Args:
        kept: The kept lab root, as the note names it (normalized).
        srv: The research space this install would use (normalized).
        where: How the research space lies to the kept lab root.

    Returns:
        The message.
    """
    return (
        f"An uninstall kept the lab's data in {kept}, and its accounts, and this install's "
        f"research space {srv} {where}; a new install does not take kept data over"
    )


def _kept_lab_remediation(kept: str, srv: str, link: str | None) -> str:
    """The two ways forward from a kept lab.

    A restore puts the files back below the research space it is given, so
    the lab comes back only into the one it had. The data of a lab root
    that is a symbolic link lies where the link leads, and ``--purge-data``
    removes only the link; a link above the lab root changes neither.

    Args:
        kept: The kept lab root, as the note names it (normalized).
        srv: The research space this install would use (normalized).
        link: Where the lab root leads when it is a symbolic link ("" when
            nothing is there any more), else None.

    Returns:
        The remediation.
    """
    if link is None:
        move, purged = f"move {kept} out of the way (for example to {kept}.kept)", ""
    elif not link:
        move, purged = f"remove the link {kept}", ""
    else:
        move = (
            f"move {link}, where the link {kept} leads, out of the way (for example to "
            f"{link}.kept) and remove the link"
        )
        purged = f" (it removes the link {kept}: delete {link} as well)"
    space = (
        ""
        if srv == kept
        else f", set pinka_root: {kept} in the machine's host_vars file (the research space "
        "the kept lab had)"
    )
    moved = (
        ""
        if link == ""
        else ", and delete the moved directory once the restored lab is verified (nothing "
        "names it any more)"
    )
    return (
        f"Restore the lab from its backups, into the research space it had: {move}{space}, "
        f"run sudo ./bootstrap/bootstrap.sh --machine <machine> --restore{moved}. Or "
        "delete the kept data on purpose: sudo ./bootstrap/bootstrap.sh --uninstall "
        f"--purge-data --machine <machine>{purged}, then re-run preflight. If the uninstall "
        "did not finish, run sudo ./bootstrap/bootstrap.sh --uninstall again first. See "
        "docs/how-to/uninstall.md."
    )
