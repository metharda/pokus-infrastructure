"""Tests for golden images: an alias counts only with the export infra wrote."""

from __future__ import annotations

import json
import unittest
from typing import Any

from labfakes import FakeSystem, context, info

from pinka_lab import checks, golden
from pinka_lab.checks import CHECKS

EXPORT = "/srv/pinka/images"
NO_RECORD = f"it points to {'f' * 64}, which has no export record"
PROPERTIES = {
    "pinka.recipe_commit": "c" * 40,
    "pinka.built_at": "2026-01-01T12:00:00Z",
    "pinka.base_fingerprint": "b" * 64,
    "requirements.nesting": "false",
    "description": "the base",
}


def image(
    alias: str, fingerprint: str = "f" * 64, changes: dict[str, Any] | None = None
) -> dict[str, Any]:
    """An image as ``incus image list --format json`` shows it, with infra's properties.

    ``changes`` replaces properties; None removes one.
    """
    properties = {k: v for k, v in {**PROPERTIES, **(changes or {})}.items() if v is not None}
    return {"fingerprint": fingerprint, "aliases": [{"name": alias}], "properties": properties}


def record(fingerprint: str = "f" * 64, **changes: Any) -> dict[str, Any]:
    """The export record of an image with infra's properties (schema 2: every property)."""
    return {
        "schema_version": 2,
        "fingerprint": fingerprint,
        "nesting": False,
        "properties": dict(PROPERTIES),
        **changes,
    }


PUT_BACK = {"properties": PROPERTIES, "public": False, "auto_update": False}


def host(images: list[dict[str, Any]], records: dict[str, Any]) -> FakeSystem:
    """A host with these images and export records (alias to record, or text)."""
    files = {
        f"{EXPORT}/{alias}/build.json": value if isinstance(value, str) else json.dumps(value)
        for alias, value in records.items()
    }
    return FakeSystem({"incus image list --format json": (0, images)}, files=files)


class ProblemsTest(unittest.TestCase):
    def test_a_golden_image(self) -> None:
        self.assertEqual(golden.problems(image("_base/1.0.0"), record()), [])
        self.assertEqual(golden.problems({}, record()), [])
        self.assertEqual(golden.problems({}, {}), [])

    def test_an_alias_without_its_export(self) -> None:
        self.assertEqual(
            golden.problems(image("_base/1.0.1"), {}),
            [f"it points to {'f' * 64}, which has no export record"],
        )
        self.assertEqual(
            golden.problems(image("_base/1.0.0"), record("e" * 64)),
            [f"it points to {'f' * 64}, but its export recorded {'e' * 64}"],
        )

    def test_an_update_source_is_never_infras(self) -> None:
        downloaded = dict(image("_base/1.0.0"), update_source={"server": "https://example.com"})
        self.assertEqual(
            golden.problems(downloaded, record()),
            ["it has an update source (https://example.com), which infra's images never have"],
        )

    def test_the_properties_prove_nothing(self) -> None:
        # A client can rewrite them: bootstrap puts them back, nothing is refused.
        changed = image("_base/1.0.0", changes={"pinka.built_at": None, "pinka.recipe_commit": "x"})
        self.assertEqual(golden.problems(changed, record(properties={})), [])

    def test_only_a_record_of_schema_2_is_read(self) -> None:
        # An older record names only some of the image's properties.
        for version, shown in (
            (1, "1"),
            ("2", '"2"'),
            (2.0, "2.0"),
            (True, "true"),
            (None, "null"),
        ):
            self.assertEqual(
                golden.problems(image("_base/1.0.0"), record(schema_version=version)),
                [f"its export record has schema {shown}, not 2; build the version again"],
            )
        self.assertEqual(golden.record_problems({}), [])
        self.assertEqual(golden.record_problems(record()), [])


