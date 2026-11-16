"""Every run of every target (``tests/distros/results.yml``), oldest first."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import yaml

HEADER = (
    "# Managed by pinka (tests/distros/results.yml). Written by `make test-distros`.\n"
    "#\n"
    "# Every run of every target of tests/distros/targets.yml, oldest first; commit\n"
    "# it after a run. docs/support-matrix.md and the support matrix's pending\n"
    "# verification are generated from the latest run of each target (make generate).\n"
)
# A result counts for this many days.
VALID_DAYS = 90


def load(path: Path) -> list[dict[str, Any]]:
    """Read the results.

    Args:
        path: ``tests/distros/results.yml``.

    Returns:
        One record per run, oldest first.

    Raises:
        ValueError: On another schema version, or a record without a target or
            without its test date as a ``YYYY-MM-DD`` string (the latest run of a
            target is chosen by that date).
    """
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
    if data is None:
        return []
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError(f"{path}: schema_version 1 expected")
    records = list(data.get("results") or [])
    for n, record in enumerate(records, 1):
        if not isinstance(record, dict) or not isinstance(record.get("target"), str):
            raise ValueError(f"{path}: result {n} names no target")
        day = record.get("tested_on")
        if not isinstance(day, str) or not _is_date(day):
            raise ValueError(
                f"{path}: result {n} ({record['target']}) has no tested_on date"
                f" (YYYY-MM-DD, quoted): {day!r}"
            )
    return records


def _is_date(text: str) -> bool:
    try:
        return dt.date.fromisoformat(text).isoformat() == text
    except ValueError:
        return False


def update(path: Path, records: list[dict[str, Any]]) -> None:
    """Add the records of a run after every earlier one; no run is replaced.

    Args:
        path: ``tests/distros/results.yml``.
        records: New records, in the order the run made them.
    """
    every = [*load(path), *records]
    body = yaml.safe_dump(
        {"schema_version": 1, "results": every}, sort_keys=False, default_flow_style=False
    )
    path.write_text(HEADER + body, encoding="utf-8")


def latest(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The latest run of each target.

    The latest is the one with the newest ``tested_on``; of runs on the same
    day, the one written last (the file keeps them in the order they ran).

    Args:
        records: Every run, oldest first (:func:`load`).

    Returns:
        Target id -> its latest record.
    """
    out: dict[str, dict[str, Any]] = {}
    for record in records:
        target = str(record["target"])
        known = out.get(target)
        if known is None or str(record.get("tested_on", "")) >= str(known.get("tested_on", "")):
            out[target] = record
    return out


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
