"""make lock-pins: the vendor sources item 0 needs, checked and printed (no network)."""

from __future__ import annotations

import base64
import copy
import datetime as dt
import gzip
import hashlib
import json
import os
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml

from pinka_lockcheck import lockpins, net, nvidia_gpus, openpgp

ROOT = Path(__file__).resolve().parents[3]
ARMOR = "-----BEGIN PGP PUBLIC" + " KEY BLOCK-----"
ARMOR_END = "-----END PGP PUBLIC" + " KEY BLOCK-----"
NOW = dt.datetime(2026, 10, 17, 12, 0, tzinfo=dt.UTC)
# Not real keys: base64 blobs that start like an OpenSSH key ("AAAA").
ED25519 = base64.b64encode(b"\x00\x00\x00\x0bssh-ed25519 test key one").decode()
ECDSA = base64.b64encode(b"\x00\x00\x00\x13ecdsa-sha2-nistp256 test key").decode()
RSA = base64.b64encode(b"\x00\x00\x00\x07ssh-rsa test key three").decode()


def public_key_packet(seed: bytes) -> bytes:
    """A version 4 public key packet (tag 6) with a body derived from seed."""
    body = b"\x04" + b"\x00\x00\x00\x01" + b"\x16" + hashlib.sha256(seed).digest()
    return bytes([0x80 | (6 << 2), len(body)]) + body


def armored(packet: bytes) -> bytes:
    text = base64.b64encode(packet).decode()
    return f"{ARMOR}\n\n{text}\n{ARMOR_END}\n".encode()


ZABBLY = public_key_packet(b"zabbly")
TAILSCALE = public_key_packet(b"tailscale")
UBUNTU = public_key_packet(b"ubuntu archive")
DOCKER = public_key_packet(b"docker")
CTK = public_key_packet(b"nvidia container toolkit")
ZABBLY_FP = openpgp.primary_fingerprints(ZABBLY)[0]
TAILSCALE_FP = openpgp.primary_fingerprints(TAILSCALE)[0]


def grouped(fingerprint: str) -> str:
    groups = [fingerprint[i : i + 4] for i in range(0, 40, 4)]
    return " ".join(groups[:5]) + "  " + " ".join(groups[5:])


def packages_index(versions: Mapping[str, list[str]]) -> bytes:
    stanzas = [
        f"Package: {name}\nVersion: {v}\nArchitecture: any\n"
        for name, listed in versions.items()
        for v in listed
    ]
    return gzip.compress("\n".join(stanzas).encode(), mtime=0)


def in_release(files: Mapping[str, bytes], components: str = "main") -> bytes:
    sums = "".join(
        f" {hashlib.sha256(data).hexdigest()} {len(data)} {path}\n" for path, data in files.items()
    )
    body = (
        f"Origin: test\nSuite: x\nArchitectures: amd64 arm64\nComponents: {components}\n"
        f"SHA256:\n{sums}"
    )
    return (
        "-----BEGIN PGP SIGNED MESSAGE-----\nHash: SHA256\n\n"
        f"{body}\n-----BEGIN PGP SIGNATURE-----\n\nfake\n-----END PGP SIGNATURE-----\n"
    ).encode()


def apt_repo(
    base: str,
    versions: Mapping[str, Mapping[str, list[str]]],
    component: str = "main",
    **kwargs: str,
) -> dict[str, bytes]:
    files = {
        f"{component}/binary-{arch}/Packages.gz": packages_index(versions[arch])
        for arch in versions
    }
    out = {f"{base}/{path}": data for path, data in files.items()}
    out[f"{base}/InRelease"] = in_release(files, **{"components": component, **kwargs})
    return out


def flat_repo(base: str, versions: Mapping[str, list[str]]) -> dict[str, bytes]:
    """A flat repository (suite ``/``): the index next to its release file."""
    files = {"Packages.gz": packages_index(versions)}
    return {f"{base}/Packages.gz": files["Packages.gz"], f"{base}/InRelease": in_release(files)}


SOURCES: dict[str, Any] = yaml.safe_load((ROOT / "ansible" / "component-sources.yml").read_text())
FACTS: dict[str, Any] = yaml.safe_load(
    (ROOT / "ansible" / "roles" / "platform_facts" / "vars" / "debian.yml").read_text()
)
LOCK: dict[str, Any] = yaml.safe_load((ROOT / "versions.lock.yml").read_text())

# The NVIDIA driver release the lock pins for Ubuntu, and a small GPU list for it:
# Turing and Ada with the open module, Pascal with the proprietary one only,
# Kepler only in the legacy branch.
DRIVER = {r: p for r, p in LOCK["packages"]["nvidia_driver"].items() if r.startswith("ubuntu-")}
UBUNTU_CODENAMES = {"ubuntu-26.04": "resolute", "ubuntu-24.04": "noble", "ubuntu-22.04": "jammy"}
DOCKER_PACKAGES = tuple(SOURCES["components"]["docker"]["ubuntu"]["packages"])
CTK_PACKAGES = tuple(SOURCES["components"]["nvidia_container_toolkit"]["ubuntu"]["packages"])
DRIVER_VERSION = nvidia_gpus.upstream_version(next(iter(DRIVER["ubuntu-24.04"].values())))
SUPPORTED_GPUS = json.dumps(
    {
        "chips": [
            {"devid": "0x1E04", "name": "RTX 2080 Ti", "features": ["kernelopen"]},
            {"devid": "0x2786", "name": "RTX 4070", "features": ["kernelopen", "vdpaufeatureset"]},
            {"devid": "0x1D01", "name": "GeForce 1030", "features": []},
            {"devid": "0x1287", "name": "GeForce 730", "features": [], "legacybranch": "470.xx"},
        ]
    }
).encode()
README = (
    "The open kernel modules can be used on any Turing or later GPU.\n\n"
    "| Product Name | PCI ID |\n| --- | --- |\n"
    "| NVIDIA GeForce RTX 2080 Ti | 1E04 |\n| NVIDIA GeForce RTX 4070 | 2786 10DE 1234 |\n"
)


