"""The requirements computed from the choices.

The numbers follow preflight/requirements.yml and the incus role's target
profiles; the fixtures of fixture_defs.py sit just above and just below the
thresholds these tests compute.
"""

from __future__ import annotations

import copy
import json
import math
import os
import shlex
import tempfile
import unittest
from typing import Any

from helpers import TempRootCase, runner

from pinka_preflight import matrix, render, vram
from pinka_preflight import requirements as reqs
from pinka_preflight.requirements import MemoryItem

GIB = 1024**3
MINIMUMS = matrix.load_matrix()["minimums"]
VRAM_MINIMUMS = matrix.vram_minimums(matrix.load_matrix())
DATA = reqs.load_requirements()
DEFAULTS = DATA["defaults"]
GPU = {"gpu_inference": True, "gpu_training": True, "vm_targets": True}
NO_GPU = {"gpu_inference": False, "gpu_training": False, "vm_targets": True}
# The other parts' recommended memory with the defaults and VM targets: the
# core stack 8.2 GiB and two VM targets with their probe clients 5 GiB.
REST = 13.2


def caps(mode: str = "auto", memory_max_gib: float | None = None) -> dict[str, Any]:
    """The plan's training: the switch and the caps."""
    return {"mode": mode, "memory_max_gib": memory_max_gib, "run_storage_max_gib": 16}


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
        *,
        vram: float | None = 12.0,
        capability: str | None = "8.9",
        data: dict[str, Any] | None = None,
        unmet: dict[str, str] | None = None,
        driver_pending: bool = False,
        **plan: Any,
    ) -> reqs.Requirements:
        commands = {
            f"df --output=target,fstype,size,avail -B1 {path}": df(mount, self.free[mount])
            for path, mount in (("/", "/"), ("/var/lib", "/"), ("/srv", "/srv"))
        }
        commands.update(self.extra)
        merged = {**json.loads(json.dumps(DEFAULTS)), **plan}
        return reqs.compute(
            DATA if data is None else data,
            merged,
            "test",
            minimums=MINIMUMS,
            features=features,
            memory_gib=memory_gib,
            fs=self.fs(),
            runner=runner(commands, which=self.extra.get("which", {})),
            machine="x86_64",
            gpu_vram_gib=vram,
            gpu_compute_capability=capability,
            vram_minimums=VRAM_MINIMUMS,
            gpu_unmet=unmet,
            driver_pending=driver_pending,
        )


