"""The LVM thin requirement of the Incus pool: node roles and actions per family."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from pinka_preflight import matrix


class NodeRoleTest(unittest.TestCase):
    def test_only_a_known_role_without_incus_skips_it(self) -> None:
        roles = ["full", "lab"]
        self.assertTrue(matrix.needed_on(roles, "full"))
        self.assertTrue(matrix.needed_on(roles, "lab"))
        self.assertFalse(matrix.needed_on(roles, "inference"))
        # an unexpected value or none fails closed: the requirement applies
        for role in (None, "", "Inference", "other"):
            self.assertTrue(matrix.needed_on(roles, role), role)
        self.assertTrue(matrix.needed_on(None, "inference"))

    def test_the_storage_backend_uses_the_same_rule(self) -> None:
        self.assertTrue(matrix.runs_incus("full"))
        self.assertTrue(matrix.runs_incus("lab"))
        self.assertFalse(matrix.runs_incus("inference"))
        for role in (None, "", "other"):
            self.assertTrue(matrix.runs_incus(role), role)

    def test_the_matrix_limits_the_requirement_to_incus_roles(self) -> None:
        spec = next(
            r for r in matrix.load_matrix()["core_requirements"] if r["id"] == "lvm_thin_pool"
        )
        self.assertEqual(spec["node_roles"], list(matrix.INCUS_ROLES))


class ActionTest(unittest.TestCase):
    def actions(self) -> dict[str, Any]:
        spec = next(
            r for r in matrix.load_matrix()["core_requirements"] if r["id"] == "lvm_thin_pool"
        )
        return dict(spec["actions"])

    def action(self, family: str | None, env_class: str = "baremetal") -> str:
        decision = SimpleNamespace(family=family)
        env = SimpleNamespace(env_class=env_class)
        return matrix._action(self.actions(), decision, env)  # type: ignore[arg-type]

    def test_every_family_has_its_own_action(self) -> None:
        wanted = {
            "debian": "linux-modules-$(uname -r)",
            "redhat": "kernel-modules-core-$(uname",
            "suse": "kernel-default",
            "archlinux": "linux or linux-lts",
        }
        for family, needle in wanted.items():
            self.assertIn(needle, self.action(family), family)

    def test_wsl2_comes_before_the_family_and_others_take_the_default(self) -> None:
        self.assertIn("wsl --update", self.action("debian", "wsl2"))
        self.assertIn("sudo modprobe dm_thin_pool", self.action("other"))
        self.assertIn("sudo modprobe dm_thin_pool", self.action(None))


if __name__ == "__main__":
    unittest.main()
