"""The observability role: collector, Prometheus, Loki, Tempo and Grafana configuration.

Every listener must be on 127.0.0.1 (or the VM listener address with
vm_targets), the collector pipelines must use defined components, and the
dashboards must only use the provisioned data sources. The configurations
were also checked with the pinned releases' own validators while writing
them (otelcol-contrib validate, promtool, loki -verify-config,
tempo -config.verify); these tests keep their structure from drifting.
"""

from __future__ import annotations

import configparser
import ipaddress
import json
import re
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

CONFIG = INFRA / "config"
TEMPLATES = (
    "otel/gateway.yaml",
    "otel/journald.yaml",
    "prometheus/prometheus.yml",
    "loki/loki.yaml",
    "tempo/tempo.yaml",
    "grafana/provisioning/datasources/pinka.yaml",
    "grafana/provisioning/dashboards/pinka.yaml",
)
DASHBOARDS = sorted((CONFIG / "grafana" / "dashboards").glob("*.json"))
LISTEN_KEYS = re.compile(r"(endpoint|listen_address|http_listen_address|grpc_listen_address|host)$")


# What the rendering of a platform with a database and dashboards holds.
PLATFORM = {
    "database": {"platform": "demo", "database": "demo", "grafana": "demo_grafana"},
    "dashboards": {"folder": "demo", "dir": "/srv/pinka/platforms/demo/releases/v1/dashboards"},
}


def obs_vars(
    vm: bool = False, gpu: bool = False, node_role: str = "full", platform: bool = True
) -> dict[str, Any]:
    profile = fixture_profile("ubuntu2404-baremetal-nvidia-driver-loaded")
    variables: dict[str, Any] = {
        "host_profile": profile,
        "pinka_features": {**profile["features"], "vm_targets": vm, "gpu_inference": gpu},
        "node_role": node_role,
        "vault_postgres_passwords": {"demo_grafana": "test"},
        "vault_grafana_admin_password": "test-admin",
        "active_platform": PLATFORM if platform else {},
    }
    return ansible_vars(
        GROUP_VARS,
        ROLES / "observability" / "defaults" / "main.yml",
        ROLES / "observability" / "vars" / "main.yml",
        **variables,
    )


def rendered(name: str, **kwargs: Any) -> Any:
    return yaml.safe_load(render_file(CONFIG / f"{name}.j2", obs_vars(**kwargs)))


def listen_addresses(node: Any, path: str = "") -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            where = f"{path}.{key}"
            if isinstance(value, (dict, list)):
                found += listen_addresses(value, where)
            elif LISTEN_KEYS.search(str(key)) and isinstance(value, str):
                found.append((where, value))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found += listen_addresses(value, f"{path}[{index}]")
    return found


@pytest.mark.parametrize("name", TEMPLATES)
def test_templates_render_with_headers(name: str) -> None:
    text = render_file(CONFIG / f"{name}.j2", obs_vars(vm=True, gpu=True))
    assert text.startswith(f"# Managed by pinka (config/{name}.j2).")
    assert yaml.safe_load(text)


@pytest.mark.parametrize("vm", [False, True])
def test_collector_listens_only_on_loopback_and_the_vm_address(vm: bool) -> None:
    allowed = {"127.0.0.1"} | ({"198.18.0.1"} if vm else set())
    for name in ("otel/gateway.yaml", "otel/journald.yaml"):
        config = rendered(name, vm=vm)
        for where, value in listen_addresses(config.get("receivers", {})) + listen_addresses(
            config.get("extensions", {})
        ):
            if "file_storage" in where:
                continue
            assert value.rsplit(":", 1)[0] in allowed, (name, where, value)
        telemetry = config["service"]["telemetry"]["metrics"]["readers"][0]["pull"]["exporter"]
        assert telemetry["prometheus"]["host"] == "127.0.0.1"