class DriftTest(unittest.TestCase):
    def test_nothing_changed(self) -> None:
        self.assertEqual(golden.drift(image("_base/1.0.0"), record()), {"changes": [], "body": {}})
        # The record's properties decide, not its nesting field.
        self.assertEqual(golden.drift(image("_base/1.0.0"), record(nesting=True))["body"], {})

    def test_what_a_client_changed(self) -> None:
        changed = dict(
            image("_base/1.0.0", changes={"pinka.built_at": None, "pinka.recipe_commit": "x"}),
            public=True,
            auto_update=True,
            profiles=["default"],
        )
        self.assertEqual(
            golden.drift(changed, record()),
            {
                "changes": [
                    "it lacks its property pinka.built_at (2026-01-01T12:00:00Z in its export record)",
                    f"its property pinka.recipe_commit is x, not {'c' * 40} as in its export record",
                    "it is public",
                    "its auto_update is on",
                ],
                "body": {**PUT_BACK, "profiles": ["default"]},
            },
        )

    def test_what_a_client_added_is_removed(self) -> None:
        changed = image("_base/1.0.0", changes={"requirements.cgroup": "v1", "description": None})
        self.assertEqual(
            golden.drift(changed, record()),
            {
                "changes": [
                    'it lacks its property description ("the base" in its export record)',
                    "it has the property requirements.cgroup (v1), which its build did not set",
                ],
                "body": PUT_BACK,
            },
        )

    def test_an_image_that_is_not_golden_is_not_put_back(self) -> None:
        changed = image("_base/1.0.0", changes={"pinka.built_at": "x"})
        nothing: dict[str, Any] = {"changes": [], "body": {}}
        self.assertEqual(golden.drift(changed, {}), nothing)
        self.assertEqual(golden.drift(changed, record("e" * 64)), nothing)
        self.assertEqual(golden.drift(changed, record(schema_version=1)), nothing)
        self.assertEqual(golden.drift({}, record()), nothing)


class ShownTest(unittest.TestCase):
    def test_values_are_printable_and_short(self) -> None:
        self.assertEqual(golden.shown("_base/1.0.0"), "_base/1.0.0")
        self.assertEqual(golden.shown("a b"), '"a b"')
        self.assertEqual(golden.shown("\x1b[2J"), '"\\u001b[2J"')
        self.assertEqual(golden.shown(None), "null")
        self.assertEqual(golden.shown("x" * 200), "x" * 77 + "...")
        self.assertEqual(golden.shown("a\x7fb"), '"a\\u007fb"')
        self.assertEqual(len(golden.shown("é" * 100)), golden.SHOWN)


class GoldenAliasesTest(unittest.TestCase):
    def golden(self, images: list[dict[str, Any]], records: dict[str, Any]) -> dict[str, Any]:
        return golden.golden_aliases(
            host(images, records), EXPORT, images, {"_base", "web-nginx-flask"}
        )

    def test_only_golden_aliases_with_their_image_and_record(self) -> None:
        both = image("_base/1.0.0")
        both["aliases"].append({"name": "_base/1.0.1"})
        other = image("web-nginx-flask/1.0.0", "a" * 64)
        derived = image("demo/web/1.0.0", "b" * 64)
        found = self.golden(
            [both, other, derived, image("web-nginx-flask/2.0.0", "c" * 64)],
            {
                "_base/1.0.0": record(),
                "_base/1.0.1": record(),
                "web-nginx-flask/1.0.0": record("a" * 64),
                "demo/web/1.0.0": record("b" * 64),
                "web-nginx-flask/2.0.0": record("c" * 64, schema_version=1),
            },
        )
        # Both aliases of one image are golden where each version's record
        # names that image; a derived image's blueprint is not the lab's; a
        # record of schema 1 is not read.
        self.assertEqual(sorted(found), ["_base/1.0.0", "_base/1.0.1", "web-nginx-flask/1.0.0"])
        self.assertEqual(found["web-nginx-flask/1.0.0"], (other, record("a" * 64)))

    def test_a_record_that_cannot_be_read_or_is_empty_is_not_golden(self) -> None:
        system = host([image("_base/1.0.0")], {})
        path = f"{EXPORT}/_base/1.0.0/build.json"
        system.infos[path] = info("root", "pinka-admin", 0o640)
        self.assertEqual(
            golden.golden_aliases(system, EXPORT, [image("_base/1.0.0")], {"_base"}), {}
        )
        for text in ("", "[]", "{}"):
            with self.subTest(text):
                self.assertEqual(self.golden([image("_base/1.0.0")], {"_base/1.0.0": text}), {})


