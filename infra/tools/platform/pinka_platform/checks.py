"""The invariant checks of contract v2 beyond the schema (§12.1-§12.4, §13 items 13-16)."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pinka_platform.contract import Contract, size_bytes
from pinka_platform.problems import Problem

# OPA namespaces a platform's decision path may not use (infra's placeholder, OPA itself).
FORBIDDEN_POLICY_ROOTS = frozenset({"pinka", "system"})
AGENT = "untrusted-agent"
HUMANS = ("researchers", "admins")
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9._+@-]+$")


def relative_path_problem(value: str) -> str | None:
    """Check that a path is relative and normalized.

    Args:
        value: The path from the manifest.

    Returns:
        The reason it is not, or None.
    """
    if not value or value.startswith("/") or "\\" in value or "\0" in value:
        return "must be a non-empty relative path"
    parts = value.removesuffix("/").split("/")
    if any(p in ("", ".", "..") for p in parts):
        return "must be normalized: no empty, . or .. segment"
    if not all(_SEGMENT_RE.match(p) for p in parts):
        return "may use only letters, digits and . _ + @ -"
    return None


def _services(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    return list(manifest["services"])


def check_profiles(manifest: Mapping[str, Any], contract: Contract) -> list[Problem]:
    """Profile counts and the per-profile restrictions of services and jobs.

    Args:
        manifest: A manifest that passed the schema.
        contract: The contract.

    Returns:
        Problems.
    """
    problems: list[Problem] = []
    services = _services(manifest)
    profiles = [s["profile"] for s in services]
    controllers = profiles.count("controller")
    if controllers != 1:
        problems.append(
            Problem(
                "controller-count", "services", f"{controllers} controllers, exactly one needed"
            )
        )
    if profiles.count("inference-broker") > 1:
        problems.append(Problem("broker-count", "services", "more than one inference-broker"))
    names: dict[str, str] = {}
    for kind, entries in (("services", services), ("jobs", manifest.get("jobs", []))):
        for i, entry in enumerate(entries):
            where = f"{kind}[{i}]"
            if entry["name"] in names:
                problems.append(
                    Problem(
                        "duplicate-name",
                        f"{where}.name",
                        f"{entry['name']!r} is also {names[entry['name']]}",
                    )
                )
            names.setdefault(entry["name"], where)
            if contract.profile(entry["profile"]).kind != kind.removesuffix("s"):
                problems.append(
                    Problem(
                        "profile-kind",
                        f"{where}.profile",
                        f"{entry['profile']} is not a {kind.removesuffix('s')} profile",
                    )
                )
    for i, service in enumerate(services):
        profile = contract.profile(service["profile"])
        where = f"services[{i}]"
        if service.get("secrets") and not profile.flag("secrets"):
            problems.append(
                Problem("secrets-forbidden", f"{where}.secrets", f"{profile.name} has no secrets")
            )
        if service.get("relaxations") and not profile.flag("relaxations"):
            problems.append(
                Problem(
                    "relaxation-forbidden",
                    f"{where}.relaxations",
                    f"{profile.name} gets no relaxation",
                )
            )
        problems += _check_limits(service.get("limits", {}), f"{where}.limits", contract)
    problems += _check_after(services)
    return problems


def _check_limits(limits: Mapping[str, Any], where: str, contract: Contract) -> list[Problem]:
    problems = []
    if "memory_max" in limits and size_bytes(limits["memory_max"]) > size_bytes(
        contract.caps["service_memory_max"]
    ):
        problems.append(
            Problem(
                "over-cap",
                f"{where}.memory_max",
                f"{limits['memory_max']} is above infra's cap "
                f"{contract.caps['service_memory_max']}",
            )
        )
    if "tasks_max" in limits and limits["tasks_max"] > contract.caps["service_tasks_max"]:
        problems.append(
            Problem(
                "over-cap",
                f"{where}.tasks_max",
                f"{limits['tasks_max']} is above infra's cap {contract.caps['service_tasks_max']}",
            )
        )
    return problems


def _check_after(services: Sequence[Mapping[str, Any]]) -> list[Problem]:
    problems = []
    known = {s["name"] for s in services}
    edges: dict[str, list[str]] = {}
    for i, service in enumerate(services):
        for other in service.get("after", []):
            if other == service["name"]:
                problems.append(
                    Problem("after", f"services[{i}].after", "a service cannot follow itself")
                )
            elif other not in known:
                problems.append(
                    Problem("after", f"services[{i}].after", f"{other!r} is not a service")
                )
            else:
                edges.setdefault(service["name"], []).append(other)
    state: dict[str, int] = {}

    def cyclic(node: str) -> bool:
        if state.get(node) == 1:
            return True
        if state.get(node) == 2:  # noqa: PLR2004 - done
            return False
        state[node] = 1
        if any(cyclic(n) for n in edges.get(node, [])):
            return True
        state[node] = 2
        return False

    if any(cyclic(s["name"]) for s in services if s["name"] not in state):
        problems.append(Problem("after", "services", "the start order (after) has a cycle"))
    return problems


def check_jobs(manifest: Mapping[str, Any], contract: Contract) -> list[Problem]:
    """Caps of training jobs.

    Args:
        manifest: A manifest that passed the schema.
        contract: The contract.

    Returns:
        Problems.
    """
    problems: list[Problem] = []
    caps = contract.caps
    for i, job in enumerate(manifest.get("jobs", [])):
        where = f"jobs[{i}]"
        res = job["resources"]
        if size_bytes(res["memory_max"]) > size_bytes(caps["job_memory_max"]):
            problems.append(
                Problem(
                    "over-cap",
                    f"{where}.resources.memory_max",
                    f"{res['memory_max']} is above infra's cap {caps['job_memory_max']}",
                )
            )
        if res["cpus"] > caps["job_cpus"]:
            problems.append(
                Problem(
                    "over-cap", f"{where}.resources.cpus", f"above infra's cap {caps['job_cpus']}"
                )
            )
        if res["gpus"] > caps["job_gpus"]:
            problems.append(
                Problem(
                    "over-cap", f"{where}.resources.gpus", f"above infra's cap {caps['job_gpus']}"
                )
            )
        if job["timeout_seconds"] > caps["job_timeout_seconds"]:
            problems.append(
                Problem(
                    "over-cap",
                    f"{where}.timeout_seconds",
                    f"above infra's cap {caps['job_timeout_seconds']}",
                )
            )
    return problems


def _allowed_clients(served_by: str, contract: Contract) -> set[str]:
    rules = contract.profile(served_by).socket_clients or {}
    allowed = set(rules.get("profiles", [])) | set(rules.get("humans", []))
    if rules.get("root"):
        allowed.add("root")
    return allowed


def check_sockets(manifest: Mapping[str, Any], contract: Contract) -> list[Problem]:
    """The connection rules of §12.3, dedicated agent sockets included.

    Args:
        manifest: A manifest that passed the schema.
        contract: The contract.

    Returns:
        Problems.
    """
    problems: list[Problem] = []
    profile_of = {s["name"]: s["profile"] for s in _services(manifest)}
    seen: set[tuple[str, str]] = set()
    for i, socket in enumerate(manifest.get("sockets", [])):
        where = f"sockets[{i}]"
        served_by = profile_of.get(socket["service"])
        if served_by is None:
            problems.append(
                Problem(
                    "socket-service", f"{where}.service", f"{socket['service']!r} is not a service"
                )
            )
            continue
        key = (socket["service"], socket["name"])
        if key in seen:
            problems.append(
                Problem(
                    "socket-duplicate",
                    f"{where}.name",
                    f"{socket['service']}/{socket['name']} twice",
                )
            )
        seen.add(key)
        profile = contract.profile(served_by)
        if not profile.flag("serves_sockets"):
            problems.append(
                Problem("socket-server", f"{where}.service", f"{served_by} serves no sockets")
            )
            continue
        clients = list(socket["clients"])
        dedicated = bool(socket.get("untrusted_clients", False))
        allowed = _allowed_clients(served_by, contract)
        agent_rule = (profile.socket_clients or {}).get("agent", "never")
        for client in clients:
            if client == AGENT:
                continue
            if client in contract.profiles and not contract.profile(client).flag("uses_sockets"):
                problems.append(
                    Problem("socket-client", f"{where}.clients", f"{client} uses no sockets")
                )
            elif client not in allowed:
                problems.append(
                    Problem(
                        "socket-client",
                        f"{where}.clients",
                        f"{client} may not use a socket served by {served_by}",
                    )
                )
        if AGENT in clients and agent_rule != "dedicated":
            problems.append(
                Problem(
                    "socket-client",
                    f"{where}.clients",
                    f"untrusted-agent may never use a socket served by {served_by}",
                )
            )
        elif AGENT in clients and not dedicated:
            problems.append(
                Problem(
                    "agent-socket-not-dedicated",
                    f"{where}.untrusted_clients",
                    "a socket the agent uses must be dedicated to it (untrusted_clients: true)",
                )
            )
        if dedicated and clients != [AGENT]:
            problems.append(
                Problem(
                    "dedicated-socket-shared",
                    f"{where}.clients",
                    "a dedicated agent socket lists untrusted-agent as its only client",
                )
            )
    return problems


def check_hooks(manifest: Mapping[str, Any], contract: Contract) -> list[Problem]:
    """Hook timeouts within infra's cap.

    Args:
        manifest: A manifest that passed the schema.
        contract: The contract.

    Returns:
        Problems.
    """
    cap = contract.caps["hook_timeout_seconds"]
    return [
        Problem("over-cap", f"hooks.{name}.timeout_seconds", f"above infra's cap {cap}")
        for name, hook in manifest["hooks"].items()
        if hook["timeout_seconds"] > cap
    ]


def check_paths(manifest: Mapping[str, Any]) -> list[Problem]:
    """Every path and command path in the manifest is relative and normalized.

    Args:
        manifest: A manifest that passed the schema.

    Returns:
        Problems.
    """
    found: list[tuple[str, str]] = []
    for i, service in enumerate(_services(manifest)):
        found.append((f"services[{i}].start[0]", service["start"][0]))
        if "config" in service:
            found.append((f"services[{i}].config", service["config"]))
    for name, hook in manifest["hooks"].items():
        found.append((f"hooks.{name}.run[0]", hook["run"][0]))
    if "cli" in manifest:
        found.append(("cli.run[0]", manifest["cli"]["run"][0]))
    for key in ("docs", "dashboards"):
        if key in manifest:
            found.append((key, manifest[key]))
    if "policy" in manifest:
        found.append(("policy.bundle", manifest["policy"]["bundle"]))
    for i, path in enumerate(manifest.get("backup", {}).get("paths", [])):
        found.append((f"backup.paths[{i}]", path))
    problems = []
    for where, value in found:
        reason = relative_path_problem(value)
        if reason:
            problems.append(Problem("path", where, f"{value!r} {reason}"))
    return problems


def check_backup(manifest: Mapping[str, Any]) -> list[Problem]:
    """Backup paths name declared state directories.

    Args:
        manifest: A manifest that passed the schema.

    Returns:
        Problems.
    """
    declared = {(s["name"], d) for s in _services(manifest) for d in s.get("state", [])}
    problems = []
    for i, path in enumerate(manifest.get("backup", {}).get("paths", [])):
        parts = path.removesuffix("/").split("/")
        if len(parts) < 2 or (parts[0], parts[1]) not in declared:  # noqa: PLR2004 - service/state
            problems.append(
                Problem(
                    "backup-path",
                    f"backup.paths[{i}]",
                    f"{path!r} is not inside a declared state directory (<service>/<state>)",
                )
            )
    return problems


def check_content_and_policy(manifest: Mapping[str, Any]) -> list[Problem]:
    """Content package names are unique; the decision path stays in the platform's namespace.

    Args:
        manifest: A manifest that passed the schema.

    Returns:
        Problems.
    """
    problems = []
    names = [c["name"] for c in manifest.get("content", [])]
    for name in sorted({n for n in names if names.count(n) > 1}):
        problems.append(Problem("duplicate-name", "content", f"package {name!r} twice"))
    policy = manifest.get("policy")
    if policy and policy["decision_path"].split("/", 1)[0] in FORBIDDEN_POLICY_ROOTS:
        problems.append(
            Problem(
                "policy-namespace",
                "policy.decision_path",
                "the decision path may not start with "
                + " or ".join(sorted(FORBIDDEN_POLICY_ROOTS)),
            )
        )
    return problems


def check_contract_version(manifest: Mapping[str, Any], contract: Contract) -> list[Problem]:
    """The manifest targets the installed contract.

    Args:
        manifest: A manifest that passed the schema.
        contract: The contract.

    Returns:
        Problems.
    """
    if manifest["contract_version"] != contract.version:
        return [
            Problem(
                "contract-version",
                "contract_version",
                f"{manifest['contract_version']}, but this infra implements {contract.version}",
            )
        ]
    return []


@dataclass(frozen=True)
class SourceRef:
    """Where a release or content package comes from.

    Attributes:
        repo: Repository URL.
        path: Subdirectory in the repository (empty: the repository root).
    """

    repo: str
    path: str


def _repo_key(url: str) -> str:
    return url.rstrip("/").removesuffix(".git")


def _segments(path: str) -> list[str]:
    return [p for p in path.split("/") if p and p != "."]


def check_sources(release: SourceRef, packages: Mapping[str, SourceRef]) -> list[Problem]:
    """Refuse a content package whose path overlaps the release path in the same repository.

    Paths overlap when one is a prefix of the other by path segments; an empty
    path (the repository root) overlaps every path (§12.1, §7.14 step 4).

    Args:
        release: Source of the release.
        packages: Sources of the content packages by name.

    Returns:
        Problems.
    """
    problems = []
    base = _segments(release.path)
    for name, source in sorted(packages.items()):
        if _repo_key(source.repo) != _repo_key(release.repo):
            continue
        other = _segments(source.path)
        shorter = min(len(base), len(other))
        if base[:shorter] == other[:shorter]:
            problems.append(
                Problem(
                    "source-overlap",
                    f"content.{name}",
                    f"path {source.path or '(root)'!r} overlaps the release path "
                    f"{release.path or '(root)'!r} in the same repository",
                )
            )
    return problems


def check_all(manifest: Mapping[str, Any], contract: Contract) -> list[Problem]:
    """Run every invariant check that needs only the manifest.

    Args:
        manifest: A manifest that passed the schema.
        contract: The contract.

    Returns:
        Problems.
    """
    return (
        check_contract_version(manifest, contract)
        + check_profiles(manifest, contract)
        + check_jobs(manifest, contract)
        + check_sockets(manifest, contract)
        + check_hooks(manifest, contract)
        + check_paths(manifest)
        + check_backup(manifest)
        + check_content_and_policy(manifest)
    )
