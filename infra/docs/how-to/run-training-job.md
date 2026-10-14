---
title: How to run a training job and serve its adapter
type: how-to
audience: [operator, developer]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: Publish a dataset, start a training run through the GPU arbiter, follow it, and serve the registered adapter in a new deployment.
---

# How to run a training job and serve its adapter

Normally the platform's controller does all of this through the GPU
arbiter's socket; this page shows the same steps as an admin with
`pinka gpu`, for a first run, a test or an investigation.
[The GPU arbiter](../explanation/gpu-arbiter.md) explains what each step
checks.

!!! note "Prerequisites"
    - A `full` node (training is unavailable on `lab` and `inference`
      nodes) with an active platform whose manifest declares the job.
    - The base model downloaded and verified ([How to add a model](add-a-model.md)).
    - A dataset built by the platform in its controller's state, already
      published by the controller, or its hash.
    - An admin account (member of `pinka-admin`, using `sudo`).

## Check the arbiter

1. Look at the GPU's mode and whether this host trains:

    <!-- qa:skip reason="reads the GPU arbiter on the lab host" -->
    ```bash
    sudo pinka gpu status
    ```

    A run starts only while no other run trains. While a deployment is
    served, the controller must have drained its sessions and vLLM must
    have no running or waiting request.

## Start the run

1. Write the submission, with the job's dataset kinds mapped to published
   dataset hashes:

    <!-- qa:skip reason="example of a submission file" -->
    ```json
    {
      "base_model": "<model-id>",
      "datasets": {"train": "<dataset-sha256>"},
      "hyperparameters": {"learning_rate": 0.0002, "epochs": 1},
      "seed": 1,
      "sessions_drained": true
    }
    ```

    `<model-id>` is the base model's id in `models.lock.yml`;
    `<dataset-sha256>` is the hash the arbiter returned when the dataset was
    published (the directory name under `/srv/pinka/training/datasets/`).
    Add `"restore": false` to leave the GPU idle after the run.

2. Start the run:

    <!-- qa:skip reason="starts a training run on the lab host" -->
    ```bash
    sudo pinka gpu train <job> --submission <file>
    ```

    `<job>` is the job's name in the manifest; `<file>` is the submission.
    The command prints the run ID and the job-spec hash. The arbiter stops
    vLLM and starts `pinka-gpu-job@<run>.service`.

## Follow the run

1. Watch the job's output and the arbiter's transitions:

    <!-- qa:skip reason="follows a training run on the lab host" -->
    ```bash
    sudo journalctl -fu pinka-gpu-job@<run>.service
    sudo pinka gpu status
    ```

    `<run>` is the run ID from the previous step.

2. To stop the run, ask for idle. A resumable job writes its checkpoint and
   ends `interrupted`; any other job ends `failed`:

    <!-- qa:skip reason="stops a training run on the lab host" -->
    ```bash
    sudo pinka gpu idle
    ```

## Serve the adapter

1. When the run succeeds the arbiter registers the adapter and serves the
   previous deployment again. Find the adapter's hash:

    <!-- qa:skip reason="reads the adapters of the lab host" -->
    ```bash
    sudo ls /srv/pinka/models/adapters/<adapter>/
    ```

    `<adapter>` is the job's `outputs.adapter` name.

2. Write a new deployment spec that names the adapter in `adapter_ids`, as
   `<adapter>@<sha256>`, next to the base model it was trained on
   ([How to add a model](add-a-model.md) shows the spec), and serve it:

    <!-- qa:skip reason="switches the served deployment on the lab host" -->
    ```bash
    sudo pinka gpu serve <id>@<version>
    ```

    `<id>@<version>` is the new deployment. The arbiter refuses an adapter
    that is not registered, whose files no longer match its hash, or that
    was trained on another model.

## Verify the run and the adapter

1. Run doctor's training checks:

    <!-- qa:skip reason="runs doctor on the lab host" -->
    ```bash
    sudo pinka doctor
    ```

    `gpu.arbiter`, `training.adapters` and `training.datasets` pass;
    `training.containers` checks a running job's container.

2. Read the adapter's lineage: base model, datasets, image digest, job-spec
   hash, hyperparameters, seed and commits:

    <!-- qa:skip reason="reads an adapter's lineage on the lab host" -->
    ```bash
    sudo cat /srv/pinka/models/adapters/<adapter>/<sha256>/LINEAGE.json
    ```

    `<adapter>` and `<sha256>` are the adapter's name and hash.
