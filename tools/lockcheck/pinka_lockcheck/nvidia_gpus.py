"""The GPU list of the NVIDIA driver release pinned for automatic installs (BUILD_PROMPT §10.1).

NVIDIA ships ``supported-gpus.json`` with every driver release: one entry per
GPU (``devid``, optional ``subvendorid``/``subdevid``, ``name``,
``features``), with ``legacybranch`` for a GPU only an older driver branch
supports and the feature ``kernelopen`` for a GPU the release's open kernel
module supports. Ubuntu's ``nvidia-kernel-common-<branch>`` package carries
the file of its version. ``make lock-pins`` pins from it the device IDs the
open module supports and those the release supports only with the
proprietary module (``nvidia_driver_gpus.releases.<version>``), and
cross-checks them against
NVIDIA's open-gpu-kernel-modules README at the release tag and the
``Modaliases`` of Ubuntu's driver packages; ``make lock-check`` downloads the
pinned package again and recomputes the list.

Automatic installs use only the open-module list: NVIDIA states that the open
modules "can be used on any Turing or later GPU" (compute capability 7.5),
which is the minimum of the support matrix, so no GPU below it ever gets a
driver installed automatically.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import subprocess
import tarfile
import tempfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# The file inside the package, and the package that carries it.
JSON_PATH = "usr/share/doc/nvidia-kernel-common-{branch}/supported-gpus.json"
COMMON_PACKAGE = "nvidia-kernel-common-{branch}"
OPEN_PACKAGE = "nvidia-driver-{branch}-open"
PROPRIETARY_PACKAGE = "nvidia-driver-{branch}"
README_URL = "https://raw.githubusercontent.com/NVIDIA/open-gpu-kernel-modules/{version}/README.md"
OPEN_FEATURE = "kernelopen"
DEVID_RE = re.compile(r"^0x([0-9A-Fa-f]{4})$")
MODALIAS_RE = re.compile(r"pci:v000010DEd0000([0-9A-F]{4})", re.IGNORECASE)
README_ROW_RE = re.compile(
    r"^\|\s*[^|]+?\s*\|\s*([0-9A-F]{4})(?:\s+[0-9A-F]{4}\s+[0-9A-F]{4})?\s*\|\s*$", re.MULTILINE
)
PACKAGE_RE = re.compile(r"^nvidia-driver-(\d+)-open$")

Extractor = Callable[[bytes, str], bytes]


class GpuListError(ValueError):
    """The GPU list cannot be read, or its sources disagree."""


@dataclass(frozen=True)
class GpuList:
    """The device IDs of one driver release.

    Attributes:
        open_module: Device IDs (four upper-case hex digits) the open kernel
            module supports, sorted.
        current: Device IDs the release supports with either module, sorted.
    """

    open_module: tuple[str, ...]
    current: tuple[str, ...]

    @property
    def proprietary_only(self) -> tuple[str, ...]:
        """Device IDs the release supports only with the proprietary module, sorted."""
        return tuple(sorted(set(self.current) - set(self.open_module)))


def upstream_version(package_version: str) -> str:
    """The driver release of a Debian package version (``580.178.04-0ubuntu0.24.04.1``).

    Args:
        package_version: The package version, possibly with an epoch.

    Returns:
        ``580.178.04``.
    """
    return package_version.split(":", 1)[-1].split("-", 1)[0]


def branch_of(package: str) -> str | None:
    """The driver branch of an open driver package name (``nvidia-driver-580-open``).

    Args:
        package: Package name.

    Returns:
        ``580``, or None when the name is not an open driver package.
    """
    match = PACKAGE_RE.match(package)
    return match.group(1) if match else None


def parse_supported_gpus(data: bytes) -> GpuList:
    """Read NVIDIA's ``supported-gpus.json``.

    Args:
        data: The file's content.

    Returns:
        The open-module and the current device IDs.

    Raises:
        GpuListError: If the file is not the expected shape, or a device ID has
            both open-module and other entries (so the device ID alone would
            not decide).
    """
    try:
        chips = json.loads(data)["chips"]
    except (ValueError, KeyError, TypeError) as exc:
        raise GpuListError(f"not a supported-gpus.json document: {exc}") from exc
    if not isinstance(chips, list):
        raise GpuListError("chips is not a list")
    open_ids: set[str] = set()
    other_ids: set[str] = set()
    current: set[str] = set()
    for chip in chips:
        match = DEVID_RE.match(str(chip.get("devid", "")))
        if not match:
            raise GpuListError(f"bad devid in {chip!r}")
        devid = match.group(1).upper()
        features = chip.get("features") or []
        legacy = chip.get("legacybranch")
        if not legacy:
            current.add(devid)
        if not legacy and OPEN_FEATURE in features:
            open_ids.add(devid)
        else:
            other_ids.add(devid)
    mixed = sorted(open_ids & other_ids)
    if mixed:
        raise GpuListError(f"device IDs with open-module and other entries: {', '.join(mixed)}")
    return GpuList(tuple(sorted(open_ids)), tuple(sorted(current)))


def parse_modaliases(field: str) -> set[str]:
    """The NVIDIA device IDs of a package's ``Modaliases`` field.

    Args:
        field: The field's value.

    Returns:
        Device IDs (four upper-case hex digits).
    """
    return {m.upper() for m in MODALIAS_RE.findall(field)}


def parse_readme(text: str) -> set[str]:
    """The device IDs of the compatible-GPU table in NVIDIA's open-gpu-kernel-modules README.

    Args:
        text: The README.

    Returns:
        Device IDs (the first ID of each row).

    Raises:
        GpuListError: If the README has no such table or does not state Turing.
    """
    if "Turing or later GPU" not in text:
        raise GpuListError("the README no longer states which GPUs the open modules support")
    ids = set(README_ROW_RE.findall(text))
    if not ids:
        raise GpuListError("the README has no table of compatible GPUs")
    return ids


def cross_check(
    gpus: GpuList, *, readme: Iterable[str], open_aliases: Iterable[str], aliases: Iterable[str]
) -> list[str]:
    """Compare the list with NVIDIA's README and Ubuntu's ``Modaliases``.

    Args:
        gpus: The list from ``supported-gpus.json``.
        readme: Device IDs of NVIDIA's README table at the release tag.
        open_aliases: Device IDs of ``nvidia-driver-<branch>-open``.
        aliases: Device IDs of ``nvidia-driver-<branch>``.

    Returns:
        One line per difference; empty when all agree.
    """
    problems = []
    for label, found, wanted in (
        ("NVIDIA's README table", set(readme), set(gpus.open_module)),
        ("the Modaliases of the open driver package", set(open_aliases), set(gpus.open_module)),
        ("the Modaliases of the proprietary driver package", set(aliases), set(gpus.current)),
    ):
        if found != wanted:
            missing, extra = sorted(wanted - found), sorted(found - wanted)
            problems.append(
                f"{label} differs: missing {', '.join(missing[:10]) or '-'}"
                f" ({len(missing)}), extra {', '.join(extra[:10]) or '-'} ({len(extra)})"
            )
    return problems


def extract_member(deb: bytes, member: str) -> bytes:
    """Read one file of a Debian package with ``dpkg-deb --fsys-tarfile``.

    Ubuntu compresses the data with zstd, which Python's standard library
    reads only from 3.14; ``dpkg-deb`` is on every Debian-family machine and
    on the CI runners.

    Args:
        deb: The package.
        member: The file's path without the leading ``./``.

    Returns:
        The file's content.

    Raises:
        GpuListError: If dpkg-deb is missing or fails, or the file is not in the package.
    """
    command = shutil.which("dpkg-deb")
    if command is None:
        raise GpuListError("dpkg-deb is not installed (Debian and Ubuntu package dpkg)")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp, "package.deb")
        path.write_bytes(deb)
        done = subprocess.run(  # noqa: S603 - fixed arguments
            [command, "--fsys-tarfile", str(path)], capture_output=True, check=False
        )
    if done.returncode != 0:
        raise GpuListError(f"dpkg-deb failed: {done.stderr.decode(errors='replace').strip()}")
    with tarfile.open(fileobj=io.BytesIO(done.stdout)) as tar:
        for info in tar.getmembers():
            if info.name.lstrip("./") == member and info.isfile():
                handle = tar.extractfile(info)
                if handle is not None:
                    return handle.read()
    raise GpuListError(f"the package has no {member}")


def verify_entry(
    version: str,
    entry: Mapping[str, Any],
    fetch: Callable[[str], bytes],
    extract: Extractor = extract_member,
) -> list[str]:
    """Download a pinned list's package again and recompute the list (``make lock-check``).

    Args:
        version: The driver release.
        entry: ``nvidia_driver_gpus.<version>`` of the lock.
        fetch: ``url -> content``.
        extract: Reads a file out of a Debian package.

    Returns:
        One line per problem; empty when the pin checks out.
    """
    deb = fetch(str(entry["source"]))
    actual = hashlib.sha256(deb).hexdigest()
    if actual != entry["package_sha256"]:
        return [f"{entry['source']} has sha256 {actual}, pinned {entry['package_sha256']}"]
    try:
        data = extract(deb, str(entry["path"]))
        gpus = parse_supported_gpus(data)
    except GpuListError as exc:
        return [f"{version}: {exc}"]
    problems = []
    if hashlib.sha256(data).hexdigest() != entry["sha256"]:
        problems.append(f"{entry['path']} has another sha256 than the pinned {entry['sha256']}")
    for name, ids in (
        ("open_module", gpus.open_module),
        ("proprietary_only", gpus.proprietary_only),
    ):
        if list(ids) != [str(i) for i in entry[name]]:
            problems.append(f"the {name} list of {version} differs from the pinned list")
    return problems
