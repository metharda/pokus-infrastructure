"""The platform database: roles, schemas and the restricted-schema check.

Two SQL scripts per platform, run by psql as the superuser:

* ``bootstrap.sql`` (idempotent): the database named after the platform, a
  login role per service except ``untrusted-agent``, a group role per profile
  (``NOLOGIN``) that its services are members of, the migrator (owner of the
  database and of the schemas ``public`` and ``restricted``), the reader and
  Grafana roles; ``CONNECT`` for the login roles only; ``restricted`` revoked
  from ``PUBLIC``, the reader and Grafana. Roles that a previous release of the
  same platform had and this one has not become ``NOLOGIN``. Every change
  raises a notice starting with ``pinka-changed:``.
* ``restricted-check.sql``: after every migration, lists every privilege on
  schema ``restricted`` and on the objects in it (relations, columns,
  functions, types), every default privilege for objects created there,
  views outside the schema that read relations in it, ``SECURITY DEFINER``
  functions of allowed roles outside it, and every role membership through
  which a role outside the allowed set reaches it; revokes all of them and
  prints one line per violation, ``pinka-violation: <what>``. A violation
  fails the deployment, even though it is revoked. ``PUBLIC``'s
  default ``EXECUTE`` on functions in the schema is revoked as a change, not
  a violation.

The allowed set: the migrator (the schema's owner), the group roles of
``controller``, ``restricted-operator`` and ``restricted-reader``, and the
login roles of the services with those profiles. Table grants are the
platform's migrations.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from pinka_platform import layout, names
from pinka_platform.contract import Contract

HEADER = (
    "-- Managed by pinka (platform {platform}, contract v2). Local changes will be overwritten."
)
SCHEMA = "restricted"
CHANGED = "pinka-changed:"
VIOLATION = "pinka-violation:"
# Profiles whose roles may use schema restricted.
RESTRICTED_PROFILES = ("controller", "restricted-operator", "restricted-reader")


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _array(values: Iterable[str]) -> str:
    items = ", ".join(_literal(v) for v in values)
    return f"ARRAY[{items}]::text[]"


def plan(
    manifest: Mapping[str, Any], contract: Contract, previous_roles: Iterable[str] = ()
) -> dict[str, Any]:
    """The database, its roles and the password files of a platform.

    Args:
        manifest: A valid manifest that requires ``postgres``.
        contract: The contract.
        previous_roles: Roles the same platform's active deployment recorded.

    Returns:
        The plan the SQL and the platform role are rendered from.
    """
    p = manifest["name"]
    services = [
        s for s in manifest["services"] if contract.profile(s["profile"]).flag("database_role")
    ]
    profiles = sorted({s["profile"] for s in services})
    logins = [
        {
            "role": names.role(p, s["name"]),
            "account": names.account(p, s["name"]),
            "group_role": names.role(p, s["profile"]),
            "profile": s["profile"],
        }
        for s in services
    ]
    migrator = names.role(p, "migrator")
    reader = names.role(p, "reader")
    grafana = names.role(p, "grafana")
    allowed = sorted(
        {migrator}
        | {names.role(p, prof) for prof in profiles if prof in RESTRICTED_PROFILES}
        | {login["role"] for login in logins if login["profile"] in RESTRICTED_PROFILES}
    )
    current = {login["role"] for login in logins} | {migrator, reader, grafana}
    current |= {names.role(p, prof) for prof in profiles}
    password_files = [
        {"role": login["role"], "owner": "root", "group": login["account"], "mode": "0640"}
        for login in logins
    ] + [{"role": migrator, "owner": "root", "group": names.account(p, "migrate"), "mode": "0640"}]
    return {
        "platform": p,
        "database": p,
        "socket_group": f"{p}-db",
        "logins": logins,
        "group_roles": [names.role(p, prof) for prof in profiles],
        "migrator": migrator,
        "reader": reader,
        "grafana": grafana,
        "restricted_allowed": allowed,
        "stale_roles": sorted(set(previous_roles) - current),
        "password_files": password_files,
        "vault_roles": sorted([login["role"] for login in logins] + [migrator, reader, grafana]),
        "password_dir": layout.POSTGRES_PASSWORD_DIR,
        "reader_credential": layout.POSTGRES_READER_CREDENTIAL,
    }


def bootstrap_sql(db: Mapping[str, Any]) -> str:
    """The idempotent bootstrap of the platform database.

    Args:
        db: The output of :func:`plan`.

    Returns:
        SQL for psql (connected to database ``postgres``).
    """
    p = db["platform"]
    name = db["database"]
    logins = [login["role"] for login in db["logins"]]
    all_logins = [*logins, db["migrator"], db["reader"], db["grafana"]]
    members = ", ".join(
        f"({_literal(login['role'])}, {_literal(login['group_role'])})" for login in db["logins"]
    )
    member_loop = (
        f"""
  FOR m, g IN SELECT * FROM (VALUES {members}) AS t(m, g) LOOP
    IF NOT pg_has_role(m, g, 'MEMBER') THEN
      EXECUTE format('GRANT %I TO %I', g, m);
      RAISE NOTICE '{CHANGED} % joined %', m, g;
    END IF;
  END LOOP;
