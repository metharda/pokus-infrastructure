"""The PostgreSQL configuration and the platform database against a real server.

A throwaway cluster in a temporary directory (Unix socket only, no TCP) runs
the rendered postgresql.conf and pg_hba.conf, infra's bootstrap SQL, the
platform database rendered from a manifest fixture (database, login and group
roles, schemas, revokes; BUILD_PROMPT §7.8, §12.5), the password handling of
the postgres role, and the restricted-schema check: every kind of forbidden
access a migration could grant is reported and revoked (§12.4). It uses the
PostgreSQL server installed on the machine (the CI runners ship one; there the
tests must run); the pinned image runs a newer major, and the SQL uses nothing
that differs between them.
"""

from __future__ import annotations

import json
import os
import pwd
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

import pinka
from ansible_testlib import (
    GROUP_VARS,
    INFRA,
    ROLES,
    ansible_vars,
    evaluate,
    fixture_profile,
    render_file,
)
from pinka_platform import contract, database

CONFIG = INFRA / "config" / "postgres"
FIXTURE = INFRA / "tools" / "platform" / "tests" / "fixtures" / "manifests" / "allprof.yaml"
SUPERUSER_PASSWORD = "superuser-test-password"  # noqa: S105 - throwaway test cluster
MONITOR_PASSWORD = "monitor-test-password"  # noqa: S105 - throwaway test cluster
ROTATED_PASSWORD = "rotated-test-password"  # noqa: S105 - throwaway test cluster
WRONG_PASSWORD = "wrong-test-password"  # noqa: S105 - throwaway test cluster
INTRUDER = (
    "DO $$ BEGIN CREATE ROLE intruder LOGIN PASSWORD 'x';"
    " EXCEPTION WHEN duplicate_object THEN NULL; END $$;"
)
MANIFEST = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
PLAN = database.plan(MANIFEST, contract.load(), ["allprof_gone"])
PLAN["bootstrap_sql"] = database.bootstrap_sql(PLAN)
PLAN["restricted_check_sql"] = database.restricted_check_sql(PLAN)
DB = PLAN["database"]
VAULT = {role: f"{role}-test-password" for role in PLAN["vault_roles"]}


def server_bindir() -> Path | None:
    candidates = sorted(
        Path("/usr/lib/postgresql").glob("*/bin"), key=lambda p: int(p.parent.name), reverse=True
    )
    if shutil.which("pg_ctl"):
        candidates.insert(0, Path(str(shutil.which("pg_ctl"))).parent)
    return next((c for c in candidates if (c / "initdb").is_file()), None)


BINDIR = server_bindir()


@pytest.fixture(scope="module", autouse=True)
def require_server() -> None:
    """Skip without PostgreSQL locally; in CI (CI=true) it must be there."""
    if BINDIR is not None:
        return
    if os.environ.get("CI", "").lower() == "true":
        pytest.fail("PostgreSQL server binaries are missing in CI; the database tests must run")
    pytest.skip("needs the PostgreSQL server binaries; allowed to skip only outside CI")


@dataclass
class Cluster:
    """A running throwaway cluster."""

    bindir: Path
    root: Path
    socket_dir: Path
    prefix: list[str]

    def psql(
        self,
        sql: str,
        user: str = "postgres",
        password: str = SUPERUSER_PASSWORD,
        db: str = DB,
    ) -> subprocess.CompletedProcess[str]:
        env = {**os.environ, "PGPASSWORD": password, "PGCONNECT_TIMEOUT": "10"}
        env.pop("PGPASSFILE", None)
        return subprocess.run(
            [
                str(self.bindir / "psql"),
                "--no-psqlrc",
                "--set=ON_ERROR_STOP=1",
                "--tuples-only",
                "--no-align",
                f"--host={self.socket_dir}",
                f"--username={user}",
                f"--dbname={db}",
            ],
            input=sql,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )

    def ok(self, sql: str, **kwargs: Any) -> str:
        result = self.psql(sql, **kwargs)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()


