"""Tests for the matrix evaluator and the network checks."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from typing import Any

from helpers import FIXTURES_DIR, PREFLIGHT_DIR, runner

from pokus_preflight import cli, matrix
from pokus_preflight.cli import ENDPOINTS_JSON, _load_json_list
from pokus_preflight.detect import network
from pokus_preflight.detect.os_release import OsRelease
from pokus_preflight.fs import FileSystem
from pokus_preflight.runner import FixtureRunner


def osr(**kw: object) -> OsRelease:
    return OsRelease(**kw)  # type: ignore[arg-type]


class MatchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.m = matrix.load_matrix()

    def test_compiled_matrix_has_catch_alls(self) -> None:
        self.assertTrue(self.m["os"][-1]["match"]["any"])
        self.assertTrue(self.m["arch"][-1]["match"]["any"])
        self.assertTrue(os.path.isfile(os.path.join(PREFLIGHT_DIR, "support-matrix.json")))

    def test_match_keys(self) -> None:
        self.assertTrue(matrix.os_rule_matches({"id_in": ["a", "b"]}, osr(id="b")))
        self.assertTrue(
            matrix.os_rule_matches({"version_major_in": ["9"]}, osr(id="x", version_id="9.6"))
        )
        self.assertFalse(
            matrix.os_rule_matches({"version_id_in": ["43"]}, osr(id="fedora", version_id="42"))
        )
        self.assertTrue(
            matrix.os_rule_matches(
                {"id_or_like_in": ["rhel"]}, osr(id="alma", id_like=["rhel", "fedora"])
            )
        )
        self.assertTrue(
            matrix.os_rule_matches({"id_or_like_in": ["ol"]}, osr(id="ol", id_like=["fedora"]))
        )
        self.assertFalse(matrix.os_rule_matches({"id": "ubuntu"}, None))
        self.assertTrue(matrix.os_rule_matches({"any": True}, None))
        with self.assertRaises(ValueError):
            matrix.os_rule_matches({"codename": "x"}, osr(id="x"))

    def test_first_match_and_identity(self) -> None:
        mint = osr(
            id="linuxmint",
            id_like=["ubuntu", "debian"],
            version_id="22",
            version_codename="wilma",
            raw={"UBUNTU_CODENAME": "noble"},
        )
        rule = matrix.match_os(self.m, mint)
        self.assertEqual((rule["tier"], rule["family"]), ("experimental", "debian"))
        self.assertEqual(matrix.release_identity(rule, mint), ("wilma", "noble"))
        ubuntu = osr(id="ubuntu", id_like=["debian"], version_id="24.04", version_codename="noble")
        self.assertEqual(
            matrix.release_identity(matrix.match_os(self.m, ubuntu), ubuntu), ("noble", "noble")
        )
        self.assertEqual(matrix.release_identity(rule, None), (None, None))

    def test_policy_rows(self) -> None:
        self.assertEqual(
            matrix.match_os(self.m, osr(id="debian", version_id="12"))["tier"], "unsupported"
        )
        self.assertEqual(
            matrix.match_os(self.m, osr(id="rocky", id_like=["rhel"], version_id="8.10"))["tier"],
            "unsupported",
        )
        self.assertEqual(
            matrix.match_os(self.m, osr(id="rocky", id_like=["rhel"], version_id="9.6"))["tier"],
            "tested",
        )
        self.assertEqual(
            matrix.match_os(self.m, osr(id="fedora", version_id="42"))["tier"], "unsupported"
        )
        self.assertEqual(matrix.match_os(self.m, None)["tier"], "unsupported")

    def test_arch(self) -> None:
        self.assertEqual(matrix.match_arch(self.m, "x86_64")["tier"], "supported")
        self.assertEqual(matrix.match_arch(self.m, "aarch64")["tier"], "experimental")
        self.assertEqual(matrix.match_arch(self.m, "riscv64")["tier"], "unsupported")

    def test_requirements_and_conditions(self) -> None:
        facts = {
            "gpu_vram_gib": 12.0,
            "gpu_compute_capability": "8.9",
            "environment": "wsl2",
            "kvm_usable": False,
        }
        self.assertTrue(matrix.requirement_met({"gpu_vram_gib_min": 8}, facts)[0])
        self.assertFalse(matrix.requirement_met({"gpu_vram_gib_min": 16}, facts)[0])
        self.assertTrue(matrix.requirement_met({"gpu_compute_capability_min": "7.0"}, facts)[0])
        self.assertFalse(matrix.requirement_met({"gpu_compute_capability_min": "9.0"}, facts)[0])
        self.assertFalse(matrix.requirement_met({"environment_in": ["baremetal"]}, facts)[0])
        self.assertFalse(matrix.requirement_met("kvm_usable", facts)[0])
        self.assertFalse(matrix.requirement_met({"gpu_vram_gib_min": 8}, {})[0])
        with self.assertRaises(ValueError):
            matrix.requirement_met({"nope": 1}, facts)
        self.assertTrue(
            matrix.condition_holds({"environment_in": ["wsl2"], "kvm_usable": False}, facts)
        )
        self.assertFalse(matrix.condition_holds({"environment": "vm"}, facts))


class NetworkTest(unittest.TestCase):
    EP = [
        {"id": "a", "url": "https://a.example/", "purpose": "A", "required": True},
        {
            "id": "b",
            "url": "https://b.example/",
            "purpose": "B",
            "required": False,
            "when": {"families": ["redhat"]},
        },
        {
            "id": "c",
            "url": "https://c.example/v2/",
            "purpose": "C",
            "required": False,
            "when": {"gpu_present": True},
        },
    ]

    def test_conditions(self) -> None:
        ctx = {"family": "debian", "distro": "ubuntu", "gpu_present": False}
        self.assertTrue(network.applies({}, ctx))
        self.assertTrue(network.applies({"distros": ["ubuntu"]}, ctx))
        self.assertFalse(network.applies({"families": ["suse"]}, ctx))
        self.assertFalse(network.applies({"gpu_present": True}, ctx))
        with self.assertRaises(ValueError):
            network.applies({"arch": ["x86_64"]}, ctx)

    def test_offline_skips(self) -> None:
        res = network.check_endpoints(
            runner(), self.EP, {"family": "debian", "gpu_present": True}, offline=True
        )
        self.assertEqual([(r.id, r.status) for r in res], [("a", "skipped"), ("c", "skipped")])

    def test_probe_statuses(self) -> None:
        r = runner(
            dns={"a.example": "192.0.2.1", "c.example": "192.0.2.3"},
            https={"https://a.example/": 200, "https://c.example/v2/": "connection refused"},
        )
        res = network.check_endpoints(
            r, self.EP, {"family": "redhat", "gpu_present": True}, offline=False
        )
        self.assertEqual(
            [(x.id, x.status) for x in res],
            [("a", "ok"), ("b", "dns_failed"), ("c", "unreachable")],
        )
        self.assertEqual(network.summarize(res), {"ok": 1, "dns_failed": 1, "unreachable": 1})

    def test_compiled_endpoints_have_ids_and_urls(self) -> None:
        eps = _load_json_list(ENDPOINTS_JSON, "endpoints")
        ids = [e["id"] for e in eps]
        self.assertEqual(len(ids), len(set(ids)))
        for e in eps:
            self.assertTrue(str(e["url"]).startswith(("http://", "https://")), e)


class DecisionNetworkTest(unittest.TestCase):
    """Required endpoints make the outcome needs_action; optional ones only warn."""

    def run_fixture(self, dns: dict[str, str], https: dict[str, int]) -> dict[str, Any]:

        src = os.path.join(FIXTURES_DIR, "ubuntu2404-baremetal-nvidia-driver-loaded")
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "root")
            shutil.copytree(src, root)
            with open(os.path.join(root, "commands.json"), encoding="utf-8") as handle:
                data = json.load(handle)
            data["dns"], data["https"] = dns, https
            with open(os.path.join(root, "commands.json"), "w", encoding="utf-8") as handle:
                json.dump(data, handle)
            args = cli.build_parser().parse_args(["--root", root])
            fs = FileSystem(root)
            report, _ = cli.run_preflight(
                args, fs, FixtureRunner.from_file(os.path.join(root, "commands.json"))
            )
        return report

    def test_unreachable_required_endpoint_needs_action(self) -> None:
        report = self.run_fixture({}, {})
        self.assertEqual(report["outcome"], "needs_action")
        self.assertTrue(any(a["id"] == "endpoint:docker-hub" for a in report["needs_action"]))

    def test_everything_reachable(self) -> None:
        eps = _load_json_list(ENDPOINTS_JSON, "endpoints")
        hosts = {e["url"].split("/")[2]: "192.0.2.10" for e in eps}
        report = self.run_fixture(hosts, {e["url"]: 200 for e in eps})
        self.assertEqual(report["outcome"], "supported")
        self.assertTrue(all(e["status"] == "ok" for e in report["network"]["endpoints"]))

    def test_unreachable_optional_endpoint_warns(self) -> None:
        eps = _load_json_list(ENDPOINTS_JSON, "endpoints")
        hosts = {e["url"].split("/")[2]: "192.0.2.10" for e in eps}
        https = {e["url"]: 200 for e in eps if e.get("required")}
        report = self.run_fixture(hosts, https)
        self.assertEqual(report["outcome"], "supported")
        self.assertTrue(any("is not reachable" in w for w in report["warnings"]))


if __name__ == "__main__":
    unittest.main()
