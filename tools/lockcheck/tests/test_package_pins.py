"""make lock-check: every apt package pin is still in its signed index (no network)."""

from __future__ import annotations

import gzip
import hashlib
import lzma
import os
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml
from retry_testlib import quick

from pinka_lockcheck import aptindex, checks, cli, net, openpgp

ROOT = Path(__file__).resolve().parents[3]
ZABBLY_URI = "https://pkgs.zabbly.example/incus/lts-6.0"
CTK_URI = "https://ctk.example/stable/deb/{deb_arch}"
ARCHIVE = checks.DISTRO_ARCHIVES["ubuntu"]


def public_key_packet(seed: bytes) -> bytes:
    """A version 4 public key packet (tag 6) with a body derived from seed."""
    body = b"\x04" + b"\x00\x00\x00\x01" + b"\x16" + hashlib.sha256(seed).digest()
    return bytes([0x80 | (6 << 2), len(body)]) + body


ZABBLY = public_key_packet(b"zabbly")
CTK = public_key_packet(b"ctk")
UBUNTU = public_key_packet(b"ubuntu archive")
OTHER = public_key_packet(b"someone else")


def fp(key: bytes) -> str:
    return openpgp.primary_fingerprints(key)[0]


def packages(versions: Mapping[str, list[str]]) -> bytes:
    return "\n".join(
        f"Package: {name}\nVersion: {v}\nArchitecture: amd64\n"
        for name, listed in versions.items()
        for v in listed
    ).encode()


class Repo:
    """A fake network of signed apt repositories.

    Every release file names the key it is signed with; ``verify`` accepts it
    only with that key, as gpgv does.
    """

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.fetched: list[str] = []

    def add(
        self,
        key: bytes,
        base: str,
        indexes: Mapping[str, Mapping[str, list[str]]],
        *,
        hashes: tuple[str, ...] = ("SHA256",),
        components: str = "",
        name: str = "Packages.gz",
    ) -> None:
        """A release file at ``base`` and one index per prefix (``main/binary-amd64/``)."""
        lines: dict[str, list[str]] = {h: [] for h in hashes}
        for prefix, versions in indexes.items():
            raw = packages(versions)
            data = gzip.compress(raw) if name.endswith(".gz") else raw
            if name.endswith(".xz"):
                data = lzma.compress(raw)
            path = f"{prefix}{name}"
            self.files[f"{base}/{path}"] = data
            for field in hashes:
                digest = getattr(hashlib, field.lower())(data).hexdigest()
                lines[field].append(f" {digest} {len(data)} {path}")
        body = f"Origin: test\nX-Test-Key: {fp(key)}\n"
        if components:
            body += f"Components: {components}\n"
        for field in hashes:
            body += f"{field}:\n" + "\n".join(lines[field]) + "\n"
        signed = (
            "-----BEGIN PGP SIGNED MESSAGE-----\nHash: SHA512\n\n"
            f"{body}\n-----BEGIN PGP SIGNATURE-----\nnot a signature\n"
            "-----END PGP SIGNATURE-----\n"
        )
        self.files[f"{base}/InRelease"] = signed.encode()

    def fetch(self, url: str) -> bytes:
        self.fetched.append(url)
        if url not in self.files:
            raise net.FetchError(f"{url}: HTTP 404")
        return self.files[url]

    @staticmethod
    def verify(keyring: bytes, signed: bytes) -> str:
        """As gpgv: the signed block wherever it is, its text as the output."""
        assert not keyring.lstrip().startswith(b"-----"), "gpgv gets a binary keyring"
        block = signed.decode().split("-----BEGIN PGP SIGNED MESSAGE-----\n", 1)[1]
        text = block.split("\n\n", 1)[1].split("\n-----BEGIN PGP SIGNATURE-----", 1)[0]
        if f"X-Test-Key: {fp(keyring)}" not in text:
            raise aptindex.AptIndexError("the signature does not verify with the key: BAD")
        return text + "\n"