@pytest.mark.parametrize("vm", [False, True])
def test_collector_pipelines_use_defined_components(vm: bool) -> None:
    config = rendered("otel/gateway.yaml", vm=vm)
    receivers = set(config["receivers"]) - {"otlp/agent"}
    assert receivers == ({"otlp", "otlp/vm"} if vm else {"otlp"})
    for name, pipeline in config["service"]["pipelines"].items():
        if name.endswith("/agent"):
            continue
        assert set(pipeline["receivers"]) == receivers, name
        assert pipeline["processors"] == ["memory_limiter", "resource", "batch"], name
        assert set(pipeline["exporters"]) <= set(config["exporters"]), name
    assert config["exporters"]["otlp_http/loki"]["endpoint"] == "http://127.0.0.1:18702/otlp"
    assert config["exporters"]["prometheus"]["resource_to_telemetry_conversion"]["enabled"]
    journald = rendered("otel/journald.yaml")
    assert journald["receivers"]["journald"]["root_path"] == "/hostfs"
    assert journald["exporters"]["otlp_grpc/gateway"]["endpoint"] == "127.0.0.1:4317"
    assert "otlp" not in journald["receivers"]  # accepts nothing from targets


def test_loki_and_tempo_bind_loopback() -> None:
    loki = rendered("loki/loki.yaml")
    assert loki["server"]["http_listen_address"] == "127.0.0.1"
    assert loki["server"]["grpc_listen_address"] == "127.0.0.1"
    assert loki["common"]["ring"]["kvstore"]["store"] == "inmemory"
    assert loki["limits_config"]["allow_structured_metadata"] is True
    assert loki["compactor"]["retention_enabled"] is True
    tempo = rendered("tempo/tempo.yaml")
    assert tempo["server"]["http_listen_address"] == "127.0.0.1"
    assert tempo["server"]["grpc_listen_address"] == "127.0.0.1"
    endpoint = tempo["distributor"]["receivers"]["otlp"]["protocols"]["grpc"]["endpoint"]
    assert endpoint == "127.0.0.1:18727"
    rings = [tempo["distributor"], tempo["compactor"], tempo["metrics_generator"]]
    assert all(r["ring"]["kvstore"]["store"] == "inmemory" for r in rings)
    assert tempo["ingester"]["lifecycler"]["ring"]["kvstore"]["store"] == "inmemory"


@pytest.mark.parametrize(
    ("gpu", "node_role", "jobs"),
    [
        (False, "full", set()),
        (True, "full", {"gpu", "vllm"}),
        (True, "lab", {"gpu"}),
    ],
)
def test_prometheus_targets_are_loopback(gpu: bool, node_role: str, jobs: set[str]) -> None:
    config = rendered("prometheus/prometheus.yml", gpu=gpu, node_role=node_role)
    names = {job["job_name"] for job in config["scrape_configs"]}
    base = {
        "prometheus", "otlp", "otel-collector", "node", "cadvisor", "postgres",
        "loki", "tempo", "grafana", "incus",
    }  # fmt: skip
    assert names == base | jobs
    for job in config["scrape_configs"]:
        for static in job["static_configs"]:
            for target in static["targets"]:
                assert target.startswith("127.0.0.1:"), (job["job_name"], target)
    incus = next(j for j in config["scrape_configs"] if j["job_name"] == "incus")
    assert incus["scheme"] == "https"
    assert incus["metrics_path"] == "/1.0/metrics"
    assert set(incus["tls_config"]) == {"ca_file", "cert_file", "key_file"}


def test_internal_ports_do_not_collide() -> None:
    variables = ansible_vars(GROUP_VARS)
    ports = list(variables["pinka_default_ports"].values()) + list(
        variables["pinka_internal_ports"].values()
    )
    assert len(ports) == len(set(ports))
    address = ipaddress.ip_address(variables["vm_collector_address"])
    assert not address.is_global
    assert address in ipaddress.ip_network("198.18.0.0/15")  # RFC 2544 benchmarking range


def test_grafana_datasources() -> None:
    config = rendered("grafana/provisioning/datasources/pinka.yaml")
    sources = {d["uid"]: d for d in config["datasources"]}
    assert set(sources) == {"prometheus", "loki", "tempo", "platform-postgres"}
    # Through the query proxy's loopback port, never to the stores directly (§7.9).
    for uid in ("prometheus", "loki", "tempo"):
        assert sources[uid]["url"] == f"http://127.0.0.1:18733/{uid}", uid
    postgres = sources["platform-postgres"]
    assert postgres["name"] == "demo PostgreSQL"
    assert postgres["url"] == "/run/pinka/postgres-reader"
    assert postgres["user"] == "demo_grafana"
    assert postgres["jsonData"]["database"] == "demo"
    secret = postgres["secureJsonData"]["password"]
    assert secret == "$__file{/run/secrets/platform-grafana}"  # noqa: S105 - a file reference
    # "$$" is Grafana's escape for a literal "$".
    assert sources["loki"]["jsonData"]["derivedFields"][0]["url"] == "$${__value.raw}"


