---
title: How doctor counts the Incus pool
type: explanation
audience: [operator, developer]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: What the Incus pool guarantees inside and under the pool, why its uncommitted space never reads higher than it is, how doctor counts each volume at its worst case, and how it watches a loop pool's file system.
---

# How doctor counts the Incus pool

Every target, build instance and personal instance lives in one LVM thin
pool, `pinka-targets`
([ADR-0017](../adr/0017-lvm-thin-only-for-the-incus-pool.md)). A thin pool
promises more than it holds: each volume has a size, but takes space only as
it is written. If it fills, every volume stops at once. Doctor's pool checks
watch the space that is not promised to anyone yet, the **uncommitted
space**.

## What is guaranteed

The guarantee has two layers.

- **Inside the pool, on every host:** the sum of the limits Incus enforces
  on what it counts never exceeds the thin pool. Each project's disk limit
  (`limits.disk`) caps its instances' root disks, custom volumes and VM
  state volumes, snapshots are blocked in `bench` and `build`, and the pool
  is sized to hold every limit in full
  ([Capacity planning](capacity-planning.md)). Storage Incus does not count,
  by any path, doctor counts at its full size, and it fails
  `incus.pool.overcommitted` once it exists.
- **Under the pool:** a dedicated device's space is guaranteed. A loop
  pool's file is preallocated and watched, not guaranteed: the host's file
  system can take more space under it later (see
  [Under a loop pool](#under-a-loop-pool)), and doctor watches that file
  system's free space.
- **The uncommitted space never reads higher than it is.** It is the thin
  pool's data size minus the count below, and every part of the count is at
  least what that part can ever take.

## What Incus does not count

The paths found so far that add storage no limit counts. Each counts at its
full size once it exists; a new one found later is added here, with a doctor
test where the count needs one.

- **Images.** Incus counts no stored image against a project's limit. The
  pool counts the golden images and a headroom for the rest; doctor watches
  what images take beyond it.
- **Backup imports.** Importing an instance backup (`incus import`) writes
  the instance's storage and restores its snapshots before Incus checks the
  instance against the project's limits, and that check counts root disks,
  never snapshots (`cmd/incusd/instances_post.go:660-986`, the storage at
  891, the check at 931, the snapshots at 951-982;
  `internal/server/project/permissions.go:1484-1532`). A VM's disk is
  restored at the backup's own size (`internal/server/storage/drivers/generic_vfs.go:851-857`).
  Importing a custom volume's backup checks only the volume's own
  configuration (`internal/server/storage/backend.go:7726-7739`).
  `restricted.snapshots` does not stop an import: it is read only where
  snapshots are created (`cmd/incusd/instance_snapshot.go:260`,
  `cmd/incusd/instance.go:624`, `cmd/incusd/storage_volumes_snapshot.go:143`).
  Neither does `restricted.backups`: Incus reads it only in
  `AllowBackupCreation` (`permissions.go:1760-1777`), which only the three
  backup *export* endpoints call (`instance_backup.go:232`,
  `storage_volumes_backup.go:332`, `storage_buckets_backup.go:320`); the
  import paths (`createFromBackup`, `instances_post.go:660`;
  `createStoragePoolVolumeFromBackup`, `storage_volumes.go:2619`) never do.
  Incus 6.0.7 has no project setting that blocks an import. A platform does
  not import backups into `bench` or `build` (the contract); a researcher's
  import into a personal project is outside the guarantee.
- **Storage buckets.** Incus 6.0.7 has no project setting that forbids
  storage buckets (`features.storage.buckets` only gives a project its own
  set, `cmd/incusd/api_project.go:1394-1401`), and no limit counts them.
  Incus serves a bucket on LVM by running a `minio` binary from its `PATH`
  (`internal/server/storage/s3/miniod/miniod.go:332`); pinka never installs
  MinIO, and doctor's `incus.pool.buckets` fails on any bucket volume
  (`buckets_...`) in the thin pool.

## Under a loop pool

A loop pool's file is preallocated, and its thin pool passes no discards
down to it (`--discards nopassdown`), so no discard punches a hole in the
file; the discarded blocks are still freed for reuse inside the pool
(lvmthin(7), "Discard"). LVM changes passdown to nopassdown on an active
pool: only a change to or from `ignore` needs the pool inactive
(`tools/lvchange.c:104-108` of lvm2 2.03.16). A pool on a dedicated device
keeps LVM's default.

The host's file system can still take space under the file later:

- **btrfs.** A write into a file is done in place only when btrfs allows
  it; otherwise it is copied and takes new space. A file without No_COW is
  copied on every overwrite once its preallocated extents are written. With
  No_COW it is still copied (Linux 6.8, `fs/btrfs/`): while a scrub or a
  balance holds its block group read-only (`block-group.c:327-328`,
  `inode.c:7044-7055` and 7148; scrub `scrub.c:2624`, balance and device
  removal `relocation.c:4124`), and for an extent older than a snapshot of
  the subvolume taken since, even one deleted again (`inode.c:1895-1902`).
  Incus creates the loop file itself and refuses one that exists
  (`internal/server/storage/drivers/driver_lvm.go:253-255`), so the role
  sets No_COW on its directory before Incus creates the pool: btrfs gives a
  new file its directory's No_COW (`fs/btrfs/inode.c:6173-6194`, called when
  the file is created, 6260), so the file is No_COW while still empty. A
  snapshot of the subvolume holding the file (snapper, timeshift), a scrub
  or a balance makes later writes take new space; a lab that must not
  depend on the host's free space uses a dedicated device.
- **Shared extents and other copy-on-write file systems.** A reflinked copy
  of the loop file (GNU `cp` reflinks where it can) or a deduplication tool
  makes btrfs share the file's extents, and a write into a shared extent is
  copied even with No_COW (`fs/btrfs/inode.c:1923-1928`,
  `btrfs_cross_ref_exist`). bcachefs is copy-on-write too, and preflight
  neither refuses nor checks it. Doctor's free-space watch catches what each
  takes.
- **WSL.** The distribution's ext4 lives in a virtual disk on the Windows
  drive (`ext4.vhdx`), which may not grow when the loop file is preallocated
  (ext4's `fallocate` writes no data). Doctor's watch measures the
  distribution's ext4, not the Windows drive, so it does not catch this:
  keep at least the pool's size free on that Windows drive
  ([WSL2 hosts](../providers/wsl2.md)).

Doctor watches the free space of the file system holding the loop file:
`incus.pool.loop_space_low` warns below the reserve preflight leaves beside
the file (50 GiB by default), and `incus.pool.loop_space_full` fails below
three image volumes, the same margin as `incus.pool.space_low` inside the
pool. Preflight gives the same answer for an existing pool, on what the file
system keeps free after the install's other items: a warning below the
reserve, an operator action below three image volumes. A new pool needs the
reserve free like any other item. The reserve is never room the lab's stores
can grow into.

## How doctor counts

Doctor reads the volume group as root with `lvs`, and Incus's listings of
projects, instances and the pool's volumes. It never reads what a volume
has written: a volume may be inactive (LVM reports no usage then), and an
instance can rewrite blocks it shares with its image, which takes new space
without its usage showing it.

| What | Counted at |
| --- | --- |
| An image volume (a VM image: both its volumes) | its full size |
| A project with a disk limit | each of its volumes at the larger of its real size and the size Incus counts for it, plus what the limit still leaves beyond the sizes Incus counts |
| The limit of a project | `limits.disk`; `limits.disk.pool.pinka-targets` only lowers it, and counts nothing without it |
| An instance's volume Incus lists while the instance listing lacks the instance (deleted, renamed or moved between doctor's two listings, or a record an interrupted delete left) | outside every project's limit, at the larger of its real and counted size: Incus counts only the instances it holds against a limit (`internal/server/project/permissions.go:1438-1441`, 1520-1529) |
| A volume Incus lists but LVM does not show yet | its size rounded up to whole extents of the volume group (LVM rounds every volume so; `vgs -o vg_extent_size`) |
| A volume of a project without a disk limit | the larger of its real size and its configured size |
| An image or volume Incus lists but has not created yet | an image at the pool's `volume.size` (a VM image with its state volume too); a volume at its configured size, in its project's limit when the project has one |
| A snapshot, a temporary volume (`.incustmp`, during a restore or while a snapshot is mounted), a bucket, any volume doctor does not recognize | its full size |

A VM counts with both its volumes, the root disk and the state volume
(`size.state`), as Incus counts both against the limit.

A project counts as disk-limited only by `limits.disk`: without it, Incus
checks neither a new volume nor a grown one against any limit
(`internal/server/project/permissions.go:345-348` and `1089-1092`, which
return before a per-pool limit is read). So `limits.disk.pool.<pool>` can
only lower a `limits.disk` that is set; in a project without `limits.disk`,
every volume counts at its full size. What a limited project's volumes take
beyond what Incus counts (an imported snapshot, a disk restored larger than
its `size`) counts too: every volume at the larger of its real size and its
counted size, snapshots in full.

Incus writes an image's database record before it creates the image's
volume (`internal/server/storage/backend.go:3929`, `3952`), so a listing
can name a volume that `lvs` does not show yet. Doctor counts every listed
volume without a logical volume at the size it will have:
the pool's `volume.size` for an image, which Incus refuses to exceed
(`internal/server/storage/drivers/volume.go:695-698`), and the configured
size for a volume, each rounded up to whole extents. `incus.pool` fails
when the pool has no `volume.size` (pinka sets it): without it Incus grows
an image volume to the image's size.

A new volume in a limited project's room takes its size in whole extents of
the volume group, while Incus counts the size itself: LVM rounds every
logical volume up to whole extents (`internal/server/storage/drivers/driver_lvm_utils.go:388-408`
rounds to 512 bytes only, and `lvcreate` gets the virtual size, 432). The
count takes the larger of the real and the counted size once the volume
exists, and the extent-rounded size before.

Doctor recognizes a volume by generating the name Incus's LVM driver gives
what Incus lists (the volume type, the project prefix, `-` doubled, a
snapshot's `/` as `-`, `.block` and `.iso` suffixes) and matching it
exactly. It parses no names: a name that matches nothing counts in full.

## The levels

| Check | Fails when |
| --- | --- |
| `incus.pool.space_low` (warning) | the uncommitted space is below three image volumes: room for two more images and a build's temporary one |
| `incus.pool.overcommitted` (error) | the uncommitted space is at or below zero |
| `incus.pool.metadata_high` (warning) | the thin pool's metadata is at or above 80 % used (lvmthin(7): LVM's own first warning) |
| `incus.pool.metadata_full` (error) | at or above 90 %, or its usage cannot be read |
| `incus.pool` (error) | the pool has another driver, or no `volume.size` |
| `incus.pool.undersized` (error) | the thin pool's data is smaller than the sum preflight computes for the stored plan, less the metadata and LVM's overhead |
| `incus.pool.loop_allocated` (error) | a loop pool's file has fewer blocks allocated than its size |
| `incus.pool.discards` (error) | a loop pool's thin pool does not have `--discards nopassdown` |
| `incus.pool.buckets` (error) | a storage bucket's volume is in the thin pool |
| `incus.pool.loop_space_low` (warning) | a loop pool's file system has less free than the reserve beside the file |
| `incus.pool.loop_space_full` (error) | a loop pool's file system has less free than three image volumes |
| `incus.pool.loop_nocow` (error) | a loop pool's file on btrfs has no No_COW attribute |

A default lab starts clearly above the warning: about 20 GiB uncommitted
(the headroom) against 9 GiB, with the default blueprints. If real-machine
measurements find the estimates too low, the estimates go up; the levels
never go down.

The checks run at the end of every bootstrap and in doctor's periodic run
(`pinka-doctor.timer`, hourly by default), so images that pile up are
reported without anyone starting doctor. See
[How to fix a failed doctor check](../how-to/fix-doctor-checks.md#check-incus-pool-space_low).

## Where it is checked

The count's code is `tools/pinka-lab/pinka_lab/pool.py`; its tests
(`tools/pinka-lab/tests/test_lab_pool.py`) use `lvs` and Incus listings in
their exact formats, with every case above.
