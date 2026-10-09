---
title: "ADR-0007: Incus for benchmark targets"
type: explanation
audience: [developer, reader]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
---

# ADR-0007: Incus for benchmark targets

- **Status:** accepted
- **Date:** 2026-10-05
- **Deciders:** pokus-infrastructure maintainers (BUILD_PROMPT §1, §7.7)

## Context

Targets are machines an agent diagnoses and repairs after faults were
injected into them. They need a full init system, several services, realistic
logs and package managers, and sometimes their own kernel. The agent must not
be able to escape a target or reach the host and the lab services.

## Decision

1. Targets are Incus system containers by default and Incus VMs for
   kernel-level scenarios, built from pinka's own golden images.
2. Incus runs the LTS line (6.0), pinned and held.
3. Official runs live in the restricted project `bench`, image builds in
   `build`, researchers' sandboxes in one restricted project each.
4. Platform services talk to Incus over its HTTPS API on `127.0.0.1` with
   restricted client certificates; the agent has no Incus access.
5. Each run gets its own bridge with no uplink and the ACL
   `pinka-run-default`.

## Consequences

- One tool covers containers and VMs with the same API, images, profiles,
  projects and network ACLs.
- The lab's Docker daemon never runs targets, so a compromised target cannot
  reach the daemon that runs the lab services.
- Incus must be installed from a source that ships the LTS line, which on
  Debian and Ubuntu is an extra repository (Zabbly).

## Alternatives considered

- **Docker containers as targets**: no real init system by default, shares
  the daemon with the lab services, no VM option.
- **VMs only (libvirt)**: heavy for the common case, slow to reset, and a
  second API for networks and images.
- **LXD**: the same model, but officially distributed only as a snap, which
  Pokus Infrastructure (pinka) cannot pin and hold the same way as packages.