def test_without_a_platform_there_is_no_database_datasource() -> None:
    config = rendered("grafana/provisioning/datasources/pinka.yaml", platform=False)
    assert {d["uid"] for d in config["datasources"]} == {"prometheus", "loki", "tempo"}
    providers = rendered("grafana/provisioning/dashboards/pinka.yaml", platform=False)["providers"]
    assert [p["name"] for p in providers] == ["pinka"]
    assert evaluate("observability_grafana_db_password", obs_vars(platform=False)) == ""


def test_platform_dashboards_get_a_folder_of_the_platform() -> None:
    providers = rendered("grafana/provisioning/dashboards/pinka.yaml")["providers"]
    platform = next(p for p in providers if p["name"] == "platform")
    assert platform["folder"] == "demo"
    assert platform["options"]["path"] == "/etc/grafana/platform-dashboards"
    assert platform["allowUiUpdates"] is False
    compose = load_yaml(INFRA / "compose" / "observability" / "compose.yml")
    volumes = compose["services"]["grafana"]["volumes"]
    assert "./config/grafana/platform-dashboards:/etc/grafana/platform-dashboards:ro" in volumes
    assert (
        "/etc/pinka/secrets/containers/platform-grafana:/run/secrets/platform-grafana:ro" in volumes
    )


@pytest.mark.parametrize("path", DASHBOARDS, ids=lambda p: p.name)
def test_dashboards_use_provisioned_datasources(path: Path) -> None:
    dashboard = json.loads(path.read_text(encoding="utf-8"))
    # Infra's own dashboards; a platform's ship with its release (§7.9).
    assert dashboard["uid"] == f"pinka-{path.stem}"
    assert dashboard["description"].startswith(
        f"Managed by pinka (config/grafana/dashboards/{path.name})."
    )
    assert dashboard["timezone"] == "utc"
    ids = [panel["id"] for panel in dashboard["panels"]]
    assert len(ids) == len(set(ids))
    for panel in dashboard["panels"]:
        for item in [panel, *panel["targets"]]:
            assert item["datasource"]["uid"] in {"prometheus", "loki", "tempo"}


def test_expected_dashboards() -> None:
    assert [p.stem for p in DASHBOARDS] == [
        "gpu-vllm",
        "host-overview",
        "incus-targets",
        "lab-services",
    ]


def test_vm_listener_unit() -> None:
    text = render_template("observability", "pinka-vm-otlp-address.service.j2", obs_vars(vm=True))
    unit = configparser.ConfigParser(interpolation=None, strict=False)
    unit.optionxform = str  # type: ignore[assignment,method-assign]
    unit.read_string(text)
    assert unit["Unit"]["Before"] == "docker.service"
    assert "ip address replace 198.18.0.1/32 dev pinka-otlp0" in text
    assert unit["Service"]["ExecStop"] == "ip link delete pinka-otlp0"


def test_compose_services_bind_loopback() -> None:
    compose = load_yaml(INFRA / "compose/observability/compose.yml")
    for name, service in compose["services"].items():
        assert service["network_mode"] == "host", name
        assert "ports" not in service, name
        assert service["read_only"] is True, name
        assert service["cap_drop"] == ["ALL"], name
    grafana = compose["services"]["grafana"]["environment"]
    assert grafana["GF_SERVER_HTTP_ADDR"] == "127.0.0.1"
    assert grafana["GF_AUTH_ANONYMOUS_ENABLED"] == "false"
    journald = compose["services"]["otel_journald"]
    assert journald["cap_add"] == ["SYS_CHROOT"]
    gateway = compose["services"]["otel_gateway"]
    # No host files besides the gateway's own read-only copy of the pairing material.
    assert gateway["volumes"] == [
        "./config/otel:/etc/otelcol:ro",
        "/etc/pinka/secrets/containers/otel-pairing:/etc/pinka-pairing:ro",
    ]
    assert gateway["user"] == "${OTELCOL_UID:?}:${OTELCOL_GID:?}"
    stores = {
        name
        for name, service in compose["services"].items()
        if "stores" in service.get("profiles", [])
    }
    assert stores == {"prometheus", "loki", "tempo", "grafana", "query_proxy"}


