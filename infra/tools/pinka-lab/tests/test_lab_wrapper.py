"""Tests for the POSIX shell wrapper installed as /usr/local/bin/pinka."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest

from pinka_lab import __version__

HERE = os.path.dirname(os.path.abspath(__file__))
PACKAGE_DIR = os.path.dirname(HERE)
PREFLIGHT_DIR = os.path.join(os.path.dirname(os.path.dirname(PACKAGE_DIR)), "preflight")
WRAPPER = os.path.join(PACKAGE_DIR, "pinka")
SH = shutil.which("sh") or "/bin/sh"


@unittest.skipUnless(shutil.which("sh"), "needs a POSIX shell")
class WrapperTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        lib = self.tmp.name
        os.symlink(PACKAGE_DIR, os.path.join(lib, "pinka-lab"))
        os.symlink(PREFLIGHT_DIR, os.path.join(lib, "preflight"))
        self.env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PINKA_LAB_LIB": lib}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_wrapper(self, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - the wrapper under test
            [SH, WRAPPER, *args],
            capture_output=True,
            text=True,
            env=dict(self.env, **env),
            cwd=self.tmp.name,
            check=False,
        )

    def test_version(self) -> None:
        done = self.run_wrapper("--version")
        self.assertEqual((done.returncode, done.stdout.strip()), (0, f"pinka {__version__}"))

    def test_arguments_reach_the_cli(self) -> None:
        done = self.run_wrapper("--config", "/nonexistent/lab.json", "status")
        self.assertEqual(done.returncode, 1)
        self.assertIn("/nonexistent/lab.json does not exist", done.stderr)

    def test_python_path_variables_are_ignored(self) -> None:
        bad = os.path.join(self.tmp.name, "bad")
        os.makedirs(os.path.join(bad, "pinka_lab"))
        with open(os.path.join(bad, "pinka_lab", "__init__.py"), "w", encoding="utf-8") as f:
            f.write("raise SystemExit('shadowed')\n")
        done = self.run_wrapper("--version", PYTHONPATH=bad)
        self.assertEqual(done.returncode, 0, done.stderr)


if __name__ == "__main__":
    unittest.main()
