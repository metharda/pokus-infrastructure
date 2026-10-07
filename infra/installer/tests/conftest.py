"""Fixtures of the installer tests: a copy of the files the installer reads."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from pokus_installer.paths import INFRA, Paths

COPIED = (
    "ansible/inventory/host_vars/example-machine.yml",
    "ansible/group_vars/all/main.yml",
    "ansible/group_vars/all/vault.yml.example",
    "ansible/roles/postgres/defaults/main.yml",
    "models.lock.yml",
    "models.catalog.yml",
    "versions.lock.yml",
    "vllm/profiles",
    "images",
    "tests/answers/ci.yml",
    "ansible/site.yml",
    "bootstrap/lib/exitcodes.sh",
)


def copy_repo(base: Path) -> Paths:
    """A copy of the repository files the installer reads, and host paths below base."""
    root = base / "infra"
    for rel in COPIED:
        src = INFRA / rel
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
    return Paths(
        infra=root,
        state_file=base / "var" / "state.json",
        run_dir=base / "run",
        stored_vault_pass=base / "etc" / "vault-pass",
    )


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Paths:
    """A copy of the repository files the installer reads, and host paths below tmp_path."""
    for name in ("SUDO_UID", "SUDO_GID"):
        monkeypatch.delenv(name, raising=False)
    return copy_repo(tmp_path)
