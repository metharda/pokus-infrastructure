"""Checks of ``versions.lock.yml`` (BUILD_PROMPT §10.1 ``make lock-check``).

* ``schema_findings``: structure (``tools/lockcheck/schema/versions-lock.schema.json``).
* ``marker_findings``: every remaining ``TODO(verify)`` or ``<verify>`` marker.
* ``collection_findings``: every locked collection has exactly one pinned
  artifact (``ansible_collections.artifacts``), transitive dependencies
  included, and no artifact pins a collection that is not locked.
* ``container_findings``: every image digest resolves in its registry; a tag
  that has moved to another digest is a warning (only the digest is used).
* ``binary_findings``: every binary asset and every collection's artifact
  downloads and has the pinned sha256.
* ``key_findings``: every repository key URL serves a key whose primary
  fingerprint is the pinned one.
* ``gpu_coverage_findings``: every verified open NVIDIA driver pin has its
  release's GPU list (``nvidia_driver_gpus``), without which no driver would
  be installed automatically.
* ``gpu_list_findings``: every pinned GPU list's package downloads with its
  sha256, and the list recomputed from it equals the pinned one; a package
  that has left the archive is a warning (the pin stays valid by its sha256).

Network functions are parameters, so the tests run without a network.
"""

from __future__ import annotations

import datetime as dt
import functools
import json
import os
import platform
import re
import tarfile
import tempfile
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

import jsonschema
import yaml

from pinka_lockcheck import net, nvidia_gpus, openpgp, retry

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
    return load_text(path.read_text(encoding="utf-8"), str(path))


def load_text(text: str, label: str = "versions.lock.yml") -> dict[str, Any]:
    """:func:`load` of the lock's text (from ``git show``, for a release package).

    Raises:
        ValueError: If it is not a mapping.
    """
    data = _jsonable(yaml.safe_load(text))
    if not isinstance(data, dict):
        raise ValueError(f"{label}: not a mapping")
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


