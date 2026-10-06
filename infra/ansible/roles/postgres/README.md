# postgres

PostgreSQL for the lab (BUILD_PROMPT §7.8, `docs/contracts.md#postgresql`),
in the `core` Compose project with `postgres_exporter`:

- image pinned by digest, data in `/srv/pokus/state/postgres` (999:999 0700),
  `initdb --data-checksums` with SCRAM authentication;
- Unix sockets only for the host: `/run/pokus/postgres/` (group `pokus-svc`,
  the platform's service roles) and `/run/pokus/postgres-reader/` (group
  `pokus`, the researchers' `pokus_reader`), created by `tmpfiles.d` at every
  boot; TCP exists only on the internal Docker networks `pokus-db` (Grafana)
  and `pokus-metrics` (exporter), never on the host;
- `pg_hba.conf` admits the superuser and the contract's roles on the sockets
  and only `pokus_grafana` and `pokus_monitor` over the internal networks.
  `pg_hba` cannot tell two socket directories apart: the directory groups
  decide who reaches a socket, and the password files decide the role;
- idempotent bootstrap SQL (`config/postgres/bootstrap.sql.j2`): roles,
  `pokus_migrator` owning the database and the schemas `public` and `gt`,
  everything on `gt` revoked from `PUBLIC`, `pokus_reader` and
  `pokus_grafana`, `CONNECT` only for the listed roles, `pg_monitor` for the
  exporter. Table grants come from the platform's migrations;
- passwords: the platform roles' from the vault into
  `/etc/pokus/secrets/postgres/<role>` (0640 root:`<account>`, the
  migrator's 0600 root), the reader's into
  `/etc/pokus/postgres-reader/pgpass` (0640 root:pokus); the superuser's and
  the exporter's are generated on the host once and mounted into the
  containers from `/etc/pokus/secrets/containers/` (directory 0700 root, files
  0400 owned by the container's UID);
- asserts `fsync`, `synchronous_commit`, `full_page_writes` and
  `data_checksums` are on.

## Variables

See `meta/argument_specs.yml`. Contract paths are constants in
`vars/main.yml`.

## Feature flags

None: the database is part of every lab host.

## Idempotency

Passwords are compared with the stored SCRAM secrets (filter
`pokus_scram_matches`); only a mismatch sets a new secret, computed on the
host (`pokus_scram_verifier`), so the clear-text password reaches neither the
server nor a log. The bootstrap SQL reports a change only when it changed
something (`NOTICE pokus-changed: ...`). A changed configuration file changes
the configuration hash and makes Compose recreate the container. Check mode
renders everything and skips the database steps.

The reader credential is in pgpass format, but libpq ignores password files
that their group may read: researchers copy it to `~/.pgpass` with mode 0600.
