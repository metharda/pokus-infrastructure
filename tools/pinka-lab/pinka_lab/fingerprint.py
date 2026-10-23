"""The environment fingerprint (BUILD_PROMPT §10.2, contracts.md C9c).

The fingerprint describes everything that can change a measurement: the host
profile's essentials, the hardware, the installed and running software, the
golden images, the configuration the lab was built from, and on WSL the
effective ``wsl.conf``. It is canonical JSON (sorted keys, no volatile
fields) hashed with sha256; the computation time and the boot ID are
recorded next to it but outside the hash.

Only root computes it (and refreshes the cache
``/var/lib/pinka/fingerprint/current.json``); any other user gets the cached,
root-computed fingerprint and never escalates.
"""

from __future__ import annotations

import configparser
import hashlib
import json
import re
from typing import Any

from pinka_lab.config import LabConfig
from pinka_lab.system import System

SCHEMA_VERSION = 1
DEFAULT_CACHE = "/var/lib/pinka/fingerprint/current.json"
VERSION_RE = re.compile(r"(\d+\.\d+(?:\.\d+)?(?:[-+~.][0-9A-Za-z.]+)?)")
# A golden image alias names its export directory: <blueprint>/<version>.
ALIAS_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.+-]*/[A-Za-z0-9_][A-Za-z0-9_.+~-]*$")
PACKAGE_KEYS = ("name", "version", "architecture")


class FingerprintError(RuntimeError):
    """The fingerprint cannot be computed or read."""


