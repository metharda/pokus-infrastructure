---
title: Capacity planning
type: explanation
audience: [operator, developer, reader]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: How preflight computes the memory and disk a lab needs from the operator's choices, what is estimated and what is measured, what grows with use, and where each choice is set.
---

# Capacity planning

The lab does not decide its own scale. A machine with 8 GiB of memory can
run a small lab with one target at a time; a workstation with a GPU can serve
a model and run training jobs. What decides is what you choose: the node role,
whether the lab serves inference and runs training jobs, the number of
targets that run at once, the model vLLM serves, the golden images, the Incus
pool, the retention of the telemetry stores and the caps on training jobs. Preflight adds up what these choices need and checks that this
machine has it, before anything is installed.

## Where the numbers come from

`preflight/requirements.yml` lists every part of the lab with the memory and
CPU it needs and the disk it takes, each with its source and whether it is
`estimated` or `measured`. `make generate` compiles it, with the container
images' sizes of `versions.lock.yml`, the golden images' estimates in their
`image.yaml`, the target profiles of the incus role and the configured
defaults, into `preflight/requirements.json`, which preflight reads.

Every number is an estimate until a lab on a real machine is measured at
idle and during a run; the measured numbers then replace them, marked
`measured`. The breakdown always shows which kind a number is. Sizes you set
yourself (the pool) are marked `configured`.

## The choices: the plan

Preflight reads the choices from a plan (`pinka-preflight --plan FILE`,
format in `preflight/schema/plan.schema.json`): the node role, the inference
and training switches, the research space, Docker's data root, the pool's
source and size, the concurrent targets, the model to serve with its files'
sizes, the golden images to build, the GPU exporter, the training caps and
the retention settings.

- Bootstrap writes the plan of the machine on every run, from its
  `host_vars`, `group_vars` and `models.lock.yml` (`pinka-installer plan`),
  passes it to preflight and to the play's preflight gate, and stores it as
  `/etc/pinka/plan.json` next to the host profile.
- `sudo pinka preflight` and doctor use the stored plan, so they compute the
  same numbers; `pinka preflight --plan FILE` computes another one.
- Without a plan, preflight takes the configured defaults (what the Ansible
  defaults set).

The features (`gpu_inference`, `gpu_training`, `vm_targets` and the others)
are not part of the plan: preflight detects them on the machine. The plan's
switches decide which of the GPU features the lab uses.

## Switching inference and training

Inference and training are switches, so a small lab does not pay for what it
does not run:

| Setting (host_vars) | Installer answers | Values |
| --- | --- | --- |
| `inference` | `features.inference` | `auto` (the default): vLLM, its images and the selected model where preflight detects `gpu_inference` on a `full` or `inference` node; `off`: none of them |
| `training` | `features.training` | `auto` (the default): training jobs where preflight detects `gpu_training` (a usable GPU) on a `full` node; `cpu`: also the jobs' CPU-only variant on a host without one; `off`: none |

A switched-off feature counts nothing in memory or disk, and its services,
images and models are not installed. With `inference: off` the GPU arbiter
serves nothing (a deployment served before stops at the next bootstrap run);
with training off it refuses every run and every dataset publication, and
the platform's job images are not pulled. A host that lacks a feature its
switch turns off is not `degraded` for it: preflight notes it instead. An
`inference` node needs `inference: auto`.

The summary and the host profile (`requirements.switches`) say what each
switch leaves on and why, for example `training off (no usable GPU for
training; training: cpu would run the CPU-only variant)`. The installer
writes `off` quoted (`"off"`): YAML 1.1, which Ansible reads, takes a plain
`off` for `false`.

## Memory

Each part has a required and a recommended value. Required is what it needs
to run; recommended adds what it uses at its upper end. Preflight sums them
for the parts the node role runs:

