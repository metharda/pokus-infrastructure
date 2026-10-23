"""Pin the vendor sources the lock cannot verify from its own data, checked and printed.

Maintainer tooling (``make lock-pins``), run only by a maintainer when pinning
or upgrading these sources; users and bootstrap never run it. It reads public
URLs and prints; it installs and changes nothing, and its output is reviewed
before it is committed. It covers:

* the repository keys of the Zabbly (Incus LTS) and Tailscale repositories:
  each key file is downloaded and its primary fingerprint computed with
  :mod:`pinka_lockcheck.openpgp`; every listed URL must carry the same single
  key. Each fingerprint is printed with where it was taken from, and
  cross-checked against a second source where one exists: the fingerprint the
  vendor publishes elsewhere (Zabbly's README on GitHub) and the key that
  keyserver.ubuntu.com returns for that fingerprint;
* the package versions of Incus (Zabbly) and Tailscale for every apt release
  of the lock, with the repository layout of
  ``ansible/component-sources.yml``: the release file (``InRelease``) must
  carry a valid signature of that key (checked with ``gpgv``), name the
  components the layout uses and list the package index whose sha256 it
  states; the newest version of every package the component installs is
  read from that index, for x86_64 and aarch64;
* the DCGM exporter image: the newest release tag on nvcr.io, cross-checked
  with the release's own ``hack/versions.env`` on GitHub, and its index
  digest and platforms;
* the Incus base images of the golden image pipeline: the newest build of
  each pinned alias on images.linuxcontainers.org, whose fingerprint (the
  sha256 of the metadata and root file system together) is computed from the
  downloads and must equal the index;
* GitHub's SSH host keys: from GitHub's meta API, GitHub's documentation and
  the server itself (``ssh-keyscan``); every reachable source must agree, at
  least two must be reachable, and each fingerprint is computed from its key.
* the GPU list of every NVIDIA driver release the lock pins for Ubuntu
  (``nvidia_driver_gpus``): NVIDIA's ``supported-gpus.json`` from Ubuntu's
  ``nvidia-kernel-common-<branch>`` package of that version, read through the
  signed ``-updates`` index (``InRelease`` checked with ``gpgv`` and the Ubuntu
  archive keyring of the machine, ``/usr/share/keyrings``), the index's and the
  package's sha256 checked; its open-module device IDs must equal NVIDIA's
  open-gpu-kernel-modules README at the release tag and the ``Modaliases`` of
  ``nvidia-driver-<branch>-open``, and its current IDs those of
  ``nvidia-driver-<branch>`` (:mod:`pinka_lockcheck.nvidia_gpus`).

A section that cannot be pinned prints why and no pins, and the run exits 1.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import gzip
import hashlib
import json
import lzma
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from pinka_lockcheck import checks, net, nvidia_gpus, openpgp

# The components and repository keys this tool pins (plan item 0 after 3f).
COMPONENTS = ("incus", "tailscale")
# Every URL of a key must carry the same single key; the first is the one the
# pkg role downloads (Tailscale publishes the same key for every distribution).
KEY_URLS: dict[str, tuple[str, ...]] = {
    "zabbly": ("https://pkgs.zabbly.com/key.asc",),
    "tailscale": (
        "https://pkgs.tailscale.com/stable/ubuntu/noble.asc",
        "https://pkgs.tailscale.com/stable/ubuntu/jammy.asc",
        "https://pkgs.tailscale.com/stable/debian/trixie.asc",
        "https://pkgs.tailscale.com/stable/fedora/repo.gpg",
    ),
}
# Where a vendor publishes its key's fingerprint apart from the key file.
FINGERPRINT_PAGES: dict[str, tuple[str, ...]] = {
    "zabbly": ("https://raw.githubusercontent.com/zabbly/incus/daily/README.md",),
}
KEYSERVER = "https://keyserver.ubuntu.com/pks/lookup?op=get&options=mr&search=0x{fingerprint}"
# The lock's architectures and their apt names.
APT_ARCHES = {"x86_64": "amd64", "aarch64": "arm64"}
DCGM_IMAGE = "nvcr.io/nvidia/k8s/dcgm-exporter"
DCGM_VERSIONS = "https://raw.githubusercontent.com/NVIDIA/dcgm-exporter/{tag}/hack/versions.env"
# Release tags of the exporter since 4.8.4 are its own version only.
DCGM_TAG_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
IMAGE_REMOTES = {"images": "https://images.linuxcontainers.org"}
SIMPLESTREAMS = "/streams/v1/images.json"
# The fingerprint field and the root file system of each image type.
IMAGE_TYPES = {
    "container": ("combined_squashfs_sha256", "squashfs"),
    "virtual-machine": ("combined_disk-kvm-img_sha256", "disk-kvm.img"),
}
GITHUB_META = "https://api.github.com/meta"
GITHUB_DOCS = "https://raw.githubusercontent.com/github/docs/main/data/reusables/ssh/{name}.md"
SSH_KEY_TYPES = ("ssh-ed25519", "ecdsa-sha2-nistp256", "ssh-rsa")
META_FINGERPRINTS = {
    "ssh-ed25519": "SHA256_ED25519",
    "ecdsa-sha2-nistp256": "SHA256_ECDSA",
    "ssh-rsa": "SHA256_RSA",
}
DOCS_FINGERPRINTS = {"Ed25519": "ssh-ed25519", "ECDSA": "ecdsa-sha2-nistp256", "RSA": "ssh-rsa"}
MIN_SSH_SOURCES = 2
# A fingerprint as vendors print it: 40 hex digits, in groups of four or not.
FINGERPRINT_RE = re.compile(r"\b(?:[0-9A-F]{4} {0,2}){9}[0-9A-F]{4}\b")


class PinError(RuntimeError):
    """A source cannot be pinned; the message says why."""


def _hash_into(url: str, digest: Any) -> None:  # noqa: ANN401 - a hashlib object
    class _Sink:
        def write(self, chunk: bytes) -> int:
            digest.update(chunk)
            return len(chunk)

    net.sha256_of_url(url, out=_Sink())  # type: ignore[arg-type]


def _keyscan(host: str) -> str | None:
    command = shutil.which("ssh-keyscan")
    if command is None:
        return None
    done = subprocess.run(  # noqa: S603 - fixed arguments
        [command, "-T", "15", "-t", "ed25519,ecdsa,rsa", host],
        capture_output=True,
        text=True,
        check=False,
    )
    return done.stdout if done.returncode == 0 and done.stdout.strip() else None


def _gpgv(keyring: bytes, signed: bytes) -> None:
    command = shutil.which("gpgv")
    if command is None:
        raise PinError("gpgv is not installed (Debian and Ubuntu package gpgv)")
    with tempfile.TemporaryDirectory() as tmp:
        ring = Path(tmp, "keyring.gpg")
        ring.write_bytes(keyring)
        done = subprocess.run(  # noqa: S603 - fixed arguments
            [command, "--keyring", str(ring), "-"],
            input=signed,
            capture_output=True,
            check=False,
        )
    if done.returncode != 0:
        detail = done.stderr.decode(errors="replace").strip().splitlines()
        raise PinError(f"the signature does not verify with the key: {' / '.join(detail)}")


def _read_file(path: str) -> bytes:
    return Path(path).read_bytes()


@dataclass
class Network:
    """Everything the tool reads from outside; the tests replace it.

    Attributes:
        download: ``url -> content`` (small files).
        hash_into: Streams a URL into a hash object (large files).
        registry_json: ``(image, path) -> (headers, JSON)`` from a registry's v2 API
            (header names in lower case).
        keyscan: ``host -> ssh-keyscan output``, or None when it cannot run.
        gpgv: ``(keyring, signed) -> None``; raises :class:`PinError` on a bad signature.
        read_file: ``path -> content`` of a local file (the Ubuntu archive keyring).
        extract: ``(deb, path) -> content`` of a file inside a Debian package.
    """

    download: Callable[[str], bytes] = net.download
    hash_into: Callable[[str, Any], None] = _hash_into
    registry_json: Callable[[str, str], tuple[Mapping[str, str], Any]] = net.registry_json
    keyscan: Callable[[str], str | None] = _keyscan
    gpgv: Callable[[bytes, bytes], None] = _gpgv
    read_file: Callable[[str], bytes] = _read_file
    extract: Callable[[bytes, str], bytes] = nvidia_gpus.extract_member


# --- Debian versions ----------------------------------------------------------------------------


def _order(char: str) -> int:
    if char == "~":
        return -1
    if char.isalpha():
        return ord(char)
    return ord(char) + 256


def _compare_part(left: str, right: str) -> int:
    """Compare an upstream version or a revision as dpkg does."""
    while left or right:
        lead_l = re.match(r"^\D*", left).group(0)  # type: ignore[union-attr]
        lead_r = re.match(r"^\D*", right).group(0)  # type: ignore[union-attr]
        for index in range(max(len(lead_l), len(lead_r))):
            a = _order(lead_l[index]) if index < len(lead_l) else 0
            b = _order(lead_r[index]) if index < len(lead_r) else 0
            if a != b:
                return -1 if a < b else 1
        left, right = left[len(lead_l) :], right[len(lead_r) :]
        num_l = re.match(r"^\d*", left).group(0)  # type: ignore[union-attr]
        num_r = re.match(r"^\d*", right).group(0)  # type: ignore[union-attr]
        a, b = int(num_l or 0), int(num_r or 0)
        if a != b:
            return -1 if a < b else 1
        left, right = left[len(num_l) :], right[len(num_r) :]
    return 0


def compare_versions(left: str, right: str) -> int:
    """Compare two Debian package versions as dpkg does (-1, 0 or 1)."""

    def split(version: str) -> tuple[int, str, str]:
        epoch, _, rest = version.rpartition(":") if ":" in version else ("0", "", version)
        upstream, _, revision = rest.rpartition("-") if "-" in rest else (rest, "", "")
        return int(epoch or 0), upstream, revision

    e_l, u_l, r_l = split(left)
    e_r, u_r, r_r = split(right)
    if e_l != e_r:
        return -1 if e_l < e_r else 1
    return _compare_part(u_l, u_r) or _compare_part(r_l, r_r)


def newest(versions: Iterable[str]) -> str:
    """The newest of some Debian versions."""
    best = ""
    for version in versions:
        if not best or compare_versions(version, best) > 0:
            best = version
    if not best:
        raise PinError("no version")
    return best


# --- repository keys ----------------------------------------------------------------------------


@dataclass
class KeyPin:
    """One repository key.

    Attributes:
        name: The key's name under ``repository_keys.keys``.
        urls: Where it is downloaded from (the first is the one the pkg role uses).
        fingerprint: Its primary key fingerprint.
        keyring: Its primary key packets, binary (for gpgv).
        sources: One line per source the fingerprint was taken from or checked against.
    """

    name: str
    urls: tuple[str, ...]
    fingerprint: str
    keyring: bytes
    sources: list[str] = field(default_factory=list)


def _single_fingerprint(data: bytes, url: str) -> str:
    try:
        found = openpgp.primary_fingerprints(data)
    except openpgp.OpenPGPError as exc:
        raise PinError(f"{url}: {exc}") from exc
    if len(found) != 1:
        raise PinError(f"{url} holds {len(found)} primary keys, expected one")
    return found[0]


def pin_key(network: Network, name: str) -> KeyPin:
    """Download a repository key from every URL and cross-check its fingerprint.

    Raises:
        PinError: If the URLs disagree or a published fingerprint differs.
    """
    urls = KEY_URLS[name]
    fingerprints = {}
    first = b""
    for url in urls:
        data = network.download(url)
        fingerprints[url] = _single_fingerprint(data, url)
        first = first or data
    if len(set(fingerprints.values())) != 1:
        found = ", ".join(f"{u}: {f}" for u, f in fingerprints.items())
        raise PinError(f"{name}: the key files differ ({found})")
    fingerprint = fingerprints[urls[0]]
    sources = [f"taken from {url}, primary key computed" for url in urls]
    for page in FINGERPRINT_PAGES.get(name, ()):
        text = network.download(page).decode("utf-8", errors="replace")
        published = {m.replace(" ", "") for m in FINGERPRINT_RE.findall(text)}
        if fingerprint not in published:
            raise PinError(f"{name}: {page} publishes {sorted(published) or 'no fingerprint'}")
        sources.append(f"cross-checked: {page} publishes the same fingerprint")
    lookup = KEYSERVER.format(fingerprint=fingerprint)
    try:
        held = _single_fingerprint(network.download(lookup), lookup)
    except (net.FetchError, PinError) as exc:
        sources.append(f"not cross-checked with keyserver.ubuntu.com: {exc}")
    else:
        if held != fingerprint:
            raise PinError(f"{name}: keyserver.ubuntu.com returns {held} for {fingerprint}")
        sources.append(
            "cross-checked: keyserver.ubuntu.com holds a key with this fingerprint "
            "(the keyserver confirms the key exists, not who owns it)"
        )
    if not FINGERPRINT_PAGES.get(name):
        sources.append("no publication of the fingerprint apart from the key files is known")
    keyring = (
        openpgp.dearmor(first.decode("ascii")) if first.lstrip().startswith(b"-----") else first
    )
    return KeyPin(name, urls, fingerprint, keyring, sources)


# --- packages -----------------------------------------------------------------------------------


@dataclass
class PackagePin:
    """The package versions of one component on one release.

    Attributes:
        component: The component (``incus``).
        release: The release key (``ubuntu-24.04``).
        versions: ``{arch: {package: version}}`` for x86_64 and aarch64.
        repository: The ``component-sources.yml`` repository read.
        index: The package index (URL), its sha256 from the signed release file.
    """

    component: str
    release: str
    versions: dict[str, dict[str, str]]
    repository: str
    index: list[str]


def _codenames(platform_facts_vars: Mapping[str, Any]) -> dict[str, str]:
    table = platform_facts_vars["platform_facts_family"]["codenames"]
    return {f"{v['distro']}-{v['version']}": name for name, v in table.items()}


def _paragraphs(text: str) -> Iterable[dict[str, str]]:
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


def _release_sha256(release: str, path: str) -> str | None:
    for paragraph in _paragraphs(release):
        for line in paragraph.get("SHA256", "").splitlines():
            parts = line.split()
            if len(parts) == 3 and parts[2] == path:  # noqa: PLR2004 - sha256 size path
                return parts[0]
    return None


def _signed_text(signed: bytes) -> str:
    text = signed.decode("utf-8")
    body = text.split("\n\n", 1)[1] if text.startswith("-----BEGIN PGP SIGNED") else text
    return body.split("\n-----BEGIN PGP SIGNATURE-----", 1)[0]


@dataclass(frozen=True)
class AptSource:
    """One apt release of a component that comes from a vendor repository.

    Attributes:
        component: The component (``incus``).
        release: The release key (``ubuntu-24.04``).
        repo_name: The repository's name in ``component-sources.yml``.
        repo: The repository (``uri``, ``suite``, ``components``, ``key``).
        packages: The packages the component installs.
    """

    component: str
    release: str
    repo_name: str
    repo: Mapping[str, Any]
    packages: tuple[str, ...]


def pin_packages(network: Network, source: AptSource, codename: str, key: KeyPin) -> PackagePin:
    """Read the newest versions of a component's packages from a signed apt repository.

    Raises:
        PinError: If the signature, the layout or an index does not check out.
    """
    component, release, repo_name, repo = (
        source.component,
        source.release,
        source.repo_name,
        source.repo,
    )
    packages = source.packages
    distro, _, version = release.partition("-")
    values = {"distro": distro, "codename": codename, "major": version.split(".")[0]}
    uri = str(repo["uri"]).format(**values).rstrip("/")
    suite = str(repo["suite"]).format(**values)
    base = f"{uri}/dists/{suite}"
    signed = network.download(f"{base}/InRelease")
    try:
        network.gpgv(key.keyring, signed)
    except PinError as exc:
        raise PinError(f"{base}/InRelease: {exc}") from exc
    release_text = _signed_text(signed)
    head = next(iter(_paragraphs(release_text)), {})
    components = head.get("Components", "").split()
    arches = head.get("Architectures", "").split()
    for wanted in repo.get("components") or []:
        if wanted not in components:
            raise PinError(f"{base}/InRelease names no component {wanted!r} ({components})")
    versions: dict[str, dict[str, str]] = {}
    index = []
    for arch, apt_arch in APT_ARCHES.items():
        if apt_arch not in arches:
            raise PinError(f"{base}/InRelease has no architecture {apt_arch}")
        component_dir = (repo.get("components") or [""])[0]
        found = None
        for name, opener in (
            ("Packages.xz", lzma.decompress),
            ("Packages.gz", gzip.decompress),
            ("Packages", bytes),
        ):
            path = f"{component_dir}/binary-{apt_arch}/{name}".lstrip("/")
            expected = _release_sha256(release_text, path)
            if expected:
                data = network.download(f"{base}/{path}")
                actual = hashlib.sha256(data).hexdigest()
                if actual != expected:
                    raise PinError(f"{base}/{path} has sha256 {actual}, InRelease says {expected}")
                found = opener(data).decode("utf-8")
                index.append(f"{base}/{path} sha256 {actual}")
                break
        if found is None:
            raise PinError(f"{base}/InRelease lists no package index for {apt_arch}")
        available: dict[str, list[str]] = {}
        for paragraph in _paragraphs(found):
            available.setdefault(paragraph.get("Package", ""), []).append(
                paragraph.get("Version", "")
            )
        missing = [p for p in packages if p not in available]
        if missing:
            raise PinError(f"{base} ({apt_arch}) has no package {', '.join(missing)}")
        versions[arch] = {p: newest(available[p]) for p in packages}
    return PackagePin(component, release, versions, repo_name, index)


def apt_sources(lock: Mapping[str, Any], sources: Mapping[str, Any]) -> list[AptSource]:
    """The apt releases of the lock whose component comes from a vendor repository."""
    found = []
    for component in COMPONENTS:
        for release in lock["packages"][component]:
            if release in ("verified_on", "source"):
                continue
            group = release.split("-", 1)[0]
            entry = sources["components"][component].get(group)
            if not entry or entry.get("source") != "vendor_repo":
                continue
            repo = sources["repositories"][entry["repo"]]
            if repo.get("type") != "apt":
                continue
            found.append(
                AptSource(component, release, str(entry["repo"]), repo, tuple(entry["packages"]))
            )
    return found


# --- the DCGM exporter --------------------------------------------------------------------------


@dataclass
class ImagePin:
    """A container image.

    Attributes:
        image: The image name.
        tag: Its tag.
        digest: The digest of the tag's index or manifest.
        platforms: Its platforms the lock knows.
        notes: Lines for the review.
    """

    image: str
    tag: str
    digest: str
    platforms: list[str]
    notes: list[str]


def _release_key(tag: str) -> tuple[int, ...]:
    return tuple(int(x) for x in tag.split("."))


def pin_dcgm(network: Network) -> ImagePin:
    """The newest DCGM exporter release on nvcr.io, cross-checked with its source.

    Raises:
        PinError: If no release tag exists or the source does not match it.
    """
    _, tags = network.registry_json(DCGM_IMAGE, "tags/list")
    releases = [t for t in tags.get("tags") or [] if DCGM_TAG_RE.match(t)]
    if not releases:
        raise PinError(f"{DCGM_IMAGE} has no release tag X.Y.Z")
    tag = max(releases, key=_release_key)
    versions_url = DCGM_VERSIONS.format(tag=tag)
    env = dict(
        line.split("=", 1)
        for line in network.download(versions_url).decode().splitlines()
        if "=" in line and not line.startswith("#")
    )
    if env.get("EXPORTER_VERSION") != tag:
        raise PinError(f"{versions_url} names EXPORTER_VERSION {env.get('EXPORTER_VERSION')}")
    headers, manifest = network.registry_json(DCGM_IMAGE, f"manifests/{tag}")
    digest = str(headers.get("docker-content-digest") or "")
    if not re.match(r"^sha256:[0-9a-f]{64}$", digest):
        raise PinError(f"{DCGM_IMAGE}:{tag}: no digest in the registry's answer")
    platforms = sorted(
        {
            f"{(m.get('platform') or {}).get('os')}/{(m.get('platform') or {}).get('architecture')}"
            for m in manifest.get("manifests") or []
        }
        & {"linux/amd64", "linux/arm64"}
    )
    if "linux/amd64" not in platforms:
        raise PinError(f"{DCGM_IMAGE}:{tag} has no linux/amd64 image")
    notes = [
        f"newest release tag of {len(releases)} on nvcr.io; {versions_url}: "
        f"EXPORTER_VERSION={tag}, DCGM_VERSION={env.get('DCGM_VERSION', 'not named')}",
    ]
    return ImagePin(DCGM_IMAGE, tag, digest, platforms, notes)


# --- the Incus base images ----------------------------------------------------------------------


@dataclass
class BaseImagePin:
    """The fingerprints of one pinned base image.

    Attributes:
        name: The key under ``incus_base_images.images``.
        fingerprints: ``{arch: fingerprint}``.
        notes: Lines for the review.
    """

    name: str
    fingerprints: dict[str, str]
    notes: list[str]


def pin_base_image(network: Network, name: str, entry: Mapping[str, Any]) -> BaseImagePin:
    """The newest build of a pinned alias, its fingerprint computed from the downloads.

    Raises:
        PinError: If the alias, a build or its files are missing, or the sums differ.
    """
    server = IMAGE_REMOTES.get(str(entry["remote"]))
    if server is None:
        raise PinError(f"{name}: unknown remote {entry['remote']!r}")
    field_name, root_type = IMAGE_TYPES[str(entry["type"])]
    streams = json.loads(network.download(server + SIMPLESTREAMS))
    fingerprints: dict[str, str] = {}
    notes = []
    for arch in entry["fingerprints"]:
        apt_arch = APT_ARCHES[arch]
        products = [
            p
            for p in streams.get("products", {}).values()
            if entry["alias"] in str(p.get("aliases", "")).split(",") and p.get("arch") == apt_arch
        ]
        if len(products) != 1:
            raise PinError(
                f"{name}: {len(products)} products with alias {entry['alias']} ({apt_arch})"
            )
        builds = products[0].get("versions") or {}
        candidates = [
            (version, items)
            for version, build in sorted(builds.items())
            if (items := build.get("items") or {})
        ]
        if not candidates:
            raise PinError(f"{name}: no build of {entry['alias']} ({apt_arch})")
        version, items = candidates[-1]
        meta = next((i for i in items.values() if i.get("ftype") == "incus.tar.xz"), None)
        root = next((i for i in items.values() if i.get("ftype") == root_type), None)
        if meta is None or root is None or not meta.get(field_name):
            raise PinError(
                f"{name}: build {version} ({apt_arch}) lacks its metadata or root file system"
            )
        digest = hashlib.sha256()
        for item in (meta, root):
            network.hash_into(f"{server}/{item['path']}", digest)
        computed = digest.hexdigest()
        if computed != meta[field_name]:
            raise PinError(
                f"{name}: build {version} ({apt_arch}) sums to {computed}, "
                f"the index says {meta[field_name]}"
            )
        fingerprints[arch] = computed
        notes.append(
            f"{arch}: build {version} of {len(builds)} on the server "
            f"({', '.join(sorted(builds))}); the fingerprint computed from incus.tar.xz and "
            "the root file system equals the index"
        )
    notes.append(
        "the server keeps only its newest builds (a few days): pin right before the first "
        "golden image build; afterwards the lab's export serves the base"
    )
    return BaseImagePin(name, fingerprints, notes)


# --- GitHub's SSH host keys ---------------------------------------------------------------------


def ssh_fingerprint(public_key: str) -> str:
    """OpenSSH's SHA256 fingerprint of a base64 public key blob."""
    blob = base64.b64decode(public_key, validate=True)
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")


