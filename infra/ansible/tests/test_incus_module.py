"""Tests for the pokus_incus_object module against an in-memory Incus API."""

from __future__ import annotations

import base64
import copy
import datetime as dt
import hashlib
import json
import ssl
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

import pokus_incus_object as mod

DEFAULTS: dict[str, Any] = {
    "name": None,
    "project": "default",
    "description": None,
    "config": {},
    "devices": None,
    "ingress": None,
    "egress": None,
    "type": None,
    "driver": None,
    "certificate": None,
    "restricted": False,
    "projects": [],
    "update": True,
}


def params(**overrides: Any) -> dict[str, Any]:
    return {**DEFAULTS, **overrides}


class FakeIncus:
    """Just enough of the REST API: collections, recursion, project scoping, PUT, PATCH."""

    def __init__(self) -> None:
        self.server: dict[str, Any] = {"config": {"core.https_address": ""}}
        self.objects: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
        self.calls: list[tuple[str, str]] = []

    def store(self, collection: str, project: str) -> dict[str, dict[str, Any]]:
        return self.objects.setdefault((collection, project), {})

    def __call__(self, method: str, path: str, body: Any) -> Any:
        self.calls.append((method, path))
        url = urlsplit(path)
        query = parse_qs(url.query)
        project = query.get("project", ["default"])[0]
        parts = url.path.split("/")
        if url.path == "/1.0":
            if method == "PATCH":
                self.server["config"].update(body["config"])
            return copy.deepcopy(self.server)
        collection = "/".join(parts[:3])
        items = self.store(collection, project)
        if len(parts) == 3:
            if method == "GET":
                return [copy.deepcopy(v) for v in items.values()]
            obj = copy.deepcopy(body)
            if collection == "/1.0/certificates":
                der = base64.b64decode(obj["certificate"])
                key = obj["fingerprint"] = hashlib.sha256(der).hexdigest()
            else:
                key = obj["name"]
                obj.setdefault("config", {})["volatile.generated"] = "x"
            items[key] = obj
            return None
        key = unquote(parts[3])
        assert method == "PUT", method
        items[key] = {**items[key], **copy.deepcopy(body)}
        return None


def self_signed_pem() -> str:
    key = ec.generate_private_key(ec.SECP384R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "pokus-test")])
    now = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now)
        .not_valid_after(now + dt.timedelta(days=1))
        .sign(key, hashes.SHA384())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def test_create_then_no_change() -> None:
    api = FakeIncus()
    p = params(
        kind="project",
        name="bench",
        description="Benchmark runs",
        config={"restricted": True, "limits.instances": 20},
    )
    first = mod.converge(api, p, check_mode=False)
    assert first["changed"] and first["action"] == "create"
    stored = api.objects[("/1.0/projects", "default")]["bench"]
    assert stored["config"]["restricted"] == "true"
    assert stored["config"]["limits.instances"] == "20"
    second = mod.converge(api, p, check_mode=False)
    assert second == {"changed": False, "action": "none", "diff": {"before": {}, "after": {}}}


def test_update_keeps_unmanaged_keys() -> None:
    api = FakeIncus()
    mod.converge(
        api,
        params(kind="network", name="pokus-build0", type="bridge", config={"ipv4.nat": True}),
        check_mode=False,
    )
    result = mod.converge(
        api,
        params(kind="network", name="pokus-build0", config={"ipv4.nat": False}),
        check_mode=False,
    )
    assert result["action"] == "update"
    stored = api.objects[("/1.0/networks", "default")]["pokus-build0"]
    assert stored["config"] == {"ipv4.nat": "false", "volatile.generated": "x"}
    assert stored["type"] == "bridge"


def test_create_only_never_updates() -> None:
    api = FakeIncus()
    created = params(
        kind="network", name="incusbr-1001", config={"ipv4.address": "auto"}, update=False
    )
    mod.converge(api, created, check_mode=False)
    api.objects[("/1.0/networks", "default")]["incusbr-1001"]["config"]["ipv4.address"] = (
        "10.0.0.1/24"
    )
    result = mod.converge(api, created, check_mode=False)
    assert result["changed"] is False
    assert ("PUT", "/1.0/networks/incusbr-1001") not in api.calls


