"""The isolation probe per loopback-capable profile.

For every service profile with loopback access, the distribution tests run
the reference platform's probe as a transient unit with that profile's
rendered hardening and groups. Where the deployed platform has a service of
the profile, the probe runs as that service's account: the inference guard
and the GPU arbiter admit the platform's rendered accounts, not a profile.
For the other profiles it runs as a temporary account from the platform UID
range, with the groups and the unit entries infra renders for a service of
that profile (a copy of the manifest with one more service, ``probe``).

The expectations come from the contract's profile flags: no profile reaches
Prometheus, Loki or Tempo directly; the query proxy answers exactly the
profiles that query; the inference API answers only ``controller`` and
``inference-broker`` and the GPU arbiter's and the run-network helper's
sockets only the controller, each of them only for an account the rendering
admits. The probe reports every other target too; only these are compared.
"""

from __future__ import annotations

import base64
import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pinka_platform import accounts, deploy, layout, names, units
from pinka_platform.contract import Contract

LOOPBACK = "loopback"
PROBE_SERVICE = "probe"
STORES = ("prometheus direct", "loki direct", "tempo direct")
COMPARED = (
    *STORES,
    "query proxy",
    "inference api",
    "gpu arbiter socket",
    "run-network helper socket",
)
# Entries of a service unit that make it that service, not its sandbox.
UNIT_ONLY = frozenset(
    {
        "Type",
        "ExecStart",
        "WorkingDirectory",
        "Restart",
        "RestartSec",
        "RuntimeDirectory",
        "RuntimeDirectoryMode",
    }
)
INFRA = Path(__file__).resolve().parents[3]
PROBE_SOURCE = (
    INFRA
    / "tests"
    / "reference-platform"
    / "packages"
    / "refplat-agent"
    / "src"
    / "refplat_agent"
    / "probe.py"
)


@dataclass(frozen=True)
class ProfileProbe:
    """How the probe runs for one profile.

    Attributes:
        profile: The profile.
        account: The account it runs as.
        temporary: Whether the account is created for the probe (and removed).
        properties: The unit's entries: the profile's rendered hardening, user
            and groups.
        expected: Whether each compared target must be reached.
    """

    profile: str
    account: str
    temporary: bool
    properties: tuple[tuple[str, str], ...]
    expected: Mapping[str, bool]


def loopback_profiles(contract: Contract) -> list[str]:
    """The service profiles with loopback access, in the contract's order."""
    return [
        name
        for name, profile in contract.profiles.items()
        if profile.kind == "service" and profile.data["network"] == LOOPBACK
    ]


def expectations(contract: Contract, profile: str, *, rendered: bool) -> dict[str, bool]:
    """What the probe must reach for a profile.

    Args:
        contract: The contract.
        profile: The profile.
        rendered: Whether the account is one the rendering made (the inference
            guard, the arbiter and the run-network helper admit only those).

    Returns:
        Target name to whether it must be reached.
    """
    grant = contract.profile(profile)
    return {
        **dict.fromkeys(STORES, False),
        "query proxy": grant.flag("telemetry_query"),
        "inference api": grant.flag("inference_api") and rendered,
        "gpu arbiter socket": grant.flag("gpu_arbiter") and rendered,
        "run-network helper socket": grant.flag("run_network_helper") and rendered,
    }


def plan(
    manifest: Mapping[str, Any], contract: Contract, ports: Mapping[str, int]
) -> list[ProfileProbe]:
    """One probe per loopback-capable profile for a deployed platform.

    Args:
        manifest: The deployed platform's manifest.
        contract: The contract.
        ports: The effective ports.

    Returns:
        The probes, in the contract's order of the profiles.
    """
    found = []
    for profile in loopback_profiles(contract):
        own = [s for s in manifest["services"] if s["profile"] == profile]
        synth = copy.deepcopy(dict(manifest))
        if own:
            service = own[0]
        else:
            start = list(manifest["services"][0]["start"])
            service = {"name": PROBE_SERVICE, "profile": profile, "start": start}
            synth["services"] = [*synth["services"], service]
        rendered = {a["name"]: a for a in accounts.render(synth, contract)["accounts"]}
        _, entries = units.service_sections(synth, service, contract, rendered, ports)
        found.append(
            ProfileProbe(
                profile=profile,
                account=names.account(manifest["name"], service["name"]),
                temporary=not own,
                properties=tuple((k, v) for k, v in entries if k not in UNIT_ONLY),
                expected=expectations(contract, profile, rendered=bool(own)),
            )
        )
    return found