def test_derived_flags() -> None:
    assert evaluate("observability_otlp_receivers", obs_vars(vm=True)) == ["otlp", "otlp/vm"]
    assert evaluate("observability_vllm", obs_vars(gpu=True, node_role="lab")) is False
    assert evaluate("observability_vllm", obs_vars(gpu=True, node_role="inference")) is True


def test_grafana_accounts_follow_the_researchers() -> None:
    variables = obs_vars()
    variables.update(
        researchers=[
            {"name": "researcher1", "admin": True},
            {"name": "researcher2", "admin": False, "grafana": False},
            {"name": "researcher3", "admin": False, "grafana": True, "display_name": "R Three"},
        ],
        researchers_removed=["researcher4"],
    )
    assert evaluate("observability_grafana_researchers", variables) == [
        "researcher1",
        "researcher3",
    ]
    assert evaluate("observability_grafana_disabled", variables) == ["researcher2", "researcher4"]
    name = (
        "(researchers | selectattr('name', 'equalto', item.item) | first).display_name"
        " | default(item.item, true)"
    )
    assert evaluate(name, {**variables, "item": {"item": "researcher3"}}) == "R Three"
    assert evaluate(name, {**variables, "item": {"item": "researcher1"}}) == "researcher1"
    tasks = (ROLES / "observability" / "tasks" / "grafana_users.yml").read_text(encoding="utf-8")
    assert name in tasks  # the expression the task uses


def split_vars(node_role: str, peer: str = "") -> dict[str, Any]:
    """Variables of a split-inference node, paired with ``peer`` if given."""
    variables = obs_vars(gpu=node_role == "inference", node_role=node_role, vm=True)
    if node_role == "inference":
        variables.update(inference_lab_node=peer, observability_forward_address="100.64.0.1")
    else:
        variables.update(inference_remote_node=peer, observability_tailnet_address="100.64.0.1")
    return variables


def gateway(variables: dict[str, Any]) -> Any:
    return yaml.safe_load(render_file(CONFIG / "otel/gateway.yaml.j2", variables))


PAIRING_TLS = {
    "cert_file": "/etc/pinka-pairing/self.crt",
    "key_file": "/etc/pinka-pairing/self.key",
    "min_version": "1.3",
}


def test_inference_node_forwards_only_once_paired() -> None:
    unpaired = gateway(split_vars("inference"))
    assert set(unpaired["exporters"]) == {"nop"}  # nothing leaves an unpaired node
    for pipeline in unpaired["service"]["pipelines"].values():
        assert pipeline["exporters"] == ["nop"]
    paired = gateway(split_vars("inference", "pinka-lab"))
    assert set(paired["exporters"]) == {"otlp_grpc/lab"}
    lab = paired["exporters"]["otlp_grpc/lab"]
    assert lab["endpoint"] == "100.64.0.1:4317"
    assert lab["tls"] == {
        **PAIRING_TLS,
        "ca_file": "/etc/pinka-pairing/peer.crt",
        "server_name_override": "pinka-lab",
    }
    assert set(paired["service"]["pipelines"]) == {"logs", "metrics", "traces"}
    for name, pipeline in paired["service"]["pipelines"].items():
        assert pipeline["exporters"] == ["otlp_grpc/lab"], name
        assert pipeline["processors"] == ["memory_limiter", "resource", "batch"], name
    # No VM listener (no Incus on an inference node); the exporters are scraped locally.
    assert set(paired["receivers"]) == {"otlp", "prometheus"}
    assert paired["service"]["pipelines"]["metrics"]["receivers"] == ["otlp", "prometheus"]
    jobs = paired["receivers"]["prometheus"]["config"]["scrape_configs"]
    assert {job["job_name"] for job in jobs} == {
        "otel-collector",
        "node",
        "cadvisor",
        "gpu",
        "vllm",
    }
    for job in jobs:
        for group in job["static_configs"]:
            assert all(t.startswith("127.0.0.1:") for t in group["targets"]), job


