"""Helpers of the installer tests: SSH keys generated at run time (never committed)."""

from __future__ import annotations

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa


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