| Part | Required / recommended (GiB) | On |
| --- | --- | --- |
| host services (sshd, tailscaled, Docker, pinka's units) | 1.0 / 1.5 | every node |
| Incus daemon | 0.2 / 0.4 | full, lab |
| PostgreSQL | 0.5 / 1.5 | full, lab |
| telemetry stores (Prometheus, Loki, Tempo, Grafana, query proxy) | 1.5 / 3.0 | full, lab |
| collectors and exporters | 0.3 / 0.8 | every node |
| OPA | 0.1 / 0.2 | full, lab |
| bootstrap and the installer | 0.5 / 0.8 | every node |
| concurrent targets | each a container (1 GiB) / a VM (2 GiB) with `vm_targets`, plus a 0.5 GiB probe client; at least the 2 GiB build instance when golden images are built | full, lab |
| vLLM serving the model | the model's files + 3 / + 5 | full, inference, with `gpu_inference`, `inference: auto` and a model |
| a training job | 0 / the training cap | full, where training runs: a usable GPU (`gpu_training`), or `training: cpu` |

The GPU arbiter runs vLLM or a training job, never both, so on a node that
serves a model the larger of the two counts. A training job counts only in
recommended: the lab runs without one, and the arbiter refuses a job over the
cap.

Three thresholds follow, each compared with `MemTotal` at 0.94 times its
nominal value, because the firmware and the kernel keep part of the installed
memory (a nominal 16 GiB machine reports about 15.1 to 15.6 GiB and passes
16 GiB):

- below the floor of 8 GiB, the core stack with no target running, the host
  is `unsupported`;
- below the required sum (never less than the floor) it is `unsupported`,
  with the breakdown and the choices that would fit, such as fewer concurrent
  targets or a smaller model;
- below the recommended sum it is `degraded`: the install continues only with
  your consent (`--accept-degraded`, or the installer's checkbox), and the
  message names the largest parts and what would fit, such as a lower
  `training_caps.memory_max` or `training: off`.

With the defaults (a full node, two targets, no model), the lab requires
8 GiB. On a host without a usable GPU training is off and it recommends
13.2 GiB with VM targets (11.2 GiB with containers only); with one, a
training job at the computed cap comes on top.

## The training cap

A training job counts at `training_caps.memory_max`, the most the GPU
arbiter lets a job take. The arbiter never runs vLLM and a job at once, so
on a node that serves a model the recommended value counts the larger of the
two, never their sum. With `memory_max: auto` (the default), preflight
computes the cap from the host: `MemTotal` minus the recommended memory of
the core stack and the targets, rounded down to whole GiB, within a floor of
4 GiB and a ceiling of 24 GiB (`training_cap` in
`preflight/requirements.yml`). The selected model does not lower it. An
explicit `memory_max`, such as `12G`, wins. The host profile records the cap
and how it was computed (`requirements.switches.training`); the GPU arbiter,
the platform's validation and doctor use it.

Above the floor, the computed cap fits the host by construction. Below it,
the floor still counts, and the message names what would fit. With the
defaults and a usable GPU:

| Host | `MemTotal` | VM targets, no model | containers only, no model | VM targets, a 5.7 GiB model |
| --- | --- | --- | --- | --- |
| nominal 16 GiB | 15.0-15.7 GiB | cap 4G; recommends 17.2 GiB: `degraded` (fits: one target, a cap of 2-3G, or `training: off`) | cap 4G; recommends 15.2 GiB: fits | cap 4G; vLLM itself recommends 10.7 GiB, 23.9 GiB in all: `degraded` |
| nominal 32 GiB | 30.1-31.4 GiB | cap 16-18G; recommends 29.2-31.2 GiB: fits | cap 18-20G: fits | cap 16-18G; the job is larger than vLLM's 10.7 GiB, 29.2-31.2 GiB in all: fits |

The Incus `bench` project's `limits.memory` follows the same choice: the
concurrent targets, each with its probe client.

CPU threads stay a fixed minimum of the support matrix (8 required, 16
recommended); the breakdown shows each part's CPU estimate for information.

## Disk at install

What is known at install time is checked per mount point, item by item:

- pinka itself: 5 GiB on the file system of `/opt/pinka`;
- the container images the node pulls, on Docker's data root: each image's
  compressed size for this architecture (in `versions.lock.yml`) times 2.5,
  until it is pulled; then its real size;
- the model's files below the research space, their sizes from
  `models.lock.yml`;
- the Incus pool: a loop file counts fully on its mount point
  (`/var/lib/incus/disks/`), a dedicated device on none; its default size is
  computed (see [The pool's size](#the-pools-size));
- each golden image to build, its estimate from its `image.yaml`, twice:
  the export below the research space and Incus's image store.

What is already on the host (a pulled image, a downloaded model, a built
golden image, the pool's file) needs no more space and counts nothing, so
preflight gives the same answer on a re-run. When a mount point is short, the
host is `unsupported` and the shortfall is listed with the largest items
first, the ones to shrink, for example, with an explicit
`incus_pool_size: 300GiB` on a host with 100 GiB free,
`the Incus pool (loop file): 300.0 GiB, missing 232.3 GiB on /`.

## The pool's size

A loop pool's size is `incus_pool_size: auto` unless you set one; preflight
computes it like the training cap, from what the pool holds:

- the golden images to build (each blueprint's estimate);
- the root disks of the concurrent targets, each with its probe client (a
  VM target 16 GiB where the host has VM targets, a container 8 GiB, the
  probe client 4 GiB); or the build instance's 16 GiB when golden images
  are built and it is larger, since a build runs while no target runs;
- a margin of 50 % for the instances' copy-on-write changes, snapshots and
  the targets' own data;

rounded up to whole GiB. It is limited by the free space on the loop file's
mount point minus pinka's other needs there: the other items at install
and, when the research space is on the same file system, what the stores
hold at their retention. It is never below 30 GiB, the pool the
distribution test VMs install with; below that the host is refused like any
other shortfall. The summary shows the size and how it was computed.

With the defaults (two concurrent targets, the golden images, one file
system for everything):

| Free on `/` | VM targets | Container targets |
| --- | --- | --- |
| 386 GiB | 66 GiB | 42 GiB |
| 144 GiB | 66 GiB | 42 GiB |
| 60 GiB | 30 GiB (limited, the floor) | 30 GiB |

An explicit size (`incus_pool_size: 300GiB`, or a number in the installer)
wins and is checked like every other item. Incus creates the pool once and
never resizes it: on a re-run the existing pool keeps its size, and preflight
counts it as present.

## What grows with use

What grows is never a reason to refuse. Preflight shows it below the
breakdown: per store, its growth per day and the size its retention holds it
to, then the growth per day and per week below the research space and the
day the disk fills at that rate.

| Store | Growth (estimated) | Held by |
| --- | --- | --- |
| Prometheus | 0.05 GiB a day | `observability_prometheus_retention` (90d) and `observability_prometheus_retention_size` |
| Loki | 0.1 GiB a day | `observability_loki_retention` (2160h) |
| Tempo | 0.05 GiB a day | `observability_tempo_retention` (720h) |
| local PostgreSQL dumps | 0.05 GiB a day | `backup_pgdump_keep` (7) |
| the database | 0.02 GiB a day | nothing: the platform's records |
| training runs | up to `training_caps.run_storage_max` per run | nothing: research data, never pruned by pinka |

The platform's own logs, traces and state come on top and follow the
platform's settings. Doctor records the free space of every mount point the
lab uses on each run and warns at 15 % free, or when the recorded trend fills
one within 7 days ([`disk.free_space`](../how-to/fix-doctor-checks.md#check-disk-free_space)).

## Where to change a choice

| Choice | Setting (host_vars) | Installer answers |
| --- | --- | --- |
| concurrent targets | `incus_concurrent_targets` | `resources.concurrent_targets` |
| pool | `incus_pool_source`, `incus_pool_size` (`auto`: computed) | `storage` |
| model | `vllm_default_deployment` | `gpu.default_model` |
| golden images | `images_blueprints` | `images.build` |
| inference, training | `inference`, `training` | `features` |
| training caps | `training_caps` (`memory_max: auto`: computed) | `training_caps` |
| retention | `observability_*_retention`, `observability_prometheus_retention_size`, `backup_pgdump_keep` | `telemetry_retention`, `backups.pg_dumps_kept` |

Run `sudo pinka preflight` after a change to see the new breakdown; bootstrap
applies it.
