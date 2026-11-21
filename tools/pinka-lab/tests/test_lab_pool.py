"""The Incus pool's count and doctor's pool checks.

The fixtures in data/pool/ are hand-written (data/pool/README.md) in the
formats of ``lvs --noheadings --units b --nosuffix --separator ,`` and of Incus
6.0.7's API listings, with every case the design note lists
(docs/explanation/incus-pool-accounting.md).
"""

from __future__ import annotations

import json
import os
import unittest
from typing import Any

from labfakes import FakeSystem, lab_data

from pinka_lab import checks, pool
from pinka_lab.config import LabConfig
from pinka_lab.doctor import Context

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "pool")
GIB = pool.GIB
MIB = 1024**2
LVS = (
    "lvs --noheadings --units b --nosuffix --separator , -o "
    + ",".join(pool.LVS_FIELDS)
    + " pinka-targets"
)


def text(name: str) -> str:
    with open(os.path.join(DATA, name), encoding="utf-8") as handle:
        return handle.read()


def listing(name: str) -> Any:
    return json.loads(text(f"incus-{name}.json"))


def recorded() -> tuple[
    pool.LogicalVolume, list[pool.LogicalVolume], dict[str, Any], dict[str, Any]
]:
    lvs = pool.parse_lvs(text("lvs.txt"))
    thin = next(lv for lv in lvs if lv.name == "IncusThinPool")
    expected = pool.expected_volumes(listing("volumes"), listing("instances"), 3 * GIB)
    limits = {
        p["name"]: pool.project_limit(p["config"], "pinka-targets") for p in listing("projects")
    }
    return thin, lvs, expected, limits


class NamesTest(unittest.TestCase):
    def test_incus_lvm_volume_names(self) -> None:
        # lvmFullVolumeName: hyphens doubled, the snapshot delimiter as a hyphen.
        self.assertEqual(
            pool.lv_name("container", "filesystem", "user-1001_c1"), "containers_user--1001_c1"
        )
        self.assertEqual(
            pool.lv_name("container", "filesystem", "bench_t1/snap0"), "containers_bench_t1-snap0"
        )
        self.assertEqual(
            pool.lv_name("virtual-machine", "block", "vm1"), "virtual-machines_vm1.block"
        )
        self.assertEqual(pool.lv_name("custom", "iso", "default_os"), "custom_default_os.iso")
        self.assertEqual(pool.lv_name("image", "filesystem", "fp1"), "images_fp1")

    def test_the_default_project_has_no_prefix_but_custom_volumes_do(self) -> None:
        expected = pool.expected_volumes(
            [
                {
                    "name": "a",
                    "project": "default",
                    "type": "container",
                    "content_type": "filesystem",
                },
                {"name": "v", "project": "default", "type": "custom", "content_type": "filesystem"},
            ],
            [],
        )
        self.assertEqual(sorted(expected), ["containers_a", "custom_default_v"])

    def test_sizes(self) -> None:
        self.assertEqual(pool.size_bytes("3GiB"), 3 * GIB)
        self.assertEqual(pool.size_bytes("500MiB"), 500 * MIB)
        self.assertEqual(pool.size_bytes("10GB"), 10 * 1000**3)
        self.assertEqual(pool.size_bytes("1073741824"), GIB)
        with self.assertRaises(pool.PoolError):
            pool.size_bytes("lots")

    def test_an_unexpected_lvs_row_is_an_error(self) -> None:
        with self.assertRaises(pool.PoolError):
            pool.parse_lvs("  images_fp1,3221225472,IncusThinPool\n")


