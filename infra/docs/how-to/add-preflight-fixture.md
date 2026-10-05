---
title: How to add a preflight fixture
type: how-to
audience: [developer]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: Describe a new kind of host as a fixture so that preflight's decision for it is tested.
---

# How to add a preflight fixture

A fixture is a fake root that looks like one kind of host to preflight. Add
one whenever you change detection or the support matrix for a case that no
fixture covers yet.

!!! note "Prerequisites"
    - A development checkout of the repository with `make setup` done.
    - The decision you expect for the new host (outcome, tier, provider, features).

!!! warning "No real host data"
    Fixtures are public. They must never contain host names, user names,
    serial numbers, UUIDs, MAC or IP addresses, or anything else from a real
    machine. Build fixtures from the synthetic layers in
    `preflight/tests/fixture_defs.py`. If you copy files from a real host,
    sanitize them first (step 3) and review every file by hand.

## Steps

1. Add an entry to `fixtures()` in `preflight/tests/fixture_defs.py`. Combine
   the existing layers (`UBUNTU_2404`, `system()`, `baremetal()`, `wsl2()`,
   `kvm()`, `nvidia_pci()` ...) and state the expected decision:

    ```python
    fx["ubuntu2404-baremetal-example"] = build(
        UBUNTU_2404, system(), baremetal(), kvm(),
        expected={"outcome": "degraded", "provider": "baremetal", "disabled": ["gpu_inference"]},
    )
    ```

    Expectation keys are listed in `preflight/tests/test_fixtures.py`.

2. Write the fixture tree and the summary snapshot:

    <!-- qa:run env=E0 -->
    ```bash
    python3 preflight/tests/fixture_defs.py --write
    POKUS_UPDATE_SNAPSHOTS=1 .venv/bin/pytest -q preflight/tests/test_fixtures.py
    ```

3. Only if you copied files from a real host into a captured root, sanitize
   them before they enter the repository:

    <!-- qa:skip reason="needs a root captured from a real host" -->
    ```bash
    python3 preflight/tests/sanitize_fixture.py <captured-root> --deny <term>
    ```

    `<captured-root>` is the directory with the copied files; `<term>` is any
    name that identifies your installation (repeat `--deny` per term). The
    sanitizer resets identity files, replaces addresses, UUIDs, e-mail
    addresses and home directory names with placeholders, and redacts the
    terms you give.

4. Review the new files and run the full checks:

    <!-- qa:run env=E0 -->
    ```bash
    git status --short preflight/tests
    make lint test
    ```

## Verify the result

You should now see the new fixture directory and its snapshot in
`git status`, and `make lint test` ends without errors; the privacy scan in
`make lint` reports 0 findings.
