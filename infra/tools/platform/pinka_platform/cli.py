"""``pinka-platform``: validate and deploy a platform release (contract v2).

``validate`` is for platform developers and the installer; ``deploy`` and
``purge`` are the deployment steps the ``platform`` role and ``pinka platform
purge`` run as root on the lab host (BUILD_PROMPT §7.14).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pinka_platform import contract as contract_mod
from pinka_platform import deploy, mirror, names, validate
from pinka_platform.loader import MANIFEST_NAME, load_file
from pinka_platform.problems import ManifestError, Problem

EXIT_VALID = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_REFUSED = 65
EXIT_CODES = {
    EXIT_VALID: ("valid", "the release passed every check, or the deployment step succeeded"),
    EXIT_FAILED: (
        "failed",
        "a deployment step failed (fetch, build, image pull, publish, purge); "
        "the reason is printed",
    ),
    EXIT_USAGE: ("usage", "wrong arguments"),
    EXIT_REFUSED: ("refused", "the release breaks contract v2; every reason is printed"),
}
DEPLOY_STEPS = (
    "prepare",
    "build",
    "pull",
    "publish",
    "retire-plan",
    "render",
    "principals",
    "activate",
    "clean",
)


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser.

    Returns:
        The parser.
    """
    parser = argparse.ArgumentParser(
        prog="pinka-platform",
        description=(
            "Validate a platform release against contract v2: the manifest's schema, the "
            "reserved names and the host, the invariant checks and the release's files."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser(
        "validate",
        help="validate a staged release directory",
        description="Validate a staged release directory and print every problem, or a summary.",
    )
    check.add_argument(
        "release", type=Path, help="release directory with platform-manifest.yaml at its root"
    )
    check.add_argument(
        "--contract",
        type=Path,
        default=contract_mod.DEFAULT_DIR,
        help="contract directory (default: contract/ of this infra checkout)",
    )
    check.add_argument(
        "--no-host-check",
        action="store_true",
        help="skip the checks against the accounts, groups and commands of this host",
    )
    check.add_argument(
        "--built",
        action="store_true",
        help="the build has run: commands below .venv/ must exist too",
    )
    check.add_argument("--json", action="store_true", help="print the result as JSON")

    dep = sub.add_parser(
        "deploy",
        help="one step of a deployment on the lab host (run by the platform role, as root)",
        description=(
            "One step of a platform deployment (BUILD_PROMPT §7.14). Each step prints one "
            "JSON object. prepare fetches, stages and validates and writes a run directory "
            "below the staging directory; the other steps work on that run directory."
        ),
    )
    dep.add_argument("step", choices=DEPLOY_STEPS, help="the step")
    dep.add_argument("--settings", type=Path, help="prepare: the settings file the role writes")
    dep.add_argument("--run", type=Path, help="the run directory prepare created")
    dep.add_argument(
        "--inspect",
        action="store_true",
        help="prepare: stop after the validation and delete the run directory again",
    )
    _common(dep)

    gone = sub.add_parser(
        "purge",
        help="remove a replaced platform's releases, state, restricted area and database",
        description=(
            "Remove a replaced platform's releases, content, state, restricted area and "
            "database. Never the active platform; its accounts stay locked."
        ),
    )
    gone.add_argument("platform", help="name of the replaced platform")
    gone.add_argument("--confirm", required=True, help="the platform name again, typed")
    gone.add_argument(
        "--remove-secrets",
        action="store_true",
        help="also remove its secrets directories, database password files and private "
        "signing key (kept by default)",
    )
    _common(gone)
    return parser


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--contract",
        type=Path,
        default=contract_mod.DEFAULT_DIR,
        help="contract directory (default: the installed contract)",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="prefix for every host path (tests and fixtures; default: /)",
    )
    parser.add_argument(
        "--no-host-check",
        action="store_true",
        help="skip the checks against the accounts, groups and commands of this host",
    )


def run_command(argv: Sequence[str]) -> mirror.Done:
    """Run a command without a shell.

    Args:
        argv: The command.

    Returns:
        The outcome.
    """
    done = subprocess.run(list(argv), capture_output=True, text=True, check=False)  # noqa: S603
    return mirror.Done(done.returncode, done.stdout, done.stderr)


def _problems(problems: Sequence[Problem]) -> list[dict[str, str]]:
    return [{"code": p.code, "where": p.where, "message": p.message} for p in problems]


def vault_text(keys: dict[str, list[str]]) -> str:
    """The vault keys a release needs, for the summary.

    Args:
        keys: ``{"needed": [...], "missing": [...]}``.

    Returns:
        Lines for the summary text ("" when the release needs none).
    """
    if not keys["needed"]:
        return ""
    missing = set(keys["missing"])
    lines = ["Vault keys:"]
    lines += [f"  {k}{' (missing)' if k in missing else ''}" for k in keys["needed"]]
    return "\n".join(lines) + "\n"


def _deploy(args: argparse.Namespace, paths: deploy.Paths) -> dict[str, Any]:
    contract = contract_mod.load(args.contract)
    if args.step == "prepare":
        if args.settings is None:
            raise deploy.DeployError("prepare needs --settings")
        settings = deploy.Settings.from_json(json.loads(args.settings.read_text(encoding="utf-8")))
        plan = deploy.prepare(
            settings,
            paths,
            contract,
            host=(lambda _: None)
            if args.no_host_check
            else (lambda name: names.host_facts(name, paths.etc_platform)),
            inspect=args.inspect,
        )
        return {
            "changed": not args.inspect,
            "run_dir": plan["run_dir"],
            "platform": plan["platform"],
            "summary": plan["summary"],
            "summary_text": validate.summary_text(plan["summary"]) + vault_text(plan["vault_keys"]),
            "replace": plan["replace"],
            "tools": plan["tools"],
            "jobs": plan["jobs"],
            "vault_keys": plan["vault_keys"],
        }
    if args.step == "retire-plan":
        active = deploy.read_active(paths)
        if active is None:
            raise deploy.DeployError("no platform is active")
        return {"changed": False, **deploy.retire_plan(active, contract, paths)}
    if args.run is None:
        raise deploy.DeployError(f"{args.step} needs --run")
    if args.step == "clean":
        deploy.clean(args.run, paths)
        return {"changed": True}
    plan = deploy.load_plan(args.run)
    steps = {
        "build": lambda: deploy.build(plan, paths, contract, run_command),
        "pull": lambda: deploy.pull(plan, run_command),
        "publish": lambda: deploy.publish(plan, paths),
        "render": lambda: deploy.render_step(plan, paths, contract),
        "principals": lambda: deploy.principals_step(plan, paths, contract),
        "activate": lambda: deploy.activate(plan, paths),
    }
    return steps[args.step]()


def _run_step(args: argparse.Namespace) -> int:
    paths = deploy.Paths.under(args.root) if args.root else deploy.Paths()
    try:
        if args.command == "purge":
            result = deploy.purge(
                args.platform, args.confirm, paths, run_command, remove_secrets=args.remove_secrets
            )
        else:
            result = _deploy(args, paths)
    except deploy.Refused as exc:
        payload = {
            "changed": False,
            "refused": True,
            "problems": _problems(exc.problems),
            "problems_text": [str(p) for p in exc.problems],
        }
        sys.stdout.write(json.dumps(payload, indent=2) + "\n")
        for problem in exc.problems:
            sys.stderr.write(f"{problem}\n")
        sys.stderr.write(f"refused: {len(exc.problems)} problem(s)\n")
        return EXIT_REFUSED
    except (deploy.DeployError, mirror.FetchError, OSError, KeyError, ValueError) as exc:
        sys.stdout.write(json.dumps({"changed": False, "error": str(exc)}) + "\n")
        sys.stderr.write(f"error: {exc}\n")
        return EXIT_FAILED
    sys.stdout.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return EXIT_VALID


def _platform_name(release: Path, contract: contract_mod.Contract) -> str:
    # The host check exempts the names of the same platform's active deployment.
    try:
        manifest = load_file(release / MANIFEST_NAME, int(contract.caps["manifest_bytes"]))
    except ManifestError:
        return ""
    name = manifest.get("name")
    return name if isinstance(name, str) else ""


def main(argv: list[str]) -> int:
    """Run the CLI.

    Args:
        argv: Arguments without the program name.

    Returns:
        Exit code (0 valid or done, 1 failed, 65 refused).
    """
    args = build_parser().parse_args(argv)
    if args.command in ("deploy", "purge"):
        return _run_step(args)
    contract = contract_mod.load(args.contract)
    host = None
    if not args.no_host_check:
        host = names.host_facts(_platform_name(args.release, contract))
    result = validate.validate_release(args.release, contract, host, built=args.built)
    if args.json:
        payload: dict[str, object] = {
            "valid": result.ok,
            "problems": [
                {"code": p.code, "where": p.where, "message": p.message} for p in result.problems
            ],
        }
        if result.ok and result.manifest is not None:
            payload["summary"] = validate.summary(result.manifest, contract)
        sys.stdout.write(json.dumps(payload, indent=2) + "\n")
    elif result.ok and result.manifest is not None:
        sys.stdout.write(validate.summary_text(validate.summary(result.manifest, contract)))
    else:
        for problem in result.problems:
            sys.stderr.write(f"{problem}\n")
        sys.stderr.write(f"refused: {len(result.problems)} problem(s)\n")
    return EXIT_VALID if result.ok else EXIT_REFUSED
