---
title: "ADR-0009: btrfs or LVM thin for the Incus pool, never dir"
type: explanation
audience: [developer, reader]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
---

# ADR-0009: btrfs or LVM thin for the Incus pool, never dir

- **Status:** accepted
- **Date:** 2026-10-05
- **Deciders:** pokus-infrastructure maintainers (BUILD_PROMPT §2, §7.7)

## Context

Runs create and destroy many short-lived instances from golden images. Disk
use must be bounded per instance and per project, and creating an instance
from an image must be fast. Incus's `dir` backend copies whole images and
cannot enforce disk quotas for containers.

## Decision

1. The pool `pinka-targets` uses btrfs, or LVM thin where btrfs is not
   available; preflight decides (`storage_backend`) and refuses hosts that
   have neither.
2. The pool lives on a dedicated device when one is configured, otherwise on a
   loop file of a fixed size inside the root filesystem.
3. A device is formatted only under safety invariant 1 (exact confirmation
   variable, not mounted, no signature unless forced, interactive
   confirmation after showing model, size and serial).

## Consequences

- Instances are copy-on-write clones of images and disk limits are enforced.
- Loop pools are slower than a dedicated device and take space from the root
  filesystem; the docs recommend a dedicated disk on bare metal.
- ZFS is not used: it is not packaged the same way on every family and its
  kernel module would add another moving part to the fingerprint.

## Alternatives considered

- **dir backend**: simplest, but no quotas and full copies of every image.
- **ZFS**: excellent, but kernel module availability differs per
  distribution and Secure Boot setup.
