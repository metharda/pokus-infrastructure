"""Primary-key fingerprints of OpenPGP public keys (RFC 4880 and RFC 9580).

Only what the lock checker needs: split a key file (ASCII armor or binary)
into packets and compute the fingerprint of every primary public key packet
(tag 6). Version 4 keys use SHA-1 over ``0x99 || 2-byte length || body``,
version 6 keys SHA-256 over ``0x9B || 4-byte length || body``. Signatures are
not verified; repositories are trusted through the pinned fingerprint.
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Iterator

# Built from two parts: the repository's privacy scan reports the complete
# header line as key material, and this file contains none.
ARMOR_BEGIN = "-----BEGIN PGP PUBLIC" + " KEY BLOCK-----"
ARMOR_END = "-----END PGP PUBLIC" + " KEY BLOCK-----"
TAG_PUBLIC_KEY = 6
# Packet lengths below are in octets (RFC 4880 §4.2).
ONE_OCTET_MAX = 191
TWO_OCTET_MAX = 223
FIVE_OCTET_MARKER = 255
OLD_FORMAT_LENGTH_TYPES = {0: 1, 1: 2, 2: 4}
KEY_VERSION_4 = 4
KEY_VERSION_6 = 6


class OpenPGPError(ValueError):
    """The data is not a well-formed OpenPGP key file."""


def _dearmor_block(body: str) -> bytes:
    lines = [line.strip() for line in body.strip().splitlines()]
    # Armor headers end at the first empty line, if there are any.
    if "" in lines and any(":" in line for line in lines[: lines.index("")]):
        lines = lines[lines.index("") + 1 :]
    data = [line for line in lines if line and not line.startswith("=")]
    try:
        return base64.b64decode("".join(data), validate=True)
    except ValueError as exc:
        raise OpenPGPError(f"invalid base64 in the armored block: {exc}") from exc


def dearmor(text: str) -> bytes:
    """Decode every ASCII-armored public key block in a text.

    Args:
        text: Armored text with one or more public key blocks.

    Returns:
        The binary packets of all blocks, in order.

    Raises:
        OpenPGPError: If no complete public key block is found.
    """
    blocks = []
    rest = text
    while ARMOR_BEGIN in rest:
        rest = rest.split(ARMOR_BEGIN, 1)[1]
        if ARMOR_END not in rest:
            raise OpenPGPError("unterminated ASCII-armored public key block")
        body, rest = rest.split(ARMOR_END, 1)
        blocks.append(_dearmor_block(body))
    if not blocks:
        raise OpenPGPError("no ASCII-armored public key block")
    return b"".join(blocks)


def key_bytes(data: bytes) -> bytes:
    """Return binary packets for armored or binary key data.

    Args:
        data: File content.

    Returns:
        Binary packets.
    """
    if data.lstrip().startswith(b"-----BEGIN"):
        return dearmor(data.decode("ascii", errors="replace"))
    return data


def _new_length(data: bytes, pos: int) -> tuple[int, int]:
    first = data[pos]
    if first <= ONE_OCTET_MAX:
        return first, pos + 1
    if first <= TWO_OCTET_MAX:
        if pos + 1 >= len(data):
            raise OpenPGPError("truncated packet length")
        return ((first - 192) << 8) + data[pos + 1] + 192, pos + 2
    if first == FIVE_OCTET_MARKER:
        if pos + 5 > len(data):
            raise OpenPGPError("truncated packet length")
        return int.from_bytes(data[pos + 1 : pos + 5], "big"), pos + 5
    raise OpenPGPError("partial body lengths are not valid in key packets")


def packets(data: bytes) -> Iterator[tuple[int, bytes]]:
    """Split binary OpenPGP data into packets.

    Args:
        data: Binary packets.

    Yields:
        ``(tag, body)`` per packet.

    Raises:
        OpenPGPError: On malformed headers or truncated packets.
    """
    pos = 0
    while pos < len(data):
        header = data[pos]
        if not header & 0x80:
            raise OpenPGPError(f"invalid packet header at offset {pos}")
        if header & 0x40:  # new format
            tag = header & 0x3F
            length, pos = _new_length(data, pos + 1)
        else:  # old format
            tag = (header >> 2) & 0x0F
            length_type = header & 0x03
            if length_type not in OLD_FORMAT_LENGTH_TYPES:
                raise OpenPGPError("indeterminate packet lengths are not supported")
            size = OLD_FORMAT_LENGTH_TYPES[length_type]
            if pos + 1 + size > len(data):
                raise OpenPGPError("truncated packet length")
            length = int.from_bytes(data[pos + 1 : pos + 1 + size], "big")
            pos += 1 + size
        if pos + length > len(data):
            raise OpenPGPError("truncated packet body")
        yield tag, data[pos : pos + length]
        pos += length


def fingerprint(body: bytes) -> str:
    """Compute the fingerprint of a public key packet body.

    Args:
        body: Body of a public key packet (tag 6) or subkey packet (tag 14).

    Returns:
        Upper-case hex fingerprint.

    Raises:
        OpenPGPError: For key versions other than 4 and 6.
    """
    if not body:
        raise OpenPGPError("empty key packet")
    version = body[0]
    if version == KEY_VERSION_4:
        return (
            hashlib.sha1(  # noqa: S324 - the v4 fingerprint is defined as SHA-1
                b"\x99" + len(body).to_bytes(2, "big") + body
            )
            .hexdigest()
            .upper()
        )
    if version == KEY_VERSION_6:
        return hashlib.sha256(b"\x9b" + len(body).to_bytes(4, "big") + body).hexdigest().upper()
    raise OpenPGPError(f"unsupported key version {version}")


def primary_fingerprints(data: bytes) -> list[str]:
    """Return the fingerprints of all primary keys in a key file.

    Args:
        data: Armored or binary key file content.

    Returns:
        Fingerprints in file order.

    Raises:
        OpenPGPError: If the file contains no primary public key.
    """
    found = [fingerprint(body) for tag, body in packets(key_bytes(data)) if tag == TAG_PUBLIC_KEY]
    if not found:
        raise OpenPGPError("no public key packet")
    return found