"""  # noqa: S608 - names validated by the manifest schema, quoted by _literal
        if members
        else ""
    )
    return f"""{HEADER.format(platform=p)}
--
-- Bootstrap of the database of platform {p}.
\\set ON_ERROR_STOP on

DO $roles$
DECLARE
  r text;
BEGIN
  FOREACH r IN ARRAY {_array(all_logins)} LOOP
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('CREATE ROLE %I LOGIN', r);
      RAISE NOTICE '{CHANGED} created role %', r;
    ELSIF NOT (SELECT rolcanlogin FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('ALTER ROLE %I LOGIN', r);
      RAISE NOTICE '{CHANGED} role % may log in again', r;
    END IF;
  END LOOP;
  FOREACH r IN ARRAY {_array(db["group_roles"])} LOOP
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('CREATE ROLE %I NOLOGIN', r);
      RAISE NOTICE '{CHANGED} created group role %', r;
    END IF;
  END LOOP;
  FOREACH r IN ARRAY {_array(db["stale_roles"])} LOOP
    IF (SELECT rolcanlogin FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('ALTER ROLE %I NOLOGIN', r);
      RAISE NOTICE '{CHANGED} role % may no longer log in', r;
    END IF;
  END LOOP;
  IF NOT EXISTS (SELECT FROM pg_database WHERE datname = {_literal(name)}) THEN
    RAISE NOTICE '{CHANGED} created database %', {_literal(name)};
  END IF;
END
$roles$;

SELECT format('CREATE DATABASE %I OWNER %I', {_literal(name)}, {_literal(db["migrator"])})
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = {_literal(name)}) \\gexec

\\connect {name}

DO $bootstrap$
DECLARE
  r text;
  m text;
  g text;
BEGIN
  IF (SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database())
      <> {_literal(db["migrator"])} THEN
    EXECUTE format('ALTER DATABASE %I OWNER TO %I', current_database(), {_literal(db["migrator"])});
    RAISE NOTICE '{CHANGED} owner of the database';
  END IF;
  IF (SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = 'public')
      <> {_literal(db["migrator"])} THEN
    EXECUTE format('ALTER SCHEMA public OWNER TO %I', {_literal(db["migrator"])});
    RAISE NOTICE '{CHANGED} owner of schema public';
  END IF;
  IF NOT EXISTS (SELECT FROM pg_namespace WHERE nspname = '{SCHEMA}') THEN
    EXECUTE format('CREATE SCHEMA {SCHEMA} AUTHORIZATION %I', {_literal(db["migrator"])});
    RAISE NOTICE '{CHANGED} created schema {SCHEMA}';
  ELSIF (SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = '{SCHEMA}')
      <> {_literal(db["migrator"])} THEN
    EXECUTE format('ALTER SCHEMA {SCHEMA} OWNER TO %I', {_literal(db["migrator"])});
    RAISE NOTICE '{CHANGED} owner of schema {SCHEMA}';
  END IF;
{member_loop}
  IF has_database_privilege('public', current_database(), 'CONNECT')
      OR has_database_privilege('public', current_database(), 'TEMPORARY') THEN
    EXECUTE format('REVOKE ALL ON DATABASE %I FROM PUBLIC', current_database());
    RAISE NOTICE '{CHANGED} revoked the database from PUBLIC';
  END IF;
  FOREACH r IN ARRAY {_array(all_logins)} LOOP
    IF NOT has_database_privilege(r, current_database(), 'CONNECT') THEN
      EXECUTE format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), r);
      RAISE NOTICE '{CHANGED} connect for %', r;
    END IF;
  END LOOP;
  IF EXISTS (
    SELECT FROM pg_namespace n, aclexplode(n.nspacl) a
    WHERE n.nspname = '{SCHEMA}'
      AND (a.grantee = 0
           OR pg_get_userbyid(a.grantee) IN ({_literal(db["reader"])}, {_literal(db["grafana"])}))
  ) THEN
    EXECUTE format('REVOKE ALL ON SCHEMA {SCHEMA} FROM PUBLIC, %I, %I',
                   {_literal(db["reader"])}, {_literal(db["grafana"])});
    RAISE NOTICE '{CHANGED} revoked schema {SCHEMA}';
  END IF;
  IF EXISTS (
    SELECT FROM pg_namespace n, aclexplode(n.nspacl) a
    WHERE n.nspname = 'public' AND a.grantee = 0 AND a.privilege_type = 'CREATE'
  ) THEN
    REVOKE CREATE ON SCHEMA public FROM PUBLIC;
    RAISE NOTICE '{CHANGED} revoked CREATE on schema public from PUBLIC';
  END IF;