SOURCES: dict[str, Any] = {
    "repositories": {
        "zabbly": {
            "type": "apt",
            "key": "zabbly",
            "uri": ZABBLY_URI,
            "suite": "{codename}",
            "components": ["main"],
        },
        "ctk-deb": {
            "type": "apt",
            "key": "ctk",
            "uri": CTK_URI,
            "suite": "/",
            "components": [],
        },
        "docker-rpm": {"type": "rpm", "key": "zabbly", "baseurl": "https://x.example/{major}"},
    },
    "components": {
        "incus": {
            "default": {"source": "distro", "packages": ["incus"]},
            "ubuntu": {"source": "vendor_repo", "repo": "zabbly", "packages": ["incus"]},
            "debian": {"source": "vendor_repo", "repo": "zabbly", "packages": ["incus"]},
        },
        "nvidia_container_toolkit": {
            "default": {"source": "vendor_repo", "repo": "docker-rpm"},
            "ubuntu": {"source": "vendor_repo", "repo": "ctk-deb"},
        },
        "nvidia_driver": {
            "default": {"source": "distro", "packages": []},
            "ubuntu": {"source": "distro", "packages": ["nvidia-driver-580-open"]},
            "debian": {"source": "community", "packages": ["nvidia-driver"]},
        },
    },
}
CODENAMES = {"ubuntu-24.04": "noble", "debian-13": "trixie"}


def doc(packages_section: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "repository_keys": {
            "keys": {
                "zabbly": {"urls": ["https://key.example/zabbly.gpg"], "fingerprint": fp(ZABBLY)},
                "ctk": {"urls": ["https://key.example/ctk.gpg"], "fingerprint": fp(CTK)},
            }
        },
        "packages": {"verified_on": "2026-11-12", "source": "test", **packages_section},
    }


def network() -> Repo:
    repo = Repo()
    repo.files["https://key.example/zabbly.gpg"] = ZABBLY
    repo.files["https://key.example/ctk.gpg"] = CTK
    return repo


def run(lock: Mapping[str, Any], repo: Repo, **kwargs: Any) -> list[str]:
    kwargs.setdefault("read_file", lambda path: UBUNTU)
    kwargs.setdefault("policy", quick())
    return [
        str(f)
        for f in checks.package_findings(
            lock, SOURCES, CODENAMES, fetch=repo.fetch, verify=repo.verify, **kwargs
        )
    ]


ZABBLY_NOBLE = f"{ZABBLY_URI}/dists/noble"
NOBLE_INDEX = f"{ZABBLY_NOBLE}/main/binary-amd64/Packages.gz"


def zabbly(repo: Repo, versions: list[str], codename: str = "noble") -> None:
    repo.add(
        ZABBLY,
        f"{ZABBLY_URI}/dists/{codename}",
        {"main/binary-amd64/": {"incus": versions}},
        components="main",
    )


# --- a vendor repository ---------------------------------------------------------------------


def test_a_pin_the_index_lists_passes() -> None:
    repo = network()
    zabbly(repo, ["1:6.0.7-b1", "1:6.0.7-b2"])
    assert run(doc({"incus": {"ubuntu-24.04": {"incus": "1:6.0.7-b1"}}}), repo) == []
    assert repo.fetched == [
        "https://key.example/zabbly.gpg",
        f"{ZABBLY_NOBLE}/InRelease",
        NOBLE_INDEX,
    ]


def test_a_pin_that_left_its_index_fails_naming_the_pin_and_the_index() -> None:
    repo = network()
    zabbly(repo, ["1:6.0.7-b1", "1:6.0.7-b2"])
    assert run(doc({"incus": {"ubuntu-24.04": {"incus": "1:6.0.6-old"}}}), repo) == [
        f"error: packages.incus.ubuntu-24.04: incus 1:6.0.6-old is not in {NOBLE_INDEX}, so apt"
        " cannot install it; renew the pin (docs/how-to/update-versions.md)"
    ]


def test_every_missing_package_of_a_pin_is_named_and_the_others_are_not() -> None:
    repo = network()
    repo.add(
        ZABBLY,
        ZABBLY_NOBLE,
        {"main/binary-amd64/": {"incus": ["1"], "incus-client": ["1"]}},
        components="main",
    )
    lock = doc({"incus": {"ubuntu-24.04": {"incus": "1", "incus-client": "2", "incus-base": "1"}}})
    found = run(lock, repo)
    assert len(found) == 2
    assert "incus-client 2 is not in" in found[0]
    assert "incus-base 1 is not in" in found[1]