class MemoryTest(ComputeCase):
    def items(self, req: reqs.Requirements) -> dict[str, tuple[float, float]]:
        return {i.id: (i.required_gib, i.recommended_gib) for i in req.memory_items}

    def test_defaults_on_a_full_node(self) -> None:
        req = self.compute(GPU)
        # 7.1 GiB summed stays below the floor; the training cap sets recommended:
        # 64 GiB leave 50.8 GiB, so the computed cap is its ceiling.
        self.assertEqual((req.floor_gib, req.required_gib, req.recommended_gib), (8, 8, 37.2))
        self.assertEqual(self.items(req)["targets"], (3.0, 5.0))
        self.assertEqual(self.items(req)["training"], (0.0, 24.0))
        self.assertEqual((req.switches.runs, req.switches.memory_max_basis), ("gpu", "computed"))
        self.assertNotIn("vllm", self.items(req))
        self.assertIn("no model selected", " ".join(req.notes))
        self.assertEqual((req.unsupported, req.degraded), ([], []))

    def test_without_vm_targets_the_targets_are_containers(self) -> None:
        req = self.compute({"gpu_training": True, "vm_targets": False})
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
        self.assertNotIn("vllm", self.items(self.compute(NO_GPU, vram=None, model=MODEL)))

    def test_node_roles(self) -> None:
        lab = self.items(self.compute(GPU, node_role="lab", model=MODEL))
        self.assertNotIn("vllm", lab)
        self.assertNotIn("training", lab)
        self.assertIn("targets", lab)
        inference = self.items(self.compute(GPU, node_role="inference", model=MODEL))
        self.assertEqual(set(inference), {"base", "collectors", "install", "vllm"})
        self.assertEqual(inference["vllm"], (8.7, 10.7))  # no training on an inference node

    def test_thresholds_at_the_tolerance(self) -> None:
        # With a GPU: below 17.2 GiB the cap is its floor of 4 GiB, so the
        # defaults recommend 17.2 GiB there: 16.168 GiB at 0.94. Above it the
        # computed cap always fits.
        cases = [
            (7.51, "unsupported", "floor"),
            (7.53, "degraded", ""),
            (16.16, "degraded", ""),
            (16.18, "", ""),
            (17.5, "", ""),
            (64.0, "", ""),
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
        # 16 GiB machines report about 15.1-15.6 GiB.
        req = self.compute(GPU, memory_gib=15.1, model=MODEL)
        self.assertEqual(req.unsupported, [])

    def test_choices_that_would_fit(self) -> None:
        text = " ".join(self.compute(GPU, memory_gib=9.0, concurrent_targets=4).unsupported)
        self.assertIn("These would fit: incus_concurrent_targets 3", text)
        text = " ".join(self.compute(GPU, memory_gib=12.0, model=MODEL).unsupported)
        self.assertIn("a model of at most 2.7 GiB", text)
        text = " ".join(self.compute(GPU, memory_gib=30.0, training=caps("auto", 24)).degraded)
        self.assertIn("These would fit: training_caps.memory_max 18G, training: off", text)
        text = " ".join(self.compute(GPU, memory_gib=15.5).degraded)
        self.assertIn("training_caps.memory_max 3G, training: off", text)

    def test_no_training_hint_when_vllm_itself_does_not_fit(self) -> None:
        # 15 GiB: 15.96 GiB nominal, 13.2 GiB for the rest, vLLM recommends 10.7 GiB.
        text = " ".join(self.compute(GPU, memory_gib=15.0, model=MODEL).degraded)
        self.assertNotIn("training_caps.memory_max", text)

    def test_unknown_memory_is_a_warning(self) -> None:
        req = self.compute(GPU, memory_gib=None)
        self.assertEqual(req.unsupported, [])
        self.assertIn("MemTotal could not be read", req.warnings[0])
        self.assertEqual(req.switches.memory_max_gib, 4)  # the floor


class PoolTest(ComputeCase):
    """incus_pool_size: auto: the targets and golden images, limited by the free space."""

    def pool(self, req: reqs.Requirements) -> tuple[float, bool]:
        item = next(i for m in req.disks for i in m.items if i.id == "pool")
        return item.size_gib, item.present

    def test_defaults(self) -> None:
        # 3.7 GiB of golden images + 2 x (16 GiB VM + 4 GiB probe client), + 50 %.
        req = self.compute(GPU)
        self.assertEqual(req.pool["size_gib"], 66)
        self.assertEqual(req.pool["basis"], "computed")
        self.assertEqual(self.pool(req), (66.0, False))
        self.assertIn("+ 50% = 66 GiB", req.pool["detail"])
        containers = self.compute({**GPU, "vm_targets": False})
        self.assertEqual(containers.pool["size_gib"], 42)
        four = self.compute(GPU, concurrent_targets=4)
        self.assertEqual(four.pool["size_gib"], 126)  # (3.7 + 4 x 20) x 1.5, rounded up

    def test_the_build_instance_counts_when_larger(self) -> None:
        req = self.compute(GPU, concurrent_targets=1, images=["base"])
        # 0.5 GiB + the 16 GiB build instance (one 20 GiB target is larger).
        self.assertEqual(req.pool["size_gib"], 31)
        alone = self.compute({**GPU, "vm_targets": False}, concurrent_targets=1, images=["base"])
        self.assertEqual(alone.pool["size_gib"], 30)  # (0.5 + 16) x 1.5 = 24.75: the floor
        self.assertIn("the 16.0 GiB build instance is not larger", req.pool["detail"])
        self.assertIn(
            "the 16.0 GiB build instance's root disk (larger than 1 x (8.0 GiB target",
            alone.pool["detail"],
        )

    def test_the_free_space_limits_it(self) -> None:
        # / holds pinka (5 GiB), the images, the Incus image store and the 50 GiB
        # the loop file leaves beside it; /srv is apart.
        self.free["/"] = 110.0
        req = self.compute(GPU, inference="off", training=caps("off"))
        others = {i.id: i.size_gib for m in req.disks for i in m.items if m.mount == "/"}
        del others["pool"]
        self.assertEqual(others[reqs.RESERVE_ID], 50.0)
        self.assertEqual(req.pool["size_gib"], math.floor(110.0 - sum(others.values())))
        self.assertLess(req.pool["size_gib"], 66)
        self.assertIn("free on / after pinka's other needs there", req.pool["detail"])
        self.assertEqual(req.unsupported, [])

    def test_never_below_the_floor(self) -> None:
        self.free["/"] = 35.0
        req = self.compute(GPU, inference="off", training=caps("off"))
        self.assertEqual(req.pool["size_gib"], 30)
        self.assertIn("(the floor of 30 GiB)", req.pool["detail"])
        self.assertIn("the Incus pool (loop file): 30.0 GiB, missing", " ".join(req.unsupported))

    def test_one_file_system_keeps_room_for_the_stores(self) -> None:
        # /srv on / too: the stores' growth up to their retention (15.3 GiB) is pinka's.
        self.extra["df --output=target,fstype,size,avail -B1 /srv"] = df("/", 80.0)
        self.free["/"] = 80.0
        req = self.compute(GPU, inference="off", training=caps("off"))
        self.assertIn("15.3 GiB the stores hold at their retention", req.pool["detail"])
        self.assertLess(req.pool["size_gib"], 66)

    def test_an_explicit_size_a_device_and_an_existing_pool(self) -> None:
        req = self.compute(GPU, pool={"source": "loop", "size_gib": 300})
        self.assertEqual((req.pool["size_gib"], req.pool["basis"]), (300, "configured"))
        device = self.compute(GPU, pool={"source": "device", "size_gib": None})
        self.assertEqual(device.pool["source"], "device")
        ids = {i.id for m in device.disks for i in m.items}
        self.assertNotIn("pool", ids)
        self.assertNotIn(reqs.RESERVE_ID, ids)  # a dedicated device leaves no reserve
        image = "/var/lib/incus/disks/pinka-targets.img"
        self.put(image)
        self.extra[f"df --output=target,fstype,size,avail -B1 {image}"] = df("/", 400.0)
        existing = self.compute(GPU)
        self.assertEqual(self.pool(existing)[1], True)  # present: counts nothing
        self.assertIn("keeps its size", existing.pool["detail"])

    def test_a_lab_node_only(self) -> None:
        self.assertEqual(self.compute(GPU, node_role="inference").pool, {})


class SwitchTest(ComputeCase):
    """The inference and training switches."""

    def items(self, req: reqs.Requirements) -> dict[str, MemoryItem]:
        return {i.id: i for i in req.memory_items}

    def test_inference_off_counts_no_vllm_image_or_model(self) -> None:
        req = self.compute(GPU, inference="off", model=MODEL, training=caps("off"))
        self.assertEqual(set(self.items(req)) & {"vllm", "training"}, set())
        disk = {i.id for m in req.disks for i in m.items}
        self.assertEqual({i for i in disk if "vllm" in i or "model:" in i or "gpu" in i}, set())
        self.assertNotIn("image:cuda_base_test", disk)
        self.assertEqual(req.recommended_gib, REST)
        sw = req.switches.as_dict()
        self.assertEqual(sw["inference"], {
            "mode": "off", "serves": False, "reason": "switched off, inference: off",
        })  # fmt: skip
        self.assertEqual(sw["training"]["runs"], "off")

    def test_training_on_the_gpu_without_inference_keeps_the_gpu_images(self) -> None:
        req = self.compute(GPU, inference="off", model=MODEL)
        disk = {i.id for m in req.disks for i in m.items}
        self.assertLessEqual({"image:gpu_exporter", "image:cuda_base_test"}, disk)
        self.assertNotIn("image:vllm", disk)
        self.assertEqual(self.items(req)["training"].recommended_gib, 24)

    def test_training_counts_only_where_it_runs(self) -> None:
        # No usable GPU: auto leaves training off, cpu chooses the CPU-only variant.
        self.assertNotIn("training", self.items(self.compute(NO_GPU, vram=None)))
        self.assertEqual(self.compute(NO_GPU, vram=None).switches.runs, "off")
        cpu = self.compute(NO_GPU, vram=None, training=caps("cpu"))
        self.assertEqual(cpu.switches.runs, "cpu")
        self.assertEqual(self.items(cpu)["training"].title, "a training job (CPU-only variant)")
        self.assertEqual(self.items(cpu)["training"].recommended_gib, 24)
        # Switched off, or a node that does not train.
        self.assertNotIn("training", self.items(self.compute(GPU, training=caps("off"))))
        lab = self.compute(GPU, node_role="lab", training=caps("cpu"))
        self.assertEqual(lab.switches.training_reason, "only a full node trains, node_role lab")

    def test_the_effective_features(self) -> None:
        sw = reqs.switches({**DEFAULTS, "inference": "off"}, GPU)
        self.assertEqual(
            sw.effective(GPU), {"gpu_inference": False, "gpu_training": True, "vm_targets": True}
        )
        self.assertEqual(sw.unwanted(), {"gpu_inference": "inference: off"})
        cpu = reqs.switches({**DEFAULTS, "training": caps("cpu")}, NO_GPU)
        self.assertEqual(cpu.unwanted(), {"gpu_training": "training: cpu"})
        self.assertEqual(cpu.effective(NO_GPU)["gpu_training"], False)  # no job gets a GPU

    def test_an_inference_node_needs_inference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "plan.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(
                    {"schema_version": 1, "node_role": "inference", "inference": "off"}, handle
                )
            with self.assertRaisesRegex(reqs.PlanError, "needs inference: auto"):
                reqs.load_plan(path, DEFAULTS)