def modaliases(*ids: str) -> str:
    return "nvidia(" + ", ".join(f"pci:v000010DEd0000{i}sv*sd*bc03sc*i*" for i in ids) + ")"


def ubuntu_suites(codename: str) -> dict[str, bytes]:
    """The restricted indexes of ``<codename>-security`` and ``<codename>``: an older driver."""
    out = {}
    for suite in (f"{codename}-security", codename):
        index = gzip.compress(
            b"Package: nvidia-driver-580-open\nVersion: 580.0-0ubuntu1\n", mtime=0
        )
        files = {"restricted/binary-amd64/Packages.gz": index}
        base = f"{lockpins.UBUNTU_ARCHIVE}/dists/{suite}"
        out[f"{base}/restricted/binary-amd64/Packages.gz"] = index
        out[f"{base}/InRelease"] = in_release(files, components="main restricted")
    return out


def ubuntu_restricted(
    release: str, codename: str, *, open_ids: tuple[str, ...] = ("1E04", "2786")
) -> dict[str, bytes]:
    """The signed restricted index of ``<codename>-updates`` and the common package it names."""
    (version,) = DRIVER[release].values()
    deb = f"deb of nvidia-kernel-common-580 {version}".encode()
    pool = f"pool/restricted/n/nvidia-graphics-drivers-580/nvidia-kernel-common-580_{version}.deb"
    stanzas = [
        f"Package: nvidia-kernel-common-580\nVersion: {version}\nFilename: {pool}\n"
        f"SHA256: {hashlib.sha256(deb).hexdigest()}\n",
        f"Package: nvidia-driver-580-open\nVersion: {version}\n"
        f"Modaliases: {modaliases(*open_ids)}\n",
        f"Package: nvidia-driver-580\nVersion: {version}\n"
        f"Modaliases: {modaliases('1D01', '1E04', '2786')}\n",
    ]
    index = gzip.compress("\n".join(stanzas).encode(), mtime=0)
    base = f"{lockpins.UBUNTU_ARCHIVE}/dists/{codename}-updates"
    files = {"restricted/binary-amd64/Packages.gz": index}
    return {
        f"{base}/restricted/binary-amd64/Packages.gz": index,
        f"{base}/InRelease": in_release(files, components="main restricted"),
        f"{lockpins.UBUNTU_ARCHIVE}/{pool}": deb,
    }


def extract(deb: bytes, member: str) -> bytes:
    """The fake packages hold only supported-gpus.json."""
    assert member == nvidia_gpus.JSON_PATH.format(branch="580"), member
    assert deb.startswith(b"deb of nvidia-kernel-common-580")
    return SUPPORTED_GPUS


