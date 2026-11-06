"""Tests for the pinka_install_record module.

The module runs on the target's Python, which is 3.9 in the check-mode
containers of the Red Hat family, so these tests are unittest-style and also
run on CPython 3.9 (``make test-py39``).
"""

from __future__ import annotations

import grp
import json
import os
import pwd
import stat
import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from unittest import mock

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
        # Never the host's own: a test reads only what it wrote.
        "kept_accounts": str(path.parent / "kept-accounts.json"),
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


# The host's accounts and groups as pwd and grp give them, for the kept-accounts tests.
USERS = [
    ("root", 0, 0, "/root", "/bin/bash"),
    ("owner1", 1000, 1000, "/home/owner1", "/bin/bash"),
    ("researcher1", 1001, 1001, "/home/researcher1", "/usr/sbin/nologin"),
    ("pinka-build", 990, 990, "/var/lib/pinka/pinka-build", "/usr/sbin/nologin"),
    ("refplat-api", 60578, 985, "/var/lib/pinka/refplat-api", "/usr/sbin/nologin"),
    ("reused", 1600, 1600, "/home/reused", "/bin/bash"),
]
GROUPS = [
    ("root", 0, []),
    ("owner1", 1000, []),
    ("pinka", 1002, ["owner1"]),
    ("researcher1", 1001, []),
    ("docker", 983, []),
    ("refplat-db", 984, []),
]
SHADOW_TEXT = (
    "root:*:20000:0:99999:7:::\n"
    "owner1:$6$example$hash:20000:0:99999:7:::\n"
    "researcher1:!$6$example$hash1:20000:0:99999:7::1:\n"
    "pinka-build:!:20000:::::1:\n"
    "refplat-api:!:20000:::::1:\n"
    "reused:$6$example$hash2:20000:0:99999:7:::\n"
)
LOCK = {"shell": "/usr/sbin/nologin", "expire": "1"}
KEPT: dict[str, Any] = {
    "schema_version": 1,
    "lab_root": "/srv/pinka",
    "lock": LOCK,
    "accounts": [
        {
            "name": "researcher1",
            "uid": "1001",
            "gid": "1001",
            "shell": "/bin/bash",
            "expire": "",
            "password_locked": True,
        },
        {
            "name": "pinka-build",
            "uid": "990",
            "gid": "990",
            "shell": "/usr/sbin/nologin",
            "expire": "",
            "password_locked": False,
        },
        {
            "name": "refplat-api",
            "uid": "60578",
            "gid": "985",
            "shell": "/usr/sbin/nologin",
            "expire": "1",
            "password_locked": False,
        },
        # Another account has the name now, and one is gone.
        {
            "name": "reused",
            "uid": "1500",
            "gid": "1500",
            "shell": "/bin/bash",
            "expire": "",
            "password_locked": True,
        },
        {
            "name": "gone",
            "uid": "1700",
            "gid": "1700",
            "shell": "/bin/bash",
            "expire": "",
            "password_locked": True,
        },
    ],
    "groups": [
        {"name": "pinka", "gid": "1002", "component": False},
        {"name": "researcher1", "gid": "1001", "component": False},
        {"name": "docker", "gid": "983", "component": True},
        {"name": "refplat-db", "gid": "999", "component": False},
    ],
}