def canonical(value: Any) -> str:  # noqa: ANN401 - JSON value
    """Canonical JSON: sorted keys, no whitespace, UTF-8 kept.

    Args:
        value: JSON-compatible value.

    Returns:
        The text that is hashed.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fingerprint_hash(fingerprint: dict[str, Any]) -> str:
    """Hash a fingerprint.

    Args:
        fingerprint: The non-volatile fields.

    Returns:
        ``sha256:<hex>``.
    """
    return "sha256:" + hashlib.sha256(canonical(fingerprint).encode("utf-8")).hexdigest()


def _json_file(system: System, path: str) -> dict[str, Any]:
    text = system.read_text(path)
    try:
        data = json.loads(text) if text else {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _version(system: System, argv: list[str]) -> str | None:
    done = system.run(argv, timeout=30)
    if not done.ok:
        return None
    match = VERSION_RE.search(done.stdout + done.stderr)
    return match.group(1) if match else None


def _host(profile: dict[str, Any]) -> dict[str, Any]:
    env = profile.get("environment") or {}
    os_info = profile.get("os") or {}
    return {
        "provider": profile.get("provider"),
        "environment_class": env.get("class"),
        "hypervisor": env.get("hypervisor"),
        "os_id": os_info.get("id"),
        "os_version": os_info.get("version_id"),
        "arch": os_info.get("arch"),
        "kernel": (profile.get("kernel") or {}).get("release"),
        "tier": profile.get("tier"),
        "release_model": profile.get("release_model"),
        "overrides": profile.get("overrides") or {},
    }


def _hardware(profile: dict[str, Any]) -> dict[str, Any]:
    hw = profile.get("hardware") or {}
    gpu = profile.get("gpu") or {}
    return {
        "cpu_model": hw.get("cpu_model"),
        "cpu_threads": hw.get("cpu_threads_effective"),
        "memory_gib": hw.get("memory_gib"),
        "gpu": {
            "name": gpu.get("name"),
            "count": gpu.get("count"),
            "vram_gib": gpu.get("vram_gib"),
            "driver": gpu.get("driver"),
            "compute_capability": gpu.get("compute_capability"),
            "path": gpu.get("path"),
        },
    }


def _software(system: System, config: LabConfig) -> dict[str, Any]:
    incus = system.run(["incus", "query", "/1.0"], timeout=30).json() or {}
    versions = {
        "docker": _version(system, ["docker", "version", "--format", "{{.Server.Version}}"]),
        "containerd": _version(system, ["containerd", "--version"]),
        "nvidia_container_toolkit": _version(system, ["nvidia-ctk", "--version"]),
        "incus": (incus.get("environment") or {}).get("server_version"),
        "tailscale": _version(system, ["tailscale", "version"]),
        "python": _version(
            system,
            [
                str(config.get("paths", "python", default="/opt/pinka/python/bin/python3")),
                "--version",
            ],
        ),
        "ansible_core": _version(
            system,
            [
                str(config.get("paths", "ansible", default="/opt/pinka/ansible-venv/bin/ansible")),
                "--version",
            ],
        ),
    }
    containers = {}
    for name in sorted(config.get("containers", default={})):
        done = system.run(["docker", "inspect", "--format", "{{.Config.Image}}", name], timeout=30)
        containers[name] = done.stdout.strip() if done.ok else None
    images = {}
    export_root = str(config.get("images", "export_root", default=f"{config.pinka_root}/images"))
    listing = system.run(["incus", "image", "list", "--format", "json"], timeout=60).json() or []
    for image in listing:
        for alias in image.get("aliases") or []:
            images[alias["name"]] = {
                "fingerprint": image.get("fingerprint"),
                "packages": _image_packages(
                    system, export_root, str(alias["name"]), image.get("fingerprint")
                ),
            }
    return {
        "versions": versions,
        "containers": containers,
        "golden_images": dict(sorted(images.items())),
    }


def _image_packages(
    system: System,
    export_root: str,
    alias: str,
    image_fingerprint: Any,  # noqa: ANN401
) -> list[dict[str, str]] | None:
    # The packages a golden image's build recorded (name, version and
    # architecture of every installed package), from its export record
    # <export_root>/<blueprint>/<version>/build.json. Null when the alias is
    # not a golden image's, the record is missing or of another image, or its
    # list is missing or malformed: nothing unproven is reported.
    if not ALIAS_RE.match(alias):
        return None
    record = _json_file(system, f"{export_root}/{alias}/build.json")
    if not image_fingerprint or record.get("fingerprint") != image_fingerprint:
        return None
    packages = record.get("packages")
    if not isinstance(packages, list):
        return None
    listed: list[dict[str, str]] = []
    for entry in packages:
        if not isinstance(entry, dict) or sorted(entry) != sorted(PACKAGE_KEYS):
            return None
        if not all(isinstance(entry[key], str) and entry[key] for key in PACKAGE_KEYS):
            return None
        listed.append({key: entry[key] for key in PACKAGE_KEYS})
    return sorted(listed, key=lambda e: (e["name"], e["architecture"], e["version"]))


def _platform(system: System, path: str) -> dict[str, Any] | None:
    # The active platform (contract v2, active.json): its release and content
    # packages by tag, commit and tree.
    active = _json_file(system, path)
    if not active:
        return None

    def source(entry: Any) -> dict[str, Any]:  # noqa: ANN401 - a JSON value
        entry = entry if isinstance(entry, dict) else {}
        return {k: entry.get(k) for k in ("tag", "commit", "tree")}

    found = active.get("content")
    content: dict[str, Any] = found if isinstance(found, dict) else {}
    return {
        "name": active.get("name"),
        "version": active.get("version"),
        "manifest_sha256": active.get("manifest_sha256"),
        "release": source(active.get("release")),
        "content": {name: source(entry) for name, entry in sorted(content.items())},
    }


def _configuration(system: System, config: LabConfig) -> dict[str, Any]:
    state = _json_file(
        system,
        str(config.get("paths", "bootstrap_state", default="/etc/pinka/bootstrap-state.json")),
    )
    return {
        "infra_commit": state.get("repo_commit"),
        "infra_tree": state.get("infra_tree"),
        "infra_dirty": state.get("infra_dirty"),
        "versions_lock_sha256": config.get("fingerprint", "versions_lock_sha256"),
        "host_vars_sha256": config.get("fingerprint", "host_vars_sha256"),
        "platform": _platform(
            system,
            str(config.get("paths", "active_platform", default="/etc/pinka/platform/active.json")),
        ),
    }


def _wsl(system: System) -> dict[str, Any]:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read_string(system.read_text("/etc/wsl.conf") or "")
    except configparser.Error:
        parser = configparser.ConfigParser(interpolation=None)
    conf = {section: dict(parser.items(section)) for section in sorted(parser.sections())}
    return {
        "wsl_conf": conf,
        "kernel_string": (system.read_text("/proc/version") or "").strip() or None,
        # Interop is off by design, so the Windows build cannot be read from Linux.
        "windows_build": None,
    }


def compute(system: System, config: LabConfig) -> dict[str, Any]:
    """Compute the fingerprint of this host (as root).

    Args:
        system: Host access.
        config: The lab description.

    Returns:
        ``{"fingerprint_schema_version", "hash", "fingerprint", "computed_at", "boot_id"}``;
        ``computed_at`` and ``boot_id`` are volatile and not hashed.

    Raises:
        FingerprintError: If the host profile is missing.
    """
    profile = _json_file(
        system, str(config.get("paths", "host_profile", default="/etc/pinka/host-profile.json"))
    )
    if not profile:
        raise FingerprintError("the host profile /etc/pinka/host-profile.json is missing")
    fingerprint: dict[str, Any] = {
        "fingerprint_schema_version": SCHEMA_VERSION,
        "machine": config.machine,
        "node_role": config.node_role,
        "lab_mode": config.get("lab_mode"),
        "host": _host(profile),
        "hardware": _hardware(profile),
        "software": _software(system, config),
        "config": _configuration(system, config),
    }
    if profile.get("provider") == "wsl2":
        fingerprint["wsl"] = _wsl(system)
    return {
        "fingerprint_schema_version": SCHEMA_VERSION,
        "hash": fingerprint_hash(fingerprint),
        "fingerprint": fingerprint,
        "computed_at": system.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "boot_id": (system.read_text("/proc/sys/kernel/random/boot_id") or "").strip() or None,
    }


def current(system: System, config: LabConfig) -> dict[str, Any]:
    """The fingerprint for the caller (contracts.md C9c).

    Root computes it and refreshes the cache; any other user reads the cache.

    Args:
        system: Host access.
        config: The lab description.

    Returns:
        The fingerprint document.

    Raises:
        FingerprintError: If it cannot be computed (root) or no cache exists.
    """
    cache = str(config.get("fingerprint", "cache", default=DEFAULT_CACHE))
    if system.euid() == 0:
        result = compute(system, config)
        system.write_text(cache, json.dumps(result, indent=1, sort_keys=True) + "\n", 0o644)
        return result
    cached = _json_file(system, cache)
    if not cached:
        raise FingerprintError(
            f"no cached fingerprint in {cache}; root computes it (sudo pinka fingerprint)"
        )
    return cached


def diff(old: dict[str, Any], new: dict[str, Any], prefix: str = "") -> list[str]:
    """List the fields that differ between two fingerprints.

    Args:
        old: Fingerprint fields before.
        new: Fingerprint fields after.
        prefix: Dotted path of the current level.

    Returns:
        ``path: old -> new`` lines, sorted.
    """
    lines = []
    for key in sorted(set(old) | set(new)):
        a, b = old.get(key), new.get(key)
        path = f"{prefix}{key}"
        if isinstance(a, dict) and isinstance(b, dict):
            lines += diff(a, b, path + ".")
        elif a != b:
            lines.append(f"{path}: {json.dumps(a)} -> {json.dumps(b)}")
    return lines