class World:
    """A fake internet with every source of the tool."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.big: dict[str, bytes] = {}
        self.registry: dict[str, tuple[dict[str, str], Any]] = {}
        self.keyscan_output: str | None = None
        self.trusted = {ZABBLY: ZABBLY, TAILSCALE: TAILSCALE}
        for url in lockpins.KEY_URLS["zabbly"]:
            self.files[url] = armored(ZABBLY)
        for url in lockpins.KEY_URLS["tailscale"]:
            self.files[url] = armored(TAILSCALE)
        self.files[lockpins.FINGERPRINT_PAGES["zabbly"][0]] = (
            f"verify that the fingerprint matches `{grouped(ZABBLY_FP)}`".encode()
        )
        for fp, packet in ((ZABBLY_FP, ZABBLY), (TAILSCALE_FP, TAILSCALE)):
            self.files[lockpins.KEYSERVER.format(fingerprint=fp)] = armored(packet)
        for name, packet in (("docker-deb", DOCKER), ("nvidia-container-toolkit", CTK)):
            self.files[LOCK["repository_keys"]["keys"][name]["urls"][0]] = armored(packet)
        for arch in ("amd64", "arm64"):
            self.files.update(
                flat_repo(
                    f"https://nvidia.github.io/libnvidia-container/stable/deb/{arch}",
                    {p: ["1.20.0-1", "1.20.1-1"] for p in CTK_PACKAGES},
                )
            )
        codenames = {
            "ubuntu-26.04": "resolute",
            "ubuntu-24.04": "noble",
            "ubuntu-22.04": "jammy",
            "debian-13": "trixie",
        }
        for release, codename in codenames.items():
            distro = release.split("-")[0]
            self.files.update(
                apt_repo(
                    f"https://download.docker.com/linux/{distro}/dists/{codename}",
                    {arch: {p: ["1", "2"] for p in DOCKER_PACKAGES} for arch in ("amd64", "arm64")},
                    component="stable",
                )
            )
            build = {"amd64": "202603272003", "arm64": "202603272249"}
            self.files.update(
                apt_repo(
                    f"https://pkgs.zabbly.com/incus/lts-6.0/dists/{codename}",
                    {
                        arch: {"incus": ["1:6.0.6-x-202603151019", f"1:6.0.6-x-{build[arch]}"]}
                        for arch in ("amd64", "arm64")
                    },
                )
            )
            self.files.update(
                apt_repo(
                    f"https://pkgs.tailscale.com/stable/{distro}/dists/{codename}",
                    {arch: {"tailscale": ["1.104.0", "1.104.1"]} for arch in ("amd64", "arm64")},
                )
            )
        for release, codename in UBUNTU_CODENAMES.items():
            self.files.update(ubuntu_restricted(release, codename))
            self.files.update(ubuntu_suites(codename))
        self.files[nvidia_gpus.README_URL.format(version=DRIVER_VERSION)] = README.encode()
        self.registry["tags/list"] = (
            {},
            {"tags": ["4.8.3-distroless", "4.8.4", "4.8.10", "4.9.0-rc1"]},
        )
        self.registry["manifests/4.8.10"] = (
            {"docker-content-digest": "sha256:" + "a" * 64},
            {
                "manifests": [
                    {"platform": {"os": "linux", "architecture": "amd64"}},
                    {"platform": {"os": "linux", "architecture": "arm64"}},
                ]
            },
        )
        self.files[lockpins.DCGM_VERSIONS.format(tag="4.8.10")] = (
            b"# versions\nDCGM_VERSION=4.6.1\nEXPORTER_VERSION=4.8.10\n"
        )
        products = {}
        for arch, apt_arch in lockpins.APT_ARCHES.items():
            meta, root = f"meta-{arch}".encode(), f"root-{arch}".encode()
            base = f"images/ubuntu/noble/{apt_arch}/cloud/20261016_07:42"
            self.big[f"https://images.linuxcontainers.org/{base}/incus.tar.xz"] = meta
            self.big[f"https://images.linuxcontainers.org/{base}/rootfs.squashfs"] = root
            products[f"ubuntu:noble:{apt_arch}:cloud"] = {
                "aliases": "ubuntu/noble/cloud,ubuntu/24.04/cloud",
                "arch": apt_arch,
                "versions": {
                    "20261015_07:42": {"items": {}},
                    "20261016_07:42": {
                        "items": {
                            "incus.tar.xz": {
                                "ftype": "incus.tar.xz",
                                "path": f"{base}/incus.tar.xz",
                                "combined_squashfs_sha256": hashlib.sha256(meta + root).hexdigest(),
                            },
                            "root.squashfs": {
                                "ftype": "squashfs",
                                "path": f"{base}/rootfs.squashfs",
                            },
                        }
                    },
                },
            }
        self.files["https://images.linuxcontainers.org/streams/v1/images.json"] = json.dumps(
            {"products": products}
        ).encode()
        self.set_docs(ED25519, ECDSA, RSA)

    def set_docs(self, ed: str, ec: str, rsa: str) -> None:
        self.files[lockpins.GITHUB_DOCS.format(name="known_hosts")] = (
            f"```text\ngithub.com ssh-ed25519 {ed}\ngithub.com ecdsa-sha2-nistp256 {ec}\n"
            f"github.com ssh-rsa {rsa}\n```\n"
        ).encode()
        self.files[lockpins.GITHUB_DOCS.format(name="fingerprints")] = (
            f"* `{lockpins.ssh_fingerprint(rsa)}` (RSA)\n"
            f"* `{lockpins.ssh_fingerprint(ec)}` (ECDSA)\n"
            f"* `{lockpins.ssh_fingerprint(ed)}` (Ed25519)\n"
        ).encode()

    def set_meta(self, ed: str = ED25519, ec: str = ECDSA, rsa: str = RSA) -> None:
        self.files[lockpins.GITHUB_META] = json.dumps(
            {
                "ssh_keys": [f"ssh-ed25519 {ed}", f"ecdsa-sha2-nistp256 {ec}", f"ssh-rsa {rsa}"],
                "ssh_key_fingerprints": {
                    "SHA256_ED25519": lockpins.ssh_fingerprint(ed),
                    "SHA256_ECDSA": lockpins.ssh_fingerprint(ec),
                    "SHA256_RSA": lockpins.ssh_fingerprint(rsa),
                },
            }
        ).encode()

    def download(self, url: str) -> bytes:
        if url not in self.files:
            raise net.FetchError(f"{url}: HTTP 404")
        return self.files[url]

    def hash_into(self, url: str, digest: Any) -> None:
        if url not in self.big:
            raise net.FetchError(f"{url}: HTTP 404")
        digest.update(self.big[url])

    def registry_json(self, image: str, path: str) -> tuple[dict[str, str], Any]:
        if path in self.registry:
            assert image == lockpins.DCGM_IMAGE
            return self.registry[path]
        # Every lab image's index and platform manifests, for the sizes.
        reference = path.removeprefix("manifests/")
        for entry in LOCK["containers"]["images"].values():
            if reference == entry["digest"]:
                children = [
                    {
                        "platform": {"os": "linux", "architecture": arch},
                        "digest": child(entry, arch),
                    }
                    for arch in ("amd64", "arm64")
                ]
                return {}, {"manifests": children}
            for arch in ("amd64", "arm64"):
                if reference == child(entry, arch):
                    return {}, {"layers": [{"size": 1000}, {"size": 23 if arch == "arm64" else 0}]}
        raise net.FetchError(f"{path}: HTTP 404")

    def network(self) -> lockpins.Network:
        trusted = {ZABBLY, TAILSCALE, UBUNTU, DOCKER, CTK}

        def gpgv(keyring: bytes, signed: bytes) -> str:
            if keyring not in trusted or b"tampered" in signed:
                raise lockpins.PinError("BAD signature")
            # As gpgv: the signed block wherever it is, its text alone as the output.
            block = signed.decode().split("-----BEGIN PGP SIGNED MESSAGE-----\n", 1)[1]
            body = block.split("\n\n", 1)[1]
            return body.split("\n-----BEGIN PGP SIGNATURE-----", 1)[0] + "\n"

        def read_file(path: str) -> bytes:
            assert path == lockpins.UBUNTU_KEYRING
            return UBUNTU

        return lockpins.Network(
            download=self.download,
            hash_into=self.hash_into,
            registry_json=self.registry_json,
            keyscan=lambda _host: self.keyscan_output,
            gpgv=gpgv,
            read_file=read_file,
            extract=extract,
        )


def child(entry: Mapping[str, Any], arch: str) -> str:
    """The fake digest of one platform's manifest of a lab image."""
    return "sha256:" + hashlib.sha256(f"{entry['digest']}/{arch}".encode()).hexdigest()


