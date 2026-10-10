"""Argument parsing and dispatch for the repository checks.

Heavy modules are imported lazily so that ``privacy`` works with the standard
library alone.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for all subcommands.

    Returns:
        The configured parser.
    """
    parser = argparse.ArgumentParser(
        prog="pinka-repo-checks",
        description="Repository quality checks for Pokus components.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    privacy = sub.add_parser(
        "privacy",
        help="scan files for personal or installation-specific data",
        description=(
            "Scan text files for personal or installation-specific data: e-mail "
            "addresses outside RFC 2606 domains, public IP addresses, MAC addresses "
            "outside the documentation range, non-nil UUIDs, SSH/PGP key material "
            "outside test-key fixtures, credential tokens, and the optional "
            "maintainer denylist from the PINKA_PRIVACY_DENYLIST environment variable. "
            "Inside a git work tree it also checks every commit message reachable "
            "from HEAD for attribution lines (Co-Authored-By and the like)."
        ),
    )
    privacy.add_argument(
        "paths",
        nargs="*",
        type=Path,
        default=[Path()],
        help="files or directories to scan (default: current directory)",
    )
    privacy.add_argument(
        "--show-matches",
        action="store_true",
        help="print matched values (local debugging only; never in CI logs)",
    )

    docs = sub.add_parser(
        "docs-lint",
        help="validate front matter, code blocks and qa markers of documentation pages",
    )
    docs.add_argument("--docs-dir", type=Path, default=Path("docs"), help="MkDocs docs_dir")
    docs.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="NAME",
        help="page path relative to docs-dir that is not part of the site (repeatable)",
    )
    docs.add_argument(
        "--component",
        required=True,
        help="component name used for release tags (<component>-vX.Y.Z)",
    )
    docs.add_argument(
        "--release",
        action="store_true",
        help="release mode: stale or missing last_verified fails instead of warning",
    )

    links = sub.add_parser(
        "links",
        help="check external links in Markdown files (weekly, non-blocking in CI)",
    )
    links.add_argument("paths", nargs="*", type=Path, default=[Path()])
    links.add_argument("--timeout", type=float, default=15.0, help="seconds per request")

    portability = sub.add_parser(
        "portability",
        help="fail if Ansible code outside platform_facts reads distribution facts",
    )
    portability.add_argument(
        "--ansible-dir", type=Path, default=Path("ansible"), help="the Ansible directory"
    )

    mermaid = sub.add_parser(
        "mermaid-extract",
        help="write each Mermaid block of the documentation to its own .mmd file",
    )
    mermaid.add_argument("--docs-dir", type=Path, default=Path("docs"))
    mermaid.add_argument("--exclude", action="append", default=[], metavar="NAME")
    mermaid.add_argument("--out", type=Path, required=True, help="output directory")
    return parser


def main(argv: list[str]) -> int:
    """Run one check.

    Args:
        argv: Command-line arguments without the program name.

    Returns:
        Process exit code: 0 when the check passes, 1 when it finds problems,
        2 for usage errors.
    """
    args = build_parser().parse_args(argv)
    if args.command == "privacy":
        from pinka_repo_checks import privacy  # noqa: PLC0415 - stdlib-only path

        return privacy.run(args.paths, show_matches=args.show_matches)
    if args.command == "docs-lint":
        from pinka_repo_checks import docs_lint  # noqa: PLC0415 - needs dev dependencies

        return docs_lint.run(
            args.docs_dir,
            excludes=args.exclude,
            component=args.component,
            release=args.release,
        )
    if args.command == "links":
        from pinka_repo_checks import links  # noqa: PLC0415 - stdlib-only path

        return links.run(args.paths, timeout=args.timeout)
    if args.command == "portability":
        from pinka_repo_checks import portability  # noqa: PLC0415 - stdlib-only path

        return portability.run(args.ansible_dir)
    if args.command == "mermaid-extract":
        from pinka_repo_checks import docs_lint  # noqa: PLC0415 - needs dev dependencies

        return docs_lint.extract_mermaid(args.docs_dir, excludes=args.exclude, out=args.out)
    return 2  # pragma: no cover - argparse enforces the choices
