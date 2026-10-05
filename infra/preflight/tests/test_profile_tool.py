"""Tests for the host profile helper used by bootstrap (profile_tool)."""

from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import tempfile
import unittest

from helpers import FIXTURES_DIR

from pokus_preflight import cli, profile_tool


def run_tool(*args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = profile_tool.main(list(args))
    return code, out.getvalue(), err.getvalue()


class ProfileToolTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def profile(self, fixture: str, name: str = "profile.json") -> str:
        """Run preflight on a fixture and return the path of its profile."""
        path = os.path.join(self.tmp, name)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            cli.main(["--root", os.path.join(FIXTURES_DIR, fixture), "--offline", "--output", path])
        return path

    def edit(self, path: str, **changes: object) -> None:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        data.update(changes)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)

    def test_get(self) -> None:
        path = self.profile("ubuntu2404-wsl2-gpu-nokvm")
        self.assertEqual(run_tool("get", path, "outcome"), (0, "degraded\n", ""))
        self.assertEqual(run_tool("get", path, "overrides.allow_dirty"), (0, "false\n", ""))
        self.assertEqual(run_tool("get", path, "repo_commit"), (0, "\n", ""))
        self.assertEqual(run_tool("get", path, "needs_action"), (0, "[]\n", ""))
        code, out, err = run_tool("get", path, "no.such.key")
        self.assertEqual((code, out), (3, ""))
        self.assertIn("no.such.key", err)

    def test_consent_degraded_lists_every_disabled_feature(self) -> None:
        path = self.profile("ubuntu2404-wsl2-gpu-nokvm")
        with open(path, encoding="utf-8") as handle:
            disabled = json.load(handle)["disabled_features"]
        self.assertTrue(disabled)
        code, out, _ = run_tool("consent", path, "degraded")
        self.assertEqual(code, 0)
        for item in disabled:
            self.assertIn(f"  - {item['feature']}: {item['impact']}", out)

    def test_consent_experimental_names_the_tier(self) -> None:
        path = self.profile("linuxmint22-baremetal")
        code, out, _ = run_tool("consent", path, "experimental")
        self.assertEqual(code, 0)
        self.assertIn("experimental tier", out)
        self.assertIn("OS tier: experimental", out)

    def test_diff(self) -> None:
        stored = self.profile("ubuntu2404-wsl2-gpu-nokvm", "stored.json")
        new = self.profile("ubuntu2404-wsl2-gpu-nokvm", "new.json")
        self.assertEqual(run_tool("diff", stored, new), (0, "", ""))
        with open(new, encoding="utf-8") as handle:
            features = json.load(handle)["features"]
        features["vm_targets"] = True
        self.edit(new, features=features, outcome="supported", generated_at="2030-01-01T00:00:00Z")
        code, out, _ = run_tool("diff", stored, new)
        self.assertEqual(code, 1)
        self.assertEqual(
            out.splitlines(),
            ["outcome: degraded -> supported", "features.vm_targets: false -> true"],
        )
        self.assertEqual(run_tool("diff", stored, os.path.join(self.tmp, "missing"))[0], 3)

    def test_override_rewrites_the_file(self) -> None:
        path = self.profile("ubuntu2404-wsl2-gpu-nokvm")
        self.assertEqual(run_tool("override", path, "accept_degraded"), (0, "", ""))
        self.assertEqual(run_tool("get", path, "overrides.accept_degraded")[1], "true\n")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            profile_tool.main(["override", path, "no_such_override"])

    def test_store_writes_profile_and_archive(self) -> None:
        path = self.profile("ubuntu2404-wsl2-gpu-nokvm")
        target = os.path.join(self.tmp, "etc", "host-profile.json")
        archive = os.path.join(self.tmp, "archive")
        code, out, _ = run_tool("store", path, "--profile-path", target, "--archive-dir", archive)
        self.assertEqual(code, 0)
        with open(path, encoding="utf-8") as handle:
            stamp = json.load(handle)["generated_at"].replace("-", "").replace(":", "")
        self.assertEqual(os.listdir(archive), [f"{stamp}.json"])
        self.assertIn(target, out)
        self.assertEqual(stat.S_IMODE(os.stat(target).st_mode), 0o644)

    def test_store_refuses_an_invalid_profile(self) -> None:
        path = self.profile("ubuntu2404-wsl2-gpu-nokvm")
        self.edit(path, outcome="maybe")
        target = os.path.join(self.tmp, "host-profile.json")
        code, _, err = run_tool("store", path, "--profile-path", target, "--archive-dir", self.tmp)
        self.assertEqual(code, 3)
        self.assertIn("schema", err)
        self.assertFalse(os.path.exists(target))


if __name__ == "__main__":
    unittest.main()
