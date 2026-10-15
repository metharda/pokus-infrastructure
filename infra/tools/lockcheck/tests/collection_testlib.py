"""Collection artifacts as ansible-galaxy builds them, for the tests of every path that checks one.

``make lock-collections`` (tools/lockcheck/tests), ``make setup`` and
bootstrap (bootstrap/tests) check the same artifacts with
``pinka_lockcheck.artifact``; their tests build them here.
"""

from __future__ import annotations

import hashlib
import io
import json
import posixpath
import tarfile
from collections.abc import Callable, Mapping, Sequence


def artifact(
    name: str,
    version: str,
    files: Mapping[str, bytes],
    *,
    links: Mapping[str, str] | None = None,
    special: Sequence[tarfile.TarInfo] = (),
    deps: Mapping[str, str] | None = None,
    licenses: tuple[str, ...] = ("GPL-3.0-or-later",),
    tamper: Callable[[dict[str, bytes]], None] | None = None,
) -> bytes:
    """A collection tarball as ``ansible-galaxy collection build`` writes it.

    Args:
        name: ``namespace.name``.
        version: Its version.
        files: Regular files by path.
        links: Symbolic links (path to stored target); FILES.json lists a link
            to a file like that file, as ansible-galaxy does.
        special: Further members as they are (hard links, FIFOs, devices).
        deps: ``collection_info.dependencies``.
        licenses: ``collection_info.license``.
        tamper: Changes the members after FILES.json and MANIFEST.json.

    Returns:
        The gzip-compressed tarball.
    """
    namespace, collection = name.split(".")
    listed = dict(files)
    for path in links or {}:
        target = path
        for _ in range(len(links or {})):  # follow a chain of links to its end
            if target not in (links or {}):
                break
            target = posixpath.normpath(
                posixpath.join(posixpath.dirname(target), (links or {})[target])
            )
        if target in files:
            listed[path] = files[target]
    listing = {
        "files": [{"name": ".", "ftype": "dir", "chksum_type": None, "chksum_sha256": None}]
        + [
            {
                "name": path,
                "ftype": "file",
                "chksum_type": "sha256",
                "chksum_sha256": hashlib.sha256(data).hexdigest(),
            }
            for path, data in sorted(listed.items())
        ],
        "format": 1,
    }
    files_json = json.dumps(listing).encode()
    manifest = {
        "collection_info": {
            "namespace": namespace,
            "name": collection,
            "version": version,
            "license": list(licenses),
            "dependencies": dict(deps or {}),
        },
        "file_manifest_file": {
            "name": "FILES.json",
            "ftype": "file",
            "chksum_type": "sha256",
            "chksum_sha256": hashlib.sha256(files_json).hexdigest(),
            "format": 1,
        },
        "format": 1,
    }
    members = {"MANIFEST.json": json.dumps(manifest).encode(), "FILES.json": files_json, **files}
    if tamper:
        tamper(members)
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        for path, data in members.items():
            info = tarfile.TarInfo(path)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        for path, linkname in (links or {}).items():
            info = tarfile.TarInfo(path)
            info.type = tarfile.SYMTYPE
            info.linkname = linkname
            tar.addfile(info)
        for info in special:
            tar.addfile(info)
    return out.getvalue()


def member(path: str, kind: bytes, linkname: str = "") -> tarfile.TarInfo:
    """A member of another type (``tarfile.LNKTYPE``, ``FIFOTYPE``, ``CHRTYPE`` ...)."""
    info = tarfile.TarInfo(path)
    info.type = kind
    info.linkname = linkname
    return info
