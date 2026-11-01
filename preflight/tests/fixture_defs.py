"""Definitions of the preflight fixture roots.

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
ANY_V4 = "0.0.0.0"  # noqa: S104 - a recorded listening address, nothing binds
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
        plan: The operator's choices (``plan.json``, passed as ``--plan``; written
            when not empty), merged with the configured defaults.
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
    plan: dict[str, Any] = field(default_factory=dict)

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
    memory_gib: float = 64,
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
        disks(root_free=400, srv_free=500)(f)

    return apply


def disks(root_free: float, srv_free: float | None = None, fstype: str = "ext4") -> Layer:
    """Answer ``df`` for the root file system (/, /var/lib) and /srv (free GiB).

    /opt/pinka, Docker's data root and /var/lib/incus do not exist yet, so
    preflight measures their nearest existing ancestors, / and /var/lib.
    Without ``srv_free`` /srv is on the root file system too.
    """
    srv_mount = "/" if srv_free is None else "/srv"

    def apply(f: Fixture) -> None:
        for path, mount, free in (("/", "/", root_free), ("/var/lib", "/", root_free),
                                  ("/srv", srv_mount, root_free if srv_free is None else srv_free)):  # fmt: skip
            f.cmd(
                f"df --output=target,fstype,size,avail -B1 {path}",
                f"Mounted on Type 1B-blocks Avail\n{mount} {fstype} {1000 * GIB} "
                f"{int(free * GIB)}\n",
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

QUERY_GPU = "--query-gpu=name,memory.total,driver_version,compute_cap --format=csv,noheader"
# Driver versions and the CUDA version each supports (`nvidia-smi --version`).
CURRENT_DRIVER = ("580.82.07", "13.0")
OLD_DRIVER = ("570.172.08", "12.8")
OLD_WINDOWS_DRIVER = ("566.36", "12.7")


# GPUs as ``nvidia-smi --query-gpu`` names them: name, memory, compute capability.
RTX_4070 = ("NVIDIA GeForce RTX 4070", "12282 MiB", "8.9")
# Compute capability 7.0: below the pinned vLLM image's kernels (V7).
TITAN_V = ("NVIDIA TITAN V", "12288 MiB", "7.0")


def nvsmi_query(driver: str, gpu: tuple[str, str, str] = RTX_4070) -> str:
    """``nvidia-smi --query-gpu`` output of a GPU (an RTX 4070) with the given driver."""
    name, memory, capability = gpu
    return f"{name}, {memory}, {driver}, {capability}\n"


def nvsmi_version(driver: str, cuda: str) -> str:
    """``nvidia-smi --version`` output (the format of drivers 590 and 595)."""
    return (
        f"NVIDIA-SMI version  : {driver}\n"
        f"NVML version        : {driver.rsplit('.', 1)[0]}\n"
        f"DRIVER version      : {driver}\n"
        f"CUDA Version        : {cuda}\n"
    )


# PCI device IDs from NVIDIA's supported-gpus.json of driver 580.178.04 (the
# GPU list of versions.lock.yml): the RTX 4070 and the RTX 2080 Ti (Turing) are
# supported by the open kernel module, the GeForce 1030 (Pascal) only by the
# proprietary module, the GeForce 730 (Kepler) only by the 470 legacy branch.
RTX_4070_DEVID = "0x2786"
RTX_2080_TI_DEVID = "0x1e04"
PASCAL_1030_DEVID = "0x1d01"
KEPLER_730_DEVID = "0x1287"
OPEN_DRIVERS = ["580.178.04"]


def nvidia_pci(
    driver: str = "loaded",
    device: str = "0000:01:00.0",
    version: tuple[str, str] = CURRENT_DRIVER,
    gpu: tuple[str, str, str] = RTX_4070,
    devid: str = RTX_4070_DEVID,
) -> Layer:
    """An NVIDIA GPU on the PCI bus with the given driver state, version and device ID."""

    def apply(f: Fixture) -> None:
        f.files[f"/sys/bus/pci/devices/{device}/vendor"] = "0x10de\n"
        f.files[f"/sys/bus/pci/devices/{device}/class"] = "0x030000\n"
        f.files[f"/sys/bus/pci/devices/{device}/device"] = f"{devid}\n"
        if driver == "loaded":
            f.files["/proc/driver/nvidia/version"] = (
                f"NVRM version: NVIDIA UNIX Open Kernel Module for x86_64  {version[0]}\n"
            )
            f.data.setdefault("which", {})["nvidia-smi"] = "/usr/bin/nvidia-smi"
            f.cmd(f"nvidia-smi {QUERY_GPU}", nvsmi_query(version[0], gpu))
            f.cmd("nvidia-smi --version", nvsmi_version(*version))
            f.cmd("modinfo nvidia", "filename: /lib/modules/x/nvidia.ko\n")
        elif driver == "installed":
            f.cmd("modinfo nvidia", "filename: /lib/modules/x/nvidia.ko\n")
        else:
            f.cmd("modinfo nvidia", "", rc=1, stderr="modinfo: ERROR: Module nvidia not found.\n")

    return apply


def windows_gpu(version: tuple[str, str] = CURRENT_DRIVER) -> Layer:
    """GPU shared by the Windows NVIDIA driver of the given version."""

    def apply(f: Fixture) -> None:
        f.chardev("/dev/dxg", rw=True)
        f.files["/usr/lib/wsl/lib/libcuda.so.1"] = ""
        f.files["/usr/lib/wsl/lib/nvidia-smi"] = ""
        f.cmd(f"/usr/lib/wsl/lib/nvidia-smi {QUERY_GPU}", nvsmi_query(version[0]))
        f.cmd("/usr/lib/wsl/lib/nvidia-smi --version", nvsmi_version(*version))

    return apply


wsl_gpu = windows_gpu()


# ------------------------------------------------------------- ports, addresses

TCP_HEADER = (
    "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt"
    "   uid  timeout inode\n"
)


def port_in_use(port: int, pid: str = "4242", comm: str = "example-server") -> Layer:
    """A process outside the lab listens on a lab port on 127.0.0.1."""

    def apply(f: Fixture) -> None:
        f.files["/proc/net/tcp"] = TCP_HEADER + (
            f"   0: 0100007F:{port:04X} 00000000:0000 0A 00000000:00000000 00:00000000"
            " 00000000     0        0 40001 1 0000000000000000 100 0 0 10 0\n"
        )
        f.files[f"/proc/{pid}/comm"] = comm + "\n"
        f.files[f"/proc/{pid}/cgroup"] = f"0::/system.slice/{comm}.service\n"
        f.dirs.append(f"/proc/{pid}/fd")
        f.meta["symlinks"][f"/proc/{pid}/fd/3"] = "socket:[40001]"

    return apply


def sockets(*entries: tuple[str, str, int, str]) -> Layer:
    """Sockets of ``/proc/net``: ``(table, hex address, port, inode)``.

    TCP tables (``tcp``, ``tcp6``) hold listening sockets, UDP tables
    (``udp``, ``udp6``) unconnected ones. Replaces every table it names.
    """

    def apply(f: Fixture) -> None:
        for table in ("tcp", "tcp6", "udp", "udp6"):
            rows = [e for e in entries if e[0] == table]
            if not rows:
                continue
            state = "0A" if table.startswith("tcp") else "07"
            f.files[f"/proc/net/{table}"] = TCP_HEADER + "".join(
                f"   {n}: {address}:{port:04X} {'0' * len(address)}:0000 {state}"
                f" 00000000:00000000 00:00000000 00000000     0        0 {inode} 1"
                " 0000000000000000 100 0 0 10 0\n"
                for n, (_, address, port, inode) in enumerate(rows)
            )

    return apply


def process(
    pid: str,
    comm: str,
    unit: str,
    inodes: tuple[str, ...] = (),
    *,
    uid: str = "0",
    ppid: str = "1",
    environ: bytes = b"",
) -> Layer:
    """A process with its unit, parent, owner, environment and sockets."""

    def apply(f: Fixture) -> None:
        f.files[f"/proc/{pid}/comm"] = comm + "\n"
        f.files[f"/proc/{pid}/cgroup"] = f"0::/system.slice/{unit}\n"
        f.files[f"/proc/{pid}/status"] = (
            f"Name:\t{comm}\nPPid:\t{ppid}\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n"
        )
        f.files[f"/proc/{pid}/environ"] = environ or b"PATH=/usr/bin\0"
        f.dirs.append(f"/proc/{pid}/fd")
        for n, inode in enumerate(inodes, 3):
            f.meta["symlinks"][f"/proc/{pid}/fd/{n}"] = f"socket:[{inode}]"

    return apply


def systemd_sockets(*inodes: str, units: str) -> Layer:
    """Sockets systemd (PID 1) holds for socket activation, and ``list-sockets``."""

    def apply(f: Fixture) -> None:
        f.dirs.append("/proc/1/fd")
        for n, inode in enumerate(inodes, 3):
            f.meta["symlinks"][f"/proc/1/fd/{n}"] = f"socket:[{inode}]"
        f.cmd("systemctl list-sockets --all --no-legend --full", units)

    return apply


def passwd(*accounts: tuple[str, int, str]) -> Layer:
    """``/etc/passwd`` with root, daemon and nobody, plus ``(name, uid, shell)``."""

    def apply(f: Fixture) -> None:
        lines = ["root:x:0:0:root:/root:/bin/bash", "daemon:x:1:1::/usr/sbin:/usr/sbin/nologin"]
        lines += [f"{name}:x:{uid}:{uid}::/home/{name}:{shell}" for name, uid, shell in accounts]
        lines.append("nobody:x:65534:65534::/nonexistent:/usr/sbin/nologin")
        f.files["/etc/passwd"] = "\n".join(lines) + "\n"

    return apply


def ssh_session(client: str, server: str, port: int, uid: str) -> Layer:
    """Preflight under sudo, started from an SSH login of ``uid``."""
    connection = f"SSH_CONNECTION={client} 51234 {server} {port}".encode()
    return chain(
        process("self", "pinka-preflight", "session-3.scope", ppid="3100"),
        process("3100", "sudo", "session-3.scope", ppid="3000"),
        process(
            "3000", "bash", "session-3.scope", uid=uid, ppid="2990", environ=connection + b"\0"
        ),
    )


def chain(*layers: Layer) -> Layer:
    """Several layers as one."""

    def apply(f: Fixture) -> None:
        for layer in layers:
            layer(f)

    return apply


def routed(network: str, dev: str = "eth0") -> Layer:
    """``ip -j addr`` and ``ip -j route`` with a route to ``network``."""

    def apply(f: Fixture) -> None:
        f.cmd("ip -j addr", json.dumps([{"ifname": "lo", "addr_info": []}]) + "\n")
        f.cmd(
            "ip -j route",
            json.dumps([{"dst": "default", "dev": dev}, {"dst": network, "dev": dev}]) + "\n",
        )

    return apply


# ----------------------------------------------- pre-existing components


def docker_engine(
    version: str = "29.8.2",
    cdi_dirs: str = '["/etc/cdi","/var/run/cdi"]',
    daemon: str | None = None,
) -> Layer:
    """A running Docker Engine that existed before pinka, with Compose and buildx."""

    def apply(f: Fixture) -> None:
        f.data.setdefault("which", {}).update(
            {"dockerd": "/usr/bin/dockerd", "docker": "/usr/bin/docker"}
        )
        f.cmd("dockerd --version", f"Docker version {version}, build 0000000\n")
        f.cmd("systemctl is-active docker.service", "active\n")
        f.cmd("docker info --format '{{json .CDISpecDirs}}'", cdi_dirs + "\n")
        f.cmd("docker compose version", "Docker Compose version v5.6.0\n")
        f.cmd("docker buildx version", "github.com/docker/buildx v0.37.1 0000000\n")
        if daemon is not None:
            f.files["/etc/docker/daemon.json"] = daemon

    return apply


def tailscale_node(
    tags: tuple[str, ...] = ("tag:home",), funnel: bool = False, ssh: bool = False
) -> Layer:
    """A Tailscale node logged in to the owner's own tailnet before pinka."""

    def apply(f: Fixture) -> None:
        f.data.setdefault("which", {})["tailscale"] = "/usr/bin/tailscale"
        f.cmd("tailscale version", "1.104.1\n  tailscale commit: 0000000\n")
        f.cmd("systemctl is-active tailscaled.service", "active\n")
        status = {
            "BackendState": "Running",
            "Self": {
                "HostName": "example-desktop",
                "Tags": list(tags),
                "KeyExpiry": "2027-04-01T00:00:00Z",
            },
        }
        f.cmd("tailscale status --json", json.dumps(status) + "\n")
        serve = {"AllowFunnel": {"example-desktop.example.com:443": True}} if funnel else {}
        f.cmd("tailscale serve status --json", json.dumps(serve) + "\n")
        f.cmd("tailscale debug prefs", json.dumps({"RunSSH": ssh}) + "\n")

    return apply


