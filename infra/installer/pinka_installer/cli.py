"""``python -m pinka_installer``: the TUI, and the answers replay of ``bootstrap.sh --answers``.

Bootstrap runs these commands with the installer environment
(``/opt/pinka/installer-venv``) as root:

- ``describe --answers FILE`` prints the machine name, the mode, the
  snapshot to restore and the consent to MOK enrollment of an answers file as
  ``key=value`` lines;
- ``apply --answers FILE --vault-password-file FILE`` writes host_vars, the
  researchers, the vault and the answers file (BUILD_PROMPT §18.6);
- ``tui`` starts the installer TUI on the terminal (BUILD_PROMPT §18).

Exit status: 0 done, 1 operator action needed (the reason is printed), 2 usage.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from pinka_installer import answers as answers_mod
from pinka_installer import apply, vault
from pinka_installer.paths import Paths

EXIT_OK = 0
EXIT_ACTION = 1


def build_parser() -> argparse.ArgumentParser:
    """The command line.

    Returns:
        The parser.
    """
    parser = argparse.ArgumentParser(
        prog="pinka-installer",
        description="The pinka installer: TUI and answers replay (run by bootstrap.sh).",
    )
    parser.add_argument("--infra", type=Path, default=None, help="infra directory of the checkout")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("validate", help="check an answers file")
    check.add_argument("--answers", type=Path, required=True, help="the answers file")
    describe = sub.add_parser(
        "describe", help="print machine, mode, snapshot and MOK consent of answers"
    )
    describe.add_argument("--answers", type=Path, required=True, help="the answers file")
    replay = sub.add_parser("apply", help="write the files of an answers file (non-interactive)")
    replay.add_argument("--answers", type=Path, required=True, help="the answers file")
    replay.add_argument("--machine", default="", help="must match the answers (from --machine)")
    replay.add_argument(
        "--vault-password-file",
        type=Path,
        required=True,
        help="file with the vault password (0600): encrypts a new vault or opens the existing one",
    )
    tui = sub.add_parser("tui", help="start the installer TUI")
    tui.add_argument("--machine", default="", help="machine name given to bootstrap")
    tui.add_argument("--answers", type=Path, default=None, help="answers to start from")
    tui.add_argument(
        "--bootstrap-arg",
        action="append",
        default=[],
        help="option passed on to the non-interactive bootstrap the TUI runs (repeatable)",
    )
    return parser


def _load(path: Path, paths: Paths) -> answers_mod.Answers | None:
    try:
        return answers_mod.load(path, apply.context(paths))
    except answers_mod.AnswersError as exc:
        sys.stderr.write(f"pinka-installer: {path} cannot be used:\n")
        for problem in exc.problems:
            sys.stderr.write(f"  {problem}\n")
        return None
    except OSError as exc:
        sys.stderr.write(f"pinka-installer: cannot read {path}: {exc.strerror}\n")
        return None


def cmd_validate(args: argparse.Namespace, paths: Paths) -> int:
    """Check an answers file.

    Args:
        args: Parsed options.
        paths: Installer paths.

    Returns:
        Exit status.
    """
    loaded = _load(args.answers, paths)
    if loaded is None:
        return EXIT_ACTION
    print(f"{args.answers}: valid answers for {loaded.machine.name}")
    return EXIT_OK


def cmd_describe(args: argparse.Namespace, paths: Paths) -> int:
    """Print what bootstrap needs to know of an answers file (``key=value`` lines).

    Args:
        args: Parsed options.
        paths: Installer paths.

    Returns:
        Exit status.
    """
    loaded = _load(args.answers, paths)
    if loaded is None:
        return EXIT_ACTION
    print(f"machine={loaded.machine.name}")
    print(f"mode={loaded.mode}")
    print(f"snapshot={loaded.restore_snapshot if loaded.mode == 'restore' else ''}")
    consent = loaded.gpu.install_driver == "auto" and loaded.gpu.accept_mok_enrollment
    print(f"accept_mok_enrollment={'true' if consent else 'false'}")
    return EXIT_OK


def cmd_apply(args: argparse.Namespace, paths: Paths) -> int:
    """Write the files of an answers file (``bootstrap.sh --answers``).

    Args:
        args: Parsed options.
        paths: Installer paths.

    Returns:
        Exit status.
    """
    loaded = _load(args.answers, paths)
    if loaded is None:
        return EXIT_ACTION
    if args.machine and args.machine != loaded.machine.name:
        sys.stderr.write(
            f"pinka-installer: --machine {args.machine} but the answers are for "
            f"{loaded.machine.name}\n"
        )
        return EXIT_ACTION
    try:
        password = apply.read_password_file(args.vault_password_file)
        result = apply.plan(
            loaded,
            paths,
            apply.environment_secrets(),
            password,
            smt_cores=apply.read_smt_cores(),
        )
    except (OSError, vault.VaultError) as exc:
        sys.stderr.write(f"pinka-installer: {exc}\n")
        return EXIT_ACTION
    written = result.write()
    for path in written:
        shown = path.relative_to(paths.infra) if path.is_relative_to(paths.infra) else path
        print(f"wrote {shown}")
    if not written:
        print("nothing to change")
    for warning in result.warnings:
        print(f"warning: {warning}")
    if result.shown_once:
        print(
            "note: the generated restic password is in the vault (vault_restic_password); "
            "keep a copy outside this host: without it the backups cannot be restored"
        )
    return EXIT_OK


def cmd_tui(args: argparse.Namespace, paths: Paths) -> int:
    """Start the installer TUI.

    Args:
        args: Parsed options.
        paths: Installer paths.

    Returns:
        Exit status of the installation.
    """
    from pinka_installer.tui.app import run  # noqa: PLC0415 - Textual only for the TUI

    return run(
        paths, machine=args.machine, answers=args.answers, bootstrap_args=list(args.bootstrap_arg)
    )


COMMANDS: dict[str, Callable[[argparse.Namespace, Paths], int]] = {
    "validate": cmd_validate,
    "describe": cmd_describe,
    "apply": cmd_apply,
    "tui": cmd_tui,
}


def main(argv: list[str]) -> int:
    """Entry point.

    Args:
        argv: Arguments without the program name.

    Returns:
        Exit status.
    """
    args = build_parser().parse_args(argv)
    paths = Paths(infra=args.infra.resolve()) if args.infra else Paths()
    return COMMANDS[args.command](args, paths)
