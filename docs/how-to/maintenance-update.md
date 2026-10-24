---
title: How to run a maintenance update of the locks
type: how-to
audience: [developer]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: As a maintainer, at each maintenance update review the repository's Dependabot alerts, move every alerted package of every lock to a fixed version and look for advisories GitHub has not flagged.
---

# How to run a maintenance update of the locks

Besides the pins of `versions.lock.yml`
([How to update pinned versions](update-versions.md)), the repository has
six more locks. At each maintenance update, review the repository's
Dependabot alerts and move every alerted package to a fixed version in its
lock. Dependabot's own pull requests stay off: their commits would not
carry the maintainer's identity.

| Lock | What it holds | Installed on a lab host | How to update it |
| --- | --- | --- | --- |
| `versions.lock.yml` | every artifact a lab host installs; the Python packages of the Ansible environment | yes | [How to update pinned versions](update-versions.md) |
| `bootstrap/requirements.lock` | the Ansible environment (`/opt/pinka/ansible-venv`) | yes, by bootstrap | `make bootstrap-lock` |
| `installer/requirements.lock` | the installer's environment (`/opt/pinka/installer-venv`) | yes, by bootstrap | `make installer-lock` |
| `ansible/roles/models/files/requirements.lock` | the model download tool's environment (`/opt/pinka/models-venv`) | yes, by the models role | `make models-lock` |
| `tests/reference-platform/uv.lock` | the reference platform's build | in the distribution test lanes only | `uv lock` in `tests/reference-platform/` |
| `uv.lock` | the development tools (linters, tests, documentation) | no | `uv lock --upgrade-package <name>` |
| `package-lock.json` | the documentation linters (Node.js) | no | `npm update <name>`, or an `overrides` entry in `package.json` when a parent pins the old version |

The release package carries every one of these files; a lab host installs
only from the first four.

!!! note "Prerequisites"
    - A development checkout of the repository with `make setup` done.
    - Access to the repository's Dependabot alerts on GitHub, and network
      access to PyPI and the npm registry.

## Update the locks

1. Open the repository's Dependabot alerts on GitHub (Security,
   Dependabot) and note, for each alert, the lock it names and the fixed
   version. GitHub names a lock by the directory it was in when the alert
   opened, so an alert can name a directory that no longer exists.

2. Move every alerted package to a fixed version in its lock, with the
   command of the table. The `make` targets keep every other pin and
   recompute the hashes; to move one package of a shipped lock only, add
   `--upgrade-package <name>` to the target's `uv pip compile` command.
   When a dependency caps the fixed version, an override
   (`override-dependencies` in `pyproject.toml`, `overrides` in
   `package.json`) with a comment naming the advisory is the last resort,
   and the tests show whether the capping package still works.

3. Look for what GitHub has not flagged yet:

    <!-- qa:skip reason="needs network access to the npm registry and PyPI" -->
    ```bash
    cd <checkout>
    npm audit
    ```

    `<checkout>` is the directory where you cloned the repository. For each
    Python lock of the table, also read the advisories PyPI lists for each
    pinned version (`https://pypi.org/pypi/<name>/<version>/json`, the
    `vulnerabilities` list), and move what has one as in step 2.

## Verify the update

The gate passes with the new pins, and the lock check re-verifies every
pin of `versions.lock.yml`:

<!-- qa:skip reason="needs network access and runs the full test suite" -->
```bash
cd <checkout>
make lint test docs
make test-random-order
make test-isolated
make lock-check
npm audit
```

`<checkout>` is the directory where you cloned the repository.
`npm audit` reports `found 0 vulnerabilities`. After a change to a shipped
lock, also build the release package (`make release-package`) and check
that it carries the new pins; CI's `release-package` job builds it too.