class CountTest(unittest.TestCase):
    def setUp(self) -> None:
        self.thin, self.lvs, self.expected, self.limits = recorded()
        self.count = pool.count(self.thin, self.lvs, self.expected, self.limits)

    def test_inactive_and_active_images_count_at_their_full_size(self) -> None:
        # fp1 and fp3 are inactive (no Data%), fp2 is 30 % used: all at 3 GiB; the
        # VM image with both its volumes (500 MiB + 3 GiB).
        self.assertEqual(self.count.images, 9 * GIB + 500 * MIB + 3 * GIB)

    def test_a_listed_volume_not_created_yet_counts_at_its_size(self) -> None:
        # bench: t1 8 + vm1 (500 MiB state + 16 GiB) + t3, listed without a volume, 8:
        # more than its 24 GiB limit, so no room is left on top.
        self.assertEqual(self.count.projects["bench"], 32 * GIB + 500 * MIB)

    def test_a_volume_larger_than_configured_counts_at_its_real_size(self) -> None:
        # user-1001: c1 4 + c2 6 (configured 4), and the 12 GiB its 20 GiB limit still leaves.
        self.assertEqual(self.count.projects["user-1001"], 22 * GIB)

    def test_the_room_a_limit_leaves_counts(self) -> None:
        self.assertEqual(self.count.projects["build"], 32 * GIB)  # b1 16 + 16 left

    def test_a_per_pool_limit_is_taken_when_smaller(self) -> None:
        self.assertEqual(self.limits["extra"], 10 * GIB)
        self.assertEqual(self.count.projects["extra"], 10 * GIB)  # scratch 5 + 5 left
        self.assertEqual(
            pool.project_limit({"limits.disk.pool.other": "1GiB"}, "pinka-targets"), None
        )
        self.assertEqual(
            pool.project_limit(
                {"limits.disk": "8GiB", "limits.disk.pool.pinka-targets": "9GiB"}, "pinka-targets"
            ),
            8 * GIB,
        )

    def test_snapshots_temporary_volumes_buckets_and_unlimited_projects_count_in_full(self) -> None:
        # snapshot 8 + .incustmp 8 + bucket 2 + the default project's custom volume 10.
        self.assertEqual(self.count.rest, 28 * GIB)
        self.assertEqual(
            sorted(self.count.unmatched),
            ["buckets_default_b", "containers_bench_t1-snap0", "containers_bench_t2.incustmp"],
        )

    def test_volumes_outside_the_thin_pool_do_not_count(self) -> None:
        self.assertNotIn("other", self.count.unmatched)

    def test_the_uncommitted_space(self) -> None:
        total = (
            (12 * GIB + 500 * MIB)
            + (32 * GIB + 500 * MIB)
            + 32 * GIB
            + 22 * GIB
            + 10 * GIB
            + 28 * GIB
        )
        self.assertEqual(self.count.total, total)
        self.assertEqual(self.count.data_size, 121 * GIB)
        self.assertEqual(self.count.uncommitted, 121 * GIB - total)
        self.assertIn("project user-1001 22.0 GiB", self.count.describe())

    def test_nothing_reads_usage(self) -> None:
        # The same pool with every volume inactive (no Data%) counts the same.
        inactive = [
            pool.LogicalVolume(
                lv.name, lv.size, lv.pool_lv, None, lv.metadata_percent, lv.metadata_size
            )
            for lv in self.lvs
        ]
        again = pool.count(self.thin, inactive, self.expected, self.limits)
        self.assertEqual(again.total, self.count.total)

    def test_an_image_being_unpacked_counts_in_full_before_incus_lists_it(self) -> None:
        unpacking = [
            *self.lvs,
            pool.LogicalVolume("images_new", 3 * GIB, "IncusThinPool", 4.0, None, None),
        ]
        again = pool.count(self.thin, unpacking, self.expected, self.limits)
        self.assertEqual(again.total, self.count.total + 3 * GIB)
        self.assertIn("images_new", again.unmatched)


