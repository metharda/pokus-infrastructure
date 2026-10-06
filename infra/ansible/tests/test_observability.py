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
    "grafana/provisioning/datasources/pokus.yaml",
    "grafana/provisioning/dashboards/pokus.yaml",
)
DASHBOARDS = sorted((CONFIG / "grafana" / "dashboards").glob("*.json"))
LISTEN_KEYS = re.compile(r"(endpoint|listen_address|http_listen_address|grpc_listen_address|host)$")


def obs_vars(vm: bool = False, gpu: bool = False, node_role: str = "full") -> dict[str, Any]:
    profile = fixture_profile("ubuntu2404-baremetal-nvidia-driver-loaded")
    variables: dict[str, Any] = {
        "host_profile": profile,
        "pokus_features": {**profile["features"], "vm_targets": vm, "gpu_inference": gpu},
        "node_role": node_role,
        "vault_postgres_passwords": {"pokus_grafana": "test"},
        "vault_grafana_admin_password": "test-admin",
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
    assert text.startswith(f"# Managed by pokus-infra (config/{name}.j2).")
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
    receivers = set(config["receivers"])
    assert receivers == ({"otlp", "otlp/vm"} if vm else {"otlp"})
    for name, pipeline in config["service"]["pipelines"].items():
        assert set(pipeline["receivers"]) == receivers, name
        assert pipeline["processors"] == ["memory_limiter", "resource", "batch"], name
        assert set(pipeline["exporters"]) <= set(config["exporters"]), name
    assert config["exporters"]["otlphttp/loki"]["endpoint"] == "http://127.0.0.1:18702/otlp"
    assert config["exporters"]["prometheus"]["resource_to_telemetry_conversion"]["enabled"]
    journald = rendered("otel/journald.yaml")
    assert journald["receivers"]["journald"]["root_path"] == "/hostfs"
    assert journald["exporters"]["otlp/gateway"]["endpoint"] == "127.0.0.1:4317"
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
    ports = list(variables["pokus_ports"].values()) + list(
        variables["pokus_internal_ports"].values()
    )
    assert len(ports) == len(set(ports))
    address = ipaddress.ip_address(variables["pokus_vm_otlp"]["address"])
    assert not address.is_global
    assert address in ipaddress.ip_network("198.18.0.0/15")  # RFC 2544 benchmarking range


def test_grafana_datasources() -> None:
    config = rendered("grafana/provisioning/datasources/pokus.yaml")
    sources = {d["uid"]: d for d in config["datasources"]}
    assert set(sources) == {"prometheus", "loki", "tempo", "postgres"}
    for uid in ("prometheus", "loki", "tempo"):
        assert sources[uid]["url"].startswith("http://127.0.0.1:"), uid
    postgres = sources["postgres"]
    assert postgres["url"] == "/run/pokus/postgres-reader"
    assert postgres["user"] == "pokus_grafana"
    secret = postgres["secureJsonData"]["password"]
    assert secret == "$__file{/run/secrets/pokus-grafana}"  # noqa: S105 - a file reference
    # "$$" is Grafana's escape for a literal "$".
    assert sources["loki"]["jsonData"]["derivedFields"][0]["url"] == "$${__value.raw}"


@pytest.mark.parametrize("path", DASHBOARDS, ids=lambda p: p.name)
def test_dashboards_use_provisioned_datasources(path: Path) -> None:
    dashboard = json.loads(path.read_text(encoding="utf-8"))
    assert dashboard["uid"] == f"pokus-{path.stem}"
    assert dashboard["description"].startswith(
        f"Managed by pokus-infra (config/grafana/dashboards/{path.name})."
    )
    assert dashboard["timezone"] == "utc"
    ids = [panel["id"] for panel in dashboard["panels"]]
    assert len(ids) == len(set(ids))
    for panel in dashboard["panels"]:
        for item in [panel, *panel["targets"]]:
            assert item["datasource"]["uid"] in {"prometheus", "loki", "tempo", "postgres"}


def test_expected_dashboards() -> None:
    assert [p.stem for p in DASHBOARDS] == [
        "gpu-vllm",
        "host-overview",
        "incus-targets",
        "lab-services",
        "run-explorer",
    ]
    explorer = json.loads((CONFIG / "grafana/dashboards/run-explorer.json").read_text())
    assert [v["name"] for v in explorer["templating"]["list"]] == ["run_id", "trace_id"]
    queries = json.dumps(explorer["panels"])
    assert 'pokus_run_id=\\"${run_id}\\"' in queries
    assert "resource.pokus.run_id" in queries


def test_vm_listener_unit() -> None:
    text = render_template("observability", "pokus-vm-otlp-address.service.j2", obs_vars(vm=True))
    unit = configparser.ConfigParser(interpolation=None, strict=False)
    unit.optionxform = str  # type: ignore[assignment,method-assign]
    unit.read_string(text)
    assert unit["Unit"]["Before"] == "docker.service"
    assert "ip address replace 198.18.0.1/32 dev pokus-otlp0" in text
    assert unit["Service"]["ExecStop"] == "ip link delete pokus-otlp0"


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
    gateway_mounts = compose["services"]["otel_gateway"]["volumes"]
    assert gateway_mounts == ["./config/otel:/etc/otelcol:ro"]  # no host files


def test_derived_flags() -> None:
    assert evaluate("observability_otlp_receivers", obs_vars(vm=True)) == ["otlp", "otlp/vm"]
    assert evaluate("observability_vllm", obs_vars(gpu=True, node_role="lab")) is False
    assert evaluate("observability_vllm", obs_vars(gpu=True, node_role="inference")) is True