def postgres_vars(socket_dir: Path) -> dict[str, Any]:
    return ansible_vars(
        GROUP_VARS,
        ROLES / "postgres" / "defaults" / "main.yml",
        ROLES / "postgres" / "vars" / "main.yml",
        host_profile=fixture_profile("ubuntu2404-baremetal-nvidia-driver-loaded"),
        active_platform={"database": PLAN},
        vault_postgres_passwords=VAULT,
        postgres_service_socket_dir=str(socket_dir / "svc"),
        postgres_reader_socket_dir=str(socket_dir / "reader"),
        postgres_shared_buffers="32MB",
    )


@pytest.fixture(scope="module")
def cluster() -> Iterator[Cluster]:
    assert BINDIR is not None
    root = Path(tempfile.mkdtemp(prefix="pinka-pg-"))
    prefix: list[str] = []
    if os.geteuid() == 0:  # initdb refuses to run as root
        nobody = pwd.getpwnam("nobody")
        root.chmod(0o755)
        prefix = ["runuser", "-u", "nobody", "--"]
    sockets = root / "sockets"
    for path in (sockets, sockets / "svc", sockets / "reader", root / "conf"):
        path.mkdir(parents=True)
    variables = postgres_vars(sockets)
    for name in ("postgresql.conf", "pg_hba.conf", "bootstrap.sql"):
        (root / "conf" / name).write_text(render_file(CONFIG / f"{name}.j2", variables))
    (root / "pwfile").write_text(SUPERUSER_PASSWORD + "\n")
    if prefix:
        for path in [root, *root.rglob("*")]:
            os.chown(path, nobody.pw_uid, nobody.pw_gid)
    data = root / "data"
    # The arguments of the image's entrypoint for POSTGRES_INITDB_ARGS.
    initdb = [
        str(BINDIR / "initdb"),
        f"--pgdata={data}",
        "--username=postgres",
        f"--pwfile={root / 'pwfile'}",
        "--data-checksums",
        "--auth-local=scram-sha-256",
        "--auth-host=scram-sha-256",
        "--encoding=UTF8",
        "--no-locale",
    ]
    subprocess.run([*prefix, *initdb], check=True, capture_output=True)
    options = " ".join(
        [
            f"-c config_file={root / 'conf' / 'postgresql.conf'}",
            f"-c hba_file={root / 'conf' / 'pg_hba.conf'}",
            # Container paths of the rendered file, replaced by writable ones.
            f"-c unix_socket_directories={sockets},{sockets / 'svc'},{sockets / 'reader'}",
            "-c listen_addresses=''",
        ]
    )
    start = [str(BINDIR / "pg_ctl"), f"--pgdata={data}", "--wait", f"--options={options}"]
    log = f"--log={root / 'server.log'}"
    subprocess.run([*prefix, *start, log, "start"], check=True, capture_output=True)
    try:
        yield Cluster(BINDIR, root, sockets, prefix)
    finally:
        subprocess.run(
            [*prefix, str(BINDIR / "pg_ctl"), f"--pgdata={data}", "--mode=immediate", "stop"],
            check=False,
            capture_output=True,
        )
        shutil.rmtree(root, ignore_errors=True)


def changes(result: subprocess.CompletedProcess[str], marker: str = "pinka-changed: ") -> list[str]:
    assert result.returncode == 0, result.stderr
    return [line.split(marker, 1)[1] for line in result.stderr.splitlines() if marker in line]


def infra_bootstrap(cluster: Cluster) -> list[str]:
    sql = (cluster.root / "conf" / "bootstrap.sql").read_text()
    return changes(cluster.psql(sql, db="postgres"))


def platform_bootstrap(cluster: Cluster) -> list[str]:
    return changes(cluster.psql(PLAN["bootstrap_sql"], db="postgres"))


def restricted_check(cluster: Cluster) -> list[str]:
    return changes(cluster.psql(PLAN["restricted_check_sql"]), "pinka-violation: ")


def set_passwords(cluster: Cluster, passwords: dict[str, str]) -> list[str]:
    """What the postgres role does: compare stored secrets, set mismatching ones."""
    query = evaluate("postgres_secrets_query", postgres_vars(cluster.socket_dir))
    stored = json.loads(cluster.ok(query, db="postgres"))
    changed = [
        role for role, pw in passwords.items() if not pinka.scram_matches(pw, stored.get(role))
    ]
    for role in changed:
        cluster.ok(
            f"ALTER ROLE \"{role}\" PASSWORD '{pinka.scram_verifier(passwords[role])}';",
            db="postgres",
        )
    return changed


