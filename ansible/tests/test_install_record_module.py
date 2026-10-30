"""Tests for the pinka_install_record module.

The module runs on the target's Python, which is 3.9 in the check-mode
containers of the Red Hat family, so these tests are unittest-style and also
run on CPython 3.9 (``make test-py39``).
"""

from __future__ import annotations

import json
import stat
import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pinka_install_record as mod

DPKG = "bash\t5.2.21-2ubuntu4\ncurl\t8.5.0-2ubuntu10.6\n"
ENABLED = "ssh.service enabled enabled\ndocker.service enabled enabled\n"
MASKED = "sleep.target masked enabled\n"
# The host profile's access.
ACCESS = {
    "host": "shared",
    "firewall_manager": "none",
    "session": {"via": "ssh", "address": "192.0.2.10", "port": 22, "user": "owner1"},
    "listeners": [{"proto": "tcp", "address": "192.0.2.10", "port": 8080, "process": "web"}],
}


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
        "access": ACCESS,
        "pkg_manager": "apt",
        "managed_files": [],
        "bootstrap_state": str(path.parent / "bootstrap-state.json"),
        "added": {},
    }
    base.update(overrides)
    return base


class InstallRecordTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_init_writes_once_with_what_existed(self) -> None:
        record_path = self.tmp / "lib" / "install-record.json"
        shared = self.tmp / "20auto-upgrades"
        shared.write_text('APT::Periodic::Unattended-Upgrade "0";\n')
        missing = self.tmp / "absent.conf"
        p = params(record_path, managed_files=[str(shared), str(missing)])
        result = mod.run_module(p, check_mode=False, run=fake_run())
        self.assertTrue(result["changed"] and result["exists"])
        record = json.loads(record_path.read_text())
        self.assertEqual(stat.S_IMODE(record_path.stat().st_mode), 0o600)
        before = record["preexisting"]
        self.assertIs(record["legacy"], False)
        self.assertTrue(record["created_at"].endswith("Z"))
        self.assertEqual(before["components"]["docker"]["origin"], "pre-existing")
        self.assertEqual(before["access"], ACCESS)
        self.assertEqual(
            before["packages"], {"bash": "5.2.21-2ubuntu4", "curl": "8.5.0-2ubuntu10.6"}
        )
        self.assertEqual(before["units_enabled"], ["docker.service", "ssh.service"])
        self.assertEqual(before["units_masked"], ["sleep.target"])
        self.assertEqual(before["files"], {str(shared): 'APT::Periodic::Unattended-Upgrade "0";\n'})
        self.assertIn("root", before["users"])
        self.assertEqual(record["added"], mod.empty_added())
        # Never rewritten: a second init changes nothing.
        again = mod.run_module(p, check_mode=False, run=fake_run(packages="other\t1\n"))
        self.assertFalse(again["changed"])
        self.assertEqual(json.loads(record_path.read_text()), record)

    def test_check_mode_writes_nothing(self) -> None:
        record_path = self.tmp / "install-record.json"
        result = mod.run_module(params(record_path), check_mode=True, run=fake_run())
        self.assertTrue(result["changed"] and not result["exists"])
        self.assertEqual(result["record"]["preexisting"]["packages"]["bash"], "5.2.21-2ubuntu4")
        self.assertFalse(record_path.exists())
        added = mod.run_module(
            params(record_path, state="add", added={"packages": ["incus"]}),
            check_mode=True,
            run=fake_run(),
        )
        self.assertEqual(added, {"changed": False, "record": {}, "exists": False})

    def test_a_host_pinka_provisioned_before_is_legacy(self) -> None:
        state = self.tmp / "bootstrap-state.json"
        # A run that stopped before the play changed nothing; one that reached
        # it provisioned the host; an unreadable state counts as provisioned.
        for text, legacy in (
            ('{"stage": "preflight"}', False),
            ('{"stage": "prepreflight"}', False),
            ('{"stage": "ansible"}', True),
            ('{"stage": "doctor"}', True),
            ("not json", True),
        ):
            state.write_text(text)
            record = self.tmp / f"r-{legacy}-{len(text)}.json"
            result = mod.run_module(params(record), check_mode=False, run=fake_run())
            self.assertIs(result["record"]["legacy"], legacy, text)

    def test_an_original_kept_before_the_record_is_recorded(self) -> None:
        # Pre-preflight edits /etc/wsl.conf before the record exists and keeps
        # the original, or notes that there was none.
        conf = self.tmp / "wsl.conf"
        conf.write_text("[boot]\nsystemd=true\n")
        kept = self.tmp / "originals" / "wsl.conf"
        kept.parent.mkdir()
        kept.write_text("[network]\nhostname=example\n")
        p = params(
            self.tmp / "r1.json", managed_files=[str(conf)], originals={str(conf): str(kept)}
        )
        files = mod.run_module(p, check_mode=False, run=fake_run())["record"]["preexisting"][
            "files"
        ]
        self.assertEqual(files, {str(conf): "[network]\nhostname=example\n"})
        kept.unlink()
        Path(f"{kept}.absent").write_text("")
        p = params(
            self.tmp / "r2.json", managed_files=[str(conf)], originals={str(conf): str(kept)}
        )
        files = mod.run_module(p, check_mode=False, run=fake_run())["record"]["preexisting"][
            "files"
        ]
        self.assertEqual(files, {})
        # Without a kept original the file itself is recorded.
        Path(f"{kept}.absent").unlink()
        p = params(
            self.tmp / "r3.json", managed_files=[str(conf)], originals={str(conf): str(kept)}
        )
        files = mod.run_module(p, check_mode=False, run=fake_run())["record"]["preexisting"][
            "files"
        ]
        self.assertEqual(files, {str(conf): "[boot]\nsystemd=true\n"})

    def test_add_keeps_only_what_is_new(self) -> None:
        record_path = self.tmp / "r.json"
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
            "closed_access": ["tcp/8080", "install_path"],
        }
        first = mod.run_module(
            params(record_path, state="add", added=items), check_mode=False, run=fake_run()
        )
        self.assertTrue(first["changed"])
        added = json.loads(record_path.read_text())["added"]
        self.assertEqual(added["components"], {"docker": True})
        # curl existed before pinka: not pinka's.
        self.assertEqual(added["packages"], ["containerd.io", "docker-ce"])
        self.assertEqual(added["memberships"], [{"user": "researcher1", "group": "pinka"}])
        self.assertEqual(added["closed_access"], ["install_path", "tcp/8080"])
        # The same items again: nothing new, nothing written.
        second = mod.run_module(
            params(record_path, state="add", added=items), check_mode=False, run=fake_run()
        )
        self.assertFalse(second["changed"])
        read = mod.run_module(params(record_path, state="read"), check_mode=False, run=fake_run())
        self.assertEqual(read["record"]["added"], added)

    def test_changed_settings_keep_their_first_value(self) -> None:
        # Uninstall sets back what pinka changed: the value
        # before pinka's first change, never one pinka set itself.
        record_path = self.tmp / "r.json"
        mod.run_module(params(record_path), check_mode=False, run=fake_run())
        first = {
            "changed": {"timezone": "Europe/Example", "firewalld:zone-of:tailscale0": None},
            "repo_keys": ["0000000000000000000000000000000000000001"],
            "incus_objects": ["project:user-1000", "network:incusbr-1000"],
        }
        result = mod.run_module(
            params(record_path, state="add", added=first), check_mode=False, run=fake_run()
        )
        self.assertTrue(result["changed"])
        later = {"changed": {"timezone": "Etc/UTC", "sysctl:net.ipv4.ip_forward": "0"}}
        mod.run_module(
            params(record_path, state="add", added=later), check_mode=False, run=fake_run()
        )
        added = json.loads(record_path.read_text())["added"]
        self.assertEqual(
            added["changed"],
            {
                "firewalld:zone-of:tailscale0": None,
                "sysctl:net.ipv4.ip_forward": "0",
                "timezone": "Europe/Example",
            },
        )
        self.assertEqual(added["repo_keys"], ["0000000000000000000000000000000000000001"])
        self.assertEqual(added["incus_objects"], ["network:incusbr-1000", "project:user-1000"])
        again = mod.run_module(
            params(record_path, state="add", added=later), check_mode=False, run=fake_run()
        )
        self.assertFalse(again["changed"])
        record = json.loads(record_path.read_text())
        for items, message in (
            ({"changed": ["timezone"]}, "needs a mapping"),
            ({"changed": {"timezone": 1}}, "must be text or null"),
        ):
            with self.assertRaisesRegex(ValueError, message):
                mod.merge(record, items)

    def test_init_keeps_the_mode_and_owner_of_shared_files(self) -> None:
        shared = self.tmp / "sshd_config"
        shared.write_text("Port 22\n")
        shared.chmod(0o600)
        record_path = self.tmp / "r.json"
        result = mod.run_module(
            params(record_path, managed_files=[str(shared)]), check_mode=False, run=fake_run()
        )
        modes = result["record"]["preexisting"]["file_modes"]
        self.assertEqual(modes[str(shared)]["mode"], "0600")
        self.assertEqual(set(modes[str(shared)]), {"mode", "owner", "group"})

    def test_remove_takes_only_listeners_reachable_again(self) -> None:
        record_path = self.tmp / "r.json"
        mod.run_module(params(record_path), check_mode=False, run=fake_run())
        closed = {"closed_access": ["tcp/8080", "udp/5353", "install_path"], "accounts": ["x"]}
        mod.run_module(
            params(record_path, state="add", added=closed), check_mode=False, run=fake_run()
        )
        again = {"closed_access": ["udp/5353", "tcp/9999"]}
        first = mod.run_module(
            params(record_path, state="remove", added=again), check_mode=False, run=fake_run()
        )
        self.assertTrue(first["changed"])
        added = json.loads(record_path.read_text())["added"]
        self.assertEqual(added["closed_access"], ["install_path", "tcp/8080"])
        self.assertEqual(added["accounts"], ["x"])
        second = mod.run_module(
            params(record_path, state="remove", added=again), check_mode=False, run=fake_run()
        )
        self.assertFalse(second["changed"])
        for items, message in (
            ({"accounts": ["x"]}, "takes only closed_access"),
            ({"closed_access": ["install_path"]}, "stays closed"),
        ):
            with self.assertRaisesRegex(ValueError, message):
                mod.run_module(
                    params(record_path, state="remove", added=items),
                    check_mode=False,
                    run=fake_run(),
                )
        self.assertEqual(json.loads(record_path.read_text())["added"], added)

    def test_errors(self) -> None:
        record_path = self.tmp / "r.json"
        with self.assertRaisesRegex(ValueError, "does not exist"):
            mod.run_module(params(record_path, state="add"), check_mode=False, run=fake_run())
        with self.assertRaisesRegex(RuntimeError, "cannot list the installed packages"):
            mod.run_module(params(record_path), check_mode=False, run=fake_run(rc=1))
        with self.assertRaisesRegex(ValueError, "needs pkg_manager"):
            mod.run_module(params(record_path, pkg_manager=None), check_mode=False, run=fake_run())
        mod.run_module(params(record_path), check_mode=False, run=fake_run())
        with self.assertRaisesRegex(ValueError, "unknown kind"):
            mod.merge(json.loads(record_path.read_text()), {"files": ["x"]})
        with self.assertRaisesRegex(ValueError, "need exactly user, group"):
            mod.merge(json.loads(record_path.read_text()), {"memberships": [{"user": "x"}]})
        record_path.write_text("[]")
        with self.assertRaisesRegex(ValueError, "not a JSON object"):
            mod.run_module(params(record_path, state="read"), check_mode=False, run=fake_run())

    def test_parsers_and_large_files(self) -> None:
        rpm = "bash\t(none):5.2.26-6.fc44\nkernel\t0:6.16.9-200.fc44\n"
        self.assertEqual(
            mod.parse_packages(rpm), {"bash": "5.2.26-6.fc44", "kernel": "0:6.16.9-200.fc44"}
        )
        self.assertEqual(mod.parse_packages("pacman 7.0.0.r6-1\n"), {"pacman": "7.0.0.r6-1"})
        big = self.tmp / "big"
        big.write_bytes(b"x" * (mod.MAX_FILE_BYTES + 1))
        self.assertEqual(
            mod.read_file(str(big)), f"<larger than {mod.MAX_FILE_BYTES} bytes, not kept>"
        )
        binary = self.tmp / "bin"
        binary.write_bytes(b"\xff\xfe")
        self.assertEqual(mod.read_file(str(binary)), "<not UTF-8 text, not kept>")

    def test_the_timestamp_is_utc_with_z(self) -> None:
        self.assertRegex(mod.utc_now(), r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
