"""Compile YAML sources to the JSON files that preflight reads."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import yaml

VERSIONS_LOCK_PREFIX = "@versions.lock:"


def load_yaml(path: Path) -> Any:  # noqa: ANN401 - YAML document
    """Load a YAML file.

    Args:
        path: File path.

    Returns:
        The parsed document.
    """
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def jsonable(value: Any) -> Any:  # noqa: ANN401 - YAML value
    """Convert YAML dates to ISO strings, recursively.

    Args:
        value: Parsed YAML.

    Returns:
        A JSON-compatible structure.
    """
    if isinstance(value, dt.date | dt.datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [jsonable(v) for v in value]
    return value


def resolve_lock_refs(value: Any, lock: Any) -> Any:  # noqa: ANN401 - YAML value
    """Replace ``"@versions.lock:<dotted.key>"`` strings with values from the lock file.

    Args:
        value: Parsed YAML.
        lock: Parsed ``versions.lock.yml``.

    Returns:
        The structure with references resolved.

    Raises:
        KeyError: If a reference does not exist.
    """
    if isinstance(value, str) and value.startswith(VERSIONS_LOCK_PREFIX):
        node = lock
        for part in value[len(VERSIONS_LOCK_PREFIX) :].split("."):
            node = node[part]
        return node
    if isinstance(value, dict):
        return {k: resolve_lock_refs(v, lock) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_lock_refs(v, lock) for v in value]
    return value


def dump_json(data: Any, source: str) -> str:  # noqa: ANN401 - JSON document
    """Serialize generated JSON with a provenance key.

    Args:
        data: Document.
        source: Source file the JSON is generated from.

    Returns:
        Indented JSON with a trailing newline.
    """
    doc = {"_generated_from": f"{source} by tools/gen (make generate); do not edit", **data}
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def support_matrix_json(root: Path) -> str:
    """Compile ``preflight/support-matrix.yml``.

    Args:
        root: Component root.

    Returns:
        JSON text.
    """
    lock = load_yaml(root / "versions.lock.yml")
    matrix = jsonable(load_yaml(root / "preflight/support-matrix.yml"))
    resolved = resolve_lock_refs(matrix, jsonable(lock))
    if not isinstance(resolved, dict):
        raise TypeError("support matrix must be a mapping")
    return dump_json(resolved, "preflight/support-matrix.yml")


def endpoints_json(root: Path) -> str:
    """Compile ``preflight/endpoints.yml``.

    Args:
        root: Component root.

    Returns:
        JSON text.
    """
    data = jsonable(load_yaml(root / "preflight/endpoints.yml"))
    if not isinstance(data, dict):
        raise TypeError("endpoints file must be a mapping")
    return dump_json(data, "preflight/endpoints.yml")
