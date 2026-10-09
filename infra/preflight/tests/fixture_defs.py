"""Definitions of the preflight fixture roots (BUILD_PROMPT §11).

Each fixture is a fake root (``etc/``, ``proc/``, ``sys/``, ``dev/`` ...) plus
``fixture.json`` (device and link metadata), ``commands.json`` (command,
DNS and HTTPS answers), ``expected.json`` (what preflight must decide) and,
for fixtures that need them, ``args.json`` (extra preflight arguments, such as
the operator's ``--nvidia-driver`` choice).
Fixtures are composed from small layers here and written to
``preflight/tests/fixtures/<name>/`` with::

    python preflight/tests/fixture_defs.py --write

The committed tree must equal the generated one (``test_fixtures.py`` checks
it). All values are synthetic: no real host, person, serial number or address
appears in a fixture. Fixtures captured from real hosts must pass
``sanitize_fixture.py`` first.

Standard library only; compatible with Python 3.9.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Union

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES_DIR = os.path.join(HERE, "fixtures")
GIB_KB = 1024 * 1024
GIB = 1024**3
NOW = "2026-10-05T09:12:44Z"
EFI_SB = "/sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c"

Content = Union[str, bytes]


@dataclass
class Fixture:
    """A fixture under construction.

    Attributes:
        files: Target path to content.
        dirs: Target directories that must exist (kept with ``.fixture-keep``).
        meta: ``fixture.json`` content.
        commands: ``shlex.join(argv)`` to ``{"rc", "stdout", "stderr"}``.
        data: Other ``commands.json`` sections (which, cpu_count, machine, ...).
        expected: ``expected.json`` content.
        args: Extra preflight arguments (``args.json``, written when not empty).
    """

    files: dict[str, Content] = field(default_factory=dict)
    dirs: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(
        default_factory=lambda: {"char_devices": [], "rw_openable": [], "symlinks": {}}
    )
    commands: dict[str, dict[str, Any]] = field(default_factory=dict)
    data: dict[str, Any] = field(default_factory=dict)
    expected: dict[str, Any] = field(default_factory=dict)
    args: list[str] = field(default_factory=list)

    def cmd(self, line: str, stdout: str = "", rc: int = 0, stderr: str = "") -> None:
        """Register a command answer."""
        self.commands[line] = {"rc": rc, "stdout": stdout, "stderr": stderr}

    def drop(self, path: str) -> None:
        """Remove a file, a directory marker, or a command answer."""
        self.files.pop(path, None)
        if path in self.dirs:
            self.dirs.remove(path)
        self.commands.pop(path, None)

    def chardev(self, path: str, rw: bool = False) -> None:
        """Add a character device marker."""
        self.files[path] = ""
        if path not in self.meta["char_devices"]:
            self.meta["char_devices"].append(path)
        if rw and path not in self.meta["rw_openable"]:
            self.meta["rw_openable"].append(path)


Layer = Callable[[Fixture], None]

# --------------------------------------------------------------------------- OS


def os_release(**fields: str) -> Layer:
    """Write ``/etc/os-release`` with the given keys (values are quoted)."""

    def apply(f: Fixture) -> None:
        f.files["/etc/os-release"] = "".join(f'{k}="{v}"\n' for k, v in fields.items())

    return apply


UBUNTU_2404 = os_release(
    PRETTY_NAME="Ubuntu 24.04.3 LTS",
    NAME="Ubuntu",
    VERSION_ID="24.04",
    VERSION_CODENAME="noble",
    ID="ubuntu",
    ID_LIKE="debian",
    UBUNTU_CODENAME="noble",
)
DEBIAN_13 = os_release(
    PRETTY_NAME="Debian GNU/Linux 13 (trixie)",
    VERSION_ID="13",
    VERSION_CODENAME="trixie",
    ID="debian",
)
DEBIAN_12 = os_release(
    PRETTY_NAME="Debian GNU/Linux 12 (bookworm)",
    VERSION_ID="12",
    VERSION_CODENAME="bookworm",
    ID="debian",
)
FEDORA_44 = os_release(
    NAME="Fedora Linux",
    VERSION_ID="44",
    ID="fedora",
    PRETTY_NAME="Fedora Linux 44 (Workstation Edition)",
    VARIANT_ID="workstation",
)
FEDORA_SILVERBLUE = os_release(
    NAME="Fedora Linux",
    VERSION_ID="44",
    ID="fedora",
    PRETTY_NAME="Fedora Linux 44 (Silverblue)",
    VARIANT_ID="silverblue",
)
ALMA_9 = os_release(
    NAME="AlmaLinux",
    VERSION_ID="9.6",
    ID="almalinux",
    ID_LIKE="rhel centos fedora",
    PRETTY_NAME="AlmaLinux 9.6",
)
RHEL_8 = os_release(
    NAME="Red Hat Enterprise Linux",
    VERSION_ID="8.10",
    ID="rhel",
    ID_LIKE="fedora",
    PRETTY_NAME="Red Hat Enterprise Linux 8.10 (Ootpa)",
)
ORACLE_9 = os_release(
    NAME="Oracle Linux Server",
    VERSION_ID="9.6",
    ID="ol",
    ID_LIKE="fedora",
    PRETTY_NAME="Oracle Linux Server 9.6",
)
TUMBLEWEED = os_release(
    NAME="openSUSE Tumbleweed",
    VERSION_ID="20261001",
    ID="opensuse-tumbleweed",
    ID_LIKE="opensuse suse",
    PRETTY_NAME="openSUSE Tumbleweed",
)
ARCH = os_release(NAME="Arch Linux", ID="arch", BUILD_ID="rolling", PRETTY_NAME="Arch Linux")
MANJARO = os_release(
    NAME="Manjaro Linux",
    ID="manjaro",
    ID_LIKE="arch",
    BUILD_ID="rolling",
    PRETTY_NAME="Manjaro Linux",
)
MINT_22 = os_release(
    NAME="Linux Mint",
    VERSION_ID="22",
    VERSION_CODENAME="wilma",
    ID="linuxmint",
    ID_LIKE="ubuntu debian",
    UBUNTU_CODENAME="noble",
    PRETTY_NAME="Linux Mint 22",
)
NIXOS = os_release(NAME="NixOS", ID="nixos", VERSION_ID="25.11", PRETTY_NAME="NixOS 25.11")
ALPINE = os_release(
    NAME="Alpine Linux", ID="alpine", VERSION_ID="3.22.1", PRETTY_NAME="Alpine Linux v3.22"
)
DEVUAN = os_release(
    NAME="Devuan GNU/Linux",
    ID="devuan",
    ID_LIKE="debian",
    VERSION_ID="6",
    VERSION_CODENAME="excalibur",
    PRETTY_NAME="Devuan GNU/Linux 6 (excalibur)",
)

# ----------------------------------------------------------------------- system

DEFAULT_FILESYSTEMS = ["sysfs", "tmpfs", "proc", "cgroup2", "ext4", "btrfs", "overlay", "vfat"]
LOADED_MODULES = ["br_netfilter", "overlay", "nf_tables", "veth", "tun", "btrfs", "dm_thin_pool"]


def system(
    *,
    kernel: str = "6.8.0-85-generic",
    threads: int = 24,
    memory_gib: float = 32,
    swap_gib: float = 8,
    flags: str = "fpu vme de pse tsc msr pae mce cx8 apic sep sse sse2 ht syscall nx lm vmx",
    loaded: list[str] | None = None,
    builtin: list[str] | None = None,
    filesystems: list[str] | None = None,
) -> Layer:
    """Common Linux host with systemd as PID 1 and cgroup v2."""

    def apply(f: Fixture) -> None:
        f.files["/proc/sys/kernel/osrelease"] = kernel + "\n"
        f.files["/proc/version"] = (
            f"Linux version {kernel} (builder@example.com) (gcc 14) #1 SMP PREEMPT_DYNAMIC\n"
        )
        f.files["/proc/cpuinfo"] = (
            "processor\t: 0\nvendor_id\t: GenuineIntel\nmodel name\t: x86-64 test CPU\n"
            f"flags\t\t: {flags}\n\n"
        )
        f.files["/proc/meminfo"] = (
            f"MemTotal:       {int(memory_gib * GIB_KB)} kB\nMemFree:        1024 kB\n"
            f"SwapTotal:      {int(swap_gib * GIB_KB)} kB\n"
        )
        f.files["/proc/1/comm"] = "systemd\n"
        f.files["/proc/1/cgroup"] = "0::/init.scope\n"
        f.files["/proc/1/environ"] = b"HOME=/\0TERM=linux\0"
        f.files["/proc/filesystems"] = "".join(
            f"nodev\t{n}\n" for n in (filesystems or DEFAULT_FILESYSTEMS)
        )
        mods = LOADED_MODULES if loaded is None else loaded
        f.files["/proc/modules"] = "".join(f"{m} 16384 0 - Live 0x0000000000000000\n" for m in mods)
        for m in builtin or []:
            f.dirs.append(f"/sys/module/{m}")
        f.files["/sys/fs/cgroup/cgroup.controllers"] = (
            "cpuset cpu io memory hugetlb pids rdma misc\n"
        )
        f.dirs.extend(["/srv", "/var/lib"])
        f.chardev("/dev/net/tun", rw=True)
        f.cmd("stat -f -c %T /sys/fs/cgroup", "cgroup2fs\n")
        f.cmd("systemctl is-system-running", "running\n")
        f.cmd(
            "timedatectl show",
            f"NTP=yes\nNTPSynchronized=yes\nTimeUSec=Mon {NOW[:10]} 09:12:44 UTC\n",
        )
        f.cmd(
            "timedatectl timesync-status", "       Server: 192.0.2.123\n       Offset: +1.250ms\n"
        )
        f.data.update({"cpu_count": threads, "machine": "x86_64", "euid": 0, "now": NOW})
        disks(var_lib_free=400, srv_free=500)(f)

    return apply


def disks(var_lib_free: float, srv_free: float, fstype: str = "ext4") -> Layer:
    """Answer ``df`` for /var/lib and /srv (free space in GiB)."""

    def apply(f: Fixture) -> None:
        f.cmd(
            "df --output=fstype,size,avail -B1 /var/lib",
            f"Type 1B-blocks Avail\n{fstype} {1000 * GIB} {int(var_lib_free * GIB)}\n",
        )
        f.cmd(
            "df --output=fstype,size,avail -B1 /srv",
            f"Type 1B-blocks Avail\n{fstype} {1000 * GIB} {int(srv_free * GIB)}\n",
        )

    return apply


def cgroup_v1(f: Fixture) -> None:
    """Legacy cgroup v1 hierarchy (no unified cgroup2)."""
    f.drop("/sys/fs/cgroup/cgroup.controllers")
    f.cmd("stat -f -c %T /sys/fs/cgroup", "tmpfs\n")


def no_systemd(pid1: str = "init") -> Layer:
    """PID 1 is not systemd; systemctl does not work."""

    def apply(f: Fixture) -> None:
        f.files["/proc/1/comm"] = pid1 + "\n"
        f.drop("systemctl is-system-running")
        f.drop("timedatectl show")
        f.drop("timedatectl timesync-status")

    return apply


# ------------------------------------------------------------------ environments


def baremetal(vendor: str = "Example Board Vendor", product: str = "Desktop System") -> Layer:
    """Real hardware: systemd-detect-virt reports none."""

    def apply(f: Fixture) -> None:
        f.cmd("systemd-detect-virt", "none\n", rc=1)
        f.cmd("systemd-detect-virt --vm", "none\n", rc=1)
        f.cmd("systemd-detect-virt --container", "none\n", rc=1)
        f.files["/sys/class/dmi/id/sys_vendor"] = vendor + "\n"
        f.files["/sys/class/dmi/id/product_name"] = product + "\n"
        f.files["/sys/class/dmi/id/board_vendor"] = vendor + "\n"
        f.files["/sys/class/dmi/id/bios_vendor"] = "Example BIOS Vendor\n"
        f.cmd("mokutil --sb-state", "SecureBoot disabled\n")
        f.dirs.append("/sys/firmware/efi")

    return apply


def vm(sdv: str, vendor: str, product: str) -> Layer:
    """Virtual machine reported by systemd-detect-virt and DMI."""

    def apply(f: Fixture) -> None:
        f.cmd("systemd-detect-virt", sdv + "\n")
        f.cmd("systemd-detect-virt --vm", sdv + "\n")
        f.cmd("systemd-detect-virt --container", "none\n", rc=1)
        f.files["/sys/class/dmi/id/sys_vendor"] = vendor + "\n"
        f.files["/sys/class/dmi/id/product_name"] = product + "\n"
        f.files["/sys/class/dmi/id/board_vendor"] = vendor + "\n"
        f.files["/sys/class/dmi/id/bios_vendor"] = vendor + "\n"
        cpu = str(f.files["/proc/cpuinfo"]).replace(" vmx", "")
        f.files["/proc/cpuinfo"] = cpu.replace("lm\n", "lm hypervisor\n")
        f.cmd("mokutil --sb-state", "SecureBoot disabled\n")

    return apply


KVM_VM = vm("kvm", "QEMU", "Standard PC (Q35 + ICH9, 2009)")
HYPERV_VM = vm("microsoft", "Microsoft Corporation", "Virtual Machine")
VMWARE_VM = vm("vmware", "VMware, Inc.", "VMware Virtual Platform")

WSL_KERNEL = "6.6.87.2-microsoft-standard-WSL2"
WSL_CONF_MANAGED = (
    "# Managed by pinka (ansible/roles/provider_wsl2/templates/wsl.conf.j2).\n"
    "[boot]\nsystemd=true\n\n[automount]\nenabled=false\nmountFsTab=false\n\n"
    "[interop]\nenabled=false\nappendWindowsPath=false\n\n[network]\nhostname=pinka-lab\n"
    "generateHosts=true\ngenerateResolvConf=true\n"
)


def wsl2(conf: str | None = WSL_CONF_MANAGED, interop: str | None = None) -> Layer:
    """WSL2 kernel, WSL libraries, systemd-detect-virt reporting wsl."""

    def apply(f: Fixture) -> None:
        f.files["/proc/sys/kernel/osrelease"] = WSL_KERNEL + "\n"
        f.files["/proc/version"] = (
            f"Linux version {WSL_KERNEL} (builder@example.com) (gcc 13) #1 SMP PREEMPT_DYNAMIC\n"
        )
        f.files["/proc/modules"] = ""
        for m in (
            "br_netfilter",
            "overlay",
            "nf_tables",
            "veth",
            "tun",
            "btrfs",
            "vhost_net",
            "vhost_vsock",
        ):
            f.dirs.append(f"/sys/module/{m}")
        f.dirs.append("/usr/lib/wsl/lib")
        f.cmd("systemd-detect-virt", "wsl\n")
        f.cmd("systemd-detect-virt --vm", "none\n", rc=1)
        f.cmd("systemd-detect-virt --container", "wsl\n")
        f.cmd("timedatectl timesync-status", "", rc=1)
        f.cmd("chronyc -n tracking", "", rc=127)
        if conf is not None:
            f.files["/etc/wsl.conf"] = conf
        if interop is not None:
            f.files["/proc/sys/fs/binfmt_misc/WSLInterop"] = interop + "\n"

    return apply


def kvm(nested: bool = True, vendor: str = "kvm_intel") -> Layer:
    """Usable /dev/kvm."""

    def apply(f: Fixture) -> None:
        f.chardev("/dev/kvm", rw=True)
        f.files[f"/sys/module/{vendor}/parameters/nested"] = ("Y" if nested else "N") + "\n"
        f.files["/proc/modules"] = (
            str(f.files.get("/proc/modules", ""))
            + f"{vendor} 16384 0 - Live 0x0\nkvm 16384 0 - Live 0x0\n"
        )

    return apply


def wsl_kvm(f: Fixture) -> None:
    """Nested virtualization enabled on the Windows side."""
    f.chardev("/dev/kvm", rw=True)
    f.dirs.extend(["/sys/module/kvm", "/sys/module/kvm_intel"])
    f.files["/sys/module/kvm_intel/parameters/nested"] = "Y\n"


# -------------------------------------------------------------------------- GPU

NVSMI_RTX4070 = "NVIDIA GeForce RTX 4070, 12282 MiB, 580.82.07, 8.9\n"


def nvidia_pci(driver: str = "loaded", device: str = "0000:01:00.0") -> Layer:
    """An NVIDIA GPU on the PCI bus with the given driver state."""

    def apply(f: Fixture) -> None:
        f.files[f"/sys/bus/pci/devices/{device}/vendor"] = "0x10de\n"
        f.files[f"/sys/bus/pci/devices/{device}/class"] = "0x030000\n"
        if driver == "loaded":
            f.files["/proc/driver/nvidia/version"] = (
                "NVRM version: NVIDIA UNIX Open Kernel Module for x86_64  580.82.07\n"
            )
            f.data.setdefault("which", {})["nvidia-smi"] = "/usr/bin/nvidia-smi"
            f.cmd(
                "nvidia-smi --query-gpu=name,memory.total,driver_version,compute_cap --format=csv,noheader",
                NVSMI_RTX4070,
            )
            f.cmd("modinfo nvidia", "filename: /lib/modules/x/nvidia.ko\n")
        elif driver == "installed":
            f.cmd("modinfo nvidia", "filename: /lib/modules/x/nvidia.ko\n")
        else:
            f.cmd("modinfo nvidia", "", rc=1, stderr="modinfo: ERROR: Module nvidia not found.\n")

    return apply


def wsl_gpu(f: Fixture) -> None:
    """GPU shared by the Windows NVIDIA driver."""
    f.chardev("/dev/dxg", rw=True)
    f.files["/usr/lib/wsl/lib/libcuda.so.1"] = ""
    f.files["/usr/lib/wsl/lib/nvidia-smi"] = ""
    f.cmd(
        "/usr/lib/wsl/lib/nvidia-smi --query-gpu=name,memory.total,driver_version,compute_cap --format=csv,noheader",
        NVSMI_RTX4070,
    )


def secure_boot_enabled(f: Fixture) -> None:
    """Secure Boot on (mokutil and the EFI variable agree)."""
    f.cmd("mokutil --sb-state", "SecureBoot enabled\n")
    f.files[EFI_SB] = b"\x06\x00\x00\x00\x01"


def selinux_enforcing(f: Fixture) -> None:
    """SELinux in enforcing mode."""
    f.files["/sys/fs/selinux/enforce"] = "1"


def apparmor(f: Fixture) -> None:
    """AppArmor enabled."""
    f.dirs.append("/sys/kernel/security/apparmor")
    f.files["/sys/module/apparmor/parameters/enabled"] = "Y\n"


def active(unit: str) -> Layer:
    """A systemd unit reported active."""

    def apply(f: Fixture) -> None:
        f.cmd(f"systemctl is-active {unit}", "active\n")

    return apply


def no_storage(f: Fixture) -> None:
    """Neither btrfs nor dm_thin_pool."""
    f.files["/proc/filesystems"] = "".join(
        f"nodev\t{n}\n" for n in DEFAULT_FILESYSTEMS if n != "btrfs"
    )
    f.files["/proc/modules"] = "".join(
        f"{m} 16384 0 - Live 0x0000000000000000\n"
        for m in LOADED_MODULES
        if m not in ("btrfs", "dm_thin_pool")
    )


# --------------------------------------------------------------------- fixtures

FULL_FEATURES = {
    "gpu_inference": True,
    "vm_targets": True,
    "dcgm_metrics": True,
    "btrfs_pool": True,
}


def build(*layers: Layer, expected: dict[str, Any], args: list[str] | None = None) -> Fixture:
    """Compose layers into a fixture run with the extra preflight ``args``."""
    f = Fixture()
    for layer in layers:
        layer(f)
    f.expected = expected
    f.args = list(args or [])
    return f


def fixtures() -> dict[str, Fixture]:  # noqa: PLR0915 - one entry per required fixture
    """Return every fixture by name."""
    base_ubuntu = [UBUNTU_2404, system(), apparmor]
    fx: dict[str, Fixture] = {}
    fx["ubuntu2404-baremetal-nvidia-driver-loaded"] = build(
        *base_ubuntu,
        baremetal(),
        kvm(),
        nvidia_pci("loaded"),
        expected={
            "outcome": "supported",
            "provider": "baremetal",
            "tier": "supported",
            "family": "debian",
            "env_class": "baremetal",
            "features": FULL_FEATURES,
        },
    )
    fx["ubuntu2404-baremetal-nvidia-no-driver-secureboot"] = build(
        *base_ubuntu,
        baremetal(),
        kvm(),
        nvidia_pci("none"),
        secure_boot_enabled,
        expected={
            "outcome": "needs_action",
            "provider": "baremetal",
            "needs_action": ["secure_boot_mok"],
            "planned_actions": [],
            "features": {"gpu_inference": False},
            "secure_boot": "enabled",
            "install_driver": "auto",
        },
    )
    # Approved deviation I1: the same host with the operator's choices.
    secureboot = [*base_ubuntu, baremetal(), kvm(), nvidia_pci("none"), secure_boot_enabled]
    fx["ubuntu2404-baremetal-nvidia-no-driver-secureboot-consent"] = build(
        *secureboot,
        args=["--accept-mok-enrollment"],
        expected={
            "outcome": "degraded",
            "provider": "baremetal",
            "needs_action": [],
            "planned_actions": ["secure_boot_mok"],
            "warnings_contain": ["planned action secure_boot_mok"],
            "disabled": ["gpu_inference"],
            "secure_boot": "enabled",
            "install_driver": "auto",
            "overrides_on": ["accept_mok_enrollment"],
        },
    )
    fx["ubuntu2404-baremetal-nvidia-no-driver-secureboot-manual"] = build(
        *secureboot,
        args=["--nvidia-driver", "manual", "--accept-mok-enrollment"],
        expected={
            "outcome": "needs_action",
            "provider": "baremetal",
            "needs_action": ["secure_boot_mok"],
            "planned_actions": [],
            "features": {"gpu_inference": False},
            "install_driver": "manual",
        },
    )
    fx["ubuntu2404-baremetal-nvidia-no-driver-secureboot-skip"] = build(
        *secureboot,
        args=["--nvidia-driver", "skip"],
        expected={
            "outcome": "degraded",
            "provider": "baremetal",
            "needs_action": [],
            "planned_actions": [],
            "disabled": ["gpu_inference"],
            "install_driver": "skip",
        },
    )
    fx["ubuntu2404-baremetal-no-gpu"] = build(
        *base_ubuntu,
        baremetal(),
        kvm(),
        expected={
            "outcome": "degraded",
            "provider": "baremetal",
            "disabled": ["gpu_inference"],
            "features": {"gpu_inference": False, "vm_targets": True},
        },
    )
    fx["ubuntu2404-wsl2-gpu-nokvm"] = build(
        *base_ubuntu,
        wsl2(),
        wsl_gpu,
        expected={
            "outcome": "degraded",
            "provider": "wsl2",
            "env_class": "wsl2",
            "hypervisor": "microsoft",
            "disabled": ["vm_targets"],
            "features": {"gpu_inference": True, "vm_targets": False, "dcgm_metrics": False},
        },
    )
    fx["ubuntu2404-wsl2-gpu-kvm"] = build(
        *base_ubuntu,
        wsl2(),
        wsl_gpu,
        wsl_kvm,
        expected={
            "outcome": "supported",
            "provider": "wsl2",
            "env_class": "wsl2",
            "features": {"gpu_inference": True, "vm_targets": True, "btrfs_pool": True},
        },
    )
    fx["ubuntu2404-wsl2-systemd-off"] = build(
        *base_ubuntu,
        wsl2(conf=None, interop="enabled"),
        wsl_gpu,
        no_systemd("init"),
        expected={
            "outcome": "needs_action",
            "provider": "wsl2",
            "needs_action": ["wsl_enable_systemd"],
            "warnings_contain": ["Windows interop is enabled"],
        },
    )
    dd = build(
        *base_ubuntu,
        wsl2(),
        wsl_gpu,
        expected={
            "outcome": "unsupported",
            "provider": "wsl2",
            "conflicts": ["docker_desktop_wsl"],
        },
    )
    dd.dirs.append("/mnt/wsl/docker-desktop")
    dd.meta["symlinks"]["/var/run/docker.sock"] = "/mnt/wsl/shared-docker/docker.sock"
    fx["ubuntu2404-wsl2-docker-desktop-integration"] = dd
    drv = build(
        *base_ubuntu,
        wsl2(),
        wsl_gpu,
        expected={"outcome": "unsupported", "conflicts": ["linux_nvidia_driver_in_wsl"]},
    )
    drv.files["/proc/driver/nvidia/version"] = (
        "NVRM version: NVIDIA UNIX x86_64 Kernel Module  580.82.07\n"
    )
    fx["ubuntu2404-wsl2-linux-nvidia-driver-installed"] = drv
    w1 = build(
        UBUNTU_2404,
        system(kernel="4.4.0-19041-Microsoft"),
        no_systemd("init"),
        cgroup_v1,
        expected={
            "outcome": "unsupported",
            "env_class": "wsl1",
            "provider": None,
            "tier": "unsupported",
        },
    )
    w1.files["/proc/modules"] = ""
    fx["wsl1"] = w1
    fx["ubuntu2404-kvm-vm-gpu-passthrough"] = build(
        *base_ubuntu,
        KVM_VM,
        kvm(nested=True),
        nvidia_pci("loaded", "0000:06:00.0"),
        expected={
            "outcome": "supported",
            "provider": "vm",
            "env_class": "vm",
            "hypervisor": "kvm",
            "features": FULL_FEATURES,
        },
    )
    fx["ubuntu2404-hyperv-vm-no-gpu"] = build(
        *base_ubuntu,
        HYPERV_VM,
        expected={
            "outcome": "degraded",
            "provider": "vm",
            "hypervisor": "microsoft",
            "features": {"gpu_inference": False, "vm_targets": False},
            "warnings_contain": ["Nested virtualization unavailable"],
        },
    )
    fx["ubuntu2404-vmware-vm-no-gpu"] = build(
        *base_ubuntu,
        VMWARE_VM,
        expected={
            "outcome": "degraded",
            "provider": "vm",
            "hypervisor": "vmware",
            "features": {"gpu_inference": False},
        },
    )
    ct = build(
        UBUNTU_2404,
        system(),
        no_systemd("bash"),
        expected={"outcome": "unsupported", "env_class": "container", "provider": None},
    )
    ct.files["/.dockerenv"] = ""
    ct.cmd("systemd-detect-virt", "docker\n")
    ct.cmd("systemd-detect-virt --container", "docker\n")
    ct.cmd("systemd-detect-virt --vm", "none\n", rc=1)
    ct.files["/proc/1/cgroup"] = (
        "0::/docker/0000000000000000000000000000000000000000000000000000000000000000\n"
    )
    fx["docker-container"] = ct
    sdv_wsl = build(
        *base_ubuntu,
        wsl2(),
        expected={"env_class": "wsl2", "provider": "wsl2", "outcome": "degraded"},
    )
    fx["systemd-detect-virt-reports-wsl"] = sdv_wsl
    fx["debian13-baremetal"] = build(
        DEBIAN_13,
        system(kernel="6.12.48+deb13-amd64"),
        apparmor,
        baremetal(),
        kvm(),
        expected={
            "tier": "supported",
            "family": "debian",
            "provider": "baremetal",
            "outcome": "degraded",
            "release_model": "fixed",
        },
    )
    fx["debian12-baremetal"] = build(
        DEBIAN_12,
        system(kernel="6.1.0-40-amd64"),
        apparmor,
        baremetal(),
        kvm(),
        expected={
            "tier": "unsupported",
            "outcome": "unsupported",
            "unsupported_contains": ["Debian 12"],
        },
    )
    fx["fedora-baremetal-nvidia"] = build(
        FEDORA_44,
        system(kernel="6.16.9-200.fc44.x86_64"),
        selinux_enforcing,
        baremetal(),
        kvm(),
        nvidia_pci("loaded"),
        expected={
            "tier": "tested",
            "family": "redhat",
            "provider": "baremetal",
            "outcome": "supported",
            "release_model": "fast",
            "features": FULL_FEATURES,
        },
    )
    fx["almalinux9-kvm-vm"] = build(
        ALMA_9,
        system(kernel="5.14.0-570.el9.x86_64"),
        selinux_enforcing,
        KVM_VM,
        expected={
            "tier": "tested",
            "family": "redhat",
            "provider": "vm",
            "hypervisor": "kvm",
            "outcome": "degraded",
        },
    )
    fx["opensuse-tumbleweed-baremetal"] = build(
        TUMBLEWEED,
        system(kernel="6.16.8-1-default"),
        apparmor,
        baremetal(),
        kvm(),
        expected={
            "tier": "tested",
            "family": "suse",
            "provider": "baremetal",
            "release_model": "rolling",
            "warnings_contain": ["Rolling release"],
        },
    )
    fx["arch-baremetal"] = build(
        ARCH,
        system(kernel="6.16.10-arch1-1"),
        baremetal(),
        kvm(),
        expected={
            "tier": "tested",
            "family": "archlinux",
            "provider": "baremetal",
            "release_model": "rolling",
            "warnings_contain": ["Rolling release"],
        },
    )
    fx["linuxmint22-baremetal"] = build(
        MINT_22,
        system(),
        apparmor,
        baremetal(),
        kvm(),
        expected={
            "tier": "experimental",
            "family": "debian",
            "provider": "baremetal",
            "parent_release": "noble",
        },
    )
    fx["manjaro-baremetal"] = build(
        MANJARO,
        system(kernel="6.12.50-1-MANJARO"),
        baremetal(),
        kvm(),
        expected={"tier": "experimental", "family": "archlinux", "provider": "baremetal"},
    )
    fx["oraclelinux9"] = build(
        ORACLE_9,
        system(kernel="5.15.0-310.184.5.el9uek.x86_64"),
        selinux_enforcing,
        baremetal(),
        kvm(),
        expected={"tier": "experimental", "family": "redhat", "parent_release": "9"},
    )
    sb = build(
        FEDORA_SILVERBLUE,
        system(kernel="6.16.9-200.fc44.x86_64"),
        selinux_enforcing,
        baremetal(),
        kvm(),
        expected={
            "outcome": "unsupported",
            "conflicts": ["immutable_os"],
            "unsupported_contains": ["vm provider"],
        },
    )
    sb.files["/run/ostree-booted"] = ""
    fx["fedora-silverblue"] = sb
    nx = build(
        NIXOS,
        system(),
        baremetal(),
        kvm(),
        expected={"outcome": "unsupported", "conflicts": ["immutable_os"]},
    )
    nx.files["/etc/NIXOS"] = ""
    fx["nixos"] = nx
    al = build(
        ALPINE,
        system(kernel="6.12.50-0-lts"),
        no_systemd("init"),
        baremetal(),
        expected={"outcome": "unsupported", "conflicts": ["non_systemd_init", "musl_libc"]},
    )
    al.files["/lib/ld-musl-x86_64.so.1"] = ""
    fx["alpine"] = al
    fx["devuan"] = build(
        DEVUAN,
        system(kernel="6.12.48+deb13-amd64"),
        no_systemd("init"),
        baremetal(),
        kvm(),
        expected={"outcome": "unsupported", "conflicts": ["non_systemd_init"]},
    )
    fx["rhel8-cgroup-v1"] = build(
        RHEL_8,
        system(kernel="4.18.0-553.el8_10.x86_64"),
        cgroup_v1,
        selinux_enforcing,
        baremetal(),
        kvm(),
        expected={
            "outcome": "unsupported",
            "tier": "unsupported",
            "unsupported_contains": ["systemd.unified_cgroup_hierarchy=1"],
        },
    )
    fx["fedora-selinux-enforcing-firewalld"] = build(
        FEDORA_44,
        system(kernel="6.16.9-200.fc44.x86_64"),
        selinux_enforcing,
        active("firewalld"),
        baremetal(),
        kvm(),
        nvidia_pci("loaded"),
        expected={
            "outcome": "supported",
            "tier": "tested",
            "firewall_backend": "firewalld",
            "mac": ["selinux", "enforcing"],
            "conflicts": [],
        },
    )
    fx["ubuntu2404-ufw-active"] = build(
        *base_ubuntu,
        active("ufw"),
        baremetal(),
        kvm(),
        nvidia_pci("loaded"),
        expected={
            "outcome": "supported",
            "firewall_backend": "ufw",
            "conflicts": [],
            "mac": ["apparmor", "enabled"],
        },
    )
    arm = build(
        *base_ubuntu,
        baremetal(),
        kvm(),
        expected={
            "outcome": "degraded",
            "tier": "experimental",
            "features": {"gpu_inference": False},
        },
    )
    arm.data["machine"] = "aarch64"
    fx["ubuntu2404-aarch64-no-gpu"] = arm
    fx["ubuntu2404-no-btrfs-no-dmthin"] = build(
        *base_ubuntu,
        no_storage,
        baremetal(),
        kvm(),
        nvidia_pci("loaded"),
        expected={
            "outcome": "unsupported",
            "storage_backend": None,
            "unsupported_contains": ["No quota-capable storage"],
        },
    )
    fx["ubuntu2404-cgroup-v1"] = build(
        *base_ubuntu,
        cgroup_v1,
        baremetal(),
        kvm(),
        nvidia_pci("loaded"),
        expected={"outcome": "unsupported", "unsupported_contains": ["cgroup2_unified"]},
    )
    fx["ubuntu2404-low-memory"] = build(
        UBUNTU_2404,
        system(memory_gib=8),
        apparmor,
        baremetal(),
        kvm(),
        nvidia_pci("loaded"),
        expected={"outcome": "unsupported", "unsupported_contains": ["memory_gib"]},
    )
    conflicting = build(
        *base_ubuntu,
        baremetal(vendor="VMware, Inc.", product="VMware Virtual Platform"),
        expected={"confidence": "low", "warnings_contain": ["confidence is low"]},
    )
    conflicting.files["/proc/cpuinfo"] = str(conflicting.files["/proc/cpuinfo"]).replace(
        "lm vmx", "lm vmx hypervisor"
    )
    fx["conflicting-signals"] = conflicting
    return fx


# ------------------------------------------------------------------------ write


def write_fixture(name: str, f: Fixture, base: str = FIXTURES_DIR) -> None:
    """Write one fixture directory (replacing any previous content)."""
    root = os.path.join(base, name)
    if os.path.isdir(root):
        shutil.rmtree(root)
    os.makedirs(root)
    for path, content in sorted(f.files.items()):
        target = os.path.join(root, path.lstrip("/"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        mode = "wb" if isinstance(content, bytes) else "w"
        with open(target, mode) as handle:
            handle.write(content)
    for d in sorted(set(f.dirs)):
        target = os.path.join(root, d.lstrip("/"))
        os.makedirs(target, exist_ok=True)
        if not os.listdir(target):
            with open(os.path.join(target, ".fixture-keep"), "w"):
                pass
    meta = copy.deepcopy(f.meta)
    meta["char_devices"] = sorted(meta["char_devices"])
    meta["rw_openable"] = sorted(meta["rw_openable"])
    commands = {"commands": dict(sorted(f.commands.items())), **dict(sorted(f.data.items()))}
    files: list[tuple[str, Any]] = [
        ("fixture.json", meta),
        ("commands.json", commands),
        ("expected.json", f.expected),
    ]
    if f.args:
        files.append(("args.json", f.args))
    for fname, data in files:
        with open(os.path.join(root, fname), "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")


def write_all(base: str = FIXTURES_DIR) -> list[str]:
    """Write every fixture; remove fixture directories that no longer exist."""
    defs = fixtures()
    os.makedirs(base, exist_ok=True)
    for name in os.listdir(base):
        if name not in defs and os.path.isdir(os.path.join(base, name)):
            shutil.rmtree(os.path.join(base, name))
    for name, f in defs.items():
        write_fixture(name, f, base)
    return sorted(defs)


if __name__ == "__main__":
    if sys.argv[1:] != ["--write"]:
        sys.stderr.write("usage: fixture_defs.py --write\n")
        sys.exit(2)
    for written in write_all():
        print(written)
