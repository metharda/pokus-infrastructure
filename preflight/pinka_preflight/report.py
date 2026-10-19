"""Host profile report: assembly, validation and storage (BUILD_PROMPT §2.1, §2.5).

The report is the JSON document described by
``preflight/schema/host-profile.schema.json``. ``generated_at`` is the only
volatile field. On a real run as root the report is written atomically to
``/etc/pinka/host-profile.json`` (0644) and archived as
``/var/lib/pinka/preflight/<UTC timestamp>.json``.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pinka_preflight import __version__
from pinka_preflight.detect.components import Components
from pinka_preflight.detect.conflicts import ConflictReport
from pinka_preflight.detect.environment import Environment
from pinka_preflight.detect.gpu import GpuInfo
from pinka_preflight.detect.hardware import Hardware
from pinka_preflight.detect.kernel import KernelInfo
from pinka_preflight.detect.network import EndpointResult
from pinka_preflight.detect.os_release import OsRelease
from pinka_preflight.detect.timesync import TimeSync
from pinka_preflight.matrix import Decision

SCHEMA_VERSION = 1
PROFILE_PATH = "/etc/pinka/host-profile.json"
ARCHIVE_DIR = "/var/lib/pinka/preflight"
SCHEMA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "schema",
    "host-profile.schema.json",
)


@dataclass
class Overrides:
    """Operator overrides, recorded in the profile and every fingerprint (§13.11).

    Attributes:
        accept_degraded: ``--accept-degraded``.
        accept_experimental: ``--accept-experimental``.
        force_unsupported: ``--force-unsupported``.
        adopt_incus: ``--adopt-incus``.
        allow_dirty: ``--allow-dirty`` (bootstrap ran from a repository with
            uncommitted changes).
        accept_mok_enrollment: ``--accept-mok-enrollment`` (bootstrap may install
            the NVIDIA driver and import a Machine Owner Key on a Secure Boot host).
    """

    accept_degraded: bool = False
    accept_experimental: bool = False
    force_unsupported: bool = False
    adopt_incus: bool = False
    allow_dirty: bool = False
    accept_mok_enrollment: bool = False


@dataclass
class Detection:
    """Everything detected about a host.

    Attributes:
        osr: Parsed os-release.
        machine: CPU architecture.
        env: Environment classification.
        kernel: Kernel facts.
        hw: Hardware facts.
        gpu: GPU facts.
        conflicts: Conflicts and informational findings.
        timesync: Time synchronization facts.
        endpoints: Network probe results.
        offline: ``--offline`` was given.
        components: Components that may exist before pinka, with their origin.
    """

    osr: OsRelease | None
    machine: str
    env: Environment
    kernel: KernelInfo
    hw: Hardware
    gpu: GpuInfo
    conflicts: ConflictReport
    timesync: TimeSync
    endpoints: list[EndpointResult]
    offline: bool
    components: Components = field(default_factory=Components)


def utc_iso(when: dt.datetime) -> str:
    """Format a time as UTC ISO 8601 with ``Z``.

    Args:
        when: Timezone-aware time.

    Returns:
        For example ``2026-10-05T09:12:44Z``.
    """
    return when.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_report(
    det: Detection,
    decision: Decision,
    overrides: Overrides,
    *,
    generated_at: dt.datetime,
    repo_commit: str | None,
    nvidia_driver_mode: str = "auto",
) -> dict[str, Any]:
    """Assemble the host profile.

    Args:
        det: Detection results.
        decision: Matrix decision.
        overrides: Operator overrides.
        generated_at: Report time.
        repo_commit: Commit of the pokus-infrastructure checkout running preflight, if known.
        nvidia_driver_mode: The host's ``install_nvidia_driver`` setting given to
            preflight (``--nvidia-driver``).

    Returns:
        The report as a JSON-compatible dictionary.
    """
    osr, k, hw, gpu = det.osr, det.kernel, det.hw, det.gpu
    wsl = det.env.wsl
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_iso(generated_at),
        "preflight_version": __version__,
        "repo_commit": repo_commit,
        "outcome": decision.outcome,
        "exit_code": decision.exit_code,
        "tier": decision.tier,
        "os_tier": decision.os_tier,
        "arch_tier": decision.arch_tier,
        "release_model": decision.release_model,
        "family": decision.family,
        "pkg_manager": decision.pkg_manager,
        "provider": decision.provider,
        "os": {
            "id": osr.id if osr else None,
            "id_like": list(osr.id_like) if osr else [],
            "version_id": osr.version_id if osr else None,
            "codename": osr.version_codename if osr else None,
            "pretty_name": osr.pretty_name if osr else None,
            "variant_id": osr.variant_id if osr else None,
            "arch": det.machine,
            "release": decision.release,
            "parent_release": decision.parent_release,
        },
        "kernel": {"release": k.release, "version": k.version},
        "init": {"pid1": k.pid1_comm, "systemd": k.systemd_pid1, "system_state": k.system_state},
        "environment": {
            "class": det.env.env_class,
            "hypervisor": det.env.hypervisor,
            "confidence": det.env.confidence,
            "signals": dict(det.env.signals),
            "conflicts_between_signals": list(det.env.conflicts_between_signals),
        },
        "wsl": None
        if wsl is None
        else {
            "conf_present": wsl.present,
            "systemd": wsl.systemd,
            "automount": wsl.automount,
            "mount_fstab": wsl.mount_fstab,
            "interop": wsl.interop_enabled,
            "append_windows_path": wsl.append_windows_path,
            "hostname": wsl.hostname,
        },
        "hardware": {
            "cpu_model": hw.cpu_model,
            "cpu_threads": hw.cpu_threads,
            "cpu_quota_threads": hw.cpu_quota_threads,
            "cpu_threads_effective": hw.cpu_threads_effective,
            "vmx": hw.vmx,
            "svm": hw.svm,
            "memory_gib": hw.memory_gib,
            "swap_gib": hw.swap_gib,
            "disks": {name: dataclasses.asdict(d) for name, d in sorted(hw.disks.items())},
            "block_devices": [dataclasses.asdict(b) for b in hw.block_devices],
        },
        "gpu": {
            "probed": gpu.probed,
            "present": gpu.present,
            "usable": gpu.usable,
            "vendor": gpu.vendor,
            "backend": gpu.backend,
            "name": gpu.name,
            "count": gpu.count,
            "vram_gib": gpu.vram_gib,
            "driver": gpu.driver,
            "compute_capability": gpu.compute_capability,
            "driver_cuda": gpu.driver_cuda,
            "path": gpu.path,
            "driver_state": gpu.driver_state,
            "unsupported_devices": list(gpu.unsupported_devices),
            "notes": list(gpu.notes),
            "install_driver": nvidia_driver_mode,
            "pci_device": gpu.pci_device,
            "open_module_drivers": list(gpu.open_module_drivers),
            "proprietary_only_drivers": list(gpu.proprietary_only_drivers),
        },
        "capabilities": {
            "cgroup2": k.cgroup2,
            "cgroup_fs_type": k.cgroup_fs_type,
            "cgroup2_controllers": list(k.cgroup2_controllers),
            "filesystems": list(k.filesystems),
            "kernel_modules": dict(sorted(k.modules.items())),
            "tun": k.tun,
            "kvm_present": k.kvm_present,
            "kvm_usable": k.kvm_usable,
            "kvm_nested": k.kvm_nested,
            "btrfs": k.feature_present("btrfs"),
            "dm_thin_pool": k.module_available("dm_thin_pool"),
            "secure_boot": k.secure_boot,
            "nouveau_loaded": k.nouveau_loaded,
        },
        "mac": {"system": k.mac_system, "mode": k.mac_mode},
        "firewall": {
            "active_manager": det.conflicts.firewall_manager,
            "backend": det.conflicts.firewall_backend,
        },
        "timesync": dataclasses.asdict(det.timesync),
        "network": {
            "offline": det.offline,
            "endpoints": [dataclasses.asdict(e) for e in det.endpoints],
        },
        "requirements": {
            "core": [dataclasses.asdict(c) for c in decision.core_requirements],
            "minimums": [dataclasses.asdict(c) for c in decision.minimums],
        },
        "features": dict(decision.features),
        "disabled_features": list(decision.disabled_features),
        "storage_backend": decision.storage_backend,
        "components": det.components.as_dict(),
        "conflicts": [dataclasses.asdict(c) for c in det.conflicts.conflicts],
        "unsupported_reasons": list(decision.unsupported_reasons),
        "needs_action": list(decision.needs_action),
        "planned_actions": list(decision.planned_actions),
        "warnings": list(decision.warnings),
        "notes": [*decision.notes, *det.conflicts.infos],
        "overrides": dataclasses.asdict(overrides),
    }


def to_json(report: dict[str, Any]) -> str:
    """Serialize a report (stable key order, trailing newline).

    Args:
        report: The report.

    Returns:
        Indented JSON text.
    """
    return json.dumps(report, indent=2, sort_keys=False, ensure_ascii=False) + "\n"


def atomic_write(path: str, text: str, mode: int = 0o644) -> None:
    """Write a file atomically (temporary file in the same directory, then rename).

    Args:
        path: Destination.
        text: Content.
        mode: File mode.
    """
    directory = os.path.dirname(path)
    os.makedirs(directory, mode=0o755, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def write_profile(
    report: dict[str, Any], when: dt.datetime, paths: Sequence[str] = ()
) -> list[str]:
    """Store the profile and its archive copy (real runs as root only).

    Args:
        report: The report.
        when: Report time, used for the archive file name.
        paths: Override destinations ``(profile, archive_dir)`` for tests.

    Returns:
        Paths written.
    """
    profile, archive_dir = (paths[0], paths[1]) if paths else (PROFILE_PATH, ARCHIVE_DIR)
    text = to_json(report)
    stamp = when.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = os.path.join(archive_dir, f"{stamp}.json")
    atomic_write(profile, text)
    atomic_write(archive, text)
    return [profile, archive]


def load_schema(path: str = SCHEMA_PATH) -> dict[str, Any]:
    """Load the host profile JSON Schema.

    Args:
        path: Schema path.

    Returns:
        The schema.
    """
    with open(path, encoding="utf-8") as handle:
        schema: dict[str, Any] = json.load(handle)
    return schema