def pinned_lock() -> dict[str, Any]:
    """The lock with the fake world's keys and its newest versions pinned."""
    lock = copy.deepcopy(LOCK)
    keys = lock["repository_keys"]["keys"]
    keys["docker-deb"]["fingerprint"] = openpgp.primary_fingerprints(DOCKER)[0]
    keys["nvidia-container-toolkit"]["fingerprint"] = openpgp.primary_fingerprints(CTK)[0]
    pins = lock["packages"]
    for release in ("ubuntu-26.04", "ubuntu-24.04", "ubuntu-22.04", "debian-13"):
        pins["docker"][release] = dict.fromkeys(DOCKER_PACKAGES, "2")
        pins["nvidia_container_toolkit"][release] = dict.fromkeys(CTK_PACKAGES, "1.20.1-1")
        pins["incus"][release] = {"incus": "1:6.0.6-x-202603272003"}
        pins["tailscale"][release] = {"tailscale": "1.104.1"}
    return lock


def run(world: World, lock: Mapping[str, Any] | None = None) -> tuple[str, list[str]]:
    return lockpins.run(lock or pinned_lock(), SOURCES, FACTS, world.network(), NOW)


# --- Debian versions ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("1.0", "1.0", 0),
        ("1:1.0", "2.0", 1),  # the epoch wins
        ("1.0~rc1", "1.0", -1),  # a tilde sorts before everything
        ("1.0-1", "1.0-2", -1),
        ("1.10", "1.9", 1),  # numbers compare as numbers
        ("1.0a", "1.0", 1),
        ("1.0+b1", "1.0a", 1),  # letters sort before other characters
        ("1:6.0.6-ubuntu24.04-202603272003", "1:6.0.6-ubuntu24.04-202603151019", 1),
        ("1.104.1", "1.104.0", 1),
    ],
)
def test_debian_versions_compare_as_dpkg_does(left: str, right: str, expected: int) -> None:
    assert lockpins.compare_versions(left, right) == expected
    assert lockpins.compare_versions(right, left) == -expected


# --- repository keys ----------------------------------------------------------------------------


def test_a_key_is_pinned_from_every_url_with_its_sources() -> None:
    pin = lockpins.pin_key(World().network(), "zabbly")
    assert pin.fingerprint == ZABBLY_FP
    assert pin.urls == lockpins.KEY_URLS["zabbly"]
    assert any("README.md publishes the same fingerprint" in s for s in pin.sources)
    assert any("keyserver.ubuntu.com holds a key" in s for s in pin.sources)
    assert pin.keyring == ZABBLY  # dearmored, for gpgv


def test_key_files_that_differ_are_refused() -> None:
    world = World()
    world.files[lockpins.KEY_URLS["tailscale"][-1]] = armored(ZABBLY)
    with pytest.raises(lockpins.PinError, match="the key files differ"):
        lockpins.pin_key(world.network(), "tailscale")


def test_a_published_fingerprint_that_differs_is_refused() -> None:
    world = World()
    world.files[lockpins.FINGERPRINT_PAGES["zabbly"][0]] = grouped(TAILSCALE_FP).encode()
    with pytest.raises(lockpins.PinError, match="publishes"):
        lockpins.pin_key(world.network(), "zabbly")


def test_a_keyserver_with_another_key_is_refused_and_an_absent_one_is_reported() -> None:
    world = World()
    world.files[lockpins.KEYSERVER.format(fingerprint=TAILSCALE_FP)] = armored(ZABBLY)
    with pytest.raises(lockpins.PinError, match=r"keyserver\.ubuntu\.com returns"):
        lockpins.pin_key(world.network(), "tailscale")
    del world.files[lockpins.KEYSERVER.format(fingerprint=TAILSCALE_FP)]
    pin = lockpins.pin_key(world.network(), "tailscale")
    assert any(s.startswith("not cross-checked with keyserver.ubuntu.com") for s in pin.sources)
    assert any("no publication of the fingerprint" in s for s in pin.sources)


# --- packages -----------------------------------------------------------------------------------


def sources_of(component: str, release: str) -> lockpins.AptSource:
    return next(
        s
        for s in lockpins.apt_sources(LOCK, SOURCES)
        if s.component == component and s.release == release
    )


def test_the_apt_releases_come_from_the_lock_and_the_component_sources() -> None:
    found = {(s.component, s.release, s.repo_name) for s in lockpins.apt_sources(LOCK, SOURCES)}
    releases = ("ubuntu-26.04", "ubuntu-24.04", "ubuntu-22.04", "debian-13")
    assert found == {
        (component, r, repo)
        for component, repo in (
            ("docker", "docker-deb"),
            ("nvidia_container_toolkit", "nvidia-container-toolkit-deb"),
            ("incus", "zabbly-incus-lts"),
            ("tailscale", "tailscale-deb"),
        )
        for r in releases
    }