class ReviewTest(unittest.TestCase):
    def review(self, images: list[dict[str, Any]], records: dict[str, Any]) -> dict[str, Any]:
        system = host(images, records)
        found = golden.review(system, EXPORT, images, {"_base", "web-nginx-flask"})
        return found

    def test_every_alias_of_a_blueprint(self) -> None:
        both = image("_base/1.0.0")
        both["aliases"].append({"name": "_base/1.0.1"})
        found = self.review(
            [both, image("personal/1"), image("web-nginx-flask/1.0.0", "a" * 64)],
            {"_base/1.0.0": record(), "web-nginx-flask/1.0.0": record("a" * 64)},
        )
        self.assertEqual(
            found,
            {
                "_base/1.0.0": {"problems": [], "changes": []},
                "_base/1.0.1": {"problems": [NO_RECORD], "changes": []},
                "web-nginx-flask/1.0.0": {"problems": [], "changes": []},
            },
        )

    def test_only_a_version_directory_has_a_record(self) -> None:
        # A record elsewhere below the export root never makes an alias golden.
        aliases = ["_base/", "_base/..", "_base/.", "_base/../web-nginx-flask/1.0.0", "_base"]
        records = {
            "_base/": record(),
            "_base/..": record(),
            "_base/.": record(),
            "_base/../web-nginx-flask/1.0.0": record(),
            "web-nginx-flask/1.0.0": record(),
        }
        found = self.review([image(a) for a in aliases], records)
        self.assertEqual(sorted(found), sorted(aliases))
        for alias in aliases:
            self.assertEqual(found[alias], {"problems": [NO_RECORD], "changes": []})

    def test_a_version_with_control_characters_reads_no_record(self) -> None:
        # open() refuses a NUL; neither is ever a version directory.
        for alias in ("_base/1.0.0\x00", "_base/1.0.\x1b"):
            found = self.review([image(alias)], {alias: record()})
            self.assertEqual(found[alias], {"problems": [NO_RECORD], "changes": []})

    def test_a_record_that_cannot_be_read(self) -> None:
        system = host([image("_base/1.0.0")], {})
        path = f"{EXPORT}/_base/1.0.0/build.json"
        system.infos[path] = info("root", "pinka-admin", 0o640)
        found = golden.review(system, EXPORT, [image("_base/1.0.0")], {"_base"})
        self.assertEqual(
            found["_base/1.0.0"],
            {"problems": [f"its export record {path} cannot be read"], "changes": []},
        )

    def test_an_unreadable_record_is_none(self) -> None:
        for text in ("{", "[]", '"x"', ""):
            found = self.review([image("_base/1.0.0")], {"_base/1.0.0": text})
            self.assertEqual(found["_base/1.0.0"], {"problems": [NO_RECORD], "changes": []})


