"""Tests for doctor --smoke: one throwaway target per golden image."""

from __future__ import annotations

import unittest
from typing import Any

from labfakes import FakeSystem, context

from pinka_lab import smoke

IMAGES: list[dict[str, Any]] = [
    {"fingerprint": "a" * 64, "aliases": [{"name": "_base/1.0.0"}]},
    {"fingerprint": "b" * 64, "aliases": [{"name": "web-nginx-flask/1.2.0"}]},
    {"fingerprint": "c" * 64, "aliases": [{"name": "web-nginx-flask/1.10.0"}]},
    {"fingerprint": "d" * 64, "aliases": [{"name": "web-nginx-flask/latest-ish"}]},
    {"fingerprint": "e" * 64, "aliases": None},
]
BLUEPRINT = {
    "name": "web-nginx-flask",
    "type": "container",
    "nesting": False,
    "services": ["nginx", "webapp"],
}
FOUND = {"data": {"result": [{"values": [["0", "line"]]}]}}


def healthy_host(**changes: Any) -> FakeSystem:
    commands: dict[str, Any] = {
        "incus image list --format json": (0, IMAGES),
        "incus network create *": (0, ""),
        "incus launch *": (0, ""),
        "incus config device add *": (0, ""),
        "incus exec * -- systemctl is-system-running --wait": (0, "running\n"),
        "incus exec * -- systemctl is-active *": (0, "active\n"),
        "incus exec * -- curl *": (7, "", "could not resolve host"),
        "incus network get * ipv4.address": (0, "10.0.0.1/24\n"),
        "incus exec * -- nc *": (1, ""),
        "incus delete *": (0, ""),
        "incus network delete *": (0, ""),
    }
    commands.update(changes)
    return FakeSystem(commands, http={"GET http://127.0.0.1:18702/*": (200, FOUND)})


def lines(system: FakeSystem, prefix: str) -> list[list[str]]:
    words = prefix.split()
    return [c for c in system.calls if c[: len(words)] == words]


class AliasTest(unittest.TestCase):
    def test_newest_alias(self) -> None:
        self.assertEqual(smoke.newest_alias(IMAGES, "web-nginx-flask"), "web-nginx-flask/1.10.0")
        self.assertEqual(smoke.newest_alias(IMAGES, "_base"), "_base/1.0.0")
        self.assertIsNone(smoke.newest_alias(IMAGES, "pg-primary"))


