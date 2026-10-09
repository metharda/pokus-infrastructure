"""Checks of a staged release directory (§7.14 step 4, §12.1)."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from pinka_platform.problems import Problem

UV_LOCK = "uv.lock"
# Files that only exist after the build (python-uv: uv sync creates .venv).
BUILT_PREFIX = ".venv/"


def _inside(root: Path, target: Path) -> bool:
    try:
        target.relative_to(root)
    except ValueError:
        return False
    return True


def check_symlinks(release: Path) -> list[Problem]:
    """Refuse symbolic links that leave the release.

    Args:
        release: The staged release.

    Returns:
        Problems.
    """
    root = release.resolve()
    problems = []
    for directory, dirs, files in os.walk(release):
        for name in sorted(dirs + files):
            path = Path(directory) / name
            if path.is_symlink() and not _inside(root, path.resolve()):
                problems.append(
                    Problem(
                        "symlink-escape",
                        str(path.relative_to(release)),
                        "symbolic link leaves the release",
                    )
                )
    return problems


def _commands(manifest: Mapping[str, Any]) -> Iterator[tuple[str, str]]:
    for i, service in enumerate(manifest["services"]):
        yield f"services[{i}].start[0]", service["start"][0]
    for name, hook in manifest["hooks"].items():
        yield f"hooks.{name}.run[0]", hook["run"][0]
    if "cli" in manifest:
        yield "cli.run[0]", manifest["cli"]["run"][0]


def _regular_file(release: Path, relative: str) -> bool:
    path = release / relative
    root = release.resolve()
    return path.is_file() and _inside(root, path.resolve())


def _directory(release: Path, relative: str) -> bool:
    path = release / relative
    return path.is_dir() and _inside(release.resolve(), path.resolve())


def check_files(release: Path, manifest: Mapping[str, Any], *, built: bool) -> list[Problem]:
    """Check the files and directories the manifest names.

    Before the build (``built=False``) the commands of a ``python-uv`` release
    below ``.venv/`` cannot exist yet; they are checked after the build.

    Args:
        release: The staged release.
        manifest: A manifest that passed the schema and the path checks.
        built: Whether the build has run.

    Returns:
        Problems.
    """
    problems: list[Problem] = []
    python_uv = manifest["runtime"] == "python-uv"
    if python_uv and not (release / UV_LOCK).is_file():
        problems.append(
            Problem("uv-lock", UV_LOCK, "runtime python-uv needs uv.lock at the release root")
        )
    for where, relative in _commands(manifest):
        if python_uv and not built and relative.startswith(BUILT_PREFIX):
            continue
        if not _regular_file(release, relative):
            problems.append(
                Problem(
                    "command-missing", where, f"{relative!r} is not a regular file in the release"
                )
            )
    for i, service in enumerate(manifest["services"]):
        if "config" not in service:
            continue
        where = f"services[{i}].config"
        if not _regular_file(release, service["config"]):
            problems.append(Problem("config-missing", where, f"{service['config']!r} is missing"))
            continue
        try:
            schema = json.loads((release / service["config"]).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            schema = None
        if not isinstance(schema, dict):
            problems.append(Problem("config-invalid", where, "not a JSON Schema object"))
    problems += _check_directories(release, manifest)
    return problems


def _check_directories(release: Path, manifest: Mapping[str, Any]) -> list[Problem]:
    problems = []
    for key, relative in (
        ("docs", manifest.get("docs")),
        ("dashboards", manifest.get("dashboards")),
        ("policy.bundle", manifest.get("policy", {}).get("bundle")),
    ):
        if relative is not None and not _directory(release, relative):
            problems.append(Problem("directory-missing", key, f"{relative!r} is not a directory"))
    if "dashboards" in manifest and _directory(release, manifest["dashboards"]):
        base = release / manifest["dashboards"]
        for path in sorted(base.rglob("*")):
            if path.is_file() and path.suffix != ".json":
                problems.append(
                    Problem(
                        "dashboards-json",
                        str(path.relative_to(release)),
                        "the dashboards directory holds JSON files only",
                    )
                )
    return problems
