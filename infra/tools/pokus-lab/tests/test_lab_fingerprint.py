"""Tests for the environment fingerprint (contracts.md C9c)."""

from __future__ import annotations

import json
import os
import unittest
import uuid
from typing import Any

from labfakes import DIGEST, PROFILE, FakeSystem, lab

from pokus_lab import fingerprint
from pokus_preflight.schema_check import validate

SCHEMA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "schema", "fingerprint.schema.json"
)
CACHE = "/var/lib/pokus/fingerprint/current.json"
PROFILE_PATH = "/etc/pokus/host-profile.json"


def host(**extra_files: str) -> FakeSystem:
    files = {
        PROFILE_PATH: json.dumps(PROFILE),
        "/etc/pokus/bootstrap-state.json": json.dumps(
            {"repo_commit": "c" * 40, "infra_tree": "d" * 40, "infra_dirty": False}
        ),
        "/srv/pokus/deploy/platform/current/DEPLOYED_FROM.json": json.dumps(
            {
                "component": "platform",
                "tag": "platform-v0.1.0",
                "commit": "e" * 40,
                "tree": "f" * 40,
            }
        ),
        "/proc/sys/kernel/random/boot_id": "00000000-0000-0000-0000-000000000000\n",
    }
    files.update(extra_files)
    return FakeSystem(
        {
            "incus query /1.0": (0, {"environment": {"server_version": "6.0.6"}}),
            "docker version --format {{.Server.Version}}": (0, "29.0.0\n"),
            "tailscale version": (0, "1.90.0\n  tailscale commit: x\n"),
            "docker inspect --format {{.Config.Image}} pokus-postgres": (
                0,
                "docker.io/library/postgres@" + DIGEST + "\n",
            ),
            "incus image list --format json": (
                0,
                [{"fingerprint": "f" * 64, "aliases": [{"name": "_base/1.0.0"}]}],
            ),
        },
        files=files,
    )


class CanonicalTest(unittest.TestCase):
    def test_canonical_and_hash(self) -> None:
        self.assertEqual(fingerprint.canonical({"b": 1, "a": "ü"}), '{"a":"ü","b":1}')
        a = fingerprint.fingerprint_hash({"x": 1, "y": [1, 2]})
        b = fingerprint.fingerprint_hash({"y": [1, 2], "x": 1})
        self.assertEqual(a, b)
        self.assertRegex(a, r"^sha256:[0-9a-f]{64}$")
        self.assertNotEqual(a, fingerprint.fingerprint_hash({"x": 1, "y": [2, 1]}))


