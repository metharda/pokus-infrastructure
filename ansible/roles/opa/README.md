# opa

Open Policy Agent for the platform's action decisions (BUILD_PROMPT §7.10,
`docs/contracts.md#policy-engine-opa`, resolution C6), in the `core` Compose
project:

- pinned image, published only on `127.0.0.1:8181`, read-only root
  filesystem, no capabilities;
- `--authorization=basic` with `authz.rego`, rendered from
  `config/opa/authz.rego.j2` (`package system.authz`): decision queries
  (`GET`/`POST`) at exactly the active platform's decision path from its
  manifest (`/v1/data/<policy.decision_path>`) and
  `/v1/data/pinka/placeholder/active` (owner decision Q7), and health
  checks, are allowed; every policy or data write, the policy listing and
  every other API answer 401;
- infra's placeholder (`config/opa/placeholder/`:
  `data.pinka.placeholder.active` is true for doctor) is always loaded; the
  policy directory `/policy` is a symbolic link
  (`/srv/pinka/services/core/policy`) to the active platform's policy
  bundle in its published release (the manifest's `policy.bundle`), or to
  an empty directory without one. A bundle may not declare packages below
  `system` or `pinka` nor put data there; the deployment refuses it. The
  resolved target is part of the configuration hash, so a new release
  recreates the container;
- decision logs go to the console, through the journald logging driver
  (`pinka-opa`) to the collector.

`ansible/tests/test_opa.py` runs the pinned OPA release against these files:
Rego unit tests and an HTTP server that must reject writes.

## Variables

See `meta/argument_specs.yml`.

## Feature flags

None.

## Idempotency

Files are copied; the link and the recorded policy directory change only
when the platform's policy appears or is redeployed, which changes the
configuration hash and makes Compose recreate the container.
