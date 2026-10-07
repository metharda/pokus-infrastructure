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
    gateway = compose["services"]["otel_gateway"]
    # No host files besides the gateway's own read-only copy of the pairing material.
    assert gateway["volumes"] == [
        "./config/otel:/etc/otelcol:ro",
        "/etc/pokus/secrets/containers/otel-pairing:/etc/pokus-pairing:ro",
    ]
    assert gateway["user"] == "10001:10001"
    stores = {
        name
        for name, service in compose["services"].items()
        if "stores" in service.get("profiles", [])
    }
    assert stores == {"prometheus", "loki", "tempo", "grafana"}


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
    "cert_file": "/etc/pokus-pairing/self.crt",
    "key_file": "/etc/pokus-pairing/self.key",
    "min_version": "1.3",
}


def test_inference_node_forwards_only_once_paired() -> None:
    unpaired = gateway(split_vars("inference"))
    assert set(unpaired["exporters"]) == {"nop"}  # nothing leaves an unpaired node
    for pipeline in unpaired["service"]["pipelines"].values():
        assert pipeline["exporters"] == ["nop"]
    paired = gateway(split_vars("inference", "pokus-lab"))
    assert set(paired["exporters"]) == {"otlp_grpc/lab"}
    lab = paired["exporters"]["otlp_grpc/lab"]
    assert lab["endpoint"] == "100.64.0.1:4317"
    assert lab["tls"] == {
        **PAIRING_TLS,
        "ca_file": "/etc/pokus-pairing/peer.crt",
        "server_name_override": "pokus-lab",
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
    paired = gateway(split_vars("lab", "pokus-gpu"))
    receiver = paired["receivers"]["otlp/inference"]["protocols"]
    assert set(receiver) == {"grpc"}
    assert receiver["grpc"]["endpoint"] == "100.64.0.1:4317"
    assert receiver["grpc"]["tls"] == {
        **PAIRING_TLS,
        "client_ca_file": "/etc/pokus-pairing/peer.crt",
    }
    attributes = {a["key"]: a for a in paired["processors"]["resource/inference"]["attributes"]}
    assert attributes["pokus.machine"] == {
        "key": "pokus.machine",
        "value": "pokus-gpu",
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
    uid = evaluate("observability_gateway_uid", obs_vars())
    assert compose["services"]["otel_gateway"]["user"] == f"{uid}:{uid}"
    assert evaluate("observability_pairing_dir", obs_vars()).endswith(
        compose["services"]["otel_gateway"]["volumes"][1].split(":")[0].rsplit("/", 1)[1]
    )
