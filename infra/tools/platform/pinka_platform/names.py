"""Every name a manifest would create, and the reserved-name and host checks (§12.1)."""

from __future__ import annotations

import grp
import json
import os
import pwd
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pinka_platform import layout
from pinka_platform.contract import Contract
from pinka_platform.problems import Problem

MAX_ACCOUNT = 32
MAX_POSTGRES = 63
# The namespaces whose names the host may already use (§12.1).
HOST_NAMESPACES = ("accounts_groups", "commands")


@dataclass(frozen=True, order=True)
class Derived:
    """A name the manifest would create.

    Attributes:
        namespace: Reserved-name namespace it belongs to.
        name: The name.
        origin: What creates it, for messages.
    """

    namespace: str
    name: str
    origin: str


def account(platform: str, member: str) -> str:
    """Account of a service or job.

    Args:
        platform: Platform name.
        member: Service or job name.

    Returns:
        ``<platform>-<member>``.
    """
    return f"{platform}-{member}"


def role(platform: str, member: str) -> str:
    """PostgreSQL role of a service, or a group role of a profile.

    Args:
        platform: Platform name.
        member: Service name or profile name.

    Returns:
        ``<platform>_<member>`` with hyphens as underscores.
    """
    return f"{platform}_{member.replace('-', '_')}"


def client_group(platform: str, service: str, socket: str) -> str:
    """Client group of a socket.

    Args:
        platform: Platform name.
        service: Serving service.
        socket: Socket name.

    Returns:
        ``<platform>-<service>-<socket>``.
    """
    return f"{platform}-{service}-{socket}"


def derive(manifest: Mapping[str, Any], contract: Contract) -> list[Derived]:
    """Derive every name the manifest would create.

    Args:
        manifest: A manifest that passed the schema.
        contract: The contract.

    Returns:
        Derived names, in manifest order.
    """
    p = manifest["name"]
    out = [Derived("platform_names", p, "the platform name")]
    services = manifest["services"]
    out.append(Derived("accounts_groups", f"{p}-migrate", "the migrate hook's account"))
    out.append(Derived("accounts_groups", f"{p}-db", "the database socket group"))
    out.append(Derived("postgresql_databases", p, "the platform database"))
    for fixed in ("migrator", "reader", "grafana"):
        out.append(Derived("postgresql_roles", role(p, fixed), f"the {fixed} role"))
    for profile in sorted({s["profile"] for s in services}):
        if contract.profile(profile).flag("database_role"):
            out.append(Derived("postgresql_roles", role(p, profile), f"group role of {profile}"))
    for i, service in enumerate(services):
        where = f"services[{i}] {service['name']}"
        name = account(p, service["name"])
        out.append(Derived("accounts_groups", name, f"account of {where}"))
        out.append(Derived("units", name, f"unit of {where}"))
        if contract.profile(service["profile"]).flag("database_role"):
            out.append(Derived("postgresql_roles", role(p, service["name"]), f"role of {where}"))
    for i, job in enumerate(manifest.get("jobs", [])):
        out.append(
            Derived(
                "accounts_groups", account(p, job["name"]), f"account of jobs[{i}] {job['name']}"
            )
        )
    for i, socket in enumerate(manifest.get("sockets", [])):
        group = client_group(p, socket["service"], socket["name"])
        out.append(Derived("accounts_groups", group, f"client group of sockets[{i}]"))
    if "cli" in manifest:
        out.append(Derived("commands", manifest["cli"]["name"], "the researchers' CLI"))
    return out


def derived_paths(manifest: Mapping[str, Any]) -> list[tuple[str, str, str]]:
    """List the path segments the manifest creates, with their parent directories.

    Args:
        manifest: A manifest that passed the schema.

    Returns:
        ``(parent, segment, origin)`` triples.
    """
    p = manifest["name"]
    out = [
        (layout.PLATFORMS_DIR, p, "platform tree"),
        (layout.RESTRICTED_DIR, p, "restricted area"),
        (layout.PLATFORM_VAR_DIR, p, "platform data in /var/lib/pinka"),
        (layout.SIGNING_DIR, p, "signing key directory"),
    ]
    for service in manifest["services"]:
        out.append((layout.PLATFORM_RUN_DIR, service["name"], "socket directory"))
        out.append((layout.SECRETS_DIR, account(p, service["name"]), "secrets directory"))
    return out


