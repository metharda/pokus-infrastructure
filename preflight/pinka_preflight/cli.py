"""Command-line interface of ``pinka-preflight`` (BUILD_PROMPT §2).

The JSON host profile goes to stdout (or ``--output``); the human summary goes
to stderr. On a real run as root the profile is also stored in
``/etc/pinka/host-profile.json`` and archived, unless ``--no-write`` is given
(read-only callers such as ``pinka preflight`` and the ``preflight_gate``
role use it). With ``--root`` nothing on the host is read or written and no
command is executed: commands come from the fixture's ``commands.json``.

Exit codes: 0 supported, 10 degraded, 20 unsupported, 30 needs_action,
40 preflight_error (detection failed; traceback on stderr).
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import sys
import traceback
from collections.abc import Sequence
from typing import Any

from pinka_preflight import __version__, render, schema_check
from pinka_preflight.detect.access import detect_access
from pinka_preflight.detect.components import component_conflicts, detect_components
from pinka_preflight.detect.conflicts import Conflict, detect_conflicts
from pinka_preflight.detect.environment import detect_environment
from pinka_preflight.detect.gpu import detect_gpu
from pinka_preflight.detect.hardware import detect_hardware
from pinka_preflight.detect.kernel import detect_kernel
from pinka_preflight.detect.lab import (
    LAB_DEFAULTS_JSON,
    port_conflicts,
    vm_address_conflicts,
)
from pinka_preflight.detect.lab import (
    load_defaults as load_lab_defaults,
)
from pinka_preflight.detect.network import check_endpoints
from pinka_preflight.detect.os_release import detect_os_release
from pinka_preflight.detect.timesync import detect_timesync
from pinka_preflight.fs import FileSystem
from pinka_preflight.matrix import (
    EXIT_CODES,
    MATRIX_JSON,
    NVIDIA_DRIVER_MODES,
    Decision,
    evaluate,
    load_matrix,
    match_os,
)
from pinka_preflight.report import (
    Detection,
    Overrides,
    build_report,
    load_schema,
    to_json,
    write_profile,
)
from pinka_preflight.runner import FixtureRunner, RealRunner, Runner

PREFLIGHT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENDPOINTS_JSON = os.path.join(PREFLIGHT_DIR, "endpoints.json")
MAX_PORT = 65535
DEFAULT_SRV_PATH = "/srv/pinka"
EXIT_PREFLIGHT_ERROR = EXIT_CODES["preflight_error"]


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser.

    Returns:
        The parser.
    """
    parser = argparse.ArgumentParser(
        prog="pinka-preflight",
        description=(
            "Detect the host, decide whether the Pokus lab is supported on it, and print a JSON "
            "host profile. Read-only except for storing the profile on real runs as root."
        ),
        epilog=(
            "Exit codes: 0 supported, 10 degraded, 20 unsupported, 30 needs_action, "
            "40 preflight_error."
        ),
    )
    parser.add_argument(
        "--root",
        metavar="PATH",
        help="inspect a fixture root instead of this host; commands come from PATH/commands.json",
    )
    parser.add_argument(
        "--output", metavar="FILE", help="write the JSON report to FILE instead of stdout"
    )
    parser.add_argument(
        "--offline", action="store_true", help="skip network reachability checks (for testing only)"
    )
    parser.add_argument(
        "--srv-path",
        metavar="PATH",
        default=DEFAULT_SRV_PATH,
        help=f"configured location of the research space (default: {DEFAULT_SRV_PATH})",
    )
    parser.add_argument(
        "--accept-degraded",
        action="store_true",
        help="record consent to continue with missing optional features",
    )
    parser.add_argument(
        "--accept-experimental",
        action="store_true",
        help="record consent to continue on an experimental tier",
    )
    parser.add_argument(
        "--force-unsupported",
        action="store_true",
        help="record that the operator forces an unsupported host (carried into every fingerprint)",
    )
    parser.add_argument(
        "--adopt-incus",
        action="store_true",
        help=(
            "treat an existing, unmanaged Incus installation as a warning instead of a "
            "blocking conflict"
        ),
    )
    parser.add_argument(
        "--nvidia-driver",
        choices=NVIDIA_DRIVER_MODES,
        default="auto",
        help="the host's install_nvidia_driver setting, which decides how a missing driver "
        "is judged (default: auto)",
    )
    parser.add_argument(
        "--accept-mok-enrollment",
        action="store_true",
        help=(
            "record consent that bootstrap installs the NVIDIA driver and imports a Machine "
            "Owner Key on a Secure Boot host (install_nvidia_driver: auto)"
        ),
    )
    parser.add_argument(
        "--port",
        metavar="NAME=PORT",
        action="append",
        type=_port_override,
        default=[],
        help="a lab port overridden in host_vars (ports:), checked instead of its default; "
        "repeat for each override",
    )
    parser.add_argument(
        "--vm-collector-address",
        metavar="ADDRESS",
        help="the host's vm_collector_address (default: the lab's default)",
    )
    parser.add_argument(
        "--pool-device",
        metavar="PATH",
        help="the host's incus_pool_device when incus_pool_source is device; checked against "
        "LXD's storage",
    )
    parser.add_argument(
        "--researcher",
        metavar="NAME",
        action="append",
        default=[],
        help="a configured researcher; a host whose login accounts are all researchers and "
        "that has no listener besides sshd is dedicated (§2.2); repeat for each",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="record that bootstrap runs from a repository with uncommitted changes",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="never store the profile in /etc/pinka or /var/lib/pinka",
    )
    parser.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default="auto",
        help="color the summary (auto: only on a terminal and without NO_COLOR)",
    )
    parser.add_argument("--quiet", action="store_true", help="do not print the summary")
    parser.add_argument(
        "--repo-commit",
        metavar="SHA",
        help="commit to record as repo_commit (default: detected with git)",
    )
    parser.add_argument(
        "--matrix", metavar="FILE", default=MATRIX_JSON, help="compiled support matrix (JSON)"
    )
    parser.add_argument(
        "--endpoints", metavar="FILE", default=ENDPOINTS_JSON, help="compiled endpoint list (JSON)"
    )
    parser.add_argument(
        "--lab-defaults",
        metavar="FILE",
        default=LAB_DEFAULTS_JSON,
        help="the lab's default ports and addresses (JSON)",
    )
    parser.add_argument("--version", action="version", version=f"pinka-preflight {__version__}")
    return parser