def _key_lines(text: str) -> dict[str, str]:
    keys = {}
    for line in text.splitlines():
        parts = line.split()
        for index, part in enumerate(parts[:-1]):
            if part in SSH_KEY_TYPES and parts[index + 1].startswith("AAAA"):
                keys[part] = parts[index + 1]
    return keys


def _source_meta(network: Network) -> dict[str, str]:
    meta = json.loads(network.download(GITHUB_META))
    keys = _key_lines("\n".join(str(k) for k in meta.get("ssh_keys") or []))
    published = meta.get("ssh_key_fingerprints") or {}
    for kind, key in keys.items():
        if published.get(META_FINGERPRINTS[kind]) != ssh_fingerprint(key):
            raise PinError(
                f"{GITHUB_META}: the published fingerprint of {kind} does not match its key"
            )
    return keys


def _source_docs(network: Network) -> dict[str, str]:
    keys = _key_lines(network.download(GITHUB_DOCS.format(name="known_hosts")).decode())
    listed = network.download(GITHUB_DOCS.format(name="fingerprints")).decode()
    published = {
        DOCS_FINGERPRINTS[m.group(2)]: m.group(1)
        for m in re.finditer(r"`(SHA256:[0-9A-Za-z+/]{43})` \((Ed25519|ECDSA|RSA)\)", listed)
    }
    for kind, key in keys.items():
        if published.get(kind) != ssh_fingerprint(key):
            raise PinError(
                f"GitHub's documentation: the fingerprint of {kind} does not match its key"
            )
    return keys


