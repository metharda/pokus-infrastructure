-- Managed by pinka (platform potka, contract v2). Local changes will be overwritten.
--
-- Bootstrap of the database of platform potka.
\set ON_ERROR_STOP on

DO $roles$
DECLARE
  r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['potka_orch', 'potka_gw', 'potka_exec', 'potka_inject', 'potka_verify', 'potka_eval', 'potka_migrator', 'potka_reader', 'potka_grafana']::text[] LOOP
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('CREATE ROLE %I LOGIN', r);
      RAISE NOTICE 'pinka-changed: created role %', r;
    ELSIF NOT (SELECT rolcanlogin FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('ALTER ROLE %I LOGIN', r);
      RAISE NOTICE 'pinka-changed: role % may log in again', r;
    END IF;
  END LOOP;
  FOREACH r IN ARRAY ARRAY['potka_controller', 'potka_inference_broker', 'potka_restricted_operator', 'potka_restricted_reader', 'potka_sandbox_operator']::text[] LOOP
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('CREATE ROLE %I NOLOGIN', r);
      RAISE NOTICE 'pinka-changed: created group role %', r;
    END IF;
  END LOOP;
  FOREACH r IN ARRAY ARRAY[]::text[] LOOP
    IF (SELECT rolcanlogin FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('ALTER ROLE %I NOLOGIN', r);
      RAISE NOTICE 'pinka-changed: role % may no longer log in', r;
    END IF;
  END LOOP;
  IF NOT EXISTS (SELECT FROM pg_database WHERE datname = 'potka') THEN
    RAISE NOTICE 'pinka-changed: created database %', 'potka';
  END IF;
END
$roles$;

SELECT format('CREATE DATABASE %I OWNER %I', 'potka', 'potka_migrator')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'potka') \gexec

\connect potka

DO $bootstrap$
DECLARE
  r text;
  m text;
  g text;
BEGIN
  IF (SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = current_database())
      <> 'potka_migrator' THEN
    EXECUTE format('ALTER DATABASE %I OWNER TO %I', current_database(), 'potka_migrator');
    RAISE NOTICE 'pinka-changed: owner of the database';
  END IF;
  IF (SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = 'public')
      <> 'potka_migrator' THEN
    EXECUTE format('ALTER SCHEMA public OWNER TO %I', 'potka_migrator');
    RAISE NOTICE 'pinka-changed: owner of schema public';
  END IF;
  IF NOT EXISTS (SELECT FROM pg_namespace WHERE nspname = 'restricted') THEN
    EXECUTE format('CREATE SCHEMA restricted AUTHORIZATION %I', 'potka_migrator');
    RAISE NOTICE 'pinka-changed: created schema restricted';
  ELSIF (SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = 'restricted')
      <> 'potka_migrator' THEN
    EXECUTE format('ALTER SCHEMA restricted OWNER TO %I', 'potka_migrator');
    RAISE NOTICE 'pinka-changed: owner of schema restricted';
  END IF;

  FOR m, g IN SELECT * FROM (VALUES ('potka_orch', 'potka_controller'), ('potka_gw', 'potka_inference_broker'), ('potka_exec', 'potka_sandbox_operator'), ('potka_inject', 'potka_restricted_operator'), ('potka_verify', 'potka_restricted_operator'), ('potka_eval', 'potka_restricted_reader')) AS t(m, g) LOOP
    IF NOT pg_has_role(m, g, 'MEMBER') THEN
      EXECUTE format('GRANT %I TO %I', g, m);
      RAISE NOTICE 'pinka-changed: % joined %', m, g;
    END IF;
  END LOOP;

  IF has_database_privilege('public', current_database(), 'CONNECT')
      OR has_database_privilege('public', current_database(), 'TEMPORARY') THEN
    EXECUTE format('REVOKE ALL ON DATABASE %I FROM PUBLIC', current_database());
    RAISE NOTICE 'pinka-changed: revoked the database from PUBLIC';
  END IF;
  FOREACH r IN ARRAY ARRAY['potka_orch', 'potka_gw', 'potka_exec', 'potka_inject', 'potka_verify', 'potka_eval', 'potka_migrator', 'potka_reader', 'potka_grafana']::text[] LOOP
    IF NOT has_database_privilege(r, current_database(), 'CONNECT') THEN
      EXECUTE format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), r);
      RAISE NOTICE 'pinka-changed: connect for %', r;
    END IF;
  END LOOP;
  IF EXISTS (
    SELECT FROM pg_namespace n, aclexplode(n.nspacl) a
    WHERE n.nspname = 'restricted'
      AND (a.grantee = 0
           OR pg_get_userbyid(a.grantee) IN ('potka_reader', 'potka_grafana'))
  ) THEN
    EXECUTE format('REVOKE ALL ON SCHEMA restricted FROM PUBLIC, %I, %I',
                   'potka_reader', 'potka_grafana');
    RAISE NOTICE 'pinka-changed: revoked schema restricted';
  END IF;
  IF EXISTS (
    SELECT FROM pg_namespace n, aclexplode(n.nspacl) a
    WHERE n.nspname = 'public' AND a.grantee = 0 AND a.privilege_type = 'CREATE'
  ) THEN
    REVOKE CREATE ON SCHEMA public FROM PUBLIC;
    RAISE NOTICE 'pinka-changed: revoked CREATE on schema public from PUBLIC';
  END IF;
END
$bootstrap$;