@dataclass
class HostFacts:
    """What already exists on the host.

    Attributes:
        accounts: User names.
        groups: Group names.
        commands: Command names on the ``PATH``.
        owned: Names that belong to the same platform's previous deployment.
    """

    accounts: set[str] = field(default_factory=set)
    groups: set[str] = field(default_factory=set)
    commands: set[str] = field(default_factory=set)
    owned: set[str] = field(default_factory=set)

    def exists(self, namespace: str, name: str) -> bool:
        """Tell whether a name is already taken on the host by something else.

        Args:
            namespace: Reserved-name namespace.
            name: The name.

        Returns:
            True if the host has it and it is not the same platform's.
        """
        if name in self.owned:
            return False
        if namespace == "accounts_groups":
            return name in self.accounts or name in self.groups
        if namespace == "commands":
            return name in self.commands
        return False


def _path_commands(path: str) -> set[str]:
    found: set[str] = set()
    for directory in path.split(os.pathsep):
        try:
            entries = list(Path(directory).iterdir()) if directory else []
        except OSError:
            continue
        found.update(e.name for e in entries if os.access(e, os.X_OK) and not e.is_dir())
    return found


def _owned(platform: str, platform_etc: Path) -> set[str]:
    # active.json records the active platform and the names its deployment created.
    try:
        active = json.loads((platform_etc / "active.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    if not isinstance(active, dict) or active.get("name") != platform:
        return set()
    return {str(n) for n in active.get("names", [])}


def host_facts(platform: str, platform_etc: Path = Path(layout.PLATFORM_ETC_DIR)) -> HostFacts:
    """Read the host's accounts, groups and commands.

    Args:
        platform: Name of the platform being validated; the names its active
            deployment recorded in ``active.json`` count as its own.
        platform_etc: ``/etc/pinka/platform``.

    Returns:
        The facts.
    """
    return HostFacts(
        accounts={p.pw_name for p in pwd.getpwall()},
        groups={g.gr_name for g in grp.getgrall()},
        commands=_path_commands(os.environ.get("PATH", "")),
        owned=_owned(platform, platform_etc),
    )


def check(
    derived: Iterable[Derived],
    paths: Iterable[tuple[str, str, str]],
    contract: Contract,
    host: HostFacts | None,
) -> list[Problem]:
    """Check derived names against the reserved lists, each other and the host.

    Args:
        derived: Names the manifest would create.
        paths: Path segments the manifest would create.
        contract: The contract.
        host: Facts of the host, or None to skip the host checks.

    Returns:
        Problems.
    """
    problems: list[Problem] = []
    seen: dict[tuple[str, str], str] = {}
    for d in derived:
        namespace = contract.reserved[d.namespace]
        if namespace.reserves(d.name):
            problems.append(Problem("reserved-name", d.origin, f"{d.name!r} is reserved"))
        if d.namespace == "accounts_groups" and len(d.name) > MAX_ACCOUNT:
            problems.append(
                Problem("name-too-long", d.origin, f"{d.name!r} is longer than {MAX_ACCOUNT}")
            )
        if d.namespace.startswith("postgresql") and len(d.name) > MAX_POSTGRES:
            problems.append(
                Problem("name-too-long", d.origin, f"{d.name!r} is longer than {MAX_POSTGRES}")
            )
        key = (d.namespace, d.name)
        if key in seen and seen[key] != d.origin:
            problems.append(Problem("name-clash", d.origin, f"{d.name!r} is also {seen[key]}"))
        seen.setdefault(key, d.origin)
        if host is not None and d.namespace in HOST_NAMESPACES and host.exists(*key):
            problems.append(
                Problem("name-exists-on-host", d.origin, f"{d.name!r} already exists on the host")
            )
    for parent, segment, origin in paths:
        if segment in contract.reserved_paths.get(parent, frozenset()):
            problems.append(
                Problem("reserved-name", origin, f"{parent}/{segment} is a reserved path")
            )
    return problems
