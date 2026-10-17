"""The agent's own package and environment (V6, owner decision with the "commit" for 3d.4).

An ``untrusted-agent`` service names the directory of its own package, the
only part of the release it sees. For ``python-uv`` that directory is a
workspace member of the release: the build installs it into an environment
of its own, ``.agent-venvs/<service>/``, with only that package and its
locked dependencies, and the agent binds only the package and that
environment. Every dependency must come from a package index (an
``https://`` registry in ``uv.lock``): a dependency on another package of
the release, directly or through others, would put code the agent must not
see into its environment, and so would one from a path, a Git repository or
a URL, which can hold the release's own packages.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pinka_platform.problems import Problem

AGENT_VENVS = ".agent-venvs"
SHARED_VENV = ".venv"
UV_LOCK = "uv.lock"
PYPROJECT = "pyproject.toml"
INDEX_PREFIX = "https://"
# uv.lock sources of the release's own packages.
MEMBER_SOURCES = ("editable", "directory", "virtual")
_NAME_RUN = re.compile(r"[-_.]+")


class LockError(Exception):
    """``uv.lock`` cannot be read as a lock file."""


def normalize(name: str) -> str:
    """A distribution name in its normalized form (PEP 503).

    Args:
        name: The name.

    Returns:
        Lowercase, with every run of ``-``, ``_`` and ``.`` replaced by ``-``.
    """
    return _NAME_RUN.sub("-", name).lower()


def env_dir(service: str) -> str:
    """The agent's own environment inside the release.

    Args:
        service: The service name.

    Returns:
        A path relative to the release.
    """
    return f"{AGENT_VENVS}/{service}"


def package_dir(service: Mapping[str, Any]) -> str:
    """The service's package directory without a trailing slash.

    Args:
        service: The service's manifest entry (with ``package``).

    Returns:
        A path relative to the release.
    """
    return str(service["package"]).removesuffix("/")


def start_path(service: Mapping[str, Any], runtime: str) -> str:
    """Where the agent's start command lies in the built release.

    For ``python-uv`` a command below ``.venv/`` comes from the agent's own
    environment; anything else is inside its package.

    Args:
        service: The service's manifest entry.
        runtime: The manifest's runtime.

    Returns:
        A path relative to the release.
    """
    start = str(service["start"][0])
    prefix = f"{SHARED_VENV}/"
    if runtime == "python-uv" and "package" in service and start.startswith(prefix):
        return f"{env_dir(service['name'])}/{start.removeprefix(prefix)}"
    return start


@dataclass(frozen=True)
class Locked:
    """One package of ``uv.lock``.

    Attributes:
        name: Its name as locked.
        version: Its version (None for some local sources).
        source: Its source table (``registry``, ``editable``, ``git`` ...).
        dependencies: Its dependency entries.
        optional: Its optional dependencies by extra.
    """

    name: str
    version: str | None
    source: dict[str, Any]
    dependencies: list[dict[str, Any]] = field(default_factory=list)
    optional: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def describe(self) -> str:
        """The package with its source, for messages."""
        kind, value = next(iter(self.source.items()), ("unknown", ""))
        return f"{self.name} ({kind} {value})"

    def from_index(self) -> bool:
        """Whether it comes from a package index over HTTPS."""
        registry = self.source.get("registry")
        return isinstance(registry, str) and registry.startswith(INDEX_PREFIX)


def _entries(value: object) -> list[dict[str, Any]]:
    return [d for d in value if isinstance(d, dict)] if isinstance(value, list) else []


def read_lock(release: Path) -> list[Locked]:
    """The packages of the release's ``uv.lock``.

    Args:
        release: The staged release.

    Returns:
        The packages.

    Raises:
        LockError: If the file is missing or not a lock file.
    """
    try:
        data = tomllib.loads((release / UV_LOCK).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise LockError(f"{UV_LOCK} cannot be read: {exc}") from exc
    packages = data.get("package")
    if not isinstance(packages, list):
        raise LockError(f"{UV_LOCK} lists no packages")
    found = []
    for entry in packages:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            raise LockError(f"{UV_LOCK} holds a package without a name")
        optional = entry.get("optional-dependencies", {})
        source = entry.get("source")
        found.append(
            Locked(
                name=entry["name"],
                version=entry.get("version") if isinstance(entry.get("version"), str) else None,
                source=dict(source) if isinstance(source, dict) else {},
                dependencies=_entries(entry.get("dependencies")),
                optional={str(k): _entries(v) for k, v in optional.items()}
                if isinstance(optional, dict)
                else {},
            )
        )
    return found


def member_for(packages: Sequence[Locked], directory: str) -> Locked | None:
    """The workspace member locked at a directory of the release.

    Args:
        packages: The lock's packages.
        directory: The package directory (relative to the release).

    Returns:
        The member, or None.
    """
    wanted = directory.removesuffix("/") or "."
    for package in packages:
        for kind in MEMBER_SOURCES:
            value = package.source.get(kind)
            if isinstance(value, str) and value.removesuffix("/") == wanted:
                return package
    return None


def _matches(entry: Mapping[str, Any], package: Locked) -> bool:
    if normalize(str(entry.get("name", ""))) != normalize(package.name):
        return False
    if "version" in entry and entry["version"] != package.version:
        return False
    return "source" not in entry or entry["source"] == package.source


def closure(packages: Sequence[Locked], root: Locked) -> list[tuple[Locked, tuple[str, ...]]]:
    """Every package the root depends on, directly or through others.

    Markers are not evaluated: a declared dependency counts on every
    platform. Extras count where a dependency entry requests them.

    Args:
        packages: The lock's packages.
        root: The agent's package.

    Returns:
        ``(package, chain)`` pairs, the root first; ``chain`` names the
        packages from the root to this one.
    """
    seen: dict[int, tuple[Locked, tuple[str, ...]]] = {id(root): (root, (root.name,))}
    expanded: set[tuple[int, str]] = set()
    queue = [root]

    def reach(entry: Mapping[str, Any], chain: tuple[str, ...]) -> None:
        extras = entry.get("extra", [])
        for package in packages:
            if not _matches(entry, package):
                continue
            path = (*chain, package.name)
            if id(package) not in seen:
                seen[id(package)] = (package, path)
                queue.append(package)
            for extra in extras if isinstance(extras, list) else []:
                if (id(package), str(extra)) in expanded:
                    continue
                expanded.add((id(package), str(extra)))
                for optional in package.optional.get(str(extra), []):
                    reach(optional, path)

    while queue:
        current = queue.pop(0)
        for entry in current.dependencies:
            reach(entry, seen[id(current)][1])
    return list(seen.values())


def distribution(release: Path, directory: str) -> str | None:
    """The ``[project].name`` of a package directory.

    Args:
        release: The staged release.
        directory: The package directory.

    Returns:
        The name, or None if the directory has no readable project.
    """
    try:
        data = tomllib.loads((release / directory / PYPROJECT).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None
    name = data.get("project", {}).get("name") if isinstance(data.get("project"), dict) else None
    return name if isinstance(name, str) else None


def _agent_member(
    release: Path, directory: str, where: str
) -> tuple[list[Locked], Locked | None, list[Problem]]:
    try:
        packages = read_lock(release)
    except LockError as exc:
        return [], None, [Problem("agent-package-lock", where, str(exc))]
    name = distribution(release, directory)
    member = member_for(packages, directory)
    if name is None or member is None or normalize(member.name) != normalize(name):
        return (
            packages,
            None,
            [
                Problem(
                    "agent-package-not-member",
                    where,
                    f"{directory!r} is not a workspace member of the release locked in {UV_LOCK}",
                )
            ],
        )
    return packages, member, []


def dependency_problems(release: Path, directory: str, where: str) -> list[Problem]:
    """Refuse an agent package that depends on anything but a package index.

    Args:
        release: The staged release.
        directory: The agent's package directory.
        where: The manifest field, for the problems.

    Returns:
        Problems.
    """
    packages, member, problems = _agent_member(release, directory, where)
    if member is None:
        return problems
    for package, chain in closure(packages, member)[1:]:
        if package.from_index():
            continue
        own = any(k in package.source for k in MEMBER_SOURCES)
        reason = (
            "another package of the same release"
            if own
            else "a source other than a package index, which can hold the release's own code"
        )
        problems.append(
            Problem(
                "agent-package-depends",
                where,
                f"{' -> '.join(chain)}: {package.describe()} is {reason}; the agent's "
                "environment holds only its own package and locked dependencies from a package "
                "index",
            )
        )
    return problems


def installed(env: Path) -> list[str]:
    """The distributions installed in an environment.

    Args:
        env: The environment.

    Returns:
        Normalized names, from the ``*.dist-info`` directories.
    """
    names = []
    for info in sorted(env.glob("lib/python*/site-packages/*.dist-info")):
        stem = info.name.removesuffix(".dist-info")
        names.append(normalize(stem.rsplit("-", 1)[0]))
    return names


def env_problems(release: Path, service: str, directory: str, where: str) -> list[Problem]:
    """Check a built agent environment: only the package and its locked dependencies.

    Args:
        release: The built release.
        service: The service name.
        directory: The agent's package directory.
        where: The manifest field, for the problems.

    Returns:
        Problems.
    """
    env = release / env_dir(service)
    if not (env / "pyvenv.cfg").is_file():
        return [
            Problem("agent-env-missing", env_dir(service), "the agent's environment was not built")
        ]
    packages, member, problems = _agent_member(release, directory, where)
    if member is None:
        return problems
    allowed = {normalize(p.name) for p, _ in closure(packages, member)}
    return [
        Problem(
            "agent-env-extra",
            env_dir(service),
            f"{name} is installed but is neither the agent's package nor one of its locked "
            "dependencies",
        )
        for name in installed(env)
        if name not in allowed
    ]
