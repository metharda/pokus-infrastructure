"""The test targets (``tests/distros/targets.yml``)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ID_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{0,40}$")
FIREWALLS = ("nftables", "firewalld", "ufw")
# What the uninstall lane's variant installs before pinka (Debian family only).
PREEXISTING = ("docker", "tailscale")
# Family of an os-release ID, for the harness's own package commands.
FAMILIES = {
    "ubuntu": "debian",
    "debian": "debian",
    "fedora": "redhat",
    "almalinux": "redhat",
    "rocky": "redhat",
    "centos": "redhat",
    "rhel": "redhat",
    "opensuse-leap": "suse",
    "opensuse-tumbleweed": "suse",
    "arch": "archlinux",
}


class TargetError(ValueError):
    """The targets file is invalid."""


@dataclass(frozen=True)
class Target:
    """One test VM.

    Attributes:
        id: Target name (also part of the VM name).
        remote: Incus image remote.
        image: Image alias on the remote.
        os_id: ``ID`` of the release's os-release.
        version_id: ``VERSION_ID``, or None for rolling releases.
        firewall: Backend the harness establishes and expects.
        selinux: ``enforcing`` or None.
        cpus: vCPUs.
        memory: Memory limit (Incus size).
        disk: Root disk size (Incus size).
        preexisting: Components the harness installs before bootstrap (the
            uninstall lane's variant, BUILD_PROMPT §19.9): ``docker``,
            ``tailscale``.
    """

    id: str
    remote: str
    image: str
    os_id: str
    version_id: str | None
    firewall: str
    selinux: str | None
    cpus: int
    memory: str
    disk: str
    preexisting: tuple[str, ...] = ()

    @property
    def image_ref(self) -> str:
        """``<remote>:<alias>`` for ``incus launch``."""
        return f"{self.remote}:{self.image}"

    @property
    def family(self) -> str:
        """Distribution family of the release."""
        return FAMILIES[self.os_id]

    @property
    def vm_name_stem(self) -> str:
        """Instance name prefix: letters, digits and dashes only."""
        return "ptd-" + self.id.replace(".", "-")


@dataclass(frozen=True)
class TargetFile:
    """The targets file.

    Attributes:
        targets: Every target, in file order.
        unverified: The ``verify`` note while the image aliases are unverified.
    """

    targets: tuple[Target, ...]
    unverified: str | None

    def select(self, ids: list[str]) -> list[Target]:
        """Targets by id, or all of them for an empty list.

        Args:
            ids: Wanted ids.

        Returns:
            The targets, in file order.

        Raises:
            TargetError: For an unknown id.
        """
        known = {t.id for t in self.targets}
        unknown = sorted(set(ids) - known)
        if unknown:
            raise TargetError(f"unknown target(s): {', '.join(unknown)}")
        return [t for t in self.targets if not ids or t.id in ids]


def _target(entry: dict[str, Any], defaults: dict[str, Any]) -> Target:
    merged = {**defaults, **entry}
    os_release = merged.get("os") or {}
    target = Target(
        id=str(merged.get("id", "")),
        remote=str(merged.get("remote", "")),
        image=str(merged.get("image", "")),
        os_id=str(os_release.get("id", "")),
        version_id=str(os_release["version_id"]) if "version_id" in os_release else None,
        firewall=str(merged.get("firewall", "")),
        selinux=merged.get("selinux"),
        cpus=int(merged.get("cpus", 0)),
        memory=str(merged.get("memory", "")),
        disk=str(merged.get("disk", "")),
        preexisting=tuple(str(c) for c in merged.get("preexisting") or ()),
    )
    problems = []
    if not ID_RE.match(target.id):
        problems.append("id")
    if not target.remote or not target.image or " " in target.image:
        problems.append("image")
    if target.os_id not in FAMILIES:
        problems.append("os.id")
    if target.firewall not in FIREWALLS:
        problems.append("firewall")
    if target.selinux not in (None, "enforcing"):
        problems.append("selinux")
    if target.cpus < 1 or not target.memory or not target.disk:
        problems.append("resources")
    if not set(target.preexisting) <= set(PREEXISTING) or (
        target.preexisting and target.family != "debian"
    ):
        problems.append("preexisting")
    if problems:
        raise TargetError(f"target {target.id or '?'}: invalid {', '.join(problems)}")
    return target


def load(path: Path) -> TargetFile:
    """Read and check the targets file.

    Args:
        path: ``tests/distros/targets.yml``.

    Returns:
        The targets.

    Raises:
        TargetError: On an invalid file or duplicate ids.
    """
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise TargetError(f"{path}: schema_version 1 expected")
    defaults = data.get("defaults") or {}
    targets = tuple(_target(entry, defaults) for entry in data.get("targets") or [])
    ids = [t.id for t in targets]
    if len(ids) != len(set(ids)):
        raise TargetError(f"{path}: duplicate target ids")
    verify = data.get("verify")
    return TargetFile(targets, str(verify) if verify else None)
