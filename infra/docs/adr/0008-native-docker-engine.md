---
title: "ADR-0008: Native Docker Engine for lab services"
type: explanation
audience: [developer, reader]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
---

# ADR-0008: Native Docker Engine for lab services

- **Status:** accepted
- **Date:** 2026-10-05
- **Deciders:** pokus-infrastructure maintainers (BUILD_PROMPT §7.6)

## Context

The lab services (PostgreSQL, OPA, the observability stack, vLLM) run as
containers pinned by digest. On WSL2 hosts, Docker Desktop is a common way to
get Docker, but it runs the engine in its own VM managed from Windows, outside
the lab's Linux host.

## Decision

1. The lab uses Docker Engine installed natively in the lab host, from
   Docker's repositories where Docker publishes them (Ubuntu, Debian, Fedora,
   RHEL-compatible) and from the distribution elsewhere, pinned and held.
2. Docker Desktop's WSL integration is a blocking preflight conflict.
3. Only the docker role writes `/etc/docker/daemon.json`, including GPU
   access (CDI, or the NVIDIA runtime on engines without CDI).
4. Researchers are never in the `docker` group; only admins on an explicit
   break-glass list are, which is logged and recorded in the fingerprint.

## Consequences

- The engine, its version and its configuration are part of the lab host and
  its fingerprint, the same on WSL2, bare metal and VMs.
- Docker's `FORWARD` policy interferes with Incus bridges; the firewall role
  adds the coexistence rules Incus documents.
- On WSL2 the GPU comes from the Windows driver through CDI in WSL mode, not
  through Docker Desktop.

## Alternatives considered

- **Docker Desktop**: not part of the Linux host,
  configured from Windows, and Pokus Infrastructure (pinka) cannot pin its version.
- **Podman**: workable, but the services' Compose files, GPU documentation and
  the platform's tooling assume Docker's API and Compose v2.