class TrainingCapTest(ComputeCase):
    """training_caps.memory_max: auto follows the host."""

    def cap(self, memory: float, **plan: Any) -> float:
        return self.compute(GPU, memory_gib=memory, **plan).switches.memory_max_gib

    def test_nominal_16_and_32_gib(self) -> None:
        # A nominal machine reports about 0.94 to 0.98 of it.
        for memory in (15.04, 15.1, 15.6):
            with self.subTest(memory=memory):
                self.assertEqual(self.cap(memory), 4)  # 1.8-2.4 GiB left: the floor
        for memory, cap in ((30.08, 16), (31.2, 18), (31.36, 18)):
            with self.subTest(memory=memory):
                self.assertEqual(self.cap(memory), cap)
        # vLLM never runs next to a job: the model does not lower the cap, and
        # the recommended value takes the larger of the two, never the sum.
        self.assertEqual(self.cap(15.6, model=MODEL), 4)
        self.assertEqual(self.cap(31.2, model=MODEL), 18)
        served = self.compute(GPU, memory_gib=31.2, model=MODEL)
        self.assertEqual(served.recommended_gib, REST + 18)
        self.assertEqual(served.degraded, [])
        vllm = next(i for i in served.memory_items if i.id == "vllm")
        self.assertEqual((vllm.required_gib, vllm.recommended_gib), (8.7, 18.0))
        self.assertNotIn("training", {i.id for i in served.memory_items})
        # Container targets (no vm_targets) leave 2 GiB more.
        req = self.compute({"gpu_training": True, "vm_targets": False}, memory_gib=31.2)
        self.assertEqual(req.switches.memory_max_gib, 20)

    def test_floor_ceiling_and_the_setting(self) -> None:
        self.assertEqual(self.cap(8.0), 4)
        self.assertEqual(self.cap(128.0), 24)
        req = self.compute(GPU, memory_gib=128.0, training=caps("auto", 48))
        self.assertEqual(
            (req.switches.memory_max_gib, req.switches.memory_max_basis), (48, "configured")
        )
        self.assertEqual(req.switches.as_dict()["training"]["memory_max"], "48G")
        self.assertEqual(reqs.size_text(0.5), "512M")

    def test_the_computed_cap_fits_the_recommended_value(self) -> None:
        for memory in (17.5, 20.0, 31.2, 37.2, 64.0):
            with self.subTest(memory=memory):
                req = self.compute(GPU, memory_gib=memory)
                self.assertLessEqual(req.recommended_gib, memory)
                self.assertEqual(req.degraded, [])
        detail = self.compute(GPU, memory_gib=31.2).switches.memory_max_detail
        self.assertEqual(
            detail,
            "MemTotal 31.20 GiB - 13.2 GiB recommended for the core stack and the targets = "
            "18.0 GiB, rounded down to whole GiB within 4 and 24 GiB",
        )


