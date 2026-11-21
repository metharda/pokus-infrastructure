"""The requirements computed from the choices.

The numbers follow preflight/requirements.yml and the incus role's target
profiles; the fixtures of fixture_defs.py sit just above and just below the
thresholds these tests compute.
"""

from __future__ import annotations

import copy
import json
import os
import shlex
import tempfile
import unittest
from dataclasses import asdict
from typing import Any, cast

from helpers import TempRootCase, runner

from pinka_preflight import matrix, render, vram
from pinka_preflight import requirements as reqs
from pinka_preflight.detect.hardware import DiskInfo
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
    """The Incus pool: the sum of everything that may be written to it (ADR-0017)."""

    IMAGE = "/var/lib/incus/disks/pinka-targets.img"

    def pool(self, req: reqs.Requirements) -> tuple[float, bool]:
        item = next(i for m in req.disks for i in m.items if i.id == "pool")
        return item.size_gib, item.present

    def test_defaults(self) -> None:
        # bench 2 x (16 VM + 4 probe client + 500 MiB VM state) = 40.98, build
        # 2 x (16 + 500 MiB VM state) = 32.98,
        # 3 researchers x 20 = 60, (6 golden images + base + 1) x 3 GiB = 24,
        # metadata and its spare 2, LVM 1, headroom 20.
        req = self.compute(GPU)
        self.assertEqual(req.pool["size_gib"], 181)
        self.assertEqual((req.pool["basis"], req.pool["required_gib"]), ("computed", 181))
        self.assertEqual(req.pool["volume_size_gib"], 3.0)
        self.assertEqual(self.pool(req), (181.0, False))
        self.assertEqual(
            {p["id"]: p["size_gib"] for p in req.pool["parts"]},
            {
                "bench": 40.98,
                "build": 32.98,
                "personal": 60.0,
                "images": 24.0,
                "metadata": 2.0,
                "lvm": 1.0,
                "headroom": 20.0,
            },
        )
        self.assertTrue(req.pool["detail"].endswith("= 181 GiB"), req.pool["detail"])

    def test_every_choice_moves_its_part(self) -> None:
        cases: list[tuple[dict[str, Any], dict[str, bool], int]] = [
            ({}, {"vm_targets": False}, 164),  # bench 2 x (8 + 4) = 24
            ({"concurrent_targets": 4}, {}, 222),  # bench 81.95
            ({"researchers": 1}, {}, 141),
            ({"personal_disk_gib": 10.0}, {}, 151),
            ({"images": []}, {}, 160),  # one image volume left: a new version's
            ({"pool": {"source": "loop", "size_gib": None, "volume_size_gib": 4.0}}, {}, 189),
        ]
        for plan, features, size in cases:
            with self.subTest(plan=plan, features=features):
                req = self.compute({**GPU, **features}, **plan)
                self.assertEqual(req.pool["size_gib"], size)

    def test_volume_size_is_just_above_the_largest_blueprint(self) -> None:
        # docker-host 0.8 GiB x 2.5 = 2, rounded up, + 1 GiB filesystem; built or not.
        for images in (list(DEFAULTS["images"]), []):
            plan = {**DEFAULTS, "images": images}
            self.assertEqual(reqs.volume_size_gib(DATA, plan), 3.0)
        configured = {**DEFAULTS, "pool": {**DEFAULTS["pool"], "volume_size_gib": 5.0}}
        self.assertEqual(reqs.volume_size_gib(DATA, configured), 5.0)

    def test_never_cut_down(self) -> None:
        # / holds pinka, the images, the image store, the reserve and the file.
        self.free["/"] = 120.0
        req = self.compute(GPU, inference="off", training=caps("off"))
        self.assertEqual(req.pool["size_gib"], 181)
        self.assertIn("the Incus pool (loop file): 181.0 GiB", " ".join(req.unsupported))

    def test_an_explicit_size_and_a_new_device(self) -> None:
        # Each smaller than the sum: an operator action, before anything changes,
        # with the numbers and its remedies.
        req = self.compute(GPU, pool={"source": "loop", "size_gib": 300})
        self.assertEqual((req.pool["size_gib"], req.pool["basis"]), (300, "configured"))
        self.assertEqual((req.unsupported, req.needs_action), ([], []))
        for size, missing in ((150, "31.0"), (180.99, "0.1")):
            small = self.compute(GPU, pool={"source": "loop", "size_gib": size})
            (action,) = small.needs_action
            self.assertEqual(action["id"], "incus_pool_size")
            self.assertIn("incus_pool_size (", action["action"])
            self.assertIn(f"missing {missing} GiB", action["action"])
            self.assertIn("set incus_pool_size to auto or at least 181GiB", action["action"])
            self.assertIn("or lower the limits", action["action"])
            self.assertEqual(small.unsupported, [])
        exact = self.compute(GPU, pool={"source": "loop", "size_gib": 181})
        self.assertEqual(exact.needs_action, [])
        device = "/dev/disk/by-id/example-disk"
        # hand-written in blockdev's format (bytes and a newline), not recorded
        self.extra[f"blockdev --getsize64 {device}"] = f"{170 * GIB}\n"
        plan = {"source": "device", "size_gib": None, "device": device}
        on_device = self.compute(GPU, pool=plan)
        self.assertEqual(
            (on_device.pool["source"], on_device.pool["required_gib"]), ("device", 181)
        )
        ids = {i.id for m in on_device.disks for i in m.items}
        self.assertNotIn("pool", ids)
        self.assertNotIn(reqs.RESERVE_ID, ids)  # a dedicated device leaves no reserve
        (action,) = on_device.needs_action
        self.assertIn("device (170.0 GiB)", action["action"])
        self.assertIn("missing 11.0 GiB", action["action"])
        self.assertIn("a device of at least 181 GiB", action["action"])
        self.extra[f"blockdev --getsize64 {device}"] = f"{181 * GIB}\n"
        self.assertEqual(self.compute(GPU, pool=plan).needs_action, [])
        # A device size that cannot be read, or no device named: an operator action.
        del self.extra[f"blockdev --getsize64 {device}"]
        (unread,) = self.compute(GPU, pool=plan).needs_action
        self.assertIn(f"device {device} cannot be read", unread["action"])
        (unset,) = self.compute(GPU, pool={**plan, "device": ""}).needs_action
        self.assertIn("(incus_pool_device is not set) cannot be read", unset["action"])

    VG = "vgs --noheadings -o vg_name pinka-targets"
    THIN = "lvs --noheadings --units b --nosuffix -o lv_size pinka-targets/IncusThinPool"
    STAT = shlex.join(["stat", "--format=%s %b %B", "/var/lib/incus/disks/pinka-targets.img"])

    def existing_file(self, size_gib: float, allocated_gib: float) -> None:
        """An existing loop file; the stat answer is hand-written in GNU stat's format."""
        self.put(self.IMAGE)
        self.extra[f"df --output=target,fstype,size,avail -B1 {self.IMAGE}"] = df("/", 400.0)
        self.extra[self.STAT] = f"{int(size_gib * GIB)} {int(allocated_gib * GIB) // 512} 512\n"

    def test_an_existing_loop_file_without_its_volume_group(self) -> None:
        # No volume group answers: the thin pool cannot be measured, whatever the
        # file's size (its metadata may have been extended since).
        for size in (100, 181, 400):
            with self.subTest(size=size):
                self.existing_file(size, size)
                req = self.compute(GPU)
                self.assertEqual(self.pool(req)[1], True)  # present: counts nothing
                (action,) = req.needs_action
                self.assertIn("its volume group pinka-targets is not", action["action"])
                self.assertIn("cannot measure the pool's thin pool", action["action"])
        del self.extra[self.STAT]
        actions = " ".join(a["action"] for a in self.compute(GPU).needs_action)
        self.assertIn("cannot be read", actions)

    def test_the_plan_must_fit_the_existing_pool(self) -> None:
        self.existing_file(181, 181)
        self.extra[self.VG] = "  pinka-targets\n"
        self.extra[self.THIN] = f"{178 * GIB}\n"
        self.assertEqual(self.compute(GPU).needs_action, [])
        # An explicit size below the existing pool's: refused before anything changes.
        (action,) = self.compute(GPU, pool={"source": "loop", "size_gib": 150}).needs_action
        self.assertIn("below the existing Incus pool's size (181.0 GiB)", action["action"])
        self.assertIn("Incus refuses a smaller size", action["action"])
        self.assertIn("at least 181GiB", action["action"])
        self.assertEqual(
            self.compute(GPU, pool={"source": "loop", "size_gib": 200}).needs_action, []
        )
        # The plan names a device over an existing loop pool: refused.
        device = {"source": "device", "size_gib": None, "device": "/dev/disk/by-id/example-disk"}
        (action,) = self.compute(GPU, pool=device).needs_action
        self.assertIn("is a loop file", action["action"])
        self.assertIn("Set incus_pool_source: loop", action["action"])
        # The plan names a loop file over an existing device pool: refused.
        del self.extra[self.STAT]
        os.remove(os.path.join(self.root, self.IMAGE.lstrip("/")))
        self.extra["df --output=target,fstype,size,avail -B1 /var/lib/incus/disks"] = df("/", 400.0)
        (action,) = self.compute(GPU).needs_action
        self.assertIn("is on a dedicated device", action["action"])
        self.assertEqual(self.compute(GPU, pool=device).needs_action, [])

    def test_an_existing_pool_is_measured_by_its_thin_pool(self) -> None:
        # The data part of the sum: 181 less the metadata and spare (2) and LVM (1),
        # before rounding: 177.96 GiB.
        self.existing_file(181, 181)
        self.extra[self.VG] = "  pinka-targets\n"
        self.extra[self.THIN] = f"{150 * GIB}\n"
        (action,) = self.compute(GPU).needs_action
        self.assertIn("thin pool (150.0 GiB of data)", action["action"])
        self.assertIn("missing 28.0 GiB", action["action"])
        self.assertIn("size=209GiB", action["action"])  # the file grown by what is missing
        self.extra[self.THIN] = f"{178 * GIB}\n"
        self.assertEqual(self.compute(GPU).needs_action, [])
        # Whatever the plan's source: a device pool (no loop file) is measured too,
        # and growing its volume group and thin pool clears it.
        del self.extra[self.STAT]
        os.remove(os.path.join(self.root, self.IMAGE.lstrip("/")))
        self.extra["df --output=target,fstype,size,avail -B1 /var/lib/incus/disks"] = df("/", 400.0)
        device = {"source": "device", "size_gib": None, "device": "/dev/disk/by-id/example-disk"}
        self.extra[self.THIN] = f"{170 * GIB}\n"
        for plan in (device,):
            (action,) = self.compute(GPU, pool=plan).needs_action
            self.assertIn("vgextend pinka-targets <device>", action["action"])
            self.assertIn("lvextend -l +100%FREE pinka-targets/IncusThinPool", action["action"])
            self.assertIn("until it holds 178 GiB", action["action"])
        self.extra[self.THIN] = f"{178 * GIB}\n"
        self.assertEqual(self.compute(GPU, pool=device).needs_action, [])
        # A volume group whose thin pool cannot be measured: an operator action.
        del self.extra[self.THIN]
        (unread,) = self.compute(GPU, pool=device).needs_action
        self.assertIn("cannot be measured", unread["action"])
        self.extra[self.VG] = ("", 5)  # vgs fails without "not found"
        (unread,) = self.compute(GPU, pool=device).needs_action
        self.assertIn("cannot be measured", unread["action"])

    def test_a_sparse_existing_file_needs_its_unallocated_part(self) -> None:
        # Incus creates the file sparse; bootstrap preallocates the rest on its mount point.
        self.existing_file(181, 10)
        req = self.compute(GPU)
        items = {i.id: i for m in req.disks for i in m.items}
        self.assertEqual(items["pool-allocate"].size_gib, 171.0)
        self.assertFalse(items["pool-allocate"].present)
        self.assertTrue(items[reqs.RESERVE_ID].present)  # doctor's levels apply instead
        self.free["/"] = 100.0
        self.assertIn(
            "the part of the Incus pool's existing file still to allocate",
            " ".join(self.compute(GPU).unsupported),
        )

    def test_an_existing_file_on_btrfs_needs_no_cow(self) -> None:
        self.existing_file(181, 181)
        self.extra[self.VG] = "  pinka-targets\n"
        self.extra[self.THIN] = f"{178 * GIB}\n"
        self.extra[f"df --output=target,fstype,size,avail -B1 {self.IMAGE}"] = (
            f"Mounted on Type 1B-blocks Avail\n/ btrfs {1000 * GIB} {400 * GIB}\n"
        )
        lsattr = f"lsattr -d {self.IMAGE}"
        self.extra[lsattr] = f"---------------------- {self.IMAGE}\n"
        (action,) = self.compute(GPU).needs_action
        self.assertIn("on btrfs without No_COW", action["action"])
        self.assertIn("No_COW cannot be set on a file that holds data", action["action"])
        self.extra[lsattr] = f"---------------C------ {self.IMAGE}\n"
        self.assertEqual(self.compute(GPU).needs_action, [])
        del self.extra[lsattr]
        (unread,) = self.compute(GPU).needs_action
        self.assertIn("with an unreadable No_COW attribute", unread["action"])

    def test_an_unreadable_file_system_type_needs_action(self) -> None:
        self.extra["df --output=target,fstype,size,avail -B1 /var/lib"] = ("", 1)
        actions = " ".join(a["action"] for a in self.compute(GPU).needs_action)
        self.assertIn("file system type of the Incus pool's loop file location", actions)

    def test_a_loop_file_on_zfs_is_refused(self) -> None:
        # The file does not exist yet: its nearest existing directory, /var/lib, is on zfs.
        self.extra["df --output=target,fstype,size,avail -B1 /var/lib"] = (
            f"Mounted on Type 1B-blocks Avail\n/var/lib zfs {1000 * GIB} {900 * GIB}\n"
        )
        text = " ".join(self.compute(GPU).unsupported)
        self.assertIn("on zfs, which cannot preallocate it", text)

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
        # The free space where it falls short: rounded down to a tenth.
        self.assertIn(f"/ has {reqs.round_down(needed - 0.01, 1):.1f} GiB free", text)
        self.assertIn("the Incus pool (loop file): 300.0 GiB, missing 0.1 GiB on /", text)

    def test_a_need_just_above_a_tenth_reads_rounded_up(self) -> None:
        # Stored and printed: the need up (10.0004 reads 10.1, never 10.0), the
        # free space down and the shortfall up.
        req = self.compute(GPU)
        req.unsupported.clear()
        item = reqs.DiskItem(
            "model:x", "model x", "/srv/x", "", 10.0004, False, 0.0, "estimated", "a test"
        )
        info = DiskInfo("/srv/x", "/srv", "/srv", "ext4", 1000.0, 5.0, int(5.0049 * GIB))
        reqs._disks([(item, "/srv/x")], cast(Any, None), cast(Any, None), req, {"/srv/x": info})
        (srv,) = req.disks
        self.assertEqual((srv.required_gib, srv.free_gib, srv.shortfall_gib), (10.01, 5.0, 5.0))
        self.assertEqual(
            req.unsupported,
            [
                "disk: /srv has 5.0 GiB free and these choices need 10.1 GiB there: "
                "model x: 10.1 GiB, missing 5.0 GiB on /srv"
            ],
        )

    def test_a_whole_gib_need_one_byte_short_reads_short_everywhere(self) -> None:
        # Free space one byte below a need of exactly 10 GiB: no stored or printed
        # figure shows a fit (the free space reads 9.9, never 10.0).
        req = self.compute(GPU)
        req.unsupported.clear()
        item = reqs.DiskItem("model:x", "model x", "/srv/x", "", 10.0, False, 0.0, "estimated", "")
        info = DiskInfo("/srv/x", "/srv", "/srv", "ext4", 1000.0, 10.0, 10 * GIB - 1)
        reqs._disks([(item, "/srv/x")], cast(Any, None), cast(Any, None), req, {"/srv/x": info})
        (srv,) = req.disks
        self.assertEqual((srv.required_gib, srv.free_gib, srv.shortfall_gib), (10.0, 9.99, 0.01))
        self.assertEqual(
            req.unsupported,
            [
                "disk: /srv has 9.9 GiB free and these choices need 10.0 GiB there: "
                "model x: 10.0 GiB, missing 0.1 GiB on /srv"
            ],
        )
        lines = render._disk_lines(asdict(srv))
        self.assertEqual(
            lines[0],
            "  disk /srv (ext4): 9.9 GiB free, 10.0 GiB needed at install, 0.1 GiB missing",
        )

    def test_a_shortfall_of_float_noise_still_reads_short_and_names_an_item(self) -> None:
        # The exact comparison refuses a need above the free space by any amount,
        # even float noise of a sum: the stored shortfall and the item follow it.
        req = self.compute(GPU)
        req.unsupported.clear()
        a = reqs.DiskItem("model:a", "a", "/srv/a", "", 5.0, False, 0.0, "estimated", "")
        b = reqs.DiskItem("model:b", "b", "/srv/b", "", 1e-15, False, 0.0, "estimated", "")
        info = DiskInfo("/srv/a", "/srv", "/srv", "ext4", 1000.0, 5.0, 5 * GIB)
        entries = [(a, "/srv/a"), (b, "/srv/b")]
        reqs._disks(
            entries, cast(Any, None), cast(Any, None), req, {"/srv/a": info, "/srv/b": info}
        )
        (srv,) = req.disks
        self.assertEqual(srv.shortfall_gib, 0.01)
        self.assertEqual(req.budgets()["disk"][0]["status"], "does_not_fit")
        self.assertEqual((a.missing_gib, b.missing_gib), (0.01, 0.0))
        self.assertEqual(len(req.unsupported), 1)
        self.assertTrue(req.unsupported[0].endswith(": a: 5.0 GiB, missing 0.1 GiB on /srv"))

    def test_item_sizes_read_rounded_up_where_the_space_falls_short(self) -> None:
        # A 0.03 GiB image never reads 0.0 GiB beside a shortfall.
        mount = {
            "mount": "/",
            "fstype": "ext4",
            "free_gib": 1.0,
            "required_gib": 9.03,
            "shortfall_gib": 8.03,
            "items": [
                {"id": "image:a", "title": "a", "size_gib": 0.03, "present": False,
                 "basis": "estimated"},
                {"id": "pool", "title": "the pool", "size_gib": 9.0, "present": False,
                 "basis": "configured"},
            ],
        }  # fmt: skip
        lines = render._disk_lines(mount)
        self.assertIn("    container images (1): 0.1 GiB (estimated)", lines)
        self.assertIn("    the pool: 9.0 GiB (configured)", lines)
        fits = {**mount, "free_gib": 20.0, "shortfall_gib": 0}
        self.assertIn("    container images (1): 0.0 GiB (estimated)", render._disk_lines(fits))
        self.assertEqual(reqs.disk_size_text(0.03, True), "0.1 GiB")
        self.assertEqual(reqs.disk_size_text(0.03, False), "0.0 GiB")

    def edge(self, pool: dict[str, Any]) -> int:
        """The fewest whole bytes free on / that hold the plan's items there."""
        needed = self.mounts(self.compute(GPU, pool=pool))["/"].required_gib

        def fits(free: int) -> bool:
            self.free["/"] = free / GIB  # exact: the df answer is these bytes
            return not self.compute(GPU, pool=pool).unsupported

        low, high = int((needed - 0.02) * GIB), int((needed + 0.02) * GIB)
        self.assertFalse(fits(low))
        self.assertTrue(fits(high))
        while high - low > 1:
            middle = (low + high) // 2
            low, high = (low, middle) if fits(middle) else (middle, high)
        return high

    def test_one_byte_short_is_refused_and_shown(self) -> None:
        pool = {"source": "loop", "size_gib": 300}
        edge = self.edge(pool)
        self.free["/"] = edge / GIB
        req = self.compute(GPU, pool=pool)
        root = self.mounts(req)["/"]
        self.assertEqual((req.unsupported, root.shortfall_gib), ([], 0))
        self.assertEqual(req.budgets()["disk"][0]["status"], "fits")
        self.free["/"] = (edge - 1) / GIB
        req = self.compute(GPU, pool=pool)
        root = self.mounts(req)["/"]
        # Stored: at least 0.01 short, the need up and the free space down.
        self.assertEqual(root.shortfall_gib, 0.01)
        assert root.free_gib is not None
        self.assertLessEqual(root.free_gib, root.required_gib - 0.01)
        budget = req.budgets()["disk"][0]
        self.assertEqual((budget["mount"], budget["status"]), ("/", "does_not_fit"))
        need, free, short = reqs.disk_texts(root.required_gib, root.free_gib, root.shortfall_gib)
        self.assertEqual(short, "0.1 GiB")
        text = " ".join(req.unsupported)
        self.assertIn(f"disk: / has {free} free and these choices need {need} there: ", text)
        self.assertIn("the Incus pool (loop file): 300.0 GiB, missing 0.1 GiB on /", text)
        self.assertEqual(len(req.unsupported), 1)

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

    POOL = {"source": "loop", "size_gib": 200}
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
        # The loop file, the reserve and what else goes on /: the file is the
        # largest item, so the shortfall falls on it first.
        needed = {m.mount: m for m in self.compute(GPU, pool=self.POOL).disks}["/"].required_gib
        others = needed - 200 - 50
        self.free["/"] = needed + 0.01  # the loop file leaves 50.01 GiB beside the others
        self.assertEqual(self.compute(GPU, pool=self.POOL).unsupported, [])
        self.free["/"] = needed - 0.01
        req = self.compute(GPU, pool=self.POOL)
        stored = {m.mount: m for m in req.disks}["/"].required_gib  # the need, rounded up
        self.assertEqual(
            req.unsupported,
            [
                f"disk: / has {reqs.round_down(needed - 0.01, 1):.1f} GiB free and these choices "
                f"need {reqs.round_up(stored, 1):.1f} GiB there: the Incus pool (loop file): "
                "200.0 GiB, "
                "missing 0.1 GiB on /"
            ],
        )
        self.assertGreater(others, 0)  # stricter than the free space less the file alone
        root = next(d for d in req.budgets()["disk"] if d["mount"] == "/")
        self.assertEqual((root["status"], root["shortfall_gib"]), ("does_not_fit", 0.01))

    def test_a_new_loop_file_needs_it_free(self) -> None:
        # A new pool: the reserve is an item of its own, a shortfall like any other.
        before = {m.mount: m for m in self.compute(GPU, pool=self.POOL).disks}["/"]
        self.assertIn(self.TITLE, {i.title for i in before.items})
        self.free["/"] = before.required_gib - 1  # the reserve counted: 1 GiB short
        req = self.compute(GPU, pool=self.POOL)
        self.assertFalse(self.reserve(req).present)
        self.assertIn("disk: / has", " ".join(req.unsupported))
        self.free["/"] = before.required_gib
        self.assertEqual(self.compute(GPU, pool=self.POOL).unsupported, [])

    def test_an_existing_loop_file_gets_doctors_two_levels(self) -> None:
        # The same condition as doctor's incus.pool.loop_space_low and _full, the same
        # answer: what the file system keeps free after the install's other items,
        # below the reserve (50 GiB) a warning, below three image volumes (9 GiB) an
        # operator action; never a disk shortfall.
        before = {m.mount: m for m in self.compute(GPU, pool=self.POOL).disks}["/"]
        # exact, as preflight and doctor compare: the other items still to come on /
        others = sum(
            i.size_gib
            for i in before.items
            if not i.present and i.id not in ("pool", reqs.RESERVE_ID)
        )
        image = "/var/lib/incus/disks/pinka-targets.img"
        self.put(image)
        paths = ("/var/lib/incus", "/var/lib/incus/disks", image)

        def free(gib: float) -> reqs.Requirements:
            self.free["/"] = gib
            for path in paths:
                self.extra[f"df --output=target,fstype,size,avail -B1 {path}"] = df("/", gib)
            return self.compute(GPU, pool=self.POOL)

        self.extra[shlex.join(["stat", "--format=%s %b %B", image])] = (
            f"{200 * GIB} {200 * GIB // 512} 512\n"
        )
        warned = "below the 50.0 GiB kept beside the file (doctor's incus.pool.loop_space_low)"
        for left, warning, action in (
            (51, False, False),
            (50.001, False, False),
            (49.999, True, False),
            (9.001, True, False),
            (8.999, False, True),
            (0, False, True),
        ):
            with self.subTest(left=left):
                req = free(others + left)
                self.assertTrue(self.reserve(req).present)
                self.assertEqual(req.unsupported, [])
                self.assertEqual(any(warned in w for w in req.warnings), warning)
                ids = [a["id"] for a in req.needs_action]
                self.assertEqual("incus_pool_loop_space" in ids, action)
        found = free(others + 5).needs_action
        (entry,) = [a for a in found if a["id"] == "incus_pool_loop_space"]
        self.assertIn("below three image volumes (9.0 GiB)", entry["action"])
        self.assertIn("incus.pool.loop_space_full", entry["action"])

    def test_auto_counts_it_too(self) -> None:
        # The computed file is never cut down to leave the reserve: both count.
        self.free["/"] = 400.0
        req = self.compute(GPU, inference="off", training=caps("off"))
        root = next(m for m in req.disks if m.mount == "/")
        self.assertEqual(req.unsupported, [])
        items = {i.id: i.size_gib for i in root.items}
        self.assertEqual((items["pool"], items[reqs.RESERVE_ID]), (181.0, 50.0))

    def test_an_existing_file_of_auto_counts_only_the_reserve(self) -> None:
        image = "/var/lib/incus/disks/pinka-targets.img"
        self.put(image)
        self.extra[f"df --output=target,fstype,size,avail -B1 {image}"] = df("/", 400.0)
        self.extra[shlex.join(["stat", "--format=%s %b %B", image])] = (
            f"{181 * GIB} {181 * GIB // 512} 512\n"
        )
        req = self.compute(GPU)
        items = {i.id: i for m in req.disks for i in m.items}
        self.assertTrue(items["pool"].present)
        self.assertTrue(items[reqs.RESERVE_ID].present)  # doctor's levels apply instead
        self.assertNotIn("pool-allocate", items)
        self.assertEqual(req.unsupported, [])

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
        self.assertIn(
            "the Incus pool (loop file): 200.0 GiB, missing 0.1 GiB on /var/lib/incus", text
        )

    def test_the_growth_does_not_grow_into_it(self) -> None:
        # /srv on / too: the reserve stays free (doctor watches it), so the
        # stores do not count it as room to grow into, before or after the file exists.
        self.extra["df --output=target,fstype,size,avail -B1 /srv"] = df("/", 400.0)
        req = self.compute(GPU, pool=self.POOL)
        assert req.growth is not None
        root = next(m for m in req.disks if m.mount == "/")
        self.assertEqual(req.growth.free_after_install_gib, round(400.0 - root.required_gib, 2))
        image = "/var/lib/incus/disks/pinka-targets.img"
        self.put(image)
        self.extra[f"df --output=target,fstype,size,avail -B1 {image}"] = df("/", 400.0)
        self.extra[shlex.join(["stat", "--format=%s %b %B", image])] = (
            f"{200 * GIB} {200 * GIB // 512} 512\n"
        )
        existing = self.compute(GPU, pool=self.POOL)
        assert existing.growth is not None
        root = next(m for m in existing.disks if m.mount == "/")
        self.assertIn(reqs.RESERVE_ID, {i.id for i in root.items})
        # beside an existing file the reserve needs no space, and is still no room to grow
        self.assertEqual(
            existing.growth.free_after_install_gib, round(400.0 - root.required_gib - 50, 2)
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


class DiskTextTest(unittest.TestCase):
    def test_rounding_ignores_float_noise(self) -> None:
        self.assertEqual(reqs.round_up(94.60000000000001, 1), 94.6)
        self.assertEqual(reqs.round_down(94.6 - 1e-13, 1), 94.6)
        self.assertEqual(reqs.round_up(94.6 + 1e-13, 1), 94.6)
        self.assertEqual(reqs.round_up(94.61, 1), 94.7)
        self.assertEqual(reqs.round_down(94.59, 1), 94.5)
        self.assertEqual(reqs.round_up(0.000001, 2), 0.01)
        self.assertEqual(reqs.round_down(94.599, 2), 94.59)
        # One byte from a tenth is a byte, not float noise.
        self.assertEqual(reqs.round_up(10 + 1 / GIB, 1), 10.1)
        self.assertEqual(reqs.round_down(10 - 1 / GIB, 1), 9.9)

    def test_float_noise_left_after_the_items_names_no_more(self) -> None:
        big = reqs.DiskItem("a", "a", "/a", "/", 5.0, False, 0.0, "estimated", "")
        small = reqs.DiskItem("b", "b", "/b", "/", 3.0, False, 0.0, "estimated", "")
        reqs._distribute([big, small], 5.0 + 1e-15)
        self.assertEqual((big.missing_gib, small.missing_gib), (5.0, 0.0))
        reqs._distribute([big, small], 5.0 + 1 / GIB)  # a byte more: named
        self.assertEqual((big.missing_gib, small.missing_gib), (5.0, 0.01))

    def test_where_it_fits_both_to_the_nearest_tenth(self) -> None:
        self.assertEqual(reqs.disk_texts(94.56, 94.64, 0), ("94.6 GiB", "94.6 GiB", ""))
        self.assertEqual(reqs.disk_texts(9.44, None, 0), ("9.4 GiB", "-", ""))

    def test_where_it_falls_short_need_up_free_down_shortfall_up(self) -> None:
        self.assertEqual(reqs.disk_texts(94.6, 94.59, 0.01), ("94.6 GiB", "94.5 GiB", "0.1 GiB"))
        self.assertEqual(reqs.disk_texts(9.41, 3.09, 6.32), ("9.5 GiB", "3.0 GiB", "6.4 GiB"))
        self.assertEqual(reqs.disk_texts(9.4, None, 9.4), ("9.4 GiB", "-", "9.4 GiB"))

    def test_the_growth_line_names_why_no_date(self) -> None:
        growth: dict[str, Any] = {
            "days_until_full": 28,
            "gib_per_day": 0.27,
            "free_after_install_gib": 6.3,
        }
        self.assertEqual(render.growth_when(growth), "full in about 28 days at this rate")
        growth["days_until_full"] = None
        self.assertEqual(
            render.growth_when(growth),
            "the disk does not fill within ten years, the retention's limits counted",
        )
        growth["free_after_install_gib"] = None
        self.assertEqual(
            render.growth_when(growth),
            "when the disk fills is not known: its free space is not measured",
        )
        growth["gib_per_day"] = 0
        self.assertEqual(render.growth_when(growth), "nothing grows there")


if __name__ == "__main__":
    unittest.main()
