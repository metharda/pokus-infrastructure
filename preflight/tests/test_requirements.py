"""The requirements computed from the choices (BUILD_PROMPT §2.3).

The numbers follow preflight/requirements.yml and the incus role's target
profiles; the fixtures of fixture_defs.py sit just above and just below the
thresholds these tests compute.
"""

from __future__ import annotations

import json
import os
import shlex
import tempfile
import unittest
from typing import Any

from helpers import TempRootCase, runner

from pinka_preflight import matrix
from pinka_preflight import requirements as reqs

GIB = 1024**3
MINIMUMS = matrix.load_matrix()["minimums"]
DATA = reqs.load_requirements()
DEFAULTS = DATA["defaults"]
GPU = {"gpu_inference": True, "vm_targets": True}
NO_GPU = {"gpu_inference": False, "vm_targets": True}
MODEL = {
    "id": "example-model",
    "hf_repo": "example-org/example-model",
    "revision": "0" * 40,
    "size_bytes": int(5.7 * GIB),
}


def df(mount: str, free_gib: float) -> str:
    """``df --output=target,fstype,size,avail -B1`` output."""
    return f"Mounted on Type 1B-blocks Avail\n{mount} ext4 {1000 * GIB} {int(free_gib * GIB)}\n"


class ComputeCase(TempRootCase):
    """A host root with / (and /var/lib) on one file system and /srv on another."""

    def setUp(self) -> None:
        """Create /srv and /var/lib, and the df answers."""
        super().setUp()
        self.mkdir("/srv")
        self.mkdir("/var/lib")
        self.free = {"/": 400.0, "/srv": 500.0}
        self.extra: dict[str, Any] = {}

    def compute(
        self,
        features: dict[str, bool],
        memory_gib: float | None = 64.0,
        **plan: Any,
    ) -> reqs.Requirements:
        commands = {
            f"df --output=target,fstype,size,avail -B1 {path}": df(mount, self.free[mount])
            for path, mount in (("/", "/"), ("/var/lib", "/"), ("/srv", "/srv"))
        }
        commands.update(self.extra)
        merged = {**json.loads(json.dumps(DEFAULTS)), **plan}
        return reqs.compute(
            DATA,
            merged,
            "test",
            minimums=MINIMUMS,
            features=features,
            memory_gib=memory_gib,
            fs=self.fs(),
            runner=runner(commands, which=self.extra.get("which", {})),
            machine="x86_64",
        )


class MemoryTest(ComputeCase):
    def items(self, req: reqs.Requirements) -> dict[str, tuple[float, float]]:
        return {i.id: (i.required_gib, i.recommended_gib) for i in req.memory_items}

    def test_defaults_on_a_full_node(self) -> None:
        req = self.compute(GPU)
        # 7.1 GiB summed stays below the floor; the training cap sets recommended.
        self.assertEqual((req.floor_gib, req.required_gib, req.recommended_gib), (8, 8, 37.2))
        self.assertEqual(self.items(req)["targets"], (3.0, 5.0))
        self.assertEqual(self.items(req)["training"], (0.0, 24.0))
        self.assertNotIn("vllm", self.items(req))
        self.assertIn("no model selected", " ".join(req.notes))
        self.assertEqual((req.unsupported, req.degraded), ([], []))

    def test_without_vm_targets_the_targets_are_containers(self) -> None:
        req = self.compute({"vm_targets": False})
        self.assertEqual(self.items(req)["targets"], (3.0, 3.0))
        self.assertEqual(req.recommended_gib, 35.2)

    def test_more_targets_raise_required(self) -> None:
        req = self.compute(GPU, concurrent_targets=4)
        self.assertEqual(req.required_gib, 10.1)
        self.assertEqual(self.items(req)["targets"], (6.0, 10.0))

    def test_a_served_model(self) -> None:
        req = self.compute(GPU, model=MODEL)
        self.assertEqual(self.items(req)["vllm"], (8.7, 24.0))
        self.assertNotIn("training", self.items(req))  # one at a time on the GPU
        self.assertEqual(req.required_gib, 15.8)

    def test_a_model_without_gpu_inference_counts_nothing(self) -> None:
        self.assertNotIn("vllm", self.items(self.compute(NO_GPU, model=MODEL)))

    def test_node_roles(self) -> None:
        lab = self.items(self.compute(GPU, node_role="lab", model=MODEL))
        self.assertNotIn("vllm", lab)
        self.assertNotIn("training", lab)
        self.assertIn("targets", lab)
        inference = self.items(self.compute(GPU, node_role="inference", model=MODEL))
        self.assertEqual(set(inference), {"base", "collectors", "install", "vllm"})
        self.assertEqual(inference["vllm"], (8.7, 10.7))  # no training on an inference node

    def test_thresholds_at_the_tolerance(self) -> None:
        cases = [
            (7.51, "unsupported", "floor"),
            (7.53, "degraded", ""),
            (34.95, "degraded", ""),
            (34.98, "", ""),
        ]
        for memory, kind, needle in cases:
            with self.subTest(memory=memory):
                req = self.compute(GPU, memory_gib=memory)
                self.assertEqual(bool(req.unsupported), kind == "unsupported")
                self.assertEqual(bool(req.degraded), kind == "degraded")
                self.assertIn(needle, " ".join(req.unsupported))
        # Four targets require 10.1 GiB: 9.494 GiB at 0.94.
        self.assertTrue(self.compute(GPU, memory_gib=9.49, concurrent_targets=4).unsupported)
        self.assertFalse(self.compute(GPU, memory_gib=9.50, concurrent_targets=4).unsupported)

    def test_a_nominal_16_gib_machine_serves_the_model(self) -> None:
        # 16 GiB machines report about 15.1-15.6 GiB (§2.2).
        req = self.compute(GPU, memory_gib=15.1, model=MODEL)
        self.assertEqual(req.unsupported, [])

    def test_choices_that_would_fit(self) -> None:
        text = " ".join(self.compute(GPU, memory_gib=9.0, concurrent_targets=4).unsupported)
        self.assertIn("These would fit: incus_concurrent_targets 3", text)
        text = " ".join(self.compute(GPU, memory_gib=12.0, model=MODEL).unsupported)
        self.assertIn("a model of at most 2.7 GiB", text)
        text = " ".join(self.compute(GPU, memory_gib=30.0).degraded)
        self.assertIn("training_caps.memory_max 18G", text)

    def test_no_training_hint_when_vllm_itself_does_not_fit(self) -> None:
        # 15 GiB: 15.96 GiB nominal, 13.2 GiB for the rest, vLLM recommends 10.7 GiB.
        text = " ".join(self.compute(GPU, memory_gib=15.0, model=MODEL).degraded)
        self.assertNotIn("training_caps.memory_max", text)

    def test_unknown_memory_is_a_warning(self) -> None:
        req = self.compute(GPU, memory_gib=None)
        self.assertEqual(req.unsupported, [])
        self.assertIn("MemTotal could not be read", req.warnings[0])


