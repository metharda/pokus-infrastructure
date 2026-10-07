"""Researchers' SSH public keys in ``users/<name>.pub`` (BUILD_PROMPT §18.3, §18.4 screen 7).

Keys are validated before anything is written: private keys are refused
explicitly, every key is parsed and fingerprinted, and ``ssh-keygen -lf``
confirms it when OpenSSH is installed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pokus_installer import validators
from pokus_installer.paths import Paths, write_file

HEADER = "# SSH public keys of {name} (one per line), written by the Pokus installer.\n"


@dataclass(frozen=True)
class KeyCheck:
    """The result of checking pasted or read keys.

    Attributes:
        keys: The valid keys.
        errors: One message per rejected line.
    """

    keys: tuple[validators.SshKey, ...]
    errors: tuple[str, ...]

    @property
    def fingerprints(self) -> list[str]:
        """The keys' fingerprints with their types and comments, for display."""
        return [f"{k.fingerprint} {k.key_type} {k.comment}".rstrip() for k in self.keys]


def check(text: str, *, use_ssh_keygen: bool = True) -> KeyCheck:
    """Check public keys, pasted or read from a file.

    Args:
        text: One key per line.
        use_ssh_keygen: Confirm each key with ``ssh-keygen -lf`` when installed.

    Returns:
        The keys and the errors.
    """
    keys, errors = validators.parse_ssh_keys(text)
    confirmed = []
    for key in keys:
        if use_ssh_keygen:
            try:
                fingerprint = validators.ssh_keygen_fingerprint(key.line)
            except ValueError as exc:
                errors.append(f"{key.fingerprint}: ssh-keygen: {exc}")
                continue
            if fingerprint is not None and fingerprint != key.fingerprint:
                errors.append(f"{key.fingerprint}: ssh-keygen reports {fingerprint}")
                continue
        confirmed.append(key)
    return KeyCheck(tuple(confirmed), tuple(errors))


def read(path: Path) -> str:
    """Read a key file, or an empty string if it does not exist.

    Args:
        path: ``users/<name>.pub`` or a file the operator named.

    Returns:
        Its text.
    """
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def render(name: str, keys: tuple[validators.SshKey, ...]) -> str:
    """The content of a researcher's key file.

    Args:
        name: User name.
        keys: Valid keys.

    Returns:
        The file text.
    """
    return HEADER.format(name=name) + "".join(f"{k.line}\n" for k in keys)


def write(paths: Paths, name: str, keys: tuple[validators.SshKey, ...]) -> Path:
    """Write ``users/<name>.pub``, owned by the invoking user.

    Args:
        paths: Installer paths.
        name: User name.
        keys: Valid keys.

    Returns:
        The file.
    """
    target = paths.public_keys(name)
    write_file(target, render(name, keys), 0o644)
    return target
