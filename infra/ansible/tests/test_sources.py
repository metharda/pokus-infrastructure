"""The component source matrix agrees with versions.lock.yml and the support matrix.

BUILD_PROMPT §19.4: every ``vendor_repo`` entry references a repository
definition whose key URL and fingerprint are in versions.lock.yml, and the lock
pins packages per release for every fixed release the support matrix accepts.
"""

from __future__ import annotations

import json
import string
from typing import Any

import pytest

import pokus
from ansible_testlib import ANSIBLE, INFRA, debian_codenames, load_yaml

SOURCES = load_yaml(ANSIBLE / "component-sources.yml")
LOCK = load_yaml(INFRA / "versions.lock.yml")
MATRIX = json.loads((INFRA / "preflight" / "support-matrix.json").read_text(encoding="utf-8"))
GROUPS = {"default", *pokus.SOURCE_GROUPS.values()}
PLACEHOLDERS = {"distro", "codename", "major", "deb_arch"}
LOCKED_COMPONENTS = ("docker", "nvidia_container_toolkit", "incus", "tailscale")


def entries() -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (component, group, entry)
        for component, groups in SOURCES["components"].items()
        for group, entry in groups.items()
    ]


def test_every_component_has_a_default_and_known_groups() -> None:
    for component, groups in SOURCES["components"].items():
        assert "default" in groups, component
        assert set(groups) <= GROUPS, component


@pytest.mark.parametrize(("component", "group", "entry"), entries())
def test_entry_shape(component: str, group: str, entry: dict[str, Any]) -> None:
    assert set(entry) <= {"source", "repo", "packages", "channel", "method", "tier", "verify"}
    assert entry["source"] in ("vendor_repo", "distro", "community"), (component, group)
    assert isinstance(entry["packages"], list), (component, group)
    if "repo" in entry:
        assert entry["repo"] in SOURCES["repositories"], (component, group)
    if entry["source"] == "vendor_repo" and component != "nvidia_driver":
        assert "repo" in entry, (component, group)
    if "verify" in entry:
        assert entry["verify"].startswith("TODO(verify): "), (component, group)
    if component == "incus":
        assert entry["channel"] in ("lts", "non_lts"), group
    if component == "nvidia_driver":
        assert entry["method"] in ("dkms", "akmods", "kmp", "prebuilt", "manual"), group


@pytest.mark.parametrize("name", sorted(SOURCES["repositories"]))
def test_repository_definitions(name: str) -> None:
    repo = SOURCES["repositories"][name]
    assert repo["key"] in LOCK["repository_keys"]["keys"], name
    assert set(repo) <= {
        "type",
        "key",
        "uri",
        "suite",
        "components",
        "baseurl",
        "repo_gpgcheck",
        "verify",
    }
    if "verify" in repo:
        assert repo["verify"].startswith("TODO(verify): "), name
    if repo["type"] == "apt":
        templates = [repo["uri"], repo["suite"]]
        assert isinstance(repo["components"], list), name
    else:
        assert repo["type"] == "rpm", name
        templates = [repo["baseurl"]]
    for template in templates:
        fields = {f for _, f, _, _ in string.Formatter().parse(template) if f}
        assert fields <= PLACEHOLDERS, (name, template)
        assert template.startswith(("https://", "{", "/")), (name, template)


def test_repositories_are_used() -> None:
    used = {entry["repo"] for _, _, entry in entries() if "repo" in entry}
    assert used == set(SOURCES["repositories"])


def fixed_release_keys() -> list[str]:
    """Release keys of every supported or tested non-rolling release in the matrix."""
    codenames = debian_codenames()
    by_release = {(e["distro"], e["version"]): name for name, e in codenames.items()}
    keys = set()
    for row in MATRIX["os"]:
        if row["tier"] not in ("supported", "tested") or row["release_model"] == "rolling":
            continue
        match = row["match"]
        for distro in match.get("id_in") or [match["id"]]:
            for version in (
                match.get("version_id_in") or match.get("version_major_in") or [match["version_id"]]
            ):
                os_info = {
                    "id": distro,
                    "version_id": version,
                    "codename": by_release.get((distro, version)),
                }
                profile = {
                    "family": row["family"],
                    "release_model": row["release_model"],
                    "os": os_info,
                }
                keys.add(pokus.release_identity(profile, codenames)["release_key"])
    return sorted(keys)


def test_matrix_release_keys() -> None:
    assert fixed_release_keys() == sorted(
        [
            "debian-13",
            "el-10",
            "el-9",
            "fedora-43",
            "fedora-44",
            "opensuse-leap-15.6",
            "opensuse-leap-16.0",
            "ubuntu-22.04",
            "ubuntu-24.04",
        ]
    )


@pytest.mark.parametrize("component", LOCKED_COMPONENTS)
def test_lock_covers_every_fixed_release(component: str) -> None:
    # A value may still be the unverified marker (make lock-check fails on
    # it); the release key itself must exist so nothing is forgotten.
    assert set(fixed_release_keys()) <= set(LOCK["packages"][component]), component


@pytest.mark.parametrize("component", [*LOCKED_COMPONENTS, "nvidia_driver"])
def test_locked_packages_belong_to_the_component(component: str) -> None:
    names = {p for entry in SOURCES["components"][component].values() for p in entry["packages"]}
    for release, pins in LOCK["packages"][component].items():
        if isinstance(pins, dict):
            assert set(pins) <= names, (component, release)
