"""``pinka-platform``: validate a platform release against contract v2."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pinka_platform import contract as contract_mod
from pinka_platform import names, validate
from pinka_platform.loader import MANIFEST_NAME, load_file
from pinka_platform.problems import ManifestError

EXIT_VALID = 0
EXIT_REFUSED = 65
EXIT_USAGE = 2
EXIT_CODES = {
    EXIT_VALID: ("valid", "the release passed every check"),
    EXIT_USAGE: ("usage", "wrong arguments"),
    EXIT_REFUSED: ("refused", "the release breaks contract v2; every reason is printed"),
}


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
    return parser


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
        Exit code (0 valid, 65 refused).
    """
    args = build_parser().parse_args(argv)
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
