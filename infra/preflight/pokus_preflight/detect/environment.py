"""Environment class detection: baremetal, wsl2, wsl1, vm, container, unknown.

Several independent signals are collected, recorded in the report, and
cross-checked (BUILD_PROMPT §2.2). WSL detection runs first: recent systemd
versions report ``wsl`` from ``systemd-detect-virt`` (also with
``--container``), and WSL must never be classified as a container.
"""

from __future__ import annotations

import configparser
import re
from dataclasses import dataclass, field
from typing import Union

from pokus_preflight.fs import FileSystem
from pokus_preflight.runner import EXIT_NOT_FOUND, Runner

SignalValue = Union[bool, str, int, None]

WSL1_KERNEL = re.compile(r"^4\.4\.0-\d+-microsoft", re.IGNORECASE)
CONTAINER_CGROUP_HINTS = (
    "docker",
    "kubepods",
    "lxc",
    "containerd",
    "libpod",
    "machine.slice/libpod",
)
SDV_HYPERVISORS = {
    "kvm": "kvm",
    "qemu": "qemu",
    "microsoft": "microsoft",
    "vmware": "vmware",
    "oracle": "oracle",
    "xen": "xen",
    "parallels": "parallels",
    "amazon": "amazon",
    "google": "google",
}
DMI_HYPERVISORS = (
    ("qemu", "qemu"),
    ("kvm", "kvm"),
    ("microsoft corporation", "microsoft"),
    ("vmware", "vmware"),
    ("innotek", "oracle"),
    ("oracle corporation", "oracle"),
    ("xen", "xen"),
    ("parallels", "parallels"),
    ("amazon ec2", "amazon"),
    ("google", "google"),
)
KVM_FAMILY = frozenset({"kvm", "qemu"})
HIGH_VOTES = 3
MEDIUM_VOTES = 2


@dataclass
class WslInfo:
    """Effective ``/etc/wsl.conf`` values (WSL defaults where unset).

    Attributes:
        present: ``/etc/wsl.conf`` exists.
        systemd: ``[boot] systemd`` (default false).
        automount: ``[automount] enabled`` (default true).
        mount_fstab: ``[automount] mountFsTab`` (default true).
        interop: ``[interop] enabled`` (default true).
        append_windows_path: ``[interop] appendWindowsPath`` (default true).
        hostname: ``[network] hostname`` if set.
        interop_registered: State of the ``WSLInterop`` binfmt entry, if present.
    """

    present: bool
    systemd: bool = False
    automount: bool = True
    mount_fstab: bool = True
    interop: bool = True
    append_windows_path: bool = True
    hostname: str | None = None
    interop_registered: str | None = None

    @property
    def interop_enabled(self) -> bool:
        """Interop is active: the binfmt entry says so, or wsl.conf does when absent."""
        if self.interop_registered is not None:
            return self.interop_registered == "enabled"
        return self.interop


@dataclass
class Environment:
    """Environment classification.

    Attributes:
        env_class: ``baremetal``, ``wsl2``, ``wsl1``, ``vm``, ``container`` or ``unknown``.
        hypervisor: Hypervisor for ``vm`` and ``wsl2`` (``microsoft``), else None.
        confidence: ``high``, ``medium`` or ``low``.
        signals: Every signal and its value.
        conflicts_between_signals: Human-readable disagreements between signals.
        wsl: Effective wsl.conf values (WSL only).
    """

    env_class: str
    hypervisor: str | None
    confidence: str
    signals: dict[str, SignalValue] = field(default_factory=dict)
    conflicts_between_signals: list[str] = field(default_factory=list)
    wsl: WslInfo | None = None


def _bool(value: str, default: bool) -> bool:
    v = value.strip().lower()
    if v in ("true", "yes", "1", "on"):
        return True
    if v in ("false", "no", "0", "off"):
        return False
    return default


def parse_wsl_conf(text: str | None) -> WslInfo:
    """Parse ``/etc/wsl.conf`` and apply WSL defaults.

    Args:
        text: File content, or None if the file does not exist.

    Returns:
        Effective values.
    """
    info = WslInfo(present=text is not None)
    if text is None:
        return info
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read_string(text)
    except configparser.Error:
        return info
    info.systemd = _bool(parser.get("boot", "systemd", fallback="false"), False)
    info.automount = _bool(parser.get("automount", "enabled", fallback="true"), True)
    info.mount_fstab = _bool(parser.get("automount", "mountFsTab", fallback="true"), True)
    info.interop = _bool(parser.get("interop", "enabled", fallback="true"), True)
    info.append_windows_path = _bool(
        parser.get("interop", "appendWindowsPath", fallback="true"), True
    )
    hostname = parser.get("network", "hostname", fallback="").strip()
    info.hostname = hostname or None
    return info