class LimitsTest(unittest.TestCase):
    """What counts as a limit, and volumes listed before they exist (small cases)."""

    THIN = pool.LogicalVolume("IncusThinPool", 100 * GIB, "", 1.0, 5.0, GIB)

    def lv(self, name: str, size: int) -> pool.LogicalVolume:
        return pool.LogicalVolume(name, size, "IncusThinPool", None, None, None)

    def test_an_instance_volume_without_its_instance_counts_outside_every_limit(self) -> None:
        # Listed by the pool, not by the instances (deleted, renamed or moved between the
        # two listings, or a record an interrupted delete left): Incus counts only the
        # instances it holds against a limit (permissions.go:1438-1441, 1520-1529), so
        # the volume must not take the project's room.
        volumes = [
            {"name": "gone", "project": "p", "type": "container", "content_type": "filesystem"},
            {"name": "vm", "project": "p", "type": "virtual-machine", "content_type": "block"},
        ]
        expected = pool.expected_volumes(volumes, [], 3 * GIB)
        self.assertEqual({project for _, project, _ in expected.values()}, {""})
        limits = {"p": 20 * GIB}
        # With its LV, larger than counted: at its real size, outside the limit.
        rows = [self.THIN, self.lv("containers_p_gone", 6 * GIB)]
        found = pool.count(self.THIN, rows, expected, limits, 4 * MIB)
        self.assertEqual(found.projects["p"], 20 * GIB)  # the whole limit is still room
        self.assertEqual(found.rest, 6 * GIB + 500 * MIB + 3 * GIB)  # the VM's not created yet
        # Without its LV: at its counted size, outside the limit, never inside it.
        found = pool.count(self.THIN, [self.THIN], expected, limits, 4 * MIB)
        self.assertEqual(found.projects["p"], 20 * GIB)
        self.assertEqual(found.rest, 3 * GIB + 500 * MIB + 3 * GIB)
        # With its instance listed it is the project's again.
        instances = [{"name": "gone", "project": "p", "type": "container", "expanded_devices": {}}]
        listed = pool.expected_volumes(volumes[:1], instances, 3 * GIB)
        self.assertEqual({project for _, project, _ in listed.values()}, {"p"})

    def test_only_limits_disk_counts_and_the_per_pool_key_only_lowers_it(self) -> None:
        # Incus 6.0.7 skips every disk check of a custom volume without
        # limits.disk (permissions.go:345-348, 1089-1092).
        only_pool = {"limits.disk.pool.pinka-targets": "10GiB"}
        self.assertIsNone(pool.project_limit(only_pool, "pinka-targets"))
        both = {"limits.disk": "50GiB", "limits.disk.pool.pinka-targets": "10GiB"}
        self.assertEqual(pool.project_limit(both, "pinka-targets"), 10 * GIB)
        self.assertEqual(pool.project_limit({"limits.disk": "8GiB"}, "pinka-targets"), 8 * GIB)
        self.assertIsNone(pool.project_limit({}, "pinka-targets"))
        # A pool-key-only project counts its volumes at their full size and reserves no room.
        expected = pool.expected_volumes(
            [
                {
                    "name": "v",
                    "project": "p",
                    "type": "custom",
                    "content_type": "filesystem",
                    "config": {"size": "4GiB"},
                }
            ],
            [],
            3 * GIB,
        )
        found = pool.count(
            self.THIN, [self.THIN, self.lv("custom_p_v", 4 * GIB)], expected, {"p": None}
        )
        self.assertEqual((found.rest, found.projects), (4 * GIB, {}))

    def test_a_listed_image_not_created_yet_counts_at_volume_size(self) -> None:
        # Incus writes the image's database row before its volume (backend.go:3929, 3952).
        volumes = [
            {"name": "c0", "project": "default", "type": "image", "content_type": "filesystem"},
            {"name": "v0", "project": "default", "type": "image", "content_type": "block"},
        ]
        expected = pool.expected_volumes(volumes, [], 3 * GIB)
        found = pool.count(self.THIN, [self.THIN], expected, {})
        # The container image's volume, and the VM image's block volume with its state volume.
        self.assertEqual(found.images, 3 * GIB + 3 * GIB + 500 * MIB)

    def test_a_listed_volume_of_a_project_without_a_limit_counts_at_its_size(self) -> None:
        volumes: list[dict[str, Any]] = [
            {
                "name": "d",
                "project": "default",
                "type": "custom",
                "content_type": "filesystem",
                "config": {"size": "7GiB"},
            },
            {"name": "c", "project": "default", "type": "container", "content_type": "filesystem"},
        ]
        instances = [
            {
                "name": "c",
                "project": "default",
                "type": "container",
                "expanded_devices": {"root": {"type": "disk", "path": "/", "size": "5GiB"}},
            },
        ]
        found = pool.count(
            self.THIN, [self.THIN], pool.expected_volumes(volumes, instances, 3 * GIB), {}
        )
        self.assertEqual(found.rest, 12 * GIB)

    def test_a_volume_not_created_yet_counts_in_whole_extents(self) -> None:
        # LVM rounds a logical volume up to whole extents; Incus counts the size itself.
        volumes = [
            {
                "name": "d",
                "project": "p",
                "type": "custom",
                "content_type": "filesystem",
                "config": {"size": f"{GIB + 1}"},
            }
        ]
        expected = pool.expected_volumes(volumes, [], 3 * GIB)
        unlimited = pool.count(self.THIN, [self.THIN], expected, {}, 4 * MIB)
        self.assertEqual(unlimited.rest, GIB + 4 * MIB)
        limited = pool.count(self.THIN, [self.THIN], expected, {"p": 2 * GIB}, 4 * MIB)
        # the volume in extents, plus what the limit leaves beyond Incus's count of it
        self.assertEqual(limited.projects["p"], GIB + 4 * MIB + (2 * GIB - (GIB + 1)))

    def test_an_unreadable_extent_fails_the_checks(self) -> None:
        system = pool_system(extra={EXTENT: (5, "", "Volume group not found")})
        with self.assertRaises(RuntimeError):
            outcome(checks.check_incus_pool_overcommitted, system)

    def test_a_vm_without_size_state_counts_incus_default(self) -> None:
        volumes = [
            {"name": "vm", "project": "p", "type": "virtual-machine", "content_type": "block"}
        ]
        instances = [
            {
                "name": "vm",
                "project": "p",
                "type": "virtual-machine",
                "expanded_devices": {"root": {"type": "disk", "path": "/", "size": "8GiB"}},
            },
        ]
        expected = pool.expected_volumes(volumes, instances, 3 * GIB)
        self.assertEqual(expected["virtual-machines_p_vm"][2], 500 * MIB)
        self.assertEqual(expected["virtual-machines_p_vm.block"][2], 8 * GIB)

    def test_custom_block_and_iso_volumes_of_a_limited_project(self) -> None:
        volumes = [
            {
                "name": "b",
                "project": "p",
                "type": "custom",
                "content_type": "block",
                "config": {"size": "6GiB"},
            },
            {
                "name": "os",
                "project": "p",
                "type": "custom",
                "content_type": "iso",
                "config": {"size": "1GiB"},
            },
        ]
        expected = pool.expected_volumes(volumes, [], 3 * GIB)
        self.assertEqual(sorted(expected), ["custom_p_b.block", "custom_p_os.iso"])
        lvs = [self.THIN, self.lv("custom_p_b.block", 6 * GIB), self.lv("custom_p_os.iso", 2 * GIB)]
        found = pool.count(self.THIN, lvs, expected, {"p": 10 * GIB})
        # 6 + 2 (the ISO's real size, above its counted 1) + the 3 its limit still leaves.
        self.assertEqual(found.projects["p"], 11 * GIB)


