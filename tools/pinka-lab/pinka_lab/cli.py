"""Command line of pinka, the lab operator's CLI (BUILD_PROMPT §9)."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from pinka_lab import __version__, commands, fingerprint
from pinka_lab.checks import CHECKS
from pinka_lab.config import DEFAULT_PATH, ConfigError, LabConfig, load
from pinka_lab.doctor import EXIT_FAILURES, Context, exit_code, render_json, render_text, run_checks
from pinka_lab.smoke import smoke_checks
from pinka_lab.system import RealSystem, System

EPILOG = (
    "Exit codes: 0 success; 1 failure (doctor: warnings only); 2 usage error "
    "(doctor: a check of severity error failed)."
)


def build_parser() -> argparse.ArgumentParser:  # noqa: PLR0915 - one parser per command
    """The argument parser (also used to generate the reference page).

    Returns:
        The parser.
    """
    parser = argparse.ArgumentParser(
        prog="pinka",
        description="Operate a Pokus lab host: health checks, fingerprint, start and stop, "
        "backups, golden images, researchers, inference node pairing, host updates, the "
        "documentation and uninstall.",
        epilog=EPILOG,
    )
    parser.add_argument(
        "--config",
        metavar="FILE",
        default=DEFAULT_PATH,
        help="lab description written by bootstrap",
    )
    parser.add_argument("--version", action="version", version=f"pinka {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    preflight = sub.add_parser("preflight", help="run preflight read-only")
    preflight.add_argument("--json", action="store_true", help="print the JSON report")

    doctor = sub.add_parser("doctor", help="run the health and security checks (root)")
    doctor.add_argument("--json", action="store_true", help="print the report as JSON")
    doctor.add_argument(
        "--smoke", action="store_true", help="also launch a throwaway target of every golden image"
    )

    sub.add_parser(
        "status",
        help="units, containers, Incus instances, the GPU and its arbiter, disk and last backup",
    )
    gpu = sub.add_parser(
        "gpu", help="the GPU arbiter's requests: status, serve, train, idle (root)"
    )
    gpu_sub = gpu.add_subparsers(dest="gpu_action", required=True, metavar="ACTION")
    gpu_sub.add_parser("status", help="the mode, the running job and the last transitions")
    gpu_serve = gpu_sub.add_parser("serve", help="serve a deployment (vLLM restarts if needed)")
    gpu_serve.add_argument("deployment", metavar="ID@VERSION", help="a deployment spec")
    gpu_train = gpu_sub.add_parser("train", help="start a training run of the active platform")
    gpu_train.add_argument("job", help="a job of the active platform's manifest")
    gpu_train.add_argument(
        "--submission",
        required=True,
        metavar="FILE",
        help="JSON: base_model, datasets, hyperparameters, seed, sessions_drained, restore",
    )
    gpu_sub.add_parser("idle", help="stop serving, or stop the running job (it checkpoints)")
    sub.add_parser(
        "start", help="start pinka-lab.target and wait for the platform's health hook (root)"
    )
    stop = sub.add_parser(
        "stop", help="drain the platform, then stop its target and pinka-lab.target (root)"
    )
    stop.add_argument(
        "--timeout",
        type=float,
        default=None,
        metavar="SECONDS",
        help="how long the platform's drain hook may take (default: lab.drain_timeout)",
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
        "as in `pinka restore -- --non-interactive`.",
    )
    restore.add_argument(
        "bootstrap_options",
        nargs="*",
        metavar="BOOTSTRAP_OPTION",
        help="passed to bootstrap.sh (after --)",
    )

    gone = sub.add_parser(
        "uninstall",
        help="remove what pinka installed and keep the lab's data (root)",
        description="Run bootstrap.sh --uninstall from the checkout recorded in "
        "/etc/pinka/bootstrap-state.json: the removal plan first, then a backup offer and the "
        "typed machine name. Further bootstrap options follow `--`, as in "
        "`pinka uninstall -- --non-interactive --confirm-machine lab1`.",
    )
    gone.add_argument("--dry-run", action="store_true", help="print the plan and change nothing")
    gone.add_argument(
        "--purge-data",
        action="store_true",
        help="also remove the lab's data (the machine name is typed a second time)",
    )
    gone.add_argument("--skip-backup", action="store_true", help="do not offer a backup first")
    gone.add_argument("--json", action="store_true", help="print the plan or the report as JSON")
    gone.add_argument(
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
    add.add_argument("--admin", action="store_true", help="member of pinka-admin (sudo)")
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
    platform = sub.add_parser("platform", help="the lab's platform (root)")
    platform_sub = platform.add_subparsers(dest="platform_action", required=True, metavar="ACTION")
    purge = platform_sub.add_parser(
        "purge",
        help="remove a replaced platform's releases, state, restricted area and database (root)",
    )
    purge.add_argument("name", help="the replaced platform")
    purge.add_argument(
        "--confirm",
        metavar="NAME",
        help="the platform name again, typed (otherwise pinka asks for it)",
    )
    purge.add_argument(
        "--remove-secrets",
        action="store_true",
        help="also remove its secrets, database password files and private signing key",
    )
    add_docs_parser(sub)
    add_window_parser(sub)
    return parser


def add_window_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add ``pinka window open``, ``close`` and ``expire`` (§7.20).

    Args:
        sub: The subcommands of the parser.
    """
    win = sub.add_parser(
        "window",
        help="maintenance windows: staging on production's hardware (root only)",
        description=(
            "Open or close a maintenance window, recorded in /etc/pinka/maintenance-window.json "
            "on each side. On production, open drains and stops the lab first and it stays "
            "stopped until close; staging on shared hardware starts only inside its window."
        ),
    )
    win_sub = win.add_subparsers(dest="window_action", required=True, metavar="ACTION")
    opened = win_sub.add_parser("open", help="record a window (production: drain and stop first)")
    opened.add_argument(
        "--start", required=True, metavar="TIME", help="UTC start, such as 2026-10-12T18:00Z"
    )
    opened.add_argument(
        "--end", required=True, metavar="TIME", help="UTC end, such as 2026-10-12T22:00Z"
    )
    win_sub.add_parser("close", help="remove the record (staging: stop the lab first)")
    win_sub.add_parser(
        "expire",
        help="stop staging on shared hardware once its window has ended "
        "(run by pinka-window-expiry.timer)",
    )