def pin_github_keys(network: Network) -> tuple[dict[str, str], list[str]]:
    """GitHub's SSH host keys, agreed by at least two sources.

    Returns:
        ``{type: public key}`` and one line per source.

    Raises:
        PinError: If sources disagree, fewer than two are reachable, or a type is missing.
    """
    sources: dict[str, dict[str, str]] = {}
    lines = []
    readers: list[tuple[str, Callable[[], dict[str, str]]]] = [
        ("GitHub's meta API (api.github.com/meta)", lambda: _source_meta(network)),
        ("GitHub's documentation (github/docs, data/reusables/ssh)", lambda: _source_docs(network)),
        (
            "the server (ssh-keyscan github.com)",
            lambda: _key_lines(network.keyscan("github.com") or ""),
        ),
    ]
    for label, read in readers:
        try:
            keys = read()
        except (net.FetchError, ValueError) as exc:
            lines.append(f"not reachable: {label}: {exc}")
            continue
        if not keys:
            lines.append(f"not reachable: {label}: no keys")
            continue
        sources[label] = keys
        lines.append(f"read: {label}")
    if len(sources) < MIN_SSH_SOURCES:
        raise PinError(
            f"GitHub's SSH host keys need at least {MIN_SSH_SOURCES} agreeing sources, "
            f"{len(sources)} reachable ({'; '.join(lines)})"
        )
    agreed = next(iter(sources.values()))
    for label, keys in sources.items():
        for kind in SSH_KEY_TYPES:
            if keys.get(kind) != agreed.get(kind):
                raise PinError(f"{kind}: {label} differs from {next(iter(sources))}")
    missing = [k for k in SSH_KEY_TYPES if k not in agreed]
    if missing:
        raise PinError(f"no source has a key of type {', '.join(missing)}")
    return {k: agreed[k] for k in SSH_KEY_TYPES}, lines