def test_a_version_of_another_package_does_not_count() -> None:
    repo = network()
    repo.add(
        ZABBLY, ZABBLY_NOBLE, {"main/binary-amd64/": {"incus-client": ["1"]}}, components="main"
    )
    assert run(doc({"incus": {"ubuntu-24.04": {"incus": "1"}}}), repo) == [
        f"error: packages.incus.ubuntu-24.04: incus 1 is not in {NOBLE_INDEX}, so apt cannot"
        " install it; renew the pin (docs/how-to/update-versions.md)"
    ]


def test_each_release_reads_its_own_suite_and_one_key_download_serves_all() -> None:
    repo = network()
    zabbly(repo, ["1"])
    zabbly(repo, ["2"], "trixie")
    lock = doc({"incus": {"ubuntu-24.04": {"incus": "1"}, "debian-13": {"incus": "1"}}})
    assert run(lock, repo) == [
        f"error: packages.incus.debian-13: incus 1 is not in {ZABBLY_URI}/dists/trixie/main/"
        "binary-amd64/Packages.gz, so apt cannot install it; renew the pin"
        " (docs/how-to/update-versions.md)"
    ]
    assert repo.fetched.count("https://key.example/zabbly.gpg") == 1


def test_a_flat_repository_with_sha512_only() -> None:
    repo = network()
    base = CTK_URI.format(deb_arch="amd64")
    repo.add(CTK, base, {"": {"tk": ["1.20.1-1"]}}, hashes=("SHA512",), name="Packages.xz")
    lock = doc({"nvidia_container_toolkit": {"ubuntu-24.04": {"tk": "1.20.1-1"}}})
    assert run(lock, repo) == []
    assert f"{base}/Packages.xz" in repo.fetched
    lock = doc({"nvidia_container_toolkit": {"ubuntu-24.04": {"tk": "1.20.0-1"}}})
    assert run(lock, network_with(repo)) == [
        f"error: packages.nvidia_container_toolkit.ubuntu-24.04: tk 1.20.0-1 is not in"
        f" {base}/Packages.xz, so apt cannot install it; renew the pin"
        " (docs/how-to/update-versions.md)"
    ]


def network_with(repo: Repo) -> Repo:
    fresh = Repo()
    fresh.files = dict(repo.files)
    return fresh


# --- what cannot be trusted or read ----------------------------------------------------------


def test_a_release_file_signed_with_another_key_fails() -> None:
    repo = network()
    repo.add(OTHER, ZABBLY_NOBLE, {"main/binary-amd64/": {"incus": ["1"]}}, components="main")
    assert run(doc({"incus": {"ubuntu-24.04": {"incus": "1"}}}), repo) == [
        f"error: packages.incus.ubuntu-24.04: cannot check the pin: {ZABBLY_NOBLE}/InRelease:"
        " the signature does not verify with the key: BAD"
    ]
    assert NOBLE_INDEX not in repo.fetched


def test_a_key_file_with_another_key_fails() -> None:
    repo = network()
    repo.files["https://key.example/zabbly.gpg"] = OTHER
    zabbly(repo, ["1"])
    assert run(doc({"incus": {"ubuntu-24.04": {"incus": "1"}}}), repo) == [
        "error: packages.incus.ubuntu-24.04: cannot check the pin: https://key.example/zabbly.gpg"
        f" has primary key(s) {fp(OTHER)}, pinned {fp(ZABBLY)}"
    ]


def test_an_index_whose_hash_differs_from_the_release_file_fails() -> None:
    repo = network()
    zabbly(repo, ["1"])
    repo.files[NOBLE_INDEX] = gzip.compress(packages({"incus": ["1", "evil"]}))
    found = run(doc({"incus": {"ubuntu-24.04": {"incus": "1"}}}), repo)
    assert len(found) == 1
    assert found[0].startswith(
        f"error: packages.incus.ubuntu-24.04: cannot check the pin: {NOBLE_INDEX} has SHA256 "
    )
    assert ", InRelease says " in found[0]


