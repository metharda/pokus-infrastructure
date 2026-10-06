# opa

Open Policy Agent for the platform's action decisions (BUILD_PROMPT §7.10,
`docs/contracts.md#policy-engine-opa`, resolution C6), in the `core` Compose
project:

- pinned image, published only on `127.0.0.1:8181`, read-only root
  filesystem, no capabilities;
- `--authorization=basic` with `config/opa/authz.rego` (`package
  system.authz`): decision queries (`GET`/`POST` below `/v1/data/pokus`) and
  health checks are allowed; every policy or data write, the policy listing
  and every other API answer 401;
- the policy directory `/policy` is a symbolic link
  (`/srv/pokus/services/core/policy`) to the deployed platform's
  `current/policy`, or to the deny-all placeholder
  (`config/opa/placeholder/`: `data.pokus.action.decision` denies,
  `data.pokus.placeholder.active` is true for doctor);
- decision logs go to the console, through the journald logging driver
  (`pokus-opa`) to the collector.

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
