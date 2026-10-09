"""Where the installer reads and writes (BUILD_PROMPT §18.3).

Every path derives from the infra directory of the checkout and a few host
directories, so the tests run against a copy of the repository in a temporary
directory.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

INFRA = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Paths:
    """The installer's files.

    Attributes:
        infra: The ``infra/`` directory of the checkout.
        state_file: Resume state (0600 root, not in git).
        run_dir: Run-time directory (events, vault password handover).
        stored_vault_pass: Optional root-only copy of the vault password.
    """

    infra: Path = INFRA
    state_file: Path = Path("/var/lib/pinka/installer/state.json")
    run_dir: Path = Path("/run/pinka/installer")
    stored_vault_pass: Path = Path("/etc/pinka/secrets/vault-pass")

    @property
    def host_vars_dir(self) -> Path:
        """``ansible/inventory/host_vars``."""
        return self.infra / "ansible" / "inventory" / "host_vars"

    def host_vars(self, machine: str) -> Path:
        """The host_vars file of a machine.

        Args:
            machine: Machine name.

        Returns:
            ``host_vars/<machine>.yml``.
        """
        return self.host_vars_dir / f"{machine}.yml"

    @property
    def example_host_vars(self) -> Path:
        """The documented example the first host_vars file starts from."""
        return self.host_vars_dir / "example-machine.yml"

    @property
    def group_vars_main(self) -> Path:
        """``ansible/group_vars/all/main.yml`` (researchers section)."""
        return self.infra / "ansible" / "group_vars" / "all" / "main.yml"

    @property
    def vault(self) -> Path:
        """``ansible/group_vars/all/vault.yml`` (encrypted)."""
        return self.infra / "ansible" / "group_vars" / "all" / "vault.yml"

    @property
    def vault_example(self) -> Path:
        """The documented vault layout."""
        return self.infra / "ansible" / "group_vars" / "all" / "vault.yml.example"

    @property
    def users_dir(self) -> Path:
        """``users/`` (researchers' SSH public keys)."""
        return self.infra / "users"

    def public_keys(self, researcher: str) -> Path:
        """A researcher's key file.

        Args:
            researcher: User name.

        Returns:
            ``users/<name>.pub``.
        """
        return self.users_dir / f"{researcher}.pub"

    @property
    def answers_dir(self) -> Path:
        """``installer-answers/``."""
        return self.infra / "installer-answers"

    def answers(self, machine: str) -> Path:
        """A machine's answers file.

        Args:
            machine: Machine name.

        Returns:
            ``installer-answers/<machine>.yml``.
        """
        return self.answers_dir / f"{machine}.yml"

    @property
    def models_lock(self) -> Path:
        """``models.lock.yml``."""
        return self.infra / "models.lock.yml"

    @property
    def models_catalog(self) -> Path:
        """``models.catalog.yml``."""
        return self.infra / "models.catalog.yml"

    @property
    def vllm_profiles(self) -> Path:
        """``vllm/profiles/``."""
        return self.infra / "vllm" / "profiles"

    @property
    def images_dir(self) -> Path:
        """``images/`` (golden image blueprints)."""
        return self.infra / "images"

    @property
    def versions_lock(self) -> Path:
        """``versions.lock.yml``."""
        return self.infra / "versions.lock.yml"

    @property
    def postgres_defaults(self) -> Path:
        """The postgres role's defaults (database role names)."""
        return self.infra / "ansible" / "roles" / "postgres" / "defaults" / "main.yml"

    @property
    def preflight(self) -> Path:
        """``preflight/pinka-preflight``."""
        return self.infra / "preflight" / "pinka-preflight"

    @property
    def bootstrap(self) -> Path:
        """``bootstrap/bootstrap.sh``."""
        return self.infra / "bootstrap" / "bootstrap.sh"

    @property
    def events_file(self) -> Path:
        """Where the Ansible callback and bootstrap write events for the TUI."""
        return self.run_dir / "events.jsonl"


def invoking_user() -> tuple[int, int] | None:
    """The user who ran ``sudo``, whose checkout the installer writes into.

    Returns:
        ``(uid, gid)`` from SUDO_UID and SUDO_GID, or None when not run by sudo.
    """
    uid, gid = os.environ.get("SUDO_UID", ""), os.environ.get("SUDO_GID", "")
    if uid.isdigit() and gid.isdigit() and int(uid) != 0:
        return int(uid), int(gid)
    return None


def write_file(path: Path, text: str, mode: int = 0o644, *, chown: bool = True) -> None:
    """Write a file of the checkout atomically, owned by the invoking user.

    The repository never becomes root-owned (BUILD_PROMPT §18.2): files and
    the directories created for them belong to SUDO_UID:SUDO_GID.

    Args:
        path: Destination.
        text: Content.
        mode: File mode.
        chown: Give the file to the invoking user (False for root-only files).
    """
    owner = invoking_user() if chown else None
    missing = [p for p in [path.parent, *path.parent.parents] if not p.exists()]
    path.parent.mkdir(parents=True, exist_ok=True)
    if owner is not None:
        for directory in missing:
            os.chown(directory, *owner)
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        tmp.chmod(mode)
        if owner is not None:
            os.chown(tmp, *owner)
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
