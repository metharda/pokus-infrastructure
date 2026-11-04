"""The compose helper role and the core project (PostgreSQL, OPA).

Templates are rendered like the template module; every Compose file is
checked with ``docker compose config`` against an .env file rendered by the
compose role (the Docker CLI works without a daemon for this).
"""

from __future__ import annotations

import configparser
import ipaddress
import re
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
    template_value,
)

# The database plan of a rendered platform (pinka-platform, database.py).
PLATFORM_DB = {
    "platform": "demo",
    "database": "demo",
    "socket_group": "demo-db",
    "logins": [
        {"role": "demo_ctl", "account": "demo-ctl", "group_role": "demo_controller"},
        {"role": "demo_obs", "account": "demo-obs", "group_role": "demo_observer"},
    ],
    "migrator": "demo_migrator",
    "reader": "demo_reader",
    "grafana": "demo_grafana",
    "password_files": [
        {"role": "demo_ctl", "owner": "root", "group": "demo-ctl", "mode": "0640"},
        {"role": "demo_obs", "owner": "root", "group": "demo-obs", "mode": "0640"},
        {"role": "demo_migrator", "owner": "root", "group": "demo-migrate", "mode": "0640"},
    ],
    "vault_roles": ["demo_ctl", "demo_grafana", "demo_migrator", "demo_obs", "demo_reader"],
}
VAULT = dict.fromkeys(PLATFORM_DB["vault_roles"], "test")
LOCK = load_yaml(INFRA / "versions.lock.yml")
# Role defaults that the include_role vars refer to.
DEFAULTS = ansible_vars(
    ROLES / "observability" / "defaults" / "main.yml", ROLES / "observability" / "vars" / "main.yml"
)
PROJECTS = sorted(p.parent.name for p in (INFRA / "compose").glob("*/compose.yml"))


def compose_vars(project: str, **extra: Any) -> dict[str, Any]:
    variables: dict[str, Any] = {
        "pinka_versions": LOCK,
        "compose_project": project,
        "compose_config_hashes": {},
        "pinka_docker_data_root": "/var/lib/docker",
        "compose_gpu_method": "none",
        "compose_pinka_gid": "1000",
        "compose_db_socket_gid": "999",
        "compose_query_gid": "998",
        "observability_ids": {
            name: {"uid": str(990 + i), "gid": str(990 + i)}
            for i, name in enumerate(
                ["otel_gateway", "prometheus", "loki", "tempo", "grafana", "query_proxy"]
            )
        }
        | {"otel_journald": {"uid": "990", "gid": "990"}},
        "observability_journal_gid": "101",
        # A host without a lib64 directory (the empty directory stands in).
        "observability_host_dirs": {
            "/var/log/journal": "/var/log/journal",
            "/run/log/journal": "/run/log/journal",
            "/lib64": "/srv/pinka/services/observability/empty",
            "/usr/lib64": "/srv/pinka/services/observability/empty",
        },
        # A lab root behind a link: cAdvisor's masks take the real paths.
        "observability_mask_paths": {
            "stdout_lines": [
                "/data/pinka",
                "/etc/pinka",
                "/var/lib",
                "/run/pinka",
                "/var/lib/pinka",
                "/var/lib/pinka-kept",
            ]
        },
        **extra,
    }
    return ansible_vars(GROUP_VARS, ROLES / "compose" / "defaults" / "main.yml", **variables)


