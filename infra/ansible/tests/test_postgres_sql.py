"""The rendered PostgreSQL configuration and bootstrap SQL against a real server.

A throwaway cluster in a temporary directory (Unix socket only, no TCP) runs
the rendered postgresql.conf and pg_hba.conf, the bootstrap SQL twice, the
password handling of the postgres role, and the contract's reader rule:
``pokus_reader`` can never see the ``gt`` schema (contracts.md#postgresql).
It uses the PostgreSQL server installed on the machine (the CI runners ship
one); the pinned image runs a newer major, and the SQL uses nothing that
differs between them.
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

import pokus
from ansible_testlib import (
    GROUP_VARS,
    INFRA,
    ROLES,
    ansible_vars,
    evaluate,
    fixture_profile,
    render_file,
)

CONFIG = INFRA / "config" / "postgres"
SUPERUSER_PASSWORD = "superuser-test-password"  # noqa: S105 - throwaway test cluster
VAULT = {
    role: f"{role}-test-password"
    for role in (
        "pokus_migrator",
        "pokus_orch",
        "pokus_gw",
        "pokus_exec",
        "pokus_inject",
        "pokus_verify",
        "pokus_eval",
        "pokus_reader",
        "pokus_grafana",
    )
}
MONITOR_PASSWORD = "monitor-test-password"  # noqa: S105 - throwaway test cluster
ROTATED_PASSWORD = "rotated-test-password"  # noqa: S105 - throwaway test cluster
WRONG_PASSWORD = "wrong-test-password"  # noqa: S105 - throwaway test cluster
INTRUDER = (
    "DO $$ BEGIN CREATE ROLE intruder LOGIN PASSWORD 'x';"
    " EXCEPTION WHEN duplicate_object THEN NULL; END $$;"
)


def server_bindir() -> Path | None:
    candidates = sorted(
        Path("/usr/lib/postgresql").glob("*/bin"), key=lambda p: int(p.parent.name), reverse=True
    )
    if shutil.which("pg_ctl"):
        candidates.insert(0, Path(str(shutil.which("pg_ctl"))).parent)
    return next((c for c in candidates if (c / "initdb").is_file()), None)


BINDIR = server_bindir()
pytestmark = pytest.mark.skipif(BINDIR is None, reason="no PostgreSQL server binaries installed")


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
        db: str = "pokus",
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
        vault_postgres_passwords=VAULT,
        postgres_service_socket_dir=str(socket_dir / "svc"),
        postgres_reader_socket_dir=str(socket_dir / "reader"),
        postgres_shared_buffers="32MB",
    )


@pytest.fixture(scope="module")
def cluster() -> Iterator[Cluster]:
    assert BINDIR is not None
    root = Path(tempfile.mkdtemp(prefix="pokus-pg-"))
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
        state = Cluster(BINDIR, root, sockets, prefix)
        state.ok("CREATE DATABASE pokus;", db="postgres")  # POSTGRES_DB=pokus in the image
        yield state
    finally:
        subprocess.run(
            [*prefix, str(BINDIR / "pg_ctl"), f"--pgdata={data}", "--mode=immediate", "stop"],
            check=False,
            capture_output=True,
        )
        shutil.rmtree(root, ignore_errors=True)


def bootstrap(cluster: Cluster) -> list[str]:
    result = cluster.psql((cluster.root / "conf" / "bootstrap.sql").read_text())
    assert result.returncode == 0, result.stderr
    return [
        line.split("pokus-changed: ", 1)[1]
        for line in result.stderr.splitlines()
        if "pokus-changed: " in line
    ]


def set_passwords(cluster: Cluster, passwords: dict[str, str]) -> list[str]:
    """What tasks/bootstrap.yml does: compare stored secrets, set mismatching ones."""
    query = evaluate("postgres_secrets_query", postgres_vars(cluster.socket_dir))
    stored = json.loads(cluster.ok(query))
    changed = [
        role for role, pw in passwords.items() if not pokus.scram_matches(pw, stored.get(role))
    ]
    for role in changed:
        cluster.ok(f"ALTER ROLE \"{role}\" PASSWORD '{pokus.scram_verifier(passwords[role])}';")
    return changed


@pytest.fixture(scope="module")
def bootstrapped(cluster: Cluster) -> Cluster:
    first = bootstrap(cluster)
    assert "created role pokus_reader" in first
    assert "created schema gt" in first
    assert "owner of schema public" in first
    passwords = {**VAULT, "pokus_monitor": MONITOR_PASSWORD}
    assert sorted(set_passwords(cluster, passwords)) == sorted(passwords)
    return cluster


def test_bootstrap_is_idempotent(bootstrapped: Cluster) -> None:
    assert bootstrap(bootstrapped) == []
    assert set_passwords(bootstrapped, {**VAULT, "pokus_monitor": MONITOR_PASSWORD}) == []


def test_durability_settings(bootstrapped: Cluster) -> None:
    query = evaluate("postgres_durability_query", postgres_vars(bootstrapped.socket_dir))
    settings = json.loads(bootstrapped.ok(query))
    assert settings == {
        "fsync": "on",
        "synchronous_commit": "on",
        "full_page_writes": "on",
        "data_checksums": "on",
    }


def test_owners_and_monitor(bootstrapped: Cluster) -> None:
    owners = bootstrapped.ok(
        "SELECT string_agg(nspname || '=' || pg_get_userbyid(nspowner), ',' ORDER BY nspname)"
        " FROM pg_namespace WHERE nspname IN ('public', 'gt');"
        " SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = 'pokus';"
        " SELECT pg_has_role('pokus_monitor', 'pg_monitor', 'MEMBER');"
    )
    assert owners.splitlines() == ["gt=pokus_migrator,public=pokus_migrator", "pokus_migrator", "t"]


def test_service_roles_log_in_over_the_sockets(bootstrapped: Cluster) -> None:
    for role in ("pokus_migrator", "pokus_orch", "pokus_reader"):
        assert bootstrapped.ok("SELECT current_user;", user=role, password=VAULT[role]) == role
    wrong = bootstrapped.psql("SELECT 1;", user="pokus_orch", password=WRONG_PASSWORD)
    assert "password authentication failed" in wrong.stderr


def test_grafana_and_monitor_use_the_sockets(bootstrapped: Cluster) -> None:
    grafana = {"user": "pokus_grafana", "password": VAULT["pokus_grafana"]}
    assert bootstrapped.ok("SELECT current_user;", **grafana) == "pokus_grafana"
    monitor = {"user": "pokus_monitor", "password": MONITOR_PASSWORD}
    assert bootstrapped.ok("SELECT count(*) > 0 FROM pg_stat_activity;", **monitor) == "t"


def test_other_roles_are_rejected(bootstrapped: Cluster) -> None:
    bootstrapped.ok(INTRUDER)
    result = bootstrapped.psql("SELECT 1;", user="intruder", password="x")  # noqa: S106
    assert result.returncode != 0
    assert "no pg_hba.conf entry" in result.stderr
    other_db = bootstrapped.psql(
        "SELECT 1;", user="pokus_orch", password=VAULT["pokus_orch"], db="postgres"
    )
    assert "no pg_hba.conf entry" in other_db.stderr


def test_reader_never_sees_gt(bootstrapped: Cluster) -> None:
    # What platform migrations do: tables in both schemas, a grant in public.
    migrator = {"user": "pokus_migrator", "password": VAULT["pokus_migrator"]}
    bootstrapped.ok(
        "CREATE TABLE IF NOT EXISTS public.runs (id int);"
        " GRANT USAGE ON SCHEMA public TO pokus_reader;"
        " GRANT SELECT ON public.runs TO pokus_reader;"
        " CREATE TABLE IF NOT EXISTS gt.answers (id int);",
        **migrator,
    )
    reader = {"user": "pokus_reader", "password": VAULT["pokus_reader"]}
    assert bootstrapped.ok("SELECT count(*) FROM public.runs;", **reader) == "0"
    denied = bootstrapped.psql("SELECT count(*) FROM gt.answers;", **reader)
    assert "permission denied for schema gt" in denied.stderr
    assert bootstrapped.ok("SELECT has_schema_privilege('pokus_reader', 'gt', 'USAGE');") == "f"
    # A migration that grants gt by mistake is undone by the next bootstrap.
    bootstrapped.ok("GRANT USAGE ON SCHEMA gt TO pokus_reader, PUBLIC;", **migrator)
    assert bootstrapped.ok("SELECT has_schema_privilege('pokus_reader', 'gt', 'USAGE');") == "t"
    assert bootstrap(bootstrapped) == ["revoked gt"]
    for role in ("pokus_reader", "pokus_grafana", "public"):
        assert bootstrapped.ok(f"SELECT has_schema_privilege('{role}', 'gt', 'USAGE');") == "f"
    grafana = {"user": "pokus_grafana", "password": VAULT["pokus_grafana"]}
    assert (
        "permission denied for schema gt"
        in bootstrapped.psql("SELECT count(*) FROM gt.answers;", **grafana).stderr
    )


def test_changed_password_is_detected(bootstrapped: Cluster) -> None:
    rotated = {"pokus_eval": ROTATED_PASSWORD}
    assert set_passwords(bootstrapped, rotated) == ["pokus_eval"]
    assert bootstrapped.ok("SELECT 1;", user="pokus_eval", password=ROTATED_PASSWORD) == "1"
    assert set_passwords(bootstrapped, rotated) == []
    assert set_passwords(bootstrapped, {"pokus_eval": VAULT["pokus_eval"]}) == ["pokus_eval"]


def test_scram_filters_agree_with_postgres(bootstrapped: Cluster) -> None:
    # PostgreSQL hashes a clear-text password itself; the filter must accept that secret.
    bootstrapped.ok(INTRUDER)
    bootstrapped.ok(
        "SET password_encryption = 'scram-sha-256'; ALTER ROLE intruder PASSWORD 'plain-test';"
    )
    stored = bootstrapped.ok("SELECT rolpassword FROM pg_authid WHERE rolname = 'intruder';")
    assert stored.startswith("SCRAM-SHA-256$4096:")
    assert pokus.scram_matches("plain-test", stored)
    assert not pokus.scram_matches("other-test", stored)
