"""Tests for the filter plugin (ansible/filter_plugins/pokus.py)."""

from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Any

import pytest

import pokus
from ansible_testlib import debian_codenames, evaluate, fixture_names, fixture_profile


def identity(name: str) -> dict[str, Any]:
    return pokus.release_identity(fixture_profile(name), debian_codenames())


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        (
            "ubuntu2404-baremetal-no-gpu",
            ("ubuntu", "24.04", "noble", "24", "ubuntu", "ubuntu-24.04"),
        ),
        ("debian13-baremetal", ("debian", "13", "trixie", "13", "debian", "debian-13")),
        # Derivatives use the parent's identity (UBUNTU_CODENAME), never their own codename.
        ("linuxmint22-baremetal", ("ubuntu", "24.04", "noble", "24", "ubuntu", "ubuntu-24.04")),
        ("fedora-selinux-enforcing-firewalld", ("fedora", "44", "", "44", "fedora", "fedora-44")),
        ("almalinux9-kvm-vm", ("almalinux", "9.6", "", "9", "rhel_like", "el-9")),
        ("oraclelinux9", ("ol", "9.6", "", "9", "rhel_like", "el-9")),
        # Rolling releases are not pinned: empty release key.
        (
            "opensuse-tumbleweed-baremetal",
            ("opensuse-tumbleweed", "20261001", "", "20261001", "opensuse", ""),
        ),
        ("arch-baremetal", ("arch", "", "", "", "arch", "")),
        ("manjaro-baremetal", ("manjaro", "", "", "", "arch", "")),
    ],
)
def test_release_identity_of_fixtures(fixture: str, expected: tuple[str, ...]) -> None:
    keys = ("distro", "release", "codename", "major", "source_group", "release_key")
    assert identity(fixture) == dict(zip(keys, expected, strict=True))


def test_every_installable_fixture_has_an_identity() -> None:
    for name in fixture_names():
        profile = fixture_profile(name)
        if profile["outcome"] == "unsupported":
            continue  # bootstrap stops before Ansible (exit code 20)
        result = pokus.release_identity(profile, debian_codenames())
        assert result["source_group"] in set(pokus.SOURCE_GROUPS.values()), name


def test_release_identity_leap_and_rhel_keys() -> None:
    leap = {
        "family": "suse",
        "release_model": "fixed",
        "os": {"id": "opensuse-leap", "version_id": "15.6"},
    }
    rhel = {
        "family": "redhat",
        "release_model": "fixed",
        "os": {"id": "rhel", "version_id": "10.0"},
    }
    assert pokus.release_identity(leap, {})["release_key"] == "opensuse-leap-15.6"
    assert pokus.release_identity(rhel, {})["release_key"] == "el-10"


def test_release_identity_errors() -> None:
    with pytest.raises(pokus.PokusFilterError, match="unsupported family"):
        pokus.release_identity({"family": "gentoo", "os": {"id": "gentoo"}}, {})
    unknown = {"family": "debian", "os": {"id": "debian", "codename": "sid"}}
    with pytest.raises(pokus.PokusFilterError, match="unknown Debian-family release 'sid'"):
        pokus.release_identity(unknown, debian_codenames())


MATRIX = {
    "docker": {
        "default": {"source": "distro", "packages": ["docker"]},
        "ubuntu": {"source": "vendor_repo", "repo": "docker-deb", "packages": ["docker-ce"]},
    }
}


def test_component_source() -> None:
    assert pokus.component_source(MATRIX, "docker", "ubuntu")["repo"] == "docker-deb"
    assert pokus.component_source(MATRIX, "docker", "arch") == {
        "source": "distro",
        "packages": ["docker"],
    }
    with pytest.raises(pokus.PokusFilterError, match="unknown component"):
        pokus.component_source(MATRIX, "podman", "arch")


LOCK = {
    "docker": {"ubuntu-24.04": {"docker-ce": "5:29.0.0-1"}, "el-9": "TODO(verify)"},
    "tailscale": {"ubuntu-24.04": {"tailscale": "TODO(verify)"}},
}


def test_lock_packages() -> None:
    assert pokus.lock_packages(LOCK, "docker", "ubuntu-24.04") == {"docker-ce": "5:29.0.0-1"}
    assert pokus.lock_packages(LOCK, "docker", "") == {}  # rolling


@pytest.mark.parametrize(
    ("component", "key"),
    [("docker", "el-9"), ("docker", "debian-13"), ("tailscale", "ubuntu-24.04"), ("incus", "el-9")],
)
def test_lock_packages_refuses_unverified_pins(component: str, key: str) -> None:
    with pytest.raises(pokus.PokusFilterError, match="no verified pins"):
        pokus.lock_packages(LOCK, component, key)


