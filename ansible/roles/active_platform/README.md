# active_platform

Reads what infra rendered for the active platform
(`/etc/pinka/platform/rendered.json`, written by the platform role when a
deployment is switched on) and sets the fact
`active_platform`: its name, accounts, units, database plan, Incus
certificates, controller account, OPA bundle, dashboards and telemetry
labels; `{}` without an active platform. The file holds no secrets and is
infra-internal, not part of contract v2.

The roles that serve the platform include it instead of naming a platform:

```yaml
- name: Read the active platform
  ansible.builtin.include_role:
    name: active_platform
```

During a deployment the platform role sets `active_platform` from the fresh
rendering first, so the roles after it in the same play see the new
platform; a value already set is kept.

## Variables

See `meta/argument_specs.yml`.

## Idempotency

The role only reads a file and sets a fact; it changes nothing.
