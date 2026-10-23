"""A collection artifact, checked before anything is installed from it: one rule for every path.

``make lock-collections``, ``make setup`` and bootstrap run this same check on
each Ansible collection artifact, so the pins and the installs never
disagree. It checks the artifact as ``ansible-galaxy collection verify``
checks an installed collection: ``MANIFEST.json`` names the collection and
version, ``FILES.json`` matches its checksum there, every file is listed with
its checksum, and nothing unlisted is in the tarball.

The members: a directory or a regular file is accepted, and a symbolic link
only when its target is relative and, resolved from the link's own
directory, stays inside the collection (``ansible-galaxy`` extracts a link
under the same condition) and names another member of the artifact. Galaxy
collections carry such links (``community.general`` links its test scripts),
and ``FILES.json`` lists a link to a file like that file, so the link counts
with the content of its target. Anything else is refused: an absolute or
escaping link, a link to nothing, a hard link, a device, a FIFO, a member
outside the collection.

Standard library only. Bootstrap runs it as a script with the Ansible
environment's Python: ``python -I artifact.py NAME VERSION FILE`` exits 0 when
the artifact checks out, 1 with the reason otherwise.
"""

from __future__ import annotations

import hashlib
import io
import json
import posixpath
import re
import sys
import tarfile
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

SPECIAL = ("MANIFEST.json", "FILES.json")
# A license text: any file in a LICENSES directory, or a text file (no
# extension, .txt, .md, .rst) whose name contains one of these words, such as
# COPYING, LICENSE.md or ansible.posix's PSF-license.txt. REUSE's ``.license``
# files, which annotate the file they are named after, are not texts.
LICENSE_WORDS = re.compile(r"copying|licen[cs]e|notice", re.IGNORECASE)
TEXT_EXTENSIONS = ("", ".txt", ".md", ".rst")
# Links followed from one member before the artifact is refused (a loop).
MAX_LINKS = 16
ROOT = "."


class ArtifactError(ValueError):
    """The artifact is refused; the message says why."""


def _kind(member: tarfile.TarInfo) -> str:
    if member.islnk():
        return "a hard link"
    if member.ischr() or member.isblk():
        return "a device"
    if member.isfifo():
        return "a FIFO"
    return f"of tar type {member.type!r}"


def _path(name: str) -> str:
    """A member's path inside the collection; refused when it lies outside."""
    path = name.removeprefix("./")
    parts = path.split("/")
    if path.startswith("/") or ".." in parts:
        raise ArtifactError(f"member {name} lies outside the collection")
    return posixpath.normpath(path)


def link_target(path: str, linkname: str) -> str:
    """Where a symbolic link inside the collection points, as a collection path.

    Args:
        path: The link's path in the collection.
        linkname: Its target as stored.

    Returns:
        The target's path (``.`` for the collection itself).

    Raises:
        ArtifactError: If the target is absolute or resolves outside the collection.
    """
    if linkname.startswith("/"):
        raise ArtifactError(f"member {path} is a link to the absolute path {linkname}")
    target = posixpath.normpath(posixpath.join(posixpath.dirname(path), linkname))
    if target == ".." or target.startswith("../"):
        raise ArtifactError(f"member {path} is a link to {linkname}, outside the collection")
    return target


def license_texts(paths: Iterable[str]) -> list[str]:
    """The license texts among an artifact's files.

    Args:
        paths: Its files' paths.

    Returns:
        The paths of its license texts (see ``LICENSE_WORDS``), sorted.
    """
    found = []
    for path in paths:
        stem, extension = posixpath.splitext(posixpath.basename(path))
        in_licenses = path.startswith("LICENSES/") or "/LICENSES/" in path
        if in_licenses or (extension.lower() in TEXT_EXTENSIONS and LICENSE_WORDS.search(stem)):
            found.append(path)
    return sorted(found)


def _directories(paths: Sequence[str]) -> set[str]:
    """Every directory the members imply (a tarball need not list each one)."""
    found = {ROOT}
    for path in paths:
        parent = posixpath.dirname(path)
        while parent and parent not in found:
            found.add(parent)
            parent = posixpath.dirname(parent)
    return found


