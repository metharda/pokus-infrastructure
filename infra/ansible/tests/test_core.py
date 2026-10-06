"""The compose helper role and the core project (PostgreSQL, OPA).

Templates are rendered like the template module; every Compose file is
checked with ``docker compose config`` against an .env file rendered by the
compose role (the Docker CLI works without a daemon for this).
"""

from __future__ import annotations

import configparser
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from ansible_testlib import (
    GROUP_VARS,
    INFRA,
    ROLES,
    ansible_vars,
    evaluate,
    fixture_profile,
    load_yaml,
    render_file,
    render_template,
)

VAULT = {
    role: "test"
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
LOCK = load_yaml(INFRA / "versions.lock.yml")
# Role defaults that the include_role vars refer to.
DEFAULTS = ansible_vars(ROLES / "observability" / "defaults" / "main.yml")
PROJECTS = sorted(p.parent.name for p in (INFRA / "compose").glob("*/compose.yml"))


def compose_vars(project: str, **extra: Any) -> dict[str, Any]:
    variables: dict[str, Any] = {
        "pokus_versions": LOCK,
        "compose_project": project,
        "compose_config_hashes": {},
        "compose_docker": {"data-root": "/var/lib/docker"},
        "compose_gpu_method": "none",
        "compose_pokus_gid": "1000",
        "compose_pokus_svc_gid": "999",
        **extra,
    }
    return ansible_vars(GROUP_VARS, ROLES / "compose" / "defaults" / "main.yml", **variables)


def postgres_vars(**extra: Any) -> dict[str, Any]:
    variables: dict[str, Any] = {
        "host_profile": fixture_profile("ubuntu2404-baremetal-nvidia-driver-loaded"),
        "vault_postgres_passwords": VAULT,
        **extra,
    }
    return ansible_vars(
        GROUP_VARS,
        ROLES / "postgres" / "defaults" / "main.yml",
        ROLES / "postgres" / "vars" / "main.yml",
        **variables,
    )


def env_lines(text: str) -> dict[str, str]:
    return dict(
        line.split("=", 1) for line in text.splitlines() if line and not line.startswith("#")
    )


def test_env_pins_every_image_by_digest() -> None:
    hashes = {"POSTGRES_CONFIG_HASH": "a" * 64}
    env = env_lines(
        render_template("compose", "env.j2", compose_vars("core", compose_config_hashes=hashes))
    )
    assert env["COMPOSE_PROJECT_NAME"] == "pokus-core"
    assert env["POKUS_ROOT"] == "/srv/pokus"
    assert env["POSTGRES_CONFIG_HASH"] == "a" * 64
    for name, image in LOCK["containers"]["images"].items():
        assert env[f"{name.upper()}_IMAGE"] == f"{image['image']}@{image['digest']}"
    assert env["OPA_PORT"] == "8181"
    assert env["GRAFANA_PORT"] == "18700"


def test_compose_unit() -> None:
    text = render_template("compose", "pokus-compose.service.j2", compose_vars("observability"))
    unit = configparser.ConfigParser(interpolation=None, strict=True)
    unit.optionxform = str  # type: ignore[assignment,method-assign]
    unit.read_string(text)
    assert unit["Service"]["Type"] == "oneshot"
    assert unit["Service"]["RemainAfterExit"] == "yes"
    assert unit["Service"]["WorkingDirectory"] == "/srv/pokus/services/observability"
    assert unit["Service"]["ExecStart"].startswith("/usr/bin/docker compose up --detach --wait")
    assert unit["Service"]["ExecStop"] == "/usr/bin/docker compose down"
    assert unit["Unit"]["PartOf"] == "pokus-lab.target"
    assert unit["Install"]["WantedBy"] == "pokus-lab.target"


def test_compose_files_reference_only_pinned_images() -> None:
    images = {
        f"${{{name.upper()}_IMAGE:?pinned image missing}}" for name in LOCK["containers"]["images"]
    }
    for project in PROJECTS:
        compose = load_yaml(INFRA / "compose" / project / "compose.yml")
        for name, service in compose["services"].items():
            assert service["image"] in images, (project, name)
            for port in service.get("ports", []):
                assert str(port).startswith("127.0.0.1:"), (project, name, port)


def compose_includes() -> dict[str, list[dict[str, Any]]]:
    """The compose role's include_role vars of every role, by project."""
    found: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(ROLES.glob("*/tasks/*.yml")):
        for task in load_yaml(path) or []:
            if task.get("ansible.builtin.include_role", {}).get("name") == "compose":
                found.setdefault(task["vars"]["compose_project"], []).append(task["vars"])
    return found


def test_every_project_is_included_with_one_environment() -> None:
    includes = compose_includes()
    assert sorted(includes) == PROJECTS
    for project, entries in includes.items():
        # Roles sharing a project write the same .env file.
        assert len({repr(e.get("compose_extra_env", {})) for e in entries}) == 1, project


@pytest.mark.parametrize("gpu_method", ["cdi", "runtime"])
@pytest.mark.parametrize("project", PROJECTS)
def test_compose_config_is_valid(project: str, gpu_method: str, tmp_path: Path) -> None:
    docker = shutil.which("docker")
    assert docker, "the Docker CLI with the Compose plugin is needed for this test"
    shutil.copy(INFRA / "compose" / project / "compose.yml", tmp_path / "compose.yml")
    include = compose_includes()[project][0]
    variables = compose_vars(
        project, compose_root=str(tmp_path.parent), compose_gpu_method=gpu_method
    )
    extra = {
        key: evaluate(value.strip("{} "), variables | DEFAULTS)
        for key, value in include.get("compose_extra_env", {}).items()
    }
    gpu = bool(include.get("compose_gpu"))
    text = render_template(
        "compose", "env.j2", {**variables, "compose_extra_env": extra, "compose_gpu": gpu}
    )
    (tmp_path / ".env").write_text(text)
    result = subprocess.run(
        [docker, "compose", "--project-directory", str(tmp_path), "config", "--format", "json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    config = yaml.safe_load(result.stdout)
    assert config["name"] == f"pokus-{project}"
    for name, service in config["services"].items():
        assert "@sha256:" in service["image"], name
        assert service.get("network_mode") in ("host", "none") or "ports" in service, name


def test_postgres_configuration() -> None:
    text = render_file(INFRA / "config/postgres/postgresql.conf.j2", postgres_vars())
    settings = dict(
        (key.strip(), value.strip())
        for key, _, value in (line.partition("=") for line in text.splitlines())
        if value and not key.startswith("#")
    )
    for name in ("fsync", "synchronous_commit", "full_page_writes"):
        assert settings[name] == "on"
    assert settings["unix_socket_directories"] == (
        "'/var/run/postgresql,/run/pokus/postgres,/run/pokus/postgres-reader'"
    )
    assert settings["hba_file"] == "'/etc/postgresql/pg_hba.conf'"
    memory = fixture_profile("ubuntu2404-baremetal-nvidia-driver-loaded")["hardware"]["memory_gib"]
    assert settings["shared_buffers"] == f"{min(max(int(memory * 64), 128), 4096)}MB"
    assert (
        render_file(
            INFRA / "config/postgres/postgresql.conf.j2",
            postgres_vars(postgres_shared_buffers="2GB"),
        ).count("shared_buffers = 2GB")
        == 1
    )


def test_pg_hba_admits_only_contract_roles_on_sockets() -> None:
    text = render_file(INFRA / "config/postgres/pg_hba.conf.j2", postgres_vars())
    rules = [line.split() for line in text.splitlines() if line and not line.startswith("#")]
    roles = (
        "pokus_migrator,pokus_orch,pokus_gw,pokus_exec,pokus_inject,pokus_verify,pokus_eval,"
        "pokus_reader,pokus_grafana,pokus_monitor"
    )
    assert rules == [
        ["local", "all", "postgres", "scram-sha-256"],
        ["local", "pokus", roles, "scram-sha-256"],
    ]
    conf = render_file(INFRA / "config/postgres/postgresql.conf.j2", postgres_vars())
    assert "listen_addresses = ''" in conf.splitlines()
    compose = load_yaml(INFRA / "compose/core/compose.yml")["services"]
    assert compose["postgres"]["network_mode"] == "none"
    assert "ports" not in compose["postgres"]


def test_socket_directories_and_reader_credential() -> None:
    variables = postgres_vars()
    tmpfiles = render_template("postgres", "pokus-postgres.tmpfiles.conf.j2", variables)
    entries = [line.split() for line in tmpfiles.splitlines() if line and not line.startswith("#")]
    assert entries == [
        ["d", "/run/pokus", "0755", "root", "root", "-"],
        ["d", "/run/pokus/postgres", "0750", "999", "pokus-svc", "-"],
        ["d", "/run/pokus/postgres-reader", "0750", "999", "pokus", "-"],
    ]
    vault = {**VAULT, "pokus_reader": "a:b\\c"}
    pgpass = render_template("postgres", "pgpass.j2", postgres_vars(vault_postgres_passwords=vault))
    lines = [line for line in pgpass.splitlines() if not line.startswith("#")]
    assert lines == ["/run/pokus/postgres-reader:5432:pokus:pokus_reader:a\\:b\\\\c"]


def test_password_files_follow_the_contract() -> None:
    files = {f["role"]: f for f in evaluate("postgres_service_roles", postgres_vars())}
    assert files.pop("pokus_migrator") == {
        "role": "pokus_migrator",
        "owner": "root",
        "group": "root",
        "mode": "0600",
    }
    for role, entry in files.items():
        assert entry == {
            "role": role,
            "owner": "root",
            "group": role.replace("_", "-"),
            "mode": "0640",
        }
    assert sorted(evaluate("postgres_vault_roles", postgres_vars())) == sorted(VAULT)


def test_psql_never_puts_the_password_on_a_command_line() -> None:
    argv = evaluate("postgres_psql", postgres_vars())
    assert argv[:6] == ["docker", "exec", "--interactive", "--user", "postgres", "pokus-postgres"]
    assert "$(cat /run/secrets/postgres-superuser)" in argv[8]
    assert not any("PGPASSWORD=" in a for a in argv[:8])


def test_opa_files_and_link() -> None:
    variables = ansible_vars(GROUP_VARS, ROLES / "opa" / "defaults" / "main.yml")
    assert evaluate("opa_policy_link", variables) == "/srv/pokus/services/core/policy"
    assert evaluate("opa_platform_policy_dir", variables) == (
        "/srv/pokus/deploy/platform/current/policy"
    )
    compose = load_yaml(INFRA / "compose/core/compose.yml")
    opa = compose["services"]["opa"]
    assert "./policy:/policy:ro" in opa["volumes"]
    assert "--authorization=basic" in opa["command"]
    assert opa["ports"] == ["127.0.0.1:${OPA_PORT:?}:8181"]
    assert yaml.safe_load((INFRA / "config/opa/opa.yaml").read_text()) == {
        "decision_logs": {"console": True}
    }