def test_lab_node_receives_the_paired_node_over_mutual_tls() -> None:
    assert "otlp/inference" not in gateway(split_vars("lab"))["receivers"]
    paired = gateway(split_vars("lab", "pinka-gpu"))
    receiver = paired["receivers"]["otlp/inference"]["protocols"]
    assert set(receiver) == {"grpc"}
    assert receiver["grpc"]["endpoint"] == "100.64.0.1:4317"
    assert receiver["grpc"]["tls"] == {
        **PAIRING_TLS,
        "client_ca_file": "/etc/pinka-pairing/peer.crt",
    }
    attributes = {a["key"]: a for a in paired["processors"]["resource/inference"]["attributes"]}
    assert attributes["pinka.machine"] == {
        "key": "pinka.machine",
        "value": "pinka-gpu",
        "action": "upsert",
    }
    pipelines = paired["service"]["pipelines"]
    expected = {"logs": "otlp_http/loki", "metrics": "prometheus", "traces": "otlp_grpc/tempo"}
    for signal, exporter in expected.items():
        local, remote = pipelines[signal], pipelines[f"{signal}/inference"]
        assert "otlp/inference" not in local["receivers"]
        assert remote["receivers"] == ["otlp/inference"]
        assert remote["processors"] == ["memory_limiter", "resource/inference", "batch"]
        assert local["exporters"] == remote["exporters"] == [exporter]


def test_stores_by_node_role() -> None:
    for role, stores in (("full", True), ("lab", True), ("inference", False)):
        assert evaluate("observability_stores", obs_vars(node_role=role)) is stores
    assert evaluate("observability_vm_listener", obs_vars(vm=True, node_role="inference")) is False
    assert evaluate("observability_vm_listener", obs_vars(vm=True, node_role="lab")) is True
    compose = load_yaml(INFRA / "compose/observability/compose.yml")
    assert evaluate("observability_gateway_account", obs_vars()) == "pinka-otelcol"
    assert evaluate("observability_pairing_dir", obs_vars()).endswith(
        compose["services"]["otel_gateway"]["volumes"][1].split(":")[0].rsplit("/", 1)[1]
    )


def test_the_stores_run_as_infra_accounts() -> None:
    # ADR-0016 as decided with the "commit" for 3d.4 and 3d.4b: the telemetry
    # guard names exactly infra's accounts, so no container that reaches the
    # stores or the query port runs as its image's default user (nobody,
    # 10001, 472); Grafana is a client of the query port only.
    compose = load_yaml(INFRA / "compose/observability/compose.yml")["services"]
    variables = obs_vars()
    accounts = evaluate("observability_container_accounts", variables)
    infra = {a["name"] for a in evaluate("infra_accounts", variables)}
    clients = {c["account"] for c in evaluate("pinka_telemetry_clients", variables)}
    query = {c["account"] for c in evaluate("pinka_query_port_clients", variables)}
    assert set(accounts.values()) <= infra
    assert set(accounts.values()) == (clients | query) - {"root"}
    assert accounts["grafana"] == "pinka-grafana"
    assert "pinka-grafana" not in clients
    for service, account in accounts.items():
        user = compose[service]["user"]
        assert re.match(r"^\$\{[A-Z_]+_UID:\?\}:", user), service
        assert account in infra, service
    assert compose["grafana"]["user"] == "${GRAFANA_UID:?}:0"
    dirs = {d["name"]: d for d in evaluate("observability_state_dirs", variables)}
    assert {dirs[n]["container"] for n in ("prometheus", "loki", "tempo", "grafana")} == {
        "prometheus",
        "loki",
        "tempo",
        "grafana",
    }
    secrets = (ROLES / "observability" / "tasks" / "secrets.yml").read_text(encoding="utf-8")
    assert '"472"' not in secrets and '"65534"' not in secrets


# --- the agent OTLP socket and the query proxy (§7.9) ------------------------------------