EXTENT = "vgs --noheadings --nosuffix --units b -o vg_extent_size pinka-targets"


def pool_system(**changes: Any) -> FakeSystem:
    lvs = changes.get("lvs", text("lvs.txt"))
    commands = {
        "incus query /1.0/storage-pools/pinka-targets": (0, changes.get("pool", listing("pool"))),
        LVS: (0, lvs),
        "incus query /1.0/projects?recursion=1": (0, listing("projects")),
        "incus query /1.0/storage-pools/pinka-targets/volumes?recursion=1&all-projects=true": (
            0,
            listing("volumes"),
        ),
        "incus query /1.0/instances?recursion=1&all-projects=true": (0, listing("instances")),
        # hand-written in vgs's format: LVM's default extent of 4 MiB
        EXTENT: (0, f"  {4 * MIB}\n"),
    }
    commands.update(changes.get("extra", {}))
    return FakeSystem(commands)


def outcome(check: Any, system: FakeSystem, preflight: dict[str, Any] | None = None) -> Any:
    context = Context(system, LabConfig(lab_data()))
    if preflight is not None:
        context.memo["preflight"] = preflight
    return check(context)


class ChecksTest(unittest.TestCase):
    def test_overcommitted_and_space_low(self) -> None:
        found = outcome(checks.check_incus_pool_overcommitted, pool_system())
        self.assertEqual(found.result, "fail")
        self.assertIn("overcommitted by 16.0 GiB", found.detail)  # 15.98 GiB
        self.assertIn("project bench 32.5 GiB", found.detail)
        self.assertEqual(outcome(checks.check_incus_pool_space_low, pool_system()).result, "fail")

    def test_the_levels(self) -> None:
        # Grow the data size until the uncommitted space sits around the levels:
        # space_low below 3 x volume.size (9 GiB), overcommitted at or below 0.
        total = pool.count(*recorded()).total
        for spare, low, over in (
            (9 * GIB, "pass", "pass"),
            (9 * GIB - 1, "fail", "pass"),
            (1, "fail", "pass"),
            (0, "fail", "fail"),
        ):
            with self.subTest(spare=spare):
                rows = text("lvs.txt").replace(
                    f"IncusThinPool,{121 * GIB},", f"IncusThinPool,{total + spare},"
                )
                self.assertEqual(
                    outcome(checks.check_incus_pool_space_low, pool_system(lvs=rows)).result, low
                )
                self.assertEqual(
                    outcome(checks.check_incus_pool_overcommitted, pool_system(lvs=rows)).result,
                    over,
                )

    def test_metadata(self) -> None:
        for percent, high, full in (
            ("5.00", "pass", "pass"),
            ("80.00", "fail", "pass"),
            ("90.00", "fail", "fail"),
        ):
            with self.subTest(percent=percent):
                rows = text("lvs.txt").replace(",40.12,5.00,", f",40.12,{percent},")
                self.assertEqual(
                    outcome(checks.check_incus_pool_metadata_high, pool_system(lvs=rows)).result,
                    high,
                )
                self.assertEqual(
                    outcome(checks.check_incus_pool_metadata_full, pool_system(lvs=rows)).result,
                    full,
                )

    def test_an_unreadable_metadata_usage_fails(self) -> None:
        rows = text("lvs.txt").replace(",40.12,5.00,", ",,,")  # an inactive thin pool
        found = outcome(checks.check_incus_pool_metadata_full, pool_system(lvs=rows))
        self.assertEqual(found.result, "fail")
        self.assertIn("cannot be read", found.detail)

    def test_lvs_failing_fails_the_checks(self) -> None:
        system = pool_system(extra={LVS: (5, "", "Volume group not found")})
        with self.assertRaises(RuntimeError):
            outcome(checks.check_incus_pool_overcommitted, system)

    @staticmethod
    def parts(bench: float) -> dict[str, Any]:
        """A preflight report whose pool parts sum to bench + 21 GiB of data, plus 3 outside it."""
        parts = [
            {"id": "bench", "size_gib": bench},
            {"id": "headroom", "size_gib": 21},
            {"id": "metadata", "size_gib": 2},
            {"id": "lvm", "size_gib": 1},
        ]
        return {"requirements": {"pool": {"required_gib": int(bench) + 24, "parts": parts}}}

    def test_undersized_compares_the_thin_pools_data(self) -> None:
        # The fixture's thin pool holds 121 GiB of data: the data part of the sum
        # (all parts but the metadata and LVM's overhead) must fit in it.
        check = checks.check_incus_pool_undersized
        self.assertEqual(outcome(check, pool_system(), self.parts(100)).result, "pass")
        found = outcome(check, pool_system(), self.parts(120))
        self.assertEqual(found.result, "fail")
        self.assertIn("missing 20.0 GiB", found.detail)
        # the loop file (180 GiB) grown by what is missing, never smaller
        self.assertIn("incus storage set pinka-targets size=200GiB", found.detail)
        self.assertIn("Incus refuses a smaller size", found.detail)
        self.assertIn("or lower the projects' limits", found.detail)
        self.assertEqual(outcome(check, pool_system(), {}).result, "fail")

    def test_a_device_pool_is_measured_by_its_thin_pool(self) -> None:
        # Incus writes the volume group's name as a device pool's source, and no size.
        device = listing("pool")
        del device["config"]["size"]
        device["config"]["source"] = "pinka-targets"
        vgs = "vgs --noheadings --units b --nosuffix -o vg_size pinka-targets"
        # A volume group larger than the sum does not help while the thin pool is smaller.
        system = pool_system(pool=device, extra={vgs: (0, f"  {500 * GIB}\n")})
        found = outcome(checks.check_incus_pool_undersized, system, self.parts(120))
        self.assertEqual(found.result, "fail")
        self.assertIn("missing 20.0 GiB", found.detail)
        self.assertIn("vgextend pinka-targets <device>", found.detail)
        self.assertIn("lvextend -l +100%FREE pinka-targets/IncusThinPool", found.detail)
        for check in (checks.check_incus_pool_loop_allocated, checks.check_incus_pool_discards):
            self.assertEqual(outcome(check, system).result, "skip")

    def test_only_on_a_lab_node(self) -> None:
        context = Context(FakeSystem(), LabConfig(lab_data(node_role="inference")))
        self.assertEqual(checks.check_incus_pool_space_low(context).result, "skip")