END
$bootstrap$;
"""  # noqa: S608 - names validated by the manifest schema, quoted by _literal


def restricted_check_sql(db: Mapping[str, Any]) -> str:
    """List, revoke and report every forbidden access to schema ``restricted``.

    Args:
        db: The output of :func:`plan`.

    Returns:
        SQL for psql (connected to the platform database). It prints one
        ``pinka-violation:`` notice per violation; the caller fails the
        deployment when there is any.
    """
    p = db["platform"]
    allowed = _array(db["restricted_allowed"])
    return f"""{HEADER.format(platform=p)}
--
-- The restricted-schema check of platform {p}: run
-- after every migration. Each finding is revoked and reported as a violation.
\\set ON_ERROR_STOP on

DO $check$
DECLARE
  allowed text[] := {allowed};
  v record;
BEGIN
  -- Privileges on the schema itself.
  FOR v IN
    SELECT DISTINCT CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END AS who
    FROM pg_namespace n, aclexplode(n.nspacl) a
    WHERE n.nspname = '{SCHEMA}' AND a.grantee <> n.nspowner
      AND (a.grantee = 0 OR NOT pg_get_userbyid(a.grantee) = ANY (allowed))
  LOOP
    RAISE NOTICE '{VIOLATION} % has privileges on schema {SCHEMA}', v.who;
    IF v.who = 'PUBLIC' THEN
      REVOKE ALL ON SCHEMA {SCHEMA} FROM PUBLIC;
    ELSE
      EXECUTE format('REVOKE ALL ON SCHEMA {SCHEMA} FROM %I', v.who);
    END IF;
  END LOOP;

  -- Privileges on tables, views, sequences and other relations in the schema.
  FOR v IN
    SELECT DISTINCT c.oid::regclass::text AS obj,
           CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END AS who,
           c.relkind
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace, aclexplode(c.relacl) a
    WHERE n.nspname = '{SCHEMA}' AND a.grantee <> c.relowner
      AND (a.grantee = 0 OR NOT pg_get_userbyid(a.grantee) = ANY (allowed))
  LOOP
    RAISE NOTICE '{VIOLATION} % has privileges on %', v.who, v.obj;
    EXECUTE format('REVOKE ALL ON %s %s FROM %s',
                   CASE WHEN v.relkind = 'S' THEN 'SEQUENCE' ELSE 'TABLE' END, v.obj,
                   CASE WHEN v.who = 'PUBLIC' THEN 'PUBLIC' ELSE quote_ident(v.who) END);
  END LOOP;

  -- Privileges on columns of relations in the schema.
  FOR v IN
    SELECT DISTINCT c.oid::regclass::text AS obj, quote_ident(t.attname) AS col,
           CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END AS who
    FROM pg_attribute t JOIN pg_class c ON c.oid = t.attrelid
         JOIN pg_namespace n ON n.oid = c.relnamespace, aclexplode(t.attacl) a
    WHERE n.nspname = '{SCHEMA}' AND t.attacl IS NOT NULL
      AND (a.grantee = 0 OR NOT pg_get_userbyid(a.grantee) = ANY (allowed))
  LOOP
    RAISE NOTICE '{VIOLATION} % has privileges on column % of %', v.who, v.col, v.obj;
    IF v.who = 'PUBLIC' THEN
      EXECUTE format('REVOKE ALL (%s) ON %s FROM PUBLIC', v.col, v.obj);
    ELSE
      EXECUTE format('REVOKE ALL (%s) ON %s FROM %I', v.col, v.obj, v.who);
    END IF;
  END LOOP;

  -- Functions and procedures in the schema. PUBLIC may execute new
  -- functions by default; without USAGE on the schema nobody can call them,
  -- so that default is revoked without counting as a violation.
  FOR v IN
    SELECT DISTINCT f.oid::regprocedure::text AS obj,
           CASE WHEN a.grantee = 0 THEN 'PUBLIC'
                ELSE quote_ident(pg_get_userbyid(a.grantee)) END AS who,
           CASE WHEN f.prokind = 'p' THEN 'PROCEDURE' ELSE 'FUNCTION' END AS kind
    FROM pg_proc f JOIN pg_namespace n ON n.oid = f.pronamespace,
         aclexplode(coalesce(f.proacl, acldefault('f', f.proowner))) a
    WHERE n.nspname = '{SCHEMA}' AND a.grantee <> f.proowner
      AND (a.grantee = 0 OR NOT pg_get_userbyid(a.grantee) = ANY (allowed))
  LOOP
    IF v.who = 'PUBLIC' THEN
      RAISE NOTICE '{CHANGED} revoked the default EXECUTE of PUBLIC on %', v.obj;
    ELSE
      RAISE NOTICE '{VIOLATION} % may execute %', v.who, v.obj;
    END IF;
    EXECUTE format('REVOKE ALL ON %s %s FROM %s', v.kind, v.obj, v.who);
  END LOOP;

  -- Privileges on types and domains in the schema (PUBLIC may use new types
  -- by default; that reveals no data, so only explicit grants are findings).
  FOR v IN
    SELECT DISTINCT format_type(t.oid, NULL) AS obj,
           CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END AS who
    FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace, aclexplode(t.typacl) a
    WHERE n.nspname = '{SCHEMA}' AND t.typacl IS NOT NULL AND a.grantee <> t.typowner
      AND a.grantee <> 0 AND NOT pg_get_userbyid(a.grantee) = ANY (allowed)
  LOOP
    RAISE NOTICE '{VIOLATION} % has privileges on type %', v.who, v.obj;
    EXECUTE format('REVOKE ALL ON TYPE %s FROM %I', v.obj, v.who);
  END LOOP;

  -- Default privileges for objects created in the schema later: per schema,
  -- and global ones, which apply in every schema.
  FOR v IN
    SELECT DISTINCT pg_get_userbyid(d.defaclrole) AS creator,
           CASE WHEN a.grantee = 0 THEN 'PUBLIC'
                ELSE quote_ident(pg_get_userbyid(a.grantee)) END AS who,
           CASE d.defaclobjtype WHEN 'r' THEN 'TABLES' WHEN 'S' THEN 'SEQUENCES'
             WHEN 'f' THEN 'FUNCTIONS' ELSE 'TYPES' END AS kind,
           d.defaclnamespace = 0 AS global
    FROM pg_default_acl d LEFT JOIN pg_namespace n ON n.oid = d.defaclnamespace,
         aclexplode(d.defaclacl) a
    WHERE (n.nspname = '{SCHEMA}' OR d.defaclnamespace = 0)
      AND d.defaclobjtype IN ('r', 'S', 'f', 'T')
      AND (a.grantee = 0 OR NOT pg_get_userbyid(a.grantee) = ANY (allowed))
      AND a.grantee <> d.defaclrole
      AND NOT (a.grantee = 0 AND d.defaclobjtype IN ('f', 'T'))
  LOOP
    RAISE NOTICE '{VIOLATION} % gets default privileges on % created by %',
      v.who, v.kind, v.creator;
    IF v.global THEN
      EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %I REVOKE ALL ON %s FROM %s',
                     v.creator, v.kind, v.who);
    ELSE
      EXECUTE format(
        'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA {SCHEMA} REVOKE ALL ON %s FROM %s',
                     v.creator, v.kind, v.who);
    END IF;
  END LOOP;

  -- Views outside the schema that read relations in it: their owner's
  -- privileges apply, so a grant on such a view reaches restricted data.
  FOR v IN
    SELECT DISTINCT w.oid::regclass::text AS obj,
           CASE WHEN a.grantee = 0 THEN 'PUBLIC'
                ELSE quote_ident(pg_get_userbyid(a.grantee)) END AS who
    FROM pg_depend d JOIN pg_rewrite rw ON rw.oid = d.objid
         JOIN pg_class w ON w.oid = rw.ev_class JOIN pg_namespace wn ON wn.oid = w.relnamespace
         JOIN pg_class t ON t.oid = d.refobjid JOIN pg_namespace tn ON tn.oid = t.relnamespace,
         aclexplode(w.relacl) a
    WHERE d.classid = 'pg_rewrite'::regclass AND d.refclassid = 'pg_class'::regclass
      AND tn.nspname = '{SCHEMA}' AND wn.nspname <> '{SCHEMA}' AND w.oid <> t.oid
      AND a.grantee <> w.relowner
      AND (a.grantee = 0 OR NOT pg_get_userbyid(a.grantee) = ANY (allowed))
  LOOP
    RAISE NOTICE '{VIOLATION} % has privileges on %, which reads schema {SCHEMA}', v.who, v.obj;
    EXECUTE format('REVOKE ALL ON %s FROM %s', v.obj, v.who);
  END LOOP;

  -- SECURITY DEFINER functions of an allowed role outside the schema: they
  -- run with their owner's privileges, so whoever may execute them may read
  -- restricted data through them.
  FOR v IN
    SELECT DISTINCT f.oid::regprocedure::text AS obj,
           CASE WHEN a.grantee = 0 THEN 'PUBLIC'
                ELSE quote_ident(pg_get_userbyid(a.grantee)) END AS who,
           CASE WHEN f.prokind = 'p' THEN 'PROCEDURE' ELSE 'FUNCTION' END AS kind
    FROM pg_proc f JOIN pg_namespace n ON n.oid = f.pronamespace,
         aclexplode(coalesce(f.proacl, acldefault('f', f.proowner))) a
    WHERE f.prosecdef AND n.nspname <> '{SCHEMA}'
      AND n.nspname NOT IN ('pg_catalog', 'information_schema')
      AND pg_get_userbyid(f.proowner) = ANY (allowed) AND a.grantee <> f.proowner
      AND (a.grantee = 0 OR NOT pg_get_userbyid(a.grantee) = ANY (allowed))
  LOOP
    RAISE NOTICE '{VIOLATION} % may execute %, a SECURITY DEFINER function of an allowed role',
      v.who, v.obj;
    EXECUTE format('REVOKE ALL ON %s %s FROM %s', v.kind, v.obj, v.who);
  END LOOP;

  -- Memberships: a role outside the allowed set that is a member of an
  -- allowed role reaches whatever that role reaches.
  FOR v IN
    SELECT DISTINCT r.rolname AS role, m.rolname AS member
    FROM pg_auth_members am JOIN pg_roles r ON r.oid = am.roleid
         JOIN pg_roles m ON m.oid = am.member
    WHERE r.rolname = ANY (allowed) AND NOT m.rolname = ANY (allowed)
      AND NOT m.rolsuper
  LOOP
    RAISE NOTICE '{VIOLATION} % is a member of %', v.member, v.role;
    EXECUTE format('REVOKE %I FROM %I CASCADE', v.role, v.member);
  END LOOP;
END
$check$;
"""  # noqa: S608 - names validated by the manifest schema, quoted by _literal


def reader_pgpass(db: Mapping[str, Any], password: str) -> str:
    """The researchers' reader credential in pgpass format.

    Args:
        db: The output of :func:`plan`.
        password: The reader's password.

    Returns:
        The file's text.
    """
    escaped = password.replace("\\", "\\\\").replace(":", "\\:")
    return (
        "# Managed by pinka (the reader credential of the platform database).\n"
        "# libpq ignores a group-readable password file: copy it to ~/.pgpass with mode 0600.\n"
        f"{layout.POSTGRES_READER_SOCKET_DIR}:5432:{db['database']}:{db['reader']}:{escaped}\n"
    )