def test_the_agent_socket_telemetry_is_stamped_untrusted() -> None:
    config = rendered("otel/gateway.yaml")
    agent = config["receivers"]["otlp/agent"]["protocols"]
    assert agent == {"http": {"endpoint": "127.0.0.1:18732"}}
    stamp = config["processors"]["resource/untrusted"]["attributes"]
    assert stamp == [{"key": "pinka.trust", "value": "untrusted", "action": "upsert"}]
    pipelines = config["service"]["pipelines"]
    for signal in ("logs", "metrics", "traces"):
        pipeline = pipelines[f"{signal}/agent"]
        assert pipeline["receivers"] == ["otlp/agent"]
        # After the lab's own resource attributes, so the stamp always wins.
        assert pipeline["processors"] == [
            "memory_limiter",
            "resource",
            "resource/untrusted",
            "batch",
        ]
        assert pipeline["exporters"] == pipelines[signal]["exporters"]
        assert "otlp/agent" not in pipelines[signal]["receivers"]
    inference = rendered("otel/gateway.yaml", node_role="inference")
    assert "otlp/agent" not in inference["receivers"]


def test_loki_indexes_the_trust_and_the_platform_labels() -> None:
    def labels(platform: dict[str, Any]) -> Any:
        variables = obs_vars()
        variables["active_platform"] = platform
        config = yaml.safe_load(render_file(CONFIG / "loki/loki.yaml.j2", variables))
        return config["limits_config"]["otlp_config"]["resource_attributes"]["attributes_config"]

    assert labels({**PLATFORM, "telemetry_labels": ["run.id", "task.id"]}) == [
        {"action": "index_label", "attributes": ["pinka.trust", "run.id", "task.id"]}
    ]
    assert labels({}) == [{"action": "index_label", "attributes": ["pinka.trust"]}]


def test_agent_socket_units() -> None:
    variables = obs_vars()
    socket = configparser.ConfigParser(interpolation=None, strict=True)
    socket.optionxform = str  # type: ignore[assignment,method-assign]
    socket.read_string(
        render_file(ROLES / "observability/templates/pinka-otlp-agent.socket.j2", variables)
    )
    assert socket["Socket"]["ListenStream"] == "/run/pinka/otlp/agent.sock"
    assert socket["Socket"]["SocketMode"] == "0666"  # sending is all it allows
    service = configparser.ConfigParser(interpolation=None, strict=True)
    service.optionxform = str  # type: ignore[assignment,method-assign]
    service.read_string(
        render_file(ROLES / "observability/templates/pinka-otlp-agent.service.j2", variables)
    )
    assert service["Service"]["ExecStart"] == (
        "/usr/lib/systemd/systemd-socket-proxyd --exit-idle-time=300 127.0.0.1:18732"
    )
    assert service["Service"]["DynamicUser"] == "yes"  # outside the platform's UID range
    assert service["Service"]["IPAddressAllow"] == "localhost"
    assert service["Service"]["IPAddressDeny"] == "any"


def test_query_socket_directory_and_link() -> None:
    text = render_file(ROLES / "observability/templates/pinka-query.tmpfiles.conf.j2", obs_vars())
    lines = [line for line in text.splitlines() if line and not line.startswith("#")]
    assert "d /run/pinka/query 0750 pinka-query-proxy pinka-query -" in lines
    assert "L+ /run/pinka/query.sock - - - - query/query.sock" in lines


def test_the_query_proxy_container() -> None:
    proxy = load_yaml(INFRA / "compose/observability/compose.yml")["services"]["query_proxy"]
    assert proxy["user"] == "${QUERY_PROXY_UID:?}:${PINKA_QUERY_GID:?}"
    assert proxy["volumes"] == [
        "./config/query-proxy:/etc/query-proxy:ro",
        "/run/pinka/query:/run/query",
    ]
    command = proxy["entrypoint"][-1]
    assert command.startswith("umask 0117 && ")  # the socket becomes 0660
    assert "rm -f /run/query/query.sock" in command


def proxy_locations() -> list[tuple[re.Pattern[str], set[str], str]]:
    text = render_file(CONFIG / "query-proxy/nginx.conf.j2", obs_vars())
    found = []
    for match in re.finditer(
        r"location ~ (\S+) \{\s*limit_except ([A-Z ]+) \{ deny all; \}\s*"
        r"rewrite \S+ \$1 break;\s*proxy_pass (\S+);",
        text,
    ):
        found.append((re.compile(match.group(1)), set(match.group(2).split()), match.group(3)))
    return found


def proxied(method: str, path: str) -> str | None:
    for pattern, methods, upstream in proxy_locations():
        if pattern.match(path):
            return (
                upstream if method in methods or (method == "HEAD" and "GET" in methods) else None
            )
    return None


