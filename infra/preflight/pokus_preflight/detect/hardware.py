"""CPU, memory, disk and block device detection (BUILD_PROMPT §2.2)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from pokus_preflight.fs import FileSystem
from pokus_preflight.runner import Runner

GIB = 1024**3
SECTOR = 512
IGNORED_BLOCK_PREFIXES = ("loop", "ram", "zram", "nbd")


@dataclass
class DiskInfo:
    """Free space of the filesystem that holds a path.

    Attributes:
        path: The path asked for.
        checked_path: The nearest existing ancestor that was measured.
        fstype: Filesystem type.
        size_gib: Filesystem size.
        free_gib: Available space as reported by ``df``.
    """

    path: str
    checked_path: str
    fstype: str | None
    size_gib: float | None
    free_gib: float | None


@dataclass
class BlockDevice:
    """A block device from ``/sys/block`` (informational only).

    Attributes:
        name: Kernel name.
        size_gib: Size.
        rotational: Rotating disk.
        removable: Removable medium.
        model: Model string, if exposed.
    """

    name: str
    size_gib: float | None
    rotational: bool | None
    removable: bool | None
    model: str | None


@dataclass
class Hardware:
    """Hardware facts.

    Attributes:
        cpu_model: First ``model name`` in ``/proc/cpuinfo``.
        cpu_threads: Logical CPUs (``os.cpu_count()``).
        cpu_quota_threads: CPUs allowed by ``/sys/fs/cgroup/cpu.max``, if limited.
        cpu_threads_effective: The smaller of the two.
        vmx: Intel VT-x flag.
        svm: AMD-V flag.
        memory_gib: ``MemTotal``.
        swap_gib: ``SwapTotal``.
        disks: Free space per role (``var_lib``, ``srv_pokus``).
        block_devices: Block devices (informational).
    """

    cpu_model: str | None
    cpu_threads: int | None
    cpu_quota_threads: float | None
    cpu_threads_effective: int | None
    vmx: bool
    svm: bool
    memory_gib: float | None
    swap_gib: float | None
    disks: dict[str, DiskInfo] = field(default_factory=dict)
    block_devices: list[BlockDevice] = field(default_factory=list)


def _gib(value: float) -> float:
    return round(value / GIB, 1)


def parse_meminfo(text: str) -> dict[str, int]:
    """Parse ``/proc/meminfo`` into bytes.

    Args:
        text: File content.

    Returns:
        Field name to size in bytes.
    """
    result: dict[str, int] = {}
    for line in text.splitlines():
        parts = line.replace(":", " ").split()
        if len(parts) >= 2 and parts[1].isdigit():  # noqa: PLR2004
            unit = parts[2].lower() if len(parts) > 2 else ""  # noqa: PLR2004
            result[parts[0]] = int(parts[1]) * (1024 if unit == "kb" else 1)
    return result


def parse_cpu_max(text: str | None) -> float | None:
    """Parse a cgroup v2 ``cpu.max`` value into a number of CPUs.

    Args:
        text: File content such as ``max 100000`` or ``200000 100000``.

    Returns:
        Allowed CPUs, or None if unlimited or unreadable.
    """
    if not text:
        return None
    parts = text.split()
    if len(parts) != 2 or parts[0] == "max":  # noqa: PLR2004
        return None
    try:
        return int(parts[0]) / int(parts[1])
    except (ValueError, ZeroDivisionError):
        return None


def nearest_existing(fs: FileSystem, path: str) -> str:
    """Return the path itself or its nearest existing ancestor.

    Args:
        fs: Filesystem view.
        path: Absolute path.

    Returns:
        An existing path (at worst ``/``).
    """
    current = FileSystem.normalize(path)
    while current != "/" and not fs.exists(current):
        current = current.rsplit("/", 1)[0] or "/"
    return current


def disk_info(fs: FileSystem, runner: Runner, path: str) -> DiskInfo:
    """Measure the filesystem that holds ``path`` with ``df``.

    Args:
        fs: Filesystem view.
        runner: Command runner.
        path: Absolute path.

    Returns:
        Disk facts (fields are None if ``df`` failed).
    """
    checked = nearest_existing(fs, path)
    res = runner.run(["df", "--output=fstype,size,avail", "-B1", checked])
    lines = res.stdout.strip().splitlines() if res.ok else []
    if len(lines) >= 2:  # noqa: PLR2004
        parts = lines[-1].split()
        try:
            return DiskInfo(path, checked, parts[0], _gib(int(parts[1])), _gib(int(parts[2])))
        except (IndexError, ValueError):
            pass
    return DiskInfo(path, checked, None, None, None)


def _flag(value: str | None) -> bool | None:
    return None if value is None else value == "1"


def block_devices(fs: FileSystem) -> list[BlockDevice]:
    """List physical-looking block devices from ``/sys/block``.

    Args:
        fs: Filesystem view.

    Returns:
        Block devices without loop, RAM and network block devices.
    """
    devices: list[BlockDevice] = []
    for name in fs.listdir("/sys/block"):
        if name.startswith(IGNORED_BLOCK_PREFIXES):
            continue
        base = f"/sys/block/{name}"
        sectors = fs.read_first_line(f"{base}/size")
        size = _gib(int(sectors) * SECTOR) if sectors and sectors.isdigit() else None
        devices.append(
            BlockDevice(
                name=name,
                size_gib=size,
                rotational=_flag(fs.read_first_line(f"{base}/queue/rotational")),
                removable=_flag(fs.read_first_line(f"{base}/removable")),
                model=fs.read_first_line(f"{base}/device/model") or None,
            )
        )
    return devices


def detect_hardware(fs: FileSystem, runner: Runner, srv_path: str) -> Hardware:
    """Collect hardware facts.

    Args:
        fs: Filesystem view.
        runner: Command runner.
        srv_path: Configured ``/srv/pokus`` location.

    Returns:
        Hardware facts.
    """
    cpuinfo = fs.read_text("/proc/cpuinfo") or ""
    model = next(
        (ln.split(":", 1)[1].strip() for ln in cpuinfo.splitlines() if ln.startswith("model name")),
        None,
    )
    flags_line = next((ln for ln in cpuinfo.splitlines() if ln.startswith("flags")), "")
    flags = set(flags_line.split(":", 1)[-1].split())
    threads = runner.cpu_count()
    quota = parse_cpu_max(fs.read_first_line("/sys/fs/cgroup/cpu.max"))
    effective = threads
    if quota is not None and threads is not None:
        effective = min(threads, math.ceil(quota))
    mem = parse_meminfo(fs.read_text("/proc/meminfo") or "")
    return Hardware(
        cpu_model=model,
        cpu_threads=threads,
        cpu_quota_threads=quota,
        cpu_threads_effective=effective,
        vmx="vmx" in flags,
        svm="svm" in flags,
        memory_gib=_gib(mem["MemTotal"]) if "MemTotal" in mem else None,
        swap_gib=_gib(mem["SwapTotal"]) if "SwapTotal" in mem else None,
        disks={
            "var_lib": disk_info(fs, runner, "/var/lib"),
            "srv_pokus": disk_info(fs, runner, srv_path),
        },
        block_devices=block_devices(fs),
    )
