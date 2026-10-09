"""The lab's description for pinka (``/etc/pinka/lab.json``).

The doctor role renders this file from the Ansible variables of the host:
what must run, which images and digests are pinned, the layout, the accounts,
the Incus objects and where the lab keeps its state. pinka never reads
the repository or the vault; everything it compares against is in here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pinka_lab.system import System

DEFAULT_PATH = "/etc/pinka/lab.json"
SCHEMA_VERSION = 1
REQUIRED = ("machine", "host_vars", "infra_dir", "pinka_root", "node_role", "provider", "features")


class ConfigError(RuntimeError):
    """The lab description is missing or unusable."""


@dataclass(frozen=True)
class LabConfig:
    """Typed access to the lab description.

    Attributes:
        data: The parsed file.
    """

    data: dict[str, Any]

    def get(self, *keys: str, default: Any = None) -> Any:  # noqa: ANN401 - JSON value
        """Read a nested value.

        Args:
            *keys: Path of keys.
            default: Value when a key is missing.

        Returns:
            The value.
        """
        value: Any = self.data
        for key in keys:
            if not isinstance(value, dict) or key not in value:
                return default
            value = value[key]
        return value

    @property
    def machine(self) -> str:
        """Machine name (tailnet host name, snapshot host)."""
        return str(self.data["machine"])

    @property
    def host_vars(self) -> str:
        """Name of the host_vars file (bootstrap ``--machine``)."""
        return str(self.data["host_vars"])

    @property
    def infra_dir(self) -> str:
        """Infra directory of the checkout bootstrap runs from."""
        return str(self.data["infra_dir"])

    @property
    def pinka_root(self) -> str:
        """Research space root."""
        return str(self.data["pinka_root"])

    @property
    def node_role(self) -> str:
        """``full``, ``lab`` or ``inference``."""
        return str(self.data["node_role"])

    @property
    def provider(self) -> str:
        """``baremetal``, ``wsl2`` or ``vm``."""
        return str(self.data["provider"])

    def feature(self, name: str) -> bool:
        """Whether a host profile feature is on.

        Args:
            name: Feature name.

        Returns:
            The flag.
        """
        return bool(self.data["features"].get(name, False))

    @property
    def runs_lab(self) -> bool:
        """True on nodes that run the lab services (PostgreSQL, Incus)."""
        return self.node_role in ("full", "lab")

    @property
    def serves_inference(self) -> bool:
        """True on nodes that serve inference with a GPU."""
        return self.feature("gpu_inference") and self.node_role in ("full", "inference")


def load(system: System, path: str = DEFAULT_PATH) -> LabConfig:
    """Read and check the lab description.

    Args:
        system: Host access.
        path: File to read.

    Returns:
        The description.

    Raises:
        ConfigError: If the file is missing, not JSON, of another schema
            version, or lacks a required key.
    """
    text = system.read_text(path)
    if text is None:
        raise ConfigError(f"{path} does not exist; run bootstrap to install pinka")
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        raise ConfigError(f"{path}: expected schema_version {SCHEMA_VERSION}")
    missing = [key for key in REQUIRED if key not in data]
    if missing:
        raise ConfigError(f"{path}: missing {', '.join(missing)}")
    return LabConfig(data)
