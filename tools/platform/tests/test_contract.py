"""The published contract files: schema, profiles and reserved names (§12, §13)."""

from __future__ import annotations

import copy
import json
import shutil
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import jsonschema
import pytest
import yaml
from platform_testlib import CONTRACT

from pinka_platform import contract as contract_mod
from pinka_platform.schema import SUPPORTED, SchemaError, check_keywords, errors

INFRA = contract_mod.DEFAULT_DIR.parent
SERVICE_PROFILES = {
    "controller",
    "inference-broker",
    "sandbox-operator",
    "restricted-operator",
    "restricted-reader",
    "observer",
    "untrusted-agent",
}


def test_schema_is_valid_draft_2020_12() -> None:
    jsonschema.Draft202012Validator.check_schema(CONTRACT.schema)
    assert CONTRACT.schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"


# Draft 2020-12 keywords whose value is a schema, an array of schemas or a
# mapping to schemas: the places a schema can hide below another one.
SUBSCHEMA_ONE = (
    "additionalProperties",
    "items",
    "contains",
    "not",
    "if",
    "then",
    "else",
    "propertyNames",
    "unevaluatedItems",
    "unevaluatedProperties",
    "contentSchema",
)
SUBSCHEMA_LIST = ("prefixItems", "allOf", "anyOf", "oneOf")
SUBSCHEMA_MAP = ("properties", "patternProperties", "dependentSchemas", "$defs", "definitions")


def schema_nodes(node: Any) -> Iterator[Mapping[str, Any]]:
    """Every schema object in a schema, found independently of the validator."""
    if not isinstance(node, Mapping):
        return
    yield node
    for key in SUBSCHEMA_ONE:
        if key in node:
            yield from schema_nodes(node[key])
    for key in SUBSCHEMA_LIST:
        for child in node.get(key, []):
            yield from schema_nodes(child)
    for key in SUBSCHEMA_MAP:
        for child in node.get(key, {}).values():
            yield from schema_nodes(child)


def test_schema_uses_only_keywords_the_validator_implements() -> None:
    # Owner decision on 3d.1: a schema change can never be silently ignored.
    used = {key for node in schema_nodes(CONTRACT.schema) for key in node}
    assert used <= SUPPORTED, sorted(used - SUPPORTED)
    assert check_keywords(CONTRACT.schema) == []


@pytest.mark.parametrize(
    "keyword", ["oneOf", "allOf", "format", "patternProperties", "if", "prefixItems"]
)
@pytest.mark.parametrize(
    "where", [(), ("properties", "services", "items"), ("$defs", "hook"), ("$defs", "command")]
)
def test_an_unimplemented_keyword_anywhere_is_caught(
    tmp_path: Path, keyword: str, where: tuple[str, ...]
) -> None:
    schema = copy.deepcopy(CONTRACT.schema)
    node = schema
    for key in where:
        node = node[key]
    node[keyword] = [{"type": "string"}] if keyword in SUBSCHEMA_LIST else {}
    assert check_keywords(schema) != []
    # The contract refuses to load, so no manifest is ever checked with it.
    shutil.copytree(contract_mod.DEFAULT_DIR, tmp_path / "contract")
    (tmp_path / "contract" / contract_mod.SCHEMA_FILE).write_text(json.dumps(schema))
    with pytest.raises(SchemaError, match=keyword):
        contract_mod.load(tmp_path / "contract")
    with pytest.raises(SchemaError):
        list(errors({}, node))


# One schema and one value that breaks it per assertion keyword: the
# validator rejects each, like the reference implementation.
KEYWORD_CASES: dict[str, tuple[dict[str, Any], Any]] = {
    "type": ({"type": "integer"}, "1"),
    "properties": ({"properties": {"a": {"type": "string"}}}, {"a": 1}),
    "required": ({"required": ["a"]}, {}),
    "additionalProperties": ({"properties": {}, "additionalProperties": False}, {"b": 1}),
    "items": ({"items": {"type": "string"}}, ["a", 1]),
    "enum": ({"enum": ["a", "b"]}, "c"),
    "const": ({"const": 2}, 2.5),
    "minimum": ({"minimum": 1}, 0),
    "maximum": ({"maximum": 1}, 2),
    "minLength": ({"minLength": 2}, "a"),
    "maxLength": ({"maxLength": 1}, "ab"),
    "pattern": ({"pattern": "^a+$"}, "ab"),
    "minItems": ({"minItems": 1}, []),
    "maxItems": ({"maxItems": 1}, [1, 2]),
    "uniqueItems": ({"uniqueItems": True}, [{"a": 1}, {"a": 1}]),
    "not": ({"not": {"const": "x"}}, "x"),
    "anyOf": ({"anyOf": [{"type": "string"}, {"minimum": 3}]}, 1),
    "$ref": ({"$defs": {"s": {"type": "string"}}, "$ref": "#/$defs/s"}, 1),
}
ANNOTATIONS = {"$schema", "$id", "$defs", "title", "description"}


def test_every_implemented_keyword_is_enforced() -> None:
    assert set(KEYWORD_CASES) | ANNOTATIONS == SUPPORTED
    for keyword, (schema, value) in KEYWORD_CASES.items():
        assert list(errors(value, schema)), keyword
        assert not jsonschema.Draft202012Validator(schema).is_valid(value), keyword


def test_schema_carries_the_reserved_lists() -> None:
    defs = CONTRACT.schema["$defs"]
    for name, namespace in CONTRACT.reserved.items():
        node = defs[f"reserved_{name}"]
        listed = node["enum"] if "enum" in node else node["anyOf"][0]["enum"]
        assert set(listed) == namespace.names, name
    assert CONTRACT.schema["properties"]["name"]["not"] == {
        "$ref": "#/$defs/reserved_platform_names"
    }


