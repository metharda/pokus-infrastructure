---
title: "ADR-0013: No orchestration frameworks"
type: explanation
audience: [developer, reader]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
---

# ADR-0013: No orchestration frameworks

- **Status:** accepted
- **Date:** 2026-10-07
- **Deciders:** pokus-infra maintainers (BUILD_PROMPT §1)

## Context

A Pokus lab runs on one machine, optionally with one paired inference node.
It hosts a handful of long-running services, the benchmark targets of one run
at a time (or a few), and a GPU serving one model. Cluster managers and job
schedulers (Kubernetes, Slurm, Ray, Nomad, GPU schedulers) are built for many
machines and many tenants, and each brings its own networking, storage,
scheduling and upgrade cycle.

## Decision

1. The lab uses no orchestration framework or job scheduler. Services are
   systemd units and Compose projects started by `pokus-lab.target`; targets
   are Incus instances that the platform creates through the Incus API; the
   GPU is used by one vLLM deployment at a time, switched by the root-owned
   deploy helper.
2. Ordering and health are expressed with systemd dependencies and the health
   gate of `pokus-lab.target`; concurrency of runs is the platform's job.
3. A second machine joins only as an inference node, paired over the tailnet
   with mutual TLS, not as a cluster member.

## Consequences

- Every moving part is a standard Linux mechanism that the fingerprint can
  record and doctor can check, and the lab installs the same way on WSL2, bare
  metal and VMs.
- Measurements are not disturbed by a scheduler's own components, and CPU
  sets can keep the lab's services apart from the targets.
- The lab does not scale out beyond one lab node and one inference node;
  larger studies use several independent labs.

## Alternatives considered

- **Kubernetes (or a small distribution of it)**: its networking and storage
  would duplicate what Incus and Docker already do, and its control plane
  would run beside every measurement.
- **Slurm, Ray, Nomad or a GPU scheduler**: aimed at sharing many GPUs among
  many users; a lab with one GPU and an orchestrator that runs one measurement
  at a time does not need a second scheduler.
