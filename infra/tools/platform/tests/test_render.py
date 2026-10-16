"""Golden files of the principals and endpoints files (§12.3), and the TOML writer."""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from platform_testlib import CONTRACT, GOLDEN, VALID, manifest

from pinka_platform import names, render, toml

# The schemas the contract publishes for the two files (generated reference
# "Principals and endpoints files").
SCHEMAS = Path(render.__file__).resolve().parents[3] / "contract"
PORTS = {
    "otlp_grpc": 4317,
    "otlp_http": 4318,
    "opa": 8181,
    "incus_https": 8443,
    "grafana": 18700,
    "prometheus": 18701,
    "loki": 18702,
    "tempo": 18703,
    "vllm": 18704,
    "gateway_adhoc": 18705,
    "docs_serve": 18706,
    "inference_control": 18710,
}
FACTS = render.LabFacts(
    machine_name="pinka-lab",
    lab_mode="production",
    node_role="full",
    ports=PORTS,
    cpuset_targets="8-15",
)


def uids(data: dict[str, Any]) -> dict[str, int]:
    members = [*data["services"], *data.get("jobs", [])]
    return {names.account(data["name"], m["name"]): 2001 + i for i, m in enumerate(members)}


def golden(name: str, text: str) -> None:
    path = GOLDEN / name
    if os.environ.get("PINKA_UPDATE_SNAPSHOTS") == "1":
        path.write_text(text, encoding="utf-8")
    assert path.is_file(), f"{path} is missing: run with PINKA_UPDATE_SNAPSHOTS=1 and review it"
    assert text == path.read_text(encoding="utf-8"), f"{name} differs from its golden file"


@pytest.mark.parametrize("fixture", VALID)
def test_principals_golden(fixture: str) -> None:
    data = manifest(fixture)
    text = render.principals_json(render.principals(data, CONTRACT, uids(data)))
    golden(f"{fixture}.principals.json", text)


@pytest.mark.parametrize("fixture", VALID)
def test_endpoints_golden(fixture: str) -> None:
    text = render.endpoints_toml(render.endpoints(manifest(fixture), FACTS))
    golden(f"{fixture}.endpoints.toml", text)


@pytest.mark.parametrize("fixture", VALID)
def test_endpoints_toml_reads_back(fixture: str) -> None:
    document = render.endpoints(manifest(fixture), FACTS)
    assert tomllib.loads(render.endpoints_toml(document)) == document