def add_docs_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add ``pinka docs serve``, ``install`` and ``rollback`` (§7.19).

    Args:
        sub: The subcommands of the parser.
    """
    docs = sub.add_parser("docs", help="the documentation of the lab")
    docs_sub = docs.add_subparsers(dest="docs_action", required=True, metavar="ACTION")
    serve = docs_sub.add_parser(
        "serve", help=f"serve the built documentation read-only on {commands.DOCS_ADDRESS}"
    )
    serve.add_argument(
        "--dir",
        metavar="DIR",
        help="built documentation site "
        f"(default: docs.dir of the lab description, {commands.DOCS_DIR})",
    )
    serve.add_argument(
        "--port",
        type=int,
        help=f"port (default: docs.port of the lab description, {commands.DOCS_PORT})",
    )
    install = docs_sub.add_parser(
        "install",
        help="install a documentation release and make it current (root only)",
        description=(
            "Verify a docs release tarball against its SHA-256, unpack it into a new version "
            "directory (refusing absolute paths, .. and links that leave it), switch current to "
            "it in one step and keep the three most recent versions."
        ),
    )
    install.add_argument("tarball", help="the release's documentation tarball")
    install.add_argument(
        "--sha256", required=True, metavar="SUM", help="the tarball's SHA-256 (published with it)"
    )
    install.add_argument(
        "--version",
        metavar="NAME",
        help="version directory name (default: the tarball's file name without its suffix)",
    )
    docs_sub.add_parser(
        "rollback",
        help="switch the documentation back to the previous release (root only)",
        description="Switch current back to the version installed before it.",
    )


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
        sys.stderr.write("doctor needs root (sudo pinka doctor)\n")
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
        sys.stderr.write(f"pinka: {exc}\n")
        return 1
    if as_json:
        sys.stdout.write(json.dumps(result, indent=1, sort_keys=True) + "\n")
    else:
        sys.stdout.write(f"{result['hash']}  (computed {result.get('computed_at')})\n")
    return 0


def dispatch(args: argparse.Namespace, system: System, config: LabConfig) -> int:  # noqa: PLR0911, PLR0912 - one branch per command
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
    if args.command == "gpu":
        return commands.gpu(
            system,
            config,
            args.gpu_action,
            deployment=getattr(args, "deployment", None),
            job=getattr(args, "job", None),
            submission=getattr(args, "submission", None),
        )
    simple = {
        "status": lambda: commands.status(system, config),
        "start": lambda: commands.start(system, config),
        "stop": lambda: commands.stop(system, config, args.timeout),
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
    if args.command == "window":
        if args.window_action == "open":
            return commands.window_open(system, config, args.start, args.end)
        if args.window_action == "close":
            return commands.window_close(system, config)
        return commands.window_expire(system, config)
    if args.command == "platform":
        return commands.platform_purge(system, config, args.name, args.confirm, args.remove_secrets)
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
        if args.command == "uninstall":
            # Uninstall works on a partial install, without the lab description.
            optional = load(host, args.config) if host.info(args.config) else None
            flags = [
                f"--{name.replace('_', '-')}"
                for name in ("dry_run", "purge_data", "skip_backup", "json")
                if getattr(args, name)
            ]
            return commands.uninstall(host, optional, flags, args.bootstrap_options)
        if args.command == "docs":
            # The documentation can be served without a lab, from a checkout.
            optional = load(host, args.config) if host.info(args.config) else None
            if args.docs_action == "install":
                return commands.docs_install(
                    host, optional, args.tarball, args.sha256, args.version
                )
            if args.docs_action == "rollback":
                return commands.docs_rollback(host, optional)
            return commands.docs_serve(host, optional, args.dir, args.port)
        config = load(host, args.config)
        return dispatch(args, host, config)
    except (ConfigError, commands.CommandError) as exc:
        sys.stderr.write(f"pinka: {exc}\n")
        return EXIT_FAILURES if args.command == "doctor" else 1
