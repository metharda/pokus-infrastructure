# Managed by pokus-infra (ansible/roles/incus/library/pokus_incus_object.py). Local changes will be overwritten.
"""Ansible module: converge one Incus object through the local REST API.

Incus has no declarative, idempotent command for single objects, and
``incus admin init --preseed`` updates every object it lists on every run.
This module reads the object with ``incus query`` (the CLI's raw API client),
compares only the fields it manages, and creates or updates the object when
they differ, so a second run reports no change. It never deletes anything.
"""

from __future__ import annotations

import base64
import hashlib
import json
import ssl
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import quote, urlencode

DOCUMENTATION = r"""
module: pokus_incus_object
short_description: Create or update one Incus object (never deletes)
description:
  - Reads the object with C(incus query), compares the managed fields and
    creates or updates it. Supports check mode and diff mode.
options:
  kind:
    description: Object kind.
    type: str
    required: true
    choices: [server, storage_pool, network, network_acl, project, profile, certificate]
  name:
    description: Object name (for certificates the trust store name). Not used for C(server).
    type: str
  project:
    description: Project of a profile.
    type: str
    default: default
  description:
    description: Object description.
    type: str
  config:
    description: Configuration keys to manage; other keys are left alone. Values are sent as strings.
    type: dict
    default: {}
  devices:
    description: Devices of a profile (managed as a whole).
    type: dict
  ingress:
    description: Ingress rules of a network ACL (managed as a whole).
    type: list
    elements: dict
  egress:
    description: Egress rules of a network ACL (managed as a whole).
    type: list
    elements: dict
  type:
    description: Network type, or certificate type (C(client) or C(metrics)).
    type: str
  driver:
    description: Storage pool driver (creation only).
    type: str
  certificate:
    description: PEM certificate to trust (certificates only).
    type: str
  restricted:
    description: Restrict a client certificate to C(projects).
    type: bool
    default: false
  projects:
    description: Projects a restricted certificate may use.
    type: list
    elements: str
    default: []
  update:
    description: Update an existing object; false only creates missing objects.
    type: bool
    default: true
"""

RETURN = r"""
action:
  description: C(none), C(create) or C(update).
  type: str
  returned: always
"""

COLLECTIONS = {
    "storage_pool": "/1.0/storage-pools",
    "network": "/1.0/networks",
    "network_acl": "/1.0/network-acls",
    "project": "/1.0/projects",
    "profile": "/1.0/profiles",
    "certificate": "/1.0/certificates",
}

Query = Callable[[str, str, Any], Any]


class IncusError(Exception):
    """An ``incus query`` call failed."""


def as_incus_value(value: Any) -> str:  # noqa: ANN401 - YAML value
    """Convert a YAML value to the string form Incus stores.

    Args:
        value: Value from the playbook.

    Returns:
        ``true``/``false`` for booleans, the string form otherwise.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def normalize(mapping: Mapping[str, Any] | None) -> dict[str, Any]:
    """Convert config values (and nested device values) to strings.

    Args:
        mapping: Config or devices.

    Returns:
        A copy with string values.
    """
    result: dict[str, Any] = {}
    for key, value in (mapping or {}).items():
        result[str(key)] = normalize(value) if isinstance(value, Mapping) else as_incus_value(value)
    return result


def fingerprint(pem: str) -> str:
    """Return the sha256 fingerprint Incus uses for a certificate.

    Args:
        pem: PEM-encoded certificate.

    Returns:
        Lower-case hex digest of the DER encoding.
    """
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()


def object_path(kind: str, key: str, project: str | None) -> str:
    """Build the API path of one object.

    Args:
        kind: Object kind.
        key: Name, or fingerprint for certificates.
        project: Project of profiles, else None.

    Returns:
        The path with the project parameter when needed.
    """
    path = f"{COLLECTIONS[kind]}/{quote(key, safe='')}"
    return f"{path}?{urlencode({'project': project})}" if project else path


def post_path(kind: str, project: str | None) -> str:
    """Build the API path that creates an object.

    Args:
        kind: Object kind.
        project: Project of profiles, else None.

    Returns:
        The collection path with the project parameter when needed.
    """
    path = COLLECTIONS[kind]
    return f"{path}?{urlencode({'project': project})}" if project else path


def collection_path(kind: str, project: str | None) -> str:
    """Build the API path that lists a collection with full objects.

    Args:
        kind: Object kind.
        project: Project of profiles, else None.

    Returns:
        The path with recursion (and the project parameter when needed).
    """
    params = {"recursion": "1"} | ({"project": project} if project else {})
    return f"{COLLECTIONS[kind]}?{urlencode(params)}"


def find(query: Query, kind: str, params: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return the current object, or None if it does not exist.

    Args:
        query: Function running ``incus query``.
        kind: Object kind.
        params: Module parameters.

    Returns:
        The object as the API returns it.
    """
    project = params["project"] if kind == "profile" else None
    items = query("GET", collection_path(kind, project), None) or []
    if kind == "certificate":
        wanted = fingerprint(params["certificate"])
        return next((i for i in items if i.get("fingerprint") == wanted), None)
    return next((i for i in items if i.get("name") == params["name"]), None)