def _sdv(runner: Runner, *args: str) -> str | None:
    """Run ``systemd-detect-virt``; None if it is not installed."""
    res = runner.run(["systemd-detect-virt", *args])
    if res.returncode == EXIT_NOT_FOUND:
        return None
    return res.stdout.strip() or "none"


def _dmi(fs: FileSystem, name: str) -> str | None:
    return fs.read_first_line(f"/sys/class/dmi/id/{name}") or None


def dmi_hypervisor(signals: dict[str, SignalValue]) -> str | None:
    """Infer a hypervisor from DMI strings.

    Args:
        signals: Collected signals with ``dmi_*`` entries.

    Returns:
        The hypervisor name, or None if DMI looks like real hardware.
    """
    text = " ".join(
        str(signals.get(k) or "")
        for k in ("dmi_sys_vendor", "dmi_product_name", "dmi_board_vendor", "dmi_bios_vendor")
    ).lower()
    if "microsoft corporation" in text and "virtual machine" not in text:
        text = text.replace("microsoft corporation", "")
    for needle, name in DMI_HYPERVISORS:
        if needle in text:
            return name
    return None


def collect_signals(fs: FileSystem, runner: Runner) -> dict[str, SignalValue]:
    """Collect every environment signal.

    Args:
        fs: Filesystem view.
        runner: Command runner.

    Returns:
        Signal names and values.
    """
    osrelease = (fs.read_first_line("/proc/sys/kernel/osrelease") or "").strip()
    interop = fs.glob("/proc/sys/fs/binfmt_misc/WSLInterop*")
    interop_state: str | None = None
    if interop:
        interop_state = fs.read_first_line(interop[0]) or "unknown"
    environ = (fs.read_bytes("/proc/1/environ") or b"").split(b"\0")
    container_env = next(
        (
            e.split(b"=", 1)[1].decode(errors="replace")
            for e in environ
            if e.startswith(b"container=")
        ),
        None,
    )
    cgroup = (fs.read_text("/proc/1/cgroup") or "").lower()
    cgroup_hint = next((h for h in CONTAINER_CGROUP_HINTS if h in cgroup), None)
    cpuinfo = fs.read_text("/proc/cpuinfo") or ""
    flags_line = next((ln for ln in cpuinfo.splitlines() if ln.startswith("flags")), "")
    return {
        "osrelease": osrelease or None,
        "osrelease_microsoft": "microsoft" in osrelease.lower(),
        "osrelease_wsl2": "wsl2" in osrelease.lower(),
        "wsl1_kernel": bool(WSL1_KERNEL.match(osrelease)),
        "dev_dxg": fs.exists("/dev/dxg"),
        "usr_lib_wsl": fs.is_dir("/usr/lib/wsl/lib"),
        "wsl_interop": interop_state,
        "wsl_conf": fs.exists("/etc/wsl.conf"),
        "systemd_detect_virt": _sdv(runner),
        "systemd_detect_virt_vm": _sdv(runner, "--vm"),
        "systemd_detect_virt_container": _sdv(runner, "--container"),
        "cpu_hypervisor_flag": " hypervisor" in f" {flags_line.split(':', 1)[-1]} ",
        "dmi_sys_vendor": _dmi(fs, "sys_vendor"),
        "dmi_product_name": _dmi(fs, "product_name"),
        "dmi_board_vendor": _dmi(fs, "board_vendor"),
        "dmi_bios_vendor": _dmi(fs, "bios_vendor"),
        "dockerenv": fs.exists("/.dockerenv"),
        "containerenv": fs.exists("/run/.containerenv"),
        "pid1_container_env": container_env,
        "cgroup_container_hint": cgroup_hint,
    }


def _confidence(votes: int) -> str:
    if votes >= HIGH_VOTES:
        return "high"
    if votes >= MEDIUM_VOTES:
        return "medium"
    return "low"