class DiskTest(ComputeCase):
    def mounts(self, req: reqs.Requirements) -> dict[str, reqs.MountCheck]:
        return {m.mount: m for m in req.disks}

    def test_items_per_mount_point(self) -> None:
        req = self.compute(GPU, model=MODEL)
        mounts = self.mounts(req)
        root = {i.id for i in mounts["/"].items}
        self.assertLessEqual({"pinka", "pool", "image:vllm", "golden-store:base"}, root)
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
        # An explicit pool: a computed one would follow the free space.
        pool = {"source": "loop", "size_gib": 300}
        needed = self.mounts(self.compute(GPU, pool=pool))["/"].required_gib
        self.free["/"] = needed + 0.01
        self.assertEqual(self.compute(GPU, pool=pool).unsupported, [])
        self.free["/"] = needed - 0.01
        text = " ".join(self.compute(GPU, pool=pool).unsupported)
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
        self.mkdir(f"/srv/pinka/images/base/{DATA['blueprints']['base']['version']}")
        vllm = DATA["images"]["vllm"]
        inspect = ["docker", "image", "inspect", "--format", "{{.Size}}"]
        self.extra = {
            shlex.join([*inspect, f"{vllm['image']}@{vllm['digest']}"]): str(20 * GIB),
            "which": {"docker": "/usr/bin/docker"},
        }
        items = {i.id: i for m in self.compute(GPU, model=MODEL).disks for i in m.items}
        for key in ("pool", "model:example-model", "golden:base", "golden-store:base"):
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


class LoopReserveTest(ComputeCase):
    """A loop pool's file leaves 50 GiB free on its mount point (pool loop_reserve_gib)."""

    POOL = {"source": "loop", "size_gib": 30}
    TITLE = "the free space kept beside the Incus pool (loop file)"

    def reserve(self, req: reqs.Requirements) -> reqs.DiskItem:
        return next(i for m in req.disks for i in m.items if i.id == reqs.RESERVE_ID)

    def test_never_below_50_gib(self) -> None:
        self.assertGreaterEqual(DATA["pool"]["loop_reserve_gib"], 50)
        path = os.path.join(reqs.PREFLIGHT_DIR, "schema", "requirements.schema.json")
        with open(path, encoding="utf-8") as handle:
            schema = json.load(handle)
        loop = schema["properties"]["pool"]["properties"]["loop_reserve_gib"]
        self.assertEqual(loop["minimum"], 50)

    def test_an_item_beside_the_loop_file(self) -> None:
        for pool in (self.POOL, {"source": "loop", "size_gib": None}):
            item = self.reserve(self.compute(GPU, pool=pool))
            self.assertEqual(
                (item.title, item.path, item.mount, item.size_gib, item.present, item.basis),
                (
                    self.TITLE,
                    "/var/lib/incus/disks/pinka-targets.img",
                    "/",
                    50.0,
                    False,
                    "estimated",
                ),
            )
            self.assertEqual(item.source, "loop_reserve_gib of preflight/requirements.yml pool")

    def test_none_beside_a_device_or_without_a_pool(self) -> None:
        device = self.compute(GPU, pool={"source": "device", "size_gib": None})
        inference = self.compute(GPU, node_role="inference")
        for req in (device, inference):
            self.assertNotIn(reqs.RESERVE_ID, {i.id for m in req.disks for i in m.items})

    def test_just_above_and_just_below(self) -> None:
        # The loop file, the reserve and what else goes on /: the reserve is the
        # largest item, so the shortfall falls on it first.
        needed = {m.mount: m for m in self.compute(GPU, pool=self.POOL).disks}["/"].required_gib
        others = needed - 30 - 50
        self.free["/"] = needed + 0.01  # the loop file leaves 50.01 GiB beside the others
        self.assertEqual(self.compute(GPU, pool=self.POOL).unsupported, [])
        self.free["/"] = needed - 0.01
        req = self.compute(GPU, pool=self.POOL)
        self.assertEqual(
            req.unsupported,
            [
                f"disk: / has {needed - 0.01:.2f} GiB free and these choices need "
                f"{needed:.2f} GiB there: {self.TITLE}: 50.0 GiB, missing 0.1 GiB on /"
            ],
        )
        self.assertGreater(others, 0)  # stricter than the free space less the file alone
        root = next(d for d in req.budgets()["disk"] if d["mount"] == "/")
        self.assertEqual((root["status"], root["shortfall_gib"]), ("does_not_fit", 0.01))

    def test_an_existing_loop_file_needs_none(self) -> None:
        # Like the file itself it counts nothing once the file exists: 1 GiB more
        # than the other items fits then, and is refused before.
        before = {m.mount: m for m in self.compute(GPU, pool=self.POOL).disks}["/"]
        others = before.required_gib - 30 - 50
        self.free["/"] = others + 1
        self.assertIn(self.TITLE, " ".join(self.compute(GPU, pool=self.POOL).unsupported))
        self.put("/var/lib/incus/disks/pinka-targets.img")
        req = self.compute(GPU, pool=self.POOL)
        self.assertTrue(self.reserve(req).present)
        self.assertEqual(req.unsupported, [])

    def test_auto_leaves_it_free(self) -> None:
        self.free["/"] = 120.0
        req = self.compute(GPU, inference="off", training=caps("off"))
        root = next(m for m in req.disks if m.mount == "/")
        self.assertEqual(req.unsupported, [])
        self.assertGreaterEqual(120.0 - root.required_gib, 0)
        others = sum(i.size_gib for i in root.items if i.id not in ("pool", reqs.RESERVE_ID))
        self.assertEqual(req.pool["size_gib"], math.floor(120.0 - others - 50))
        self.assertIn(
            f"({others:.1f} GiB at install, 50.0 GiB kept beside the file)", req.pool["detail"]
        )
        # Where even the floor does not fit, the detail says by how much.
        self.free["/"] = 60.0
        short = self.compute(GPU, inference="off", training=caps("off"))
        self.assertIn(
            f"; pinka's other needs on / ({others:.1f} GiB at install, 50.0 GiB kept beside "
            f"the file) exceed its free space by {others + 50 - 60:.1f} GiB, so 30 GiB "
            "(the floor of 30 GiB)",
            short.pool["detail"],
        )
        self.assertIn(self.TITLE, " ".join(short.unsupported))

    def test_an_existing_file_of_auto_counts_neither(self) -> None:
        image = "/var/lib/incus/disks/pinka-targets.img"
        self.put(image)
        self.extra[f"df --output=target,fstype,size,avail -B1 {image}"] = df("/", 400.0)
        req = self.compute(GPU)
        items = {i.id: i for m in req.disks for i in m.items}
        self.assertTrue(items["pool"].present and items[reqs.RESERVE_ID].present)
        self.assertIn("keeps its size", req.pool["detail"])

    def test_on_the_mount_point_of_the_loop_file(self) -> None:
        # /var/lib/incus on a file system of its own: the file and the reserve count there.
        self.mkdir("/var/lib/incus/disks")
        for path in ("/var/lib/incus", "/var/lib/incus/disks"):
            self.extra[f"df --output=target,fstype,size,avail -B1 {path}"] = df(
                "/var/lib/incus", 200.0
            )
        mounts = {m.mount: m for m in self.compute(GPU, pool=self.POOL).disks}
        incus = {i.id for i in mounts["/var/lib/incus"].items}
        self.assertLessEqual({"pool", reqs.RESERVE_ID}, incus)
        self.assertFalse({"pool", reqs.RESERVE_ID} & {i.id for i in mounts["/"].items})
        need = mounts["/var/lib/incus"].required_gib
        for path in ("/var/lib/incus", "/var/lib/incus/disks"):
            self.extra[f"df --output=target,fstype,size,avail -B1 {path}"] = df(
                "/var/lib/incus", need - 0.01
            )
        text = " ".join(self.compute(GPU, pool=self.POOL).unsupported)
        self.assertIn(f"{self.TITLE}: 50.0 GiB, missing 0.1 GiB on /var/lib/incus", text)

    def test_the_growth_does_not_count_it_as_used(self) -> None:
        # /srv on / too: the reserve stays free, so the stores may grow into it.
        self.extra["df --output=target,fstype,size,avail -B1 /srv"] = df("/", 400.0)
        req = self.compute(GPU, pool=self.POOL)
        assert req.growth is not None
        root = next(m for m in req.disks if m.mount == "/")
        self.assertEqual(
            req.growth.free_after_install_gib, round(400.0 - root.required_gib + 50, 2)
        )
        # Once the file exists, neither counts: nothing is added back.
        image = "/var/lib/incus/disks/pinka-targets.img"
        self.put(image)
        self.extra[f"df --output=target,fstype,size,avail -B1 {image}"] = df("/", 400.0)
        existing = self.compute(GPU, pool=self.POOL)
        assert existing.growth is not None
        root = next(m for m in existing.disks if m.mount == "/")
        self.assertEqual(
            existing.growth.free_after_install_gib, round(400.0 - root.required_gib, 2)
        )


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