def test_the_newest_version_of_each_architecture_is_read_from_the_signed_index() -> None:
    world = World()
    key = lockpins.pin_key(world.network(), "zabbly")
    pin = lockpins.pin_packages(world.network(), sources_of("incus", "ubuntu-24.04"), "noble", key)
    assert pin.versions == {
        "x86_64": {"incus": "1:6.0.6-x-202603272003"},
        "aarch64": {"incus": "1:6.0.6-x-202603272249"},
    }
    assert pin.index[0].startswith(
        "https://pkgs.zabbly.com/incus/lts-6.0/dists/noble/main/binary-amd64/Packages.gz sha256 "
    )


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("signature", "BAD signature"),
        ("index", "InRelease says"),
        ("component", "names no component 'main'"),
        ("package", "has no package incus"),
        ("injected", "InRelease says"),
    ],
)
def test_a_repository_that_does_not_check_out_is_refused(change: str, message: str) -> None:
    world = World()
    base = "https://pkgs.zabbly.com/incus/lts-6.0/dists/noble"
    if change == "signature":
        world.files[f"{base}/InRelease"] += b"tampered"
    elif change == "index":
        world.files[f"{base}/main/binary-amd64/Packages.gz"] = packages_index({"incus": ["1"]})
    elif change == "component":
        files = {
            p: world.files[f"{base}/{p}"]
            for p in ("main/binary-amd64/Packages.gz", "main/binary-arm64/Packages.gz")
        }
        world.files[f"{base}/InRelease"] = in_release(files, components="contrib")
    elif change == "injected":
        # Unsigned text before the signed block, with a hash of its own for
        # another index: what gpgv verified decides, so the index is refused.
        evil = packages_index({"incus": ["9:evil"]})
        world.files[f"{base}/main/binary-amd64/Packages.gz"] = evil
        world.files[f"{base}/InRelease"] = (
            in_release({"main/binary-amd64/Packages.gz": evil}).split(b"-----BEGIN PGP SIGNED")[0]
            + b"Components: main\nSHA256:\n "
            + hashlib.sha256(evil).hexdigest().encode()
            + b" 1 main/binary-amd64/Packages.gz\n\n"
            + world.files[f"{base}/InRelease"]
        )
    else:
        world.files.update(apt_repo(base, {a: {"other": ["1"]} for a in ("amd64", "arm64")}))
    key = lockpins.pin_key(world.network(), "zabbly")
    with pytest.raises(lockpins.PinError, match=message):
        lockpins.pin_packages(world.network(), sources_of("incus", "ubuntu-24.04"), "noble", key)


# --- the DCGM exporter, the base images, GitHub's keys ----------------------------------------


def test_the_newest_dcgm_release_tag_is_pinned_and_cross_checked() -> None:
    pin = lockpins.pin_dcgm(World().network())
    assert (pin.tag, pin.digest) == ("4.8.10", "sha256:" + "a" * 64)
    assert pin.platforms == ["linux/amd64", "linux/arm64"]
    assert "DCGM_VERSION=4.6.1" in pin.notes[0]


def test_a_dcgm_release_whose_source_names_another_version_is_refused() -> None:
    world = World()
    world.files[lockpins.DCGM_VERSIONS.format(tag="4.8.10")] = b"EXPORTER_VERSION=4.8.9\n"
    with pytest.raises(lockpins.PinError, match=r"EXPORTER_VERSION 4\.8\.9"):
        lockpins.pin_dcgm(world.network())


def test_a_base_image_fingerprint_is_computed_from_the_downloads() -> None:
    world = World()
    entry = LOCK["incus_base_images"]["images"]["ubuntu-24.04"]
    pin = lockpins.pin_base_image(world.network(), "ubuntu-24.04", entry)
    assert pin.fingerprints == {
        "x86_64": hashlib.sha256(b"meta-x86_64root-x86_64").hexdigest(),
        "aarch64": hashlib.sha256(b"meta-aarch64root-aarch64").hexdigest(),
    }
    assert any("keeps only its newest builds" in n for n in pin.notes)
    world.big[
        next(u for u in world.big if u.endswith("arm64/cloud/20261016_07:42/rootfs.squashfs"))
    ] = b"x"
    with pytest.raises(lockpins.PinError, match="the index says"):
        lockpins.pin_base_image(world.network(), "ubuntu-24.04", entry)


def test_github_keys_need_two_agreeing_sources() -> None:
    world = World()
    with pytest.raises(lockpins.PinError, match="at least 2 agreeing sources, 1 reachable"):
        lockpins.pin_github_keys(world.network())
    world.set_meta()
    keys, lines = lockpins.pin_github_keys(world.network())
    assert keys == {"ssh-ed25519": ED25519, "ecdsa-sha2-nistp256": ECDSA, "ssh-rsa": RSA}
    assert "not reachable: the server (ssh-keyscan github.com): no keys" in lines
    world.keyscan_output = f"github.com ssh-ed25519 {ED25519}\ngithub.com ssh-rsa {RSA}\n"
    with pytest.raises(lockpins.PinError, match="ecdsa-sha2-nistp256: the server"):
        lockpins.pin_github_keys(world.network())


def test_github_keys_whose_published_fingerprint_differs_are_refused() -> None:
    world = World()
    world.set_meta()
    meta = json.loads(world.files[lockpins.GITHUB_META])
    meta["ssh_key_fingerprints"]["SHA256_RSA"] = lockpins.ssh_fingerprint(ED25519)
    world.files[lockpins.GITHUB_META] = json.dumps(meta).encode()
    with pytest.raises(lockpins.PinError, match="published fingerprint of ssh-rsa"):
        lockpins.pin_github_keys(world.network())


# --- the GPU list of the NVIDIA driver ---------------------------------------------------------


