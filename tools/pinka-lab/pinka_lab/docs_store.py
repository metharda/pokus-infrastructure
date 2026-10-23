"""Installed documentation releases (BUILD_PROMPT §7.19).

A release is a docs site tarball (``docs/DOCS_SPEC.md`` §B.4) with its
sha256. Each installed version is a directory ``<base>/<version>/``; the
symbolic link ``<base>/current`` names the one the docs service serves and
switches in one step (a new link renamed over the old one). ``versions.json``
records the versions in the order they were installed, with their checksums;
the three most recent are kept. Only root installs releases.

Extraction refuses absolute paths, ``..``, links that leave the version
directory and anything that is not a file, a directory or a link.
"""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import shutil
import tarfile
import tempfile
from typing import Any

CURRENT = "current"
INDEX = "versions.json"
KEEP = 3
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SUFFIXES = (".tar.gz", ".tgz", ".tar.xz", ".tar.bz2", ".tar")
DIR_MODE = 0o755
FILE_MODE = 0o644


class DocsError(Exception):
    """A documentation release cannot be installed or switched."""


def version_from(tarball: str) -> str:
    """The version name of a tarball: its file name without the archive suffix.

    Args:
        tarball: Path or URL of the tarball.

    Returns:
        For example ``pinka-docs-v0.3.0`` for ``.../pinka-docs-v0.3.0.tar.gz``.
    """
    name = posixpath.basename(tarball)
    for suffix in SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def sha256_of(path: str) -> str:
    """The hex SHA-256 of a file.

    Args:
        path: The file.

    Returns:
        Lowercase hex digest.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _inside(path: str) -> bool:
    """Whether a relative archive path stays inside the extraction directory."""
    if not path or path.startswith("/"):
        return False
    normalized = posixpath.normpath(path)
    return normalized != ".." and not normalized.startswith("../")


def check_members(members: list[tarfile.TarInfo]) -> None:
    """Refuse archive members that could leave the version directory.

    Args:
        members: The archive's members.

    Raises:
        DocsError: For an absolute path, ``..``, a link leaving the directory
            or a member that is not a file, a directory or a link.
    """
    for member in members:
        name = member.name
        if not _inside(name) or ".." in name.split("/"):
            raise DocsError(f"refused: {name!r} leaves the version directory")
        if member.issym():
            target = posixpath.join(posixpath.dirname(name), member.linkname)
            if member.linkname.startswith("/") or not _inside(target):
                raise DocsError(f"refused: link {name!r} -> {member.linkname!r} leaves it")
        elif member.islnk():
            if not _inside(member.linkname) or ".." in member.linkname.split("/"):
                raise DocsError(f"refused: hard link {name!r} -> {member.linkname!r} leaves it")
        elif not (member.isfile() or member.isdir()):
            raise DocsError(f"refused: {name!r} is not a file, directory or link")


def _load(base: str) -> list[dict[str, Any]]:
    try:
        with open(os.path.join(base, INDEX), encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return []
    except ValueError as exc:
        raise DocsError(f"{os.path.join(base, INDEX)} is not JSON: {exc}") from exc
    return [v for v in data.get("versions", []) if isinstance(v, dict)]


def _save(base: str, versions: list[dict[str, Any]]) -> None:
    path = os.path.join(base, INDEX)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump({"versions": versions}, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.chmod(tmp, FILE_MODE)
    os.replace(tmp, path)


def current(base: str) -> str | None:
    """The version ``current`` names.

    Args:
        base: The docs directory.

    Returns:
        The version, or None if none is installed.
    """
    try:
        return os.path.basename(os.readlink(os.path.join(base, CURRENT)))
    except OSError:
        return None


def _switch(base: str, version: str) -> None:
    link = os.path.join(base, CURRENT)
    tmp = os.path.join(base, f".{CURRENT}.tmp")
    if os.path.lexists(tmp):
        os.unlink(tmp)
    os.symlink(version, tmp)
    os.replace(tmp, link)


def _normalize_modes(root: str) -> None:
    os.chmod(root, DIR_MODE)
    for directory, dirs, files in os.walk(root):
        for name in dirs:
            path = os.path.join(directory, name)
            if not os.path.islink(path):
                os.chmod(path, DIR_MODE)
        for name in files:
            path = os.path.join(directory, name)
            if not os.path.islink(path):
                os.chmod(path, FILE_MODE)


def _extract(tarball: str, into: str) -> str:
    with tarfile.open(tarball) as archive:
        members = archive.getmembers()
        check_members(members)
        if hasattr(tarfile, "data_filter"):
            archive.extractall(into, members, filter="data")
        else:  # pragma: no cover - CPython before the extraction filters
            archive.extractall(into, members)  # noqa: S202 - every member checked above
    site = into
    entries = [e for e in os.listdir(into) if not e.startswith(".")]
    if not os.path.isfile(os.path.join(into, "index.html")) and len(entries) == 1:
        site = os.path.join(into, entries[0])
    if not os.path.isfile(os.path.join(site, "index.html")):
        raise DocsError("refused: the tarball holds no site (index.html is missing)")
    _normalize_modes(site)
    return site


def install(base: str, tarball: str, sha256: str, version: str | None, now: str) -> str:
    """Install a release, switch ``current`` to it and keep the three most recent.

    Args:
        base: The docs directory.
        tarball: The release tarball.
        sha256: Its expected hex SHA-256.
        version: The version name (default: from the tarball's file name).
        now: The install time (UTC, ISO 8601 with ``Z``).

    Returns:
        What happened.

    Raises:
        DocsError: On a checksum mismatch, a bad name or a refused archive.
    """
    sha256 = sha256.lower()
    if not SHA256_RE.match(sha256):
        raise DocsError(f"--sha256 must be 64 hex digits, not {sha256!r}")
    name = version or version_from(tarball)
    if not VERSION_RE.match(name) or name == CURRENT:
        raise DocsError(f"invalid version name {name!r}; pass --version")
    actual = sha256_of(tarball)
    if actual != sha256:
        raise DocsError(f"checksum mismatch: {tarball} is {actual}, expected {sha256}")
    os.makedirs(base, mode=DIR_MODE, exist_ok=True)
    versions = _load(base)
    known = next((v for v in versions if v.get("version") == name), None)
    if known is not None and os.path.isdir(os.path.join(base, name)):
        if known.get("sha256") != sha256:
            raise DocsError(f"version {name} is installed with another checksum")
        if current(base) == name:
            return f"{name} is already current"
        _switch(base, name)
        return f"{name} was installed; switched to it"
    work = tempfile.mkdtemp(prefix=".install-", dir=base)
    try:
        site = _extract(tarball, work)
        target = os.path.join(base, name)
        if os.path.lexists(target):
            shutil.rmtree(target)
        os.rename(site, target)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    versions = [v for v in versions if v.get("version") != name]
    versions.append({"version": name, "sha256": sha256, "installed_at": now})
    _switch(base, name)
    while len(versions) > KEEP:
        old = versions.pop(0)
        shutil.rmtree(os.path.join(base, str(old["version"])), ignore_errors=True)
    _save(base, versions)
    return f"installed {name}"


def rollback(base: str) -> str:
    """Switch ``current`` back to the version installed before it.

    Args:
        base: The docs directory.

    Returns:
        What happened.

    Raises:
        DocsError: Without a current or an earlier version.
    """
    now = current(base)
    names = [str(v.get("version")) for v in _load(base)]
    if now is None or now not in names:
        raise DocsError("no documentation release is current")
    position = names.index(now)
    if position == 0:
        raise DocsError(f"{now} is the oldest installed version; nothing to roll back to")
    previous = names[position - 1]
    if not os.path.isdir(os.path.join(base, previous)):
        raise DocsError(f"{previous} is missing from {base}")
    _switch(base, previous)
    return f"switched from {now} back to {previous}"