@pytest.mark.parametrize(
    ("manager", "expected"),
    [
        ("apt", ["docker-ce=5:29.0.0-1", "plain"]),
        ("dnf", ["docker-ce-5:29.0.0-1", "plain"]),
        ("zypper", ["docker-ce=29.0.0-1", "plain"]),
        ("pacman", ["docker-ce", "plain"]),
    ],
)
def test_package_specs(manager: str, expected: list[str]) -> None:
    versions = {"docker-ce": "5:29.0.0-1"}
    assert pokus.package_specs(["docker-ce", "plain"], versions, manager) == expected


def test_package_specs_unknown_manager() -> None:
    with pytest.raises(pokus.PokusFilterError, match="unknown package manager"):
        pokus.package_specs(["x"], {}, "apk")


def test_repo_url() -> None:
    ident = {"distro": "ubuntu", "codename": "noble", "major": "24"}
    assert (
        pokus.repo_url("https://example.com/{distro}/{codename}", ident)
        == "https://example.com/ubuntu/noble"
    )
    assert pokus.repo_url("https://example.com/{deb_arch}", ident, {"deb_arch": "arm64"}).endswith(
        "/arm64"
    )
    assert (
        pokus.repo_url("https://example.com/{major}/$basearch", ident)
        == "https://example.com/24/$basearch"
    )
    with pytest.raises(pokus.PokusFilterError, match="no value for placeholder"):
        pokus.repo_url("https://example.com/{deb_arch}", ident)


def test_apt_pin_block() -> None:
    block = pokus.apt_pin_block({"b": "2", "a": "1:1.0"})
    assert block == (
        "Package: a\nPin: version 1:1.0\nPin-Priority: 1001\n\n"
        "Package: b\nPin: version 2\nPin-Priority: 1001"
    )
    assert pokus.apt_pin_block({}) == ""


PRIMARY = "0123456789ABCDEF0123456789ABCDEF01234567"
SUBKEY = "FEDCBA9876543210FEDCBA9876543210FEDCBA98"
COLONS = "\n".join(
    [
        "pub:-:255:22:89ABCDEF01234567:1700000000:::-:::scSC::::::23::0:",
        f"fpr:::::::::{PRIMARY.lower()}:",
        "uid:-::::1700000000::0000::Example Signing Key <signing@example.com>::::::::::0:",
        "sub:-:255:18:FEDCBA98FEDCBA98:1700000000::::::e::::::23:",
        f"fpr:::::::::{SUBKEY}:",
        "pub:-:4096:1:0000000000000000:1700000000:::-:::scSC::::::23::0:",
        f"fpr:::::::::{SUBKEY}:",
    ]
)


def test_fingerprints_returns_primary_keys_only() -> None:
    assert pokus.fingerprint_from_colons(COLONS) == [PRIMARY, SUBKEY]
    assert pokus.fingerprint_from_colons("") == []


def test_filters_are_registered_with_ansible() -> None:
    names = set(pokus.FilterModule().filters())
    assert names == {
        "pokus_release_identity",
        "pokus_component_source",
        "pokus_lock_packages",
        "pokus_package_specs",
        "pokus_repo_url",
        "pokus_apt_pin_block",
        "pokus_fingerprints",
        "pokus_scram_verifier",
        "pokus_scram_matches",
        "pokus_config_hashes",
        "pokus_image_by_alias",
        "pokus_to_toml",
        "pokus_socket_specs",
        "pokus_unit_hardening",
        "pokus_cpuset_cpus",
    }
    profile = fixture_profile("debian13-baremetal")
    result = evaluate("p | pokus_release_identity(c)", {"p": profile, "c": debian_codenames()})
    assert result["release_key"] == "debian-13"


def test_image_by_alias() -> None:
    images: list[dict[str, Any]] = [
        {"fingerprint": "a", "aliases": None},
        {"fingerprint": "b", "aliases": []},
        {"fingerprint": "c", "aliases": [{"name": "web/1.0.0"}, {"name": "_base/1.0.0"}]},
    ]
    assert pokus.image_by_alias(images, "_base/1.0.0")["fingerprint"] == "c"
    assert pokus.image_by_alias(images, "_base/2.0.0") == {}
    assert pokus.image_by_alias(None, "_base/1.0.0") == {}


# RFC 7677 section 3 exchange (user "user", password "pencil"); StoredKey and
# ServerKey as derived by the scramp project's test suite.
RFC7677_SALT = "W22ZaJ0SNY7soEsUEjb6gQ=="
RFC7677_SECRET = (
    "SCRAM-SHA-256$4096:W22ZaJ0SNY7soEsUEjb6gQ=="  # noqa: S105 - published test vector
    "$WG5d8oPm3OtcPnkdi4Uo7BkeZkBFzpcXkuLmtbsT4qY=:wfPLwcE6nTWhTAmQ7tl2KeoiWGPlZqQxSrmfPwDl2dU="
)
RFC7677_AUTH_MESSAGE = (
    b"n=user,r=rOprNGfwEbeRWgbNEkqO,"
    b"r=rOprNGfwEbeRWgbNEkqO%hvYDpWUa2RaTCAfuxFIlj)hNlF$k0,s=W22ZaJ0SNY7soEsUEjb6gQ==,i=4096,"
    b"c=biws,r=rOprNGfwEbeRWgbNEkqO%hvYDpWUa2RaTCAfuxFIlj)hNlF$k0"
)
RFC7677_PROOF = "dHzbZapWIk4jUhN+Ute9ytag9zjfMHgsqmmiz7AndVQ="
RFC7677_SERVER_SIGNATURE = "6rriTRBi23WpRR/wtup+mMhUZUn/dB5nLTJRsjl95G4="


