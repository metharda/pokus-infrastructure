"""Signed apt package indexes: the release file's signature, then the index's hash.

An apt repository publishes a release file (``InRelease``), signed with the
repository's key, which states the hash of every package index. A package
index read here is trusted only when the release file's signature verifies
with the given keyring alone (``gpgv``: a vendor's pinned key, or a
distribution's archive keyring) and the index has the hash the release file
states (SHA256, or SHA512 where a repository states only that). What is read
of the release file is the text gpgv verified (its output), never the file
as downloaded: gpgv accepts unsigned text before and after the signed block.
Two layouts: ``<uri>/dists/<suite>/InRelease`` with the indexes below
``<component>/binary-<arch>/``, and a flat repository (suite ``/``) with
``<uri>/InRelease`` and the indexes next to it.

Network and ``gpgv`` are parameters, so the tests run without either.
"""

from __future__ import annotations

import gzip
import hashlib
import lzma
import re
import shutil
import subprocess
import tempfile
import zlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

# The index files a release file may list, in the order they are tried.
INDEX_FILES: tuple[tuple[str, Callable[[bytes], bytes]], ...] = (
    ("Packages.xz", lzma.decompress),
    ("Packages.gz", gzip.decompress),
    ("Packages", bytes),
)
# The hash fields of a release file, in the order they are preferred.
HASHES = (("SHA256", hashlib.sha256), ("SHA512", hashlib.sha512))


class AptIndexError(RuntimeError):
    """A release file or an index cannot be trusted or read; the message says why."""


def verify_signature(keyring: bytes, signed: bytes) -> str:
    """Check a clear-signed release file with ``gpgv`` and the given keyring alone.

    Args:
        keyring: The keyring's packets, binary (not armored).
        signed: The release file (``InRelease``).

    Returns:
        The signed text as gpgv verified it (its output): what lies outside
        the signed block, which gpgv ignores, is not in it.

    Raises:
        AptIndexError: If gpgv is missing, the signature does not verify or the
            signed text is not UTF-8.
    """
    command = shutil.which("gpgv")
    if command is None:
        raise AptIndexError("gpgv is not installed (Debian and Ubuntu package gpgv)")
    with tempfile.TemporaryDirectory() as tmp:
        ring = Path(tmp, "keyring.gpg")
        ring.write_bytes(keyring)
        done = subprocess.run(  # noqa: S603 - fixed arguments
            [command, "--keyring", str(ring), "--output", "-", "-"],
            input=signed,
            capture_output=True,
            check=False,
        )
    if done.returncode != 0:
        detail = done.stderr.decode(errors="replace").strip().splitlines()
        raise AptIndexError(f"the signature does not verify with the key: {' / '.join(detail)}")
    try:
        return done.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AptIndexError(f"the signed text is not UTF-8: {exc}") from exc


def paragraphs(text: str) -> Iterable[dict[str, str]]:
    """The paragraphs of a Debian control file (a release file, a package index).

    Args:
        text: The file's text.

    Yields:
        One mapping per paragraph; a continued field's lines joined with newlines.
    """
    for block in re.split(r"\n\s*\n", text):
        fields: dict[str, str] = {}
        key = ""
        for line in block.splitlines():
            if line.startswith((" ", "\t")) and key:
                fields[key] += "\n" + line.strip()
            elif ":" in line:
                key, _, value = line.partition(":")
                fields[key] = value.strip()
        if fields:
            yield fields


def release_hash(release: str, path: str) -> tuple[str, str] | None:
    """The hash a release file states for one of its files.

    Args:
        release: The release file's signed text.
        path: The file's path relative to the release file.

    Returns:
        ``(field, hex digest)`` from the first hash field that names the file
        (SHA256 before SHA512), or None when none does.
    """
    head = next(iter(paragraphs(release)), {})
    for name, _ in HASHES:
        for line in head.get(name, "").splitlines():
            parts = line.split()
            if len(parts) == 3 and parts[2] == path:  # noqa: PLR2004 - hash size path
                return name, parts[0]
    return None


@dataclass(frozen=True)
class Index:
    """One package index, read through its signed release file.

    Attributes:
        url: Where the index was read.
        versions: ``{package: {version, ...}}``, every version the index lists.
    """

    url: str
    versions: dict[str, set[str]]


@dataclass(frozen=True)
class Repository:
    """Where a release file is and where its indexes are, relative to it.

    Attributes:
        base: The directory of the release file (``.../dists/<suite>``, or the
            flat repository's URI).
        prefix: The indexes' directory below ``base`` (``main/binary-amd64/``,
            or empty in a flat repository).
        component: The component the release file must name, or empty.
    """

    base: str
    prefix: str
    component: str = ""

    @classmethod
    def of(cls, uri: str, suite: str, component: str, apt_arch: str) -> Repository:
        """The repository of a ``uri``/``suite``/``component`` line, for one architecture."""
        uri = uri.rstrip("/")
        if suite == "/":
            return cls(uri, "")
        return cls(f"{uri}/dists/{suite}", f"{component}/binary-{apt_arch}/", component)


def read_release(
    fetch: Callable[[str], bytes], verify: Callable[[bytes, bytes], str], keyring: bytes, base: str
) -> str:
    """Download a release file and check its signature.

    Args:
        fetch: ``url -> content``.
        verify: ``(keyring, signed) -> signed text`` (:func:`verify_signature`),
            raising on a bad signature.
        keyring: The keyring to check it with, binary.
        base: The release file's directory.

    Returns:
        The release file's text as the signature check verified it.

    Raises:
        AptIndexError: If the signature does not verify.
    """
    url = f"{base}/InRelease"
    signed = fetch(url)
    try:
        return verify(keyring, signed)
    except AptIndexError as exc:
        raise AptIndexError(f"{url}: {exc}") from exc


def read_index(fetch: Callable[[str], bytes], release: str, repository: Repository) -> Index:
    """Download a package index and check it against its signed release file.

    Args:
        fetch: ``url -> content``.
        release: The release file's signed text (:func:`read_release`).
        repository: Where the index is.

    Returns:
        The index's packages and versions.

    Raises:
        AptIndexError: If the release file does not name the component or the
            index, the index's hash differs from the one it states, or the
            index cannot be decompressed or decoded.
    """
    head = next(iter(paragraphs(release)), {})
    component = repository.component
    if component and component not in head.get("Components", "").split():
        raise AptIndexError(f"{repository.base}/InRelease names no component {component!r}")
    for name, opener in INDEX_FILES:
        path = f"{repository.prefix}{name}"
        stated = release_hash(release, path)
        if stated is None:
            continue
        field, expected = stated
        url = f"{repository.base}/{path}"
        data = fetch(url)
        actual = dict(HASHES)[field](data).hexdigest()
        if actual != expected:
            raise AptIndexError(f"{url} has {field} {actual}, InRelease says {expected}")
        try:
            text = opener(data).decode("utf-8")
        except (OSError, EOFError, lzma.LZMAError, zlib.error, UnicodeDecodeError) as exc:
            raise AptIndexError(f"{url} cannot be read: {exc}") from exc
        versions: dict[str, set[str]] = {}
        for paragraph in paragraphs(text):
            versions.setdefault(paragraph.get("Package", ""), set()).add(
                paragraph.get("Version", "")
            )
        return Index(url, versions)
    raise AptIndexError(
        f"{repository.base}/InRelease lists no package index in {repository.prefix or './'}"
    )