class DiskTest(ComputeCase):
    def mounts(self, req: reqs.Requirements) -> dict[str, reqs.MountCheck]:
        return {m.mount: m for m in req.disks}

    def test_items_per_mount_point(self) -> None:
        req = self.compute(GPU, model=MODEL)
        mounts = self.mounts(req)
        root = {i.id for i in mounts["/"].items}
        self.assertLessEqual({"pinka", "pool", "image:vllm", "golden-store:_base"}, root)
        self.assertNotIn("image:dcgm_exporter", root)  # the nvidia_smi exporter is chosen
        self.assertNotIn("image:refplat_job", root)  # tests only
        srv = {i.id for i in mounts["/srv"].items}
        self.assertEqual(srv, {"model:example-model"} | {f"golden:{b}" for b in DEFAULTS["images"]})
        vllm = next(i for i in mounts["/"].items if i.id == "image:vllm")
        size = DATA["images"]["vllm"]["sizes"]["linux/amd64"] * 2.5 / GIB
        self.assertAlmostEqual(vllm.size_gib, round(size, 2))
        self.assertEqual(vllm.basis, "estimated")

    def test_a_device_pool_counts_on_no_mount_point(self) -> None:
        req = self.compute(GPU, pool={"source": "device", "size_gib": 500})
        self.assertNotIn("pool", {i.id for m in req.disks for i in m.items})
        self.assertIn("dedicated device", " ".join(req.notes))

    def test_just_above_and_just_below(self) -> None:
        needed = self.mounts(self.compute(GPU))["/"].required_gib
        self.free["/"] = needed + 0.01
        self.assertEqual(self.compute(GPU).unsupported, [])
        self.free["/"] = needed - 0.01
        text = " ".join(self.compute(GPU).unsupported)
        self.assertIn(f"/ has {needed - 0.01:.2f} GiB free", text)
        self.assertIn("the Incus pool (loop file): 300.0 GiB, missing 0.1 GiB on /", text)

    def test_the_shortfall_falls_on_the_largest_items(self) -> None:
        self.free["/srv"] = 3.0
        req = self.compute(GPU, model=MODEL)
        srv = self.mounts(req)["/srv"]
        missing = {i.id: i.missing_gib for i in srv.items if i.missing_gib}
        self.assertEqual(missing["model:example-model"], 5.7)
        self.assertAlmostEqual(sum(missing.values()), srv.shortfall_gib, places=1)

    def test_what_is_on_the_host_needs_no_space(self) -> None:
        self.put("/var/lib/incus/disks/pinka-targets.img")
        self.put(f"/srv/pinka/models/example-org/example-model/{'0' * 40}/MANIFEST.json")
        self.mkdir("/srv/pinka/images/_base/1.0.0")
        vllm = DATA["images"]["vllm"]
        inspect = ["docker", "image", "inspect", "--format", "{{.Size}}"]
        self.extra = {
            shlex.join([*inspect, f"{vllm['image']}@{vllm['digest']}"]): str(20 * GIB),
            "which": {"docker": "/usr/bin/docker"},
        }
        items = {i.id: i for m in self.compute(GPU, model=MODEL).disks for i in m.items}
        for key in ("pool", "model:example-model", "golden:_base", "golden-store:_base"):
            self.assertTrue(items[key].present, key)
        self.assertFalse(items["golden:docker-host"].present)
        self.assertEqual(
            (items["image:vllm"].size_gib, items["image:vllm"].basis), (20.0, "measured")
        )
        self.assertTrue(items["image:vllm"].present)

    def test_unknown_free_space_is_a_warning(self) -> None:
        req = reqs.compute(
            DATA,
            json.loads(json.dumps(DEFAULTS)),
            "test",
            minimums=MINIMUMS,
            features=GPU,
            memory_gib=64.0,
            fs=self.fs(),
            runner=runner(),
            machine="x86_64",
        )
        self.assertEqual(req.unsupported, [])
        self.assertIn("could not be measured", " ".join(req.warnings))