def test_the_gpu_list_is_pinned_from_the_signed_index_and_cross_checked() -> None:
    (pin,) = lockpins.pin_nvidia_gpus(World().network(), LOCK, UBUNTU_CODENAMES)
    assert pin.version == DRIVER_VERSION
    assert pin.gpus.open_module == ("1E04", "2786")  # neither Pascal nor Kepler
    assert pin.gpus.current == ("1D01", "1E04", "2786")  # Kepler needs the legacy branch
    assert pin.sha256 == hashlib.sha256(SUPPORTED_GPUS).hexdigest()
    assert pin.source.startswith(f"{lockpins.UBUNTU_ARCHIVE}/pool/restricted/")
    assert "noble-updates restricted: InRelease signed" in pin.notes[0]
    assert "equal NVIDIA's open-gpu-kernel-modules README" in pin.notes[1]
    assert pin.notes[2].startswith("the same file in ubuntu-22.04")
    entry = yaml.safe_load("\n".join(lockpins.render_gpu_pins([pin])))[DRIVER_VERSION]
    assert entry["open_module"] == ["1E04", "2786"]
    assert nvidia_gpus.verify_entry(DRIVER_VERSION, entry, World().download, extract) == []


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("open-modaliases", "the Modaliases of the open driver package differs: missing 2786"),
        ("readme", "NVIDIA's README table differs: missing 2786"),
        ("readme-turing", "no longer states which GPUs"),
        ("package", "the index says otherwise"),
        ("signature", "BAD signature"),
        ("injected", "InRelease says"),
    ],
)
def test_a_gpu_list_that_does_not_check_out_is_refused(change: str, message: str) -> None:
    world = World()
    readme = nvidia_gpus.README_URL.format(version=DRIVER_VERSION)
    noble = f"{lockpins.UBUNTU_ARCHIVE}/dists/noble-updates/InRelease"
    if change == "open-modaliases":
        world.files.update(ubuntu_restricted("ubuntu-24.04", "noble", open_ids=("1E04",)))
    elif change == "readme":
        world.files[readme] = README.replace("| NVIDIA GeForce RTX 4070 |", "|").encode()
    elif change == "readme-turing":
        world.files[readme] = README.replace("Turing", "Volta").encode()
    elif change == "package":
        deb = next(u for u in world.files if u.endswith(".deb") and "24.04" in u)
        world.files[deb] += b"tampered"
    elif change == "injected":
        # Unsigned text before the signed block, stating another index's hash.
        index = noble.replace("InRelease", "restricted/binary-amd64/Packages.gz")
        evil = gzip.compress(b"Package: nvidia-driver-580-open\nVersion: 9:evil\n", mtime=0)
        world.files[index] = evil
        world.files[noble] = (
            b"SHA256:\n "
            + hashlib.sha256(evil).hexdigest().encode()
            + b" 1 restricted/binary-amd64/Packages.gz\n\n"
            + world.files[noble]
        )
    else:
        world.files[noble] += b"tampered"
    with pytest.raises(lockpins.PinError, match=message):
        lockpins.pin_nvidia_gpus(world.network(), LOCK, UBUNTU_CODENAMES)


def test_the_gpu_list_is_read_as_nvidia_documents_it() -> None:
    mixed = {"chips": [{"devid": "0x1E04", "features": ["kernelopen"]}, {"devid": "0x1E04"}]}
    with pytest.raises(nvidia_gpus.GpuListError, match="open-module and other entries: 1E04"):
        nvidia_gpus.parse_supported_gpus(json.dumps(mixed).encode())
    with pytest.raises(nvidia_gpus.GpuListError, match="bad devid"):
        nvidia_gpus.parse_supported_gpus(b'{"chips": [{"devid": "1E04"}]}')
    with pytest.raises(nvidia_gpus.GpuListError, match=r"not a supported-gpus\.json"):
        nvidia_gpus.parse_supported_gpus(b"[]")
    assert nvidia_gpus.upstream_version("1:580.178.04-0ubuntu0.24.04.1") == "580.178.04"
    assert nvidia_gpus.branch_of("nvidia-driver-580-open") == "580"
    assert nvidia_gpus.branch_of("nvidia-driver-580") is None


def test_lock_check_recomputes_the_pinned_list() -> None:
    (pin,) = lockpins.pin_nvidia_gpus(World().network(), LOCK, UBUNTU_CODENAMES)
    entry = yaml.safe_load("\n".join(lockpins.render_gpu_pins([pin])))[DRIVER_VERSION]
    files = World().files
    assert nvidia_gpus.verify_entry(DRIVER_VERSION, entry, files.__getitem__, extract) == []
    assert entry["proprietary_only"] == ["1D01"]
    changed = {**entry, "open_module": ["1E04"], "proprietary_only": []}
    assert nvidia_gpus.verify_entry(DRIVER_VERSION, changed, files.__getitem__, extract) == [
        f"the open_module list of {DRIVER_VERSION} differs from the pinned list",
        f"the proprietary_only list of {DRIVER_VERSION} differs from the pinned list",
    ]
    files[entry["source"]] += b"tampered"
    (problem,) = nvidia_gpus.verify_entry(DRIVER_VERSION, entry, files.__getitem__, extract)
    assert "has sha256" in problem


# --- the whole run ------------------------------------------------------------------------------


def test_the_run_prints_every_section_in_the_lock_format() -> None:
    world = World()
    world.set_meta()
    text, failed = run(world)
    assert failed == []
    blocks = {}
    for block in text.split("# --- ")[1:]:
        header, _, body = block.partition("\n")
        blocks[header.split(" ")[0]] = yaml.safe_load(body)
    assert blocks["repository_keys.keys"]["zabbly"] == {
        "urls": list(lockpins.KEY_URLS["zabbly"]),
        "fingerprint": ZABBLY_FP,
    }
    assert blocks["packages.incus"]["ubuntu-24.04"] == {"incus": "1:6.0.6-x-202603272003"}
    assert blocks["packages.tailscale"]["debian-13"] == {"tailscale": "1.104.1"}
    assert blocks["containers.images.dcgm_exporter"]["dcgm_exporter"]["tag"] == "4.8.10"
    sizes = blocks["containers.images.<name>.sizes"]
    assert set(sizes) == set(LOCK["containers"]["images"])
    assert sizes["vllm"] == {"sizes": {"linux/amd64": 1000, "linux/arm64": 1023}}
    assert set(
        blocks["incus_base_images.images.<name>.fingerprints"]["ubuntu-24.04"]["fingerprints"]
    ) == {
        "x86_64",
        "aarch64",
    }
    assert set(blocks["github_ssh_host_keys.keys"]) == set(lockpins.SSH_KEY_TYPES)
    assert blocks["nvidia_driver_gpus.releases"][DRIVER_VERSION]["open_module"] == ["1E04", "2786"]
    assert "aarch64 hosts cannot install it" in text  # Zabbly's builds differ per architecture
    assert text.startswith("# make lock-pins 2026-10-17T12:00:00Z")