def test_a_release_file_without_the_component_fails() -> None:
    repo = network()
    repo.add(ZABBLY, ZABBLY_NOBLE, {"main/binary-amd64/": {"incus": ["1"]}}, components="other")
    assert run(doc({"incus": {"ubuntu-24.04": {"incus": "1"}}}), repo) == [
        "error: packages.incus.ubuntu-24.04: cannot check the pin:"
        f" {ZABBLY_NOBLE}/InRelease names no component 'main'"
    ]


def test_a_release_file_that_lists_no_index_fails() -> None:
    repo = network()
    repo.add(ZABBLY, ZABBLY_NOBLE, {"main/binary-arm64/": {"incus": ["1"]}}, components="main")
    assert run(doc({"incus": {"ubuntu-24.04": {"incus": "1"}}}), repo) == [
        "error: packages.incus.ubuntu-24.04: cannot check the pin:"
        f" {ZABBLY_NOBLE}/InRelease lists no package index in main/binary-amd64/"
    ]


def test_a_download_is_tried_again_after_a_transport_error() -> None:
    repo = network()
    zabbly(repo, ["1"])
    calls: list[str] = []

    def flaky(url: str) -> bytes:
        calls.append(url)
        if url == NOBLE_INDEX and calls.count(url) == 1:
            raise net.FetchError(f"{url}: connection reset", transient=True)
        return repo.fetch(url)

    log: list[str] = []
    found = checks.package_findings(
        doc({"incus": {"ubuntu-24.04": {"incus": "1"}}}),
        SOURCES,
        CODENAMES,
        fetch=flaky,
        verify=repo.verify,
        read_file=lambda path: UBUNTU,
        policy=quick(log),
    )
    assert found == []
    assert calls.count(NOBLE_INDEX) == 2
    assert f"download {NOBLE_INDEX}: attempt 2 of 3" in log


def test_an_index_that_cannot_be_downloaded_fails_the_pin() -> None:
    repo = network()
    zabbly(repo, ["1"])
    del repo.files[NOBLE_INDEX]
    assert run(doc({"incus": {"ubuntu-24.04": {"incus": "1"}}}), repo) == [
        f"error: packages.incus.ubuntu-24.04: cannot check the pin: {NOBLE_INDEX}: HTTP 404"
    ]


def test_a_source_without_a_known_index_fails() -> None:
    lock = doc({"nvidia_driver": {"debian-13": {"nvidia-driver": "550-1"}}})
    assert run(lock, network()) == [
        "error: packages.nvidia_driver.debian-13: cannot check the pin: no package index is known"
        " for source 'community' on debian-13 (ansible/component-sources.yml)"
    ]


def test_an_rpm_repository_for_an_apt_release_fails() -> None:
    sources = {
        **SOURCES,
        "components": {"incus": {"ubuntu": {"source": "vendor_repo", "repo": "docker-rpm"}}},
    }
    repo = network()
    found = checks.package_findings(
        doc({"incus": {"ubuntu-24.04": {"incus": "1"}}}),
        sources,
        CODENAMES,
        fetch=repo.fetch,
        verify=repo.verify,
        policy=quick(),
    )
    assert [str(f) for f in found] == [
        "error: packages.incus.ubuntu-24.04: cannot check the pin:"
        " repository docker-rpm is not an apt repository"
    ]


# --- what is not checked -----------------------------------------------------------------------


def test_unverified_values_and_other_releases_are_left_alone() -> None:
    repo = network()
    lock = doc(
        {
            "incus": {
                "ubuntu-24.04": "TODO(verify)",
                "debian-13": {"incus": "TODO(verify): which build"},
                "fedora-44": {"incus": "6.0.7-1.fc44"},
            }
        }
    )
    assert run(lock, repo) == []
    assert repo.fetched == []


def test_an_unverified_package_of_a_pin_is_skipped_and_the_others_are_checked() -> None:
    repo = network()
    zabbly(repo, ["1"])
    lock = doc({"incus": {"ubuntu-24.04": {"incus": "2", "incus-client": "TODO(verify)"}}})
    assert run(lock, repo) == [
        f"error: packages.incus.ubuntu-24.04: incus 2 is not in {NOBLE_INDEX}, so apt cannot"
        " install it; renew the pin (docs/how-to/update-versions.md)"
    ]


