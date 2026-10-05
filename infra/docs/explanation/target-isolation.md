---
title: How benchmark targets are isolated
type: explanation
audience: [operator, developer, researcher]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: Incus projects, run networks, the run ACL, resource limits and telemetry paths that keep targets apart from the host and each other.
---

# How benchmark targets are isolated

A benchmark target is a machine the agent under test may break on purpose.
It must not be able to reach the lab host, other runs, the internet, or the
researchers' own sandboxes. This page explains which mechanisms the `incus`,
`firewall` and `docker` roles set up for that, and why.

## Incus, not Docker, runs targets

Targets are Incus system containers (or VMs for kernel-level scenarios),
never containers of the lab's own Docker daemon: a target needs a full init
system and its own services, and an agent that controls a Docker container on
the host's daemon is one step from controlling the host. Docker only runs the
lab's own services. [ADR-0007](../adr/0007-incus-for-targets.md) records the
decision.

## Projects

Incus projects separate who may do what:

| Project | Used by | Restrictions |
| --- | --- | --- |
| `bench` | official runs; the platform's service certificates | restricted; nesting, proxy devices and snapshots allowed; VMs only with the `vm_targets` feature; memory and CPU limits from the host's size |
| `build` | golden image builds; only the orchestrator's certificate | restricted to the NAT bridge `pokus-build0` |
| `user-<uid>` | one per researcher, through `incus-user` | the restrictions `incus-user` sets, resource limits, no GPU |

`<uid>` is the researcher's numeric user ID. The platform's certificates are
*restricted* certificates: they can act only inside their projects and can
neither change projects nor reach the server configuration. The agent itself
has no Incus access at all; everything it does to a target goes through the
executor.

## Run networks

Each run gets its own bridge, `r-<short>`, created by the platform with no
NAT and no uplink (`<short>` is derived from the run's ID). Every run network
carries the network ACL `pokus-run-default`, which has no rules: Incus then
rejects everything that leaves the bridge, whether to the host, to another
run's network or to the outside, while instances on the same bridge talk to
each other freely. Incus's own DHCP and DNS on the bridge keep working because
Incus adds them as baseline rules ahead of the ACL.

The host firewall adds a second layer that does not depend on Incus:

- `nftables` backend: nothing is forwarded from an `r-*` interface to a
  non-run interface, and nothing starts a connection into one;
- `firewalld` backend: run bridges (`r-+`) are in the zone `pokus-run`, which
  accepts only DHCP, DHCPv6 and DNS from them;
- `ufw` backend: from `r-+` only DHCP and DNS are allowed in.

The build bridge and the researchers' bridges (`incusbr-<uid>`) have NAT and
an uplink and are trusted as Incus documents for firewalld and ufw hosts.
Docker's `FORWARD` drop policy is neutralized for all Incus bridges by
`pokus-docker-incus-compat.service`, exactly as Incus's documentation
describes.

## Resource limits

Project limits cap the sum over all instances in `bench` and in every
personal project; profiles give each instance default CPU, memory and root
disk limits that blueprints may raise within the project's limits. All
instances live in one storage pool, `pokus-targets`, which must be btrfs or
LVM thin so that disk quotas work; Pokus never uses the `dir` backend
([ADR-0009](../adr/0009-btrfs-or-lvm-thin-pool.md)).

## What targets cannot see

Target profiles set `security.guestapi=false`, so `/dev/incus` is absent
inside targets and an instance cannot query Incus about itself. Nothing that
identifies a scenario ever enters an instance: the platform passes only an
opaque run ID and node alias for telemetry.

## Telemetry from inside a target

Target containers send OpenTelemetry data to `127.0.0.1:4317` inside the
container. An Incus proxy device listens there and connects to the
collector on the host's loopback address, so the container needs no route to
the host. VMs cannot use this path: Incus supports proxy devices on VMs only
in NAT mode, which listens on the host side. VMs therefore reach a dedicated
collector listener through a narrow ACL rule instead
([integration contract](../contracts.md#telemetry)).

## Watching what happens

`pokus-incus-events.service` writes every Incus lifecycle and logging event of
all projects into the journal (identifier `pokus-incus-events`). The
collector forwards them, and the platform uses them to check that only the
executor touched a target while the agent was working.

Related: [Lab host architecture](../architecture.md),
[How to configure the firewall backend and SELinux](../how-to/firewall-and-selinux.md).
