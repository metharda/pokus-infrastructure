"""Accounts, groups and memberships rendered from the profiles (§6.1, §12.2, §12.4).

Every membership follows from a profile grant or a socket's clients; nothing
in the manifest names a group. :func:`enforce` then re-checks the result
against the rules that hold whatever the data says (§12.4): an
``untrusted-agent`` or ``gpu-trainer`` account is never a member of
``pinka-restricted``, of the database socket group, of ``pinka-query`` or of
an infra group that grants host access, and the agent has no group except the
client groups of its dedicated sockets.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pinka_platform import layout, names
from pinka_platform.contract import Contract

RESTRICTED_GROUP = "pinka-restricted"
QUERY_GROUP = "pinka-query"
DATASETS_GROUP = "pinka-datasets"
# The infra groups platform accounts may join; users_groups creates them.
INFRA_GROUPS = (RESTRICTED_GROUP, QUERY_GROUP, DATASETS_GROUP)
# Groups that grant host access: never for a platform account.
HOST_GROUPS = frozenset(
    {
        "root",
        "wheel",
        "sudo",
        "adm",
        "docker",
        "incus",
        "incus-admin",
        "lxd",
        "pinka",
        "pinka-admin",
    }
)
# Profiles that are trusted with nothing (§12.4).
UNTRUSTED = frozenset({"untrusted-agent", "gpu-trainer"})


class RenderError(Exception):
    """The rendered accounts break a rule that holds whatever the data says."""


@dataclass(frozen=True)
class Account:
    """A platform account.

    Attributes:
        name: Account name; its primary group has the same name.
        member: Service or job name, or ``migrate``.
        kind: ``service``, ``job`` or ``migrate``.
        profile: Its profile (None for the migrate account).
        groups: Supplementary groups, sorted.
    """

    name: str
    member: str
    kind: str
    profile: str | None
    groups: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        """The account as the platform role applies it."""
        what = f"{self.kind} {self.member}" + (f" ({self.profile})" if self.profile else "")
        return {
            "name": self.name,
            "group": self.name,
            "groups": list(self.groups),
            "home": f"{layout.SERVICE_HOME_DIR}/{self.name}",
            "comment": what,
            "kind": self.kind,
            "member": self.member,
            "profile": self.profile,
        }


def db_group(platform: str) -> str:
    """The group of the database socket directory.

    Args:
        platform: Platform name.

    Returns:
        ``<platform>-db``.
    """
    return f"{platform}-db"


def _socket_groups(manifest: Mapping[str, Any], profile: str) -> list[str]:
    p = manifest["name"]
    return [
        names.client_group(p, s["service"], s["name"])
        for s in manifest.get("sockets", [])
        if profile in s["clients"]
    ]


def _service_groups(manifest: Mapping[str, Any], contract: Contract, profile: str) -> list[str]:
    grant = contract.profile(profile)
    p = manifest["name"]
    found = _socket_groups(manifest, profile)
    if grant.data["restricted_data"] == "read":
        found.append(RESTRICTED_GROUP)
    if grant.flag("telemetry_query"):
        found.append(QUERY_GROUP)
    if grant.flag("database_role") and "postgres" in manifest.get("requires", []):
        found.append(db_group(p))
    return found


def build(manifest: Mapping[str, Any], contract: Contract) -> list[Account]:
    """Derive every account of the platform with its memberships.

    Args:
        manifest: A valid manifest.
        contract: The contract.

    Returns:
        The accounts: services, jobs and the migrate account, in that order.
    """
    p = manifest["name"]
    out = [
        Account(
            names.account(p, s["name"]),
            s["name"],
            "service",
            s["profile"],
            tuple(sorted(set(_service_groups(manifest, contract, s["profile"])))),
        )
        for s in manifest["services"]
    ]
    out += [
        Account(names.account(p, j["name"]), j["name"], "job", j["profile"], (DATASETS_GROUP,))
        for j in manifest.get("jobs", [])
    ]
    migrate_groups = (db_group(p),) if "postgres" in manifest.get("requires", []) else ()
    out.append(Account(names.account(p, "migrate"), "migrate", "migrate", None, migrate_groups))
    return out


def platform_groups(manifest: Mapping[str, Any]) -> list[str]:
    """The groups the platform creates besides the accounts' own groups.

    Args:
        manifest: A valid manifest.

    Returns:
        The socket client groups, and ``<platform>-db`` with ``postgres``.
    """
    p = manifest["name"]
    found = [names.client_group(p, s["service"], s["name"]) for s in manifest.get("sockets", [])]
    if "postgres" in manifest.get("requires", []):
        found.append(db_group(p))
    return found


def enforce(manifest: Mapping[str, Any], accounts: list[Account]) -> None:
    """Refuse memberships that no manifest may cause (§12.4).

    Args:
        manifest: The manifest the accounts were derived from.
        accounts: The derived accounts.

    Raises:
        RenderError: A forbidden membership, with every reason.
    """
    p = manifest["name"]
    dedicated = {
        names.client_group(p, s["service"], s["name"])
        for s in manifest.get("sockets", [])
        if s.get("untrusted_clients")
    }
    allowed_infra = set(INFRA_GROUPS)
    problems: list[str] = []
    for account in accounts:
        groups = set(account.groups)
        if host := sorted(groups & HOST_GROUPS):
            problems.append(f"{account.name} would join {', '.join(host)}")
        if account.profile in UNTRUSTED:
            forbidden = {RESTRICTED_GROUP, QUERY_GROUP, db_group(p)}
            if bad := sorted(groups & forbidden):
                problems.append(f"{account.name} ({account.profile}) would join {', '.join(bad)}")
        if account.profile == "untrusted-agent" and (extra := sorted(groups - dedicated)):
            problems.append(
                f"{account.name} may join only the client groups of its dedicated sockets,"
                f" not {', '.join(extra)}"
            )
        if account.profile == "gpu-trainer" and (extra := sorted(groups - {DATASETS_GROUP})):
            problems.append(f"{account.name} (gpu-trainer) would join {', '.join(extra)}")
        unknown = groups - allowed_infra - set(platform_groups(manifest))
        if unknown:
            problems.append(
                f"{account.name} would join groups infra does not manage: {sorted(unknown)}"
            )
    if problems:
        raise RenderError("; ".join(problems))


def render(manifest: Mapping[str, Any], contract: Contract) -> dict[str, Any]:
    """The accounts and groups of a platform, checked by :func:`enforce`.

    Args:
        manifest: A valid manifest.
        contract: The contract.

    Returns:
        ``{"infra_groups", "groups", "accounts"}``.
    """
    accounts = build(manifest, contract)
    enforce(manifest, accounts)
    return {
        "infra_groups": list(INFRA_GROUPS),
        "groups": [*(a.name for a in accounts), *platform_groups(manifest)],
        "accounts": [a.as_dict() for a in accounts],
    }


def assign_uids(rendered: list[dict[str, Any]], users: Mapping[str, int]) -> list[dict[str, Any]]:
    """Give every platform account a UID from the platform range (§7.9 option b).

    An existing account keeps its UID; a new one gets the lowest UID of the
    range that no account on the host uses. The telemetry guard drops the
    range's connections to the telemetry backends, so an account outside it
    is refused rather than moved.

    Args:
        rendered: The rendered accounts (``name`` each).
        users: Every account on the host, name to UID.

    Returns:
        The accounts with ``uid`` set.

    Raises:
        ValueError: An account exists outside the range, or the range is full.
    """
    used = set(users.values())
    free = (
        uid
        for uid in range(layout.PLATFORM_UID_MIN, layout.PLATFORM_UID_MAX + 1)
        if uid not in used
    )
    out = []
    for account in rendered:
        name = account["name"]
        if name in users:
            uid = users[name]
            if not layout.PLATFORM_UID_MIN <= uid <= layout.PLATFORM_UID_MAX:
                raise ValueError(
                    f"account {name} exists with UID {uid}, outside the platform range "
                    f"{layout.PLATFORM_UID_MIN}-{layout.PLATFORM_UID_MAX}; remove it (userdel) "
                    "and deploy again"
                )
        else:
            found = next(free, None)
            if found is None:
                raise ValueError(
                    f"no free UID left in the platform range "
                    f"{layout.PLATFORM_UID_MIN}-{layout.PLATFORM_UID_MAX}"
                )
            uid = found
        out.append({**account, "uid": uid})
    return out
