---
title: "ADR-0017: LVM thin only for the Incus pool, sized to hold every limit"
type: explanation
audience: [developer, reader]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
---

# ADR-0017: LVM thin only for the Incus pool, sized to hold every limit

- **Status:** accepted; supersedes
  [ADR-0009](0009-btrfs-or-lvm-thin-pool.md)
- **Date:** 2026-11-19
- **Deciders:** pokus-infrastructure maintainers

## Context

A target is a machine the agent under test may break on purpose, so its disk
limit must hold against root inside it. On btrfs, a subvolume created inside
an instance escapes the instance's quota (Incus's btrfs documentation,
"Quotas"). A thin pool promises more than it holds, and a full thin pool
stops every volume at once.

## Decision

1. The Incus pool is LVM thin only. Without `dm_thin_pool` preflight reports
   `needs_action` with the change that provides it; never `dir` (no
   quotas), never btrfs.
2. `bench` and `build` have `limits.disk`, and block snapshots, which no
   limit counts. Personal projects have a small `limits.disk`.
3. The pool's size is the sum of everything that may be written to it: every
   project's limit in full, the image volumes at the pool's `volume.size`,
   the thin pool's metadata, LVM's overhead and a headroom. The same sum is
   `incus_pool_size: auto` and the check of an explicit size or an existing
   pool; a loop pool's file is preallocated.
4. The guarantee, inside the pool: the sum of the limits Incus enforces on
   what it counts never exceeds the thin pool. Storage Incus does not count,
   by any path (images, backup imports, and any path found later), doctor
   counts at its full size, and it fails `incus.pool.overcommitted` once it
   exists. Doctor also runs periodically.
5. Under the pool: a dedicated device's space is guaranteed. A loop pool's
   file is preallocated and watched, not guaranteed: the host's file system
   can take more space under it later, so doctor watches its free space
   against the reserve preflight leaves beside the file.

## Consequences

- An instance cannot write past its root disk, and what the projects'
  limits allow fits in the pool. A backup import, which Incus restores with
  snapshots no limit counts, can take more; doctor reports it once it exists.
- A loop pool depends on the host's free space; a lab that must not uses a
  dedicated device.
- The pool is larger than before, chiefly by the personal projects' limits;
  a loop pool takes its full size on its file system at install.
- An image larger than `volume.size` cannot be used until the operator
  raises it, which grows the pool.
- One driver everywhere makes disk faults behave the same on every lab; the
  fingerprint records it.

## Alternatives considered

- **btrfs first, LVM thin as a fallback** (ADR-0009): quotas a subvolume
  escapes.
- **A percentage margin on the limits**: limits are caps and snapshots are
  blocked, so a margin covers nothing they do not.
- **Reading the volumes' usage** (`thin_ls`): needs a metadata snapshot of a
  live pool and reads what can change at once; counting each volume at its
  worst case needs neither.
