-- Managed by pinka (platform potka, contract v2). Local changes will be overwritten.
--
-- The restricted-schema check of platform potka: run
-- after every migration. Each finding is revoked and reported as a violation.
\set ON_ERROR_STOP on

DO $check$
DECLARE
  allowed text[] := ARRAY['potka_controller', 'potka_eval', 'potka_inject', 'potka_migrator', 'potka_orch', 'potka_restricted_operator', 'potka_restricted_reader', 'potka_verify']::text[];
  v record;
BEGIN
  -- Privileges on the schema itself.
  FOR v IN
    SELECT DISTINCT CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END AS who
    FROM pg_namespace n, aclexplode(n.nspacl) a
    WHERE n.nspname = 'restricted' AND a.grantee <> n.nspowner
      AND (a.grantee = 0 OR NOT pg_get_userbyid(a.grantee) = ANY (allowed))
  LOOP
    RAISE NOTICE 'pinka-violation: % has privileges on schema restricted', v.who;
    IF v.who = 'PUBLIC' THEN
      REVOKE ALL ON SCHEMA restricted FROM PUBLIC;
    ELSE
      EXECUTE format('REVOKE ALL ON SCHEMA restricted FROM %I', v.who);
    END IF;
  END LOOP;

  -- Privileges on tables, views, sequences and other relations in the schema.
  FOR v IN
    SELECT DISTINCT c.oid::regclass::text AS obj,
           CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END AS who,
           c.relkind
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace, aclexplode(c.relacl) a
    WHERE n.nspname = 'restricted' AND a.grantee <> c.relowner
      AND (a.grantee = 0 OR NOT pg_get_userbyid(a.grantee) = ANY (allowed))
  LOOP
    RAISE NOTICE 'pinka-violation: % has privileges on %', v.who, v.obj;
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
    WHERE n.nspname = 'restricted' AND t.attacl IS NOT NULL
      AND (a.grantee = 0 OR NOT pg_get_userbyid(a.grantee) = ANY (allowed))
  LOOP
    RAISE NOTICE 'pinka-violation: % has privileges on column % of %', v.who, v.col, v.obj;
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
    WHERE n.nspname = 'restricted' AND a.grantee <> f.proowner
      AND (a.grantee = 0 OR NOT pg_get_userbyid(a.grantee) = ANY (allowed))
  LOOP
    IF v.who = 'PUBLIC' THEN
      RAISE NOTICE 'pinka-changed: revoked the default EXECUTE of PUBLIC on %', v.obj;
    ELSE
      RAISE NOTICE 'pinka-violation: % may execute %', v.who, v.obj;
    END IF;
    EXECUTE format('REVOKE ALL ON %s %s FROM %s', v.kind, v.obj, v.who);
  END LOOP;

  -- Privileges on types and domains in the schema (PUBLIC may use new types
  -- by default; that reveals no data, so only explicit grants are findings).
  FOR v IN
    SELECT DISTINCT format_type(t.oid, NULL) AS obj,
           CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END AS who
    FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace, aclexplode(t.typacl) a
    WHERE n.nspname = 'restricted' AND t.typacl IS NOT NULL AND a.grantee <> t.typowner
      AND a.grantee <> 0 AND NOT pg_get_userbyid(a.grantee) = ANY (allowed)
  LOOP
    RAISE NOTICE 'pinka-violation: % has privileges on type %', v.who, v.obj;
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
    WHERE (n.nspname = 'restricted' OR d.defaclnamespace = 0)
      AND d.defaclobjtype IN ('r', 'S', 'f', 'T')
      AND (a.grantee = 0 OR NOT pg_get_userbyid(a.grantee) = ANY (allowed))
      AND a.grantee <> d.defaclrole
      AND NOT (a.grantee = 0 AND d.defaclobjtype IN ('f', 'T'))
  LOOP
    RAISE NOTICE 'pinka-violation: % gets default privileges on % created by %',
      v.who, v.kind, v.creator;
    IF v.global THEN
      EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %I REVOKE ALL ON %s FROM %s',
                     v.creator, v.kind, v.who);
    ELSE
      EXECUTE format(
        'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA restricted REVOKE ALL ON %s FROM %s',
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
      AND tn.nspname = 'restricted' AND wn.nspname <> 'restricted' AND w.oid <> t.oid
      AND a.grantee <> w.relowner
      AND (a.grantee = 0 OR NOT pg_get_userbyid(a.grantee) = ANY (allowed))
  LOOP
    RAISE NOTICE 'pinka-violation: % has privileges on %, which reads schema restricted', v.who, v.obj;
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
    WHERE f.prosecdef AND n.nspname <> 'restricted'
      AND n.nspname NOT IN ('pg_catalog', 'information_schema')
      AND pg_get_userbyid(f.proowner) = ANY (allowed) AND a.grantee <> f.proowner
      AND (a.grantee = 0 OR NOT pg_get_userbyid(a.grantee) = ANY (allowed))
  LOOP
    RAISE NOTICE 'pinka-violation: % may execute %, a SECURITY DEFINER function of an allowed role',
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
    RAISE NOTICE 'pinka-violation: % is a member of %', v.member, v.role;
    EXECUTE format('REVOKE %I FROM %I CASCADE', v.role, v.member);
  END LOOP;
END
$check$;