# --- the distribution's own archive ------------------------------------------------------------


def archive(repo: Repo, suite: str, versions: Mapping[str, Mapping[str, list[str]]]) -> None:
    repo.add(
        UBUNTU,
        f"{ARCHIVE.uri}/dists/{suite}",
        {f"{part}/binary-amd64/": v for part, v in versions.items()},
        components=" ".join(ARCHIVE.components),
        name="Packages.xz",
    )


def ubuntu_archive(repo: Repo, driver: list[str]) -> None:
    for suite in ("noble-updates", "noble-security", "noble"):
        archive(
            repo,
            suite,
            {
                "main": {"bash": ["5"]},
                "restricted": {
                    "nvidia-driver-580-open": driver if suite == "noble-updates" else []
                },
                "universe": {"incus": ["6.0.0"]},
                "multiverse": {},
            },
        )


def index_url(suite: str, part: str) -> str:
    return f"{ARCHIVE.uri}/dists/{suite}/{part}/binary-amd64/Packages.xz"


def test_a_distribution_pin_is_searched_until_found() -> None:
    repo = network()
    ubuntu_archive(repo, ["580.178.04-0ubuntu0.24.04.1"])
    lock = doc(
        {
            "nvidia_driver": {
                "ubuntu-24.04": {"nvidia-driver-580-open": "580.178.04-0ubuntu0.24.04.1"}
            }
        }
    )
    assert run(lock, repo) == []
    assert repo.fetched == [
        f"{ARCHIVE.uri}/dists/noble-updates/InRelease",
        index_url("noble-updates", "main"),
        index_url("noble-updates", "restricted"),
    ]


def test_a_distribution_pin_in_no_suite_names_every_index_read() -> None:
    repo = network()
    ubuntu_archive(repo, ["580.178.05-0ubuntu0.24.04.1"])
    lock = doc(
        {
            "nvidia_driver": {
                "ubuntu-24.04": {"nvidia-driver-580-open": "580.178.04-0ubuntu0.24.04.1"}
            }
        }
    )
    read = [
        index_url(suite, part)
        for suite in ("noble-updates", "noble-security", "noble")
        for part in ARCHIVE.components
    ]
    assert run(lock, repo) == [
        "error: packages.nvidia_driver.ubuntu-24.04: nvidia-driver-580-open"
        f" 580.178.04-0ubuntu0.24.04.1 is in none of {', '.join(read)}, so apt cannot install"
        " it; renew the pin (docs/how-to/update-versions.md)"
    ]


def test_the_archive_is_checked_with_the_machine_s_archive_keyring() -> None:
    repo = network()
    ubuntu_archive(repo, ["1"])
    lock = doc({"nvidia_driver": {"ubuntu-24.04": {"nvidia-driver-580-open": "1"}}})
    asked: list[str] = []

    def read_file(path: str) -> bytes:
        asked.append(path)
        return OTHER

    found = run(lock, repo, read_file=read_file)
    assert asked == ["/usr/share/keyrings/ubuntu-archive-keyring.gpg"]
    assert found == [
        "error: packages.nvidia_driver.ubuntu-24.04: cannot check the pin:"
        f" {ARCHIVE.uri}/dists/noble-updates/InRelease: the signature does not verify with the"
        " key: BAD"
    ]


def test_a_missing_archive_keyring_fails_the_pin() -> None:
    lock = doc({"nvidia_driver": {"ubuntu-24.04": {"nvidia-driver-580-open": "1"}}})

    def read_file(path: str) -> bytes:
        raise FileNotFoundError(path)

    found = run(lock, network(), read_file=read_file)
    assert len(found) == 1
    assert found[0].startswith(
        "error: packages.nvidia_driver.ubuntu-24.04: cannot check the pin: the archive keyring"
        " /usr/share/keyrings/ubuntu-archive-keyring.gpg cannot be read"
    )
    assert found[0].endswith("; install it (Ubuntu's package ubuntu-keyring)")