# --- the run ------------------------------------------------------------------------------------


# --- NVIDIA driver GPU lists -----------------------------------------------------------------

UBUNTU_ARCHIVE = "https://archive.ubuntu.com/ubuntu"
UBUNTU_KEYRING = "/usr/share/keyrings/ubuntu-archive-keyring.gpg"


@dataclass
class GpuPin:
    """The pinned GPU list of one driver release.

    Attributes:
        version: The driver release (``580.178.04``).
        source: The package the list was read from.
        package_sha256: Its sha256.
        path: The list's path inside the package.
        sha256: The list's sha256.
        gpus: The device IDs.
        notes: Where everything came from and what it was checked against.
    """

    version: str
    source: str
    package_sha256: str
    path: str
    sha256: str
    gpus: nvidia_gpus.GpuList
    notes: list[str] = field(default_factory=list)


def _ubuntu_restricted(network: Network, codename: str) -> tuple[str, list[dict[str, str]]]:
    """The restricted amd64 index of ``<codename>-updates``, its signature and sha256 checked."""
    base = f"{UBUNTU_ARCHIVE}/dists/{codename}-updates"
    signed = network.download(f"{base}/InRelease")
    try:
        keyring = network.read_file(UBUNTU_KEYRING)
    except OSError as exc:
        raise PinError(f"the Ubuntu archive keyring is missing ({exc}); run on Ubuntu") from exc
    try:
        network.gpgv(keyring, signed)
    except PinError as exc:
        raise PinError(f"{base}/InRelease: {exc}") from exc
    release_text = _signed_text(signed)
    for name, opener in (("Packages.xz", lzma.decompress), ("Packages.gz", gzip.decompress)):
        path = f"restricted/binary-amd64/{name}"
        expected = _release_sha256(release_text, path)
        if not expected:
            continue
        data = network.download(f"{base}/{path}")
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected:
            raise PinError(f"{base}/{path} has sha256 {actual}, InRelease says {expected}")
        note = (
            f"{codename}-updates restricted: InRelease signed by the Ubuntu archive keyring; "
            f"{name} sha256 {actual}"
        )
        return note, list(_paragraphs(opener(data).decode("utf-8")))
    raise PinError(f"{base}/InRelease lists no restricted amd64 index")


