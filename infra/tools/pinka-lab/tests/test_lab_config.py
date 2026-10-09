"""Tests for the lab description (/etc/pinka/lab.json)."""

from __future__ import annotations

import json
import unittest

from labfakes import FakeSystem, lab, lab_data

from pinka_lab.config import DEFAULT_PATH, REQUIRED, ConfigError, LabConfig, load


class LoadTest(unittest.TestCase):
    def load_text(self, text: str | None) -> LabConfig:
        files = {} if text is None else {DEFAULT_PATH: text}
        return load(FakeSystem(files=files))

    def test_valid(self) -> None:
        config = self.load_text(json.dumps(lab_data()))
        self.assertEqual(config.machine, "pinka-lab")

    def test_missing_file(self) -> None:
        with self.assertRaisesRegex(ConfigError, "does not exist"):
            self.load_text(None)

    def test_not_json(self) -> None:
        with self.assertRaisesRegex(ConfigError, DEFAULT_PATH):
            self.load_text("{")

    def test_other_schema_version(self) -> None:
        with self.assertRaisesRegex(ConfigError, "schema_version 1"):
            self.load_text(json.dumps(lab_data(schema_version=2)))
        with self.assertRaisesRegex(ConfigError, "schema_version 1"):
            self.load_text("[]")

    def test_missing_required_keys(self) -> None:
        for key in REQUIRED:
            data = lab_data()
            del data[key]
            with self.assertRaisesRegex(ConfigError, f"missing {key}"):
                self.load_text(json.dumps(data))


class AccessTest(unittest.TestCase):
    def test_nested_get(self) -> None:
        config = lab()
        self.assertEqual(config.get("incus", "pool"), "pinka-targets")
        self.assertEqual(config.get("incus", "nothing", default=3), 3)
        self.assertEqual(config.get("machine", "deeper", default="x"), "x")

    def test_properties(self) -> None:
        config = lab()
        self.assertEqual(config.host_vars, "pinka-lab")
        self.assertEqual(config.infra_dir, "/srv/checkout/pinka/infra")
        self.assertEqual(config.pinka_root, "/srv/pinka")
        self.assertEqual(config.provider, "baremetal")
        self.assertTrue(config.feature("gpu_inference"))
        self.assertFalse(config.feature("unknown"))

    def test_node_roles(self) -> None:
        cases = {
            ("full", True): (True, True),
            ("lab", True): (True, False),
            ("inference", True): (False, True),
            ("full", False): (True, False),
        }
        for (role, gpu), (runs_lab, serves) in cases.items():
            config = lab(node_role=role, features__gpu_inference=gpu)
            self.assertEqual((config.runs_lab, config.serves_inference), (runs_lab, serves))


if __name__ == "__main__":
    unittest.main()
