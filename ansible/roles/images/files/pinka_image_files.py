#!/opt/pinka/ansible-venv/bin/python3 -I
# Managed by pinka (ansible/roles/images/files/pinka_image_files.py). Local changes will be overwritten.
"""Check the files of an exported Incus image.

``incus image export <fingerprint> <dir>/`` writes either one unified tarball
``<fingerprint>.<ext>`` or a split image, the metadata tarball
``meta-<fingerprint>.<ext>`` and the root file system ``<fingerprint>.<ext>``.
The fingerprint is the sha256 of the tarball, or of the metadata followed by
the root file system. ``verify`` finds the files of a fingerprint in a
directory, recomputes it and, with ``--write-sums``, records every file's
sha256 in ``SHA256SUMS`` (``sha256sum`` format) and, with ``--properties``,
reads the image's properties from the ``metadata.yaml`` of its metadata
tarball, which the fingerprint covers: the gzip tarball's first member, read
in memory within a limit on the compressed and on the uncompressed bytes,
nothing extracted to disk. Its JSON output names the files in the order
``incus image import`` takes them.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import tarfile
import zlib
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

CHUNK = 1 << 20
SUMS = "SHA256SUMS"
FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")
METADATA = "metadata.yaml"
# A metadata.yaml larger than this is refused.
METADATA_LIMIT = 1 << 20
# The uncompressed bytes read: metadata.yaml and its headers (a PAX header
# with the file's extended attributes).
UNCOMPRESSED_LIMIT = METADATA_LIMIT + (64 << 10)
# The compressed bytes read: gzip never grows data by more than a little.
COMPRESSED_LIMIT = 2 * UNCOMPRESSED_LIMIT
GZIP_MAGIC = b"\x1f\x8b"
READ_SIZE = 64 << 10


@dataclass(frozen=True)
class ImageFiles:
    """The files of one exported image.

    Attributes:
        fingerprint: The image's fingerprint.
        split: True for a metadata tarball plus root file system.
        files: File names in import order (metadata first).
        sha256: sha256 of every file.
    """

    fingerprint: str
    split: bool
    files: list[str]
    sha256: dict[str, str]


def find(directory: Path, fingerprint: str) -> list[Path]:
    """Return the files of a fingerprint in import order.

    Args:
        directory: Export directory.
        fingerprint: Full fingerprint.

    Returns:
        ``[metadata, rootfs]`` for a split image, ``[tarball]`` otherwise.

    Raises:
        ValueError: If the fingerprint is malformed or the files are missing or ambiguous.
    """
    if not FINGERPRINT_RE.match(fingerprint):
        raise ValueError(f"not a full fingerprint: {fingerprint!r}")
    meta = sorted(directory.glob(f"meta-{fingerprint}*"))
    main = sorted(p for p in directory.glob(f"{fingerprint}*") if p.name != SUMS)
    if len(meta) > 1 or len(main) != 1:
        raise ValueError(
            f"{directory}: expected one image file for {fingerprint}, found {len(main)}"
        )
    return meta + main


def sha256_file(path: Path, also: Callable[[bytes], None] | None = None) -> str:
    """Hash a file, optionally passing its bytes on as well.

    Args:
        path: File to hash.
        also: Receives the same bytes (the fingerprint's running digest).

    Returns:
        The file's own sha256.
    """
    own = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            own.update(chunk)
            if also is not None:
                also(chunk)
    return own.hexdigest()


def verify(directory: Path, fingerprint: str) -> ImageFiles:
    """Recompute an exported image's fingerprint.

    Args:
        directory: Export directory.
        fingerprint: Expected fingerprint.

    Returns:
        The image's files.

    Raises:
        ValueError: If the files are missing or do not produce the fingerprint.
    """
    paths = find(directory, fingerprint)
    combined = hashlib.sha256()
    sums = {p.name: sha256_file(p, combined.update) for p in paths}
    if combined.hexdigest() != fingerprint:
        raise ValueError(
            f"{directory}: the files hash to {combined.hexdigest()}, not {fingerprint}"
        )
    return ImageFiles(fingerprint, len(paths) > 1, [p.name for p in paths], sums)


def head(path: Path) -> bytes:
    """The start of a gzip file, decompressed, within both limits.

    Args:
        path: A gzip file.

    Returns:
        At most ``UNCOMPRESSED_LIMIT`` bytes, from at most ``COMPRESSED_LIMIT``
        bytes of the file.

    Raises:
        ValueError: If the file is not gzip.
    """
    out = bytearray()
    inflate = zlib.decompressobj(16 + zlib.MAX_WBITS)  # gzip only
    read = 0
    with path.open("rb") as handle:
        start = handle.read(len(GZIP_MAGIC))
        if start != GZIP_MAGIC:
            raise ValueError(f"{path}: not a gzip tarball")
        pending = start
        read = len(start)
        try:
            while len(out) < UNCOMPRESSED_LIMIT and not inflate.eof:
                data = inflate.unconsumed_tail or pending
                pending = b""
                if not data:
                    # At the limit this reads 0 bytes, which ends the loop as
                    # the end of the file does.
                    data = handle.read(min(READ_SIZE, COMPRESSED_LIMIT - read))
                    if not data:
                        break
                    read += len(data)
                out += inflate.decompress(data, UNCOMPRESSED_LIMIT - len(out))
        except zlib.error as exc:
            raise ValueError(f"{path}: not a gzip tarball: {exc}") from exc
    return bytes(out)


def properties(directory: Path, image: ImageFiles) -> dict[str, str]:
    """Read an image's properties from the metadata.yaml it was exported with.

    At publication Incus stores exactly the properties of the image's
    ``metadata.yaml``; a client of the image store can change the stored
    record later, never these files, which the fingerprint covers. Incus
    writes ``metadata.yaml`` as the tarball's first member
    (``internal/server/instance/drivers/driver_lxc.go``, ``Export``), so only
    that member is read: in memory, from the first ``COMPRESSED_LIMIT`` bytes
    of the file, decompressed to at most ``UNCOMPRESSED_LIMIT``; nothing is
    extracted to disk.

    Args:
        directory: Export directory.
        image: Verified files (the metadata tarball, or the unified tarball, first).

    Returns:
        The ``properties`` of ``metadata.yaml`` (empty when it names none).

    Raises:
        ValueError: If the file is not a gzip tarball, its first member is not
            a regular file ``metadata.yaml`` of at most ``METADATA_LIMIT``
            bytes within the limits (a link, a device, a directory or any
            other member first is refused), or its properties are not a
            mapping of strings to strings.
    """
    path = directory / image.files[0]
    start = head(path)
    within = f"the first {len(start)} bytes"
    try:
        with tarfile.open(fileobj=io.BytesIO(start), mode="r:") as tar:
            member = tar.next()
            if tar.pax_headers:
                raise ValueError(f"{path}: a global header before {METADATA}")
            if member is None or member.name != METADATA:
                name = "nothing" if member is None else repr(member.name)
                raise ValueError(f"{path}: its first member is {name}, not {METADATA}")
            if member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE) or member.sparse:
                raise ValueError(f"{path}: {METADATA} is not a regular file")
            if member.size > METADATA_LIMIT:
                raise ValueError(f"{path}: {METADATA} is larger than {METADATA_LIMIT} bytes")
            handle = tar.extractfile(member)
            if handle is None:
                raise ValueError(f"{path}: {METADATA} cannot be read")
            text = handle.read()
    except (tarfile.TarError, EOFError) as exc:
        raise ValueError(f"{path}: no complete {METADATA} in {within}: {exc}") from exc
    try:
        # Every scalar as its text, as Incus reads it into strings:
        # pinka.built_at stays a string, not a timestamp.
        meta = yaml.load(text, Loader=yaml.BaseLoader)  # noqa: S506 - BaseLoader builds only strings, lists and mappings
    except yaml.YAMLError as exc:
        raise ValueError(f"{path}: {METADATA} is not YAML: {exc}") from exc
    if not isinstance(meta, dict):
        raise ValueError(f"{path}: {METADATA} is not a mapping")
    found = meta.get("properties")
    if found is None:
        return {}
    if not isinstance(found, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in found.items()
    ):
        raise ValueError(f"{path}: the properties of {METADATA} are not strings")
    return dict(found)


def write_sums(directory: Path, image: ImageFiles) -> bool:
    """Write ``SHA256SUMS`` unless it already has this content.

    Args:
        directory: Export directory.
        image: Verified files.

    Returns:
        True if the file was written.
    """
    text = "".join(f"{image.sha256[name]}  {name}\n" for name in image.files)
    path = directory / SUMS
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        return False
    tmp = path.with_name(SUMS + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.chmod(0o640)
    tmp.replace(path)
    return True


def main(argv: Sequence[str]) -> int:
    """Run ``verify``.

    Args:
        argv: Arguments without the program name.

    Returns:
        0 when the files match the fingerprint, 1 otherwise.
    """
    parser = argparse.ArgumentParser(prog="image-files", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["verify"])
    parser.add_argument("--dir", type=Path, required=True)
    parser.add_argument("--fingerprint", required=True)
    parser.add_argument("--write-sums", action="store_true")
    parser.add_argument("--properties", action="store_true")
    args = parser.parse_args(list(argv))
    try:
        image = verify(args.dir, args.fingerprint)
        found = {"properties": properties(args.dir, image)} if args.properties else {}
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    changed = write_sums(args.dir, image) if args.write_sums else False
    print(json.dumps({"ok": True, "changed": changed, **asdict(image), **found}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
