"""Command line of pokus-lab, the lab operator's CLI (BUILD_PROMPT §9)."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from pokus_lab import __version__, commands, fingerprint
from pokus_lab.checks import CHECKS
from pokus_lab.config import DEFAULT_PATH, ConfigError, LabConfig, load
from pokus_lab.doctor import EXIT_FAILURES, Context, exit_code, render_json, render_text, run_checks
from pokus_lab.smoke import smoke_checks
from pokus_lab.system import RealSystem, System

EPILOG = (
    "Exit codes: 0 success; 1 failure (doctor: warnings only); 2 usage error "
    "(doctor: a check of severity error failed)."
)


def build_parser() -> argparse.ArgumentParser:
    """The argument parser (also used to generate the reference page).

    Returns:
        The parser.
    """
    parser = argparse.ArgumentParser(
        prog="pokus-lab",
        description="Operate a Pokus lab host: health checks, fingerprint, start and stop, "
        "backups, golden images, researchers, inference node pairing and host updates.",
        epilog=EPILOG,
    )
    parser.add_argument(
        "--config",
        metavar="FILE",
        default=DEFAULT_PATH,
        help="lab description written by bootstrap",
    )
    parser.add_argument("--version", action="version", version=f"pokus-lab {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    preflight = sub.add_parser("preflight", help="run preflight read-only")
    preflight.add_argument("--json", action="store_true", help="print the JSON report")

    doctor = sub.add_parser("doctor", help="run the health and security checks (root)")
    doctor.add_argument("--json", action="store_true", help="print the report as JSON")
    doctor.add_argument(
        "--smoke", action="store_true", help="also launch a throwaway target of every golden image"
    )

    sub.add_parser("status", help="units, containers, Incus instances, GPU, disk and last backup")
    sub.add_parser("start", help="start pokus-lab.target (root)")
    stop = sub.add_parser("stop", help="drain the orchestrator, then stop pokus-lab.target (root)")
    stop.add_argument(
        "--timeout", type=float, default=1800, metavar="SECONDS", help="how long the drain may take"
    )

    fp = sub.add_parser("fingerprint", help="print the environment fingerprint and its hash")
    fp.add_argument("--json", action="store_true", help="print the whole fingerprint as JSON")

    backup = sub.add_parser("backup", help="back up now or list the snapshots (root)")
    backup.add_argument(
        "action",
        choices=("now", "list"),
        help="now: dump the database and back up; list: this host's snapshots",
    )

    restore = sub.add_parser(
        "restore",
        help="run bootstrap with --restore (root)",
        description="Run bootstrap.sh with --restore. Bootstrap options follow `--`, "
        "as in `pokus-lab restore -- --non-interactive`.",
    )
    restore.add_argument(
        "bootstrap_options",
        nargs="*",
        metavar="BOOTSTRAP_OPTION",
        help="passed to bootstrap.sh (after --)",
    )

    images = sub.add_parser("images", help="golden images: list, build, export")
    images_sub = images.add_subparsers(dest="images_action", required=True, metavar="ACTION")
    images_sub.add_parser("list", help="aliases, fingerprints, build time and export state")
    build = images_sub.add_parser("build", help="build missing images, or one blueprint (root)")
    build.add_argument(
        "blueprint", nargs="?", help="blueprint to build (with the blueprints it starts from)"
    )
    build.add_argument(
        "--rebuild", action="store_true", help="build the blueprint again although its image exists"
    )
    images_sub.add_parser(
        "export", help="export images that lack an export and check every export (root)"
    )

    user = sub.add_parser(
        "user", help="edit the researcher list of the configuration (never the host)"
    )
    user_sub = user.add_subparsers(dest="user_action", required=True, metavar="ACTION")
    add = user_sub.add_parser("add", help="add a researcher")
    add.add_argument("name")
    add.add_argument(
        "--key-file", required=True, metavar="FILE", help="the researcher's OpenSSH public key(s)"
    )
    add.add_argument("--admin", action="store_true", help="member of pokus-admin (sudo)")
    remove = user_sub.add_parser("remove", help="lock a researcher (moved to researchers_removed)")
    remove.add_argument("name")
    keys = user_sub.add_parser("keys", help="replace a researcher's public keys")
    keys.add_argument("name")
    keys.add_argument(
        "--key-file", required=True, metavar="FILE", help="the new OpenSSH public key(s)"
    )

    pair = sub.add_parser(
        "pair", help="pair a lab node with an inference node over the tailnet (root)"
    )
    pair_sub = pair.add_subparsers(dest="pair_action", required=True, metavar="ACTION")
    show = pair_sub.add_parser("show", help="on the inference node: show a code and wait")
    show.add_argument(
        "--timeout", type=float, default=600, metavar="SECONDS", help="how long to wait"
    )
    connect = pair_sub.add_parser("connect", help="on the lab node: pair with the inference node")
    connect.add_argument("node", help="tailnet name of the inference node")
    connect.add_argument("code", help="the code the inference node shows")

    sub.add_parser("wsl-helpers", help="where the optional Windows helpers are (wsl2 only)")
    update = sub.add_parser(
        "update-host",
        help="drain, update the OS keeping the holds, bootstrap, doctor, fingerprint diff (root)",
    )
    update.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    return parser


def run_doctor(system: System, config: LabConfig, as_json: bool, smoke: bool) -> int:
    """Run doctor (root only).

    Args:
        system: Host access.
        config: The lab description.
        as_json: Print JSON.
        smoke: Add the smoke checks.

    Returns:
        Doctor's exit code.
    """
    if system.euid() != 0:
        sys.stderr.write("doctor needs root (sudo pokus-lab doctor)\n")
        return EXIT_FAILURES
    checks = list(CHECKS)
    if smoke:
        checks += smoke_checks(list(config.get("images", "blueprints", default=[])))
    findings = run_checks(checks, Context(system, config))
    if as_json:
        checked_at = system.now().strftime("%Y-%m-%dT%H:%M:%SZ")
        sys.stdout.write(render_json(findings, config.machine, checked_at))
    else:
        sys.stdout.write(render_text(findings))
    return exit_code(findings)


def run_fingerprint(system: System, config: LabConfig, as_json: bool) -> int:
    """Print the fingerprint (root computes it, others read the cache).

    Args:
        system: Host access.
        config: The lab description.
        as_json: Print the whole document.

    Returns:
        0, or 1 when it cannot be computed or read.
    """
    try:
        result = fingerprint.current(system, config)
    except fingerprint.FingerprintError as exc:
        sys.stderr.write(f"pokus-lab: {exc}\n")
        return 1
    if as_json:
        sys.stdout.write(json.dumps(result, indent=1, sort_keys=True) + "\n")
    else:
        sys.stdout.write(f"{result['hash']}  (computed {result.get('computed_at')})\n")
    return 0


def dispatch(args: argparse.Namespace, system: System, config: LabConfig) -> int:  # noqa: PLR0911 - one branch per command
    """Run the chosen command.

    Args:
        args: Parsed arguments.
        system: Host access.
        config: The lab description.

    Returns:
        The command's exit code.
    """
    if args.command == "preflight":
        return commands.preflight(system, config, args.json)
    if args.command == "doctor":
        return run_doctor(system, config, args.json, args.smoke)
    if args.command == "fingerprint":
        return run_fingerprint(system, config, args.json)
    simple = {
        "status": lambda: commands.status(system, config),
        "start": lambda: commands.start(system),
        "stop": lambda: commands.stop(system, args.timeout),
        "restore": lambda: commands.restore(system, config, args.bootstrap_options),
        "wsl-helpers": lambda: commands.wsl_helpers(config),
        "update-host": lambda: commands.update_host(system, config, args.yes, []),
    }
    if args.command in simple:
        return simple[args.command]()
    if args.command == "backup":
        return (
            commands.backup_now(system, config)
            if args.action == "now"
            else commands.backup_list(system, config)
        )
    if args.command == "images":
        if args.images_action == "list":
            return commands.images_list(system, config)
        if args.images_action == "build":
            return commands.images_build(system, config, args.blueprint, args.rebuild, [])
        return commands.images_export(system, config, [])
    if args.command == "pair":
        if args.pair_action == "show":
            return commands.pair_show(system, config, args.timeout)
        return commands.pair_connect(system, config, args.node, args.code)
    if args.user_action == "add":
        return commands.user_add(system, config, args.name, args.key_file, args.admin)
    if args.user_action == "remove":
        return commands.user_remove(system, config, args.name)
    return commands.user_keys(system, config, args.name, args.key_file)


def main(argv: Sequence[str], system: System | None = None) -> int:
    """Entry point.

    Args:
        argv: Arguments without the program name.
        system: Host access (the real host when None).

    Returns:
        Exit code.
    """
    args = build_parser().parse_args(list(argv))
    host = system or RealSystem()
    try:
        config = load(host, args.config)
        return dispatch(args, host, config)
    except (ConfigError, commands.CommandError) as exc:
        sys.stderr.write(f"pokus-lab: {exc}\n")
        return EXIT_FAILURES if args.command == "doctor" else 1
