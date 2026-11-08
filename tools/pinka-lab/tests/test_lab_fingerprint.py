"""Tests for the environment fingerprint (contracts.md C9c)."""

from __future__ import annotations

import json
import os
import unittest
import uuid
from typing import Any

from labfakes import DIGEST, PROFILE, FakeSystem, lab

from pinka_lab import fingerprint
from pinka_preflight.schema_check import validate

# The fingerprint's schema is published with the contract.
SCHEMA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "contract",
    "records",
    "fingerprint.schema.json",
)
CACHE = "/var/lib/pinka/fingerprint/current.json"
PROFILE_PATH = "/etc/pinka/host-profile.json"


PACKAGES = [
    {"name": "libc6", "version": "2.0-1", "architecture": "amd64"},
    {"name": "tzdata", "version": "3.0-1", "architecture": "all"},
]
RECORD_PATH = "/srv/pinka/images/_base/1.0.0/build.json"


def record(**changes: Any) -> str:
    # The export record of _base/1.0.0, written by the images role.
    data = {
        "schema_version": 2,
        "alias": "_base/1.0.0",
        "fingerprint": "f" * 64,
        "type": "container",
        "nesting": False,
        "properties": {"requirements.nesting": "false"},
    }
    data["packages"] = list(reversed(PACKAGES))
    data.update(changes)
    return json.dumps(data)


GOLDEN = {"fingerprint": "f" * 64, "type": "container", "nesting": False, "packages": PACKAGES}