ALLOWED = [
    ("GET", "/prometheus/api/v1/query", "http://127.0.0.1:18701"),
    ("POST", "/prometheus/api/v1/query_range", "http://127.0.0.1:18701"),
    ("GET", "/prometheus/api/v1/series", "http://127.0.0.1:18701"),
    ("GET", "/prometheus/api/v1/labels", "http://127.0.0.1:18701"),
    ("GET", "/prometheus/api/v1/label/job/values", "http://127.0.0.1:18701"),
    ("GET", "/loki/loki/api/v1/query", "http://127.0.0.1:18702"),
    ("POST", "/loki/loki/api/v1/query_range", "http://127.0.0.1:18702"),
    ("GET", "/loki/loki/api/v1/series", "http://127.0.0.1:18702"),
    ("GET", "/loki/loki/api/v1/labels", "http://127.0.0.1:18702"),
    ("GET", "/loki/loki/api/v1/label/job/values", "http://127.0.0.1:18702"),
    ("GET", "/tempo/api/traces/0123456789abcdef", "http://127.0.0.1:18703"),
    ("GET", "/tempo/api/v2/traces/0123456789abcdef", "http://127.0.0.1:18703"),
    ("GET", "/tempo/api/search", "http://127.0.0.1:18703"),
    ("GET", "/tempo/api/search/tags", "http://127.0.0.1:18703"),
    ("GET", "/tempo/api/search/tag/service.name/values", "http://127.0.0.1:18703"),
    ("GET", "/tempo/api/v2/search/tags", "http://127.0.0.1:18703"),
    ("GET", "/tempo/api/v2/search/tag/service.name/values", "http://127.0.0.1:18703"),
]
# Push and remote write, deletion, admin, configuration, flush, rules and
# alerting, and the query paths with another method (the pinned releases'
# routes, see the proxy's configuration).
REFUSED = [
    ("POST", "/prometheus/api/v1/write"),
    ("POST", "/prometheus/api/v1/otlp/v1/metrics"),
    ("POST", "/prometheus/api/v1/read"),
    ("POST", "/prometheus/api/v1/admin/tsdb/delete_series"),
    ("PUT", "/prometheus/api/v1/admin/tsdb/snapshot"),
    ("GET", "/prometheus/api/v1/status/config"),
    ("GET", "/prometheus/api/v1/targets"),
    ("GET", "/prometheus/api/v1/rules"),
    ("GET", "/prometheus/api/v1/alerts"),
    ("DELETE", "/prometheus/api/v1/series"),
    ("POST", "/prometheus/-/quit"),
    ("POST", "/loki/loki/api/v1/push"),
    ("POST", "/loki/otlp/v1/logs"),
    ("POST", "/loki/loki/api/v1/delete"),
    ("GET", "/loki/loki/api/v1/tail"),
    ("GET", "/loki/loki/api/v1/rules"),
    ("POST", "/loki/flush"),
    ("GET", "/loki/config"),
    ("PUT", "/loki/loki/api/v1/query"),
    ("POST", "/tempo/api/search"),
    ("POST", "/tempo/v1/traces"),
    ("GET", "/tempo/api/overrides"),
    ("POST", "/tempo/flush"),
    ("GET", "/tempo/status/config"),
    ("GET", "/prometheus/api/v1/label/a/b/values"),
    ("GET", "/prometheus/api/v1/query/../admin"),
    ("GET", "/api/v1/query"),
]


@pytest.mark.parametrize(("method", "path", "upstream"), ALLOWED)
def test_the_query_proxy_forwards_the_query_endpoints(
    method: str, path: str, upstream: str
) -> None:
    assert proxied(method, path) == upstream


@pytest.mark.parametrize(("method", "path"), REFUSED)
def test_the_query_proxy_refuses_everything_else(method: str, path: str) -> None:
    assert proxied(method, path) is None


def test_the_query_proxy_listens_on_its_socket_and_loopback_only() -> None:
    text = render_file(CONFIG / "query-proxy/nginx.conf.j2", obs_vars())
    listens = re.findall(r"^\s*listen (\S+);", text, re.MULTILINE)
    assert listens == ["unix:/run/query/query.sock", "127.0.0.1:18733"]
    assert "location / {\n            return 403;" in text
    assert len(proxy_locations()) == 3  # every location is parsed by the tests above
    assert "proxy_set_header Authorization" not in text  # no credentials added
