"""``pokus-lab doctor --smoke``: one throwaway target per golden image (BUILD_PROMPT §9).

For every blueprint the lab knows, the newest image is launched in the
project ``bench`` on a fresh run network without NAT (named ``r-<12 chars>``
like the platform's, with the run ACL) and with the OTLP proxy device and the
resource attributes the platform would give it. Then systemd must be up, the
blueprint's services active, its telemetry must reach Loki under the
throwaway run ID, and neither the internet nor the lab's services may be
reachable from inside. Everything is deleted afterwards, also on failure.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

from pokus_lab.doctor import Check, Context, Outcome, failed, passed, skipped
from pokus_lab.system import System

LAB_PORTS = (8181, 18700, 18701, 18702, 18703, 18704)
MISSING = (126, 127)  # command not executable or not found inside the target


def _incus(system: System, *args: str, timeout: float = 120.0) -> Any:  # noqa: ANN401 - command result
    return system.run(["incus", *args], timeout=timeout)


def newest_alias(images: list[dict[str, Any]], blueprint: str) -> str | None:
    """The alias of a blueprint's newest version.

    Args:
        images: ``incus image list --format json``.
        blueprint: Blueprint name.

    Returns:
        ``<blueprint>/<version>`` with the highest version, or None.
    """
    versions = []
    for image in images:
        for alias in image.get("aliases") or []:
            name, _, version = str(alias.get("name", "")).partition("/")
            if name == blueprint and version:
                try:
                    versions.append((tuple(int(p) for p in version.split(".")), alias["name"]))
                except ValueError:
                    continue
    return max(versions)[1] if versions else None


def smoke_one(ctx: Context, blueprint: dict[str, Any], alias: str) -> Outcome:
    """Launch one throwaway target, check it, and delete it.

    Args:
        ctx: Check context.
        blueprint: ``{"name", "type", "nesting", "services"}`` from the lab description.
        alias: Image alias to launch.

    Returns:
        The outcome.
    """
    system = ctx.system
    short = uuid.uuid4().hex[:12]
    run_id = f"smoke-{short}"
    network = f"r-{short}"
    instance = f"smoke-{short}"
    project = str(ctx.config.get("images", "bench_project", default="bench"))
    acl = str(ctx.config.get("incus", "run_acl", default="pokus-run-default"))
    problems: list[str] = []
    try:
        created = _incus(
            system,
            "network",
            "create",
            network,
            "ipv4.address=auto",
            "ipv4.nat=false",
            "ipv6.address=none",
            f"security.acls={acl}",
        )
        if not created.ok:
            return failed(f"cannot create {network}: {created.stderr.strip()}")
        launched = _incus(
            system,
            "launch",
            alias,
            instance,
            "--project",
            project,
            "--profile",
            "target-container",
            "--network",
            network,
            "--config",
            f"security.nesting={'true' if blueprint.get('nesting') else 'false'}",
            "--config",
            f"environment.OTEL_RESOURCE_ATTRIBUTES=pokus.run_id={run_id},pokus.node=smoke",
            timeout=300,
        )
        if not launched.ok:
            return failed(f"cannot launch {alias}: {launched.stderr.strip()}")
        proxy = _incus(
            system,
            "config",
            "device",
            "add",
            instance,
            "otlp",
            "proxy",
            "--project",
            project,
            "listen=tcp:127.0.0.1:4317",
            "connect=tcp:127.0.0.1:4317",
            "bind=instance",
        )
        if not proxy.ok:
            problems.append(f"proxy device: {proxy.stderr.strip()}")
        problems += _inside_checks(ctx, project, instance, network, blueprint)
        problems += _telemetry(ctx, run_id)
    finally:
        _incus(system, "delete", "--project", project, "--force", instance)
        _incus(system, "network", "delete", network)
    if problems:
        return failed(f"{alias}: " + "; ".join(problems))
    return passed(f"{alias}: systemd up, services active, telemetry tagged, isolated")


def _inside_checks(
    ctx: Context, project: str, instance: str, network: str, blueprint: dict[str, Any]
) -> list[str]:
    system = ctx.system

    def inside(*argv: str, timeout: float = 60.0) -> Any:  # noqa: ANN401 - command result
        return _incus(system, "exec", "--project", project, instance, "--", *argv, timeout=timeout)

    problems = []
    state = inside("systemctl", "is-system-running", "--wait", timeout=300).stdout.strip()
    if state != "running":
        failed_units = inside("systemctl", "--failed", "--no-legend", "--plain").stdout.split()
        problems.append(
            f"systemd is {state or 'unknown'} ({', '.join(failed_units[:3]) or 'no failed units'})"
        )
    for service in blueprint.get("services", []):
        if inside("systemctl", "is-active", service).stdout.strip() != "active":
            problems.append(f"{service} is not active")
    egress = inside("curl", "-sS", "-m", "5", "-o", "/dev/null", "https://example.com/")
    if egress.returncode in MISSING:
        problems.append("curl is missing in the image; egress not tested")
    elif egress.ok:
        problems.append("the internet is reachable")
    gateway = _incus(system, "network", "get", network, "ipv4.address").stdout.strip().split("/")[0]
    if not gateway:
        problems.append(f"{network} has no IPv4 address; isolation from the host not tested")
    for port in LAB_PORTS if gateway else ():
        probe = inside("nc", "-z", "-w", "3", gateway, str(port))
        if probe.returncode in MISSING:
            problems.append("nc is missing in the image; isolation from the host not tested")
            break
        if probe.ok:
            problems.append(f"the lab host answers on {gateway}:{port}")
    return problems


def _telemetry(ctx: Context, run_id: str) -> list[str]:
    loki = ctx.config.get("observability", "loki")
    if not loki:
        return []
    start = int(ctx.system.now().timestamp()) - 600
    query = quote(f'{{service_name=~".+"}} | pokus_run_id="{run_id}"', safe="")
    url = f"{loki}/loki/api/v1/query_range?start={start}&query={query}"
    deadline = time.monotonic() + float(
        ctx.config.get("observability", "smoke_wait_seconds", default=120)
    )
    while True:
        response = ctx.system.http("GET", url)
        result = (((response.json() or {}).get("data") or {}).get("result")) or []
        if result:
            return []
        if time.monotonic() > deadline:
            return [f"no log line tagged {run_id} reached Loki"]
        time.sleep(float(ctx.config.get("observability", "poll_seconds", default=5)))


def _runner(blueprint: dict[str, Any]) -> Callable[[Context], Outcome]:
    def run(ctx: Context) -> Outcome:
        if not ctx.config.runs_lab:
            return skipped("no Incus on this node")
        if blueprint.get("type") == "vm":
            if not ctx.config.feature("vm_targets"):
                return skipped("VM blueprint, and vm_targets is off")
            return skipped("VM blueprints are not smoke-tested by this version")
        images = (
            ctx.system.run(["incus", "image", "list", "--format", "json"], timeout=60).json() or []
        )
        alias = newest_alias(images, str(blueprint["name"]))
        if alias is None:
            return failed(f"no image of {blueprint['name']} is published")
        return smoke_one(ctx, blueprint, alias)

    return run


def smoke_checks(blueprints: list[dict[str, Any]]) -> list[Check]:
    """One smoke check per blueprint.

    Args:
        blueprints: ``images.blueprints`` of the lab description.

    Returns:
        The checks, in the order of the blueprints.
    """
    return [
        Check(
            f"smoke.{b['name']}",
            f"A throwaway {b['name']} target boots, runs its services, reports and is isolated.",
            "error",
            "Inspect the image (`pokus-lab images list`); check the run ACL and the firewall.",
            _runner(b),
        )
        for b in blueprints
    ]
