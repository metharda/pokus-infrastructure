"""GPU detection per environment class.

NVIDIA is implemented behind a ``gpu_backend`` value (``nvidia`` or
``none``). AMD GPUs are reported as present but unsupported.

* ``wsl2``: the GPU comes from the Windows driver through ``/dev/dxg`` and
  ``/usr/lib/wsl/lib``. A Linux NVIDIA kernel driver inside WSL is a blocking
  conflict; installing one is never suggested.
* ``baremetal`` / ``vm``: NVIDIA display or 3D controllers are found on the PCI
  bus; the driver state is not installed, installed but not loaded, or loaded.
  In a VM a GPU exists only with PCI passthrough; its absence is normal. The
  GPU's PCI device ID is looked up in the GPU lists of the pinned driver
  releases (``nvidia-gpus.json``, generated from versions.lock.yml): with
  ``install_nvidia_driver: auto`` a driver is installed only for a GPU the
  release's open kernel module supports.
* other classes: the GPU is not probed.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from pinka_preflight.fs import FileSystem
from pinka_preflight.runner import Runner

NVIDIA_VENDOR = "0x10de"
AMD_VENDOR = "0x1002"
GPU_CLASSES = ("0x0300", "0x0302")
QUERY = ["--query-gpu=name,memory.total,driver_version,compute_cap", "--format=csv,noheader"]
# The CUDA version the driver supports is not a query field: `nvidia-smi
# --version` prints it as "CUDA Version : 13.1" and the banner of a plain
# `nvidia-smi` as "CUDA Version: 13.1" (the nvidia-smi captures of drivers 590
# and 595 in nvidia_gpu_exporter v1.15.1). That exporter's parser also reads
# "CUDA UMD version", the name drivers of the 610 branch and later use, where
# the old line points to it instead of carrying a number.
VERSION_QUERY = ["--version"]
CUDA_VERSION_RE = re.compile(
    r"\bCUDA( UMD)? Version\s*:\s*([0-9]+\.[0-9]+)(?:\.[0-9]+)?(?![0-9.])", re.IGNORECASE
)
WSL_NVIDIA_SMI = "/usr/lib/wsl/lib/nvidia-smi"
# The GPU lists of the pinned driver releases (tools/gen from versions.lock.yml).
NVIDIA_GPUS_JSON = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "nvidia-gpus.json",
)
MIB_PER_GIB = 1024
QUERY_FIELDS = 4


@dataclass
class GpuInfo:
    """GPU facts.

    Attributes:
        probed: The environment class allowed probing.
        present: An NVIDIA GPU is present (WSL: exposed through dxg).
        usable: The GPU can run CUDA workloads now.
        vendor: ``nvidia``, ``amd`` or None.
        backend: ``nvidia`` or ``none``.
        name: GPU name from ``nvidia-smi``.
        count: Number of GPUs reported by ``nvidia-smi``.
        vram_gib: Memory of the first GPU.
        driver: Driver version.
        compute_capability: CUDA compute capability, e.g. ``8.9``.
        driver_cuda: Highest CUDA version the loaded driver supports, e.g.
            ``13.0`` (on WSL2 the Windows driver's).
        path: ``wsl-dxg``, ``native`` or ``passthrough``.
        driver_state: ``windows_driver``, ``not_installed``,
            ``installed_not_loaded`` or ``loaded``.
        driver_installed: A Linux NVIDIA kernel driver is installed.
        driver_loaded: A Linux NVIDIA kernel driver is loaded.
        linux_driver_in_wsl: A Linux NVIDIA kernel driver exists inside WSL.
        unsupported_devices: GPUs found but not supported (for example AMD).
        notes: Human-readable remarks.
        pci_device: The PCI vendor and device ID of the first NVIDIA GPU
            (``10de:2786``); None when not read from the PCI bus.
        open_module_drivers: The pinned driver releases whose open kernel
            module supports that GPU; a driver is installed automatically only
            for one of them.
        proprietary_only_drivers: The pinned driver releases that support that
            GPU only with their proprietary module (Maxwell, Pascal, Volta),
            which is why such a GPU gets no driver.
    """

    probed: bool = False
    present: bool = False
    usable: bool = False
    vendor: str | None = None
    backend: str = "none"
    name: str | None = None
    count: int = 0
    vram_gib: float | None = None
    driver: str | None = None
    compute_capability: str | None = None
    driver_cuda: str | None = None
    path: str | None = None
    driver_state: str | None = None
    driver_installed: bool = False
    driver_loaded: bool = False
    linux_driver_in_wsl: bool = False
    unsupported_devices: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    pci_device: str | None = None
    open_module_drivers: list[str] = field(default_factory=list)
    proprietary_only_drivers: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DriverGpus:
    """The GPU list of one pinned driver release.

    Attributes:
        open_module: PCI device IDs (four upper-case hex digits) its open
            kernel module supports.
        proprietary_only: Device IDs it supports only with its proprietary module.
    """

    open_module: frozenset[str]
    proprietary_only: frozenset[str] = frozenset()


def load_gpu_lists(path: str = NVIDIA_GPUS_JSON) -> dict[str, DriverGpus]:
    """Load the GPU lists of the pinned driver releases.

    Args:
        path: ``nvidia-gpus.json``.

    Returns:
        Driver release to its GPU list.
    """
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    return {
        str(version): DriverGpus(
            frozenset(str(i) for i in lists["open_module"]),
            frozenset(str(i) for i in lists["proprietary_only"]),
        )
        for version, lists in data["drivers"].items()
    }


def parse_nvidia_smi(stdout: str) -> list[tuple[str, float | None, str, str]]:
    """Parse ``nvidia-smi --query-gpu=... --format=csv,noheader`` output.

    Args:
        stdout: Command output, one GPU per line, e.g.
            ``NVIDIA GeForce RTX 4070, 12282 MiB, 560.94, 8.9``.

    Returns:
        ``(name, vram_gib, driver, compute_capability)`` per GPU.
    """
    gpus: list[tuple[str, float | None, str, str]] = []
    for line in stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != QUERY_FIELDS:
            continue
        mem = parts[1].split()
        vram = round(float(mem[0]) / MIB_PER_GIB, 1) if mem and _is_number(mem[0]) else None
        gpus.append((parts[0], vram, parts[2], parts[3]))
    return gpus


def parse_driver_cuda(stdout: str) -> str | None:
    """Parse the CUDA version the driver supports from ``nvidia-smi`` output.

    Args:
        stdout: Output of ``nvidia-smi --version`` or of a plain ``nvidia-smi``.

    Returns:
        ``major.minor``; the ``CUDA UMD version`` line wins over the legacy
        ``CUDA Version`` line; None if neither carries a version number.
    """
    legacy: str | None = None
    for match in CUDA_VERSION_RE.finditer(stdout):
        if match.group(1):
            return match.group(2)
        legacy = legacy or match.group(2)
    return legacy


def _is_number(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


def _apply_query(info: GpuInfo, runner: Runner, program: str) -> bool:
    res = runner.run([program, *QUERY], timeout=20.0)
    gpus = parse_nvidia_smi(res.stdout) if res.ok else []
    if not gpus:
        info.notes.append(f"{program} did not report a GPU")
        return False
    info.name, info.vram_gib, info.driver, info.compute_capability = gpus[0]
    info.count = len(gpus)
    info.driver_cuda = _driver_cuda(runner, program)
    if info.driver_cuda is None:
        info.notes.append(f"{program} did not report the CUDA version the driver supports")
    return True


def _driver_cuda(runner: Runner, program: str) -> str | None:
    for args in (VERSION_QUERY, []):
        res = runner.run([program, *args], timeout=20.0)
        version = parse_driver_cuda(res.stdout) if res.ok else None
        if version:
            return version
    return None


def _driver_in_wsl(fs: FileSystem, runner: Runner) -> bool:
    if fs.exists("/proc/driver/nvidia") or fs.is_dir("/sys/module/nvidia"):
        return True
    res = runner.run(["dkms", "status"])
    return res.ok and "nvidia" in res.stdout.lower()


def detect_wsl2(fs: FileSystem, runner: Runner) -> GpuInfo:
    """Detect a GPU exposed by the Windows driver.

    Args:
        fs: Filesystem view.
        runner: Command runner.

    Returns:
        GPU facts.
    """
    info = GpuInfo(probed=True, path="wsl-dxg", driver_state="windows_driver")
    info.linux_driver_in_wsl = _driver_in_wsl(fs, runner)
    has_dxg = fs.exists("/dev/dxg")
    has_libcuda = bool(fs.glob("/usr/lib/wsl/lib/libcuda.so*"))
    if not has_dxg:
        info.notes.append("/dev/dxg missing: no GPU is shared with WSL")
        return info
    if not has_libcuda:
        info.notes.append("/usr/lib/wsl/lib/libcuda.so* missing: update the Windows NVIDIA driver")
    if _apply_query(info, runner, WSL_NVIDIA_SMI):
        info.present, info.vendor, info.backend = True, "nvidia", "nvidia"
        info.usable = has_libcuda and not info.linux_driver_in_wsl
    return info


def _pci_gpus(fs: FileSystem) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for vendor_path in fs.glob("/sys/bus/pci/devices/*/vendor"):
        device = vendor_path.rsplit("/", 1)[0]
        vendor = (fs.read_first_line(vendor_path) or "").lower()
        pci_class = (fs.read_first_line(f"{device}/class") or "").lower()
        if pci_class.startswith(GPU_CLASSES):
            found.append((device.rsplit("/", 1)[1], vendor))
    return found


def detect_pci(
    fs: FileSystem,
    runner: Runner,
    env_class: str,
    gpu_lists: Mapping[str, DriverGpus] | None = None,
) -> GpuInfo:
    """Detect a GPU on the PCI bus (bare metal or passthrough).

    Args:
        fs: Filesystem view.
        runner: Command runner.
        env_class: ``baremetal`` or ``vm``.
        gpu_lists: The GPU lists of the pinned driver releases
            (:func:`load_gpu_lists` when None).

    Returns:
        GPU facts.
    """
    info = GpuInfo(probed=True)
    devices = _pci_gpus(fs)
    nvidia = [d for d, v in devices if v == NVIDIA_VENDOR]
    info.unsupported_devices = [f"{d} (AMD)" for d, v in devices if v == AMD_VENDOR]
    if not nvidia:
        if env_class == "vm":
            info.notes.append("no GPU passthrough")
        else:
            info.notes.append("no NVIDIA GPU on the PCI bus")
        return info
    info.present, info.vendor, info.backend = True, "nvidia", "nvidia"
    info.path = "passthrough" if env_class == "vm" else "native"
    device = (fs.read_first_line(f"/sys/bus/pci/devices/{nvidia[0]}/device") or "").strip()
    if re.fullmatch(r"0x[0-9a-fA-F]{4}", device):
        devid = device[2:].upper()
        info.pci_device = f"10de:{devid.lower()}"
        lists = load_gpu_lists() if gpu_lists is None else gpu_lists
        info.open_module_drivers = sorted(v for v, g in lists.items() if devid in g.open_module)
        info.proprietary_only_drivers = sorted(
            v for v, g in lists.items() if devid in g.proprietary_only
        )
    info.driver_loaded = fs.exists("/proc/driver/nvidia/version")
    info.driver_installed = info.driver_loaded or runner.run(["modinfo", "nvidia"]).ok
    if info.driver_loaded:
        info.driver_state = "loaded"
        info.usable = _apply_query(info, runner, "nvidia-smi")
    elif info.driver_installed:
        info.driver_state = "installed_not_loaded"
    else:
        info.driver_state = "not_installed"
    return info


def detect_gpu(
    fs: FileSystem,
    runner: Runner,
    env_class: str,
    gpu_lists: Mapping[str, DriverGpus] | None = None,
) -> GpuInfo:
    """Detect GPUs according to the environment class.

    Args:
        fs: Filesystem view.
        runner: Command runner.
        env_class: Environment class.
        gpu_lists: The GPU lists of the pinned driver releases (see :func:`detect_pci`).

    Returns:
        GPU facts (not probed for container, wsl1 and unknown).
    """
    if env_class == "wsl2":
        return detect_wsl2(fs, runner)
    if env_class in ("baremetal", "vm"):
        return detect_pci(fs, runner, env_class, gpu_lists)
    return GpuInfo(notes=[f"GPU not probed in environment class {env_class}"])