class SmokeTest(unittest.TestCase):
    def test_healthy_target(self) -> None:
        system = healthy_host()
        outcome = smoke.smoke_one(context(system), BLUEPRINT, "web-nginx-flask/1.10.0")
        self.assertEqual(outcome.result, "pass", outcome.detail)
        network = lines(system, "incus network create")[0]
        name = network[3]
        self.assertRegex(name, r"^r-[0-9a-f]{12}$")
        self.assertIn("ipv4.nat=false", network)
        self.assertIn("security.acls=pinka-run-default", network)
        launch = lines(system, "incus launch")[0]
        self.assertEqual(launch[2], "web-nginx-flask/1.10.0")
        self.assertEqual(launch[launch.index("--project") + 1], "bench")
        self.assertEqual(launch[launch.index("--profile") + 1], "target-container")
        self.assertEqual(launch[launch.index("--network") + 1], name)
        run_id = "smoke-" + name[2:]
        self.assertIn(
            f"environment.OTEL_RESOURCE_ATTRIBUTES=potka.run_id={run_id},potka.node=smoke", launch
        )
        self.assertIn("security.nesting=false", launch)
        nc_ports = [c[-1] for c in system.calls if "nc" in c]
        self.assertEqual([int(p) for p in nc_ports], list(smoke.LAB_PORTS))
        self.assertTrue(any(run_id in url for _, url, _ in system.http_calls))
        self.assertEqual(lines(system, "incus delete")[0][-1], launch[3])
        self.assertEqual(lines(system, "incus network delete")[0][-1], name)

    def test_problems_are_reported_and_everything_is_deleted(self) -> None:
        system = healthy_host(
            **{
                "incus exec * -- systemctl is-system-running --wait": (1, "degraded\n"),
                "incus exec * -- systemctl --failed --no-legend --plain": (
                    0,
                    "webapp.service failed\n",
                ),
                "incus exec * -- systemctl is-active *": (3, "inactive\n"),
                "incus exec * -- curl *": (0, ""),
                "incus exec * -- nc *": (0, ""),
            }
        )
        system.http_answers = {"GET http://127.0.0.1:18702/*": (200, {"data": {"result": []}})}
        outcome = smoke.smoke_one(context(system), BLUEPRINT, "web-nginx-flask/1.10.0")
        self.assertEqual(outcome.result, "fail")
        for text in (
            "systemd is degraded (webapp.service",
            "nginx is not active",
            "the internet is reachable",
            "the lab host answers on 10.0.0.1:8181",
            "no log line tagged smoke-",
        ):
            self.assertIn(text, outcome.detail)
        self.assertEqual(len(lines(system, "incus delete")), 1)
        self.assertEqual(len(lines(system, "incus network delete")), 1)

    def test_missing_tools_are_not_a_pass(self) -> None:
        system = healthy_host(
            **{
                "incus exec * -- curl *": (127, "", "curl: not found"),
                "incus exec * -- nc *": (127, "", "nc: not found"),
            }
        )
        outcome = smoke.smoke_one(context(system), BLUEPRINT, "web-nginx-flask/1.10.0")
        self.assertEqual(outcome.result, "fail")
        self.assertIn("curl is missing", outcome.detail)
        self.assertIn("nc is missing", outcome.detail)
        self.assertEqual(sum(1 for c in system.calls if "nc" in c), 1)
        system = healthy_host(**{"incus network get * ipv4.address": (0, "\n")})
        outcome = smoke.smoke_one(context(system), BLUEPRINT, "web-nginx-flask/1.10.0")
        self.assertIn("no IPv4 address", outcome.detail)

    def test_launch_failure_still_cleans_up(self) -> None:
        system = healthy_host(**{"incus launch *": (1, "", "image not found")})
        outcome = smoke.smoke_one(context(system), BLUEPRINT, "web-nginx-flask/1.10.0")
        self.assertEqual(outcome.result, "fail")
        self.assertIn("image not found", outcome.detail)
        self.assertEqual(len(lines(system, "incus network delete")), 1)

    def test_nesting_blueprint(self) -> None:
        system = healthy_host()
        blueprint = dict(BLUEPRINT, nesting=True, services=[])
        smoke.smoke_one(context(system), blueprint, "web-nginx-flask/1.10.0")
        self.assertIn("security.nesting=true", lines(system, "incus launch")[0])


class ChecksTest(unittest.TestCase):
    def test_one_check_per_blueprint(self) -> None:
        checks = smoke.smoke_checks([{"name": "_base"}, BLUEPRINT])
        self.assertEqual([c.id for c in checks], ["smoke._base", "smoke.web-nginx-flask"])
        self.assertTrue(all(c.severity == "error" for c in checks))

    def test_skips_and_missing_images(self) -> None:
        vm = smoke.smoke_checks([dict(BLUEPRINT, type="vm")])[0]
        self.assertEqual(vm.run(context(FakeSystem())).result, "skip")
        inference = smoke.smoke_checks([BLUEPRINT])[0]
        self.assertEqual(inference.run(context(FakeSystem(), node_role="inference")).result, "skip")
        missing = smoke.smoke_checks([dict(BLUEPRINT, name="pg-primary")])[0]
        outcome = missing.run(context(healthy_host()))
        self.assertEqual(outcome.result, "fail")
        self.assertIn("no image of pg-primary", outcome.detail)

    def test_runs_the_newest_image(self) -> None:
        system = healthy_host()
        outcome = smoke.smoke_checks([BLUEPRINT])[0].run(context(system))
        self.assertEqual(outcome.result, "pass", outcome.detail)
        self.assertEqual(lines(system, "incus launch")[0][2], "web-nginx-flask/1.10.0")


if __name__ == "__main__":
    unittest.main()
