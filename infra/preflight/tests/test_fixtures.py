"""Every fixture root produces the expected decision (BUILD_PROMPT §11).

Also checks that the committed fixture tree equals ``fixture_defs.py``, that
every report validates against the host profile schema, and that the human
summary matches its snapshot. Set ``PINKA_UPDATE_SNAPSHOTS=1`` to rewrite the
snapshots after an intended change, then review the diff.
"""

from __future__ import annotations

import contextlib
import filecmp
import importlib
import io
import json
import os
import tempfile
import unittest
from typing import Any

import fixture_defs
from helpers import FIXTURES_DIR, SNAPSHOTS_DIR

from pinka_preflight import cli, render, report, schema_check

jsonschema: Any
try:
    jsonschema = importlib.import_module("jsonschema")
except ImportError:  # plain `python3 -m unittest` run without the dev environment
    jsonschema = None


def fixture_args(name: str) -> list[str]:
    """The extra preflight arguments of a fixture (``args.json``, if any)."""
    path = os.path.join(FIXTURES_DIR, name, "args.json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as handle:
        args: list[str] = json.load(handle)
    return args


def run_fixture(name: str) -> dict[str, Any]:
    root = os.path.join(FIXTURES_DIR, name)
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        cli.main(["--root", root, "--offline", "--quiet", *fixture_args(name)])
    doc: dict[str, Any] = json.loads(out.getvalue())
    return doc


def expected(name: str) -> dict[str, Any]:
    with open(os.path.join(FIXTURES_DIR, name, "expected.json"), encoding="utf-8") as handle:
        data: dict[str, Any] = json.load(handle)
    return data


def diff_trees(a: str, b: str) -> list[str]:
    cmp = filecmp.dircmp(a, b)
    problems = [f"only in committed: {x}" for x in cmp.left_only] + [
        f"missing: {x}" for x in cmp.right_only
    ]
    _, mismatch, errors = filecmp.cmpfiles(a, b, cmp.common_files, shallow=False)
    problems += [f"differs: {x}" for x in mismatch + errors]
    for sub in cmp.common_dirs:
        problems += [f"{sub}/{p}" for p in diff_trees(os.path.join(a, sub), os.path.join(b, sub))]
    return problems


class FixtureTreeTest(unittest.TestCase):
    def test_committed_fixtures_match_definitions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture_defs.write_all(tmp)
            problems = diff_trees(FIXTURES_DIR, tmp)
        self.assertEqual(problems, [], "run: python preflight/tests/fixture_defs.py --write")

    def test_required_fixtures_exist(self) -> None:
        required = {
            "ubuntu2404-baremetal-nvidia-driver-loaded",
            "ubuntu2404-baremetal-nvidia-old-driver",
            "ubuntu2404-wsl2-gpu-old-driver",
            "ubuntu2404-baremetal-nvidia-no-driver-secureboot",
            "ubuntu2404-baremetal-nvidia-no-driver-secureboot-consent",
            "ubuntu2404-baremetal-nvidia-no-driver-secureboot-manual",
            "ubuntu2404-baremetal-nvidia-no-driver-secureboot-skip",
            "ubuntu2404-baremetal-no-gpu",
            "ubuntu2404-wsl2-gpu-nokvm",
            "ubuntu2404-wsl2-gpu-kvm",
            "ubuntu2404-wsl2-systemd-off",
            "ubuntu2404-wsl2-docker-desktop-integration",
            "ubuntu2404-wsl2-linux-nvidia-driver-installed",
            "wsl1",
            "ubuntu2404-kvm-vm-gpu-passthrough",
            "ubuntu2404-hyperv-vm-no-gpu",
            "ubuntu2404-vmware-vm-no-gpu",
            "docker-container",
            "systemd-detect-virt-reports-wsl",
            "debian13-baremetal",
            "fedora-baremetal-nvidia",
            "almalinux9-kvm-vm",
            "opensuse-tumbleweed-baremetal",
            "arch-baremetal",
            "linuxmint22-baremetal",
            "manjaro-baremetal",
            "oraclelinux9",
            "fedora-silverblue",
            "nixos",
            "alpine",
            "devuan",
            "rhel8-cgroup-v1",
            "fedora-selinux-enforcing-firewalld",
            "ubuntu2404-ufw-active",
            "ubuntu2404-aarch64-no-gpu",
            "ubuntu2404-no-btrfs-no-dmthin",
            "ubuntu2404-cgroup-v1",
            "ubuntu2404-low-memory",
            "conflicting-signals",
        }
        self.assertLessEqual(required, set(os.listdir(FIXTURES_DIR)))


class FixtureDecisionTest(unittest.TestCase):
    maxDiff = None

    def check(self, name: str) -> None:  # noqa: PLR0912 - one branch per expectation key
        doc = run_fixture(name)
        exp = expected(name)
        simple = {
            "outcome": doc["outcome"],
            "provider": doc["provider"],
            "tier": doc["tier"],
            "family": doc["family"],
            "release_model": doc["release_model"],
            "env_class": doc["environment"]["class"],
            "hypervisor": doc["environment"]["hypervisor"],
            "confidence": doc["environment"]["confidence"],
            "parent_release": doc["os"]["parent_release"],
            "firewall_backend": doc["firewall"]["backend"],
            "storage_backend": doc["storage_backend"],
            "secure_boot": doc["capabilities"]["secure_boot"],
            "install_driver": doc["gpu"]["install_driver"],
        }
        for key, value in exp.items():
            if key in simple:
                self.assertEqual(simple[key], value, f"{name}: {key}")
            elif key == "features":
                for feature, on in value.items():
                    self.assertEqual(doc["features"][feature], on, f"{name}: feature {feature}")
            elif key == "disabled":
                self.assertEqual([d["feature"] for d in doc["disabled_features"]], value, name)
            elif key in ("needs_action", "planned_actions"):
                self.assertEqual([a["id"] for a in doc[key]], value, f"{name}: {key}")
            elif key == "overrides_on":
                on = [k for k, v in doc["overrides"].items() if v]
                self.assertEqual(on, value, f"{name}: overrides")
            elif key == "conflicts":
                self.assertEqual([c["id"] for c in doc["conflicts"]], value, name)
            elif key == "warnings_contain":
                for needle in value:
                    self.assertTrue(
                        any(needle in w for w in doc["warnings"]), f"{name}: warning {needle!r}"
                    )
            elif key == "unsupported_contains":
                for needle in value:
                    self.assertTrue(
                        any(needle in r for r in doc["unsupported_reasons"]),
                        f"{name}: reason {needle!r}",
                    )
            elif key == "remediation_contains":
                remediations = [d.get("remediation", "") for d in doc["disabled_features"]]
                for needle in value:
                    self.assertTrue(
                        any(needle in r for r in remediations), f"{name}: remediation {needle!r}"
                    )
            elif key == "mac":
                self.assertEqual([doc["mac"]["system"], doc["mac"]["mode"]], value, name)
            else:
                self.fail(f"{name}: unknown expectation {key}")
        self.assertEqual(
            doc["exit_code"],
            {"supported": 0, "degraded": 10, "unsupported": 20, "needs_action": 30}[doc["outcome"]],
        )
        self.assertEqual(schema_check.validate(doc, report.load_schema()), [], name)
        self.check_snapshot(name, render.render(doc))

    def check_snapshot(self, name: str, text: str) -> None:
        path = os.path.join(SNAPSHOTS_DIR, f"{name}.txt")
        if os.environ.get("PINKA_UPDATE_SNAPSHOTS") == "1":
            os.makedirs(SNAPSHOTS_DIR, exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(text, handle.read(), f"summary snapshot of {name}")

    def test_every_fixture(self) -> None:
        names = sorted(
            n for n in os.listdir(FIXTURES_DIR) if os.path.isdir(os.path.join(FIXTURES_DIR, n))
        )
        self.assertGreaterEqual(len(names), 34)
        for name in names:
            with self.subTest(fixture=name):
                self.check(name)

    def test_reports_validate_with_reference_validator(self) -> None:
        if jsonschema is None:
            self.skipTest("reference jsonschema validator not installed (stdlib-only run)")
        schema = report.load_schema()
        jsonschema.Draft202012Validator.check_schema(schema)
        validator = jsonschema.Draft202012Validator(schema)
        for name in sorted(os.listdir(FIXTURES_DIR)):
            with self.subTest(fixture=name):
                errors = [e.message for e in validator.iter_errors(run_fixture(name))]
                self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
