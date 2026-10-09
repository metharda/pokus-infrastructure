"""Tests for the privacy scanner.

Values that the scanner must flag are assembled at runtime, so this file itself
passes the privacy scan.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from pinka_repo_checks import privacy

PLAIN = Path("docs/page.md")

# Assembled so the literals never appear in the repository.
PUBLIC_V4 = ".".join(["8", "8", "4", "4"])
PUBLIC_V6 = ":".join(["2606", "4700", "", "1111"])
FOREIGN_EMAIL = "someone" + "@" + "mail-provider" + ".dev"
LOCAL_MAC = ":".join(["02", "42", "ac", "11", "00", "02"])
RANDOM_UUID = str(uuid.UUID(int=0x1234_5678_9ABC))
SSH_KEY = "ssh-ed25519 " + "AAAA" + "C3NzaC1lZDI1NTE5" + "A" * 24
PRIVATE_KEY = "-----BEGIN " + "OPENSSH PRIVATE KEY-----"
PGP_KEY = "-----BEGIN PGP " + "PUBLIC KEY BLOCK-----"
TS_KEY = "tskey" + "-auth-" + "k" * 20


def rules(text: str, path: Path = PLAIN, denylist: tuple[str, ...] = ()) -> list[str]:
    return [f.rule for f in privacy.scan_text(path, text, denylist)]


@pytest.mark.parametrize(
    "text",
    [
        "researcher1@example.com",
        "ops@lab.example.org",
        "x@host.test",
        "git clone git@github.com:example-org/pokus-platform.git",
        "PubkeyAcceptedAlgorithms sk-ssh-ed25519@openssh.com",
        "uses: actions/checkout@v4",
        "npm install cspell@10.3.6",
    ],
)
def test_allowed_email_like_text(text: str) -> None:
    assert rules(text) == []


def test_foreign_email_is_flagged() -> None:
    assert rules(f"contact {FOREIGN_EMAIL} today") == ["email"]


@pytest.mark.parametrize(
    "text",
    [
        "listen 127.0.0.1:4317",
        "pool 10.0.0.0/8 and 172.30.0.0/16 and 192.168.0.0/16",
        "tailnet 100.64.0.0/10",
        "docs 192.0.2.10 198.51.100.7 203.0.113.0/24",
        "bind 0.0.0.0",
        "kernel 6.6.87.2-microsoft-standard-WSL2",
        "version 1.2.3",
        "localhost ::1 and fe80::1 and fd00::5 and 2001:db8::42",
        "time 2026-10-05T09:12:44Z",
    ],
)
def test_allowed_addresses(text: str) -> None:
    assert rules(text) == []


def test_public_ipv4_is_flagged() -> None:
    assert rules(f"nameserver {PUBLIC_V4}") == ["public-ip"]


def test_public_ipv4_network_is_flagged() -> None:
    assert rules(f"allow {PUBLIC_V4}/32") == ["public-ip"]


def test_public_ipv6_is_flagged() -> None:
    assert rules(f"addr {PUBLIC_V6}") == ["public-ip"]


def test_documentation_mac_is_allowed() -> None:
    assert rules("hwaddr 00:00:5e:00:53:01 and 00-00-5E-00-53-FF") == []


def test_other_mac_is_flagged() -> None:
    assert rules(f"hwaddr {LOCAL_MAC}") == ["mac"]


def test_nil_uuid_is_allowed() -> None:
    assert rules("product_uuid 00000000-0000-0000-0000-000000000000") == []


def test_non_nil_uuid_is_flagged() -> None:
    assert rules(f"product_uuid {RANDOM_UUID}") == ["uuid"]


@pytest.mark.parametrize("material", [SSH_KEY, PRIVATE_KEY, PGP_KEY])
def test_key_material_is_flagged_outside_fixtures(material: str) -> None:
    assert rules(material) == ["key-material"]


@pytest.mark.parametrize(
    "path",
    [
        Path("preflight/tests/fixtures/test-keys/id_ed25519.pub"),
        Path("installer/tests/test-keys/sample.pub"),
    ],
)
def test_key_material_is_allowed_in_test_key_fixtures(path: Path) -> None:
    assert rules(SSH_KEY, path) == []


def test_test_keys_must_be_below_tests() -> None:
    assert rules(SSH_KEY, Path("test-keys/tests/x.pub")) == ["key-material"]


def test_tailscale_auth_key_is_flagged() -> None:
    assert rules(f"authkey: {TS_KEY}") == ["tailscale-auth-key"]


def test_placeholder_auth_key_is_allowed() -> None:
    assert rules("authkey: tskey-auth-<key>") == []


def test_denylist_is_case_insensitive_and_redacted() -> None:
    findings = privacy.scan_text(PLAIN, "Lab of Somebody", ["somebody"])
    assert [f.rule for f in findings] == ["denylist"]
    assert "somebody" not in findings[0].message.lower()
    assert findings[0].value == "<redacted>"


def test_denylist_matches_whole_words_only() -> None:
    assert rules("hash bCAsomebodyX0 and somebodys", denylist=("somebody",)) == []
    assert rules("by Somebody-Else", denylist=("somebody",)) == ["denylist"]
    assert rules("owner: example-org/team", denylist=("example-org",)) == ["denylist"]


def test_load_denylist_splits_on_newlines_and_commas() -> None:
    env = {privacy.DENYLIST_ENV: " Alpha ,beta\n\n gamma "}
    assert privacy.load_denylist(env) == ["alpha", "beta", "gamma"]


def test_load_denylist_empty_by_default() -> None:
    assert privacy.load_denylist({}) == []


def test_run_reports_without_values(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(privacy.DENYLIST_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "clean.md").write_text("researcher1@example.com\n", encoding="utf-8")
    (tmp_path / "dirty.md").write_text(f"dns {PUBLIC_V4}\n", encoding="utf-8")
    (tmp_path / "binary.bin").write_bytes(b"\0" + PUBLIC_V4.encode())
    assert privacy.run([tmp_path]) == 1
    out = capsys.readouterr().out
    assert "dirty.md:1:5: public-ip" in out
    assert PUBLIC_V4 not in out
    assert "binary.bin" not in out


def test_run_show_matches_prints_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(privacy.DENYLIST_ENV, raising=False)
    (tmp_path / "dirty.md").write_text(f"dns {PUBLIC_V4}\n", encoding="utf-8")
    assert privacy.run([tmp_path], show_matches=True) == 1
    assert PUBLIC_V4 in capsys.readouterr().out


def test_run_clean_tree_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(privacy.DENYLIST_ENV, raising=False)
    (tmp_path / "a.md").write_text("bind 127.0.0.1\n", encoding="utf-8")
    assert privacy.run([tmp_path]) == 0