def postgres_vars(platform: bool = True, **extra: Any) -> dict[str, Any]:
    variables: dict[str, Any] = {
        "host_profile": fixture_profile("ubuntu2404-baremetal-nvidia-driver-loaded"),
        "vault_postgres_passwords": VAULT,
        "active_platform": {"database": PLATFORM_DB} if platform else {},
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
    assert env["COMPOSE_PROJECT_NAME"] == "pinka-core"
    assert env["PINKA_ROOT"] == "/srv/pinka"
    assert env["POSTGRES_CONFIG_HASH"] == "a" * 64
    for name, image in LOCK["containers"]["images"].items():
        assert env[f"{name.upper()}_IMAGE"] == f"{image['image']}@{image['digest']}"
    assert env["OPA_PORT"] == "8181"
    assert env["GRAFANA_PORT"] == "18700"
    # Per service, not from the Docker daemon.
    assert env["LOG_MAX_SIZE"] == "20m"
    assert env["LOG_MAX_FILE"] == "5"
    assert env["CGROUP_PARENT"] == "pinka-services.slice"
    assert env["CORE_SUBNET"] == "172.30.0.0/24"
    assert env["DOCKER_DATA_ROOT"] == "/var/lib/docker"


def test_compose_unit() -> None:
    text = render_template("compose", "pinka-compose.service.j2", compose_vars("observability"))
    unit = configparser.ConfigParser(interpolation=None, strict=True)
    unit.optionxform = str  # type: ignore[assignment,method-assign]
    unit.read_string(text)
    assert unit["Service"]["Type"] == "oneshot"
    assert unit["Service"]["RemainAfterExit"] == "yes"
    assert unit["Service"]["WorkingDirectory"] == "/srv/pinka/services/observability"
    assert unit["Service"]["ExecStart"].startswith("/usr/bin/docker compose up --detach --wait")
    assert unit["Service"]["ExecStop"] == "/usr/bin/docker compose down"
    assert unit["Unit"]["PartOf"] == "pinka-lab.target"
    assert unit["Install"]["WantedBy"] == "pinka-lab.target"


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
    profiles = (
        evaluate(include["compose_profiles"].strip("{} "), variables | DEFAULTS)
        if "compose_profiles" in include
        else []
    )
    text = render_template(
        "compose",
        "env.j2",
        {**variables, "compose_extra_env": extra, "compose_gpu": gpu, "compose_profiles": profiles},
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
    assert config["name"] == f"pinka-{project}"
    for name, service in config["services"].items():
        assert "@sha256:" in service["image"], name
        assert service.get("network_mode") in ("host", "none") or "ports" in service, name
        # The same on a Docker that existed before pinka.
        assert service["cgroup_parent"] == "pinka-services.slice", name
        logging = service["logging"]
        if logging["driver"] != "journald":  # OPA's decision logs go to the collector
            assert logging == {"driver": "local", "options": {"max-size": "20m", "max-file": "5"}}
        # Docker refuses two mounts at one place.
        targets = [v["target"] for v in service.get("volumes", [])]
        targets += [entry.split(":")[0] for entry in service.get("tmpfs", [])]
        assert len(targets) == len(set(targets)), name
    if project == "observability":
        # cAdvisor's masks at the real paths (the lab's root behind a link here).
        masks = [
            v["target"] for v in config["services"]["cadvisor"]["volumes"] if v["type"] == "tmpfs"
        ]
        assert masks == [
            "/rootfs/data/pinka",
            "/rootfs/etc/pinka",
            "/rootfs/var/lib",
            "/rootfs/var/lib/pinka",
            "/rootfs/var/lib/pinka-kept",
            "/rootfs/run/pinka",
            "/var/run/pinka",
        ]
    networks = config.get("networks", {})
    for network in networks.values():
        # Explicit subnets from pinka's own pool, never Docker's default pools.
        (subnet,) = [c["subnet"] for c in network["ipam"]["config"]]
        assert ipaddress.IPv4Network(subnet).subnet_of(ipaddress.IPv4Network("172.30.0.0/16"))
    assert ("default" in networks) == (project == "core")


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
        "'/var/run/postgresql,/run/pinka/postgres,/run/pinka/postgres-reader'"
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


def test_pg_hba_admits_only_the_platform_roles_on_its_database() -> None:
    text = render_file(INFRA / "config/postgres/pg_hba.conf.j2", postgres_vars())
    rules = [line.split() for line in text.splitlines() if line and not line.startswith("#")]
    assert rules == [
        ["local", "all", "postgres", "scram-sha-256"],
        ["local", "postgres", "pinka_monitor", "scram-sha-256"],
        [
            "local",
            "demo",
            "demo_ctl,demo_obs,demo_migrator,demo_reader,demo_grafana",
            "scram-sha-256",
        ],
    ]
    conf = render_file(INFRA / "config/postgres/postgresql.conf.j2", postgres_vars())
    assert "listen_addresses = ''" in conf.splitlines()
    compose = load_yaml(INFRA / "compose/core/compose.yml")["services"]
    assert compose["postgres"]["network_mode"] == "none"
    assert "ports" not in compose["postgres"]
    assert "POSTGRES_DB" not in compose["postgres"]["environment"]
    exporter = compose["postgres_exporter"]
    assert exporter["environment"]["DATA_SOURCE_URI"].startswith("/postgres?")
    assert exporter["group_add"] == ["${POSTGRES_SOCKET_GID:?}"]


CONTRACT = INFRA / "docs" / "contracts.md"


def contract_section(heading: str, level: str = "##") -> str:
    """The text of one section of the contract page, up to the next of its level or above."""
    text = CONTRACT.read_text(encoding="utf-8")
    parts = text.split(f"\n{level} {heading}\n")
    assert len(parts) == 2, heading
    return re.split(rf"\n#{{2,{len(level)}}} ", parts[1], maxsplit=1)[0]


def table_rows(section: str) -> list[str]:
    return [line for line in section.splitlines() if line.startswith("| ")]


def one_row(rows: list[str], item: str) -> str:
    found = [row for row in rows if row.startswith(f"| {item} |")]
    assert len(found) == 1, item
    return found[0]


def test_the_contract_names_infras_clients_of_the_socket_directories() -> None:
    # Who reaches the two directories is tested against the Compose files in
    # test_container_mounts.py; these rows must name exactly them.
    rows = table_rows(contract_section("PostgreSQL"))
    assert one_row(rows, "Service socket directory") == (
        "| Service socket directory | `/run/pinka/postgres/`, group `<platform>-db`: besides "
        "root and PostgreSQL itself, only the accounts with a database role (the platform's "
        "services' and its `migrate` hook's, `<platform>-migrate`), and infra's metrics "
        "exporter (`pinka_monitor`), which holds the group in its container; the agent never |"
    )
    assert one_row(rows, "Reader socket directory") == (
        "| Reader socket directory | `/run/pinka/postgres-reader/`, group `pinka`: the "
        "researchers' reader role and Grafana; the reader's password file "
        "`/etc/pinka/postgres-reader/pgpass` is 0640 root:`pinka` |"
    )
    assert one_row(rows, "Infra's own role") == (
        "| Infra's own role | `pinka_monitor` (member of `pg_monitor`) for the metrics "
        "exporter; never used by a platform. `pg_monitor` may read every session's query "
        "text (`pg_stat_activity.query`); the exporter exports none of it, as a metric or a "
        "label |"
    )


def test_the_clarifications_have_a_section_and_a_rule() -> None:
    # Clarifications change no field, value or permission a platform relies
    # on, so they need no version bump, but they are never silent.
    # In the contract's own list of changes, as a table of its own.
    changes = contract_section("Changes from contract v1")
    section = contract_section("Clarifications of contract v2", "###")
    assert section.strip() in changes
    intro = " ".join(section.split("\n\n| Section |")[0].split())
    assert intro == (
        "Each states what infra already does, without a version bump. None changes a field, a "
        "value or a permission a platform relies on: no file infra renders for a platform "
        "(such as `principals.json` and `endpoints.toml`), no schema and no reserved name "
        "changes, only this page. A platform that keeps a copy of this page updates it when it "
        "pins the release that brings the clarification."
    )
    assert table_rows(section) == [
        "| Section | Clarification |",
        "| --- | --- |",
        "| [PostgreSQL](#postgresql) | besides root and PostgreSQL itself, the service socket "
        "directory also admits the `migrate` hook's account (`<platform>-migrate`, a member of "
        "`<platform>-db` when the platform "
        "requires `postgres`) and infra's metrics exporter (`pinka_monitor`), which holds the "
        "directory's group in its container |",
        "| [PostgreSQL](#postgresql) | `pg_monitor`, which `pinka_monitor` is a member of, may "
        "read every session's query text; the exporter exports none of it, as a metric or a "
        "label |",
    ]
    # The rule, in the paragraph "Changing the contract".
    text = CONTRACT.read_text(encoding="utf-8")
    (paragraph,) = [p for p in text.split("\n\n") if p.startswith("**Changing the contract**")]
    assert (
        "Clarifications which change no field, value or permission a platform relies on go "
        "into the Clarifications table ([Clarifications of contract "
        "v2](#clarifications-of-contract-v2)) without a version bump."
    ) in " ".join(paragraph.split())
    # The v1 table holds no clarification.
    v1_table = changes.split("\n### ", 1)[0].lower()
    assert "clarif" not in v1_table


def test_the_socket_group_of_the_exporter_is_the_directorys() -> None:
    # The exporter's POSTGRES_SOCKET_GID (compose role) names the group the
    # postgres role gives the service socket directory, with and without a
    # platform.
    for platform in (True, False):
        variables = postgres_vars(platform=platform)
        compose = ansible_vars(
            GROUP_VARS,
            ROLES / "compose" / "defaults" / "main.yml",
            active_platform=variables["active_platform"],
        )
        group = evaluate("compose_db_socket_group", compose)
        assert group == evaluate("postgres_service_group", variables)
        assert group == ("demo-db" if platform else "root")
        tmpfiles = render_template("postgres", "pinka-postgres.tmpfiles.conf.j2", variables)
        assert f"d /run/pinka/postgres 0750 999 {group} -" in tmpfiles.splitlines()
        # And the GID the exporter's container gets: the compose role's lookup
        # of that group, rendered into the project's .env.
        tasks = load_yaml(ROLES / "compose" / "tasks" / "main.yml")
        (derive,) = [t for t in tasks if t["name"] == "Derive the settings the project needs"]
        lookup = derive["ansible.builtin.set_fact"]["compose_db_socket_gid"]
        facts = {"getent_group": {group: ["x", "4242", ""], "pinka": ["x", "1000", ""]}}
        gid = str(template_value(lookup, compose | {"ansible_facts": facts})).strip()
        assert gid == "4242"
        env = env_lines(
            render_template("compose", "env.j2", compose_vars("core", compose_db_socket_gid=gid))
        )
        assert env["POSTGRES_SOCKET_GID"] == "4242"


PRELOAD = re.compile(
    r"^\s*(shared_preload_libraries|session_preload_libraries|local_preload_libraries"
    r"|include|include_dir|include_if_exists)\b",
    re.IGNORECASE,
)


def test_the_exporter_reads_no_query_text() -> None:
    # pinka_monitor (pg_monitor) may read every session's query text. The
    # exporter v0.20.1 runs only its default collectors, none of which reads
    # pg_stat_activity.query or pg_stat_statements.query (checked in its
    # source). What could change that is pinned here: the image (a new tag
    # or digest fails this test until the new version's collectors are
    # checked in its source), its command and environment (a collector flag,
    # extended queries, database discovery), its mounts, and PostgreSQL's
    # preloaded libraries (pg_stat_statements needs them).
    image = LOCK["containers"]["images"]["postgres_exporter"]
    assert (image["image"], image["tag"], image["digest"]) == (
        "docker.io/prometheuscommunity/postgres-exporter",
        "v0.20.1",
        "sha256:ac5ec343104fae0e2d84a27bb8d69b38430a11910c5382cad85d478d2bab713e",
    )
    services = load_yaml(INFRA / "compose/core/compose.yml")["services"]
    exporter = services["postgres_exporter"]
    assert exporter["image"] == "${POSTGRES_EXPORTER_IMAGE:?pinned image missing}"
    assert set(exporter) == {
        "logging",
        "cgroup_parent",
        "image",
        "container_name",
        "restart",
        "depends_on",
        "network_mode",
        "user",
        "group_add",
        "command",
        "environment",
        "volumes",
        "read_only",
        "security_opt",
        "cap_drop",
    }
    assert exporter["command"] == ["--web.listen-address=127.0.0.1:${POSTGRES_EXPORTER_PORT:?}"]
    assert exporter["environment"] == {
        "DATA_SOURCE_URI": "/postgres?host=/run/pinka/postgres&sslmode=disable",
        "DATA_SOURCE_USER": "pinka_monitor",
        "DATA_SOURCE_PASS_FILE": "/run/secrets/pinka-monitor",
    }
    assert exporter["volumes"] == [
        "/run/pinka/postgres:/run/pinka/postgres",
        "/etc/pinka/secrets/containers/pinka-monitor:/run/secrets/pinka-monitor:ro",
    ]
    # PostgreSQL itself: its configuration file only, no -c setting, no
    # include, no preloaded library, in either syntax (name = value or name
    # value), in any case; no initdb scripts (/docker-entrypoint-initdb.d) and
    # no setting through its environment.
    postgres = services["postgres"]
    assert postgres["command"] == [
        "postgres",
        "-c",
        "config_file=/etc/postgresql/postgresql.conf",
    ]
    assert set(postgres) == {
        "cgroup_parent",
        "command",
        "container_name",
        "environment",
        "healthcheck",
        "image",
        "labels",
        "logging",
        "network_mode",
        "restart",
        "shm_size",
        "stop_grace_period",
        "volumes",
    }
    assert postgres["volumes"] == [
        "${PINKA_ROOT:?}/state/postgres:/var/lib/postgresql",
        "./config/postgres:/etc/postgresql:ro",
        "/run/pinka/postgres:/run/pinka/postgres",
        "/run/pinka/postgres-reader:/run/pinka/postgres-reader",
        "/etc/pinka/secrets/containers/postgres-superuser:/run/secrets/postgres-superuser:ro",
    ]
    assert postgres["environment"] == {
        "POSTGRES_USER": "postgres",
        "POSTGRES_PASSWORD_FILE": "/run/secrets/postgres-superuser",
        "POSTGRES_INITDB_ARGS": (
            "--data-checksums --auth-local=scram-sha-256 --auth-host=scram-sha-256"
        ),
    }
    for platform in (True, False):
        conf = render_file(
            INFRA / "config/postgres/postgresql.conf.j2", postgres_vars(platform=platform)
        )
        assert not [line for line in conf.splitlines() if PRELOAD.match(line)]
    sql = " ".join(
        (INFRA / "config/postgres/bootstrap.sql.j2").read_text(encoding="utf-8").lower().split()
    )
    assert "pg_stat_statements" not in sql and "alter system" not in sql
    # The platform's SQL: tools/platform/tests/test_rendering.py.


def test_socket_directories_and_reader_credential() -> None:
    tmpfiles = render_template("postgres", "pinka-postgres.tmpfiles.conf.j2", postgres_vars())
    entries = [line.split() for line in tmpfiles.splitlines() if line and not line.startswith("#")]
    assert entries == [
        ["d", "/run/pinka", "0755", "root", "root", "-"],
        ["d", "/run/pinka/postgres", "0750", "999", "demo-db", "-"],
        ["d", "/run/pinka/postgres-reader", "0750", "999", "pinka", "-"],
    ]
    standalone = render_template(
        "postgres", "pinka-postgres.tmpfiles.conf.j2", postgres_vars(platform=False)
    )
    assert "d /run/pinka/postgres 0750 999 root -" in standalone
    vault = {**VAULT, "demo_reader": "a:b\\c"}
    pgpass = render_template("postgres", "pgpass.j2", postgres_vars(vault_postgres_passwords=vault))
    lines = [line for line in pgpass.splitlines() if not line.startswith("#")]
    assert lines == ["/run/pinka/postgres-reader:5432:demo:demo_reader:a\\:b\\\\c"]


def test_password_roles_follow_the_platform() -> None:
    assert evaluate("postgres_platform_logins", postgres_vars()) == [
        "demo_ctl",
        "demo_obs",
        "demo_migrator",
        "demo_reader",
        "demo_grafana",
    ]
    assert evaluate("postgres_all_roles", postgres_vars(platform=False)) == ["pinka_monitor"]
    assert evaluate("postgres_service_group", postgres_vars(platform=False)) == "root"


def test_psql_never_puts_the_password_on_a_command_line() -> None:
    argv = evaluate("postgres_psql", postgres_vars())
    assert argv[:6] == ["docker", "exec", "--interactive", "--user", "postgres", "pinka-postgres"]
    assert "$(cat /run/secrets/postgres-superuser)" in argv[8]
    assert not any("PGPASSWORD=" in a for a in argv[:8])


def test_opa_files_and_link() -> None:
    variables = ansible_vars(GROUP_VARS, ROLES / "opa" / "defaults" / "main.yml")
    assert evaluate("opa_policy_link", variables) == "/srv/pinka/services/core/policy"
    assert evaluate("opa_platform_policy_dir", variables) == ""
    assert evaluate("opa_decision_path", variables) == ""
    rendered = {
        "opa": {
            "bundle": "/srv/pinka/platforms/demo/releases/v1/policy",
            "decision_path": "demo/decide",
        }
    }
    with_platform = ansible_vars(
        GROUP_VARS, ROLES / "opa" / "defaults" / "main.yml", active_platform=rendered
    )
    assert evaluate("opa_platform_policy_dir", with_platform) == rendered["opa"]["bundle"]
    assert evaluate("opa_decision_path", with_platform) == "demo/decide"
    compose = load_yaml(INFRA / "compose/core/compose.yml")
    opa = compose["services"]["opa"]
    assert "./policy:/policy:ro" in opa["volumes"]
    assert opa["command"][-3:] == ["/config/authz.rego", "/config/placeholder", "/policy"]
    assert "--authorization=basic" in opa["command"]
    assert opa["ports"] == ["127.0.0.1:${OPA_PORT:?}:8181"]
    assert yaml.safe_load((INFRA / "config/opa/opa.yaml").read_text()) == {
        "decision_logs": {"console": True}
    }
