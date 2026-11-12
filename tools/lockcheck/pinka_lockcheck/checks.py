"""Checks of ``versions.lock.yml`` (``make lock-check``).

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
* ``package_findings``: every package version of an apt release is still in
  the index apt installs it from (:mod:`pinka_lockcheck.aptindex`); one that
  has left it is an error, since apt cannot install it.

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

from pinka_lockcheck import aptindex, net, nvidia_gpus, openpgp, retry

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


def end_anchored(pattern: str) -> str:
    r"""A schema pattern for Python's ``re`` with JSON Schema's ``$``.

    JSON Schema reads a pattern as ECMA 262 does, where ``$`` (without the
    multiline flag) matches only at the end of the value; Python's ``$`` also
    matches before a final newline, so ``^[a-z]+$`` would accept ``"ab\n"``.
    Each ``$`` outside a character class becomes ``\Z``.

    Args:
        pattern: The schema's pattern.

    Returns:
        The pattern for ``re.search``.
    """
    out: list[str] = []
    i = 0
    in_class = False
    while i < len(pattern):
        char = pattern[i]
        if char == "\\" and i + 1 < len(pattern):
            out.append(pattern[i : i + 2])
            i += 2
            continue
        if in_class:
            in_class = char != "]"
        elif char == "[":
            in_class = True
            out.append(char)
            i += 1
            # A "]" first in the class (after an optional "^") is a literal.
            if pattern[i : i + 1] == "^":
                out.append("^")
                i += 1
            if pattern[i : i + 1] == "]":
                out.append("]")
                i += 1
            continue
        elif char == "$":
            out.append(r"\Z")
            i += 1
            continue
        out.append(char)
        i += 1
    return "".join(out)


def _pattern(
    validator: Any,  # noqa: ANN401 - jsonschema's validator; its protocol has no descend
    pattern: str,
    instance: object,
    schema: Mapping[str, Any],
) -> Iterator[jsonschema.ValidationError]:
    if isinstance(instance, str) and not re.search(end_anchored(pattern), instance):
        yield jsonschema.ValidationError(f"{instance!r} does not match {pattern!r}")


def _pattern_properties(
    validator: Any,  # noqa: ANN401 - jsonschema's validator; its protocol has no descend
    patterns: Mapping[str, Any],
    instance: object,
    schema: Mapping[str, Any],
) -> Iterator[jsonschema.ValidationError]:
    if not isinstance(instance, Mapping):
        return
    for pattern, subschema in patterns.items():
        for key, value in instance.items():
            if re.search(end_anchored(pattern), key):
                yield from validator.descend(value, subschema, path=key, schema_path=pattern)


def _additional_properties(
    validator: Any,  # noqa: ANN401 - jsonschema's validator; its protocol has no descend
    additional: object,
    instance: object,
    schema: Mapping[str, Any],
) -> Iterator[jsonschema.ValidationError]:
    if not isinstance(instance, Mapping):
        return
    properties = schema.get("properties", {})
    patterns = [end_anchored(p) for p in schema.get("patternProperties", {})]
    extras = {
        key
        for key in instance
        if key not in properties and not any(re.search(p, key) for p in patterns)
    }
    if isinstance(additional, Mapping):
        for extra in extras:
            yield from validator.descend(instance[extra], additional, path=extra)
    elif not additional and extras:
        # jsonschema's own messages, with the patterns as the schema writes them.
        if "patternProperties" in schema:
            verb = "does" if len(extras) == 1 else "do"
            joined = ", ".join(repr(each) for each in sorted(extras))
            regexes = ", ".join(repr(each) for each in sorted(schema["patternProperties"]))
            yield jsonschema.ValidationError(
                f"{joined} {verb} not match any of the regexes: {regexes}"
            )
        else:
            listed = sorted(extras, key=str)
            verb = "was" if len(listed) == 1 else "were"
            names = ", ".join(repr(each) for each in listed)
            yield jsonschema.ValidationError(
                f"Additional properties are not allowed ({names} {verb} unexpected)"
            )


# Draft 2020-12 with JSON Schema's "$" in pattern, patternProperties and
# additionalProperties (end_anchored).
Validator = jsonschema.validators.extend(  # type: ignore[no-untyped-call]
    jsonschema.Draft202012Validator,
    {
        "pattern": _pattern,
        "patternProperties": _pattern_properties,
        "additionalProperties": _additional_properties,
    },
)


def schema_findings(doc: Mapping[str, Any], schema_path: Path = SCHEMA_PATH) -> list[Finding]:
    """Validate the document against the JSON Schema.

    Args:
        doc: Lock document.
        schema_path: Schema file.

    Returns:
        One error per violation.
    """
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = Validator(schema)
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


def size_findings(
    doc: Mapping[str, Any], sizes: Callable[[str, str], dict[str, int]] = net.image_sizes
) -> list[Finding]:
    """Check every lab image's recorded compressed sizes against its manifests.

    Args:
        doc: Lock document.
        sizes: ``(image, digest) -> {platform: bytes}``.

    Returns:
        Errors for a size that differs from the registry or a platform without one.
    """
    findings: list[Finding] = []
    for name, entry in doc["containers"]["images"].items():
        where = f"containers.images.{name}.sizes"
        if not _verified(entry["digest"]):
            continue
        try:
            found = sizes(entry["image"], entry["digest"])
        except net.FetchError as exc:
            findings.append(Finding(ERROR, where, f"registry request failed: {exc}"))
            continue
        recorded = dict(entry.get("sizes") or {})
        for target in entry["platforms"]:
            if target not in found:
                findings.append(Finding(ERROR, where, f"the index has no {target} image"))
            elif recorded.get(target) != found[target]:
                findings.append(
                    Finding(
                        ERROR,
                        where,
                        f"{target}: recorded {recorded.get(target)}, "
                        f"the manifest says {found[target]}",
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


# --- package pins in their indexes ---------------------------------------------------------------

# The lock's package versions are the x86_64 builds (it pins one version per
# release), so their indexes are read for amd64.
APT_ARCH = "amd64"


@dataclass(frozen=True)
class DistroArchive:
    """A distribution's own archive, for a component that comes from the distribution.

    Attributes:
        uri: The archive's URI.
        suites: The suites searched, in order (``{codename}`` is replaced).
        components: The components searched in each suite, in order.
        keyring: The archive's keyring on the machine (binary).
    """

    uri: str
    suites: tuple[str, ...]
    components: tuple[str, ...]
    keyring: str


DISTRO_ARCHIVES = {
    "ubuntu": DistroArchive(
        "https://archive.ubuntu.com/ubuntu",
        ("{codename}-updates", "{codename}-security", "{codename}"),
        ("main", "restricted", "universe", "multiverse"),
        "/usr/share/keyrings/ubuntu-archive-keyring.gpg",
    ),
}


def apt_codenames(platform_facts_vars: Mapping[str, Any]) -> dict[str, str]:
    """The lock's apt release keys and their codenames (``ubuntu-24.04`` -> ``noble``).

    Args:
        platform_facts_vars: ``ansible/roles/platform_facts/vars/debian.yml``.

    Returns:
        Release key -> codename.
    """
    table = platform_facts_vars["platform_facts_family"]["codenames"]
    return {f"{v['distro']}-{v['version']}": str(name) for name, v in table.items()}


def _read_bytes(path: str) -> bytes:
    return Path(path).read_bytes()


@dataclass
class _Indexes:
    """Reads release files, keys and indexes once each, every download tried again."""

    doc: Mapping[str, Any]
    fetch: Fetcher
    verify: Callable[[bytes, bytes], str]
    read_file: Callable[[str], bytes]
    policy: retry.Policy
    keyrings: dict[str, bytes]
    releases: dict[tuple[str, bytes], str]
    indexes: dict[tuple[str, str, bytes], aptindex.Index]

    def download(self, url: str) -> bytes:
        return self.policy.run(functools.partial(self.fetch, url), f"download {url}")

    def vendor_keyring(self, name: str) -> bytes:
        """The pinned vendor key, downloaded and its fingerprint checked."""
        if name not in self.keyrings:
            entry = self.doc["repository_keys"]["keys"][name]
            url = entry["urls"][0]
            data = self.download(url)
            found = openpgp.primary_fingerprints(data)
            if found != [entry["fingerprint"]]:
                raise aptindex.AptIndexError(
                    f"{url} has primary key(s) {', '.join(found)}, pinned {entry['fingerprint']}"
                )
            self.keyrings[name] = openpgp.key_bytes(data)
        return self.keyrings[name]

    def file_keyring(self, path: str) -> bytes:
        """A keyring of the machine (a distribution's archive key)."""
        if path not in self.keyrings:
            try:
                self.keyrings[path] = self.read_file(path)
            except OSError as exc:
                raise aptindex.AptIndexError(
                    f"the archive keyring {path} cannot be read ({exc}); install it"
                    " (Ubuntu's package ubuntu-keyring)"
                ) from exc
        return self.keyrings[path]

    def index(self, keyring: bytes, repository: aptindex.Repository) -> aptindex.Index:
        """One package index, its release file's signature and its hash checked.

        Both are kept per keyring too, so a release file is never trusted on
        another key's check.
        """
        release = (repository.base, keyring)
        if release not in self.releases:
            self.releases[release] = aptindex.read_release(
                self.download, self.verify, keyring, repository.base
            )
        key = (repository.base, repository.prefix, keyring)
        if key not in self.indexes:
            self.indexes[key] = aptindex.read_index(
                self.download, self.releases[release], repository
            )
        return self.indexes[key]


def _candidates(
    reader: _Indexes, sources: Mapping[str, Any], component: str, release: str, codename: str
) -> Iterator[aptindex.Index]:
    """The indexes a release's component is installed from, read one by one."""
    group, _, version = release.partition("-")
    entries = sources["components"].get(component) or {}
    entry = entries.get(group) or entries.get("default") or {}
    if entry.get("source") == "vendor_repo":
        repo = sources["repositories"][entry["repo"]]
        if repo.get("type") != "apt":
            raise aptindex.AptIndexError(f"repository {entry['repo']} is not an apt repository")
        values = {
            "distro": group,
            "codename": codename,
            "major": version.split(".")[0],
            "deb_arch": APT_ARCH,
        }
        keyring = reader.vendor_keyring(str(repo["key"]))
        for part in repo.get("components") or [""]:
            repository = aptindex.Repository.of(
                str(repo["uri"]).format(**values),
                str(repo["suite"]).format(**values),
                str(part),
                APT_ARCH,
            )
            yield reader.index(keyring, repository)
        return
    archive = DISTRO_ARCHIVES.get(group)
    if entry.get("source") == "distro" and archive is not None:
        keyring = reader.file_keyring(archive.keyring)
        for suite in archive.suites:
            for part in archive.components:
                yield reader.index(
                    keyring,
                    aptindex.Repository.of(
                        archive.uri, suite.format(codename=codename), part, APT_ARCH
                    ),
                )
        return
    raise aptindex.AptIndexError(
        f"no package index is known for source {entry.get('source')!r} on {release}"
        " (ansible/component-sources.yml)"
    )


def package_findings(
    doc: Mapping[str, Any],
    sources: Mapping[str, Any],
    codenames: Mapping[str, str],
    *,
    fetch: Fetcher = net.download,
    verify: Callable[[bytes, bytes], str] = aptindex.verify_signature,
    read_file: Callable[[str], bytes] = _read_bytes,
    policy: retry.Policy = retry.DEFAULT,
) -> list[Finding]:
    """Check that every verified apt package pin is still in its signed index.

    apt installs only versions an index lists, and some repositories list only
    their newest builds (Zabbly) or the newest version of a pocket (Ubuntu's
    archive), so a pin can stop being installable without changing. For every
    apt release of the lock (a codename of the Debian family's
    ``platform_facts``), each verified package version is looked up in the
    index ``ansible/component-sources.yml`` installs it from: a vendor
    repository's, read through its release file signed with the pinned key, or
    the distribution's own archive (every suite and component, through the
    archive keyring of the machine). A download is tried again after a
    transport error.

    Args:
        doc: Lock document.
        sources: ``ansible/component-sources.yml``.
        codenames: Release key -> codename (:func:`apt_codenames`).
        fetch: ``url -> content``.
        verify: ``(keyring, signed) -> signed text``, raising on a bad signature.
        read_file: ``path -> content`` of a keyring on the machine.
        policy: How often to try a download.

    Returns:
        One error per package version no index lists, naming the pin and the
        indexes read, and one per pin whose indexes cannot be read or trusted.
    """
    reader = _Indexes(doc, fetch, verify, read_file, policy, {}, {}, {})
    findings: list[Finding] = []
    for component, releases in doc["packages"].items():
        if not isinstance(releases, Mapping):
            continue
        for release, pin in releases.items():
            codename = codenames.get(str(release))
            if codename is None or not isinstance(pin, Mapping):
                continue
            wanted = {str(p): str(v) for p, v in pin.items() if _verified(v)}
            if not wanted:
                continue
            where = f"packages.{component}.{release}"
            read: list[str] = []
            try:
                for index in _candidates(reader, sources, str(component), str(release), codename):
                    read.append(index.url)
                    wanted = {
                        p: v for p, v in wanted.items() if v not in index.versions.get(p, set())
                    }
                    if not wanted:
                        break
            except (aptindex.AptIndexError, net.FetchError, openpgp.OpenPGPError) as exc:
                findings.append(Finding(ERROR, where, f"cannot check the pin: {exc}"))
                continue
            except (KeyError, IndexError, ValueError, TypeError, AttributeError) as exc:
                # The lock or component-sources.yml lacks what the lookup needs
                # (a key, a repository, a placeholder's value) or has another shape.
                findings.append(
                    Finding(ERROR, where, f"cannot check the pin: {type(exc).__name__}: {exc}")
                )
                continue
            absent = (
                f"is not in {read[0]}" if len(read) == 1 else f"is in none of {', '.join(read)}"
            )
            for package, version in wanted.items():
                findings.append(
                    Finding(
                        ERROR,
                        where,
                        f"{package} {version} {absent}, so apt cannot install it;"
                        " renew the pin (docs/how-to/update-versions.md)",
                    )
                )
    return findings
