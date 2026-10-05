"""Operating system identification from ``os-release`` (BUILD_PROMPT §2.2)."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field

from pokus_preflight.fs import FileSystem

OS_RELEASE_PATHS = ("/etc/os-release", "/usr/lib/os-release")
_LINE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")


@dataclass
class OsRelease:
    """Parsed ``os-release`` fields used by the support matrix.

    Attributes:
        id: ``ID``, lower-cased (``linux`` if absent, per os-release(5)).
        id_like: ``ID_LIKE`` split into words.
        version_id: ``VERSION_ID``.
        version_codename: ``VERSION_CODENAME``.
        pretty_name: ``PRETTY_NAME``.
        variant_id: ``VARIANT_ID``.
        ubuntu_codename: ``UBUNTU_CODENAME`` (Ubuntu and derivatives).
        debian_codename: ``DEBIAN_CODENAME`` (Debian derivatives).
        raw: Every key and value read.
    """

    id: str
    id_like: list[str] = field(default_factory=list)
    version_id: str | None = None
    version_codename: str | None = None
    pretty_name: str | None = None
    variant_id: str | None = None
    ubuntu_codename: str | None = None
    debian_codename: str | None = None
    raw: dict[str, str] = field(default_factory=dict)

    @property
    def version_major(self) -> str | None:
        """The part of ``VERSION_ID`` before the first dot."""
        return self.version_id.split(".", 1)[0] if self.version_id else None


def parse_os_release(text: str) -> dict[str, str]:
    """Parse ``os-release`` content (shell-compatible ``KEY=value`` lines).

    Args:
        text: File content.

    Returns:
        Mapping of keys to unquoted values; malformed lines are ignored.
    """
    result: dict[str, str] = {}
    for line in text.splitlines():
        m = _LINE.match(line.strip())
        if not m:
            continue
        key, value = m.group(1), m.group(2)
        try:
            parts = shlex.split(value, comments=False, posix=True)
        except ValueError:
            continue
        result[key] = " ".join(parts) if parts else ""
    return result


def detect_os_release(fs: FileSystem) -> OsRelease | None:
    """Read ``/etc/os-release`` (falling back to ``/usr/lib/os-release``).

    Args:
        fs: Filesystem view.

    Returns:
        The parsed release, or None if neither file exists.
    """
    for path in OS_RELEASE_PATHS:
        text = fs.read_text(path)
        if text is None:
            continue
        raw = parse_os_release(text)
        return OsRelease(
            id=raw.get("ID", "linux").lower(),
            id_like=raw.get("ID_LIKE", "").lower().split(),
            version_id=raw.get("VERSION_ID") or None,
            version_codename=raw.get("VERSION_CODENAME") or None,
            pretty_name=raw.get("PRETTY_NAME") or None,
            variant_id=raw.get("VARIANT_ID") or None,
            ubuntu_codename=raw.get("UBUNTU_CODENAME") or None,
            debian_codename=raw.get("DEBIAN_CODENAME") or None,
            raw=raw,
        )
    return None