def login(role: str) -> dict[str, str]:
    return {"user": role, "password": VAULT[role]}


@pytest.fixture(scope="module")
def bootstrapped(cluster: Cluster) -> Cluster:
    assert "created role pinka_monitor" in infra_bootstrap(cluster)
    cluster.ok("CREATE ROLE allprof_gone LOGIN;", db="postgres")  # a previous release's role
    first = platform_bootstrap(cluster)
    assert "created database allprof" in first
    assert "created role allprof_ctl" in first
    assert "created group role allprof_controller" in first
    assert "created schema restricted" in first
    assert "allprof_ctl joined allprof_controller" in first
    assert "role allprof_gone may no longer log in" in first
    passwords = {**VAULT, "pinka_monitor": MONITOR_PASSWORD}
    assert sorted(set_passwords(cluster, passwords)) == sorted(passwords)
    return cluster


def test_bootstrap_is_idempotent(bootstrapped: Cluster) -> None:
    assert infra_bootstrap(bootstrapped) == []
    assert platform_bootstrap(bootstrapped) == []
    assert set_passwords(bootstrapped, {**VAULT, "pinka_monitor": MONITOR_PASSWORD}) == []


def test_durability_settings(bootstrapped: Cluster) -> None:
    query = evaluate("postgres_durability_query", postgres_vars(bootstrapped.socket_dir))
    settings = json.loads(bootstrapped.ok(query))
    assert settings == {
        "fsync": "on",
        "synchronous_commit": "on",
        "full_page_writes": "on",
        "data_checksums": "on",
    }


def test_owners_roles_and_monitor(bootstrapped: Cluster) -> None:
    owners = bootstrapped.ok(
        "SELECT string_agg(nspname || '=' || pg_get_userbyid(nspowner), ',' ORDER BY nspname)"
        " FROM pg_namespace WHERE nspname IN ('public', 'restricted');"
        " SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = 'allprof';"
        " SELECT pg_has_role('pinka_monitor', 'pg_monitor', 'MEMBER');"
        " SELECT rolcanlogin FROM pg_roles WHERE rolname = 'allprof_controller';"
        " SELECT rolcanlogin FROM pg_roles WHERE rolname = 'allprof_gone';"
    )
    assert owners.splitlines() == [
        "public=allprof_migrator,restricted=allprof_migrator",
        "allprof_migrator",
        "t",
        "f",
        "f",
    ]


def test_the_agent_has_no_role(bootstrapped: Cluster) -> None:
    assert bootstrapped.ok("SELECT count(*) FROM pg_roles WHERE rolname = 'allprof_agt';") == "0"


def test_service_roles_log_in_over_the_sockets(bootstrapped: Cluster) -> None:
    for role in ("allprof_migrator", "allprof_ctl", "allprof_obs", "allprof_reader"):
        assert bootstrapped.ok("SELECT current_user;", **login(role)) == role
    wrong = bootstrapped.psql("SELECT 1;", user="allprof_ctl", password=WRONG_PASSWORD)
    assert "password authentication failed" in wrong.stderr


def test_grafana_and_monitor_use_the_sockets(bootstrapped: Cluster) -> None:
    assert bootstrapped.ok("SELECT current_user;", **login("allprof_grafana")) == "allprof_grafana"
    monitor = {"user": "pinka_monitor", "password": MONITOR_PASSWORD, "db": "postgres"}
    assert bootstrapped.ok("SELECT count(*) > 0 FROM pg_stat_activity;", **monitor) == "t"


def test_other_roles_and_databases_are_rejected(bootstrapped: Cluster) -> None:
    bootstrapped.ok(INTRUDER, db="postgres")
    result = bootstrapped.psql("SELECT 1;", user="intruder", password="x")  # noqa: S106
    assert result.returncode != 0
    assert "no pg_hba.conf entry" in result.stderr
    other_db = bootstrapped.psql("SELECT 1;", db="postgres", **login("allprof_ctl"))
    assert "no pg_hba.conf entry" in other_db.stderr
    monitor_on_platform = bootstrapped.psql(
        "SELECT 1;", user="pinka_monitor", password=MONITOR_PASSWORD
    )
    assert "no pg_hba.conf entry" in monitor_on_platform.stderr


