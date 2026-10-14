"""Helpers of the installer tests: a repository copy and SSH keys made at run time."""

from __future__ import annotations

import shutil
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa

from pinka_installer.paths import INFRA, Paths

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


def public_key(kind: str = "ed25519", comment: str = "researcher1@example.com") -> str:
    """A fresh OpenSSH public key line (generated, never committed)."""
    if kind == "ed25519":
        key = ed25519.Ed25519PrivateKey.generate().public_key()
    elif kind == "ecdsa":
        key = ec.generate_private_key(ec.SECP256R1()).public_key()  # type: ignore[assignment]
    else:
        bits = int(kind.removeprefix("rsa"))
        key = rsa.generate_private_key(65537, bits).public_key()  # type: ignore[assignment]
    line = key.public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)
    return f"{line.decode()} {comment}".rstrip()


def private_key() -> str:
    """A fresh OpenSSH private key (to check that it is refused)."""
    key = ed25519.Ed25519PrivateKey.generate()
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.OpenSSH,
        serialization.NoEncryption(),
    ).decode()
