"""Tests for the filter plugin (ansible/filter_plugins/pokus.py)."""

from __future__ import annotations

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
    }
    profile = fixture_profile("debian13-baremetal")
    result = evaluate("p | pokus_release_identity(c)", {"p": profile, "c": debian_codenames()})
    assert result["release_key"] == "debian-13"
