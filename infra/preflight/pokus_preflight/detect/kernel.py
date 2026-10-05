"""Kernel, init system and kernel capability detection (BUILD_PROMPT §2.2)."""

from __future__ import annotations

from dataclasses import dataclass, field

from pokus_preflight.fs import FileSystem
from pokus_preflight.runner import Runner

MODULES = (
    "br_netfilter",
    "overlay",
    "nf_tables",
    "veth",
    "tun",
    "vhost_net",
    "vhost_vsock",
    "kvm",
    "kvm_intel",
    "kvm_amd",
    "btrfs",
    "dm_thin_pool",
)
FILESYSTEM_MODULES = ("btrfs", "overlay")
EFI_SECURE_BOOT_GLOB = "/sys/firmware/efi/efivars/SecureBoot-*"
EFI_VALUE_OFFSET = 4  # efivarfs files start with 4 attribute bytes


@dataclass
class KernelInfo:
    """Kernel, init and capability facts.

    Attributes:
        release: ``/proc/sys/kernel/osrelease``.
        version: ``/proc/version``.
        pid1_comm: Name of PID 1 (``/proc/1/comm``).
        systemd_pid1: PID 1 is systemd.
        system_state: Output of ``systemctl is-system-running``.
        cgroup_fs_type: Filesystem type of ``/sys/fs/cgroup`` (``stat -f``).
        cgroup2: Unified cgroup v2 hierarchy is mounted.
        cgroup2_controllers: Available cgroup v2 controllers.
        filesystems: Filesystems listed in ``/proc/filesystems``.
        modules: State per module: ``loaded``, ``builtin``, ``loadable`` or ``missing``.
        tun: ``/dev/net/tun`` is a character device.
        kvm_present: ``/dev/kvm`` is a character device.
        kvm_usable: ``/dev/kvm`` opens read-write.
        kvm_nested: Nested virtualization parameter, if readable.
        secure_boot: ``enabled``, ``disabled``, ``not_efi`` or ``unknown``.
        nouveau_loaded: The nouveau driver is loaded.
        mac_system: ``selinux``, ``apparmor`` or ``none``.
        mac_mode: ``enforcing``, ``permissive``, ``enabled``, ``disabled`` or None.
    """

    release: str | None
    version: str | None
    pid1_comm: str | None
    systemd_pid1: bool
    system_state: str | None
    cgroup_fs_type: str | None
    cgroup2: bool
    cgroup2_controllers: list[str] = field(default_factory=list)
    filesystems: list[str] = field(default_factory=list)
    modules: dict[str, str] = field(default_factory=dict)
    tun: bool = False
    kvm_present: bool = False
    kvm_usable: bool = False
    kvm_nested: bool | None = None
    secure_boot: str = "unknown"
    nouveau_loaded: bool = False
    mac_system: str = "none"
    mac_mode: str | None = None

    def module_available(self, name: str) -> bool:
        """Tell whether a module is loaded, built in or loadable.

        Args:
            name: Module name.

        Returns:
            True unless the module is missing.
        """
        return self.modules.get(name, "missing") != "missing"

    def feature_present(self, name: str) -> bool:
        """Tell whether a kernel feature is usable (module or filesystem).

        Args:
            name: Module or filesystem name.

        Returns:
            True if the module is available or the filesystem is registered.
        """
        return self.module_available(name) or name in self.filesystems


def loaded_modules(fs: FileSystem) -> list[str]:
    """List loaded modules from ``/proc/modules``.

    Args:
        fs: Filesystem view.

    Returns:
        Module names.
    """
    text = fs.read_text("/proc/modules") or ""
    return [line.split()[0] for line in text.splitlines() if line.split()]


def module_state(fs: FileSystem, runner: Runner, name: str, loaded: list[str]) -> str:
    """Classify one kernel module.

    Built-in modules have ``/sys/module/<name>`` but no ``/proc/modules`` entry
    (common on WSL). ``modprobe -n`` decides whether a module can be loaded.

    Args:
        fs: Filesystem view.
        runner: Command runner.
        name: Module name.
        loaded: Names from ``/proc/modules``.

    Returns:
        ``loaded``, ``builtin``, ``loadable`` or ``missing``.
    """
    if name in loaded:
        return "loaded"
    if fs.is_dir(f"/sys/module/{name}"):
        return "builtin"
    if runner.run(["modprobe", "-n", name]).ok:
        return "loadable"
    return "missing"


