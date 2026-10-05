---
title: "ADR-0001: Preflight uses only the Python standard library"
type: explanation
audience: [developer, reader]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
---

# ADR-0001: Preflight uses only the Python standard library

- **Status:** accepted
- **Date:** 2026-10-05
- **Deciders:** pokus-infra maintainers (owner answer E10 for the matrix format)

## Context

Preflight decides whether installation may start at all, so it runs before
any dependency is installed: on a fresh host, possibly with an old system
interpreter, and later again from `pokus-lab` when the managed environments
may be broken. Its decision must also be reproducible and testable without
real hardware.

## Decision

1. The preflight package uses the Python standard library only and stays
   source-compatible with Python 3.9 (`from __future__ import annotations`,
   no `match`, no 3.10+ library calls).
2. The support matrix and the endpoint list are written in YAML for people
   and compiled to committed JSON at build time; preflight reads only the
   JSON. A drift check fails CI if the JSON is out of date.
3. The host profile is validated at runtime by a small validator that
   implements exactly the JSON Schema keywords the profile schema uses; it
   rejects any other keyword. The test suite cross-checks every fixture with
   the reference `jsonschema` implementation.
4. Every filesystem, command and network access goes through the `fs` and
   `runner` interfaces, so fixture roots replace the real host in tests.

## Consequences

- Preflight runs anywhere a Python 3.9+ interpreter exists, without network
  access to a package index.
- The pinned mypy no longer type-checks against Python 3.9 (its minimum is
  3.10). Compatibility is enforced instead by ruff with a `py39` target and by
  running the full preflight test suite on CPython 3.9 in `make test`.
- The YAML-to-JSON step adds a generated file per source; the drift check
  keeps them in sync.

## Alternatives considered

- **A bundled YAML subset parser** (explicitly allowed by BUILD_PROMPT §4):
  rejected because a hand-written parser is a new source of subtle bugs in
  the component that gates installation.
- **Third-party libraries in a bootstrap environment** (PyYAML, jsonschema):
  rejected because preflight must run before any environment exists.
