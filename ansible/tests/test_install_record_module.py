"""Tests for the pinka_install_record module (BUILD_PROMPT §7.21)."""

from __future__ import annotations

import json
import stat
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pinka_install_record as mod
import pytest

DPKG = "bash\t5.2.21-2ubuntu4\ncurl\t8.5.0-2ubuntu10.6\n"
ENABLED = "ssh.service enabled enabled\ndocker.service enabled enabled\n"
MASKED = "sleep.target masked enabled\n"


def fake_run(*, packages: str = DPKG, rc: int = 0) -> Any:
    calls: list[list[str]] = []

    def run(argv: Sequence[str]) -> tuple[int, str, str]:
        calls.append(list(argv))
        if argv[0] in ("dpkg-query", "rpm", "pacman"):
            return rc, packages, "" if rc == 0 else "dpkg-query: error"
        if argv[-1] == "enabled":
            return 0, ENABLED, ""
        return 0, MASKED, ""

    run.calls = calls  # type: ignore[attr-defined]
    return run


def params(path: Path, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "path": str(path),
        "state": "init",
        "components": {"docker": {"installed": True, "origin": "pre-existing"}},
        "pkg_manager": "apt",
        "managed_files": [],
        "bootstrap_state": str(path.parent / "bootstrap-state.json"),
        "added": {},
    }
    base.update(overrides)
    return base


def test_init_writes_once_with_what_existed(tmp_path: Path) -> None:
    record_path = tmp_path / "lib" / "install-record.json"
    shared = tmp_path / "20auto-upgrades"
    shared.write_text('APT::Periodic::Unattended-Upgrade "0";\n')
    missing = tmp_path / "absent.conf"
    p = params(record_path, managed_files=[str(shared), str(missing)])
    result = mod.run_module(p, check_mode=False, run=fake_run())
    assert result["changed"] and result["exists"]
    record = json.loads(record_path.read_text())
    assert stat.S_IMODE(record_path.stat().st_mode) == 0o600
    before = record["preexisting"]
    assert record["legacy"] is False
    assert record["created_at"].endswith("Z")
    assert before["components"]["docker"]["origin"] == "pre-existing"
    assert before["packages"] == {"bash": "5.2.21-2ubuntu4", "curl": "8.5.0-2ubuntu10.6"}
    assert before["units_enabled"] == ["docker.service", "ssh.service"]
    assert before["units_masked"] == ["sleep.target"]
    assert before["files"] == {str(shared): 'APT::Periodic::Unattended-Upgrade "0";\n'}
    assert "root" in before["users"]
    assert record["added"] == mod.empty_added()
    # Never rewritten: a second init changes nothing.
    again = mod.run_module(p, check_mode=False, run=fake_run(packages="other\t1\n"))
    assert not again["changed"]
    assert json.loads(record_path.read_text()) == record


def test_check_mode_writes_nothing(tmp_path: Path) -> None:
    record_path = tmp_path / "install-record.json"
    result = mod.run_module(params(record_path), check_mode=True, run=fake_run())
    assert result["changed"] and not result["exists"]
    assert result["record"]["preexisting"]["packages"]["bash"] == "5.2.21-2ubuntu4"
    assert not record_path.exists()
    added = mod.run_module(
        params(record_path, state="add", added={"packages": ["incus"]}),
        check_mode=True,
        run=fake_run(),
    )
    assert added == {"changed": False, "record": {}, "exists": False}


def test_a_host_pinka_provisioned_before_is_legacy(tmp_path: Path) -> None:
    (tmp_path / "bootstrap-state.json").write_text("{}")
    result = mod.run_module(params(tmp_path / "r.json"), check_mode=False, run=fake_run())
    assert result["record"]["legacy"] is True


def test_add_keeps_only_what_is_new(tmp_path: Path) -> None:
    record_path = tmp_path / "r.json"
    mod.run_module(params(record_path), check_mode=False, run=fake_run())
    items = {
        "components": {"docker": True, "tailscale": False},
        "packages": ["curl", "docker-ce", "containerd.io"],
        "holds": ["docker-ce"],
        "accounts": ["pinka-build"],
        "groups": ["pinka"],
        "memberships": [{"user": "researcher1", "group": "pinka"}],
        "keys": [{"user": "researcher1", "key": "ssh-ed25519 AAAAC3Nz example-key-1"}],
        "tailscale_serve": ["https:443"],
    }
    first = mod.run_module(
        params(record_path, state="add", added=items), check_mode=False, run=fake_run()
    )
    assert first["changed"]
    added = json.loads(record_path.read_text())["added"]
    assert added["components"] == {"docker": True}
    # curl existed before pinka: not pinka's.
    assert added["packages"] == ["containerd.io", "docker-ce"]
    assert added["memberships"] == [{"user": "researcher1", "group": "pinka"}]
    # The same items again: nothing new, nothing written.
    second = mod.run_module(
        params(record_path, state="add", added=items), check_mode=False, run=fake_run()
    )
    assert not second["changed"]
    read = mod.run_module(params(record_path, state="read"), check_mode=False, run=fake_run())
    assert read["record"]["added"] == added


def test_errors(tmp_path: Path) -> None:
    record_path = tmp_path / "r.json"
    with pytest.raises(ValueError, match="does not exist"):
        mod.run_module(params(record_path, state="add"), check_mode=False, run=fake_run())
    with pytest.raises(RuntimeError, match="cannot list the installed packages"):
        mod.run_module(params(record_path), check_mode=False, run=fake_run(rc=1))
    with pytest.raises(ValueError, match="needs pkg_manager"):
        mod.run_module(params(record_path, pkg_manager=None), check_mode=False, run=fake_run())
    mod.run_module(params(record_path), check_mode=False, run=fake_run())
    with pytest.raises(ValueError, match="unknown kind"):
        mod.merge(json.loads(record_path.read_text()), {"files": ["x"]})
    with pytest.raises(ValueError, match="need exactly user, group"):
        mod.merge(json.loads(record_path.read_text()), {"memberships": [{"user": "x"}]})
    record_path.write_text("[]")
    with pytest.raises(ValueError, match="not a JSON object"):
        mod.run_module(params(record_path, state="read"), check_mode=False, run=fake_run())


def test_parsers_and_large_files(tmp_path: Path) -> None:
    rpm = "bash\t(none):5.2.26-6.fc44\nkernel\t0:6.16.9-200.fc44\n"
    assert mod.parse_packages(rpm) == {"bash": "5.2.26-6.fc44", "kernel": "0:6.16.9-200.fc44"}
    assert mod.parse_packages("pacman 7.0.0.r6-1\n") == {"pacman": "7.0.0.r6-1"}
    big = tmp_path / "big"
    big.write_bytes(b"x" * (mod.MAX_FILE_BYTES + 1))
    assert mod.read_file(str(big)) == f"<larger than {mod.MAX_FILE_BYTES} bytes, not kept>"
    binary = tmp_path / "bin"
    binary.write_bytes(b"\xff\xfe")
    assert mod.read_file(str(binary)) == "<not UTF-8 text, not kept>"
