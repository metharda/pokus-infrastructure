"""The researchers section of ``group_vars/all/main.yml``.

Only ``researchers`` and ``researchers_removed`` change; ruamel.yaml's
round-trip mode keeps every other key, the comments and the one-line style of
the entries. Fields the installer does not manage (``shell``, ``keys_file``
and any other key an operator added) stay as they are.
"""

from __future__ import annotations

import io
from collections.abc import Mapping, Sequence
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

from pinka_installer import yamlio
from pinka_installer.answers import Researcher

DEFAULT_SHELL = "/bin/bash"


def _yaml() -> YAML:
    yaml = yamlio.round_trip()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.width = 4096  # never re-wrap the operator's lines
    return yaml


def _entry(researcher: Researcher, current: Mapping[str, Any] | None) -> CommentedMap:
    entry = CommentedMap()
    entry["name"] = researcher.name
    entry["admin"] = researcher.admin
    entry["shell"] = (current or {}).get("shell", DEFAULT_SHELL)
    entry["keys_file"] = (current or {}).get("keys_file", f"users/{researcher.name}.pub")
    if researcher.display_name:
        entry["display_name"] = researcher.display_name
    if not researcher.grafana:
        entry["grafana"] = False
    for key, value in (current or {}).items():
        if key not in entry and key not in ("display_name", "grafana"):
            entry[key] = value
    entry.fa.set_flow_style()
    return entry


def render(text: str, researchers: Sequence[Researcher], removed: Sequence[str]) -> str:
    """Replace the researchers in ``group_vars/all/main.yml``.

    Args:
        text: The current file.
        researchers: The lab's researchers.
        removed: Researchers who left (locked, never deleted).

    Returns:
        The edited file.
    """
    yaml = _yaml()
    doc = yaml.load(text)
    current = {str(r.get("name")): r for r in doc.get("researchers") or []}
    _replace(doc, "researchers", [_entry(r, current.get(r.name)) for r in researchers])
    _replace(doc, "researchers_removed", list(removed), flow=True)
    stream = io.StringIO()
    yaml.dump(doc, stream)
    return stream.getvalue()


def _replace(doc: CommentedMap, key: str, items: list[Any], *, flow: bool = False) -> None:
    """Refill a sequence in place, which keeps the comments attached to it."""
    seq = doc.get(key)
    if isinstance(seq, CommentedSeq):
        seq.clear()
        seq.extend(items)
    else:
        seq = CommentedSeq(items)
        doc[key] = seq
    if flow:
        seq.fa.set_flow_style()


def researchers(text: str) -> tuple[list[Researcher], list[str]]:
    """Read the researchers of ``group_vars/all/main.yml``.

    Args:
        text: The file.

    Returns:
        The researchers and the removed ones.
    """
    doc = YAML(typ="safe").load(text) or {}
    found = [
        Researcher(
            name=str(r["name"]),
            admin=bool(r.get("admin", False)),
            display_name=str(r.get("display_name", "")),
            grafana=bool(r.get("grafana", True)),
        )
        for r in doc.get("researchers") or []
    ]
    return found, [str(n) for n in doc.get("researchers_removed") or []]