def _package(paragraphs: Iterable[Mapping[str, str]], name: str, version: str) -> Mapping[str, str]:
    for paragraph in paragraphs:
        if paragraph.get("Package") == name and paragraph.get("Version") == version:
            return paragraph
    raise PinError(f"the index has no {name} {version}")


def pin_nvidia_gpus(
    network: Network, lock: Mapping[str, Any], codenames: Mapping[str, str]
) -> list[GpuPin]:
    """Pin the GPU list of every NVIDIA driver release the lock pins for an Ubuntu release.

    Raises:
        PinError: If an index, a package, the list or a cross-check does not check out.
    """
    pins: dict[str, GpuPin] = {}
    readmes: dict[str, set[str]] = {}
    for release, packages in lock["packages"]["nvidia_driver"].items():
        if not release.startswith("ubuntu-") or not isinstance(packages, Mapping):
            continue
        for package, package_version in packages.items():
            branch = nvidia_gpus.branch_of(str(package))
            if branch is None or checks.MARKER_RE.search(str(package_version)):
                continue
            version = nvidia_gpus.upstream_version(str(package_version))
            if release not in codenames:
                raise PinError(f"no codename for {release} in platform_facts")
            note, paragraphs = _ubuntu_restricted(network, codenames[release])
            common = _package(
                paragraphs, nvidia_gpus.COMMON_PACKAGE.format(branch=branch), str(package_version)
            )
            source = f"{UBUNTU_ARCHIVE}/{common['Filename']}"
            deb = network.download(source)
            package_sha256 = hashlib.sha256(deb).hexdigest()
            if package_sha256 != common.get("SHA256"):
                raise PinError(f"{source} has sha256 {package_sha256}, the index says otherwise")
            path = nvidia_gpus.JSON_PATH.format(branch=branch)
            try:
                data = network.extract(deb, path)
                gpus = nvidia_gpus.parse_supported_gpus(data)
                if version not in readmes:
                    readme = network.download(nvidia_gpus.README_URL.format(version=version))
                    readmes[version] = nvidia_gpus.parse_readme(readme.decode("utf-8"))
            except nvidia_gpus.GpuListError as exc:
                raise PinError(f"{source}: {exc}") from exc
            open_pkg = _package(
                paragraphs, nvidia_gpus.OPEN_PACKAGE.format(branch=branch), str(package_version)
            )
            proprietary = _package(
                paragraphs,
                nvidia_gpus.PROPRIETARY_PACKAGE.format(branch=branch),
                str(package_version),
            )
            problems = nvidia_gpus.cross_check(
                gpus,
                readme=readmes[version],
                open_aliases=nvidia_gpus.parse_modaliases(open_pkg.get("Modaliases", "")),
                aliases=nvidia_gpus.parse_modaliases(proprietary.get("Modaliases", "")),
            )
            if problems:
                raise PinError(f"{release} {version}: " + "; ".join(problems))
            sha256 = hashlib.sha256(data).hexdigest()
            if version in pins:
                if pins[version].sha256 != sha256:
                    raise PinError(f"{release}: another supported-gpus.json for {version}")
                pins[version].notes.append(f"the same file in {release} ({package_version})")
                continue
            pins[version] = GpuPin(
                version,
                source,
                package_sha256,
                path,
                sha256,
                gpus,
                [
                    f"supported-gpus.json of NVIDIA driver {version}, from Ubuntu's "
                    f"{common['Package']} {package_version} ({note})",
                    f"cross-checked: the {len(gpus.open_module)} open-module IDs equal NVIDIA's "
                    f"open-gpu-kernel-modules README at tag {version} and the Modaliases of "
                    f"{open_pkg['Package']}; the {len(gpus.current)} current IDs equal the "
                    f"Modaliases of {proprietary['Package']}",
                ],
            )
    return list(pins.values())