def rules(items: Any) -> list[dict[str, str]]:  # noqa: ANN401 - YAML list
    """Normalize ACL rules: string values, empty fields dropped.

    Args:
        items: Rules from the playbook or from the API.

    Returns:
        Comparable rules.
    """
    return [
        {k: as_incus_value(v) for k, v in rule.items() if v not in ("", None)}
        for rule in items or []
    ]


def desired_fields(kind: str, params: Mapping[str, Any]) -> dict[str, Any]:
    """Return the managed fields of an object as the API represents them.

    Args:
        kind: Object kind.
        params: Module parameters.

    Returns:
        Field name to value.
    """
    fields: dict[str, Any] = {}
    if params.get("description") is not None:
        fields["description"] = params["description"]
    if kind == "certificate":
        fields.update(
            name=params["name"],
            type=params.get("type") or "client",
            restricted=bool(params["restricted"]),
            projects=list(params["projects"]) if params["restricted"] else [],
        )
        return fields
    fields["config"] = normalize(params.get("config"))
    if kind == "profile" and params.get("devices") is not None:
        fields["devices"] = normalize(params["devices"])
    if kind == "network_acl":
        fields["ingress"] = rules(params.get("ingress"))
        fields["egress"] = rules(params.get("egress"))
    return fields


def differences(current: Mapping[str, Any], wanted: Mapping[str, Any]) -> dict[str, Any]:
    """Return the managed fields whose current value differs.

    ``config`` is compared key by key (unmanaged keys are ignored); every other
    field is compared as a whole.

    Args:
        current: Object as the API returns it.
        wanted: Result of :func:`desired_fields`.

    Returns:
        Field name to wanted value, only for fields that differ.
    """
    changed: dict[str, Any] = {}
    for field, value in wanted.items():
        if field == "config":
            have = current.get("config") or {}
            delta = {k: v for k, v in value.items() if have.get(k) != v}
            if delta:
                changed["config"] = delta
        elif field == "projects":
            if sorted(current.get("projects") or []) != sorted(value):
                changed[field] = value
        elif field in ("ingress", "egress"):
            if rules(current.get(field)) != value:
                changed[field] = value
        elif current.get(field) != value:
            changed[field] = value
    return changed


def create_body(kind: str, params: Mapping[str, Any], wanted: Mapping[str, Any]) -> dict[str, Any]:
    """Build the POST body that creates the object.

    Args:
        kind: Object kind.
        params: Module parameters.
        wanted: Result of :func:`desired_fields`.

    Returns:
        Request body.
    """
    body: dict[str, Any] = {**wanted}
    if kind == "certificate":
        der = ssl.PEM_cert_to_DER_cert(params["certificate"])
        body["certificate"] = base64.b64encode(der).decode()
        return body
    body["name"] = params["name"]
    if kind == "network" and params.get("type"):
        body["type"] = params["type"]
    if kind == "storage_pool":
        body["driver"] = params["driver"]
    return body


