---
title: What pokus-infra provides
type: explanation
audience: [operator, developer, reader]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
---

# pokus-infra

pokus-infra turns a Linux machine into a Pokus lab host. Pokus is an
open-source lab for benchmarking AI diagnostic agents on reproducible,
injected Linux faults. pokus-infra provides everything below the research
services: host checks, accounts, remote access, the isolated target sandbox,
local model serving, observability, backups and health checks. The research
services themselves come from pokus-platform and run on top of what this
component provisions.

!!! warning "Work in progress"
    This component is being built step by step. Pages are added together with
    the code they describe; until then this site contains only the reference
    pages listed in the navigation.

## Where to start

- [Check whether a machine can host the lab](how-to/check-a-host.md) before
  installing anything.
- [How the preflight gate decides](explanation/preflight-gate.md) explains the
  checks and the [support matrix](support-matrix.md) lists the supported
  distributions, architectures and environments.
- The [integration contract](contracts.md) lists every path, account, socket,
  port and file that pokus-infra provides to pokus-platform.

## Licenses

Code is licensed under Apache-2.0 (`LICENSE`); documentation is licensed under
CC-BY-4.0 (`LICENSE-docs`).
