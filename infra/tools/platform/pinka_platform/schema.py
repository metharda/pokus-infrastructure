"""A small JSON Schema (draft 2020-12) validator for the platform manifest.

The manifest validator runs on lab hosts with bootstrap's Python environment,
which carries PyYAML but not ``jsonschema``. This module implements exactly
the keywords ``contract/platform-manifest.schema.json`` uses; any other
keyword raises :class:`SchemaError`, so the schema cannot grow beyond what is
checked. The tests check every fixture against the reference ``jsonschema``
implementation as well.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from typing import Any

SUPPORTED = frozenset(
    {
        "$schema",
        "$id",
        "$defs",
        "$ref",
        "title",
        "description",
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "const",
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
        "pattern",
        "minItems",
        "maxItems",
        "uniqueItems",
        "not",
        "anyOf",
    }
)
_TYPES: dict[str, type | tuple[type, ...]] = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "null": type(None),
}


class SchemaError(Exception):
    """The schema uses a keyword this validator does not implement."""


def _is_type(value: Any, name: str) -> bool:  # noqa: ANN401 - a JSON value
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, _TYPES[name])


def _resolve(root: Mapping[str, Any], ref: str) -> Mapping[str, Any]:
    if not ref.startswith("#/"):
        raise SchemaError(f"unsupported $ref {ref!r}")
    node: Any = root
    for part in ref[2:].split("/"):
        node = node[part]
    if not isinstance(node, Mapping):
        raise SchemaError(f"$ref {ref!r} does not name a schema")
    return node


def _canonical(value: Any) -> str:  # noqa: ANN401 - a JSON value
    return json.dumps(value, sort_keys=True, default=str)


def _type_name(value: Any) -> str:  # noqa: ANN401 - a JSON value
    for name in ("boolean", "integer", "number", "string", "array", "object", "null"):
        if _is_type(value, name):
            return name
    return type(value).__name__


def errors(  # noqa: PLR0912, PLR0915 - one branch per keyword
    value: Any,  # noqa: ANN401 - a JSON value
    schema: Mapping[str, Any],
    root: Mapping[str, Any] | None = None,
    path: str = "",
) -> Iterator[tuple[str, str]]:
    """Validate a value against a schema node.

    Args:
        value: The value.
        schema: The schema node.
        root: The whole schema, for ``$ref``; defaults to ``schema``.
        path: Location of the value, for messages (empty: the whole manifest).

    Yields:
        ``(path, message)`` per violation.

    Raises:
        SchemaError: The schema uses an unsupported keyword.
    """
    root = schema if root is None else root
    unknown = set(schema) - SUPPORTED
    if unknown:
        raise SchemaError(f"unsupported schema keywords: {', '.join(sorted(unknown))}")
    if "$ref" in schema:
        yield from errors(value, _resolve(root, schema["$ref"]), root, path)
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_is_type(value, t) for t in types):
            yield path, f"is {_type_name(value)}, expected {' or '.join(types)}"
            return
    if "const" in schema and (
        value != schema["const"] or _type_name(value) != _type_name(schema["const"])
    ):
        yield path, f"must be {schema['const']!r}"
    if "enum" in schema and not any(
        value == option and _type_name(value) == _type_name(option) for option in schema["enum"]
    ):
        yield path, f"{value!r} is not one of {', '.join(repr(o) for o in schema['enum'])}"
    if "not" in schema and not list(errors(value, schema["not"], root, path)):
        yield path, f"{value!r} is not allowed here"
    if "anyOf" in schema and all(list(errors(value, s, root, path)) for s in schema["anyOf"]):
        yield path, f"{value!r} matches none of the allowed forms"
    if _is_type(value, "number"):
        if "minimum" in schema and value < schema["minimum"]:
            yield path, f"{value} is below the minimum {schema['minimum']}"
        if "maximum" in schema and value > schema["maximum"]:
            yield path, f"{value} is above the maximum {schema['maximum']}"
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            yield path, f"is shorter than {schema['minLength']} characters"
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            yield path, f"is longer than {schema['maxLength']} characters"
        if "pattern" in schema and not re.search(schema["pattern"], value):
            yield path, f"{value!r} does not match {schema['pattern']}"
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            yield path, f"has fewer than {schema['minItems']} items"
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            yield path, f"has more than {schema['maxItems']} items"
        if schema.get("uniqueItems") and len({_canonical(v) for v in value}) != len(value):
            yield path, "has duplicate items"
        if "items" in schema:
            for i, item in enumerate(value):
                yield from errors(item, schema["items"], root, f"{path}[{i}]")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                yield path, f"missing required field {key!r}"
        props = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for key, item in value.items():
            where = f"{path}.{key}" if path else key
            if key in props:
                yield from errors(item, props[key], root, where)
            elif extra is False:
                yield path, f"unknown field {key!r}"
            elif isinstance(extra, Mapping):
                yield from errors(item, extra, root, where)


def check_keywords(schema: Mapping[str, Any]) -> list[str]:
    """List the unsupported keywords anywhere in a schema.

    Args:
        schema: The schema.

    Returns:
        Unsupported keywords with their locations.
    """
    found: list[str] = []

    def walk(node: Any, where: str) -> None:  # noqa: ANN401 - a schema node
        if not isinstance(node, Mapping):
            return
        found.extend(f"{where}: {key}" for key in sorted(set(node) - SUPPORTED))
        for key in ("properties", "$defs"):
            for name, child in node.get(key, {}).items():
                walk(child, f"{where}/{key}/{name}")
        for key in ("items", "not", "additionalProperties"):
            walk(node.get(key), f"{where}/{key}")
        for i, child in enumerate(node.get("anyOf", [])):
            walk(child, f"{where}/anyOf/{i}")

    walk(schema, "#")
    return found
