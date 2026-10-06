#!/opt/pokus/ansible-venv/bin/python3 -I
# Managed by pokus-infra (ansible/roles/images/files/pokus_image_files.py). Local changes will be overwritten.
"""Check the files of an exported Incus image (BUILD_PROMPT §7.13, §7.15).

``incus image export <fingerprint> <dir>/`` writes either one unified tarball
``<fingerprint>.<ext>`` or a split image, the metadata tarball
``meta-<fingerprint>.<ext>`` and the root file system ``<fingerprint>.<ext>``.
The fingerprint is the sha256 of the tarball, or of the metadata followed by
the root file system. ``verify`` finds the files of a fingerprint in a
directory, recomputes it and, with ``--write-sums``, records every file's
sha256 in ``SHA256SUMS`` (``sha256sum`` format). Its JSON output names the
files in the order ``incus image import`` takes them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

CHUNK = 1 << 20
SUMS = "SHA256SUMS"
FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")


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
    args = parser.parse_args(list(argv))
    try:
        image = verify(args.dir, args.fingerprint)
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    changed = write_sums(args.dir, image) if args.write_sums else False
    print(json.dumps({"ok": True, "changed": changed, **asdict(image)}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