def test_a_clean_migration_passes_the_check_and_allowed_roles_keep_access(
    bootstrapped: Cluster,
) -> None:
    migrator = login("allprof_migrator")
    bootstrapped.ok(
        "CREATE TABLE IF NOT EXISTS restricted.answers (id int);"
        " CREATE TABLE IF NOT EXISTS public.runs (id int);"
        " GRANT USAGE ON SCHEMA restricted TO allprof_controller, allprof_restricted_operator;"
        " GRANT SELECT ON restricted.answers TO allprof_controller, allprof_restricted_operator;"
        " GRANT USAGE ON SCHEMA public TO allprof_reader, allprof_observer;"
        " GRANT SELECT ON public.runs TO allprof_reader, allprof_observer;",
        **migrator,
    )
    assert restricted_check(bootstrapped) == []
    assert (
        bootstrapped.ok("SELECT count(*) FROM restricted.answers;", **login("allprof_ctl")) == "0"
    )
    assert (
        bootstrapped.ok("SELECT count(*) FROM restricted.answers;", **login("allprof_rop")) == "0"
    )
    assert bootstrapped.ok("SELECT count(*) FROM public.runs;", **login("allprof_reader")) == "0"
    for role in ("allprof_reader", "allprof_grafana", "allprof_obs", "allprof_sbx"):
        denied = bootstrapped.psql("SELECT count(*) FROM restricted.answers;", **login(role))
        assert "permission denied for schema restricted" in denied.stderr, role


FORBIDDEN_GRANTS = [
    (
        "schema usage",
        "GRANT USAGE ON SCHEMA restricted TO allprof_reader",
        "allprof_reader has privileges on schema restricted",
    ),
    (
        "schema usage for PUBLIC",
        "GRANT USAGE ON SCHEMA restricted TO PUBLIC",
        "PUBLIC has privileges on schema restricted",
    ),
    (
        "table",
        "GRANT SELECT ON restricted.answers TO allprof_grafana",
        "allprof_grafana has privileges on restricted.answers",
    ),
    (
        "column",
        "GRANT SELECT (id) ON restricted.answers TO allprof_obs",
        "allprof_obs has privileges on column id of restricted.answers",
    ),
    (
        "sequence",
        "CREATE SEQUENCE IF NOT EXISTS restricted.counter;"
        " GRANT USAGE ON SEQUENCE restricted.counter TO allprof_sbx",
        "allprof_sbx has privileges on restricted.counter",
    ),
    (
        "function",
        "CREATE OR REPLACE FUNCTION restricted.f() RETURNS int LANGUAGE sql AS 'select 1';"
        " GRANT EXECUTE ON FUNCTION restricted.f() TO allprof_brk",
        "allprof_brk may execute restricted.f()",
    ),
    (
        "default privileges in the schema",
        "ALTER DEFAULT PRIVILEGES IN SCHEMA restricted GRANT SELECT ON TABLES TO allprof_reader",
        "allprof_reader gets default privileges on TABLES created by allprof_migrator",
    ),
    (
        "global default privileges",
        "ALTER DEFAULT PRIVILEGES GRANT SELECT ON TABLES TO allprof_obs",
        "allprof_obs gets default privileges on TABLES created by allprof_migrator",
    ),
    (
        "a view outside the schema",
        "CREATE OR REPLACE VIEW public.leak AS SELECT * FROM restricted.answers;"
        " GRANT SELECT ON public.leak TO allprof_reader",
        "allprof_reader has privileges on leak, which reads schema restricted",
    ),
    (
        "a SECURITY DEFINER function outside the schema",
        "CREATE OR REPLACE FUNCTION public.peek() RETURNS bigint LANGUAGE sql SECURITY DEFINER"
        " AS 'select count(*) from restricted.answers'",
        "PUBLIC may execute peek(), a SECURITY DEFINER function of an allowed role",
    ),
]


