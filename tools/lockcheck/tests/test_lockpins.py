"""make lock-pins: the vendor sources item 0 needs, checked and printed (no network)."""

from __future__ import annotations

import base64
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

from pinka_lockcheck import lockpins, net, openpgp

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
    base: str, versions: Mapping[str, Mapping[str, list[str]]], **kwargs: str
) -> dict[str, bytes]:
    files = {f"main/binary-{arch}/Packages.gz": packages_index(versions[arch]) for arch in versions}
    out = {f"{base}/{path}": data for path, data in files.items()}
    out[f"{base}/InRelease"] = in_release(files, **kwargs)
    return out


SOURCES: dict[str, Any] = yaml.safe_load((ROOT / "ansible" / "component-sources.yml").read_text())
FACTS: dict[str, Any] = yaml.safe_load(
    (ROOT / "ansible" / "roles" / "platform_facts" / "vars" / "debian.yml").read_text()
)
LOCK: dict[str, Any] = yaml.safe_load((ROOT / "versions.lock.yml").read_text())


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
        codenames = {"ubuntu-24.04": "noble", "ubuntu-22.04": "jammy", "debian-13": "trixie"}
        for release, codename in codenames.items():
            distro = release.split("-")[0]
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
        assert image == lockpins.DCGM_IMAGE
        if path not in self.registry:
            raise net.FetchError(f"{path}: HTTP 404")
        return self.registry[path]

    def network(self) -> lockpins.Network:
        trusted = {ZABBLY, TAILSCALE}

        def gpgv(keyring: bytes, signed: bytes) -> None:
            if keyring not in trusted or b"tampered" in signed:
                raise lockpins.PinError("BAD signature")

        return lockpins.Network(
            download=self.download,
            hash_into=self.hash_into,
            registry_json=self.registry_json,
            keyscan=lambda _host: self.keyscan_output,
            gpgv=gpgv,
        )


def run(world: World) -> tuple[str, list[str]]:
    return lockpins.run(LOCK, SOURCES, FACTS, world.network(), NOW)


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
    assert found == {
        ("incus", r, "zabbly-incus-lts") for r in ("ubuntu-24.04", "ubuntu-22.04", "debian-13")
    } | {("tailscale", r, "tailscale-deb") for r in ("ubuntu-24.04", "ubuntu-22.04", "debian-13")}


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
    assert set(
        blocks["incus_base_images.images.<name>.fingerprints"]["ubuntu-24.04"]["fingerprints"]
    ) == {
        "x86_64",
        "aarch64",
    }
    assert set(blocks["github_ssh_host_keys.keys"]) == set(lockpins.SSH_KEY_TYPES)
    assert "aarch64 hosts cannot install it" in text  # Zabbly's builds differ per architecture
    assert text.startswith("# make lock-pins 2026-10-17T12:00:00Z")


def test_a_section_that_cannot_be_pinned_is_named_and_the_rest_is_printed() -> None:
    world = World()  # no meta API, no ssh-keyscan
    del world.files[lockpins.KEY_URLS["zabbly"][0]]
    text, failed = run(world)
    assert [f.split(":")[0] for f in failed] == [
        "repository_keys.keys.zabbly",
        "packages.incus.ubuntu-24.04",
        "packages.incus.ubuntu-22.04",
        "packages.incus.debian-13",
        "github_ssh_host_keys",
    ]
    assert "# zabbly: NOT PINNED" in text
    assert 'ubuntu-24.04: {tailscale: "1.104.1"}' in text
    assert "# 5 section(s) not pinned" in text


# --- gpgv, for real ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("gpg") is None or shutil.which("gpgv") is None, reason="needs gpg")
def test_the_signature_check_runs_gpgv_with_only_the_pinned_key(tmp_path: Path) -> None:
    env = {**os.environ, "GNUPGHOME": str(tmp_path)}
    tmp_path.chmod(0o700)

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
    lockpins._gpgv(keyring, signed)
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