def _port_override(text: str) -> tuple[str, int]:
    name, sep, value = text.partition("=")
    if not sep or not name or not value.isdigit() or not 1 <= int(value) <= MAX_PORT:
        raise argparse.ArgumentTypeError(f"expected NAME=PORT with a port 1-{MAX_PORT}: {text!r}")
    return name, int(value)


def _pools(defaults: dict[str, Any]) -> list[ipaddress.IPv4Network]:
    """The lab's own Docker address pools."""
    pools = [ipaddress.ip_network(str(p["base"])) for p in defaults["docker_address_pools"]]
    return [p for p in pools if isinstance(p, ipaddress.IPv4Network)]


def lab_conflicts(
    args: argparse.Namespace,
    fs: FileSystem,
    runner: Runner,
    defaults: dict[str, Any],
    *,
    kvm_usable: bool,
) -> list[Conflict]:
    """Ports in use and VM collector address conflicts (§2.2, §12.11).

    Args:
        args: Parsed arguments.
        fs: Filesystem view.
        runner: Command runner.
        defaults: The lab's defaults (``lab-defaults.json``).
        kvm_usable: KVM is usable, so VM targets are possible.

    Returns:
        The conflicts.
    """
    ports = {**defaults["ports"], **dict(args.port)}
    found = port_conflicts(fs, ports, _pools(defaults))
    if kvm_usable:
        address = args.vm_collector_address or str(defaults["vm_collector"]["address"])
        found += vm_address_conflicts(fs, runner, address, defaults)
    return found


def _load_json_list(path: str, key: str) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    items = data.get(key, [])
    return [dict(i) for i in items]


def _repo_commit(args: argparse.Namespace, fs: FileSystem, runner: Runner) -> str | None:
    if args.repo_commit:
        return str(args.repo_commit)
    if fs.is_fixture:
        return None
    res = runner.run(["git", "-C", PREFLIGHT_DIR, "rev-parse", "HEAD"])
    return res.stdout.strip() or None if res.ok else None