# --- the index reader --------------------------------------------------------------------------


def test_release_hash_prefers_sha256_and_reads_sha512() -> None:
    release = (
        "Origin: x\nSHA256:\n aaa 1 main/Packages\nSHA512:\n bbb 1 main/Packages\n ccc 2 Packages\n"
    )
    assert aptindex.release_hash(release, "main/Packages") == ("SHA256", "aaa")
    assert aptindex.release_hash(release, "Packages") == ("SHA512", "ccc")
    assert aptindex.release_hash(release, "other") is None


def test_only_the_text_the_signature_check_verified_is_read() -> None:
    # gpgv ignores unsigned text around the signed block; a paragraph put in
    # front of it, with a hash of its own, must not decide anything.
    repo = network()
    zabbly(repo, ["1"])
    evil = gzip.compress(packages({"incus": ["1", "6.0.6-old"]}))
    injected = (
        "Components: main\nSHA256:\n"
        f" {hashlib.sha256(evil).hexdigest()} {len(evil)} main/binary-amd64/Packages.gz\n\n"
    )
    repo.files[f"{ZABBLY_NOBLE}/InRelease"] = (
        injected.encode() + repo.files[f"{ZABBLY_NOBLE}/InRelease"] + b"Components: evil\n"
    )
    repo.files[NOBLE_INDEX] = evil
    found = run(doc({"incus": {"ubuntu-24.04": {"incus": "6.0.6-old"}}}), repo)
    assert len(found) == 1
    assert found[0].startswith(
        f"error: packages.incus.ubuntu-24.04: cannot check the pin: {NOBLE_INDEX} has SHA256 "
    )