class DoctorCheckTest(unittest.TestCase):
    def test_registered_as_an_error(self) -> None:
        check = next(c for c in CHECKS if c.id == "images.golden")
        self.assertEqual(check.severity, "error")
        self.assertIs(check.run, checks.check_images_golden)
        self.assertIn("incus image alias delete <alias>", check.remediation)
        self.assertIn("pinka images build", check.remediation)

    def test_golden_aliases_pass(self) -> None:
        system = host([image("_base/1.0.0"), image("personal/1")], {"_base/1.0.0": record()})
        outcome = checks.check_images_golden(context(system))
        self.assertEqual(outcome.result, "pass", outcome.detail)
        self.assertIn("1 alias(es)", outcome.detail)
        outcome = checks.check_images_golden(context(host([], {})))
        self.assertEqual(outcome.result, "pass", outcome.detail)
        self.assertIn("no image of a blueprint", outcome.detail)

    def test_an_alias_created_before_its_build_fails(self) -> None:
        # A client of the shared store creates the next version's alias.
        forged = image("_base/1.0.1", "e" * 64)
        system = host([image("_base/1.0.0"), forged], {"_base/1.0.0": record()})
        outcome = checks.check_images_golden(context(system))
        self.assertEqual(outcome.result, "fail")
        self.assertEqual(
            outcome.detail, f"_base/1.0.1: it points to {'e' * 64}, which has no export record"
        )

    def test_every_problem_is_named(self) -> None:
        source = dict(image("_base/1.0.0"), update_source={"server": "https://example.com"})
        escaped = image("_base/\x1b[2J", "a" * 64)
        system = host([source, escaped], {"_base/1.0.0": record()})
        outcome = checks.check_images_golden(context(system))
        self.assertEqual(outcome.result, "fail")
        self.assertIn("_base/1.0.0: it has an update source (https://example.com)", outcome.detail)
        self.assertIn('"_base/\\u001b[2J": it points to', outcome.detail)
        self.assertNotIn("\x1b", outcome.detail)
        # A changed record is the warning's, never this error's.
        changed = dict(image("_base/1.0.0", changes={"pinka.built_at": None}), public=True)
        outcome = checks.check_images_golden(context(host([changed], {"_base/1.0.0": record()})))
        self.assertEqual(outcome.result, "pass", outcome.detail)

    def test_the_detail_stays_short(self) -> None:
        many = [image(f"_base/1.0.{n}", f"{n:064x}") for n in range(25)]
        outcome = checks.check_images_golden(context(host(many, {})))
        self.assertEqual(outcome.result, "fail")
        self.assertEqual(outcome.detail.count("which has no export record"), 10)
        self.assertTrue(outcome.detail.endswith("; and 15 more"), outcome.detail)

    def test_no_listing_fails(self) -> None:
        outcome = checks.check_images_golden(context(FakeSystem()))
        self.assertEqual(outcome.result, "fail")
        self.assertIn("gave no list of images", outcome.detail)

    def test_skipped_without_incus(self) -> None:
        outcome = checks.check_images_golden(context(FakeSystem(), node_role="inference"))
        self.assertEqual(outcome.result, "skip")

    def test_a_changed_record_is_a_warning(self) -> None:
        check = next(c for c in CHECKS if c.id == "images.properties")
        self.assertEqual(check.severity, "warning")
        self.assertIs(check.run, checks.check_images_properties)
        self.assertIn("pinka images build", check.remediation)
        changed = dict(image("_base/1.0.0", changes={"pinka.built_at": None}), auto_update=True)
        forged = image("_base/1.0.1", "e" * 64, changes={"pinka.built_at": "x"})
        system = host([changed, forged, image("personal/1")], {"_base/1.0.0": record()})
        outcome = checks.check_images_properties(context(system))
        self.assertEqual(outcome.result, "fail")
        self.assertEqual(
            outcome.detail,
            "_base/1.0.0: it lacks its property pinka.built_at (2026-01-01T12:00:00Z in its"
            " export record); _base/1.0.0: its auto_update is on",
        )
        system = host([image("_base/1.0.0"), forged], {"_base/1.0.0": record()})
        outcome = checks.check_images_properties(context(system))
        self.assertEqual(outcome.result, "pass", outcome.detail)
        self.assertIn("1 golden image alias(es)", outcome.detail)
        self.assertEqual(checks.check_images_properties(context(FakeSystem())).result, "fail")
        skipped = checks.check_images_properties(context(FakeSystem(), node_role="inference"))
        self.assertEqual(skipped.result, "skip")

    def test_the_export_root_of_the_lab(self) -> None:
        system = host([image("_base/1.0.0")], {})
        system.files["/elsewhere/_base/1.0.0/build.json"] = json.dumps(record())
        outcome = checks.check_images_golden(context(system, images__export_root="/elsewhere"))
        self.assertEqual(outcome.result, "pass", outcome.detail)


if __name__ == "__main__":
    unittest.main()