def run_preflight(
    args: argparse.Namespace, fs: FileSystem, runner: Runner
) -> tuple[dict[str, Any], Decision]:
    """Detect, decide and assemble the report.

    Args:
        args: Parsed arguments.
        fs: Filesystem view.
        runner: Command runner.

    Returns:
        ``(report, decision)``.

    Raises:
        RuntimeError: If the report does not validate against the schema.
    """
    osr = detect_os_release(fs)
    env = detect_environment(fs, runner)
    kernel = detect_kernel(fs, runner)
    hw = detect_hardware(fs, runner, args.srv_path)
    gpu = detect_gpu(fs, runner, env.env_class)
    conflicts = detect_conflicts(
        fs,
        runner,
        env_class=env.env_class,
        os_id=osr.id if osr else "",
        systemd_pid1=kernel.systemd_pid1,
        gpu_linux_driver_in_wsl=gpu.linux_driver_in_wsl,
        adopt_incus=args.adopt_incus,
    )
    defaults = load_lab_defaults(args.lab_defaults)
    conflicts.conflicts += lab_conflicts(args, fs, runner, defaults, kvm_usable=kernel.kvm_usable)
    components = detect_components(
        fs,
        runner,
        pinka_tags=defaults["tailscale_tags"],
        nvidia_installed=gpu.driver_installed,
        nvidia_version=gpu.driver,
    )
    found, notes = component_conflicts(
        fs,
        components,
        gpu_usable=gpu.usable,
        lxd_clash_args={
            "bridge_prefixes": defaults["incus_bridges"]["prefixes"],
            "bridge_names": defaults["incus_bridges"]["names"],
            "networks": _pools(defaults),
            "pool_device": args.pool_device,
        },
    )
    conflicts.conflicts += found
    conflicts.infos += notes
    access = detect_access(
        fs,
        runner,
        firewall_manager=conflicts.firewall_manager,
        researchers=args.researcher,
        tailnet_v4=str(defaults["tailnet"]),
        docker_pools=_pools(defaults),
        record=components.record,
        admitted_udp=[int(p) for p in defaults.get("admitted_udp_ports", [])],
    )
    if access.host == "shared":
        conflicts.infos.append(
            "shared host (" + "; ".join(access.reasons) + "): the firewall keeps the "
            "pre-existing listeners reachable as found unless closed with consent (§7.5)"
        )
    timesync = detect_timesync(runner)
    matrix = load_matrix(args.matrix)
    family = match_os(matrix, osr).get("family")
    endpoints = check_endpoints(
        runner,
        _load_json_list(args.endpoints, "endpoints"),
        {"family": family, "distro": osr.id if osr else None, "gpu_present": gpu.present},
        offline=args.offline,
    )
    machine = runner.machine()
    det = Detection(
        osr,
        machine,
        env,
        kernel,
        hw,
        gpu,
        conflicts,
        timesync,
        endpoints,
        args.offline,
        components,
        access,
    )
    decision = evaluate(
        matrix,
        osr=osr,
        machine=machine,
        env=env,
        kernel=kernel,
        hw=hw,
        gpu=gpu,
        conflicts=conflicts,
        endpoints=endpoints,
        choices={
            "nvidia_driver_mode": args.nvidia_driver,
            "mok_enrollment_accepted": args.accept_mok_enrollment,
        },
    )
    overrides = Overrides(
        accept_degraded=args.accept_degraded,
        accept_experimental=args.accept_experimental,
        force_unsupported=args.force_unsupported,
        adopt_incus=args.adopt_incus,
        allow_dirty=args.allow_dirty,
        accept_mok_enrollment=args.accept_mok_enrollment,
    )
    report = build_report(
        det,
        decision,
        overrides,
        generated_at=runner.now(),
        repo_commit=_repo_commit(args, fs, runner),
        nvidia_driver_mode=args.nvidia_driver,
    )
    errors = schema_check.validate(report, load_schema())
    if errors:
        raise RuntimeError("host profile does not match its schema: " + "; ".join(errors))
    return report, decision


def _use_color(choice: str) -> bool:
    if choice == "always":
        return True
    if choice == "never" or os.environ.get("NO_COLOR"):
        return False
    return sys.stderr.isatty()


def main(argv: Sequence[str]) -> int:
    """Run preflight.

    Args:
        argv: Arguments without the program name.

    Returns:
        The exit code of the outcome, or 40 if detection failed.
    """
    parser = build_parser()
    args = parser.parse_args(list(argv))
    known = set(load_lab_defaults(args.lab_defaults)["ports"])
    unknown = sorted({name for name, _ in args.port} - known)
    if unknown:
        parser.error(
            f"--port: unknown lab ports {', '.join(unknown)} (known: {', '.join(sorted(known))})"
        )
    try:
        if args.root:
            fs = FileSystem(args.root)
            runner: Runner = FixtureRunner.from_file(os.path.join(fs.root, "commands.json"))
        else:
            fs, runner = FileSystem("/"), RealRunner()
        report, decision = run_preflight(args, fs, runner)
    except Exception:
        sys.stderr.write("pinka-preflight: detection failed (preflight_error)\n")
        traceback.print_exc()
        return EXIT_PREFLIGHT_ERROR
    text = to_json(report)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(text)
    else:
        sys.stdout.write(text)
    if not args.quiet:
        sys.stderr.write(render.render(report, color=_use_color(args.color)))
    if not fs.is_fixture and not args.no_write:
        if runner.euid() == 0:
            written = write_profile(report, runner.now())
            if not args.quiet:
                sys.stderr.write("Profile written: " + ", ".join(written) + "\n")
        elif not args.quiet:
            sys.stderr.write("Profile not stored: not running as root (use sudo, or --no-write).\n")
    return decision.exit_code
