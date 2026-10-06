#!/bin/sh
# Managed by pokus-infra (images/pg-primary/tests/test_pg.sh). Local changes will be overwritten.
#
# Post-build assertions of `pg-primary`, run inside the build instance.
set -eu
fail() {
  echo "test_pg: $*" >&2
  exit 1
}

systemctl is-active --quiet postgresql.service || fail "PostgreSQL is not running"
pg_isready -q || fail "PostgreSQL does not accept connections"
[ "$(runuser -u postgres -- psql -tAc "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = 'appdb'")" = app ] \
  || fail "the database appdb is missing or not owned by app"
echo "test_pg: ok"
