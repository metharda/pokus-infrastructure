---
title: GPU arbiter requests
type: reference
audience: [developer, operator]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: The GPU arbiter's socket protocol, its files, the job container's fixed options and mounts, and what a training job sees.
---

# GPU arbiter requests

The GPU arbiter ([explanation](../explanation/gpu-arbiter.md))
answers on `/run/pinka/gpu.sock` (0600, owned by the active platform's
controller; on an inference node by `pinka-inference`). Root uses the same
socket through `pinka gpu`.

## Protocol

One connection carries one request: a single line of JSON (at most 1 MiB),
answered with a single line of JSON. Every answer has `ok`; a refusal has
`ok: false` and `error`. Unknown keys are refused. A `serve` answer comes
when the deployment answers on `/health` (or failed), an `idle` answer when
a running job has stopped.

A run's status (`runs/<run>.json`, `job.status` of `status`) is
`starting`, `resuming` or `running` while it holds the GPU; `suspended`
when the lab's stop suspended it (`pinka stop`, `pinka update-host`, a
stopped job unit, a shutdown): the GPU stays in `training` for it and it
resumes when the lab starts, after a reboot and on `pinka start` alike;
`succeeded`, `failed` or `interrupted` at its end. An `idle` without
`resume` ends a suspended run (`interrupted`).

| Request | Keys | Answer | Who |
| --- | --- | --- | --- |
| `status` | `request` | `mode`, `deployment`, `run`, `since`, `node_role`, `training_available`, `transitions` (the last ten), `job` (while training) | controller, root, the control endpoint |
| `serve` | `request`, `deployment` (`<id>@<version>`) | `mode`, `deployment`, `changed` | controller, root, the control endpoint |
| `idle` | `request`; root only: `resume` (true: the lab stops, a resumable run is suspended and resumes when the lab starts) | `mode`; `run` when a job was stopped; `suspended` when it was suspended | controller, root |
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

The record formats are in [Platform records](platform-records.md).

## The tree hash

A dataset's or adapter's hash is the same on every machine:

- one line per regular file, without the tree's own record files:
  `<sha256>  <path>` and a newline, the file's SHA-256 in lowercase hex, two
  spaces and its path relative to the tree's root, with `/` separators and
  no leading `./` (what `sha256sum` prints);
- the lines sorted by the path's UTF-8 bytes, as `LC_ALL=C sort` sorts
  them, whatever the locale (`B` before `_` before `a`, `a.txt` before
  `a/z`);
- the hash is the SHA-256, in lowercase hex, of the lines' UTF-8 bytes.

A file or directory name that is not UTF-8 or holds a control character or
a backslash is refused at publication and at registration (`sha256sum`
would escape it), and doctor reports such a tree as damaged. The same hash
from a shell, in the tree's directory:

<!-- qa:skip reason="example; run it in a dataset or adapter directory" -->
```bash
find . -type f ! -name PROVENANCE.json -printf '%P\n' | LC_ALL=C sort \
  | while IFS= read -r f; do sha256sum -- "$f"; done | sha256sum
```

Leave out `MANIFEST.json` and `LINEAGE.json` instead for an adapter.

## The job's container

`docker run --rm --name pinka-job-<run> --pull never` with these options,
which no manifest field changes:

| Option | Value |
| --- | --- |
| `--network` | `none` |
| `--read-only`, `--tmpfs` | read-only root file system; `/tmp:rw,nosuid,nodev,size=<n>`, `<n>` the smaller of `tmp_max` and the job's memory; the kernel counts the tmpfs against the job's memory limit |
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
(`timeout_seconds`), a non-zero exit, output and checkpoints over
`run_storage_max` (measured every 30 seconds while the job runs; the
container is stopped) or a missing or empty adapter.

## Caps

`training_caps` (host_vars) bounds every job; a key left out takes
the default shown.

| Cap | Default | Checked |
| --- | --- | --- |
| `timeout_seconds` | 86400 | at submission (the job's `timeout_seconds`); the run fails when it runs out |
| `memory_max` | `24G` | at submission (the job's `resources.memory_max`, also its swap limit) |
| `cpus` | 8 | at submission (the job's `resources.cpus`) |
| `dataset_max` | `8G` | at publication (the copy stops as soon as it is larger) and at mount |
| `run_storage_max` | `16G` | free space on `/srv/pinka/training/jobs` at submission and at start; the run's `output/` and `checkpoints/` while it runs |
| `tmp_max` | `1G` | the size of the job's `/tmp` |

The deployment and doctor (`training.caps`) warn when a job of the active
platform exceeds this host's caps: the platform deploys, but every run of
that job is refused.