class LoopAndBucketsTest(unittest.TestCase):
    FILE = "/var/lib/incus/disks/pinka-targets.img"
    STAT = f"stat --format=%s %b %B {FILE}"
    DISCARDS = "lvs --noheadings -o discards pinka-targets/IncusThinPool"

    def test_the_loop_file_must_stay_fully_allocated(self) -> None:
        size = 180 * GIB
        for blocks, result in ((size // 512, "pass"), (size // 512 - 8, "fail")):
            with self.subTest(blocks=blocks):
                system = pool_system(extra={self.STAT: (0, f"{size} {blocks} 512\n")})
                found = outcome(checks.check_incus_pool_loop_allocated, system)
                self.assertEqual(found.result, result)
        unreadable = outcome(checks.check_incus_pool_loop_allocated, pool_system())
        self.assertEqual(unreadable.result, "fail")
        device = listing("pool")
        del device["config"]["size"]
        skipped = outcome(checks.check_incus_pool_loop_allocated, pool_system(pool=device))
        self.assertEqual(skipped.result, "skip")

    def test_a_loop_pool_passes_no_discards_down(self) -> None:
        for mode, result in (("nopassdown", "pass"), ("passdown", "fail"), ("ignore", "fail")):
            with self.subTest(mode=mode):
                system = pool_system(extra={self.DISCARDS: (0, f"  {mode}\n")})
                self.assertEqual(outcome(checks.check_incus_pool_discards, system).result, result)
        device = listing("pool")
        del device["config"]["size"]
        self.assertEqual(
            outcome(checks.check_incus_pool_discards, pool_system(pool=device)).result, "skip"
        )

    def test_any_bucket_fails(self) -> None:
        found = outcome(checks.check_incus_pool_buckets, pool_system())
        self.assertEqual(found.result, "fail")
        self.assertIn("buckets_default_b", found.detail)
        rows = "".join(r for r in text("lvs.txt").splitlines(keepends=True) if "buckets_" not in r)
        self.assertEqual(
            outcome(checks.check_incus_pool_buckets, pool_system(lvs=rows)).result, "pass"
        )

    def test_a_failing_listing_fails_the_checks(self) -> None:
        system = pool_system(extra={"incus query /1.0/projects?recursion=1": (1, "", "boom")})
        with self.assertRaises(RuntimeError):
            outcome(checks.check_incus_pool_overcommitted, system)


class LoopSpaceTest(unittest.TestCase):
    """The file system under a loop pool: its free space and No_COW on btrfs."""

    FILE = "/var/lib/incus/disks/pinka-targets.img"
    DF = f"df --output=target,fstype,avail -B1 {FILE}"
    LSATTR = f"lsattr {FILE}"
    # preflight's report: the reserve kept beside the loop file (50 GiB)
    REPORT = {
        "requirements": {
            "disk": [{"mount": "/", "items": [{"id": "pool-reserve", "size_gib": 50.0}]}]
        }
    }

    def system(self, free: int, fstype: str = "ext4", attrs: str | None = None) -> FakeSystem:
        """df's answer is hand-written in its format (a header, then one line)."""
        extra: dict[str, Any] = {
            self.DF: (0, f"Mounted on Type Avail\n/ {fstype} {free}\n"),
        }
        if attrs is not None:
            extra[self.LSATTR] = (0, f"{attrs} {self.FILE}\n")
        return pool_system(extra=extra)

    def test_the_levels(self) -> None:
        # warning below the reserve (50 GiB), error below three image volumes (9 GiB)
        low, full = checks.check_incus_pool_loop_space_low, checks.check_incus_pool_loop_space_full
        for free, warn, error in (
            (50 * GIB, "pass", "pass"),
            (50 * GIB - 1, "fail", "pass"),
            (9 * GIB, "fail", "pass"),
            (9 * GIB - 1, "fail", "fail"),
        ):
            with self.subTest(free=free):
                self.assertEqual(outcome(low, self.system(free), self.REPORT).result, warn)
                self.assertEqual(outcome(full, self.system(free), self.REPORT).result, error)
        self.assertIn("kept beside", outcome(low, self.system(GIB), self.REPORT).detail)
        # unreadable: a failure, never a pass
        self.assertEqual(outcome(low, pool_system(), self.REPORT).result, "fail")
        self.assertEqual(outcome(full, pool_system(), self.REPORT).result, "fail")
        self.assertEqual(outcome(low, self.system(100 * GIB), {}).result, "fail")
        device = listing("pool")
        del device["config"]["size"]
        device["config"]["source"] = "pinka-targets"
        for check in (low, full, checks.check_incus_pool_loop_nocow):
            self.assertEqual(outcome(check, pool_system(pool=device), self.REPORT).result, "skip")

    def test_no_cow_on_btrfs(self) -> None:
        check = checks.check_incus_pool_loop_nocow
        found = outcome(check, self.system(100 * GIB, "btrfs", "----------------------"))
        self.assertEqual(found.result, "fail")
        self.assertIn("no No_COW attribute", found.detail)
        self.assertEqual(
            outcome(check, self.system(100 * GIB, "btrfs", "---------------C------")).result,
            "pass",
        )
        self.assertEqual(outcome(check, self.system(100 * GIB, "btrfs")).result, "fail")
        self.assertEqual(outcome(check, self.system(100 * GIB, "ext4")).result, "skip")
        self.assertEqual(outcome(check, pool_system()).result, "fail")  # df unreadable


class PeriodicTest(unittest.TestCase):
    REPORT = "/var/lib/pinka/doctor/last.json"

    def check(self, report: str | None, minutes: int = 60) -> Any:
        files = {} if report is None else {self.REPORT: report}
        data = lab_data()
        data["doctor"] = {"report": self.REPORT, "periodic_interval_minutes": minutes}
        return checks.check_doctor_periodic(Context(FakeSystem(files=files), LabConfig(data)))

    def test_fresh_old_missing(self) -> None:
        # FakeSystem's now is 2026-01-01T12:00:00Z.
        fresh = json.dumps({"checked_at": "2026-01-01T11:00:00Z"})
        old = json.dumps({"checked_at": "2026-01-01T09:59:00Z"})
        self.assertEqual(self.check(fresh).result, "pass")
        self.assertEqual(self.check(old).result, "fail")
        self.assertEqual(self.check(old, minutes=120).result, "pass")
        self.assertIn("missing or unreadable", self.check(None).detail)
        self.assertIn("missing or unreadable", self.check("{").detail)


if __name__ == "__main__":
    unittest.main()
