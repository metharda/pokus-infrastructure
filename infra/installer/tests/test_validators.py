"""Validation rules (BUILD_PROMPT §18.8)."""

from __future__ import annotations

import shutil

import pytest
from installer_testlib import private_key, public_key

from pinka_installer import keys, validators


@pytest.mark.parametrize("value", ["pinka-lab", "a", "lab2", "x" * 63])
def test_hostname_ok(value: str) -> None:
    assert validators.hostname(value) is None


@pytest.mark.parametrize("value", ["", "-lab", "lab-", "Lab", "lab_1", "lab.example", "x" * 64])
def test_hostname_bad(value: str) -> None:
    assert validators.hostname(value)


@pytest.mark.parametrize("value", ["researcher1", "_svc", "a-b_c", "r" * 32])
def test_username_ok(value: str) -> None:
    assert validators.username(value, ["pinka-build"]) is None


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("Researcher", "lower-case"),
        ("1abc", "lower-case"),
        ("r" * 33, "lower-case"),
        ("root", "reserved"),
        ("docker", "reserved"),
        ("pinka-build", "lab's own accounts"),
        ("pinka-new", "lab's own accounts"),
    ],
)
def test_username_bad(value: str, reason: str) -> None:
    message = validators.username(value, ["pinka-build"])
    assert message and reason in message


@pytest.mark.parametrize("value", ["/srv/pinka", "/", "/var/lib/docker"])
def test_absolute_path_ok(value: str) -> None:
    assert validators.absolute_path(value) is None


@pytest.mark.parametrize(
    "value", ["srv", "/srv/../etc", "/srv/./x", "/srv//x", "/srv/", "/a b", "/a\tb"]
)
def test_absolute_path_bad(value: str) -> None:
    assert validators.absolute_path(value)


def test_cpusets() -> None:
    assert validators.parse_cpuset("0-2,5") == {0, 1, 2, 5}
    assert validators.cpuset("0-5", 8) is None
    assert "does not exist" in (validators.cpuset("0-8", 8) or "")
    for bad in ("", "a", "3-1", "1,,2", "1-"):
        assert validators.cpuset(bad)
    assert validators.cpusets_disjoint("0-5", "6-19") is None
    assert "share CPUs 4-5" in (validators.cpusets_disjoint("0-5", "4-9") or "")
    assert validators.format_cpuset([0, 1, 2, 5, 7, 8]) == "0-2,5,7-8"


def test_sizes() -> None:
    assert validators.size("300GiB") is None
    assert validators.size_bytes("2GB") == 2 * 10**9
    assert validators.size_bytes("1TiB") == 2**40
    for bad in ("0GiB", "1.5GiB", "300", "300 GiB", "300gb"):
        assert validators.size(bad)


def test_urls_and_sources() -> None:
    assert validators.https_url("https://example.com/x") is None
    assert validators.https_url("http://example.com")
    assert "credentials" in (validators.https_url("https://user:pw@example.com") or "")
    for ok in (
        "git@github.com:example-org/pokus-platform.git",
        "ssh://git@git.example.com:2222/example-org/pokus-platform.git",
        "https://git.example.com/example-org/pokus-platform.git",
    ):
        assert validators.source_repo(ok) is None, ok
    for bad in (
        "github.com/example-org/pokus-platform",
        "ftp://example.com/x",
        "https://u:p@example.com/x",
    ):
        assert validators.source_repo(bad), bad


@pytest.mark.parametrize("value", ["platform-v1.0.0", "main", "release/1.x", "a" * 40, "0" * 40])
def test_git_ref_ok(value: str) -> None:
    assert validators.git_ref(value) is None


@pytest.mark.parametrize(
    "value", ["", "@", "-x", "a..b", "a b", "a~1", "x.lock", "a/", "a@{1}", "a\x01"]
)
def test_git_ref_bad(value: str) -> None:
    assert validators.git_ref(value)


def test_tags_models_times_snapshots() -> None:
    assert validators.tailscale_tag("tag:pinka-lab") is None
    for bad in ("pinka-lab", "tag:Pinka", "tag:1x", "tag:"):
        assert validators.tailscale_tag(bad)
    assert validators.hf_repo("example-org/model-7b.v1") is None
    for bad in ("model", "a/b/c", "-a/b", "a/b..c"):
        assert validators.hf_repo(bad)
    assert validators.revision("0123456789abcdef0123456789abcdef01234567") is None
    for bad in ("main", "0123456789ABCDEF0123456789ABCDEF01234567", "abc"):
        assert validators.revision(bad)
    assert validators.time_of_day("03:00") is None
    assert validators.time_of_day("24:00")
    assert validators.snapshot("latest") is None
    assert validators.snapshot("deadbeef") is None
    assert validators.snapshot("xyz")


def test_restic_repositories() -> None:
    assert validators.restic_repository("sftp:backup@backup.example.com:/srv/restic/lab") is None
    assert validators.restic_repository("sftp:backup@192.0.2.10:/srv/restic") is None
    assert validators.restic_repository("rest:https://backup.example.com/lab") is None
    assert validators.restic_repository("/var/backups/pinka") is None
    for bad in (
        "sftp:backup.example.com:/x",
        "sftp:u@h:relative",
        "rest:http://x",
        "s3:x",
        "relative",
    ):
        assert validators.restic_repository(bad), bad


def test_interfaces() -> None:
    assert validators.interface("enp5s0") is None
    for bad in ("lo", "tailscale0", "a b", "x" * 16):
        assert validators.interface(bad)


def test_ssh_keys_are_parsed_and_fingerprinted() -> None:
    lines = [public_key("ed25519"), public_key("ecdsa", ""), public_key("rsa3072")]
    found, errors = validators.parse_ssh_keys("# keys\n\n" + "\n".join(lines) + "\n")
    assert errors == []
    assert [k.key_type for k in found] == ["ssh-ed25519", "ecdsa-sha2-nistp256", "ssh-rsa"]
    assert all(k.fingerprint.startswith("SHA256:") and "=" not in k.fingerprint for k in found)
    assert found[2].bits == 3072
    assert found[0].line == lines[0]


def test_bad_ssh_keys_are_rejected_with_a_reason() -> None:
    _, errors = validators.parse_ssh_keys(private_key())
    assert errors and "PRIVATE" in errors[0]
    _, errors = validators.parse_ssh_keys(public_key("rsa2048"))
    assert "3072 bits" in errors[0]
    good = public_key()
    kind, data, *_ = good.split()
    _, errors = validators.parse_ssh_keys(f"ssh-rsa {data}")
    assert "does not match" in errors[0]
    _, errors = validators.parse_ssh_keys(f"{kind} not-base64!")
    assert "base64" in errors[0]
    _, errors = validators.parse_ssh_keys("ssh-dss AAAA")
    assert "unsupported" in errors[0]
    _, errors = validators.parse_ssh_keys(f"{good}\n{good}")
    assert "duplicate" in errors[0]
    _, errors = validators.parse_ssh_keys("\n# nothing\n")
    assert errors == ["no public key found"]


def test_key_check_with_and_without_ssh_keygen() -> None:
    line = public_key()
    result = keys.check(line, use_ssh_keygen=False)
    assert result.errors == ()
    assert result.fingerprints[0].endswith("researcher1@example.com")
    if shutil.which("ssh-keygen"):
        assert keys.check(line).keys == result.keys
    else:
        assert validators.ssh_keygen_fingerprint(line) is None
