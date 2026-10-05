"""Human-readable summary of a host profile (printed to stderr).

Colors are used only when the output is a terminal and ``NO_COLOR`` is not
set. The text never contains the report time, so it is stable for snapshot
tests.
"""

from __future__ import annotations

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
    banner = f"Pokus preflight: {OUTCOME_TITLES[outcome]} (exit {report['exit_code']})"
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
            f"  {_fmt(gpu['name'])}, {_fmt(gpu['vram_gib'])} GiB, driver {_fmt(gpu['driver'])},"
            f" compute {_fmt(gpu['compute_capability'])}, path {_fmt(gpu['path'])},"
            f" usable {_fmt(gpu['usable'])}"
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
    section(
        "Action needed",
        [f"{a['id']}: {a['action']}" for a in report["needs_action"]],
        "needs_action",
    )
    section(
        "Disabled features",
        [f"{d['feature']}: {d['impact']}" for d in report["disabled_features"]],
        "degraded",
    )
    section("Warnings", list(report["warnings"]), "degraded")
    section("Notes", list(report["notes"]), "dim")
    return "\n".join(lines) + "\n"