def collection_findings(doc: Mapping[str, Any]) -> list[Finding]:
    """Every locked collection has one pinned artifact, and every artifact a locked version.

    Args:
        doc: Lock document.

    Returns:
        One error per collection without an artifact and per artifact without
        a locked collection.
    """
    section = doc.get("ansible_collections") or {}
    locked = set(section.get("collections") or {})
    pinned = set(section.get("artifacts") or {})
    return [
        Finding(
            ERROR, f"ansible_collections.artifacts.{name}", "a locked collection has no artifact"
        )
        for name in sorted(locked - pinned)
    ] + [
        Finding(
            ERROR, f"ansible_collections.artifacts.{name}", "an artifact of a collection not locked"
        )
        for name in sorted(pinned - locked)
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
        for group in ("images", "target_images", "ci_images")
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
    # Galaxy's artifact of every collection, which bootstrap installs.
    for name, asset in doc["ansible_collections"].get("artifacts", {}).items():
        yield f"ansible_collections.artifacts.{name}", asset["url"], asset["sha256"]


def binary_findings(
    doc: Mapping[str, Any],
    hasher: Hasher = net.sha256_of_url,
    policy: retry.Policy = retry.DEFAULT,
) -> list[Finding]:
    """Download every binary asset and compare its sha256.

    A collection's artifact is downloaded again after a transport error, like
    in every path that downloads collections (:mod:`pinka_lockcheck.retry`);
    a sha256 that differs from the pin is never retried.

    Args:
        doc: Lock document.
        hasher: ``url -> sha256``.
        policy: How often to try a collection's artifact.

    Returns:
        One error per unreachable or mismatching asset.
    """
    findings: list[Finding] = []
    for where, url, sha256 in _binary_assets(doc):
        if not _verified(url, sha256):
            continue
        try:
            if where.startswith("ansible_collections."):
                actual = policy.run(functools.partial(hasher, url), f"download {url}")
            else:
                actual = hasher(url)
        except net.FetchError as exc:
            findings.append(Finding(ERROR, where, f"download failed: {exc}"))
            continue
        if actual != sha256:
            findings.append(Finding(ERROR, where, f"sha256 is {actual}, pinned {sha256}"))
    return findings


class FetchRefusedError(RuntimeError):
    """A pinned binary cannot be fetched (unknown, unverified, unsupported or mismatching)."""


def _archive_member(archive: Path, member: str, where: str) -> bytes:
    """The one regular file named ``member`` in a verified tar.gz archive."""
    with tarfile.open(archive, "r:gz") as tar:
        named = [m for m in tar.getmembers() if PurePosixPath(m.name).name == member]
        files = [m for m in named if m.isfile()]
        if len(named) != 1 or len(files) != 1:
            kinds = ", ".join(
                sorted({"file" if m.isfile() else "link or directory" for m in named})
            )
            raise FetchRefusedError(
                f"{where}: the archive has {len(named)} member(s) named {member!r}"
                + (f" ({kinds})" if named else "")
                + ", not exactly one regular file"
            )
        handle = tar.extractfile(files[0])
        if handle is None:  # pragma: no cover - isfile() members always have content
            raise FetchRefusedError(f"{where}: cannot read {member!r}")
        with handle:
            return handle.read()


def fetch_binary(
    doc: Mapping[str, Any],
    name: str,
    dest: Path,
    *,
    member: str | None = None,
    machine: str | None = None,
    downloader: Downloader = net.sha256_of_url,
) -> str:
    """Download a pinned binary, verify its sha256 and install it.

    A ``binary`` entry is the executable itself; from a ``tar.gz`` entry the
    one regular file named ``member`` is installed (uv, for one). The
    download is written next to ``dest`` and the executable is renamed into
    place only after the checksum matched, so ``dest`` never holds unverified
    content.

    Args:
        doc: Lock document.
        name: Entry under ``binaries`` with ``format: binary`` or ``tar.gz``.
        dest: Path of the executable to write.
        member: For a ``tar.gz`` entry: the file name of the executable in the
            archive (required).
        machine: ``platform.machine()`` value; the running machine by default.
        downloader: ``(url, file) -> sha256`` that writes the content to ``file``.

    Returns:
        The verified sha256 (of the download).

    Raises:
        FetchRefusedError: If the entry is unknown, unverified, of another
            format, has no asset for the machine, or the checksum differs; for
            an archive, if no member is named or the archive does not hold
            exactly one regular file of that name.
    """
    entry = doc["binaries"].get(name)
    if entry is None:
        raise FetchRefusedError(f"no binary {name!r} in the lock")
    if entry["format"] not in ("binary", "tar.gz"):
        raise FetchRefusedError(
            f"binaries.{name} is {entry['format']}, not a single binary or a tar.gz archive"
        )
    if entry["format"] == "tar.gz" and not member:
        raise FetchRefusedError(f"binaries.{name} is a tar.gz archive: name its executable")
    arch = MACHINES.get(machine or platform.machine())
    asset = entry["assets"].get(arch or "")
    if asset is None:
        raise FetchRefusedError(f"binaries.{name} has no asset for {machine or platform.machine()}")
    if not _verified(asset["url"], asset["sha256"]):
        raise FetchRefusedError(f"binaries.{name}.assets.{arch} is not verified")
    where = f"binaries.{name}.assets.{arch}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(dir=dest.parent, prefix=f".{dest.name}.")
    temp = Path(temp_name)
    download = temp.with_name(temp.name + ".download")
    try:
        with os.fdopen(handle, "wb") as out:
            if entry["format"] == "binary":
                actual = downloader(asset["url"], out)
            else:
                with download.open("wb") as archive:
                    actual = downloader(asset["url"], archive)
        if actual != asset["sha256"]:
            raise FetchRefusedError(f"{where}: sha256 is {actual}, pinned {asset['sha256']}")
        if entry["format"] == "tar.gz":
            temp.write_bytes(_archive_member(download, str(member), where))
        temp.chmod(0o755)
        temp.replace(dest)
    finally:
        temp.unlink(missing_ok=True)
        download.unlink(missing_ok=True)
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


# The image servers of incus_base_images and the fingerprint field of each image type.
IMAGE_SERVERS = {"images": "https://images.linuxcontainers.org/streams/v1/images.json"}
IMAGE_FINGERPRINT_FIELDS = {
    "container": "combined_squashfs_sha256",
    "virtual-machine": "combined_disk-kvm-img_sha256",
}


def base_image_findings(doc: Mapping[str, Any], fetch: Fetcher = net.download) -> list[Finding]:
    """Check that every pinned Incus base image is still on its image server.

    Public image servers keep only their newest builds, so a pin ages out
    within days. That is a warning, not an error: a lab that exported the base
    keeps importing it from there, and a new lab renews the pin with
    ``make lock-pins`` before its first golden image build.

    Args:
        doc: Lock document.
        fetch: ``url -> content``.

    Returns:
        Errors for an unknown remote or an unreadable index, warnings for
        fingerprints the server no longer has.
    """
    findings: list[Finding] = []
    indexes: dict[str, set[str]] = {}
    for name, entry in doc["incus_base_images"]["images"].items():
        where = f"incus_base_images.images.{name}"
        pinned = {a: f for a, f in entry["fingerprints"].items() if _verified(f)}
        if not pinned:
            continue
        url = IMAGE_SERVERS.get(str(entry["remote"]))
        if url is None:
            findings.append(Finding(ERROR, where, f"unknown remote {entry['remote']!r}"))
            continue
        if url not in indexes:
            try:
                products = json.loads(fetch(url)).get("products", {})
            except (net.FetchError, ValueError) as exc:
                findings.append(Finding(ERROR, where, f"{url}: {exc}"))
                continue
            field = IMAGE_FINGERPRINT_FIELDS[str(entry["type"])]
            indexes[url] = {
                str(item[field])
                for product in products.values()
                for build in (product.get("versions") or {}).values()
                for item in (build.get("items") or {}).values()
                if item.get(field)
            }
        for arch, fingerprint in pinned.items():
            if fingerprint not in indexes[url]:
                findings.append(
                    Finding(
                        WARNING,
                        where,
                        f"{arch} fingerprint {fingerprint} is no longer on "
                        f"{url.split('/streams')[0]}; renew it with make lock-pins before "
                        "a new lab's first golden image build",
                    )
                )
    return findings


def gpu_coverage_findings(doc: Mapping[str, Any]) -> list[Finding]:
    """Every verified NVIDIA driver pin has the GPU list of its release.

    The play installs a driver automatically only when the upstream version of
    one of the release's pinned packages has a GPU list and that list names
    the GPU (``pinka_driver_supports_gpu``); a verified pin without a list
    would never install anything, whatever the package names.

    Args:
        doc: Lock document.

    Returns:
        One error per verified driver pin none of whose versions has a list.
    """
    releases = (doc.get("nvidia_driver_gpus") or {}).get("releases") or {}
    pins = (doc.get("packages") or {}).get("nvidia_driver") or {}
    findings: list[Finding] = []
    for release, packages in pins.items():
        if not isinstance(packages, Mapping) or not _verified(*packages.values()):
            continue
        upstream = sorted({nvidia_gpus.upstream_version(str(v)) for v in packages.values()})
        if not any(version in releases for version in upstream):
            findings.append(
                Finding(
                    ERROR,
                    f"packages.nvidia_driver.{release}",
                    f"no GPU list for {', '.join(upstream)} in nvidia_driver_gpus.releases;"
                    " pin it with make lock-pins",
                )
            )
    return findings


def gpu_list_findings(
    doc: Mapping[str, Any],
    fetch: Fetcher = net.download,
    extract: Callable[[bytes, str], bytes] = nvidia_gpus.extract_member,
) -> list[Finding]:
    """Download every pinned GPU list's package again and recompute the list.

    Args:
        doc: Lock document.
        fetch: ``url -> content``.
        extract: Reads a file out of a Debian package.

    Returns:
        Errors for a changed package or list, warnings for a package that has
        left the archive.
    """
    findings: list[Finding] = []
    for version, entry in doc["nvidia_driver_gpus"]["releases"].items():
        where = f"nvidia_driver_gpus.releases.{version}"
        try:
            problems = nvidia_gpus.verify_entry(str(version), entry, fetch, extract)
        except net.FetchError as exc:
            findings.append(
                Finding(
                    WARNING,
                    where,
                    f"{entry['source']} cannot be read ({exc}); the pin stays valid by its "
                    "sha256, renew it with make lock-pins when the driver pin changes",
                )
            )
            continue
        findings += [Finding(ERROR, where, problem) for problem in problems]
    return findings