def _id_lines(ids: Sequence[str], indent: str, per_line: int = 12) -> list[str]:
    quoted = [f'"{i}"' for i in ids]
    rows = [", ".join(quoted[n : n + per_line]) for n in range(0, len(quoted), per_line)]
    return [f"{indent}{row}," for row in rows[:-1]] + [f"{indent}{rows[-1]}"] if rows else []


def _comment(lines: Iterable[str], indent: str = "") -> list[str]:
    return [f"{indent}# {line}" for line in lines]


def render_gpu_pins(pins: Iterable[GpuPin], indent: str = "") -> list[str]:
    """The entries of ``nvidia_driver_gpus.releases`` in the lock's format."""
    out: list[str] = []
    for pin in pins:
        notes = [
            part for note in pin.notes for part in textwrap.wrap(note, 80, break_on_hyphens=False)
        ]
        out += [f'{indent}"{pin.version}":', *_comment(notes, indent + "  ")]
        out += [
            f"{indent}  source: {pin.source}",
            f"{indent}  package_sha256: {pin.package_sha256}",
            f"{indent}  path: {pin.path}",
            f"{indent}  sha256: {pin.sha256}",
            f"{indent}  open_module: [",
            *_id_lines(pin.gpus.open_module, indent + "    "),
            f"{indent}  ]",
            f"{indent}  proprietary_only: [",
            *_id_lines(pin.gpus.proprietary_only, indent + "    "),
            f"{indent}  ]",
        ]
    return out