@pytest.mark.parametrize(("kind", "grant", "violation"), FORBIDDEN_GRANTS)
def test_a_forbidden_grant_is_reported_and_revoked(
    bootstrapped: Cluster, kind: str, grant: str, violation: str
) -> None:
    del kind
    bootstrapped.ok(
        "CREATE TABLE IF NOT EXISTS restricted.answers (id int);", **login("allprof_migrator")
    )
    bootstrapped.ok(grant + ";", **login("allprof_migrator"))
    assert violation in restricted_check(bootstrapped)
    assert restricted_check(bootstrapped) == []  # revoked: the next check is clean


def test_a_membership_in_an_allowed_role_is_reported_and_revoked(bootstrapped: Cluster) -> None:
    bootstrapped.ok("GRANT allprof_migrator TO allprof_reader;")
    bootstrapped.ok("GRANT allprof_controller TO allprof_grafana;")
    found = restricted_check(bootstrapped)
    assert "allprof_reader is a member of allprof_migrator" in found
    assert "allprof_grafana is a member of allprof_controller" in found
    assert restricted_check(bootstrapped) == []
    assert (
        bootstrapped.ok("SELECT pg_has_role('allprof_reader', 'allprof_migrator', 'MEMBER');")
        == "f"
    )


def test_publics_default_execute_in_the_schema_is_revoked_without_a_violation(
    bootstrapped: Cluster,
) -> None:
    bootstrapped.ok(
        "CREATE OR REPLACE FUNCTION restricted.g() RETURNS int LANGUAGE sql AS 'select 2';",
        **login("allprof_migrator"),
    )
    result = bootstrapped.psql(PLAN["restricted_check_sql"])
    assert changes(result, "pinka-violation: ") == []
    assert "revoked the default EXECUTE of PUBLIC on restricted.g()" in changes(result)


def test_the_bootstrap_undoes_reader_access_to_the_schema(bootstrapped: Cluster) -> None:
    bootstrapped.ok(
        "GRANT USAGE ON SCHEMA restricted TO allprof_reader, PUBLIC;", **login("allprof_migrator")
    )
    assert platform_bootstrap(bootstrapped) == ["revoked schema restricted"]
    for role in ("allprof_reader", "allprof_grafana", "public"):
        assert (
            bootstrapped.ok(f"SELECT has_schema_privilege('{role}', 'restricted', 'USAGE');") == "f"
        )


def test_changed_password_is_detected(bootstrapped: Cluster) -> None:
    rotated = {"allprof_obs": ROTATED_PASSWORD}
    assert set_passwords(bootstrapped, rotated) == ["allprof_obs"]
    assert bootstrapped.ok("SELECT 1;", user="allprof_obs", password=ROTATED_PASSWORD) == "1"
    assert set_passwords(bootstrapped, rotated) == []
    assert set_passwords(bootstrapped, {"allprof_obs": VAULT["allprof_obs"]}) == ["allprof_obs"]


def test_scram_filters_agree_with_postgres(bootstrapped: Cluster) -> None:
    # PostgreSQL hashes a clear-text password itself; the filter must accept that secret.
    bootstrapped.ok(INTRUDER, db="postgres")
    bootstrapped.ok(
        "SET password_encryption = 'scram-sha-256'; ALTER ROLE intruder PASSWORD 'plain-test';",
        db="postgres",
    )
    stored = bootstrapped.ok(
        "SELECT rolpassword FROM pg_authid WHERE rolname = 'intruder';", db="postgres"
    )
    assert stored.startswith("SCRAM-SHA-256$4096:")
    assert pinka.scram_matches("plain-test", stored)
    assert not pinka.scram_matches("other-test", stored)


def test_pg_hba_without_a_platform(tmp_path: Path) -> None:
    variables = ansible_vars(
        GROUP_VARS,
        ROLES / "postgres" / "defaults" / "main.yml",
        ROLES / "postgres" / "vars" / "main.yml",
        host_profile=fixture_profile("ubuntu2404-baremetal-nvidia-driver-loaded"),
    )
    lines = [
        line
        for line in render_file(CONFIG / "pg_hba.conf.j2", variables).splitlines()
        if line and not line.startswith("#")
    ]
    assert lines == [
        "local   all       postgres  scram-sha-256",
        "local   postgres  pinka_monitor  scram-sha-256",
    ]
    assert evaluate("postgres_service_group", variables) == "root"
    del tmp_path
