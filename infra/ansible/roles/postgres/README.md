# postgres

PostgreSQL for the lab (BUILD_PROMPT §7.8, `docs/contracts.md#postgresql`),
in the `core` Compose project with `postgres_exporter`:

- image pinned by digest, data in `/srv/pinka/state/postgres` (999:999 0700),
  `initdb --data-checksums` with SCRAM authentication;
- Unix sockets only: `/run/pinka/postgres/` (group `<platform>-db`, the
  active platform's database socket group, whose members are exactly the
  accounts of the services with a database role; `root` without a platform)
  and `/run/pinka/postgres-reader/` (group `pinka`, the researchers' reader
  role), created by `tmpfiles.d` at every boot. Grafana and the exporter use
  the sockets too; PostgreSQL has no TCP listener on the host;
- `pg_hba.conf` admits the superuser, the exporter (`pinka_monitor`, database
  `postgres`) and the active platform's login roles on its own database.
  `pg_hba` cannot tell two socket directories apart: the directory groups
  decide who reaches a socket, and the password files decide the role;
- `config/postgres/bootstrap.sql.j2` creates only the exporter's role with
  `pg_monitor`. The active platform's database is set up by the platform
  role (`tasks_from: platform`) from SQL that `pinka-platform` renders from
  the manifest: the database `<platform>`, one login role per service with a
  database role (`<platform>_<service>`) in its NOLOGIN group role, the
  migrator `<platform>_migrator` owning the database and the schemas
  `public` and `restricted`, the researchers' `<platform>_reader` and
  Grafana's `<platform>_grafana`, `CONNECT` only for these roles, schema
  `restricted` revoked from `PUBLIC`, the reader and Grafana; roles a
  previous release had and this one lacks become `NOLOGIN`. Every change is
  printed as a `pinka-changed:` notice, so a second run reports no change;
- the restricted check after every bootstrap (and, with the hooks, after
  every migration): every privilege on schema `restricted`, its relations,
  columns, functions and types, the default privileges, views outside the
  schema that read it, `SECURITY DEFINER` functions of allowed roles and
  memberships in allowed roles are checked against the roles allowed by the
  profiles (`controller`, `restricted-operator`, `restricted-reader` and the
  migrator); each forbidden one is revoked and reported, and the deployment
  fails;
- passwords: the platform roles' from the vault (`vault_postgres_passwords`)
  into `/etc/pinka/secrets/postgres/<role>` (directory 0711, files 0640
  root:`<account>`, the migrator's readable only by `<platform>-migrate`),
  the reader's into `/etc/pinka/postgres-reader/pgpass` (0640 root:pinka);
  the superuser's and the exporter's are generated on the host once and
  mounted into the containers from `/etc/pinka/secrets/containers/`
  (directory 0700 root, files 0400 owned by the container's UID);
- asserts `fsync`, `synchronous_commit`, `full_page_writes` and
  `data_checksums` are on.

## Variables

See `meta/argument_specs.yml`. Contract paths are constants in
`vars/main.yml`.

## Feature flags

None: the database is part of every lab host.

## Idempotency

Passwords are compared with the stored SCRAM secrets (filter
`pinka_scram_matches`); only a mismatch sets a new secret, computed on the
host (`pinka_scram_verifier`), so the clear-text password reaches neither the
server nor a log. The bootstrap SQL reports a change only when it changed
something (`NOTICE pinka-changed: ...`). A changed configuration file changes
the configuration hash and makes Compose recreate the container. Check mode
renders everything and skips the database steps.

The reader credential is in pgpass format, but libpq ignores password files
that their group may read: researchers copy it to `~/.pgpass` with mode 0600.
