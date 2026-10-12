"""Shared helpers of the contract v2 tests."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import yaml

from pinka_platform import agentpkg
from pinka_platform import contract as contract_mod

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures" / "manifests"
GOLDEN = HERE / "golden"
# The Pokus Platform's manifest, as infra's tests keep it (§12.1).
FIRST_PLATFORM = "pokus-platform"
VALID = ("refplat", FIRST_PLATFORM, "allprof")
# A python-uv release's pyproject.toml with its build backend locked in the
# "build" dependency group (the build's second phase runs without network).
PYPROJECT = """[project]
name = "{name}"
version = "0.1.0"

[dependency-groups]
build = ["hatchling"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
"""
# The package index of the fixtures' locked dependencies.
INDEX = "https://pypi.org/simple"
# Each agent package depends on one package from the index (V6).
AGENT_DEPENDENCY = "sockio"
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


def agent_distribution(service: dict[str, Any]) -> str:
    """The distribution name of a fixture agent's package: its directory's name."""
    return str(service["package"]).rstrip("/").rsplit("/", 1)[-1]


def lock_text(data: dict[str, Any], extra: str = "") -> str:
    """A ``uv.lock`` of a fixture release: the root, each agent package and its dependency.

    Args:
        data: The manifest.
        extra: More lock text, appended.

    Returns:
        Lock text.
    """
    out = ['version = 1\nrevision = 3\nrequires-python = ">=3.12"\n']
    out.append(f'\n[[package]]\nname = "{data["name"]}"\nversion = "0.1.0"\n')
    out.append('source = { editable = "." }\n')
    agents = [s for s in data["services"] if "package" in s]
    for service in agents:
        directory = str(service["package"]).rstrip("/")
        out.append(
            f'\n[[package]]\nname = "{agent_distribution(service)}"\nversion = "0.1.0"\n'
            f'source = {{ editable = "{directory}" }}\n'
            f'dependencies = [{{ name = "{AGENT_DEPENDENCY}" }}]\n'
        )
    if agents:
        out.append(
            f'\n[[package]]\nname = "{AGENT_DEPENDENCY}"\nversion = "1.0"\n'
            f'source = {{ registry = "{INDEX}" }}\n'
        )
    return "".join(out) + extra


def write_agent_env(release: Path, service: dict[str, Any], *extra: str) -> Path:
    """The agent's own environment as the build leaves it: its package and dependency.

    Args:
        release: The release.
        service: The agent's manifest entry.
        *extra: More distributions installed into it.

    Returns:
        The environment.
    """
    env = release / agentpkg.env_dir(service["name"])
    site = env / "lib" / "python3.12" / "site-packages"
    site.mkdir(parents=True, exist_ok=True)
    (env / "pyvenv.cfg").write_text("home = /opt/pinka/python/bin\n", encoding="utf-8")
    for dist in (agent_distribution(service), AGENT_DEPENDENCY, *extra):
        (site / f"{dist.replace('-', '_')}-1.0.dist-info").mkdir(exist_ok=True)
    return env


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
    agents = [s for s in data["services"] if "package" in s]
    python_uv = data["runtime"] == "python-uv"
    if python_uv:
        (release / "uv.lock").write_text(lock_text(data), encoding="utf-8")
        (release / "pyproject.toml").write_text(
            PYPROJECT.format(name=data["name"]), encoding="utf-8"
        )
    for service in agents:
        package = release / service["package"]
        package.mkdir(parents=True, exist_ok=True)
        if python_uv:
            (package / "pyproject.toml").write_text(
                PYPROJECT.format(name=agent_distribution(service)), encoding="utf-8"
            )
            if built:
                write_agent_env(release, service)
    commands = [agentpkg.start_path(s, data["runtime"]) for s in data["services"]]
    commands += [h["run"][0] for h in data["hooks"].values()]
    if "cli" in data:
        commands.append(data["cli"]["run"][0])
    for relative in commands:
        if relative.startswith((".venv/", f"{agentpkg.AGENT_VENVS}/")) and not built:
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