def _resolve(
    by_path: Mapping[str, tarfile.TarInfo], directories: set[str], path: str
) -> tarfile.TarInfo | None:
    """The member a link ends at (None for a directory without a member of its own)."""
    seen = 0
    while True:
        member = by_path.get(path)
        if member is None:
            if path in directories:
                return None
            raise ArtifactError(f"a link points to {path}, which is not in the artifact")
        if not member.issym():
            return member
        seen += 1
        if seen > MAX_LINKS:
            raise ArtifactError(f"the links from {path} do not end at a file or directory")
        path = link_target(path, member.linkname)


def members(artifact: bytes) -> dict[str, bytes]:
    """The files of an artifact by path, links to files with their target's content.

    Args:
        artifact: The artifact (a gzip-compressed tarball).

    Returns:
        Content by path; directories and links to directories have none.

    Raises:
        ArtifactError: For any member the module docstring refuses.
    """
    files: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(artifact), mode="r:gz") as tar:
        by_path = {_path(m.name): m for m in tar.getmembers()}
        directories = _directories(list(by_path))
        for path, member in by_path.items():
            content = member
            if member.isdir():
                continue
            if member.issym():
                target = _resolve(by_path, directories, link_target(path, member.linkname))
                if target is None or target.isdir():
                    continue
                if not target.isfile():
                    raise ArtifactError(f"member {path} is a link to {_kind(target)}")
                content = target
            elif not member.isfile():
                raise ArtifactError(f"member {path} is {_kind(member)}")
            extracted = tar.extractfile(content)
            if extracted is None:  # pragma: no cover - regular files always have content
                raise ArtifactError(f"member {path} cannot be read")
            files[path] = extracted.read()
    return files


def check(name: str, version: str, artifact: bytes) -> dict[str, Any]:
    """Check an artifact like ``ansible-galaxy collection verify``.

    Args:
        name: ``namespace.name``.
        version: The version it must be.
        artifact: The artifact.

    Returns:
        Its ``collection_info``, with ``license_files`` added.

    Raises:
        ArtifactError: On any difference.
    """
    files = members(artifact)
    for special in SPECIAL:
        if special not in files:
            raise ArtifactError(f"{name} {version}: the artifact has no {special}")
    manifest = json.loads(files["MANIFEST.json"])
    info = manifest.get("collection_info") or {}
    found = f"{info.get('namespace')}.{info.get('name')}"
    if found != name or info.get("version") != version:
        raise ArtifactError(f"{name} {version}: MANIFEST.json names {found} {info.get('version')}")
    listing = manifest.get("file_manifest_file") or {}
    if listing.get("chksum_type") != "sha256" or listing.get("chksum_sha256") != (
        hashlib.sha256(files["FILES.json"]).hexdigest()
    ):
        raise ArtifactError(f"{name} {version}: FILES.json does not match MANIFEST.json")
    listed: dict[str, str] = {}
    for entry in json.loads(files["FILES.json"]).get("files", []):
        if entry.get("ftype") == "file":
            if entry.get("chksum_type") != "sha256":
                raise ArtifactError(
                    f"{name} {version}: {entry.get('name')} has no sha256 in FILES.json"
                )
            listed[str(entry["name"])] = str(entry["chksum_sha256"])
    present = {path for path in files if path not in SPECIAL}
    if missing := sorted(set(listed) - present):
        raise ArtifactError(f"{name} {version}: listed but missing: {', '.join(missing[:5])}")
    if extra := sorted(present - set(listed)):
        raise ArtifactError(
            f"{name} {version}: in the tarball but not listed: {', '.join(extra[:5])}"
        )
    if wrong := sorted(
        p for p, digest in listed.items() if hashlib.sha256(files[p]).hexdigest() != digest
    ):
        raise ArtifactError(
            f"{name} {version}: checksum differs from FILES.json: {', '.join(wrong[:5])}"
        )
    info = dict(info)
    info["license_files"] = license_texts(present)
    return info


def main(argv: Sequence[str] | None = None) -> int:
    """``artifact.py NAME VERSION FILE``: 0 when the artifact checks out, else 1."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 3:  # noqa: PLR2004 - NAME VERSION FILE
        sys.stderr.write("usage: artifact.py NAME VERSION FILE\n")
        return 2
    name, version, file = args
    try:
        check(name, version, Path(file).read_bytes())
    except (ArtifactError, OSError, tarfile.TarError, ValueError) as exc:
        sys.stderr.write(f"artifact: {name} {version}: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