def run(  # noqa: PLR0912, PLR0915 - one block per section
    lock: Mapping[str, Any],
    sources: Mapping[str, Any],
    platform_facts_vars: Mapping[str, Any],
    network: Network,
    now: dt.datetime,
) -> tuple[str, list[str]]:
    """Pin every section.

    Returns:
        The output in the lock's format, and one line per section that could not be pinned.
    """
    out = [
        f"# make lock-pins {now.strftime('%Y-%m-%dT%H:%M:%SZ')}: read from public URLs;",
        "# nothing was installed or changed. Review, then paste each block where its",
        "# header says.",
        f"verified_on: {now.date().isoformat()}",
    ]
    failed: list[str] = []
    keys: dict[str, KeyPin] = {}
    out.append("# --- repository_keys.keys")
    for name in KEY_URLS:
        try:
            key_pin = keys[name] = pin_key(network, name)
        except (PinError, net.FetchError) as exc:
            failed.append(f"repository_keys.keys.{name}: {exc}")
            out.append(f"# {name}: NOT PINNED: {exc}")
            continue
        out += [f"{name}:", *_comment(key_pin.sources, "  ")]
        out.append("  urls:")
        out += [f"    - {url}" for url in key_pin.urls]
        out.append(f"  fingerprint: {key_pin.fingerprint}")
    codenames = _codenames(platform_facts_vars)
    by_component: dict[str, list[str]] = {}
    for source in apt_sources(lock, sources):
        component, release, repo_name = source.component, source.release, source.repo_name
        lines = by_component.setdefault(component, [])
        key = keys.get(str(source.repo["key"]))
        try:
            if key is None:
                raise PinError(f"the key {source.repo['key']} of {repo_name} is not pinned")
            if release not in codenames:
                raise PinError(f"no codename for {release} in platform_facts")
            pin = pin_packages(network, source, codenames[release], key)
        except (PinError, net.FetchError, OSError, ValueError) as exc:
            failed.append(f"packages.{component}.{release}: {exc}")
            lines.append(f"# {release}: NOT PINNED: {exc}")
            continue
        main, other = pin.versions["x86_64"], pin.versions["aarch64"]
        rendered = ", ".join(f'{p}: "{v}"' for p, v in main.items())
        lines.append(f"{release}: {{{rendered}}}")
        lines += _comment(
            [f"InRelease of {repo_name} signed by {key.fingerprint}; {i}" for i in pin.index], "  "
        )
        if other != main:
            lines += _comment(
                [
                    "x86_64 versions above; aarch64 has "
                    + ", ".join(f"{p} {v}" for p, v in other.items())
                    + " (the lock has one version per release: aarch64 hosts cannot install it)"
                ],
                "  ",
            )
        else:
            lines += _comment(["the same versions on x86_64 and aarch64"], "  ")
    for component in COMPONENTS:
        out.append(
            f"# --- packages.{component} (apt releases; layout: ansible/component-sources.yml)"
        )
        out += by_component.get(component) or [
            "# no apt release of this component uses a vendor repository"
        ]
    out.append("# --- containers.images.dcgm_exporter")
    try:
        image = pin_dcgm(network)
    except (PinError, net.FetchError, ValueError) as exc:
        failed.append(f"containers.images.dcgm_exporter: {exc}")
        out.append(f"# NOT PINNED: {exc}")
    else:
        out += [
            "dcgm_exporter:",
            *_comment(image.notes, "  "),
            f"  image: {image.image}",
            f'  tag: "{image.tag}"',
            f"  digest: {image.digest}",
            f"  platforms: [{', '.join(image.platforms)}]",
        ]
    out.append("# --- containers.images.<name>.sizes (compressed bytes per platform)")
    for name, entry in lock["containers"]["images"].items():
        try:
            sizes = net.image_sizes(entry["image"], entry["digest"], network.registry_json)
        except (net.FetchError, KeyError, ValueError) as exc:
            failed.append(f"containers.images.{name}.sizes: {exc}")
            out.append(f"# {name}: NOT PINNED: {exc}")
            continue
        rendered = ", ".join(f"{platform}: {size}" for platform, size in sorted(sizes.items()))
        out.append(f"{name}: {{sizes: {{{rendered}}}}}")
    out.append("# --- incus_base_images.images.<name>.fingerprints")
    for name, entry in lock["incus_base_images"]["images"].items():
        try:
            base = pin_base_image(network, name, entry)
        except (PinError, net.FetchError, ValueError, KeyError) as exc:
            failed.append(f"incus_base_images.images.{name}: {exc}")
            out.append(f"# {name}: NOT PINNED: {exc}")
            continue
        out += [f"{name}:", *_comment(base.notes, "  "), "  fingerprints:"]
        out += [f'    {arch}: "{fp}"' for arch, fp in base.fingerprints.items()]
    out.append("# --- nvidia_driver_gpus.releases")
    try:
        gpu_pins = pin_nvidia_gpus(network, lock, codenames)
    except (PinError, net.FetchError, OSError, ValueError) as exc:
        failed.append(f"nvidia_driver_gpus: {exc}")
        out.append(f"# NOT PINNED: {exc}")
    else:
        out += render_gpu_pins(gpu_pins)
    out.append("# --- github_ssh_host_keys.keys")
    try:
        ssh_keys, ssh_sources = pin_github_keys(network)
    except PinError as exc:
        failed.append(f"github_ssh_host_keys: {exc}")
        out.append(f"# NOT PINNED: {exc}")
    else:
        out += _comment(ssh_sources)
        out += _comment(["every fingerprint computed from its key; every reachable source agrees"])
        for kind, public_key in ssh_keys.items():
            fingerprint = ssh_fingerprint(public_key)
            out.append(f'{kind}: {{public_key: "{public_key}", fingerprint: "{fingerprint}"}}')
    if failed:
        out.append(f"# {len(failed)} section(s) not pinned; see the NOT PINNED lines")
    return "\n".join(out) + "\n", failed


def main(argv: Sequence[str] | None = None) -> int:
    """Print the pins; 1 when any section cannot be pinned."""
    parser = argparse.ArgumentParser(prog="lock-pins", description=__doc__.splitlines()[0])
    parser.add_argument("--lock", type=Path, default=Path("versions.lock.yml"))
    parser.add_argument("--sources", type=Path, default=Path("ansible/component-sources.yml"))
    parser.add_argument(
        "--codenames",
        type=Path,
        default=Path("ansible/roles/platform_facts/vars/debian.yml"),
        help="the Debian-family codename table",
    )
    args = parser.parse_args(argv)
    try:
        lock = checks.load(args.lock)
        sources = yaml.safe_load(args.sources.read_text(encoding="utf-8"))
        facts = yaml.safe_load(args.codenames.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"lock-pins: {exc}\n")
        return 1
    text, failed = run(lock, sources, facts, Network(), dt.datetime.now(dt.UTC))
    sys.stdout.write(text)
    for line in failed:
        sys.stderr.write(f"lock-pins: not pinned: {line}\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