def _classify_wsl(s: dict[str, SignalValue], conflicts: list[str]) -> Environment:
    release = str(s.get("osrelease") or "")
    if s["wsl1_kernel"]:
        env_class = "wsl1"
        votes = 1 + (not s["dev_dxg"]) + (s["systemd_detect_virt"] in ("wsl", None))
    else:
        env_class = "wsl2"
        major = re.match(r"^(\d+)\.", release)
        kernel_says = bool(s["osrelease_wsl2"]) or bool(major and int(major.group(1)) >= 5)  # noqa: PLR2004
        if s["osrelease_microsoft"] and not kernel_says:
            conflicts.append("kernel string mentions Microsoft but is neither WSL1 nor WSL2-like")
        votes = sum(
            bool(v)
            for v in (
                s["osrelease_microsoft"],
                s["dev_dxg"] or s["usr_lib_wsl"],
                "wsl" in (s["systemd_detect_virt"], s["systemd_detect_virt_container"]),
                s["wsl_interop"] is not None,
            )
        )
    if not s["osrelease_microsoft"]:
        conflicts.append(
            "systemd-detect-virt reports wsl but the kernel string does not mention Microsoft"
        )
    if s["dockerenv"] or s["containerenv"]:
        conflicts.append("container marker files exist inside WSL")
    return Environment(env_class, "microsoft", _confidence(int(votes)), s, conflicts)


def classify(s: dict[str, SignalValue]) -> Environment:  # noqa: PLR0911 - one return per class
    """Classify the environment from collected signals.

    Args:
        s: Signals from :func:`collect_signals`.

    Returns:
        The classification without WSL details.
    """
    conflicts: list[str] = []
    sdv, sdv_vm, sdv_ct = (
        s["systemd_detect_virt"],
        s["systemd_detect_virt_vm"],
        s["systemd_detect_virt_container"],
    )
    if s["osrelease_microsoft"] or "wsl" in (sdv, sdv_ct):
        return _classify_wsl(s, conflicts)

    markers = [
        bool(s["dockerenv"]),
        bool(s["containerenv"]),
        s["pid1_container_env"] is not None,
        sdv_ct not in (None, "none"),
        s["cgroup_container_hint"] is not None,
    ]
    if any(markers):
        return Environment("container", None, _confidence(sum(markers)), s, conflicts)

    dmi_hv = dmi_hypervisor(s)
    flag = bool(s["cpu_hypervisor_flag"])
    if sdv_vm not in (None, "none"):
        hv = SDV_HYPERVISORS.get(str(sdv_vm), "other")
        votes = 1 + flag + (dmi_hv is not None)
        if dmi_hv is not None and dmi_hv != hv and not {dmi_hv, hv} <= KVM_FAMILY:
            conflicts.append(f"systemd-detect-virt reports {hv} but DMI suggests {dmi_hv}")
            votes -= 1
        return Environment("vm", hv, _confidence(votes), s, conflicts)

    if sdv_vm == "none":
        if flag and dmi_hv is not None:
            conflicts.append(
                "systemd-detect-virt reports no VM, but the CPU hypervisor flag is set and DMI "
                f"suggests {dmi_hv}"
            )
            return Environment("vm", dmi_hv, "low", s, conflicts)
        if flag or dmi_hv is not None:
            what = "the CPU hypervisor flag is set" if flag else f"DMI suggests {dmi_hv}"
            conflicts.append(f"systemd-detect-virt reports no VM, but {what}")
            return Environment("baremetal", None, "low", s, conflicts)
        has_dmi = s["dmi_sys_vendor"] is not None
        return Environment("baremetal", None, "high" if has_dmi else "medium", s, conflicts)

    # systemd-detect-virt is not available.
    if flag and dmi_hv is not None:
        return Environment("vm", dmi_hv, "medium", s, conflicts)
    if flag or dmi_hv is not None:
        return Environment("vm", dmi_hv or "other", "low", s, conflicts)
    if s["dmi_sys_vendor"] is not None:
        return Environment("baremetal", None, "medium", s, conflicts)
    return Environment("unknown", None, "low", s, conflicts)


def detect_environment(fs: FileSystem, runner: Runner) -> Environment:
    """Collect signals and classify the environment.

    Args:
        fs: Filesystem view.
        runner: Command runner.

    Returns:
        The classification, with wsl.conf values for WSL.
    """
    signals = collect_signals(fs, runner)
    env = classify(signals)
    if env.env_class in ("wsl2", "wsl1"):
        wsl = parse_wsl_conf(fs.read_text("/etc/wsl.conf"))
        state = signals.get("wsl_interop")
        wsl.interop_registered = str(state) if state is not None else None
        env.wsl = wsl
    return env
