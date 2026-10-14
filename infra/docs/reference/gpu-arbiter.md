---
title: GPU arbiter requests
type: reference
audience: [developer, operator]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: The GPU arbiter's socket protocol, its files, the job container's fixed options and mounts, and what a training job sees.
---

# GPU arbiter requests

The GPU arbiter (BUILD_PROMPT §7.18, [explanation](../explanation/gpu-arbiter.md))
answers on `/run/pinka/gpu.sock` (0600, owned by the active platform's
controller; on an inference node by `pinka-inference`). Root uses the same
socket through `pinka gpu`.

## Protocol

One connection carries one request: a single line of JSON (at most 1 MiB),
answered with a single line of JSON. Every answer has `ok`; a refusal has
`ok: false` and `error`. Unknown keys are refused. A `serve` answer comes
when the deployment answers on `/health` (or failed), an `idle` answer when
a running job has stopped.

| Request | Keys | Answer | Who |
| --- | --- | --- | --- |
| `status` | `request` | `mode`, `deployment`, `run`, `since`, `node_role`, `training_available`, `transitions` (the last ten), `job` (while training) | controller, root, the control endpoint |
| `serve` | `request`, `deployment` (`<id>@<version>`) | `mode`, `deployment`, `changed` | controller, root, the control endpoint |
| `idle` | `request` | `mode`; `run` when a job was stopped | controller, root |
| `train` | `request`, `job`, `submission` | `mode`, `run`, `spec_sha256` | controller, root; `full` nodes only |
| `publish-dataset` | `request`, `source`, `provenance` | `dataset_sha256`, `published` (false when it existed) | controller, root; `full` nodes only |

**Submission** (`train`): `base_model` (a model id of `models.lock.yml`, or
null for a job without one), `datasets` (exactly the job's dataset kinds,
each mapped to a published dataset hash), `hyperparameters` (an object),
`seed` (an integer), `sessions_drained` (true; required while vLLM serves),
`restore` (optional; default `gpu_arbiter.restore_after_training`). The
job-spec hash is the SHA-256 of the canonical JSON (sorted keys, no
whitespace) of `job`, `base_model`, `datasets`, `hyperparameters` and
`seed`. The run ID is `<job>-<YYYYmmddtHHMMSS>-<first 8 hex of the hash>`.

**Publication** (`publish-dataset`): `source` is an absolute path below one
of the controller's state directories (without links); every file and
directory in it must belong to the controller. `provenance` has exactly
`instances` (`{id, split}` each; no `test`) and `source_runs`.

## Files

| Path | Content |
| --- | --- |
| `/etc/pinka/gpu/arbiter.json` | settings: node role, peer, `gpu_arbiter`, `training_caps`, how containers reach the GPU, ports, timeouts |
| `/var/lib/pinka/gpu/state.json` | the mode (0644) |
| `/var/lib/pinka/gpu/transitions.jsonl` | every transition: `time`, `from`, `to`, `requester`, `deployment`, `run`, `job`, `outcome`, `detail` (0644) |
| `/var/lib/pinka/gpu/runs/<run>.json` | a run's record: job, submission, hash, status, attempts, times, adapter (root only) |
| `/srv/pinka/training/datasets/<sha256>/` | a published dataset with `PROVENANCE.json` (root:`pinka-datasets` 0750, files 0640) |
| `/srv/pinka/training/jobs/<run>/` | the run's `output/` and `checkpoints/` (the job's account, 0750) |
| `/srv/pinka/models/adapters/<name>/<sha256>/` | a registered adapter with `MANIFEST.json` and `LINEAGE.json` (root, 0555, files 0444) |

The record formats are in [Platform records](platform-records.md). A
dataset's or adapter's hash is the SHA-256 of the lines
`<sha256>  <path>` (the output format of `sha256sum`) of its files, sorted
by path, without its own record files.

## The job's container

`docker run --rm --name pinka-job-<run> --pull never` with these options,
which no manifest field changes:

| Option | Value |
| --- | --- |
| `--network` | `none` |
| `--read-only`, `--tmpfs` | read-only root file system; `/tmp:rw,nosuid,nodev` |
| `--cap-drop` | `ALL` |
| `--security-opt` | `no-new-privileges` (Docker's default seccomp profile stays) |
| `--ipc` | `private`; no host PID, network or user namespace |
| `--user`, `--group-add` | the job's account `<platform>-<job>`; `pinka-datasets` |
| `--memory`, `--memory-swap`, `--cpus` | the manifest's `memory_max` (no swap) and `cpus`, within `training_caps` |
| `--pids-limit` | 4096 (`gpu_arbiter_pids_limit`) |
| GPU | `--device nvidia.com/gpu=all` (CDI) or the NVIDIA runtime, only with `resources.gpus: 1` |

| Inside the container | Content |
| --- | --- |
| `/model` | the base model, read-only (with `inputs.base_model`) |
| `/datasets/<kind>` | each declared dataset, read-only |
| `/output` | read-write; the job leaves its adapter in `/output/adapter/` |
| `/checkpoints` | read-write; a resumed run finds its checkpoints here |
| `PINKA_JOB_RUN`, `PINKA_JOB_SEED` | the run ID and the seed |
| `PINKA_JOB_HYPERPARAMETERS` | the hyperparameters as compact JSON |
| `PINKA_JOB_RESUME` | `1` when the run resumes after a reboot |

The job's standard output and error go to the journal of
`pinka-gpu-job@<run>.service`. A run ends `failed` on a timeout
(`timeout_seconds`), a non-zero exit or a missing or empty adapter.