class GrowthTest(ComputeCase):
    def test_retention_caps_each_store(self) -> None:
        growth = self.compute(GPU).growth
        assert growth is not None
        items = {i.id: i for i in growth.items}
        self.assertEqual(items["prometheus"].cap_gib, 4.5)  # 0.05 GiB a day for 90 days
        self.assertEqual(items["loki"].cap_gib, 9.0)
        self.assertIsNone(items["database"].cap_gib)
        self.assertEqual(items["training_runs"].per_run_gib, 16.0)
        self.assertEqual((growth.gib_per_day, growth.gib_per_week), (0.27, 1.89))

    def test_size_cap_and_shorter_retention(self) -> None:
        retention = {**DEFAULTS["retention"], "prometheus_days": 365, "prometheus_size_gib": 2}
        growth = self.compute(GPU, retention=retention).growth
        assert growth is not None
        self.assertEqual({i.id: i.cap_gib for i in growth.items}["prometheus"], 2.0)

    def test_the_day_the_disk_fills(self) -> None:
        self.free["/srv"] = 10.0
        growth = self.compute(GPU).growth
        assert growth is not None
        self.assertEqual(growth.free_after_install_gib, round(10.0 - 3.7, 2))
        # 0.27 GiB a day until the dumps' retention stops them on day 7 (1.89 GiB),
        # then 0.22 GiB a day: 6.3 GiB on day 28.
        self.assertEqual(growth.days_until_full, 28)
        self.free["/srv"] = 500.0
        growth = self.compute(GPU).growth
        assert growth is not None
        self.assertIsNone(growth.days_until_full)  # the database alone: 0.02 GiB a day

    def test_an_inference_node_grows_nothing_below_srv(self) -> None:
        growth = self.compute(GPU, node_role="inference").growth
        assert growth is not None
        self.assertEqual(growth.items, [])


class PlanTest(unittest.TestCase):
    def write(self, data: Any) -> str:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = os.path.join(directory.name, "plan.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        return path

    def test_defaults_without_a_file(self) -> None:
        plan, label = reqs.load_plan(None, DEFAULTS)
        self.assertEqual((plan, label), (DEFAULTS, "defaults"))
        plan, _ = reqs.load_plan(None, DEFAULTS, "/data/pinka")
        self.assertEqual(plan["srv_path"], "/data/pinka")

    def test_the_file_overrides_the_defaults(self) -> None:
        path = self.write({"schema_version": 1, "concurrent_targets": 5, "srv_path": "/data"})
        plan, label = reqs.load_plan(path, DEFAULTS, "/ignored")
        self.assertEqual((plan["concurrent_targets"], plan["srv_path"], label), (5, "/data", path))
        self.assertEqual(plan["retention"], DEFAULTS["retention"])

    def test_invalid_plans(self) -> None:
        invalid: tuple[Any, ...] = (
            [],
            {"schema_version": 2},
            {"schema_version": 1, "concurrent_targets": 0},
            {"schema_version": 1, "node_role": "storage"},
            {"schema_version": 1, "model": {"id": "x", "size_bytes": 1}},
            {"schema_version": 1, "unknown": True},
        )
        for data in invalid:
            with self.subTest(data=data), self.assertRaises(reqs.PlanError):
                reqs.load_plan(self.write(data), DEFAULTS)
        with self.assertRaises(reqs.PlanError):
            reqs.load_plan("/nonexistent/plan.json", DEFAULTS)

    def test_unknown_blueprint(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            from pinka_preflight.fs import FileSystem

            with self.assertRaises(reqs.PlanError):
                reqs.compute(
                    DATA,
                    {**DEFAULTS, "images": ["nonexistent"]},
                    "test",
                    minimums=MINIMUMS,
                    features=GPU,
                    memory_gib=64.0,
                    fs=FileSystem(root),
                    runner=runner(),
                    machine="x86_64",
                )


if __name__ == "__main__":
    unittest.main()
