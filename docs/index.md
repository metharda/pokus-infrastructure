---
title: What Pokus Infrastructure provides
type: explanation
audience: [operator, developer, reader]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
---

# Pokus Infrastructure

Pokus Infrastructure (pinka) turns a Linux machine into a Pokus lab host. Pokus is an
open-source lab for benchmarking AI diagnostic agents on reproducible,
injected Linux faults. Pokus Infrastructure provides everything below the research
services: host checks, accounts, remote access, the isolated target sandbox,
local model serving, observability, backups and health checks. The research
services themselves come from Pokus Platform (potka) and run on top of what this
component provisions.

## Where to start

- New lab: [Install a lab on WSL2](tutorials/install-wsl2.md) (the reference
  setup) or [Install a lab on bare-metal Linux](tutorials/install-baremetal.md).
  Before installing anything,
  [check whether a machine can host the lab](how-to/check-a-host.md); the
  [support matrix](support-matrix.md) lists the supported distributions,
  architectures and environments.
- Running lab: [Operating the lab](operations.md) maps every operator task to
  its guide; researchers start with [How to connect to the lab](how-to/connect.md).
- Understanding the design: [Lab host architecture](architecture.md),
  [Security of the lab host](security.md) and
  [Reproducibility, fingerprints and sessions](reproducibility.md).
- Integrating with the platform: the [integration contract](contracts.md)
  lists every path, account, socket, port and file that Pokus Infrastructure provides
  to Pokus Platform.

## Licenses

Code is licensed under Apache-2.0 (`LICENSE`); documentation is licensed under
CC-BY-4.0 (`LICENSE-docs`).
