"""Checks of ``versions.lock.yml`` (BUILD_PROMPT §10.1 ``make lock-check``).

* ``schema_findings``: structure (``tools/lockcheck/schema/versions-lock.schema.json``).
* ``marker_findings``: every remaining ``TODO(verify)`` or ``<verify>`` marker.
* ``container_findings``: every image digest resolves in its registry; a tag
  that has moved to another digest is a warning (only the digest is used).
* ``binary_findings``: every binary asset downloads and has the pinned sha256.
* ``key_findings``: every repository key URL serves a key whose primary
  fingerprint is the pinned one.

Network functions are parameters, so the tests run without a network.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import platform
import re
import tempfile
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

import jsonschema
import yaml

from pokus_lockcheck import net, openpgp

MARKER = "TODO(verify)"
MARKER_RE = re.compile(r"TODO\(verify\)|<verify>")
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schema" / "versions-lock.schema.json"
ERROR = "error"
WARNING = "warning"

Resolver = Callable[[str, str], str | None]
Hasher = Callable[[str], str]
Fetcher = Callable[[str], bytes]
Downloader = Callable[[str, BinaryIO], str]
# platform.machine() names of the lock's architectures.
MACHINES = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}


@dataclass(frozen=True)
class Finding:
    """One problem found in the lock file.

    Attributes:
        severity: ``error`` or ``warning``.
        where: Dotted path of the value, or ``line N`` for markers.
        message: What is wrong.
    """

    severity: str
    where: str
    message: str

    def __str__(self) -> str:
        """Render as ``severity: where: message``."""
        return f"{self.severity}: {self.where}: {self.message}"


def _jsonable(value: Any) -> Any:  # noqa: ANN401 - YAML value
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


def load(path: Path) -> dict[str, Any]:
    """Read the lock file with dates as ISO strings.

    Args:
        path: Path of ``versions.lock.yml``.

    Returns:
        The document.

    Raises:
        ValueError: If it is not a mapping.
    """
    data = _jsonable(yaml.safe_load(path.read_text(encoding="utf-8")))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: not a mapping")
    return data


def schema_findings(doc: Mapping[str, Any], schema_path: Path = SCHEMA_PATH) -> list[Finding]:
    """Validate the document against the JSON Schema.

    Args:
        doc: Lock document.
        schema_path: Schema file.

    Returns:
        One error per violation.
    """
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    return [
        Finding(ERROR, ".".join(str(p) for p in e.absolute_path) or "(root)", e.message)
        for e in sorted(validator.iter_errors(doc), key=lambda e: list(map(str, e.absolute_path)))
    ]


def marker_findings(text: str) -> list[Finding]:
    """Report every unverified marker.

    Args:
        text: Lock file text.

    Returns:
        One error per line with a marker.
    """
    return [
        Finding(ERROR, f"line {n}", f"unverified value: {line.strip()}")
        for n, line in enumerate(text.splitlines(), start=1)
        if MARKER_RE.search(line)
    ]


def _verified(*values: Any) -> bool:  # noqa: ANN401 - YAML values
    return all(isinstance(v, str) and not MARKER_RE.search(v) for v in values)


def container_findings(
    doc: Mapping[str, Any], resolve: Resolver = net.manifest_digest
) -> list[Finding]:
    """Check that every pinned image digest exists.

    Args:
        doc: Lock document.
        resolve: ``(image, tag_or_digest) -> digest or None``.

    Returns:
        Errors for missing digests, warnings for moved tags.
    """
    findings: list[Finding] = []
    pinned = [
        (f"containers.{group}.{name}", entry)
        for group in ("images", "target_images")
        for name, entry in doc["containers"].get(group, {}).items()
    ]
    for where, entry in pinned:
        image, tag, digest = entry["image"], entry["tag"], entry["digest"]
        if not _verified(tag, digest):
            continue
        try:
            found = resolve(image, digest)
            if found is None:
                findings.append(Finding(ERROR, where, f"{image}@{digest} does not exist"))
                continue
            if found != digest:
                findings.append(Finding(ERROR, where, f"{image}@{digest} resolves to {found}"))
                continue
            current = resolve(image, tag)
        except net.FetchError as exc:
            findings.append(Finding(ERROR, where, f"registry request failed: {exc}"))
            continue
        if current != digest:
            findings.append(
                Finding(
                    WARNING, where, f"tag {tag} now points to {current}; the pin stays {digest}"
                )
            )
    return findings


def _binary_assets(doc: Mapping[str, Any]) -> Iterator[tuple[str, str, str]]:
    for name, entry in doc["binaries"].items():
        for arch, asset in entry["assets"].items():
            yield f"binaries.{name}.assets.{arch}", asset["url"], asset["sha256"]


def binary_findings(doc: Mapping[str, Any], hasher: Hasher = net.sha256_of_url) -> list[Finding]:
    """Download every binary asset and compare its sha256.

    Args:
        doc: Lock document.
        hasher: ``url -> sha256``.

    Returns:
        One error per unreachable or mismatching asset.
    """
    findings: list[Finding] = []
    for where, url, sha256 in _binary_assets(doc):
        if not _verified(url, sha256):
            continue
        try:
            actual = hasher(url)
        except net.FetchError as exc:
            findings.append(Finding(ERROR, where, f"download failed: {exc}"))
            continue
        if actual != sha256:
            findings.append(Finding(ERROR, where, f"sha256 is {actual}, pinned {sha256}"))
    return findings


class FetchRefusedError(RuntimeError):
    """A pinned binary cannot be fetched (unknown, unverified, unsupported or mismatching)."""


def fetch_binary(
    doc: Mapping[str, Any],
    name: str,
    dest: Path,
    *,
    machine: str | None = None,
    downloader: Downloader = net.sha256_of_url,
) -> str:
    """Download a pinned single-file binary, verify its sha256 and install it.

    The file is written next to ``dest`` and renamed into place only after the
    checksum matched, so ``dest`` never holds unverified content.

    Args:
        doc: Lock document.
        name: Entry under ``binaries`` with ``format: binary``.
        dest: Path of the executable to write.
        machine: ``platform.machine()`` value; the running machine by default.
        downloader: ``(url, file) -> sha256`` that writes the content to ``file``.

    Returns:
        The verified sha256.

    Raises:
        FetchRefusedError: If the entry is unknown, unverified, not a single
            binary, has no asset for the machine, or the checksum differs.
    """
    entry = doc["binaries"].get(name)
    if entry is None:
        raise FetchRefusedError(f"no binary {name!r} in the lock")
    if entry["format"] != "binary":
        raise FetchRefusedError(f"binaries.{name} is {entry['format']}, not a single binary")
    arch = MACHINES.get(machine or platform.machine())
    asset = entry["assets"].get(arch or "")
    if asset is None:
        raise FetchRefusedError(f"binaries.{name} has no asset for {machine or platform.machine()}")
    if not _verified(asset["url"], asset["sha256"]):
        raise FetchRefusedError(f"binaries.{name}.assets.{arch} is not verified")
    dest.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(dir=dest.parent, prefix=f".{dest.name}.")
    temp = Path(temp_name)
    try:
        with os.fdopen(handle, "wb") as out:
            actual = downloader(asset["url"], out)
        if actual != asset["sha256"]:
            raise FetchRefusedError(
                f"binaries.{name}.assets.{arch}: sha256 is {actual}, pinned {asset['sha256']}"
            )
        temp.chmod(0o755)
        temp.replace(dest)
    finally:
        temp.unlink(missing_ok=True)
    return actual


def key_findings(doc: Mapping[str, Any], fetch: Fetcher = net.download) -> list[Finding]:
    """Download every repository key and compare its primary fingerprint.

    Args:
        doc: Lock document.
        fetch: ``url -> content``.

    Returns:
        One error per unreachable, unparsable or mismatching key.
    """
    findings: list[Finding] = []
    for name, entry in doc["repository_keys"]["keys"].items():
        where = f"repository_keys.keys.{name}"
        wanted = entry["fingerprint"]
        for url in entry["urls"]:
            if not _verified(url, wanted):
                continue
            try:
                found = openpgp.primary_fingerprints(fetch(url))
            except net.FetchError as exc:
                findings.append(Finding(ERROR, where, f"download failed: {exc}"))
                continue
            except openpgp.OpenPGPError as exc:
                findings.append(Finding(ERROR, where, f"{url}: {exc}"))
                continue
            if found != [wanted]:
                findings.append(
                    Finding(
                        ERROR,
                        where,
                        f"{url} has primary key(s) {', '.join(found)}, pinned {wanted}",
                    )
                )
    return findings