class ComputeTest(unittest.TestCase):
    def test_contents(self) -> None:
        result = fingerprint.compute(host(), lab())
        self.assertEqual(
            sorted(result),
            ["boot_id", "computed_at", "fingerprint", "fingerprint_schema_version", "hash"],
        )
        fp = result["fingerprint"]
        self.assertEqual(result["hash"], fingerprint.fingerprint_hash(fp))
        self.assertEqual(fp["machine"], "pokus-lab")
        self.assertEqual(fp["host"]["os_id"], "ubuntu")
        self.assertEqual(fp["hardware"]["gpu"]["vram_gib"], 12)
        software = fp["software"]
        self.assertEqual(software["versions"]["incus"], "6.0.6")
        self.assertEqual(software["versions"]["docker"], "29.0.0")
        self.assertEqual(software["versions"]["tailscale"], "1.90.0")
        self.assertIsNone(software["versions"]["containerd"])
        self.assertEqual(
            software["containers"]["pokus-postgres"], "docker.io/library/postgres@" + DIGEST
        )
        self.assertEqual(software["golden_images"], {"_base/1.0.0": "f" * 64})
        config = fp["config"]
        self.assertEqual(config["infra_commit"], "c" * 40)
        self.assertEqual(config["versions_lock_sha256"], "0" * 64)
        self.assertEqual(config["platform"]["tag"], "platform-v0.1.0")
        self.assertIsNone(config["catalog"])
        self.assertNotIn("wsl", fp)
        self.assertEqual(result["computed_at"], "2026-01-01T12:00:00Z")

    def test_volatile_fields_are_not_hashed(self) -> None:
        one = fingerprint.compute(host(), lab())
        two = fingerprint.compute(
            host(**{"/proc/sys/kernel/random/boot_id": str(uuid.UUID(int=1))}),
            lab(),
        )
        self.assertEqual(one["hash"], two["hash"])
        self.assertNotEqual(one["boot_id"], two["boot_id"])

    def test_changes_change_the_hash(self) -> None:
        base = fingerprint.compute(host(), lab())["hash"]
        other = fingerprint.compute(host(), lab(fingerprint__host_vars_sha256="2" * 64))["hash"]
        self.assertNotEqual(base, other)

    def test_wsl(self) -> None:
        profile = dict(PROFILE, provider="wsl2")
        system = host(
            **{
                PROFILE_PATH: json.dumps(profile),
                "/etc/wsl.conf": "[boot]\nsystemd=true\n[interop]\nenabled=false\n",
                "/proc/version": "Linux version 6.6.0-microsoft-standard-WSL2\n",
            }
        )
        wsl = fingerprint.compute(system, lab())["fingerprint"]["wsl"]
        self.assertEqual(
            wsl["wsl_conf"], {"boot": {"systemd": "true"}, "interop": {"enabled": "false"}}
        )
        self.assertIn("WSL2", wsl["kernel_string"])
        self.assertIsNone(wsl["windows_build"])

    def test_missing_profile(self) -> None:
        with self.assertRaises(fingerprint.FingerprintError):
            fingerprint.compute(FakeSystem(), lab())


class SchemaTest(unittest.TestCase):
    def setUp(self) -> None:
        with open(SCHEMA_PATH, encoding="utf-8") as handle:
            self.schema = json.load(handle)

    def test_documents_match_the_schema(self) -> None:
        wsl_profile = dict(PROFILE, provider="wsl2")
        for system in (
            host(),
            host(
                **{PROFILE_PATH: json.dumps(wsl_profile), "/etc/wsl.conf": "[boot]\nsystemd=true\n"}
            ),
            FakeSystem(files={PROFILE_PATH: json.dumps({"provider": "baremetal"})}),
        ):
            document = fingerprint.compute(system, lab())
            self.assertEqual(validate(document, self.schema, self.schema), [])

    def test_schema_rejects_drift(self) -> None:
        document = fingerprint.compute(host(), lab())
        document["fingerprint"]["software"]["versions"]["new_tool"] = "1.0"
        document["hash"] = "md5:0"
        errors = validate(document, self.schema, self.schema)
        self.assertEqual(len(errors), 2, errors)


class CurrentTest(unittest.TestCase):
    def test_root_computes_and_caches(self) -> None:
        system = host()
        result = fingerprint.current(system, lab())
        text, mode = system.written[CACHE]
        self.assertEqual(mode, 0o644)
        self.assertEqual(json.loads(text), result)

    def test_other_users_read_the_cache(self) -> None:
        cached: dict[str, Any] = {"hash": "sha256:" + "0" * 64, "computed_at": "x"}
        system = FakeSystem(files={CACHE: json.dumps(cached)}, euid=1000)
        self.assertEqual(fingerprint.current(system, lab()), cached)
        self.assertEqual(system.calls, [])
        self.assertEqual(system.written, {})

    def test_other_users_without_cache(self) -> None:
        with self.assertRaisesRegex(fingerprint.FingerprintError, "sudo pokus-lab fingerprint"):
            fingerprint.current(FakeSystem(euid=1000), lab())


class DiffTest(unittest.TestCase):
    def test_diff(self) -> None:
        old = {"a": 1, "b": {"c": "x", "d": [1]}, "e": None}
        new = {"a": 1, "b": {"c": "y", "d": [1]}, "f": True}
        self.assertEqual(fingerprint.diff(old, new), ['b.c: "x" -> "y"', "f: null -> true"])
        self.assertEqual(fingerprint.diff(old, old), [])


if __name__ == "__main__":
    unittest.main()