def test_scram_verifier_matches_rfc7677() -> None:
    secret = pokus.scram_verifier("pencil", RFC7677_SALT)
    assert secret == RFC7677_SECRET
    stored, server = (base64.b64decode(k) for k in secret.rsplit("$", 1)[1].split(":"))
    # The client proof of the RFC's exchange verifies against StoredKey, and
    # ServerKey produces the RFC's server signature.
    signature = hmac.new(stored, RFC7677_AUTH_MESSAGE, "sha256").digest()
    client_key = bytes(
        a ^ b for a, b in zip(base64.b64decode(RFC7677_PROOF), signature, strict=True)
    )
    assert hashlib.sha256(client_key).digest() == stored
    server_signature = hmac.new(server, RFC7677_AUTH_MESSAGE, "sha256").digest()
    assert base64.b64encode(server_signature).decode() == RFC7677_SERVER_SIGNATURE


def test_scram_verifier_draws_a_salt() -> None:
    first, second = pokus.scram_verifier("pencil"), pokus.scram_verifier("pencil")
    assert first != second
    assert pokus.scram_matches("pencil", first)
    assert len(base64.b64decode(first.split(":")[1].split("$")[0])) == 16


@pytest.mark.parametrize(
    ("password", "secret", "expected"),
    [
        ("pencil", RFC7677_SECRET, True),
        ("pencil2", RFC7677_SECRET, False),
        ("pencil", None, False),
        ("pencil", "", False),
        ("pencil", "md5" + "0" * 32, False),
        ("pencil", RFC7677_SECRET.replace("4096", "4097"), False),
        ("pencil", "SCRAM-SHA-256$4096:%%%$a:b", False),
    ],
)
def test_scram_matches(password: str, secret: str | None, expected: bool) -> None:
    assert pokus.scram_matches(password, secret) is expected


@pytest.mark.parametrize("password", ["", "pässword", "tab\there"])
def test_scram_rejects_passwords_postgres_would_normalize(password: str) -> None:
    with pytest.raises(pokus.PokusFilterError, match="printable ASCII"):
        pokus.scram_verifier(password, RFC7677_SALT)
    with pytest.raises(pokus.PokusFilterError, match="printable ASCII"):
        pokus.scram_matches(password, None)


def test_scram_rejects_a_bad_salt() -> None:
    with pytest.raises(pokus.PokusFilterError, match="not base64"):
        pokus.scram_verifier("pencil", "not base64!")


def test_config_hashes_per_directory() -> None:
    root = "/srv/pokus/services/core/config"
    files = [
        {"path": f"{root}/postgres/postgresql.conf", "checksum": "a"},
        {"path": f"{root}/postgres/pg_hba.conf", "checksum": "b"},
        {"path": f"{root}/opa/authz.rego", "checksum": "c"},
        {"path": f"{root}/otel-agent/x/y.yaml", "checksum": "d"},
    ]
    hashes = pokus.config_hashes(files, root + "/")
    assert sorted(hashes) == ["OPA_CONFIG_HASH", "OTEL_AGENT_CONFIG_HASH", "POSTGRES_CONFIG_HASH"]
    assert hashes == pokus.config_hashes(list(reversed(files)), root)  # order does not matter
    changed = pokus.config_hashes([{**files[0], "checksum": "z"}, *files[1:]], root)
    assert changed["POSTGRES_CONFIG_HASH"] != hashes["POSTGRES_CONFIG_HASH"]
    assert changed["OPA_CONFIG_HASH"] == hashes["OPA_CONFIG_HASH"]
    assert pokus.config_hashes([], root) == {}


@pytest.mark.parametrize("path", ["/srv/pokus/services/core/config/top.conf", "/elsewhere/a/b"])
def test_config_hashes_rejects_files_outside_directories(path: str) -> None:
    with pytest.raises(pokus.PokusFilterError, match="not in a directory"):
        pokus.config_hashes([{"path": path, "checksum": "a"}], "/srv/pokus/services/core/config")


def test_cpuset_cpus() -> None:
    assert pokus.cpuset_cpus("") == []
    assert pokus.cpuset_cpus(None) == []
    assert pokus.cpuset_cpus("0-5") == [0, 1, 2, 3, 4, 5]
    assert pokus.cpuset_cpus("6-7,3,6") == [3, 6, 7]
    assert pokus.cpuset_cpus(" 1 , 2 ") == [1, 2]
    for bad in ("a-b", "5-3", "1,,2", "-1", "1-"):
        with pytest.raises(pokus.PokusFilterError):
            pokus.cpuset_cpus(bad)
