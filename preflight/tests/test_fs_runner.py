"""Tests for the filesystem view and the runners."""

from __future__ import annotations

import datetime as dt
import sys
import unittest

from helpers import TempRootCase, runner

from pinka_preflight.fs import FileSystem
from pinka_preflight.runner import EXIT_NOT_FOUND, EXIT_TIMEOUT, FixtureRunner, RealRunner


class FileSystemTest(TempRootCase):
    def test_paths_cannot_escape_the_root(self) -> None:
        self.put("/etc/passwd", "fixture\n")
        fs = self.fs()
        self.assertEqual(fs.host_path("/../../etc/passwd"), fs.host_path("/etc/passwd"))
        self.assertEqual(fs.read_text("/../../../etc/passwd"), "fixture\n")

    def test_relative_paths_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.fs().read_text("etc/os-release")

    def test_real_root_maps_paths_unchanged(self) -> None:
        fs = FileSystem("/")
        self.assertFalse(fs.is_fixture)
        self.assertEqual(fs.host_path("/proc/../etc/os-release"), "/etc/os-release")

    def test_reads_and_missing_files(self) -> None:
        self.put("/etc/a", "one\ntwo\n")
        fs = self.fs()
        self.assertEqual(fs.read_first_line("/etc/a"), "one")
        self.assertIsNone(fs.read_text("/etc/missing"))
        self.assertIsNone(fs.read_first_line("/etc/missing"))
        self.assertTrue(fs.is_file("/etc/a"))
        self.assertTrue(fs.is_dir("/etc"))

    def test_metadata_devices_and_links(self) -> None:
        self.put("/dev/kvm")
        self.put("/dev/fake")
        self.meta(
            char_devices=["/dev/kvm", "/dev/absent"],
            rw_openable=["/dev/kvm"],
            symlinks={"/var/run/docker.sock": "/mnt/wsl/x.sock"},
        )
        fs = self.fs()
        self.assertTrue(fs.is_char_device("/dev/kvm"))
        self.assertFalse(fs.is_char_device("/dev/fake"))
        self.assertFalse(fs.is_char_device("/dev/absent"))
        self.assertTrue(fs.can_open_rw("/dev/kvm"))
        self.assertFalse(fs.can_open_rw("/dev/fake"))
        self.assertTrue(fs.is_symlink("/var/run/docker.sock"))
        self.assertTrue(fs.exists("/var/run/docker.sock"))
        self.assertEqual(fs.readlink("/var/run/docker.sock"), "/mnt/wsl/x.sock")
        self.assertIsNone(fs.readlink("/dev/kvm"))
        self.assertIn("docker.sock", fs.listdir("/var/run"))

    def test_glob_and_keep_files(self) -> None:
        self.put("/sys/bus/pci/devices/0000:01:00.0/vendor", "0x10de\n")
        self.put("/sys/bus/pci/devices/0000:02:00.0/vendor", "0x8086\n")
        self.put("/sys/module/overlay/.fixture-keep")
        fs = self.fs()
        self.assertEqual(
            fs.glob("/sys/bus/pci/devices/*/vendor"),
            [
                "/sys/bus/pci/devices/0000:01:00.0/vendor",
                "/sys/bus/pci/devices/0000:02:00.0/vendor",
            ],
        )
        self.assertEqual(fs.listdir("/sys/module/overlay"), [])
        self.assertTrue(fs.is_dir("/sys/module/overlay"))
        self.assertEqual(fs.glob("/nothing/*"), [])

    def test_invalid_metadata_is_rejected(self) -> None:
        self.put("/fixture.json", "[1]")
        with self.assertRaises(ValueError):
            FileSystem(self.root)


class FixtureRunnerTest(unittest.TestCase):
    def test_known_and_unknown_commands(self) -> None:
        r = runner({"echo hi": "hi\n", "false": ("", 1)})
        self.assertTrue(r.run(["echo", "hi"]).ok)
        self.assertEqual(r.run(["echo", "hi"]).stdout, "hi\n")
        self.assertEqual(r.run(["false"]).returncode, 1)
        self.assertEqual(r.run(["missing"]).returncode, EXIT_NOT_FOUND)

    def test_quoted_command_keys(self) -> None:
        r = runner({"docker context ls --format '{{.Name}}'": "default\n"})
        self.assertEqual(
            r.run(["docker", "context", "ls", "--format", "{{.Name}}"]).stdout, "default\n"
        )

    def test_scalar_sections_and_defaults(self) -> None:
        r = FixtureRunner(
            {
                "which": {"docker": "/usr/bin/docker"},
                "cpu_count": 8,
                "machine": "aarch64",
                "euid": 1000,
            }
        )
        self.assertEqual(r.which("docker"), "/usr/bin/docker")
        self.assertIsNone(r.which("podman"))
        self.assertEqual(r.cpu_count(), 8)
        self.assertEqual(r.machine(), "aarch64")
        self.assertEqual(r.euid(), 1000)
        empty = FixtureRunner({})
        self.assertIsNone(empty.cpu_count())
        self.assertEqual(empty.machine(), "x86_64")
        self.assertEqual(empty.now(), dt.datetime(2026, 10, 5, 9, 12, 44, tzinfo=dt.timezone.utc))

    def test_network_answers(self) -> None:
        r = FixtureRunner(
            {
                "dns": {"a.example": "192.0.2.1"},
                "https": {"https://a.example/": 404, "https://b.example/": "refused"},
            }
        )
        self.assertEqual(r.resolve("a.example"), "192.0.2.1")
        self.assertIsNone(r.resolve("c.example"))
        self.assertTrue(r.https_probe("https://a.example/").reachable)
        refused = r.https_probe("https://b.example/")
        self.assertFalse(refused.reachable)
        self.assertEqual(refused.error, "refused")
        self.assertFalse(r.https_probe("https://c.example/").reachable)

    def test_missing_commands_file_means_nothing_available(self) -> None:
        r = FixtureRunner.from_file("/nonexistent/commands.json")
        self.assertEqual(r.run(["uname"]).returncode, EXIT_NOT_FOUND)


class RealRunnerTest(unittest.TestCase):
    def test_runs_without_shell(self) -> None:
        res = RealRunner().run([sys.executable, "-c", "import sys; print('a b'); sys.exit(3)"])
        self.assertEqual(res.returncode, 3)
        self.assertEqual(res.stdout, "a b\n")

    def test_missing_program(self) -> None:
        self.assertEqual(RealRunner().run(["pinka-no-such-program-xyz"]).returncode, EXIT_NOT_FOUND)

    def test_timeout(self) -> None:
        res = RealRunner().run([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.3)
        self.assertEqual(res.returncode, EXIT_TIMEOUT)

    def test_environment_is_sanitized(self) -> None:
        res = RealRunner().run(
            [
                sys.executable,
                "-c",
                "import os; print(os.environ.get('LC_ALL'), 'HOME' in os.environ)",
            ]
        )
        self.assertEqual(res.stdout.strip(), "C False")

    def test_now_is_utc_without_microseconds(self) -> None:
        now = RealRunner().now()
        self.assertEqual(now.tzinfo, dt.timezone.utc)
        self.assertEqual(now.microsecond, 0)


if __name__ == "__main__":
    unittest.main()
