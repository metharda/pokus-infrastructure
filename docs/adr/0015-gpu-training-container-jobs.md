---
title: "ADR-0015: GPU training as infra-run container jobs"
type: explanation
audience: [developer, operator, reader]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
---

# ADR-0015: GPU training as infra-run container jobs

- **Status:** accepted: decision 7 (GPU training jobs, items 7.1 to 7.7 for
  infra) and the new points N3 and N4 of the owner's decisions on contract
  v2 after infra step 14, written into the build prompt by plan item 3b; the
  arbiter's process model, the job interface, the run ID format and the
  topology approved with the "commit" for 3d.5 (implemented in 3d.5); the
  size caps and the resume after `pinka start` asked for with the same
  "commit" (implemented in 3d.6)
- **Date:** 2026-10-09, the spec update of plan item 3b that wrote the
  owner's decision into the build prompt; the decision itself has no
  recorded date
- **Deciders:** pokus-infrastructure maintainers (BUILD_PROMPT §7.18, §12.8)

## Context

The research loop the lab supports is: test, train an adapter on the same
GPU, serve the new model, re-test on the same benchmark (BUILD_PROMPT §1).
The reference machine has one NVIDIA GPU, and vLLM serves the agents' model
from it. Contract v1 had no training or adapter-creation flow: the platform
could only import adapters, and a root helper switched vLLM deployments for
the orchestrator.

Training adds four requirements:

- **One GPU, two users.** vLLM serving and training both want the GPU, and
  only one of them may hold it at a time.
- **No GPU devices or root for platform code.** Every platform unit runs
  with `PrivateDevices=yes`, an empty capability bounding set and
  `NoNewPrivileges=yes` (§7.14), and the controller's profile says "never
  GPU devices" ([ADR-0014](0014-platforms-through-manifests.md)).
- **No contamination.** Training data is built from benchmark runs and
  scenario data, and ground truth is restricted data (§12.4). A model trained
  on instances of the `test` split would invalidate the benchmark it is
  tested on.
- **Traceable results.** An adapter is a research result: it must be
  traceable to its base model, data, image, settings and commits, and
  backed up.

## Decision

1. **Jobs in the manifest, run by infra.** A platform declares its training
   jobs in the manifest's `jobs[]` with the profile `gpu-trainer`, the only
   profile that ever gets GPU devices. The image is an OCI reference pinned
   by `@sha256:` digest, pulled at deployment and never at job start. The
   controller submits a run (the job, a base model from `models.lock.yml`,
   dataset hashes, hyperparameters and a seed); the arbiter checks it,
   computes the job-spec hash from the canonical submission and runs the job
   as the unit `pinka-gpu-job@<run>` with the container `pinka-job-<run>` in
   infra's Docker.
2. **Container options fixed by infra.** No manifest field adds a mount, a
   capability, a network or a namespace. Every job container gets
   `--network none`; no privileged mode; a `--read-only` root file system
   with a private, size-limited `/tmp` that counts against its memory;
   `--cap-drop ALL`; `no-new-privileges`; Docker's default seccomp profile;
   no host PID, IPC, network or user namespace; the job's own non-root
   account `<platform>-<job>` with `pinka-datasets` as its only extra group;
   the GPU through CDI as for vLLM, WSL2 included; and memory, CPU and
   process limits within infra's caps. It sees exactly three kinds of mounts:
   the base model (read-only), the declared datasets (read-only) and the
   run's `output/` and `checkpoints/` directories (read-write). No restricted
   data, no sockets, no secrets.
3. **One root-owned GPU arbiter.** `pinka-gpu-arbiter`, socket-activated,
   replaces contract v1's deploy helper. Its socket `/run/pinka/gpu.sock`
   (0600) belongs to the active platform's controller; root uses it through
   `pinka gpu`. Its modes are `idle`, `serving <deployment>` and
   `training <run>`, exactly one at a time, and vLLM starts only through it.
   The arbiter refuses `train` while vLLM has running or waiting requests or
   before the controller has declared its sessions drained; it then stops
   vLLM, runs the job and afterwards restores the previous deployment unless
   the request asks otherwise. Every transition goes to
   `/var/lib/pinka/gpu/transitions.jsonl` and to the journal.