def command(probe: ProfileProbe, platform: str, unit: str, source: str | None = None) -> list[str]:
    """The ``systemd-run`` command that runs the probe for one profile.

    The probe's source travels base64-encoded, so neither systemd's
    specifiers nor its environment expansion can touch it, and the unit
    needs no file of the probe.

    Args:
        probe: The profile's probe.
        platform: The deployed platform's name (for the probe's defaults).
        unit: The transient unit's name.
        source: The probe's source (default: the reference platform's).

    Returns:
        The command.
    """
    text = PROBE_SOURCE.read_text(encoding="utf-8") if source is None else source
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    argv = ["systemd-run", "--quiet", "--wait", "--pipe", "--collect", "--service-type=exec"]
    argv.append(f"--unit={unit}")
    for key, value in probe.properties:
        argv += ["--property", f"{key}={value}"]
    code = f"import base64; exec(base64.b64decode('{encoded}'))"
    return [
        *argv,
        "--",
        deploy.PYTHON,
        "-I",
        "-c",
        code,
        "--profile",
        probe.profile,
        "--platform",
        platform,
    ]


def account_script(probe: ProfileProbe) -> tuple[str, str]:
    """Shell commands that create and remove a temporary account from the platform range.

    Args:
        probe: A probe with ``temporary`` set.

    Returns:
        ``(create, remove)``.
    """
    name = probe.account
    create = (
        f"uid=; for candidate in $(seq {layout.PLATFORM_UID_MIN} {layout.PLATFORM_UID_MAX}); do"
        ' if ! getent passwd "$candidate" >/dev/null && ! getent group "$candidate" >/dev/null;'
        ' then uid=$candidate; break; fi; done; test -n "$uid" &&'
        f' groupadd --gid "$uid" {name} &&'
        f' useradd --uid "$uid" --gid "$uid" --no-create-home --home-dir /'
        f" --shell /usr/sbin/nologin {name}"
    )
    remove = f"userdel {name}; groupdel {name} 2>/dev/null; true"
    return create, remove


def compare(probe: ProfileProbe, report: Mapping[str, Any]) -> list[str]:
    """Differences between a probe's report and the profile's expectations.

    Args:
        probe: The profile's probe.
        report: The probe's JSON report.

    Returns:
        One message per difference; empty when the profile is isolated as it must be.
    """
    outcomes = {o["name"]: o for o in report.get("outcomes", [])}
    problems = []
    if report.get("profile") != probe.profile:
        problems.append(f"{probe.profile}: the report is for {report.get('profile')!r}")
    for target, wanted in probe.expected.items():
        outcome = outcomes.get(target)
        if outcome is None:
            problems.append(f"{probe.profile}: {target} was not tried")
        elif bool(outcome["reached"]) != wanted:
            verb = "must reach" if wanted else "reached"
            problems.append(f"{probe.profile} {verb} {target} ({outcome['detail']})")
    return problems


def agent_problems(report: Mapping[str, Any], allowed: Sequence[str] = ()) -> list[str]:
    """What the agent's own probe reached (it must reach nothing).

    Args:
        report: The report the agent sent the controller.
        allowed: Targets it may reach (none).

    Returns:
        One message per target reached.
    """
    if not report.get("outcomes"):
        return ["the agent has not reported its probe"]
    return [
        f"untrusted-agent reached {o['name']} ({o['detail']})"
        for o in report["outcomes"]
        if o["reached"] and o["name"] not in allowed
    ]