def detect_secure_boot(fs: FileSystem, runner: Runner) -> str:
    """Determine the Secure Boot state.

    ``mokutil --sb-state`` is preferred; the EFI variable is the fallback.

    Args:
        fs: Filesystem view.
        runner: Command runner.

    Returns:
        ``enabled``, ``disabled``, ``not_efi`` or ``unknown``.
    """
    res = runner.run(["mokutil", "--sb-state"])
    text = (res.stdout + res.stderr).lower()
    if "secureboot enabled" in text:
        return "enabled"
    if "secureboot disabled" in text:
        return "disabled"
    if not fs.is_dir("/sys/firmware/efi"):
        return "not_efi"
    for path in fs.glob(EFI_SECURE_BOOT_GLOB):
        data = fs.read_bytes(path, 16) or b""
        if len(data) > EFI_VALUE_OFFSET:
            return "enabled" if data[EFI_VALUE_OFFSET] == 1 else "disabled"
    return "unknown"


def detect_mac(fs: FileSystem, runner: Runner) -> tuple[str, str | None]:
    """Detect the mandatory access control system and its mode.

    Args:
        fs: Filesystem view.
        runner: Command runner.

    Returns:
        ``(system, mode)``.
    """
    enforce = fs.read_first_line("/sys/fs/selinux/enforce")
    if enforce is not None:
        return "selinux", "enforcing" if enforce == "1" else "permissive"
    res = runner.run(["getenforce"])
    if res.ok and res.stdout.strip():
        mode = res.stdout.strip().lower()
        if mode in ("enforcing", "permissive"):
            return "selinux", mode
    if fs.is_dir("/sys/kernel/security/apparmor"):
        enabled = fs.read_first_line("/sys/module/apparmor/parameters/enabled")
        return "apparmor", "disabled" if enabled == "N" else "enabled"
    return "none", None


def detect_kernel(fs: FileSystem, runner: Runner) -> KernelInfo:
    """Collect kernel, init and capability facts.

    Args:
        fs: Filesystem view.
        runner: Command runner.

    Returns:
        The kernel facts.
    """
    pid1 = fs.read_first_line("/proc/1/comm")
    state = runner.run(["systemctl", "is-system-running"])
    fstype_res = runner.run(["stat", "-f", "-c", "%T", "/sys/fs/cgroup"])
    fstype = fstype_res.stdout.strip() or None if fstype_res.ok else None
    controllers_text = fs.read_text("/sys/fs/cgroup/cgroup.controllers")
    cgroup2 = controllers_text is not None and fstype == "cgroup2fs"
    filesystems = [
        line.split()[-1]
        for line in (fs.read_text("/proc/filesystems") or "").splitlines()
        if line.split()
    ]
    loaded = loaded_modules(fs)
    modules = {name: module_state(fs, runner, name, loaded) for name in MODULES}
    nested: bool | None = None
    for vendor in ("kvm_intel", "kvm_amd"):
        value = fs.read_first_line(f"/sys/module/{vendor}/parameters/nested")
        if value is not None:
            nested = value in ("Y", "y", "1")
            break
    mac_system, mac_mode = detect_mac(fs, runner)
    return KernelInfo(
        release=fs.read_first_line("/proc/sys/kernel/osrelease"),
        version=fs.read_first_line("/proc/version"),
        pid1_comm=pid1,
        systemd_pid1=pid1 == "systemd",
        system_state=state.stdout.strip() or None if state.returncode != 127 else None,  # noqa: PLR2004
        cgroup_fs_type=fstype,
        cgroup2=cgroup2,
        cgroup2_controllers=(controllers_text or "").split() if cgroup2 else [],
        filesystems=filesystems,
        modules=modules,
        tun=fs.is_char_device("/dev/net/tun"),
        kvm_present=fs.is_char_device("/dev/kvm"),
        kvm_usable=fs.is_char_device("/dev/kvm") and fs.can_open_rw("/dev/kvm"),
        kvm_nested=nested,
        secure_boot=detect_secure_boot(fs, runner),
        nouveau_loaded="nouveau" in loaded or fs.is_dir("/sys/module/nouveau"),
        mac_system=mac_system,
        mac_mode=mac_mode,
    )