def update_body(current: Mapping[str, Any], wanted: Mapping[str, Any]) -> dict[str, Any]:
    """Build a PUT body: the current writable fields with the managed ones replaced.

    Args:
        current: Object as the API returns it.
        wanted: Result of :func:`desired_fields`.

    Returns:
        Request body.
    """
    body: dict[str, Any] = {}
    for field in (
        "description",
        "config",
        "devices",
        "ingress",
        "egress",
        "name",
        "type",
        "restricted",
        "projects",
    ):
        if field in current:
            body[field] = current[field]
    for field, value in wanted.items():
        body[field] = {**(current.get("config") or {}), **value} if field == "config" else value
    return body


def converge(query: Query, params: Mapping[str, Any], check_mode: bool) -> dict[str, Any]:
    """Create or update one object.

    Args:
        query: Function running ``incus query`` (method, path, body) -> decoded JSON.
        params: Module parameters.
        check_mode: Report what would change without changing anything.

    Returns:
        Module result: ``changed``, ``action`` and ``diff``.
    """
    kind = params["kind"]
    if kind == "server":
        current = query("GET", "/1.0", None) or {}
        delta = differences(current, {"config": normalize(params.get("config"))})
        if delta and not check_mode:
            query("PATCH", "/1.0", {"config": delta["config"]})
        before = {k: (current.get("config") or {}).get(k) for k in delta.get("config", {})}
        return {
            "changed": bool(delta),
            "action": "update" if delta else "none",
            "diff": {"before": before, "after": delta.get("config", {})},
        }
    wanted = desired_fields(kind, params)
    current = find(query, kind, params)
    if current is None:
        if not check_mode:
            project = params["project"] if kind == "profile" else None
            query("POST", post_path(kind, project), create_body(kind, params, wanted))
        return {"changed": True, "action": "create", "diff": {"before": {}, "after": wanted}}
    delta = differences(current, wanted) if params["update"] else {}
    if delta and not check_mode:
        key = current["fingerprint"] if kind == "certificate" else params["name"]
        project = params["project"] if kind == "profile" else None
        query("PUT", object_path(kind, key, project), update_body(current, wanted))
    before = {k: current.get(k) for k in delta}
    return {
        "changed": bool(delta),
        "action": "update" if delta else "none",
        "diff": {"before": before, "after": delta},
    }


def main() -> None:  # pragma: no cover - exercised on hosts
    """Run the module."""
    from ansible.module_utils.basic import AnsibleModule  # noqa: PLC0415 - only on the host

    spec = {
        "kind": {"type": "str", "required": True, "choices": ["server", *COLLECTIONS]},
        "name": {"type": "str"},
        "project": {"type": "str", "default": "default"},
        "description": {"type": "str"},
        "config": {"type": "dict", "default": {}},
        "devices": {"type": "dict"},
        "ingress": {"type": "list", "elements": "dict"},
        "egress": {"type": "list", "elements": "dict"},
        "type": {"type": "str"},
        "driver": {"type": "str"},
        "certificate": {"type": "str"},
        "restricted": {"type": "bool", "default": False},
        "projects": {"type": "list", "elements": "str", "default": []},
        "update": {"type": "bool", "default": True},
    }
    module = AnsibleModule(
        argument_spec=spec,
        supports_check_mode=True,
        required_if=[
            ("kind", "storage_pool", ["name", "driver"]),
            ("kind", "certificate", ["name", "certificate"]),
            ("kind", "network", ["name"]),
            ("kind", "network_acl", ["name"]),
            ("kind", "project", ["name"]),
            ("kind", "profile", ["name"]),
        ],
    )

    def query(method: str, path: str, body: Any) -> Any:  # noqa: ANN401 - JSON
        argv = ["incus", "query", "-X", method]
        if method != "GET":
            argv.append("--wait")
        if body is not None:
            argv += ["-d", json.dumps(body)]
        rc, out, err = module.run_command([*argv, path])
        if rc != 0:
            raise IncusError(f"incus query {method} {path}: {err.strip() or out.strip()}")
        return json.loads(out) if out.strip() else None

    try:
        result = converge(query, module.params, module.check_mode)
    except (IncusError, ValueError) as exc:
        module.fail_json(msg=str(exc))
    module.exit_json(**result)


if __name__ == "__main__":
    main()
