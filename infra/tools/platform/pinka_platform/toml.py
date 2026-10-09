"""A minimal TOML writer for the files infra renders for platforms.

Python's standard library reads TOML (``tomllib``) but does not write it.
The endpoints file needs only strings, integers, booleans, lists of those
and nested tables, so that is all this writer supports; anything else is an
error. The tests read every output back with ``tomllib``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


def _scalar(value: Any) -> str:  # noqa: ANN401 - a TOML value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        # A JSON string is a valid TOML basic string for the characters used here.
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_scalar(v) for v in value) + "]"
    raise TypeError(f"unsupported TOML value: {value!r}")


def _table(data: Mapping[str, Any], prefix: str, out: list[str]) -> None:
    plain = {k: v for k, v in data.items() if not isinstance(v, Mapping)}
    tables = {k: v for k, v in data.items() if isinstance(v, Mapping)}
    if prefix and (plain or not tables):
        out.append(f"\n[{prefix}]")
    for key, value in plain.items():
        out.append(f"{key} = {_scalar(value)}")
    for key, value in tables.items():
        _table(value, f"{prefix}.{key}" if prefix else key, out)


def dumps(data: Mapping[str, Any], header: str = "") -> str:
    """Serialize a mapping as TOML.

    Args:
        data: Top-level keys first, then tables.
        header: Comment lines put at the top (without ``#``).

    Returns:
        TOML text.
    """
    out = [f"# {line}" if line else "#" for line in header.splitlines()]
    _table(data, "", out)
    return "\n".join(out).lstrip("\n") + "\n"