def test_a_section_that_cannot_be_pinned_is_named_and_the_rest_is_printed() -> None:
    world = World()  # no meta API, no ssh-keyscan
    del world.files[lockpins.KEY_URLS["zabbly"][0]]
    text, failed = run(world)
    assert [f.split(":")[0] for f in failed] == [
        "repository_keys.keys.zabbly",
        "packages.incus.ubuntu-24.04",
        "packages.incus.ubuntu-26.04",
        "packages.incus.ubuntu-22.04",
        "packages.incus.debian-13",
        "github_ssh_host_keys",
    ]
    assert "# zabbly: NOT PINNED" in text
    assert 'ubuntu-24.04: {tailscale: "1.104.1"}' in text
    assert "# 6 section(s) not pinned" in text


# --- gpgv, for real ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("gpg") is None or shutil.which("gpgv") is None, reason="needs gpg")
def test_the_signature_check_runs_gpgv_with_only_the_pinned_key(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    env = {**os.environ, "GNUPGHOME": str(tmp_path)}
    tmp_path.chmod(0o700)
    # gpg starts an agent for the key; stop it with the test.
    request.addfinalizer(
        lambda: subprocess.run(["gpgconf", "--kill", "gpg-agent"], env=env, check=False)  # noqa: S607
    )

    def gpg(*args: str, data: bytes | None = None) -> bytes:
        return subprocess.run(  # noqa: S603 - fixed arguments
            ["gpg", "--batch", "--pinentry-mode", "loopback", "--passphrase", "", *args],  # noqa: S607
            input=data,
            capture_output=True,
            check=True,
            env=env,
        ).stdout

    gpg("--quick-gen-key", "Test Repository <repo@example.com>", "ed25519", "sign", "never")
    signed = gpg("--clearsign", data=b"Suite: test\n")
    keyring = gpg("--export")
    assert lockpins._gpgv(keyring, signed) == "Suite: test\n"
    # Unsigned text before the signed block is not in what is read.
    assert lockpins._gpgv(keyring, b"Suite: evil\n\n" + signed) == "Suite: test\n"
    assert lockpins._gpgv(keyring, signed + b"Suite: evil\n") == "Suite: test\n"
    with pytest.raises(lockpins.PinError, match="does not verify"):
        lockpins._gpgv(keyring, signed.replace(b"Suite: test", b"Suite: evil"))
    other = public_key_packet(b"another key")
    with pytest.raises(lockpins.PinError, match="does not verify"):
        lockpins._gpgv(other, signed)


# --- the target ---------------------------------------------------------------------------------


def test_make_lock_pins_runs_the_tool_and_is_documented() -> None:
    makefile = (ROOT / "Makefile").read_text()
    assert "\nlock-pins: $(VENV)/.synced ## Maintainers only:" in makefile
    assert "$(LOCKCHECK).lockpins" in makefile
    page = (ROOT / "docs" / "how-to" / "pin-vendor-sources.md").read_text()
    assert "make lock-pins" in page
    # The wheel shortcut of one development environment is no documented path.
    assert "ansible-14.4.0-py3-none-any.whl" not in page


# --- every apt pin: Docker, the Toolkit's flat repository, the driver, pins gone ---------------


def blocks_of(text: str) -> dict[str, Any]:
    out = {}
    for block in text.split("# --- ")[1:]:
        header, _, body = block.partition("\n")
        out[header.split(" ")[0]] = yaml.safe_load(body)
    return out


def test_docker_the_toolkit_and_the_driver_are_pinned_from_their_signed_indexes() -> None:
    world = World()
    world.set_meta()
    text, failed = run(world)
    assert failed == []
    blocks = blocks_of(text)
    assert blocks["packages.docker"]["ubuntu-26.04"] == dict.fromkeys(DOCKER_PACKAGES, "2")
    assert blocks["packages.docker"]["debian-13"] == dict.fromkeys(DOCKER_PACKAGES, "2")
    assert blocks["packages.nvidia_container_toolkit"]["ubuntu-24.04"] == dict.fromkeys(
        CTK_PACKAGES, "1.20.1-1"
    )
    # The flat repository: one release file per architecture, its URI naming it.
    for arch in ("amd64", "arm64"):
        base = f"https://nvidia.github.io/libnvidia-container/stable/deb/{arch}"
        assert f"{base}/Packages.gz sha256 " in text
    # The driver: the newest version in Ubuntu's restricted pocket, -updates first.
    driver = blocks["packages.nvidia_driver"]
    assert set(driver) == set(UBUNTU_CODENAMES)
    for release, pinned in DRIVER.items():
        assert driver[release] == pinned
    assert "resolute-security restricted: InRelease signed by the Ubuntu archive keyring" in text


def test_a_pinned_version_gone_from_its_index_is_reported() -> None:
    world = World()
    world.set_meta()
    lock = pinned_lock()
    lock["packages"]["incus"]["ubuntu-24.04"] = {"incus": "1:6.0.5-x-old"}
    lock["packages"]["nvidia_driver"]["ubuntu-26.04"] = {"nvidia-driver-580-open": "570.1-0"}
    text, failed = run(world, lock)
    gone = [f for f in failed if "no longer in its index" in f]
    assert gone == [
        "packages.incus.ubuntu-24.04: the pinned incus 1:6.0.5-x-old is no longer in its index;"
        " apt cannot install it",
        "packages.nvidia_driver.ubuntu-26.04: the pinned nvidia-driver-580-open 570.1-0 is no"
        " longer in its index; apt cannot install it",
    ]
    assert "# NOT IN THE INDEX: packages.incus.ubuntu-24.04" in text
    # Counted on their own; the one section not pinned is the GPU list of the
    # driver version the lock now names.
    assert "# 2 pinned version(s) not in their index; see the NOT IN THE INDEX lines" in text
    assert "# 1 section(s) not pinned; see the NOT PINNED lines" in text
    assert [f.split(":")[0] for f in failed if lockpins.GONE not in f] == ["nvidia_driver_gpus"]
    # The new pin is still printed for the maintainer to paste.
    assert blocks_of(text)["packages.incus"]["ubuntu-24.04"] == {"incus": "1:6.0.6-x-202603272003"}


def test_a_marked_pin_and_a_package_outside_the_layout_are_not_reported_gone() -> None:
    world = World()
    world.set_meta()
    # Zabbly's noble index also lists a package the layout does not install.
    world.files.update(
        apt_repo(
            "https://pkgs.zabbly.com/incus/lts-6.0/dists/noble",
            {
                arch: {"incus": ["1:6.0.6-x-202603272003"], "incus-extra": ["7"]}
                for arch in ("amd64", "arm64")
            },
        )
    )
    lock = pinned_lock()
    lock["packages"]["incus"]["ubuntu-24.04"] = {
        "incus": "1:6.0.6-x-202603272003",
        "incus-extra": "7",
    }
    lock["packages"]["docker"]["ubuntu-26.04"]["docker-ce"] = "TODO(verify)"
    text, failed = run(world, lock)
    assert failed == []
    assert "NOT IN THE INDEX" not in text


def restricted_suite(world: World, suite: str, stanzas: str) -> None:
    """Replace a suite's signed restricted index of Ubuntu's archive."""
    index = gzip.compress(stanzas.encode(), mtime=0)
    base = f"{lockpins.UBUNTU_ARCHIVE}/dists/{suite}"
    world.files[f"{base}/restricted/binary-amd64/Packages.gz"] = index
    world.files[f"{base}/InRelease"] = in_release(
        {"restricted/binary-amd64/Packages.gz": index}, components="main restricted"
    )


def test_the_driver_is_the_newest_over_its_three_suites() -> None:
    (pinned,) = DRIVER["ubuntu-26.04"].values()
    for suite in ("resolute-security", "resolute"):
        world = World()
        world.set_meta()
        restricted_suite(world, suite, "Package: nvidia-driver-580-open\nVersion: 580.999-0\n")
        text, failed = run(world)
        driver = blocks_of(text)["packages.nvidia_driver"]["ubuntu-26.04"]
        assert driver == {"nvidia-driver-580-open": "580.999-0"}, suite
        # Still listed: the lock's version is not gone.
        assert failed == [], suite
        assert pinned != "580.999-0"


def test_a_driver_package_in_no_suite_or_a_suite_that_fails_is_not_pinned() -> None:
    world = World()
    world.set_meta()
    lock = pinned_lock()
    lock["packages"]["nvidia_driver"]["ubuntu-22.04"] = {"nvidia-driver-999-open": "1"}
    # resolute-security's index no longer matches the hash its release file states.
    security = (
        f"{lockpins.UBUNTU_ARCHIVE}/dists/resolute-security/restricted/binary-amd64/Packages.gz"
    )
    world.files[security] = gzip.compress(b"Package: other\nVersion: 1\n", mtime=0)
    text, failed = run(world, lock)
    driver = [f for f in failed if f.startswith("packages.nvidia_driver.")]
    # In the lock's order: 24.04, 22.04, 26.04.
    assert [f.split(":")[0] for f in driver] == [
        "packages.nvidia_driver.ubuntu-22.04",
        "packages.nvidia_driver.ubuntu-26.04",
    ]
    assert "resolute-security/restricted/binary-amd64/Packages.gz has SHA256" in driver[1]
    assert any("has no package nvidia-driver-999-open" in f for f in driver)
    assert "# ubuntu-26.04: NOT PINNED:" in text
    assert "NOT IN THE INDEX" not in text


def test_the_tools_key_urls_are_the_locks() -> None:
    for name, urls in lockpins.KEY_URLS.items():
        assert list(urls) == LOCK["repository_keys"]["keys"][name]["urls"], name


def test_a_key_the_lock_does_not_pin_is_not_pinned_and_named() -> None:
    world = World()
    with pytest.raises(lockpins.PinError, match="the lock pins no key nope with a URL"):
        lockpins.lock_key(world.network(), LOCK, "nope")


def test_a_vendor_key_is_the_lock_s_own_and_its_fingerprint_is_checked() -> None:
    world = World()
    world.set_meta()
    world.files[LOCK["repository_keys"]["keys"]["docker-deb"]["urls"][0]] = armored(TAILSCALE)
    text, failed = run(world)
    docker = [f for f in failed if f.startswith("packages.docker.")]
    assert len(docker) == 4
    assert f"holds {TAILSCALE_FP}, the lock pins " in docker[0]
    assert "# ubuntu-26.04: NOT PINNED:" in text


def test_every_component_a_repository_names_is_read() -> None:
    world = World()
    base = "https://pkgs.zabbly.com/incus/lts-6.0/dists/noble"
    world.files.update(
        {
            f"{base}/main/binary-{arch}/Packages.gz": packages_index({"incus": ["1"]})
            for arch in ("amd64", "arm64")
        }
    )
    world.files.update(
        {
            f"{base}/extra/binary-{arch}/Packages.gz": packages_index({"incus-ui": ["2"]})
            for arch in ("amd64", "arm64")
        }
    )
    files = {
        p: world.files[f"{base}/{p}"]
        for p in (
            "main/binary-amd64/Packages.gz",
            "main/binary-arm64/Packages.gz",
            "extra/binary-amd64/Packages.gz",
            "extra/binary-arm64/Packages.gz",
        )
    }
    world.files[f"{base}/InRelease"] = in_release(files, components="main extra")
    source = sources_of("incus", "ubuntu-24.04")
    two = lockpins.AptSource(
        source.component,
        source.release,
        source.repo_name,
        {**source.repo, "components": ["main", "extra"]},
        ("incus", "incus-ui"),
    )
    key = lockpins.pin_key(world.network(), "zabbly")
    pin = lockpins.pin_packages(world.network(), two, "noble", key)
    assert pin.versions["x86_64"] == {"incus": "1", "incus-ui": "2"}
    assert len(pin.index) == 4