def test_profiles_are_project_scoped_and_devices_managed_whole() -> None:
    api = FakeIncus()
    devices = {"root": {"type": "disk", "path": "/", "pool": "pokus-targets", "size": "8GiB"}}
    p = params(
        kind="profile",
        name="target-container",
        project="bench",
        config={"security.guestapi": False},
        devices=devices,
    )
    mod.converge(api, p, check_mode=False)
    assert ("POST", "/1.0/profiles?project=bench") in api.calls
    assert "target-container" in api.objects[("/1.0/profiles", "bench")]
    assert mod.converge(api, p, check_mode=False)["changed"] is False
    api.objects[("/1.0/profiles", "bench")]["target-container"]["devices"]["eth9"] = {"type": "nic"}
    result = mod.converge(api, p, check_mode=False)
    assert result["action"] == "update"
    assert ("PUT", "/1.0/profiles/target-container?project=bench") in api.calls
    assert api.objects[("/1.0/profiles", "bench")]["target-container"]["devices"] == devices


def test_acl_rules_ignore_empty_fields() -> None:
    api = FakeIncus()
    rule = {
        "action": "allow",
        "state": "enabled",
        "destination": "192.0.2.1",
        "destination_port": 4317,
        "protocol": "tcp",
    }
    p = params(kind="network_acl", name="pokus-run-default", egress=[rule], ingress=[])
    mod.converge(api, p, check_mode=False)
    stored = api.objects[("/1.0/network-acls", "default")]["pokus-run-default"]
    # The API returns every rule field, unset ones as empty strings.
    stored["egress"][0].update({"source": "", "icmp_type": "", "description": ""})
    assert mod.converge(api, p, check_mode=False)["changed"] is False
    stored["egress"][0]["destination_port"] = "4318"
    assert mod.converge(api, p, check_mode=False)["action"] == "update"


def test_server_config_patch_and_check_mode() -> None:
    api = FakeIncus()
    p = params(kind="server", config={"core.https_address": "127.0.0.1:8443"})
    dry = mod.converge(api, p, check_mode=True)
    assert dry["changed"] and api.server["config"]["core.https_address"] == ""
    assert dry["diff"] == {
        "before": {"core.https_address": ""},
        "after": {"core.https_address": "127.0.0.1:8443"},
    }
    mod.converge(api, p, check_mode=False)
    assert api.server["config"]["core.https_address"] == "127.0.0.1:8443"
    assert mod.converge(api, p, check_mode=False)["changed"] is False


def test_check_mode_does_not_create() -> None:
    api = FakeIncus()
    result = mod.converge(api, params(kind="project", name="build"), check_mode=True)
    assert result["action"] == "create"
    assert [m for m, _ in api.calls] == ["GET"]


def test_certificates_by_fingerprint() -> None:
    pem = self_signed_pem()
    api = FakeIncus()
    p = params(
        kind="certificate",
        name="pokus-orch",
        certificate=pem,
        restricted=True,
        projects=["bench", "build"],
    )
    assert mod.converge(api, p, check_mode=False)["action"] == "create"
    ((fp, stored),) = api.objects[("/1.0/certificates", "default")].items()
    assert fp == mod.fingerprint(pem) == hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()
    assert stored["restricted"] is True and stored["type"] == "client"
    assert (
        mod.converge(api, {**p, "projects": ["build", "bench"]}, check_mode=False)["changed"]
        is False
    )
    result = mod.converge(api, {**p, "projects": ["bench"]}, check_mode=False)
    assert result["action"] == "update"
    assert api.objects[("/1.0/certificates", "default")][fp]["projects"] == ["bench"]
    metrics = mod.converge(
        api,
        params(kind="certificate", name="pokus-metrics", certificate=pem, type="metrics"),
        check_mode=True,
    )
    assert metrics["diff"]["after"]["type"] == "metrics"


def test_values_are_strings() -> None:
    assert mod.normalize({"a": True, "b": 3, "c": {"d": False}}) == {
        "a": "true",
        "b": "3",
        "c": {"d": "false"},
    }
    body = mod.create_body(
        "storage_pool",
        params(kind="storage_pool", name="pokus-targets", driver="btrfs"),
        {"config": {"size": "300GiB"}},
    )
    assert json.loads(json.dumps(body)) == {
        "config": {"size": "300GiB"},
        "name": "pokus-targets",
        "driver": "btrfs",
    }
