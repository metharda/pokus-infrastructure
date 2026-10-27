# layout

Creates the research space (`pinka_root`, default `/srv/pinka`) and the lab's
host paths from one data structure in
`group_vars/all/main.yml`: `pinka_layout` (relative to `pinka_root`),
`pinka_system_layout` (absolute paths) and `pinka_layout_user_mode` for
`users/<name>/`. The same data renders the layout table of
`docs/architecture.md`, so the documentation cannot drift from what the role
creates.

Default ACLs (`default_acl`) give the `pinka` group read and write access to
new files in `datasets/` and `shared/`. `state/<service>/` directories are
created by the service roles with the container's user ID.

## Variables

See `meta/argument_specs.yml`.

## Feature flags

None.

## Idempotency

Declarative file and ACL modules.
