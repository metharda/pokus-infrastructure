"""Validation rules."""

from __future__ import annotations

import os
import resource
import shutil
import subprocess
import sys
import textwrap

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
    assert validators.cpuset_ranges("0-2,5") == [(0, 2), (5, 5)]
    assert validators.in_cpuset(2, [(0, 2)]) and not validators.in_cpuset(3, [(0, 2)])
    assert validators.format_ranges([(5, 9), (0, 3), (4, 4), (20, 21), (21, 30)]) == "0-9,20-30"
    assert validators.format_ranges([(7, 7)]) == "7"


# A child process with this much address space and time: a range built one CPU at a time
# fails with MemoryError or is killed, instead of hanging the test run.
BOUNDED_BYTES = 2 * 2**30
BOUNDED_SECONDS = 60


def run_bounded(code: str) -> list[str]:
    """Run Python code in a child process with bounded memory and time; its output lines."""

    def limit() -> None:
        resource.setrlimit(resource.RLIMIT_AS, (BOUNDED_BYTES, BOUNDED_BYTES))

    done = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
        timeout=BOUNDED_SECONDS,
        preexec_fn=limit,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout.splitlines()


def test_a_cpuset_range_of_many_digits_is_never_built() -> None:
    # The operator's CPU sets are handled as ranges: one of many digits is checked,
    # compared and drawn as fast as a short one (the CPU bar runs on every keystroke).
    assert run_bounded(
        """
        from pinka_installer import validators as v
        from pinka_installer.tui.screens_capacity import cpu_bar
        huge = "0-99999999999"
        print(v.cpuset(huge, 8))
        print(v.cpuset(huge))
        print(v.cpusets_disjoint(huge, "5-99999999998,3"))
        print(v.smt_split(huge, "4-7", [{0, 8}, {4, 12}]))
        print(cpu_bar(huge, "4-7", 8))
        print(v.cpuset("99999999999"))
        even = ",".join(str(n) for n in range(0, 40000, 2))
        odd = ",".join(str(n) for n in range(1, 40000, 2))
        print(v.cpusets_disjoint(even, odd))
        print(v.cpusets_disjoint(even, "100-103," + odd))
        bar = cpu_bar(even, odd, 40000)
        evens = {bar[n] for n in range(0, len(bar), 2)}
        odds = {bar[n] for n in range(1, len(bar), 2)}
        print(len(bar), bar[:6], evens, odds)
        print(v.smt_split(even, odd, [{0, 1}, {2, 3}]))
        """
    ) == [
        "CPU 99999999999 does not exist (this host has CPUs 0-7)",
        "None",
        "services and targets share CPUs 3,5-99999999998",
        "SMT siblings of one core are split between services and targets (4,12): target "
        "measurements would share cores with the lab services; keep both threads of a core "
        "in the same set",
        "SSSSXXXX",
        "None",
        "None",
        "services and targets share CPUs 100,102",
        "40000 STSTST {'S'} {'T'}",
        "SMT siblings of one core are split between services and targets (0-1; 2-3): target "
        "measurements would share cores with the lab services; keep both threads of a core "
        "in the same set",
    ]


def test_a_value_no_dropdown_offers() -> None:
    choices = ("full", "lab", "inference")
    assert validators.offered("lab", choices) is None
    assert validators.offered("edge", choices) == (
        "'edge' is not one of the choices (full, lab, inference): choose one"
    )
    assert validators.offered("", choices) == "nothing is chosen: choose full, lab, inference"
    # Not a string (YAML read a number or a boolean): refused, shown as read.
    assert validators.offered(5, ["5"]) == "'5' is not one of the choices (5): choose one"
    assert validators.offered(False, ["off"]) == (
        "'False' is not one of the choices (off): choose one"
    )
    long = "x" * 100
    assert validators.offered(long, choices) == (
        f"'{'x' * validators.OFFERED_SHOWN}...' is not one of the choices "
        "(full, lab, inference): choose one"
    )


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


def test_platform_sources_are_the_platform_tool_s() -> None:
    release = {"repo": "git@github.com:example-org/example-platform.git", "ref": "example-v1"}
    for repo in (
        "git@github.com:example-org/example-platform.git",
        "ssh://git@git.example.com:2222/example-org/example-platform.git",
        "https://git.example.com/example-org/example-platform.git",
        "file:///srv/git/example-platform.git",
    ):
        assert validators.platform_sources({**release, "repo": repo}, {}) == [], repo
    for repo in (
        "github.com/example-org/example-platform",
        "ftp://example.com/x",
        "https://u:p@example.com/x",
        "http://git.example.com/x.git",
    ):
        found = validators.platform_sources({**release, "repo": repo}, {})
        assert found and found[0].startswith("platform/repo:"), repo
    assert validators.platform_sources({**release, "ref": "a..b"}, {})[0].startswith("platform/ref")
    bad_path = {"cases": {**release, "path": "../x"}}
    assert validators.platform_sources(release, bad_path)[0].startswith("content/cases/path")
    assert validators.platform_sources(release, {"Bad": release})[0].startswith("content:")


def test_cap_sizes_and_secret_keys() -> None:
    for ok in ("24G", "512M", "1K"):
        assert validators.cap_size(ok) is None, ok
    for bad in ("24GiB", "0G", "1.5G", "G", "24"):
        assert validators.cap_size(bad), bad
    assert validators.platform_secret_key("ctl.api-token") is None
    for bad in ("ctl", "Ctl.x", "ctl.X", "verylongname1.x"):
        assert validators.platform_secret_key(bad), bad


def test_serve_on_an_adopted_node_needs_consent() -> None:
    # Off by default on a node logged in before pinka.
    assert validators.serve_consent(adopted=True, serve=True, consent=False)
    assert validators.serve_consent(adopted=True, serve=True, consent=True) is None
    assert validators.serve_consent(adopted=True, serve=False, consent=False) is None
    assert validators.serve_consent(adopted=False, serve=True, consent=False) is None


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


def test_listener_names() -> None:
    for good in ("tcp/22", "udp/5353", "tcp/65535"):
        assert validators.listener_name(good) is None, good
    for bad in ("8080", "tcp/0", "tcp/65536", "sctp/1", "tcp/08", "TCP/22", "tcp/22 "):
        assert validators.listener_name(bad), bad


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


def test_uninstall_confirmation() -> None:
    assert validators.uninstall_confirmation("pinka-lab", "pinka-lab") is None
    assert "type the machine name, pinka-lab" in str(
        validators.uninstall_confirmation("", "pinka-lab")
    )
    for typed in ("pinka-lab ", "Pinka-lab", "pinka"):
        assert "not the machine name" in str(validators.uninstall_confirmation(typed, "pinka-lab"))