@pytest.mark.skipif(shutil.which("gpg") is None or shutil.which("gpgv") is None, reason="needs gpg")
def test_the_real_signature_check_returns_only_the_signed_text(
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
    body = b"Suite: test\nComponents: main\nSHA256:\n good 1 main/binary-amd64/Packages\n"
    signed = gpg("--clearsign", data=body)
    keyring = gpg("--export")
    injected = b"Components: main\nSHA256:\n evil 1 main/binary-amd64/Packages\n\n"
    for data in (signed, injected + signed, signed + b"Components: evil\n"):
        served = {"https://r.example/dists/test/InRelease": data}
        text = aptindex.read_release(
            served.__getitem__,
            aptindex.verify_signature,
            keyring,
            "https://r.example/dists/test",
        )
        assert text == body.decode()
        assert aptindex.release_hash(text, "main/binary-amd64/Packages") == ("SHA256", "good")
    with pytest.raises(aptindex.AptIndexError, match="does not verify"):
        aptindex.verify_signature(keyring, signed.replace(b"good", b"evil"))
    with pytest.raises(aptindex.AptIndexError, match="does not verify"):
        aptindex.verify_signature(public_key_packet(b"another key"), signed)
    latin1 = gpg("--clearsign", data=b"Suite: \xe9\n")
    with pytest.raises(aptindex.AptIndexError, match="the signed text is not UTF-8"):
        aptindex.verify_signature(keyring, latin1)


def test_an_uncompressed_index_is_read_too() -> None:
    repo = network()
    repo.add(
        ZABBLY,
        ZABBLY_NOBLE,
        {"main/binary-amd64/": {"incus": ["1"]}},
        components="main",
        name="Packages",
    )
    assert run(doc({"incus": {"ubuntu-24.04": {"incus": "1"}}}), repo) == []
    assert repo.fetched[-1] == f"{ZABBLY_NOBLE}/main/binary-amd64/Packages"


def test_the_repository_of_each_layout() -> None:
    assert aptindex.Repository.of("https://r.example/", "noble", "main", "amd64") == (
        aptindex.Repository("https://r.example/dists/noble", "main/binary-amd64/", "main")
    )
    assert aptindex.Repository.of("https://r.example/amd64", "/", "", "amd64") == (
        aptindex.Repository("https://r.example/amd64", "")
    )


# --- the real lock and the command -------------------------------------------------------------


def real(path: str) -> Any:
    return yaml.safe_load((ROOT / path).read_text())


def test_every_verified_apt_pin_of_the_lock_has_an_index_to_check() -> None:
    lock = checks.load(ROOT / "versions.lock.yml")
    sources = real("ansible/component-sources.yml")
    codenames = checks.apt_codenames(real("ansible/roles/platform_facts/vars/debian.yml"))

    def unreachable(url: str) -> bytes:
        raise net.FetchError(f"{url}: offline")

    found = checks.package_findings(
        lock, sources, codenames, fetch=unreachable, read_file=lambda p: UBUNTU, policy=quick()
    )
    pins = {
        f"packages.{component}.{release}"
        for component, releases in lock["packages"].items()
        if isinstance(releases, dict)
        for release, pin in releases.items()
        if release in codenames
        and isinstance(pin, dict)
        and any(checks.MARKER_RE.search(str(v)) is None for v in pin.values())
    }
    # Every one of them reaches a download: none is left without an index.
    assert {f.where for f in found} == pins
    assert all(": offline" in f.message for f in found), [str(f) for f in found]
    assert {"packages.incus.ubuntu-24.04", "packages.nvidia_driver.ubuntu-24.04"} <= pins


def test_the_lock_check_runs_the_package_check_with_the_tree_s_sources(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: dict[str, Any] = {}

    def fake(lock: Any, sources: Any, codenames: Any) -> list[checks.Finding]:
        seen.update(sources=sources, codenames=codenames)
        return [checks.Finding(checks.ERROR, "packages.incus.ubuntu-24.04", "gone")]

    monkeypatch.setattr(checks, "package_findings", fake)
    assert (
        cli.main(
            [
                "check",
                "--lock",
                str(ROOT / "versions.lock.yml"),
                "--only",
                "packages",
                "--skip-markers",
            ]
        )
        == 1
    )
    assert seen["sources"] == real("ansible/component-sources.yml")
    assert seen["codenames"]["ubuntu-24.04"] == "noble"
    assert "error: packages.incus.ubuntu-24.04: gone" in capsys.readouterr().out


def test_the_lock_check_fails_when_the_sources_cannot_be_read(tmp_path: Path) -> None:
    findings = cli._package_findings({}, tmp_path)
    assert [f.where for f in findings] == ["packages"]
    assert findings[0].severity == checks.ERROR
    assert findings[0].message.startswith("cannot read the sources: ")


def test_packages_is_a_category_every_lock_check_runs() -> None:
    assert "packages" in cli.CATEGORIES
    makefile = (ROOT / "Makefile").read_text()
    for target in ("lock-check:", "lock-check-without-markers:"):
        recipe = makefile.split(f"\n{target}", 1)[1].split("\n\n", 1)[0]
        assert "--only" not in recipe, target


# --- after the first review --------------------------------------------------------------------


def armored(packet: bytes) -> bytes:
    import base64  # noqa: PLC0415 - only here

    begin, end = (
        "-----BEGIN PGP PUBLIC" + " KEY BLOCK-----",
        "-----END PGP PUBLIC" + " KEY BLOCK-----",
    )
    return f"{begin}\n\n{base64.b64encode(packet).decode()}\n{end}\n".encode()


def test_an_armored_vendor_key_is_dearmored_for_the_check() -> None:
    repo = network()
    repo.files["https://key.example/zabbly.gpg"] = armored(ZABBLY)
    zabbly(repo, ["1"])
    assert run(doc({"incus": {"ubuntu-24.04": {"incus": "1"}}}), repo) == []


def test_a_sha512_mismatch_fails_and_neither_a_404_nor_a_mismatch_is_tried_again() -> None:
    repo = network()
    base = CTK_URI.format(deb_arch="amd64")
    repo.add(CTK, base, {"": {"tk": ["1"]}}, hashes=("SHA512",), name="Packages.xz")
    repo.files[f"{base}/Packages.xz"] = lzma.compress(packages({"tk": ["1", "2"]}))
    found = run(doc({"nvidia_container_toolkit": {"ubuntu-24.04": {"tk": "1"}}}), repo)
    assert len(found) == 1
    assert f"cannot check the pin: {base}/Packages.xz has SHA512 " in found[0]
    assert repo.fetched.count(f"{base}/Packages.xz") == 1
    repo = network()
    zabbly(repo, ["1"])
    del repo.files[NOBLE_INDEX]
    run(doc({"incus": {"ubuntu-24.04": {"incus": "1"}}}), repo)
    assert repo.fetched.count(NOBLE_INDEX) == 1


def test_every_component_of_a_vendor_repository_is_read() -> None:
    sources = {
        **SOURCES,
        "repositories": {
            **SOURCES["repositories"],
            "zabbly": {**SOURCES["repositories"]["zabbly"], "components": ["main", "extra"]},
        },
    }
    repo = network()
    repo.add(
        ZABBLY,
        ZABBLY_NOBLE,
        {"main/binary-amd64/": {"incus": ["1"]}, "extra/binary-amd64/": {"incus-ui": ["2"]}},
        components="main extra",
    )
    found = checks.package_findings(
        doc({"incus": {"ubuntu-24.04": {"incus": "1", "incus-ui": "2"}}}),
        sources,
        CODENAMES,
        fetch=repo.fetch,
        verify=repo.verify,
        policy=quick(),
    )
    assert found == []
    assert f"{ZABBLY_NOBLE}/extra/binary-amd64/Packages.gz" in repo.fetched


def test_a_release_file_is_trusted_only_for_the_key_it_was_checked_with() -> None:
    # Two sources on one base with different keys: the second is checked with
    # its own key, not trusted on the first one's check.
    sources = {
        **SOURCES,
        "repositories": {
            **SOURCES["repositories"],
            "same-base": {**SOURCES["repositories"]["zabbly"], "key": "ctk"},
        },
        "components": {
            **SOURCES["components"],
            "tailscale": {"ubuntu": {"source": "vendor_repo", "repo": "same-base"}},
        },
    }
    repo = network()
    zabbly(repo, ["1"])
    found = checks.package_findings(
        doc(
            {
                "incus": {"ubuntu-24.04": {"incus": "1"}},
                "tailscale": {"ubuntu-24.04": {"incus": "1"}},
            }
        ),
        sources,
        CODENAMES,
        fetch=repo.fetch,
        verify=repo.verify,
        policy=quick(),
    )
    assert [str(f) for f in found] == [
        "error: packages.tailscale.ubuntu-24.04: cannot check the pin:"
        f" {ZABBLY_NOBLE}/InRelease: the signature does not verify with the key: BAD"
    ]


@pytest.mark.parametrize(
    "broken",
    [b"not gzip", gzip.compress(b"")[:10] + b"\xff" * 16],
    ids=["not-gzip", "corrupt-deflate-stream"],
)
def test_a_corrupt_index_fails_the_pin_without_ending_the_check(broken: bytes) -> None:
    repo = network()
    zabbly(repo, ["1"])
    zabbly(repo, ["1"], "trixie")
    release = repo.files[f"{ZABBLY_NOBLE}/InRelease"].decode()
    good = hashlib.sha256(repo.files[NOBLE_INDEX]).hexdigest()
    repo.files[f"{ZABBLY_NOBLE}/InRelease"] = release.replace(
        good, hashlib.sha256(broken).hexdigest()
    ).encode()
    repo.files[NOBLE_INDEX] = broken
    lock = doc({"incus": {"ubuntu-24.04": {"incus": "1"}, "debian-13": {"incus": "1"}}})
    found = run(lock, repo)
    assert len(found) == 1
    assert found[0].startswith(
        f"error: packages.incus.ubuntu-24.04: cannot check the pin: {NOBLE_INDEX} cannot be read: "
    )
    # The check went on to the next release, which passed.
    assert f"{ZABBLY_URI}/dists/trixie/main/binary-amd64/Packages.gz" in repo.fetched


def test_a_key_the_lock_does_not_have_fails_the_pin_without_ending_the_check() -> None:
    lock = doc({"incus": {"ubuntu-24.04": {"incus": "1"}}})
    del lock["repository_keys"]["keys"]["zabbly"]
    assert run(lock, network()) == [
        "error: packages.incus.ubuntu-24.04: cannot check the pin: KeyError: 'zabbly'"
    ]
