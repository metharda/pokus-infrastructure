"""The latest result per target (``tests/distros/results.yml``)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import yaml

HEADER = (
    "# Managed by pinka (tests/distros/results.yml). Written by `make test-distros`.\n"
    "#\n"
    "# The latest result per target of tests/distros/targets.yml; commit it after a\n"
    "# run. docs/support-matrix.md is generated from it (make generate).\n"
)
# A result counts for this many days (BUILD_PROMPT §19.9).
VALID_DAYS = 90


def load(path: Path) -> list[dict[str, Any]]:
    """Read the results.

    Args:
        path: ``tests/distros/results.yml``.

    Returns:
        One record per tested target.

    Raises:
        ValueError: On another schema version.
    """
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
    if data is None:
        return []
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError(f"{path}: schema_version 1 expected")
    return list(data.get("results") or [])


def update(path: Path, records: list[dict[str, Any]], order: list[str]) -> None:
    """Replace the records of the given targets, keeping the others.

    Args:
        path: ``tests/distros/results.yml``.
        records: New records.
        order: Target ids in file order of targets.yml.
    """
    by_target = {r["target"]: r for r in load(path)}
    by_target.update({r["target"]: r for r in records})
    rank = {t: i for i, t in enumerate(order)}
    ordered = sorted(
        by_target.values(), key=lambda r: (rank.get(r["target"], len(rank)), r["target"])
    )
    body = yaml.safe_dump(
        {"schema_version": 1, "results": ordered}, sort_keys=False, default_flow_style=False
    )
    path.write_text(HEADER + body, encoding="utf-8")


def status(record: dict[str, Any] | None) -> str:
    """Summary of one record.

    Args:
        record: The record, or None.

    Returns:
        ``passed``, ``failed: <stage>`` or ``not tested``.
    """
    if record is None:
        return "not tested"
    failed = [s for s, r in (record.get("stages") or {}).items() if r == "failed"]
    if failed:
        return f"failed: {failed[0]}"
    if all(r == "passed" for r in (record.get("stages") or {}).values()):
        return "passed"
    return "incomplete"


def valid_until(record: dict[str, Any]) -> str:
    """The date after which a result no longer counts.

    Args:
        record: The record.

    Returns:
        ISO date.
    """
    tested = dt.date.fromisoformat(str(record["tested_on"]))
    return (tested + dt.timedelta(days=VALID_DAYS)).isoformat()
