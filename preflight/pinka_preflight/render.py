"""Human-readable summary of a host profile (printed to stderr).

Colors are used only when the output is a terminal and ``NO_COLOR`` is not
set. The text never contains the report time, so it is stable for snapshot
tests.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

RESET = "\033[0m"
COLORS = {
    "supported": "\033[1;32m",
    "degraded": "\033[1;33m",
    "needs_action": "\033[1;33m",
    "unsupported": "\033[1;31m",
    "dim": "\033[2m",
    "bold": "\033[1m",
}
OUTCOME_TITLES = {
    "supported": "SUPPORTED",
    "degraded": "DEGRADED",
    "needs_action": "NEEDS ACTION",
    "unsupported": "UNSUPPORTED",
}


def _fmt(value: Any) -> str:  # noqa: ANN401 - report value
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _gib(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f} GiB"


def _disk_lines(mount: Mapping[str, Any]) -> list[str]:
    """One mount point: the total, then its items (container and golden images summed)."""
    lines = [
        f"  disk {mount['mount']} ({_fmt(mount['fstype'])}): {_gib(mount['free_gib'])} free, "
        f"{_gib(mount['required_gib'])} needed at install"
        + (f", {_gib(mount['shortfall_gib'])} missing" if mount["shortfall_gib"] else "")
    ]
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for item in mount["items"]:
        kind = item["id"].split(":", 1)[0] if ":" in item["id"] else item["id"]
        groups.setdefault(kind, []).append(item)
    titles = {
        "image": "container images",
        "golden": "golden images (export)",
        "golden-store": "golden images (Incus image store)",
    }
    for kind, items in groups.items():
        present = sum(1 for i in items if i["present"])
        size = sum(i["size_gib"] for i in items)
        title = titles.get(kind) if len(items) > 1 or kind in titles else None
        label = f"{title} ({len(items)})" if title else items[0]["title"]
        state = f", {present} present" if present else ""
        basis = sorted({i["basis"] for i in items})
        lines.append(f"    {label}: {_gib(size)}{state} ({', '.join(basis)})")
    return lines


def requirements_lines(req: Mapping[str, Any]) -> list[str]:
    """The requirements computed from the choices (§2.3), for the summary and the log.

    Args:
        req: The host profile's ``requirements``.

    Returns:
        Text lines.
    """
    mem = req["memory_gib"]
    lines = [
        f"  plan {os.path.basename(req['plan'])}; memory {_gib(mem['total'])}:"
        f" floor {mem['floor']:g},"
        f" required {mem['required']:g}, recommended {mem['recommended']:g} GiB"
        f" (each compared at {mem['tolerance']:g} times the nominal value)"
    ]
    inference, training = req["switches"]["inference"], req["switches"]["training"]
    lines.append(
        f"  inference {'on' if inference['serves'] else 'off'} ({inference['reason']});"
        f" training {training['runs']} ({training['reason']})"
    )
    lines.append(
        f"  training cap {training['memory_max']} ({training['memory_max_basis']}:"
        f" {training['memory_max_detail']})"
    )
    for item in mem["items"]:
        detail = f"; {item['detail']}" if item["detail"] else ""
        lines.append(
            f"    {item['title']}: {item['required_gib']:g} / {item['recommended_gib']:g} GiB"
            f" ({item['basis']}{detail})"
        )
    lines.append(f"  CPU estimate {req['cpu_threads']['estimated']:g} threads")
    pool = req.get("pool")
    if pool and pool["source"] == "loop":
        lines.append(
            f"  Incus pool: loop file of {pool['size_gib']:g} GiB ({pool['basis']}"
            + (f": {pool['detail']}" if pool["detail"] else "")
            + ")"
        )
    for mount in req["disk"]:
        lines.extend(_disk_lines(mount))
    growth = req.get("growth")
    if growth:
        if growth["days_until_full"] is None:
            when = "not full within ten years at this rate"
        else:
            when = f"full in about {growth['days_until_full']} days at this rate"
        lines.append(
            f"  growth on {_fmt(growth['mount'])}: {growth['gib_per_day']:g} GiB a day,"
            f" {growth['gib_per_week']:g} GiB a week; {when}"
        )
        for item in growth["items"]:
            if item["per_run_gib"] is not None:
                lines.append(f"    {item['title']}: up to {item['per_run_gib']:g} GiB per run")
            else:
                cap = (
                    "no retention" if item["cap_gib"] is None else f"up to {item['cap_gib']:g} GiB"
                )
                lines.append(f"    {item['title']}: {item['gib_per_day']:g} GiB a day, {cap}")
    lines.extend(f"  - {note}" for note in req["notes"])
    return lines


def render(report: Mapping[str, Any], *, color: bool = False) -> str:  # noqa: PLR0915 - linear layout
    """Render the summary.

    Args:
        report: Host profile.
        color: Use ANSI colors.

    Returns:
        Multi-line text ending with a newline.
    """

    def paint(text: str, key: str) -> str:
        return f"{COLORS[key]}{text}{RESET}" if color else text

    outcome = str(report["outcome"])
    os_info: dict[str, Any] = report["os"]
    env: dict[str, Any] = report["environment"]
    hw: dict[str, Any] = report["hardware"]
    gpu: dict[str, Any] = report["gpu"]
    caps: dict[str, Any] = report["capabilities"]
    lines: list[str] = []
    banner = f"pinka preflight: {OUTCOME_TITLES[outcome]} (exit {report['exit_code']})"
    lines.append(paint(banner, outcome))
    lines.append(
        f"  tier {report['tier']} (os {report['os_tier']}, arch {report['arch_tier']})"
        f" · release model {_fmt(report['release_model'])} · provider {_fmt(report['provider'])}"
    )
    lines.append("")
    lines.append(paint("Environment", "bold"))
    hv = f", hypervisor {env['hypervisor']}" if env["hypervisor"] else ""
    lines.append(f"  class {env['class']}{hv}, confidence {env['confidence']}")
    for conflict in env["conflicts_between_signals"]:
        lines.append(f"  ! {conflict}")
    lines.append(paint("OS and kernel", "bold"))
    lines.append(
        f"  {_fmt(os_info['pretty_name'])} [{_fmt(os_info['id'])} {_fmt(os_info['version_id'])},"
        f" family {_fmt(report['family'])}, parent release {_fmt(os_info['parent_release'])}]"
    )
    lines.append(f"  arch {os_info['arch']}, kernel {_fmt(report['kernel']['release'])}")
    lines.append(
        f"  PID 1 {_fmt(report['init']['pid1'])},"
        f" system state {_fmt(report['init']['system_state'])}"
    )
    lines.append(paint("Hardware", "bold"))
    lines.append(
        f"  CPU {_fmt(hw['cpu_model'])}: {_fmt(hw['cpu_threads_effective'])} threads"
        f" (vmx {_fmt(hw['vmx'])}, svm {_fmt(hw['svm'])})"
    )
    lines.append(f"  memory {_fmt(hw['memory_gib'])} GiB, swap {_fmt(hw['swap_gib'])} GiB")
    for name, disk in sorted(hw["disks"].items()):
        lines.append(
            f"  disk {name}: {disk['checked_path']} {_fmt(disk['fstype'])},"
            f" {_fmt(disk['free_gib'])} GiB free of {_fmt(disk['size_gib'])} GiB"
        )
    lines.append(paint("GPU", "bold"))
    if gpu["present"]:
        lines.append(
            f"  {_fmt(gpu['name'])}, {_fmt(gpu['vram_gib'])} GiB, driver {_fmt(gpu['driver'])}"
            f" (CUDA {_fmt(gpu['driver_cuda'])}), compute {_fmt(gpu['compute_capability'])},"
            f" path {_fmt(gpu['path'])},"
            f" usable {_fmt(gpu['usable'])}"
        )
        if gpu.get("pci_device"):
            drivers = ", ".join(gpu.get("open_module_drivers") or [])
            if not drivers:
                proprietary = ", ".join(gpu.get("proprietary_only_drivers") or [])
                drivers = (
                    f"none (only by the proprietary module of {proprietary})"
                    if proprietary
                    else "none (not in the pinned driver's list)"
                )
            lines.append(
                f"  PCI {gpu['pci_device']}, supported by the open kernel module of the pinned "
                f"driver: {drivers}"
            )
    else:
        lines.append("  none")
    for note in gpu["notes"]:
        lines.append(f"  - {note}")
    lines.append(paint("Capabilities", "bold"))
    lines.append(
        f"  cgroup2 {_fmt(caps['cgroup2'])}, tun {_fmt(caps['tun'])},"
        f" KVM usable {_fmt(caps['kvm_usable'])},"
        f" btrfs {_fmt(caps['btrfs'])}, dm_thin_pool {_fmt(caps['dm_thin_pool'])},"
        f" Secure Boot {caps['secure_boot']}"
    )
    lines.append(
        f"  MAC {report['mac']['system']} ({_fmt(report['mac']['mode'])}),"
        f" firewall backend {report['firewall']['backend']},"
        f" storage {_fmt(report['storage_backend'])}"
    )
    lines.append(paint("Features", "bold"))
    lines.append(
        "  "
        + ", ".join(f"{name} {'on' if on else 'off'}" for name, on in report["features"].items())
    )
    access = report["access"]
    session = access["session"]
    if session["via"] == "ssh":
        via = f"SSH to {session['address']}:{session['port']} as {session['user'] or 'unknown'}"
        via += " over the tailnet" if session["tailnet"] else ""
    else:
        via = "the console"
    lines.append(paint("Access", "bold"))
    lines.append(
        f"  {access['host']} host, firewall manager {access['firewall_manager']},"
        f" install session: {via}"
    )
    for item in access["listeners"]:
        owner = item["process"] or "unknown process"
        owner += f", {item['unit']}" if item["unit"] else ""
        admitted = ", admitted by the strict policy" if item["admitted"] else ""
        lines.append(
            f"  listener {item['proto']}/{item['port']} on {item['address']} ({owner}){admitted}"
        )
    lines.append(paint("Requirements", "bold"))
    lines.extend(requirements_lines(report["requirements"]))
    network = report["network"]
    if network["offline"]:
        lines.append(paint("Network", "bold") + ": skipped (--offline)")
    else:
        ok = sum(1 for e in network["endpoints"] if e["status"] == "ok")
        lines.append(
            paint("Network", "bold") + f": {ok}/{len(network['endpoints'])} endpoints reachable"
        )

    def section(title: str, items: list[str], key: str) -> None:
        if items:
            lines.append("")
            lines.append(paint(title, key))
            lines.extend(f"  - {item}" for item in items)

    section("Unsupported", list(report["unsupported_reasons"]), "unsupported")
    section("Degraded", list(report["degraded_reasons"]), "degraded")
    section(
        "Action needed",
        [f"{a['id']}: {a['action']}" for a in report["needs_action"]],
        "needs_action",
    )
    section(
        "Disabled features",
        [
            f"{d['feature']}: {d['impact']} {d.get('remediation', '')}".rstrip()
            for d in report["disabled_features"]
        ],
        "degraded",
    )
    section("Warnings", list(report["warnings"]), "degraded")
    section("Notes", list(report["notes"]), "dim")
    return "\n".join(lines) + "\n"