ON_ELSE = "Or set inference: auto (this node then serves nothing, degraded with consent) or off."
PROFILE_FIX = "a GPU with at least 11.3 GiB of memory, or another serving profile"


class VramTest(ComputeCase):
    """The GPU memory's serving and training lines, and one status per resource."""

    def line(self, req: reqs.Requirements, use: str) -> dict[str, Any]:
        found: dict[str, Any] = req.budgets()["vram"][use]
        return found

    def statuses(self, req: reqs.Requirements) -> tuple[Any, Any]:
        vram = req.budgets()["vram"]
        return tuple((vram[use] or {}).get("status") for use in ("serving", "training"))  # type: ignore[return-value]

    def bigger_model(self, estimates: dict[str, float]) -> dict[str, Any]:
        data = copy.deepcopy(DATA)
        data["serving"]["models"]["example-model"] = estimates
        return data

    def test_the_support_matrix_minimums(self) -> None:
        # 8000 MiB, exactly.
        self.assertEqual(VRAM_MINIMUMS, {"gpu_inference": 7.8125, "gpu_training": 7.8125})

    def test_the_default_profile_fits_a_12_gib_gpu(self) -> None:
        req = self.compute(GPU, vram=12.0)
        self.assertEqual(self.statuses(req), ("fits", "fits"))
        serving = self.line(req, "serving")
        self.assertEqual(
            serving["items"],
            [
                {
                    "title": "the serving profile ada-12g",
                    "gib": 11.23,
                    "basis": "configured",
                    "mib": 11500,
                }
            ],
        )
        self.assertEqual(
            (serving["needed_gib"], serving["available_gib"], serving["shortfall_gib"]),
            (11.23, 12.0, 0.0),
        )
        training = self.line(req, "training")
        self.assertEqual((training["needed_gib"], training["items"][0]["mib"]), (7.81, 8000))
        self.assertEqual(self.line(req, "serving")["available_mib"], 12288.0)
        self.assertTrue(req.switches.serves)
        self.assertEqual((req.unsupported, req.degraded), ([], []))

    def test_a_model_bigger_than_the_gpu(self) -> None:
        data = self.bigger_model({"ada-12g": 14.0})
        why = "the model example-model needs 14.0 GiB of VRAM, the GPU has 12.0 GiB"
        auto = self.compute(GPU, vram=12.0, data=data, model=MODEL)
        line = self.line(auto, "serving")
        self.assertEqual((line["status"], line["reason"], line["shortfall_gib"]), ("off", why, 2.0))
        self.assertEqual(line["items"][-1]["basis"], "estimated")
        # auto: nothing is served or counted, and the host is degraded.
        self.assertFalse(auto.switches.serves)
        self.assertEqual(auto.switches.inference_reason, f"{why}, inference: auto")
        self.assertNotIn("vllm", [i.id for i in auto.memory_items])
        self.assertEqual(auto.unsupported, [])
        self.assertEqual(
            auto.degraded,
            [
                f"vram: {why}, so this node serves nothing (inference: auto). To serve, use a "
                "smaller model, or a GPU with at least 14.0 GiB of memory; set inference: off to "
                "go on without consent"
            ],
        )
        # on: refused with the shortfall and the remedy, and nothing is served
        # either (so --force-unsupported installs no model server that cannot start).
        on = self.compute(GPU, vram=12.0, data=data, model=MODEL, inference="on")
        self.assertEqual(self.line(on, "serving")["status"], "does_not_fit")
        self.assertFalse(on.switches.serves)
        self.assertNotIn("vllm", [i.id for i in on.memory_items])
        self.assertEqual(on.degraded, [])
        self.assertEqual(
            on.unsupported,
            [
                f"vram: {why} (2.0 GiB short); inference: on must serve on this node. Use a "
                f"smaller model, or a GPU with at least 14.0 GiB of memory. {ON_ELSE}"
            ],
        )
        # The profile's own minimum does not fit either: a smaller model cannot help.
        both = self.compute(GPU, vram=10.0, data=data, model=MODEL, inference="on")
        self.assertEqual(
            both.unsupported,
            [
                "vram: the model example-model needs 14.0 GiB of VRAM, the GPU has 10.0 GiB "
                "(4.0 GiB short); inference: on must serve on this node. Use a GPU with at least "
                f"14.0 GiB of memory, or another serving profile. {ON_ELSE}"
            ],
        )
        # The same model fits a bigger GPU, and one exactly its size.
        for size in (16.0, 14.0):
            self.assertEqual(
                self.statuses(self.compute(GPU, vram=size, data=data, model=MODEL))[0], "fits"
            )

    def test_the_serving_profile_decides_on_a_smaller_gpu(self) -> None:
        on = self.compute(GPU, vram=10.0, model=MODEL, inference="on")
        # 11.23 GiB, rounded up: a message never understates what is needed.
        why = "the serving profile ada-12g needs 11.3 GiB of VRAM, the GPU has 10.0 GiB"
        self.assertEqual(self.line(on, "serving")["reason"], why)
        self.assertEqual(self.line(on, "serving")["shortfall_gib"], 1.23)
        self.assertEqual(
            on.unsupported,
            [
                f"vram: {why} (1.3 GiB short); inference: on must serve on this node. Use "
                f"{PROFILE_FIX}. {ON_ELSE}"
            ],
        )
        near = self.line(self.compute(GPU, vram=11.2, inference="on"), "serving")
        self.assertEqual(
            near["reason"],
            "the serving profile ada-12g needs 11.3 GiB of VRAM, the GPU has 11.2 GiB",
        )
        # Two decimals where one would read the same.
        data = self.bigger_model({"ada-12g": 11.27})
        nearer = self.line(self.compute(GPU, vram=11.26, data=data, model=MODEL), "serving")
        self.assertEqual(
            nearer["reason"],
            "the model example-model needs 11.27 GiB of VRAM, the GPU has 11.26 GiB",
        )
        self.assertEqual(vram.at_least(11.23), "11.3 GiB")
        self.assertEqual(vram.at_least(8.0), "8.0 GiB")

    def test_an_inference_node_that_cannot_serve(self) -> None:
        # Refused under auto too: an inference node exists only to serve.
        req = self.compute(GPU, vram=10.0, node_role="inference")
        self.assertEqual(self.line(req, "serving")["status"], "does_not_fit")
        self.assertEqual(req.degraded, [])
        self.assertEqual(
            req.unsupported,
            [
                "vram: the serving profile ada-12g needs 11.3 GiB of VRAM, the GPU has 10.0 GiB "
                f"(1.3 GiB short); an inference node must serve. Use {PROFILE_FIX}. Or use this "
                "machine as a full or lab node."
            ],
        )
        self.assertTrue(reqs.must_serve({"inference": "auto", "node_role": "inference"}))
        self.assertFalse(reqs.must_serve({"inference": "auto", "node_role": "full"}))
        # Where no remedy precedes it, the alternative is a sentence of its own.
        node = {"inference": "auto", "node_role": "inference"}
        self.assertEqual(
            reqs.serve_or_else(node, after_remedy=False), "Use this machine as a full or lab node."
        )
        self.assertEqual(
            reqs.serve_or_else({"inference": "on", "node_role": "full"}, after_remedy=False),
            "Set inference: auto (this node then serves nothing, degraded with consent) or off.",
        )

    def test_a_model_without_settings_for_the_profile(self) -> None:
        data = self.bigger_model({"other-profile": 5.0})
        req = self.compute(GPU, vram=24.0, data=data, model=MODEL, inference="on")
        line = self.line(req, "serving")
        why = "the model example-model has no settings for the serving profile ada-12g"
        self.assertEqual(
            (line["status"], line["reason"], line["shortfall_gib"]), ("does_not_fit", why, 0.0)
        )
        self.assertEqual(
            req.unsupported,
            [
                f"vram: {why}; inference: on must serve on this node. Use a model with settings "
                f"for the serving profile, or another profile. {ON_ELSE}"
            ],
        )

    def test_a_model_the_catalog_does_not_know(self) -> None:
        req = self.compute(GPU, vram=12.0, model=MODEL, inference="on")
        line = self.line(req, "serving")
        self.assertEqual(line["status"], "fits")
        self.assertEqual([i["title"] for i in line["items"]], ["the serving profile ada-12g"])
        self.assertIn(
            "the default model example-model has no VRAM estimate in models.catalog.yml: only "
            "the serving profile ada-12g's minimum is checked against the GPU's memory",
            req.notes,
        )
        # Not where nothing is served, nor where no GPU could serve it.
        off = self.compute(GPU, vram=12.0, model=MODEL, inference="off")
        self.assertFalse(any("no VRAM estimate" in n for n in off.notes))
        no_gpu = self.compute(NO_GPU, vram=None, model=MODEL, inference="on")
        self.assertFalse(any("no VRAM estimate" in n for n in no_gpu.notes))

    def test_a_gpu_that_cannot_serve_or_train(self) -> None:
        small = {"gpu_inference": "VRAM >= 8000 MiB", "gpu_training": "VRAM >= 8000 MiB"}
        on = self.compute(NO_GPU, vram=6.0, inference="on", unmet=small)
        serving = self.line(on, "serving")
        self.assertEqual(
            (serving["status"], serving["reason"], serving["needed_gib"], serving["shortfall_gib"]),
            (
                "does_not_fit",
                "gpu_inference is not available on this host (unmet: VRAM >= 8000 MiB)",
                11.23,
                5.23,
            ),
        )
        self.assertEqual(
            [i["title"] for i in serving["items"]],
            ["the support matrix's minimum for gpu_inference", "the serving profile ada-12g"],
        )
        training = self.line(on, "training")
        self.assertEqual(
            (training["status"], training["reason"]),
            (
                "off",
                "gpu_training is not available on this host (unmet: VRAM >= 8000 MiB); training: cpu "
                "would run the CPU-only variant",
            ),
        )
        none = self.compute(NO_GPU, vram=None)
        serving = self.line(none, "serving")
        self.assertEqual(
            (
                serving["status"],
                serving["reason"],
                serving["available_gib"],
                serving["shortfall_gib"],
            ),
            ("off", "gpu_inference is not available on this host: no usable NVIDIA GPU", None, 0.0),
        )
        self.assertEqual(
            self.line(none, "training")["reason"],
            "gpu_training is not available on this host: no usable NVIDIA GPU; training: cpu "
            "would run the CPU-only variant",
        )
        # A GPU the support matrix refuses for another reason (its compute
        # capability), its memory measured.
        cc = self.compute(NO_GPU, vram=12.0, unmet={"gpu_inference": "compute capability >= 7.5"})
        self.assertEqual(
            (self.line(cc, "serving")["status"], self.line(cc, "serving")["reason"]),
            (
                "off",
                "gpu_inference is not available on this host (unmet: compute capability >= 7.5)",
            ),
        )
        unnamed = self.line(self.compute(NO_GPU, vram=12.0), "serving")
        self.assertEqual(unnamed["reason"], "gpu_inference is not available on this host")
        # Bootstrap installs the driver in this run: both lines are judged by the next run.
        pending = self.compute(NO_GPU, vram=None, inference="on", driver_pending=True)
        self.assertEqual(
            (self.line(pending, "serving")["status"], self.line(pending, "serving")["reason"]),
            (
                "off",
                "the NVIDIA driver is installed in this run: serving is judged once it is loaded, "
                "by the next bootstrap run",
            ),
        )
        self.assertEqual(
            (self.line(pending, "training")["status"], self.line(pending, "training")["reason"]),
            (
                "off",
                "the NVIDIA driver is installed in this run: training on the GPU is judged once "
                "it is loaded, by the next bootstrap run",
            ),
        )

    def test_a_node_that_serves_nothing_has_no_vram_reason(self) -> None:
        # A small GPU degrades nothing where nothing would be served here.
        plans: list[dict[str, Any]] = [{"inference": "off"}, {"node_role": "lab"}]
        for plan in plans:
            with self.subTest(plan=plan):
                req = self.compute(GPU, vram=8.0, **plan)
                self.assertEqual((req.unsupported, req.degraded), ([], []))

    def test_switched_off_and_the_node_roles(self) -> None:
        off = self.line(self.compute(GPU, vram=12.0, inference="off"), "serving")
        self.assertEqual(
            (off["status"], off["reason"], off["items"], off["shortfall_gib"]),
            ("off", "switched off, inference: off", [], 0.0),
        )
        cpu = self.line(self.compute(NO_GPU, vram=None, training=caps("cpu")), "training")
        self.assertEqual(
            (cpu["status"], cpu["reason"], cpu["items"]),
            (
                "off",
                "jobs run their CPU-only variant on the CPU (training: cpu); no GPU memory is used",
                [],
            ),
        )
        trained_off = self.line(self.compute(GPU, vram=12.0, training=caps("off")), "training")
        self.assertEqual(trained_off["reason"], "switched off, training: off")
        lab = self.compute(GPU, vram=12.0, node_role="lab").budgets()["vram"]
        self.assertEqual((lab["serving"], lab["training"], lab["gpu_gib"]), (None, None, None))
        inference = self.compute(GPU, vram=12.0, node_role="inference").budgets()["vram"]
        self.assertEqual((inference["serving"]["status"], inference["training"]), ("fits", None))

    def test_unknown_gpu_memory_and_profile(self) -> None:
        self.assertEqual(self.statuses(self.compute(GPU, vram=None)), ("unknown", "unknown"))
        with self.assertRaisesRegex(reqs.PlanError, "'nope' is not a serving profile"):
            self.compute(GPU, vram=12.0, vllm_profile="nope")

    def test_the_fits_the_installer_shows(self) -> None:
        serving = self.compute(GPU, vram=12.0).serving
        self.assertEqual((serving["profile"], serving["suggested_profile"]), ("ada-12g", "ada-12g"))
        self.assertEqual(
            serving["profiles"]["ada-12g"],
            {"min_vram_gib": 11.23, "compute_capability": "8.9", "fits": True},
        )
        self.assertEqual(
            serving["models"]["qwen3-8b-awq"],
            {"ada-12g": {"expected_vram_gib": 10.5, "fits": True}},
        )
        # Another compute capability: the profile fits but is not suggested.
        other = self.compute(GPU, vram=12.0, capability="8.6").serving
        self.assertEqual(
            (other["suggested_profile"], other["profiles"]["ada-12g"]["fits"]), (None, True)
        )
        # An unknown capability does not stop the suggestion.
        self.assertEqual(
            self.compute(GPU, vram=12.0, capability=None).serving["suggested_profile"], "ada-12g"
        )
        # An 11 GiB GPU: the model's estimate fits, the profile's minimum does not.
        eleven = self.compute(GPU, vram=11.0).serving
        self.assertEqual(eleven["suggested_profile"], None)
        self.assertEqual(eleven["models"]["qwen3-8b-awq"]["ada-12g"]["fits"], False)
        # The GPU's memory is not known: no answer either way.
        unknown = self.compute(NO_GPU, vram=None).serving
        self.assertEqual(unknown["suggested_profile"], None)
        self.assertEqual(unknown["profiles"]["ada-12g"]["fits"], None)
        self.assertEqual(unknown["models"]["qwen3-8b-awq"]["ada-12g"]["fits"], None)

    def test_the_summary_lines(self) -> None:
        unknown = self.compute(GPU, vram=None).as_dict()
        self.assertEqual(
            render.vram_lines(unknown, gpu_usable=True)[0], "  VRAM: the GPU's memory is not known"
        )
        self.assertEqual(render.vram_lines(unknown)[0], "  VRAM: no usable NVIDIA GPU")
        short = render.vram_lines(self.compute(GPU, vram=10.0, inference="on").as_dict())
        self.assertEqual(short[0], "  VRAM: the GPU has 10240 MiB, 10.0 GiB (measured)")
        # A configured minimum prints as stated, in MiB; an estimate in GiB, rounded up.
        model = self.bigger_model({"ada-12g": 10.47})
        fits = render.vram_lines(self.compute(GPU, vram=12.0, data=model, model=MODEL).as_dict())
        self.assertEqual(
            fits[1],
            "    serving (inference: auto): fits: the serving profile ada-12g 11500 MiB "
            "(configured); the model example-model 10.5 GiB (estimated)",
        )
        self.assertTrue(
            short[1].startswith("    serving (inference: on): does not fit, 1.3 GiB short: ")
        )

    def test_one_status_per_resource(self) -> None:
        memory = self.compute(GPU, vram=12.0).budgets()["memory"]
        self.assertEqual((memory["status"], memory["shortfall_gib"]), ("fits", 0.0))
        req = self.compute(GPU, memory_gib=30.0, training=caps("auto", 24))
        degraded = req.budgets()["memory"]
        self.assertEqual(
            (degraded["status"], degraded["shortfall_gib"]),
            ("degraded", reqs.gib(req.tolerance * req.recommended_gib - 30.0)),
        )
        req = self.compute(GPU, memory_gib=9.0, concurrent_targets=4)
        short = req.budgets()["memory"]
        self.assertEqual(
            (short["status"], short["shortfall_gib"]),
            ("does_not_fit", reqs.gib(req.tolerance * req.required_gib - 9.0)),
        )
        self.assertEqual(
            self.compute(GPU, memory_gib=None).budgets()["memory"]["status"], "unknown"
        )
        disks = {d["mount"]: d for d in self.compute(GPU).budgets()["disk"]}
        self.assertEqual({d["status"] for d in disks.values()}, {"fits"})
        self.free["/"] = 1.0
        req = self.compute(GPU)
        full = {d["mount"]: d for d in req.budgets()["disk"]}
        check = next(m for m in req.disks if m.mount == "/")
        self.assertEqual(full["/"]["status"], "does_not_fit")
        self.assertEqual(
            (full["/"]["free_gib"], full["/"]["required_gib"], full["/"]["shortfall_gib"]),
            (check.free_gib, check.required_gib, check.shortfall_gib),
        )
        self.assertGreater(check.shortfall_gib, 0)


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

    def test_inference_on(self) -> None:
        plan, _ = reqs.load_plan(self.write({"schema_version": 1, "inference": "on"}), DEFAULTS)
        self.assertEqual(plan["inference"], "on")
        node = {"schema_version": 1, "node_role": "inference"}
        self.assertEqual(
            reqs.load_plan(self.write({**node, "inference": "on"}), DEFAULTS)[0]["inference"], "on"
        )
        with self.assertRaisesRegex(reqs.PlanError, "it needs inference: auto or on"):
            reqs.load_plan(self.write({**node, "inference": "off"}), DEFAULTS)
        lab = {"schema_version": 1, "node_role": "lab", "inference": "on"}
        with self.assertRaises(reqs.PlanError) as caught:
            reqs.load_plan(self.write(lab), DEFAULTS)
        self.assertIn(reqs.LAB_ON, str(caught.exception))
        self.assertIn("set inference: on on the paired inference node instead", reqs.LAB_ON)
        for value in ("yes", True, "ON"):
            with self.subTest(value=value), self.assertRaises(reqs.PlanError):
                reqs.load_plan(self.write({"schema_version": 1, "inference": value}), DEFAULTS)
        with self.assertRaises(reqs.PlanError):
            reqs.load_plan(self.write({"schema_version": 1, "vllm_profile": "Bad Name"}), DEFAULTS)

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