def lxd_snap(*networks: tuple[str, str]) -> Layer:
    """LXD from its snap with managed networks (name, IPv4 address)."""

    def apply(f: Fixture) -> None:
        f.cmd(
            "snap list lxd",
            "Name  Version  Rev    Tracking     Publisher   Notes\n"
            "lxd   5.21.3   33110  5.21/stable  canonical*  -\n",
        )
        nets = [{"name": n, "managed": True, "config": {"ipv4.address": a}} for n, a in networks]
        f.cmd("lxc network list --format json", json.dumps(nets) + "\n")
        pools = [
            {"name": "default", "config": {"source": "/var/snap/lxd/common/lxd/disks/default.img"}}
        ]
        f.cmd("lxc storage list --format json", json.dumps(pools) + "\n")

    return apply


def podman(docker_command: bool = False) -> Layer:
    """Podman, optionally with podman-docker providing the docker command."""

    def apply(f: Fixture) -> None:
        which = f.data.setdefault("which", {})
        which["podman"] = "/usr/bin/podman"
        f.cmd("podman --version", "podman version 5.4.2\n")
        f.cmd("systemctl is-active podman.socket", "active\n")
        if docker_command:
            which["docker"] = "/usr/bin/docker"
            f.cmd(
                "docker --version",
                "Emulate Docker CLI using podman. Create /etc/containers/nodocker to quiet msg.\npodman version 5.4.2\n",
            )

    return apply


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


