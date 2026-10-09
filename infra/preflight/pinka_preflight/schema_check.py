"""A small JSON Schema (draft 2020-12) validator for the host profile.

Preflight is standard-library only, yet must emit a profile that validates
against ``host-profile.schema.json`` (BUILD_PROMPT §2.1). This validator
implements exactly the keywords that schema uses; any other keyword raises an
error, so the schema cannot silently grow beyond what is checked. The test
suite also validates every fixture's profile with the reference
``jsonschema`` implementation when it is installed.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
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
        "pattern",
        "minItems",
        "uniqueItems",
    }
)
_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "null": type(None),
}


def _is_type(value: Any, name: str) -> bool:  # noqa: ANN401 - JSON value
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, _TYPES[name])


def _resolve(root: Mapping[str, Any], ref: str) -> Mapping[str, Any]:
    if not ref.startswith("#/"):
        raise ValueError(f"unsupported $ref {ref!r}")
    node: Any = root
    for part in ref[2:].split("/"):
        node = node[part]
    return node  # type: ignore[no-any-return]


def validate(  # noqa: PLR0912 - one branch per keyword
    value: Any,  # noqa: ANN401 - JSON value
    schema: Mapping[str, Any],
    root: Mapping[str, Any] | None = None,
    path: str = "$",
) -> list[str]:
    """Validate a JSON value.

    Args:
        value: The instance.
        schema: The (sub)schema.
        root: The root schema, for ``$ref``.
        path: Location of ``value``, for messages.

    Returns:
        Error messages; empty if the value is valid.

    Raises:
        ValueError: If the schema uses an unsupported keyword.
    """
    root = schema if root is None else root
    unknown = set(schema) - SUPPORTED
    if unknown:
        raise ValueError(f"unsupported schema keywords at {path}: {sorted(unknown)}")
    if "$ref" in schema:
        return validate(value, _resolve(root, schema["$ref"]), root, path)
    errors: list[str] = []
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_is_type(value, t) for t in types):
            return [f"{path}: expected {' or '.join(types)}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} is not one of {schema['enum']}")
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected {schema['const']!r}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: {value} < {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: {value} > {schema['maximum']}")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than {schema['minLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{path}: does not match {schema['pattern']!r}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: fewer than {schema['minItems']} items")
        if schema.get("uniqueItems") and len({repr(v) for v in value}) != len(value):
            errors.append(f"{path}: items are not unique")
        if "items" in schema:
            for i, item in enumerate(value):
                errors.extend(validate(item, schema["items"], root, f"{path}[{i}]"))
    if isinstance(value, dict):
        errors.extend(
            f"{path}: missing {key!r}" for key in schema.get("required", []) if key not in value
        )
        props = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for key, item in value.items():
            if key in props:
                errors.extend(validate(item, props[key], root, f"{path}.{key}"))
            elif extra is False:
                errors.append(f"{path}: unexpected property {key!r}")
            elif isinstance(extra, dict):
                errors.extend(validate(item, extra, root, f"{path}.{key}"))
    return errors