4. **Datasets published through the split guard.** The platform builds a
   dataset in its controller's state and asks the arbiter to publish it. The
   arbiter copies and hashes it into `/srv/pinka/training/datasets/<sha256>/`
   (root:`pinka-datasets`, read-only; the group has no human members) with
   `PROVENANCE.json`: every scenario instance with its split, the source runs
   and the builder's platform commit. It refuses to publish, and refuses to
   mount, a dataset whose provenance lists an instance of the `test` split,
   and it verifies the dataset's hash at mount. Data derived from restricted
   data reaches a job only this way.
5. **Adapters registered with lineage.** On success the arbiter hashes the
   adapter and registers it read-only under
   `/srv/pinka/models/adapters/<name>/<sha256>/` with `MANIFEST.json` and
   `LINEAGE.json` (base model hashes, dataset hashes, image digest, job-spec
   hash, hyperparameters, seed, the platform's and infra's commits, start
   and end). Deployment specs name adapters as `<name>@<sha256>` in
   `adapter_ids`, and vLLM loads only registered adapters. The record
   schemas are in `contract/records/`; adapters and datasets are backed up by
   default.
6. **Checkpoints and resume.** Jobs checkpoint into their run's
   `checkpoints/` directory. A run that the lab's stop or a crash left
   behind resumes from its checkpoint when the lab starts again (after a
   reboot or on `pinka start` alike) only if its job declares
   `resumable: true`; otherwise it ends `interrupted`, and the previous
   deployment comes back or the GPU stays idle.
7. **Caps.** Timeout, memory and CPUs come from the manifest within
   `training_caps`, checked at submission. A dataset larger than
   `dataset_max` is never published; a run starts only with
   `run_storage_max` of free space and is stopped as failed when its output
   and checkpoints grow past it.
8. **A CPU-only variant.** `resources.gpus: 0` runs the same flow without
   the device, for hosts without a GPU and for tests; the reference
   platform's `tune` and `tunecpu` are the two variants of one toy job.
9. **Full nodes only.** Training runs only on `node_role: full`. On a `lab`
   and `inference` pair the installer and doctor state that training is
   unavailable there.

## Consequences

- vLLM is stopped for the whole run: the agents' model is unavailable while
  a job trains, no benchmark request is served by a GPU that a job is using,
  and the platform drains its sessions before it asks for `train`.
- A job has no network at run time and sees nothing but its mounts, so
  everything it needs must be in its image, its base model or its datasets.
- The GPU is reached only by vLLM, which infra runs, and by a `gpu-trainer`
  container while the arbiter is in training mode for it; no platform
  service ever gets a GPU device or root.
- Long runs survive a stop or a reboot only if the job is resumable and
  writes checkpoints; any other job ends `interrupted` or `failed` and must
  be submitted again.
- Every served adapter can be traced to its inputs. Doctor checks the
  registered hashes, restore re-registers adapters and verifies their
  hashes, and the platform can flag test results whose scenario template is
  in an adapter's training data, read from the lineage (the contamination
  flag, a platform-side addition of decision 7).
- The split guard is only as good as the provenance: infra checks the
  instances and splits it is given, and that the list is complete and
  truthful is the platform's obligation, tested on the platform side.
- Training on a `lab` and `inference` pair (split-node training) is future
  work; a lab without a `full` node cannot train.
- Training jobs run in containers while platform services stay systemd
  units ([ADR-0014](0014-platforms-through-manifests.md)).

## Alternatives considered

- **Training inside a platform service**: the service would need the GPU
  devices that the hardening baseline hides from every platform unit
  (`PrivateDevices=yes`), and it would keep all the grants of its profile;
  for the controller that includes reading restricted data and the
  loopback network. A container that sees only its declared inputs keeps
  restricted data out of training and puts every dataset in front of the
  split guard.
- **A second GPU for training**: the reference machine has one GPU, and the
  research loop trains on the same GPU it serves from (§1); infra's cap on a
  job's GPUs is 1.
- **A cluster or GPU scheduler** (Kubernetes, Slurm, Ray, Nomad):
  [ADR-0013](0013-no-orchestration-frameworks.md) rejects them for the lab
  as a whole; the arbiter is a mode switch on one machine, not a scheduler.
- **Container options from the manifest**: a manifest that could add
  mounts, capabilities, networks or namespaces could bring restricted data,
  a socket or a secret into a job. The options are fixed in the arbiter
  instead.

How the arbiter works is explained in
[The GPU arbiter](../explanation/gpu-arbiter.md), its requests are listed in
[GPU arbiter requests](../reference/gpu-arbiter.md), and a run end to end is
in [How to run a training job and serve its adapter](../how-to/run-training-job.md).