def schema(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((SCHEMAS / f"{name}.schema.json").read_text(encoding="utf-8"))
    return data


@pytest.mark.parametrize("fixture", VALID)
def test_files_follow_the_published_schemas(fixture: str) -> None:
    data = manifest(fixture)
    principals = json.loads(render.principals_json(render.principals(data, CONTRACT, uids(data))))
    jsonschema.validate(principals, schema("principals"))
    for facts in (
        FACTS,
        render.LabFacts("pinka-lab", "production", "lab", PORTS, inference_mode="remote"),
        render.LabFacts("pinka-qa", "staging", "full", PORTS, "scripted", "http://127.0.0.1:9/v1"),
    ):
        endpoints = tomllib.loads(render.endpoints_toml(render.endpoints(data, facts)))
        jsonschema.validate(endpoints, schema("endpoints"))


def test_schemas_match_the_rendering() -> None:
    principals = schema("principals")["properties"]
    endpoints = schema("endpoints")["properties"]
    assert principals["schema_version"]["const"] == render.SCHEMA_VERSION
    assert principals["contract_version"]["const"] == CONTRACT.version
    assert endpoints["schema_version"]["const"] == render.SCHEMA_VERSION
    assert endpoints["inference"]["properties"]["mode"]["enum"] == list(render.INFERENCE_MODES)
    assert set(principals["groups"]["required"]) == set(CONTRACT.human_classes)
    # The schema describes exactly the keys the rendering writes: the allprof
    # fixture uses every facility, and a remote node adds the control URL.
    data = manifest("allprof")
    local = render.endpoints(data, FACTS)
    remote = render.endpoints(
        data, render.LabFacts("pinka-lab", "production", "lab", PORTS, inference_mode="remote")
    )
    assert set(local) == set(remote) == set(endpoints)
    for table, value in local.items():
        if isinstance(value, dict) and table != "ports":
            written = set(value) | set(remote[table])
            assert written == set(endpoints[table]["properties"]), table
    document = render.principals(data, CONTRACT, uids(data))
    assert set(document) | {"_comment"} == set(principals)
    for key in ("services", "jobs", "sockets"):
        assert set(document[key][0]) == set(principals[key]["items"]["properties"]), key
    clients = principals["sockets"]["items"]["properties"]["clients"]["properties"]
    assert set(document["sockets"][0]["clients"]) == set(clients)


def test_principals_lists_the_accounts_of_client_profiles() -> None:
    data = manifest("allprof")
    document = render.principals(data, CONTRACT, uids(data))
    sockets = {(s["service"], s["name"]): s for s in document["sockets"]}
    svc = sockets[("ctl", "svc")]
    assert svc["clients"]["accounts"] == [
        "allprof-brk",
        "allprof-obs",
        "allprof-rop",
        "allprof-rrd",
        "allprof-sbx",
    ]
    assert svc["owner"] == "allprof-ctl"
    assert svc["group"] == "allprof-ctl-svc"
    assert svc["path"] == "/run/pinka/platform/ctl/svc.sock"
    assert svc["fd_name"] == "svc"
    agent = sockets[("brk", "agent")]
    assert agent["agent_dedicated"]
    assert agent["clients"]["accounts"] == ["allprof-agt"]
    assert sockets[("ctl", "api")]["clients"]["human_groups"] == ["pinka", "pinka-admin"]
    assert document["groups"] == {"admins": "pinka-admin", "researchers": "pinka"}
    assert json.loads(render.principals_json(document))["platform"] == "allprof"


def test_principals_name_each_service_s_paths() -> None:
    data = manifest("allprof")
    services = {s["name"]: s for s in render.principals(data, CONTRACT, uids(data))["services"]}
    assert services["ctl"]["config"] == "/etc/pinka/platform/config/ctl.json"
    assert services["ctl"]["secrets_dir"] == "/etc/pinka/secrets/allprof-ctl"
    assert services["ctl"]["state"] == {"records": "/srv/pinka/platforms/allprof/state/ctl/records"}
    assert services["agt"]["config"] is None
    assert services["agt"]["secrets_dir"] is None


def test_endpoints_carry_only_the_required_facilities() -> None:
    document = render.endpoints(manifest("refplat"), FACTS)
    assert set(document) == {
        "schema_version",
        "platform",
        "lab",
        "postgres",
        "telemetry",
        "inference",
        "gpu",
        "signing",
        "attestations",
        "sessions",
        "ports",
    }
    assert "refplat_agent" not in document["postgres"]["roles"].values()
    data = manifest("refplat")
    data["requires"].remove("inference")
    assert "inference" not in render.endpoints(data, FACTS)
    assert document["signing"]["key_file"] == "/etc/pinka/signing/refplat/signing.key"


def test_endpoints_follow_port_overrides() -> None:
    ports = dict(PORTS, otlp_grpc=14317, opa=18181, vllm=28704)
    facts = render.LabFacts("pinka-lab", "production", "full", ports)
    document = render.endpoints(manifest("allprof"), facts)
    assert document["telemetry"]["otlp_grpc"] == "127.0.0.1:14317"
    assert document["telemetry"]["vm_collector_grpc"] == "198.18.0.1:14317"
    assert document["opa"] == {
        "url": "http://127.0.0.1:18181",
        "decision_path": "/v1/data/allprof/decide",
    }
    assert document["inference"]["api_url"] == "http://127.0.0.1:28704"
    assert document["ports"]["vllm"] == 28704


def test_remote_inference_goes_through_the_local_forwarder() -> None:
    facts = render.LabFacts("pinka-lab", "production", "lab", PORTS, inference_mode="remote")
    inference = render.endpoints(manifest("allprof"), facts)["inference"]
    assert inference == {
        "mode": "remote",
        "researcher_endpoint": "127.0.0.1:18705",
        "api_url": "http://127.0.0.1:18704",
        "control_url": "http://127.0.0.1:18710",
    }


def test_scripted_inference_only_on_staging() -> None:
    url = "http://127.0.0.1:9999/v1"
    staging = render.LabFacts("pinka-qa", "staging", "full", PORTS, "scripted", url)
    assert render.endpoints(manifest("allprof"), staging)["inference"]["api_url"] == url
    production = render.LabFacts("pinka-lab", "production", "full", PORTS, "scripted", url)
    with pytest.raises(ValueError, match="staging"):
        render.endpoints(manifest("allprof"), production)
    with pytest.raises(ValueError, match="unknown"):
        render.endpoints(
            manifest("allprof"), render.LabFacts("x", "staging", "full", PORTS, "other")
        )


def test_toml_writer() -> None:
    data = {"a": 1, "b": 'x"y', "t": {"c": True, "d": ["p", "q"], "u": {"e": 2}}, "v": {"w": {}}}
    text = toml.dumps(data, "header line\n\nsecond")
    assert text.startswith("# header line\n#\n# second\n")
    assert tomllib.loads(text) == data
    with pytest.raises(TypeError):
        toml.dumps({"a": 1.5})


GOLDEN_KINDS = (
    "principals.json",
    "endpoints.toml",
    # test_rendering.py
    "units.txt",
    "accounts.json",
    "bootstrap.sql",
    "restricted-check.sql",
    "render.json",
)


def test_golden_directory_has_no_stray_files() -> None:
    expected = {f"{f}.{kind}" for f in VALID for kind in GOLDEN_KINDS}
    assert {p.name for p in Path(GOLDEN).iterdir()} == expected