class KeptAccountsTest(unittest.TestCase):
    """init takes over what an uninstall kept; unlock undoes the lock of the accounts named."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.kept = self.tmp / "kept-accounts.json"
        self.kept.write_text(json.dumps(KEPT))
        self.kept.chmod(0o600)
        self.record_path = self.tmp / "install-record.json"
        shadow = self.tmp / "shadow"
        shadow.write_text(SHADOW_TEXT)
        users = [pwd.struct_passwd((n, "x", u, g, "", d, s)) for n, u, g, d, s in USERS]
        groups = [grp.struct_group((n, "x", g, m)) for n, g, m in GROUPS]
        by_user = {u.pw_name: u for u in users}
        by_group = {g.gr_name: g for g in groups}
        for patch in (
            mock.patch.object(mod, "SHADOW", str(shadow)),
            mock.patch.object(pwd, "getpwall", return_value=users),
            mock.patch.object(pwd, "getpwnam", side_effect=lambda n: by_user[n]),
            mock.patch.object(grp, "getgrall", return_value=groups),
            mock.patch.object(grp, "getgrnam", side_effect=lambda n: by_group[n]),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_state(
        self, state: str, *, check_mode: bool = False, rc: int = 0, **overrides: Any
    ) -> tuple[dict[str, Any], list[list[str]]]:
        """Run the module; returns the result and the usermod calls."""
        run = fake_run()
        usermods: list[list[str]] = []

        def with_usermod(argv: Sequence[str]) -> tuple[int, str, str]:
            if argv[0] == "usermod":
                usermods.append(list(argv))
                return rc, "", "" if rc == 0 else "usermod: user is busy"
            return run(argv)  # type: ignore[no-any-return]

        result = mod.run_module(
            params(self.record_path, state=state, kept_accounts=str(self.kept), **overrides),
            check_mode=check_mode,
            run=with_usermod,
        )
        return result, usermods

    def record(self) -> dict[str, Any]:
        return dict(json.loads(self.record_path.read_text()))

    def test_init_takes_over_the_kept_accounts_and_unlocks_none(self) -> None:
        result, usermods = self.run_state("init")
        self.assertEqual(usermods, [])
        self.assertEqual(
            result["taken_over"],
            {
                "accounts": ["pinka-build", "refplat-api", "researcher1"],
                "groups": ["pinka", "researcher1"],
                "component_groups": ["docker"],
            },
        )
        record = self.record()
        before = record["preexisting"]
        # Taken over: pinka's again, not what existed before pinka.
        self.assertEqual(before["users"], ["owner1", "reused", "root"])
        self.assertEqual(sorted(before["groups"]), ["owner1", "refplat-db", "root"])
        self.assertEqual(record["added"]["accounts"], ["pinka-build", "refplat-api", "researcher1"])
        # A group of Docker's package only leaves what existed before pinka.
        self.assertEqual(record["added"]["groups"], ["pinka", "researcher1"])
        # The notes of the accounts taken over wait in the record for the roles.
        self.assertEqual(record["kept_accounts"]["lock"], LOCK)
        self.assertEqual(
            [e["name"] for e in record["kept_accounts"]["accounts"]],
            ["pinka-build", "refplat-api", "researcher1"],
        )
        self.assertEqual(record["kept_accounts"]["accounts"][2], KEPT["accounts"][0])
        self.assertFalse(self.kept.exists())
        # Never rewritten, and nothing left to take over.
        again, _ = self.run_state("init")
        self.assertFalse(again["changed"])
        self.assertEqual(self.record(), record)

    def test_unlock_undoes_the_lock_of_the_named_accounts_only(self) -> None:
        self.run_state("init")
        result, usermods = self.run_state("unlock", accounts=["researcher1", "pinka-build", "x"])
        self.assertEqual(
            usermods,
            [
                ["usermod", "-s", "/usr/sbin/nologin", "-e", "", "pinka-build"],
                ["usermod", "-s", "/bin/bash", "-e", "", "-U", "researcher1"],
            ],
        )
        self.assertTrue(result["changed"])
        self.assertEqual(result["unlocked"], ["pinka-build", "researcher1"])
        # The named notes leave the record; the platform's account no role named stays locked.
        notes = self.record()["kept_accounts"]
        self.assertEqual([e["name"] for e in notes["accounts"]], ["refplat-api"])
        again, usermods = self.run_state("unlock", accounts=["researcher1", "pinka-build"])
        self.assertEqual((again["changed"], usermods), (False, []))
        # Its own lock was its state before: nothing to undo, and the note goes.
        result, usermods = self.run_state("unlock", accounts=["refplat-api"])
        self.assertEqual((result["changed"], result["unlocked"], usermods), (True, [], []))
        self.assertNotIn("kept_accounts", self.record())

    def test_a_lock_someone_changed_or_another_account_is_left_alone(self) -> None:
        self.run_state("init")
        Path(mod.SHADOW).write_text(
            SHADOW_TEXT.replace(
                "researcher1:!$6$example$hash1:20000:0:99999:7::1:",
                "researcher1:!$6$example$hash1:20000:0:99999:7::30000:",
            )
        )
        # pinka-build carries the lock, but under another UID than the note's:
        # another account with the name.
        record = self.record()
        for entry in record["kept_accounts"]["accounts"]:
            if entry["name"] == "pinka-build":
                entry["uid"] = "991"
        mod.write_record(str(self.record_path), record)
        result, usermods = self.run_state("unlock", accounts=["researcher1", "pinka-build"])
        self.assertEqual((result["unlocked"], usermods), ([], []))
        self.assertEqual(
            [e["name"] for e in self.record()["kept_accounts"]["accounts"]], ["refplat-api"]
        )

    def test_an_account_gone_since_loses_its_note(self) -> None:
        self.run_state("init")
        with mock.patch.object(pwd, "getpwnam", side_effect=KeyError("researcher1")):
            result, usermods = self.run_state("unlock", accounts=["researcher1"])
        self.assertEqual((result["changed"], result["unlocked"], usermods), (True, [], []))
        self.assertEqual(
            [e["name"] for e in self.record()["kept_accounts"]["accounts"]],
            ["pinka-build", "refplat-api"],
        )

    def test_check_mode_changes_nothing(self) -> None:
        result, usermods = self.run_state("init", check_mode=True)
        self.assertEqual(usermods, [])
        self.assertEqual(
            result["taken_over"]["accounts"], ["pinka-build", "refplat-api", "researcher1"]
        )
        self.assertNotIn("researcher1", result["record"]["preexisting"]["users"])
        self.assertTrue(self.kept.exists())
        self.assertFalse(self.record_path.exists())
        # Without the record a check run of unlock has nothing to say.
        result, usermods = self.run_state("unlock", check_mode=True, accounts=["researcher1"])
        self.assertEqual((result["changed"], result["unlocked"], usermods), (False, [], []))
        # With it, it names what it would undo and changes nothing.
        self.run_state("init")
        written = self.record_path.read_text()
        result, usermods = self.run_state("unlock", check_mode=True, accounts=["researcher1"])
        self.assertEqual((result["unlocked"], usermods), (["researcher1"], []))
        self.assertEqual(self.record_path.read_text(), written)

    def test_a_failed_undo_keeps_the_note(self) -> None:
        self.run_state("init")
        with self.assertRaisesRegex(
            RuntimeError,
            "cannot undo the lock of the kept account researcher1: usermod: user is busy",
        ):
            self.run_state("unlock", rc=10, accounts=["researcher1"])
        self.assertIn(
            "researcher1", [e["name"] for e in self.record()["kept_accounts"]["accounts"]]
        )
        with self.assertRaisesRegex(ValueError, "does not exist"):
            self.record_path.unlink()
            self.run_state("unlock", accounts=["researcher1"])

    def test_an_existing_record_gets_what_was_kept(self) -> None:
        mod.write_record(
            str(self.record_path),
            {
                "schema_version": 1,
                "preexisting": {"users": ["root"], "groups": {"root": []}},
                "added": {**mod.empty_added(), "accounts": ["pinka-build"]},
                # An older note of pinka-build, unlike the file's: the file wins.
                "kept_accounts": {
                    "lock": LOCK,
                    "accounts": [{**KEPT["accounts"][1], "shell": "/bin/sh"}],
                },
            },
        )
        result, usermods = self.run_state("init")
        self.assertTrue(result["changed"])
        self.assertEqual(usermods, [])
        record = self.record()
        self.assertEqual(record["preexisting"], {"users": ["root"], "groups": {"root": []}})
        self.assertEqual(record["added"]["accounts"], ["pinka-build", "refplat-api", "researcher1"])
        self.assertEqual(record["added"]["groups"], ["pinka", "researcher1"])
        self.assertEqual(
            [e["name"] for e in record["kept_accounts"]["accounts"]],
            ["pinka-build", "refplat-api", "researcher1"],
        )
        self.assertEqual(record["kept_accounts"]["accounts"][0], KEPT["accounts"][1])
        self.assertFalse(self.kept.exists())

    def test_a_note_others_could_write_is_refused(self) -> None:
        for mode in (0o620, 0o602):
            self.kept.chmod(mode)
            with self.assertRaisesRegex(ValueError, "is no regular file of root's"):
                self.run_state("init")
        self.kept.chmod(0o600)
        target = self.tmp / "elsewhere.json"
        self.kept.rename(target)
        self.kept.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "is no regular file of root's"):
            self.run_state("init")
        self.assertFalse(self.record_path.exists())
        with mock.patch.object(os, "geteuid", return_value=4242):
            self.kept.unlink()
            target.rename(self.kept)
            with self.assertRaisesRegex(ValueError, "is no regular file of root's"):
                self.run_state("init")

    def test_no_note_no_change(self) -> None:
        self.kept.unlink()
        result, _ = self.run_state("init")
        self.assertEqual(
            result["taken_over"], {"accounts": [], "groups": [], "component_groups": []}
        )
        self.assertEqual(result["record"]["added"]["accounts"], [])
        self.assertNotIn("kept_accounts", result["record"])
        self.kept.write_text("[]")
        self.kept.chmod(0o600)
        self.record_path.unlink()
        with self.assertRaisesRegex(ValueError, "not a JSON object"):
            self.run_state("init")
