"""The published contract: schema, profiles and reserved names (``contract/``)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

import yaml

DEFAULT_DIR = Path(__file__).resolve().parents[3] / "contract"
SCHEMA_FILE = "platform-manifest.schema.json"
PROFILES_FILE = "profiles.yml"
RESERVED_FILE = "reserved-names.yml"
_SIZE_RE = re.compile(r"^([1-9][0-9]*)([KMG])$")
_UNITS = {"K": 1024, "M": 1024**2, "G": 1024**3}


def size_bytes(value: str) -> int:
    """Convert a systemd-style size.

    Args:
        value: For example ``512M``.

    Returns:
        Bytes.

    Raises:
        ValueError: Not a size.
    """
    match = _SIZE_RE.match(value)
    if not match:
        raise ValueError(f"not a size: {value!r}")
    return int(match.group(1)) * _UNITS[match.group(2)]


@dataclass(frozen=True)
class Profile:
    """One privilege profile of ``profiles.yml``.

    Attributes:
        name: Profile name.
        data: Its entry in ``profiles.yml``.
    """

    name: str
    data: dict[str, Any]

    @property
    def kind(self) -> str:
        """``service`` or ``job``."""
        return str(self.data["kind"])

    def flag(self, key: str) -> bool:
        """Read a boolean grant.

        Args:
            key: Field of the profile.

        Returns:
            The grant.
        """
        return bool(self.data[key])

    @property
    def socket_clients(self) -> dict[str, Any] | None:
        """Who may use a socket this profile serves (None: serves none)."""
        clients = self.data["socket_clients"]
        return dict(clients) if clients else None


@dataclass(frozen=True)
class Namespace:
    """Reserved names of one namespace.

    Attributes:
        names: Exact names.
        patterns: Shell-style patterns (``pg_*``).
    """

    names: frozenset[str]
    patterns: tuple[str, ...]

    def reserves(self, name: str) -> bool:
        """Tell whether a name is reserved here.

        Args:
            name: The name.

        Returns:
            True if it is a listed name or matches a listed pattern.
        """
        return name in self.names or any(fnmatchcase(name, p) for p in self.patterns)


@dataclass(frozen=True)
class Contract:
    """Everything the validator needs from ``contract/``.

    Attributes:
        version: Contract version.
        schema: The manifest's JSON Schema.
        profiles: Profiles by name.
        caps: Infra's caps on manifest values.
        defaults: Defaults for omitted limits.
        relaxations: The fixed list of relaxations.
        human_classes: Human client classes and their groups.
        reserved: Reserved names per namespace.
        reserved_paths: Reserved path segments per parent directory.
    """

    version: int
    schema: dict[str, Any]
    profiles: dict[str, Profile]
    caps: dict[str, Any]
    defaults: dict[str, Any]
    relaxations: dict[str, Any]
    human_classes: dict[str, Any]
    reserved: dict[str, Namespace]
    reserved_paths: dict[str, frozenset[str]]

    def profile(self, name: str) -> Profile:
        """Look up a profile.

        Args:
            name: Profile name.

        Returns:
            The profile.
        """
        return self.profiles[name]

    @property
    def service_profiles(self) -> list[str]:
        """Profiles a service may have, in table order."""
        return [n for n, p in self.profiles.items() if p.kind == "service"]


def _yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: not a mapping")
    return data


def load(directory: Path = DEFAULT_DIR) -> Contract:
    """Load the contract.

    Args:
        directory: The ``contract/`` directory.

    Returns:
        The contract.
    """
    profiles_data = _yaml(directory / PROFILES_FILE)
    reserved_data = _yaml(directory / RESERVED_FILE)
    reserved = {
        name: Namespace(frozenset(entry.get("names", [])), tuple(entry.get("patterns", [])))
        for name, entry in reserved_data["namespaces"].items()
    }
    reserved_paths = {
        base: frozenset(segments) for base, segments in reserved_data["path_segments"].items()
    }
    return Contract(
        version=int(profiles_data["contract_version"]),
        schema=json.loads((directory / SCHEMA_FILE).read_text(encoding="utf-8")),
        profiles={n: Profile(n, d) for n, d in profiles_data["profiles"].items()},
        caps=dict(profiles_data["caps"]),
        defaults=dict(profiles_data["defaults"]),
        relaxations=dict(profiles_data["relaxations"]),
        human_classes=dict(profiles_data["human_classes"]),
        reserved=reserved,
        reserved_paths=reserved_paths,
    )