def test_exactly_the_eight_profiles() -> None:
    assert set(CONTRACT.profiles) == SERVICE_PROFILES | {"gpu-trainer"}
    assert set(CONTRACT.service_profiles) == SERVICE_PROFILES
    assert CONTRACT.profile("gpu-trainer").kind == "job"
    service_enum = set(
        CONTRACT.schema["properties"]["services"]["items"]["properties"]["profile"]["enum"]
    )
    assert service_enum == SERVICE_PROFILES


def test_profile_tables_are_complete() -> None:
    data = yaml.safe_load((contract_mod.DEFAULT_DIR / "profiles.yml").read_text(encoding="utf-8"))
    for name, profile in data["profiles"].items():
        assert set(profile["table"]) == set(data["columns"]), name


def test_restricted_data_is_closed_to_the_agent_and_the_trainer() -> None:
    # §12.4, invariant 15: restricted data only for three profiles.
    readers = {n for n, p in CONTRACT.profiles.items() if p.data["restricted_data"] == "read"}
    assert readers == {"controller", "restricted-operator", "restricted-reader"}
    schema_access = {
        n for n, p in CONTRACT.profiles.items() if p.data["restricted_schema"] == "allowed"
    }
    assert schema_access == readers


def test_the_agent_gets_nothing() -> None:
    # Invariant 4: no network, database, Incus, restricted data, secrets or relaxation.
    agent = CONTRACT.profile("untrusted-agent")
    assert agent.data["network"] == "none"
    assert not agent.flag("database_role")
    assert agent.data["incus_projects"] == []
    assert agent.data["restricted_data"] == "never"
    assert not agent.flag("secrets")
    assert not agent.flag("relaxations")
    assert not agent.flag("serves_sockets")
    assert agent.data["telemetry_send"] == "agent-socket"
    assert not agent.flag("telemetry_query")
    assert not agent.flag("inference_api")


def test_only_the_trainer_gets_gpu_devices() -> None:
    # Invariant 17.
    assert {n for n, p in CONTRACT.profiles.items() if p.flag("gpu_devices")} == {"gpu-trainer"}
    assert {n for n, p in CONTRACT.profiles.items() if p.flag("gpu_arbiter")} == {"controller"}


def test_inference_api_and_query_access() -> None:
    # Invariants 21 and 22, §12.2.
    assert {n for n, p in CONTRACT.profiles.items() if p.flag("inference_api")} == {
        "controller",
        "inference-broker",
    }
    assert {n for n, p in CONTRACT.profiles.items() if p.flag("telemetry_query")} == {
        "controller",
        "sandbox-operator",
        "restricted-reader",
        "observer",
    }


def test_incus_access() -> None:
    projects = {
        n: p.data["incus_projects"]
        for n, p in CONTRACT.profiles.items()
        if p.data["incus_projects"]
    }
    assert projects == {
        "controller": ["bench", "build"],
        "sandbox-operator": ["bench"],
        "restricted-operator": ["bench"],
    }
    assert {n for n, p in CONTRACT.profiles.items() if p.flag("run_network_helper")} == {
        "controller"
    }


def test_listening_ports() -> None:
    ports = {
        n: p.data["listen_port"] for n, p in CONTRACT.profiles.items() if p.data["listen_port"]
    }
    assert ports == {"inference-broker": 18705}


def test_socket_rules() -> None:
    # §12.3: restricted-* sockets accept only the controller and root (invariant 16).
    for name in ("restricted-operator", "restricted-reader"):
        rules = CONTRACT.profile(name).socket_clients
        assert rules == {"profiles": ["controller"], "humans": [], "root": True, "agent": "never"}
    dedicated = {
        n
        for n, p in CONTRACT.profiles.items()
        if (p.socket_clients or {}).get("agent") == "dedicated"
    }
    assert dedicated == {"controller", "inference-broker", "sandbox-operator"}
    for name, profile in CONTRACT.profiles.items():
        rules = profile.socket_clients or {}
        assert "untrusted-agent" not in rules.get("profiles", []), name
        assert set(rules.get("profiles", [])) <= SERVICE_PROFILES, name
        assert set(rules.get("humans", [])) <= set(CONTRACT.human_classes), name


def test_reserved_names_include_infra_groups() -> None:
    group_vars = yaml.safe_load(
        (INFRA / "ansible/group_vars/all/main.yml").read_text(encoding="utf-8")
    )
    infra_groups = {g["name"] for g in group_vars["pinka_groups"] if g["name"].startswith("pinka")}
    assert infra_groups <= CONTRACT.reserved["accounts_groups"].names
    assert CONTRACT.reserved["postgresql_roles"].reserves("pg_anything")
    assert CONTRACT.reserved["incus"].reserves("r-0123456789ab")
    assert CONTRACT.reserved["containers"].reserves("pinka-job-x")
    assert not CONTRACT.reserved["containers"].reserves("other-job-x")


def test_facilities_match_the_schema() -> None:
    requires = CONTRACT.schema["properties"]["requires"]["items"]["enum"]
    assert list(CONTRACT.facilities) == requires
    for facility, uses in CONTRACT.facilities.items():
        assert set(uses["profiles"]) <= set(CONTRACT.profiles), facility
        for field in uses["fields"]:
            node: Any = CONTRACT.schema
            for part in field.split("."):
                node = node["properties"][part]
            assert node, (facility, field)


def test_caps_parse() -> None:
    assert contract_mod.size_bytes(CONTRACT.caps["service_memory_max"]) > contract_mod.size_bytes(
        CONTRACT.defaults["service_memory_max"]
    )
    assert contract_mod.size_bytes("1K") == 1024
