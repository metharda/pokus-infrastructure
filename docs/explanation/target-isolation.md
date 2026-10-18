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
| `build` | golden image builds; only the orchestrator's certificate | restricted to the NAT bridge `pinka-build0` |
| `user-<uid>` | one per researcher, through `incus-user` | the restrictions `incus-user` sets, resource limits, no GPU |

`<uid>` is the researcher's numeric user ID. The platform's certificates are
*restricted* certificates: they can act only inside their projects and can
neither change projects nor reach the server configuration. The agent itself
has no Incus access at all; everything it does to a target goes through the
executor.

## Run networks

Each run gets its own bridge, `r-<short>`, created by the platform with no
NAT and no uplink (`<short>` is derived from the run's ID). Every run network
carries the network ACL `pinka-run-default`, which has no rules: Incus then
rejects everything that leaves the bridge, whether to the host, to another
run's network or to the outside, while instances on the same bridge talk to
each other freely. Incus's own DHCP and DNS on the bridge keep working because
Incus adds them as baseline rules ahead of the ACL.

The host firewall adds a second layer that does not depend on Incus:

- `nftables` backend: nothing is forwarded from an `r-*` interface to a
  non-run interface, and nothing starts a connection into one;
- `firewalld` backend: run bridges (`r-+`) are in the zone `pinka-run`, which
  accepts only DHCP, DHCPv6 and DNS from them;
- `ufw` backend: from `r-+` only DHCP and DNS are allowed in.

The build bridge and the researchers' bridges (`incusbr-<uid>`) have NAT and
an uplink and are trusted as Incus documents for firewalld and ufw hosts.
Docker's `FORWARD` drop policy is neutralized for all Incus bridges by
`pinka-docker-incus-compat.service`, exactly as Incus's documentation
describes.

## Resource limits

Project limits cap the sum over all instances in `bench` and in every
personal project; profiles give each instance default CPU, memory and root
disk limits that blueprints may raise within the project's limits. All
instances live in one storage pool, `pinka-targets`, which must be btrfs or
LVM thin so that disk quotas work; Pokus Infrastructure (pinka) never uses the `dir` backend
([ADR-0009](../adr/0009-btrfs-or-lvm-thin-pool.md)).

## CPU sets

A measurement on a target must not be disturbed by the lab itself. Two
host_vars split the CPUs: `cpuset_services` for the lab and `cpuset_targets`
for the benchmark targets; they must not overlap, and the installer suggests
a split (about 30 % of the CPUs for the services).

With simultaneous multithreading (SMT, Hyper-Threading) two CPU numbers can
be the two threads of one physical core; they share its execution units, so
a target on one thread is measured together with whatever runs on the
other. The installer's suggestion therefore gives whole cores to one set
(the host's `thread_siblings_list`), and both the installer and doctor warn
when a core's threads are split between `cpuset_services` and
`cpuset_targets`. On many hosts the siblings are not neighbors: `cpu0` and
`cpu8` can be one core of a 16-thread CPU.

- Every lab service runs in the systemd slice `pinka-services.slice`, whose
  `AllowedCPUs=` is `cpuset_services`: the lab's own units (`Slice=`) and
  every lab container, which pinka's Compose projects and the GPU arbiter
  place in that slice (`cgroup_parent`, `--cgroup-parent`). Docker's other
  containers on the machine keep their own settings.
- The platform pins targets: it sets each instance's `limits.cpu` to CPUs
  inside `cpuset_targets`, which `endpoints.toml` passes to it. A plain CPU
  count would let Incus place the instance on any CPU. The `bench` project's
  `limits.cpu` is the number of CPUs in `cpuset_targets`.
- Instances of the build project and of the researchers' personal projects
  are not pinned; they do not run during measurements.

With both sets empty nothing is pinned. Doctor's check `resources.cpusets`
warns when a set names CPUs the host does not have (on WSL, `.wslconfig`
decides how many there are) or when the services run elsewhere; a changed
set takes effect at the next start of the lab.

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
in NAT mode, which listens on the host side. With the `vm_targets` feature,
VMs therefore reach a dedicated collector listener on `198.18.0.1` (an
address on the dummy interface `pinka-otlp0`): the run ACL `pinka-run-default`
gets one egress rule for that address and TCP `4317`/`4318`, and the firewall
accepts nothing else from run networks
([integration contract](../contracts.md#telemetry),
[How telemetry flows](telemetry.md)).

## Watching what happens

`pinka-incus-events.service` writes every Incus lifecycle and logging event of
all projects into the journal (identifier `pinka-incus-events`). The
collector forwards them, and the platform uses them to check that only the
executor touched a target while the agent was working.

Related: [Lab host architecture](../architecture.md),
[How to configure the firewall backend and SELinux](../how-to/firewall-and-selinux.md).
