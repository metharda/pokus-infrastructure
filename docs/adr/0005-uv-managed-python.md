---
title: "ADR-0005: uv-managed Python instead of the distribution's Python"
type: explanation
audience: [developer, reader]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
---

# ADR-0005: uv-managed Python instead of the distribution's Python

- **Status:** accepted
- **Date:** 2026-10-05
- **Deciders:** pokus-infrastructure maintainers

## Context

Ansible, the installer, `pinka` and the platform are Python programs.
Distributions ship Python versions years apart (some minimal RHEL images ship
only `platform-python`), with their own patches and package splits. Relying on
them would make the lab's behavior depend on the host distribution and would
make two labs on different hosts run different interpreters.

## Decision

1. Bootstrap installs **uv** as a static binary verified against the sha256
   pinned in `versions.lock.yml`, then a **pinned CPython** with
   `uv python install` into `/opt/pinka/python`.
2. Ansible runs from a virtual environment of that interpreter
   (`/opt/pinka/ansible-venv`), installed from the hash-locked
   `bootstrap/requirements.lock`. The installer, `pinka` and the platform
   use the same interpreter with their own lockfiles.
3. The distribution's Python is never required for Pokus Infrastructure (pinka) code. Package
   modules that need the distribution's bindings (`apt`, `dnf`) use
   ansible-core's respawn to the system interpreter for that one module; the
   `pkg` role keeps that dependency in one place.
4. Preflight is the only exception: it runs before anything is installed and
   therefore uses the system's Python 3.9 or newer with the standard library
   only ([ADR-0001](0001-stdlib-only-preflight.md)).

## Consequences

- Every host runs the same interpreter and the same Ansible version; both are
  in the fingerprint.
- The lab needs network access to the CPython build on first install (or an
  offline mirror); the downloads are verified against pinned checksums.
- Distribution updates of the system Python cannot break Pokus Infrastructure.

## Alternatives considered

- **Distribution Python and packages**: different versions on every family,
  no hash-locked dependencies.
- **pyenv or building CPython from source**: needs a compiler toolchain on the
  host and takes long; uv's builds are prebuilt and checksummed.
- **Containerized Ansible**: Ansible must manage the host itself, including
  Docker; running it inside a container adds a circular dependency.