def listener(
    proto: str, address: str, port: int, process: str, unit: str, *, admitted: bool = False
) -> dict[str, Any]:
    """One expected listener of the host profile's ``access``."""
    return {
        "proto": proto,
        "address": address,
        "port": port,
        "process": process,
        "unit": unit,
        "admitted": admitted,
    }


def build(
    *layers: Layer,
    expected: dict[str, Any],
    args: list[str] | None = None,
    plan: dict[str, Any] | None = None,
) -> Fixture:
    """Compose layers into a fixture run with the extra preflight ``args`` and ``plan``."""
    f = Fixture()
    for layer in layers:
        layer(f)
    f.expected = expected
    f.args = list(args or [])
    f.plan = dict(plan or {})
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
    fx["ubuntu2404-baremetal-nvidia-old-driver"] = build(
        *base_ubuntu,
        baremetal(),
        kvm(),
        nvidia_pci("loaded", version=OLD_DRIVER),
        expected={
            "outcome": "degraded",
            "provider": "baremetal",
            "disabled": ["gpu_inference"],
            "features": {
                "gpu_inference": False,
                "gpu_training": True,  # a job brings its own CUDA
                "dcgm_metrics": True,
                "vm_targets": True,
            },
            "remediation_contains": [
                "supports CUDA 12.8, the pinned vLLM image needs 13.0",
                "install a driver branch that supports CUDA 13.0",
            ],
        },
    )
    # V7: compute capability 7.5 at least; no override enables GPU inference below.
    fx["ubuntu2404-baremetal-nvidia-compute-7-0"] = build(
        *base_ubuntu,
        baremetal(),
        kvm(),
        nvidia_pci("loaded", gpu=TITAN_V),
        expected={
            "outcome": "degraded",
            "provider": "baremetal",
            "disabled": ["gpu_inference"],
            "features": {
                "gpu_inference": False,
                "gpu_training": True,  # a job brings its own CUDA
                "dcgm_metrics": True,
                "vm_targets": True,
            },
            "remediation_contains": [
                "compute capability is 7.0, below 7.5",
                "GPU inference is refused on this GPU",
            ],
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
    # With install_nvidia_driver auto, a driver is installed
    # only for a GPU the pinned release's open kernel module supports.
    no_driver = [*base_ubuntu, baremetal(), kvm()]
    unsupported_gpu = {
        "outcome": "degraded",
        "provider": "baremetal",
        "needs_action": [],
        "disabled": ["gpu_inference", "gpu_training"],
        "open_module_drivers": [],
        "install_driver": "auto",
    }
    # Kepler: only the legacy 470 branch supports the GeForce 730.
    kepler = {
        **unsupported_gpu,
        "pci_device": "10de:1287",
        "proprietary_only_drivers": [],
        "warnings_contain": ["No usable GPU: the pinned NVIDIA driver does not support this GPU"],
    }
    fx["ubuntu2404-baremetal-nvidia-kepler"] = build(
        *no_driver, nvidia_pci("none", devid=KEPLER_730_DEVID), expected=kepler
    )
    fx["ubuntu2404-baremetal-nvidia-kepler-secureboot"] = build(
        *no_driver,
        nvidia_pci("none", devid=KEPLER_730_DEVID),
        secure_boot_enabled,
        expected={**kepler, "secure_boot": "enabled"},
    )
    # Pascal: the pinned release supports the GeForce 1030 only with its proprietary module.
    fx["ubuntu2404-baremetal-nvidia-pascal"] = build(
        *no_driver,
        nvidia_pci("none", devid=PASCAL_1030_DEVID),
        expected={
            **unsupported_gpu,
            "pci_device": "10de:1d01",
            "proprietary_only_drivers": OPEN_DRIVERS,
            "warnings_contain": ["only with its proprietary kernel module (Maxwell, Pascal"],
        },
    )
    supported_gpu = {
        "outcome": "degraded",
        "needs_action": [],
        "disabled": ["gpu_inference", "gpu_training"],
        "pci_device": "10de:1e04",
        "open_module_drivers": OPEN_DRIVERS,
        "proprietary_only_drivers": [],
        "install_driver": "auto",
    }
    fx["ubuntu2404-baremetal-nvidia-turing"] = build(
        *no_driver,
        nvidia_pci("none", devid=RTX_2080_TI_DEVID),
        expected={**supported_gpu, "provider": "baremetal", "family": "debian"},
    )
    # One per family: the list belongs to the driver release, not the distribution.
    for name, layers, family in (
        ("debian13", [DEBIAN_13, system(kernel="6.12.48+deb13-amd64"), apparmor], "debian"),
        (
            "fedora",
            [FEDORA_44, system(kernel="6.16.9-200.fc44.x86_64"), selinux_enforcing],
            "redhat",
        ),
        (
            "almalinux9",
            [ALMA_9, system(kernel="5.14.0-570.el9.x86_64"), selinux_enforcing],
            "redhat",
        ),
        ("opensuse-tumbleweed", [TUMBLEWEED, system(kernel="6.16.8-1-default"), apparmor], "suse"),
        ("arch", [ARCH, system(kernel="6.16.10-arch1-1")], "archlinux"),
    ):
        fx[f"{name}-baremetal-nvidia-turing"] = build(
            *layers,
            baremetal(),
            kvm(),
            nvidia_pci("none", devid=RTX_2080_TI_DEVID),
            expected={**supported_gpu, "provider": "baremetal", "family": family},
        )
    # The same host with the operator's choices.
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
            "disabled": ["gpu_inference", "gpu_training"],
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
            "disabled": ["gpu_inference", "gpu_training"],
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
            "disabled": ["gpu_inference", "gpu_training"],
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
    # A driver whose CUDA version is below the pinned vLLM
    # image's makes gpu_inference unavailable; WSL2 points to Windows.
    fx["ubuntu2404-wsl2-gpu-old-driver"] = build(
        *base_ubuntu,
        wsl2(),
        windows_gpu(OLD_WINDOWS_DRIVER),
        wsl_kvm,
        expected={
            "outcome": "degraded",
            "provider": "wsl2",
            "env_class": "wsl2",
            "disabled": ["gpu_inference"],  # the GPU still trains
            "features": {"gpu_inference": False, "vm_targets": True},
            "remediation_contains": [
                "supports CUDA 12.7, the pinned vLLM image needs 13.0",
                "update the NVIDIA driver on Windows",
            ],
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
    # A lab port taken by another program, and a VM
    # collector address another network already routes.
    fx["ubuntu2404-lab-port-in-use"] = build(
        *base_ubuntu,
        baremetal(),
        kvm(),
        nvidia_pci("loaded"),
        port_in_use(18700),
        expected={"outcome": "needs_action", "needs_action": ["lab_port_in_use"]},
    )
    fx["ubuntu2404-vm-collector-address-routed"] = build(
        *base_ubuntu,
        baremetal(),
        kvm(),
        nvidia_pci("loaded"),
        routed("198.18.0.0/15"),
        expected={"outcome": "needs_action", "needs_action": ["vm_collector_address_conflict"]},
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
    # Memory thresholds, each just below and just above
    # 0.94 times its nominal value. The numbers follow preflight/requirements.yml and
    # the incus role's profiles; test_requirements.py checks them.
    fx["ubuntu2404-low-memory"] = build(
        UBUNTU_2404,
        system(memory_gib=7.51),  # floor 8 GiB: 7.52 GiB
        apparmor,
        baremetal(),
        kvm(),
        nvidia_pci("loaded"),
        expected={"outcome": "unsupported", "unsupported_contains": ["below the floor of 8 GiB"]},
    )
    # The defaults require the floor; with a GPU they recommend 13.2 GiB for the
    # rest and a training job at the computed cap, here its floor of 4 GiB.
    fx["ubuntu2404-memory-above-floor"] = build(
        UBUNTU_2404,
        system(memory_gib=7.53),
        apparmor,
        baremetal(),
        kvm(),
        nvidia_pci("loaded"),
        expected={
            "outcome": "degraded",
            "degraded_contains": ["below the 17.2 GiB recommended", "a training job 4.0 GiB"],
            "switches": {"training.memory_max": "4G", "training.memory_max_basis": "computed"},
        },
    )
    # Four targets require 10.1 GiB: 9.49 GiB at the tolerance.
    four_targets = {"concurrent_targets": 4}
    fx["ubuntu2404-memory-below-required"] = build(
        UBUNTU_2404,
        system(memory_gib=9.48),
        apparmor,
        baremetal(),
        kvm(),
        plan=four_targets,
        expected={
            "outcome": "unsupported",
            "unsupported_contains": ["below the 10.1 GiB these choices require",
                                     "incus_concurrent_targets 3"],
        },
    )  # fmt: skip
    fx["ubuntu2404-memory-above-required"] = build(
        UBUNTU_2404,
        system(memory_gib=9.51),
        apparmor,
        baremetal(),
        kvm(),
        plan=four_targets,
        expected={"outcome": "degraded", "degraded_contains": ["recommended for these choices"]},
    )
    # With a GPU the defaults recommend 13.2 GiB for the rest (VM targets) and a
    # training job at the computed cap (MemTotal minus the rest, 4 to 24 GiB):
    # below 17.2 GiB the cap is its floor, 17.2 GiB at the tolerance is 16.17 GiB.
    fx["ubuntu2404-memory-below-recommended"] = build(
        UBUNTU_2404,
        system(memory_gib=16.16),
        apparmor,
        baremetal(),
        kvm(),
        nvidia_pci("loaded"),
        expected={
            "outcome": "degraded",
            "degraded_contains": ["training_caps.memory_max 3G", "training: off"],
        },
    )
    fx["ubuntu2404-memory-above-recommended"] = build(
        UBUNTU_2404,
        system(memory_gib=16.18),
        apparmor,
        baremetal(),
        kvm(),
        nvidia_pci("loaded"),
        expected={"outcome": "supported", "switches": {"training.memory_max": "4G"}},
    )
    # Above it the cap follows the host: 31.25 GiB (a nominal 32 GiB machine)
    # leaves 18 GiB for a job, and the recommended value still fits.
    fx["ubuntu2404-training-cap-computed"] = build(
        UBUNTU_2404,
        system(memory_gib=31.25),
        apparmor,
        baremetal(),
        kvm(),
        nvidia_pci("loaded"),
        expected={
            "outcome": "supported",
            "switches": {"training.runs": "gpu", "training.memory_max": "18G"},
        },
    )
    # An explicit cap wins over the computed one.
    fx["ubuntu2404-training-cap-configured"] = build(
        UBUNTU_2404,
        system(memory_gib=31.2),
        apparmor,
        baremetal(),
        kvm(),
        nvidia_pci("loaded"),
        plan={"training": {"mode": "auto", "memory_max_gib": 24, "run_storage_max_gib": 16}},
        expected={
            "outcome": "degraded",
            "degraded_contains": ["training_caps.memory_max 19G", "training: off"],
            "switches": {"training.memory_max": "24G", "training.memory_max_basis": "configured"},
        },
    )
    # A served model of 5.7 GiB: vLLM needs its files and 3 GiB, 15.8 GiB in
    # all (14.85 GiB at the tolerance); a nominal 16 GiB machine passes.
    model = {
        "model": {
            "id": "example-model",
            "hf_repo": "example-org/example-model",
            "revision": "0" * 40,
            "size_bytes": int(5.7 * GIB),
        }
    }
    fx["ubuntu2404-gpu-model-below-required"] = build(
        UBUNTU_2404,
        system(memory_gib=14.84),
        apparmor,
        baremetal(),
        kvm(),
        nvidia_pci("loaded"),
        plan=model,
        expected={
            "outcome": "unsupported",
            "unsupported_contains": ["vLLM serving example-model", "a model of at most 5.7 GiB"],
        },
    )
    fx["ubuntu2404-gpu-model-above-required"] = build(
        UBUNTU_2404,
        system(memory_gib=14.86),
        apparmor,
        baremetal(),
        kvm(),
        nvidia_pci("loaded"),
        plan=model,
        expected={
            "outcome": "degraded",
            "degraded_contains": ["vLLM serving example-model or a training job 10.7 GiB"],
        },
    )
    # The switches: a GPU host with inference and training off counts
    # neither, and a host without a GPU is not degraded for features switched off.
    switched_off = {
        "inference": "off",
        "training": {"mode": "off", "memory_max_gib": None, "run_storage_max_gib": 16},
    }
    fx["ubuntu2404-gpu-switched-off"] = build(
        UBUNTU_2404,
        system(memory_gib=13.0),
        apparmor,
        baremetal(),
        kvm(),
        nvidia_pci("loaded"),
        plan={**switched_off, **model},
        expected={
            "outcome": "supported",
            "features": {"gpu_inference": True, "gpu_training": True},
            "switches": {"inference.serves": False, "training.runs": "off"},
        },
    )
    fx["ubuntu2404-no-gpu-switched-off"] = build(
        UBUNTU_2404,
        system(memory_gib=13.0),
        apparmor,
        baremetal(),
        kvm(),
        plan=switched_off,
        expected={
            "outcome": "supported",
            "disabled": [],
            "notes_contain": ["not used: inference: off", "not used: training: off"],
        },
    )
    # training: cpu runs the CPU-only variant without a GPU, and counts it.
    fx["ubuntu2404-no-gpu-training-cpu"] = build(
        UBUNTU_2404,
        system(memory_gib=13.0),
        apparmor,
        baremetal(),
        kvm(),
        plan={"training": {"mode": "cpu", "memory_max_gib": None, "run_storage_max_gib": 16}},
        expected={
            "outcome": "degraded",
            "disabled": ["gpu_inference"],
            "degraded_contains": ["a training job (CPU-only variant) 4.0 GiB", "training: off"],
            "switches": {"training.runs": "cpu", "training.memory_max": "4G"},
        },
    )
    # Disk known at install: the 300 GiB loop pool on a root file system with
    # 100 GiB free, and the model on a research space with 3 GiB free.
    fx["ubuntu2404-disk-shortfall"] = build(
        UBUNTU_2404,
        system(),
        disks(root_free=100, srv_free=3),
        apparmor,
        baremetal(),
        kvm(),
        nvidia_pci("loaded"),
        plan={**model, "pool": {"source": "loop", "size_gib": 300}},
        expected={
            "outcome": "unsupported",
            "unsupported_contains": [
                "the Incus pool (loop file): 300.0 GiB, missing 232.3 GiB on /",
                "model example-model: 5.7 GiB, missing 5.7 GiB on /srv",
            ],
        },
    )
    # incus_pool_size: auto on one file system: the golden images and two
    # VM targets with their probe clients, + 50 %, is 66 GiB with 144 or 386 GiB
    # free; with 60 GiB free the space left after pinka's other needs (14.6 GiB
    # at install, 15.3 GiB of stores at their retention) sets the 30 GiB floor.
    for free, size in ((144, 66), (386, 66), (60, 30)):
        fx[f"ubuntu2404-pool-auto-{free}"] = build(
            UBUNTU_2404,
            system(),
            disks(root_free=free),
            apparmor,
            baremetal(),
            kvm(),
            expected={
                "outcome": "degraded",
                "disabled": ["gpu_inference", "gpu_training"],
                "pool": {"size_gib": size, "basis": "computed"},
            },
        )
    # Components that existed before pinka
    # are used as they are; preflight stops where the lab could not run with
    # them unchanged.
    host = [*base_ubuntu, baremetal(), kvm(), nvidia_pci("loaded")]
    fx["ubuntu2404-baremetal-docker-preexisting"] = build(
        *host,
        docker_engine(),
        expected={
            "outcome": "supported",
            "conflicts": [],
            "needs_action": [],
            "components": {"docker": "pre-existing"},
            "notes_contain": ["Docker Engine 29.8.2 is installed already"],
        },
    )
    fx["ubuntu2404-baremetal-docker-cdi-off"] = build(
        *host,
        docker_engine(cdi_dirs="[]", daemon='{"features": {"cdi": false}}\n'),
        expected={"outcome": "needs_action", "needs_action": ["docker_cdi_off"]},
    )
    fx["ubuntu2404-baremetal-tailscale-adopted"] = build(
        *host,
        tailscale_node(),
        expected={
            "outcome": "supported",
            "needs_action": [],
            "components": {"tailscale": "pre-existing"},
            "notes_contain": ["pinka uses the node as it is"],
        },
    )
    # Pre-existing access: what was reachable
    # before the install, the access path of the session, dedicated or shared.
    lan_sshd = [
        sockets(("tcp", "00000000", 22, "50001"), ("tcp6", "0" * 32, 22, "50002")),
        systemd_sockets(
            "50001",
            "50002",
            units="0.0.0.0:22 ssh.socket ssh.service\n[::]:22 ssh.socket ssh.service\n",
        ),
    ]
    fx["ubuntu2404-access-dedicated"] = build(
        *host,
        sockets(
            ("tcp", "00000000", 22, "50001"),
            ("tcp6", "0" * 32, 22, "50002"),
            ("udp", "00000000", 68, "50003"),
        ),
        lan_sshd[1],
        process("610", "dhcpcd", "dhcpcd.service", ("50003",)),
        passwd(("researcher1", 1000, "/bin/bash")),
        ssh_session("192.0.2.50", "192.0.2.10", 22, uid="1000"),
        args=["--researcher", "researcher1"],
        expected={
            "outcome": "supported",
            "access": {
                "host": "dedicated",
                "session": {
                    "via": "ssh",
                    "address": "192.0.2.10",
                    "port": 22,
                    "user": "researcher1",
                    "tailnet": False,
                },
                "listeners": [
                    listener("tcp", ANY_V4, 22, "sshd", "ssh.socket"),
                    listener("tcp", "::", 22, "sshd", "ssh.socket"),
                    listener("udp", ANY_V4, 68, "dhcpcd", "dhcpcd.service", admitted=True),
                ],
                "accounts": ["researcher1"],
                "reasons": [],
            },
        },
    )
    fx["ubuntu2404-access-shared"] = build(
        *host,
        sockets(("tcp", "00000000", 22, "51001"), ("tcp", "00000000", 8080, "51002")),
        process("700", "sshd", "ssh.service", ("51001",)),
        process("710", "example-web", "example-web.service", ("51002",)),
        passwd(("researcher1", 1000, "/bin/bash"), ("owner1", 1001, "/bin/bash")),
        ssh_session("192.0.2.50", "192.0.2.10", 22, uid="1001"),
        args=["--researcher", "researcher1"],
        expected={
            "outcome": "supported",
            "access": {
                "host": "shared",
                "session": {
                    "via": "ssh",
                    "address": "192.0.2.10",
                    "port": 22,
                    "user": "owner1",
                    "tailnet": False,
                },
                "listeners": [
                    listener("tcp", ANY_V4, 22, "sshd", "ssh.service"),
                    listener("tcp", ANY_V4, 8080, "example-web", "example-web.service"),
                ],
                "accounts": ["owner1", "researcher1"],
                "reasons": [
                    "tcp/8080 on 0.0.0.0 (example-web)",
                    "login account owner1 is not a researcher",
                ],
            },
            "notes_contain": ["shared host (tcp/8080 on 0.0.0.0 (example-web);"],
        },
    )
    fx["ubuntu2404-access-console"] = build(
        *host,
        *lan_sshd,
        passwd(("researcher1", 1000, "/bin/bash")),
        args=["--researcher", "researcher1"],
        expected={
            "outcome": "supported",
            "access": {
                "host": "dedicated",
                "session": {"via": "console"},
                "accounts": ["researcher1"],
            },
        },
    )
    fx["ubuntu2404-baremetal-tailscale-funnel"] = build(
        *host,
        tailscale_node(funnel=True),
        expected={"outcome": "needs_action", "needs_action": ["tailscale_funnel_on"]},
    )
    fx["ubuntu2404-baremetal-tailscale-ssh"] = build(
        *host,
        tailscale_node(ssh=True),
        expected={"outcome": "needs_action", "needs_action": ["tailscale_ssh_on"]},
    )
    fx["ubuntu2404-baremetal-lxd"] = build(
        *host,
        docker_engine(),
        lxd_snap(("lxdbr0", "10.150.12.1/24")),
        expected={
            "outcome": "supported",
            "conflicts": [],
            "needs_action": [],
            "components": {"lxd": "pre-existing", "docker": "pre-existing"},
            "notes_contain": ["LXD 5.21.3 is installed: pinka leaves it as it is"],
        },
    )
    fx["ubuntu2404-baremetal-lxd-clash"] = build(
        *host,
        lxd_snap(("lxdbr0", "172.30.8.1/24")),
        expected={"outcome": "needs_action", "needs_action": ["lxd_incus_clash"]},
    )
    fx["ubuntu2404-baremetal-podman"] = build(
        *host,
        podman(),
        expected={
            "outcome": "supported",
            "needs_action": [],
            "components": {"podman": "pre-existing"},
            "notes_contain": ["Podman 5.4.2 is installed: pinka leaves it as it is"],
        },
    )
    fx["ubuntu2404-baremetal-podman-docker"] = build(
        *host,
        podman(docker_command=True),
        expected={"outcome": "needs_action", "needs_action": ["podman_docker"]},
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
    if f.plan:
        files.append(("plan.json", {"schema_version": 1, **f.plan}))
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