def host(**extra_files: str) -> FakeSystem:
    files = {
        RECORD_PATH: record(),
        PROFILE_PATH: json.dumps(PROFILE),
        "/etc/pinka/bootstrap-state.json": json.dumps(
            {"repo_commit": "c" * 40, "infra_tree": "d" * 40, "infra_dirty": False}
        ),
        "/etc/pinka/platform/active.json": json.dumps(
            {
                "record_version": 1,
                "name": "demo",
                "version": "0.1.0",
                "manifest_sha256": "a" * 64,
                "release": {
                    "name": "demo-v0.1.0",
                    "tag": "demo-v0.1.0",
                    "commit": "e" * 40,
                    "tree": "f" * 40,
                    "target": "/srv/pinka/platforms/demo/releases/demo-v0.1.0",
                },
                "content": {
                    "cases": {
                        "name": "cases-v1.0.0",
                        "tag": "cases-v1.0.0",
                        "commit": "1" * 40,
                        "tree": "2" * 40,
                        "target": "/srv/pinka/restricted/demo/cases/cases-v1.0.0",
                        "restricted": True,
                    }
                },
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
            "docker inspect --format {{.Config.Image}} pinka-postgres": (
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


class GoldenImagePackagesTest(unittest.TestCase):
    # Every result can be traced to the exact package versions of its targets:
    # the packages of each golden image are part of the hashed fingerprint.

    def images(self, listing: Any = None, **files: str) -> dict[str, Any]:
        system = host(**files)
        if listing is not None:
            system.commands["incus image list --format json"] = (0, listing)
        fp = fingerprint.compute(system, lab())["fingerprint"]
        images: dict[str, Any] = fp["software"]["golden_images"]
        return images

    def test_a_changed_package_changes_the_hash(self) -> None:
        changed = [dict(PACKAGES[0], version="2.0-2"), PACKAGES[1]]
        a = fingerprint.compute(host(), lab())["hash"]
        b = fingerprint.compute(host(**{RECORD_PATH: record(packages=changed)}), lab())["hash"]
        self.assertNotEqual(a, b)

    def test_a_golden_image_carries_type_and_nesting_from_its_record(self) -> None:
        self.assertEqual(self.images(), {"_base/1.0.0": GOLDEN})
        nested = record(type="vm", nesting=True)
        self.assertEqual(
            self.images(**{RECORD_PATH: nested})["_base/1.0.0"],
            dict(GOLDEN, type="vm", nesting=True),
        )
        # Never from the image's properties, which a client can rewrite.
        rewritten = [
            {
                "fingerprint": "f" * 64,
                "aliases": [{"name": "_base/1.0.0"}],
                "properties": {"requirements.nesting": "true"},
            }
        ]
        self.assertEqual(self.images(rewritten)["_base/1.0.0"]["nesting"], False)

    def test_unproven_package_lists_are_null(self) -> None:
        cases = {
            "no list": record(packages=None),
            "a field missing": record(packages=[{"name": "libc6", "version": "2.0-1"}]),
            "an extra field": record(packages=[dict(PACKAGES[0], status="installed")]),
            "an empty version": record(packages=[dict(PACKAGES[0], version="")]),
        }
        for case, text in cases.items():
            with self.subTest(case):
                entry = self.images(**{RECORD_PATH: text})["_base/1.0.0"]
                self.assertEqual(entry, dict(GOLDEN, packages=None))

    def test_an_alias_that_is_not_golden_is_left_out(self) -> None:
        # The golden rule doctor and the images role apply: an export record,
        # its fingerprint, schema 2, no update source.
        cases = {
            "missing record": "",
            "not JSON": "{",
            "record of another image": record(fingerprint="e" * 64),
            "record of schema 1": record(schema_version=1),
        }
        for case, text in cases.items():
            with self.subTest(case):
                self.assertEqual(self.images(**{RECORD_PATH: text}), {})
        downloaded = [
            {
                "fingerprint": "f" * 64,
                "aliases": [{"name": "_base/1.0.0"}],
                "update_source": {"server": "https://example.com"},
            }
        ]
        self.assertEqual(self.images(downloaded), {})

    def test_a_golden_alias_whose_record_lacks_type_or_nesting_is_left_out(self) -> None:
        # Golden by the shared rule, but its record proves no type or nesting.
        cases = {
            "no type": record(type=None),
            "another type": record(type="virtual-machine"),
            "no nesting": record(nesting=None),
            "nesting as text": record(nesting="false"),
        }
        for case, text in cases.items():
            with self.subTest(case):
                self.assertEqual(self.images(**{RECORD_PATH: text}), {})

    def test_an_image_without_a_fingerprint_is_left_out(self) -> None:
        # The contract's fingerprint is always a string: an image and a record
        # that both lack one match, and still prove nothing.
        for fingerprint_value in (None, ""):
            with self.subTest(fingerprint_value):
                listing = [{"fingerprint": fingerprint_value, "aliases": [{"name": "_base/1.0.0"}]}]
                text = record(fingerprint=fingerprint_value)
                self.assertEqual(self.images(listing, **{RECORD_PATH: text}), {})

    def test_type_and_nesting_change_the_hash(self) -> None:
        a = fingerprint.compute(host(), lab())["hash"]
        for change in ({"type": "vm"}, {"nesting": True}):
            with self.subTest(change):
                b = fingerprint.compute(host(**{RECORD_PATH: record(**change)}), lab())["hash"]
                self.assertNotEqual(a, b)

    def test_a_node_without_images_lists_none(self) -> None:
        # No blueprints in lab.json (a node that runs no lab): no alias is
        # golden, also one with a valid record.
        system = host()
        fp = fingerprint.compute(system, lab(images__blueprints=[]))["fingerprint"]
        self.assertEqual(fp["software"]["golden_images"], {})

    def test_only_aliases_of_the_labs_blueprints_are_listed(self) -> None:
        # A platform's derived image, an alias of an unknown blueprint, and a
        # path-escaping alias are never golden, even with a valid record where
        # they would point.
        listing = [
            {
                "fingerprint": "f" * 64,
                "aliases": [
                    {"name": "_base/1.0.0"},
                    {"name": "demo/web/1.0.0"},
                    {"name": "other/1.0.0"},
                    {"name": "_base/../x"},
                    {"name": "../x"},
                    {"name": "x"},
                ],
            }
        ]
        files = {
            "/srv/pinka/images/demo/web/1.0.0/build.json": record(),
            "/srv/pinka/images/other/1.0.0/build.json": record(),
            "/srv/pinka/images/_base/../x/build.json": record(),
            "/srv/pinka/images/../x/build.json": record(),
            "/srv/pinka/images/x/build.json": record(),
        }
        self.assertEqual(self.images(listing, **files), {"_base/1.0.0": GOLDEN})
        # A pre-created alias of the next version: no record, not listed.
        listing = [
            {"fingerprint": "f" * 64, "aliases": [{"name": "_base/1.0.0"}]},
            {"fingerprint": "e" * 64, "aliases": [{"name": "_base/1.0.1"}]},
        ]
        self.assertEqual(self.images(listing), {"_base/1.0.0": GOLDEN})

    def test_a_listing_that_is_no_list_lists_nothing(self) -> None:
        self.assertEqual(self.images({"error": "x"}), {})


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
        self.assertEqual(fp["machine"], "pinka-lab")
        self.assertEqual(fp["host"]["os_id"], "ubuntu")
        self.assertEqual(fp["hardware"]["gpu"]["vram_gib"], 12)
        software = fp["software"]
        self.assertEqual(software["versions"]["incus"], "6.0.6")
        self.assertEqual(software["versions"]["docker"], "29.0.0")
        self.assertEqual(software["versions"]["tailscale"], "1.90.0")
        self.assertIsNone(software["versions"]["containerd"])
        self.assertEqual(
            software["containers"]["pinka-postgres"], "docker.io/library/postgres@" + DIGEST
        )
        self.assertEqual(software["golden_images"], {"_base/1.0.0": GOLDEN})  # packages sorted
        config = fp["config"]
        self.assertEqual(config["infra_commit"], "c" * 40)
        self.assertEqual(config["versions_lock_sha256"], "0" * 64)
        self.assertEqual(
            config["platform"],
            {
                "name": "demo",
                "version": "0.1.0",
                "manifest_sha256": "a" * 64,
                "release": {"tag": "demo-v0.1.0", "commit": "e" * 40, "tree": "f" * 40},
                "content": {"cases": {"tag": "cases-v1.0.0", "commit": "1" * 40, "tree": "2" * 40}},
            },
        )
        self.assertIsNone(
            fingerprint.compute(host(**{"/etc/pinka/platform/active.json": ""}), lab())[
                "fingerprint"
            ]["config"]["platform"]
        )
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

    def test_schema_requires_type_and_nesting(self) -> None:
        # A golden image without its record's type and nesting is no entry of
        # the contract; neither is another type, nesting as text or a null
        # fingerprint.
        for change in (
            {"type": None},
            {"type": "virtual-machine"},
            {"nesting": "false"},
            {"fingerprint": None},
        ):
            with self.subTest(change):
                document = fingerprint.compute(host(), lab())
                entry = document["fingerprint"]["software"]["golden_images"]["_base/1.0.0"]
                entry.update(change)
                self.assertTrue(validate(document, self.schema, self.schema))
        for drop in ("type", "nesting"):
            with self.subTest(drop):
                document = fingerprint.compute(host(), lab())
                del document["fingerprint"]["software"]["golden_images"]["_base/1.0.0"][drop]
                self.assertTrue(validate(document, self.schema, self.schema))


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
        with self.assertRaisesRegex(fingerprint.FingerprintError, "sudo pinka fingerprint"):
            fingerprint.current(FakeSystem(euid=1000), lab())


class DiffTest(unittest.TestCase):
    def test_diff(self) -> None:
        old = {"a": 1, "b": {"c": "x", "d": [1]}, "e": None}
        new = {"a": 1, "b": {"c": "y", "d": [1]}, "f": True}
        self.assertEqual(fingerprint.diff(old, new), ['b.c: "x" -> "y"', "f: null -> true"])
        self.assertEqual(fingerprint.diff(old, old), [])


if __name__ == "__main__":
    unittest.main()
