"""Shared helpers of the contract v2 tests."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import yaml

from pinka_platform import contract as contract_mod

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures" / "manifests"
GOLDEN = HERE / "golden"
VALID = ("refplat", "potka", "allprof")
CONTRACT = contract_mod.load()


def manifest(name: str) -> dict[str, Any]:
    """Load a valid fixture manifest.

    Args:
        name: Fixture name.

    Returns:
        A fresh copy of the manifest.
    """
    data: dict[str, Any] = yaml.safe_load((FIXTURES / f"{name}.yaml").read_text(encoding="utf-8"))
    return copy.deepcopy(data)


def dump(data: dict[str, Any]) -> str:
    """Serialize a manifest as YAML text.

    Args:
        data: The manifest.

    Returns:
        YAML text.
    """
    return yaml.safe_dump(data, sort_keys=False)


def write_release(root: Path, data: dict[str, Any], *, built: bool = False) -> Path:
    """Create a release directory with every file the manifest names.

    Args:
        root: Directory to create the release in.
        data: The manifest.
        built: Also create the files below ``.venv/`` (as after the build).

    Returns:
        The release directory.
    """
    release = root / "release"
    release.mkdir(parents=True, exist_ok=True)
    (release / "platform-manifest.yaml").write_text(dump(data), encoding="utf-8")
    if data["runtime"] == "python-uv":
        (release / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    commands = [s["start"][0] for s in data["services"]]
    commands += [h["run"][0] for h in data["hooks"].values()]
    if "cli" in data:
        commands.append(data["cli"]["run"][0])
    for relative in commands:
        if relative.startswith(".venv/") and not built:
            continue
        path = release / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\n", encoding="utf-8")
        path.chmod(0o755)
    for service in data["services"]:
        if "config" in service:
            path = release / service["config"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"type": "object"}), encoding="utf-8")
    for key in ("docs", "dashboards"):
        if key in data:
            (release / data[key]).mkdir(parents=True, exist_ok=True)
    if "policy" in data:
        (release / data["policy"]["bundle"]).mkdir(parents=True, exist_ok=True)
    return release
