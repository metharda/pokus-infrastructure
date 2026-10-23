---
title: How to add a distribution family
type: how-to
audience: [developer]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: The checklist for supporting a new Linux distribution family in pokus-infrastructure.
---

# How to add a distribution family

A family is a group of distributions that share a package manager, a way to
hold package versions and service names. This checklist adds one. A new
release or derivative of an existing family needs only the support-matrix
step and, for a fixed release, version pins.

!!! note "Prerequisites"
    - A development checkout with `make setup` done.
    - A VM image of the distribution for testing.
    - The distribution must use systemd and glibc; others stay unsupported.

## Add the family

1. **Support matrix.** Add the family under `families` in
   `preflight/support-matrix.yml` with its package manager, and the
   distribution rows under `os` with tier `experimental` and the vendor's end
   of support. Teach preflight's package manager list about a new package
   manager if needed (`preflight/pinka_preflight/`), then regenerate:

    <!-- qa:run env=E0 -->
    ```bash
    cd <checkout>
    make generate
    ```

    `<checkout>` is the directory of your clone.

2. **Fixtures.** Add a preflight fixture for the new family in
   `preflight/tests/fixture_defs.py`; see
   [How to add a preflight fixture](../how-to/add-preflight-fixture.md).

3. **Platform facts.** Add `ansible/roles/platform_facts/vars/<family>.yml`
   with `service_names`, `initramfs_tool`, `version_hold_method` and
   `codenames`, and add the family to `FAMILY_DEFAULT_GROUPS` and, if its
   distributions need their own repositories, to `SOURCE_GROUPS` in
   `ansible/filter_plugins/pinka.py`.

4. **Package abstraction.** Add `install_<manager>.yml` and, for a new hold
   method, `hold_<method>.yml` to `ansible/roles/pkg/tasks/`, and the package
   manager to `pinka_package_specs`.

5. **Family vars and tasks of every role.** Every role that has
   `vars/<family>.yml` or `tasks/<name>_<family>.yml` files needs one for the
   new family: today `base`, `mac`, `provider_vm` and
   `provider_baremetal` (`reboot_<family>.yml`), plus `base`'s
   `updates_<family>.yml`.

6. **Component sources.** Add rows for the new source group to
   `ansible/component-sources.yml` where the default does not fit, with
   repository definitions and keys in `versions.lock.yml`. Mark anything you
   could not verify with `verify: "TODO(verify): ..."`.

7. **Version pins.** For fixed releases add
   `packages.<component>.<release key>` entries to `versions.lock.yml`, then
   run `make generate` again.

8. **Distribution tests.** Add a target per release to
   `tests/distros/targets.yml` (image alias, `os` fields, firewall backend)
   and run it with `make test-distros`; when it passes, move the rows to
   `tested` in the matrix and commit `tests/distros/results.yml`. Add a
   pinned base image of the family to `containers.ci_images` in
   `versions.lock.yml` and an `image:` entry to the `check-mode` job of
   `.github/workflows/ci.yml` and the `check-mode-extended` job of
   `.github/workflows/check-mode-extended.yml`, so the CI fast lane checks
   the family; see
   [How to test the distributions](../how-to/test-distributions.md).

9. **Documentation.** Write `docs/distros/<family>.md` following the existing
   family pages and add it to `mkdocs.yml`.

## Verify the family

Run the checks; the role tests fail if a role lacks vars or task files for
any family, and the portability check fails if a role reads distribution
facts directly:

<!-- qa:run env=E0 -->
```bash
make lint test docs
```

All three targets end without errors.
