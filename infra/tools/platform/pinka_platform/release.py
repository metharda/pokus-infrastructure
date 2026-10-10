"""Checks of a staged release directory (§7.14 step 4, §12.1)."""

from __future__ import annotations

import json
import os
import re
import stat
import tomllib
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

from pinka_platform import loader
from pinka_platform.problems import ManifestError, Problem

UV_LOCK = "uv.lock"
PYPROJECT = "pyproject.toml"
# The dependency group that locks a python-uv release's build backend, so the
# build's second phase runs without network (§7.14 step 5; proposed V3).
BUILD_GROUP = "build"
# OPA namespaces of infra (§7.10): OPA's own authorization and the placeholder.
RESERVED_POLICY_ROOTS = frozenset({"system", "pinka"})
RESERVED_PACKAGE = re.compile(r"^\s*package\s+(system|pinka)\b", re.MULTILINE)
SPECIAL_KINDS = {
    stat.S_IFCHR: "character device",
    stat.S_IFBLK: "block device",
    stat.S_IFIFO: "FIFO",
    stat.S_IFSOCK: "socket",
}
Lstat = Callable[[Path], os.stat_result]
# Files that only exist after the build (python-uv: uv sync creates .venv).
BUILT_PREFIX = ".venv/"


def _inside(root: Path, target: Path) -> bool:
    try:
        target.relative_to(root)
    except ValueError:
        return False
    return True


def check_symlinks(release: Path, skip: tuple[str, ...] = ()) -> list[Problem]:
    """Refuse symbolic links that leave the release.

    Args:
        release: The staged release (or a content package).
        skip: Top-level entries not to check: after the build, ``.venv``
            links to infra's pinned Python by design.

    Returns:
        Problems.
    """
    root = release.resolve()
    problems = []
    for directory, dirs, found in os.walk(release):
        files = found
        if Path(directory) == release:
            dirs[:] = [d for d in dirs if d not in skip]
            files = [f for f in found if f not in skip]
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


def _lstat(path: Path) -> os.stat_result:
    return path.lstat()


def check_special_files(release: Path, lstat: Lstat | None = None) -> list[Problem]:
    """Refuse device files, FIFOs, sockets and any setuid or setgid bit.

    Checks every entry, the built environment included: neither a source nor
    a build may bring them in (owner decision on 3d.2).

    Args:
        release: The staged release (or a content package).
        lstat: ``lstat``; tests fake device files, which only root can create.

    Returns:
        Problems.
    """
    lstat = lstat or _lstat
    problems = []
    for directory, dirs, files in os.walk(release):
        for name in sorted(dirs + files):
            path = Path(directory) / name
            info = lstat(path)
            where = str(path.relative_to(release))
            kind = SPECIAL_KINDS.get(stat.S_IFMT(info.st_mode))
            if kind:
                problems.append(Problem("special-file", where, f"a {kind} is not allowed"))
            if not stat.S_ISLNK(info.st_mode) and info.st_mode & (stat.S_ISUID | stat.S_ISGID):
                bits = " and ".join(
                    n
                    for b, n in ((stat.S_ISUID, "setuid"), (stat.S_ISGID, "setgid"))
                    if info.st_mode & b
                )
                problems.append(Problem("setid-bit", where, f"the {bits} bit is not allowed"))
    return problems


def check_build_backend(release: Path) -> list[Problem]:
    """A python-uv release locks its build backend in the ``build`` dependency group.

    The build's second phase runs without network and without build
    isolation, so the backend must already be in the environment from the
    first phase, installed from ``uv.lock`` with its hashes. A project
    without ``[build-system]`` is not built and needs no group.

    Args:
        release: The staged release.

    Returns:
        Problems.
    """
    path = release / PYPROJECT
    if not path.is_file():
        return [Problem("pyproject-missing", PYPROJECT, "runtime python-uv needs pyproject.toml")]
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        return [Problem("pyproject-invalid", PYPROJECT, str(exc))]
    if "build-system" not in data:
        return []
    groups = data.get("dependency-groups", {})
    if not isinstance(groups, dict) or not groups.get(BUILD_GROUP):
        return [
            Problem(
                "build-backend-unlocked",
                PYPROJECT,
                f"[build-system] needs a [dependency-groups] {BUILD_GROUP} group with the "
                "build backend, so the build runs from uv.lock without network",
            )
        ]
    return []


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
    if python_uv:
        problems += check_build_backend(release)
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
    bundle = manifest.get("policy", {}).get("bundle")
    if bundle is not None and _directory(release, bundle):
        problems += check_policy_bundle(release, bundle)
    return problems


def check_policy_bundle(release: Path, bundle: str) -> list[Problem]:
    """Refuse a policy bundle that reaches into infra's OPA namespaces (§7.10).

    OPA's own authorization lives in ``package system.authz`` and infra's
    placeholder in ``package pinka.placeholder``; OPA merges every file of a
    package, so a bundle rule there could widen what OPA allows. No module of
    the bundle may declare a package below ``system`` or ``pinka``, and no data
    file may sit below a ``system/`` or ``pinka/`` directory of the bundle.

    Args:
        release: The staged release.
        bundle: The manifest's ``policy.bundle``.

    Returns:
        Problems.
    """
    problems: list[Problem] = []
    base = release / bundle
    for path in sorted(base.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        where = str(path.relative_to(release))
        top = path.relative_to(base).parts[0]
        if path.suffix == ".rego":
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                problems.append(Problem("policy-module", where, "cannot be read as UTF-8"))
                continue
            if RESERVED_PACKAGE.search(text):
                problems.append(
                    Problem(
                        "policy-package",
                        where,
                        "declares a package below system or pinka, which belong to infra",
                    )
                )
        elif path.suffix in {".json", ".yaml", ".yml"}:
            if top in RESERVED_POLICY_ROOTS:
                problems.append(
                    Problem("policy-data", where, "data below system/ or pinka/ belongs to infra")
                )
            elif top == path.name:
                problems += _root_data_problems(path, where)
    return problems


def _root_data_problems(path: Path, where: str) -> list[Problem]:
    # OPA merges a data file at the bundle's root into the top of ``data``:
    # its own top-level keys must stay out of infra's namespaces too.
    # YAML is read like the manifest (no anchors, aliases or duplicate keys).
    try:
        text = path.read_text(encoding="utf-8")
        value = json.loads(text) if path.suffix == ".json" else loader.load_text(text)
    except (OSError, UnicodeDecodeError, ValueError, ManifestError):
        return [Problem("policy-data", where, "cannot be parsed as a mapping")]
    if isinstance(value, dict) and RESERVED_POLICY_ROOTS & set(map(str, value)):
        return [Problem("policy-data", where, "data below system or pinka belongs to infra")]
    return []
