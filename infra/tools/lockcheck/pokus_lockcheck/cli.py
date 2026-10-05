"""``python -m pokus_lockcheck {validate,check,todos}``.

* ``validate FILE=SCHEMA...``: validate YAML files against JSON Schemas
  (``make lint``; no network).
* ``check``: the lock check (``make lock-check``): schema, remaining markers,
  and, unless ``--offline``, every container digest, binary checksum and
  repository key fingerprint over the network.
* ``todos``: list every ``TODO(verify)`` marker in the repository
  (``make verify-todos``).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from pokus_lockcheck import checks

CATEGORIES = ("containers", "binaries", "keys")
# Files that discuss the marker or mirror the lock file, and tests that use it as data.
TODO_EXCLUDES = (
    "CLAUDE.md",
    "docs/BUILD_PROMPT.md",
    "docs/DOCS_SPEC.md",
    "docs/IMPLEMENTATION_NOTES.md",
    "docs/how-to/update-versions.md",
    "docs/reference/versions.md",
    "ansible/group_vars/all/versions.yml",
    "tools/lockcheck/",
    "tools/gen/pokus_infra_gen/versions.py",
    "bootstrap/tests/",
)


def _validate(pairs: Sequence[str]) -> int:
    failed = 0
    for pair in pairs:
        file_name, _, schema_name = pair.partition("=")
        if not schema_name:
            sys.stderr.write(f"expected FILE=SCHEMA, got {pair!r}\n")
            return 2
        findings = checks.schema_findings(checks.load(Path(file_name)), Path(schema_name))
        for finding in findings:
            print(f"{file_name}: {finding}")
        print(f"schema: {file_name}: {len(findings)} error(s)", file=sys.stderr)
        failed += bool(findings)
    return 1 if failed else 0


def _check(lock: Path, *, offline: bool, only: Sequence[str]) -> int:
    doc = checks.load(lock)
    findings = checks.schema_findings(doc)
    findings += checks.marker_findings(lock.read_text(encoding="utf-8"))
    if not offline and not any(f.where == "(root)" for f in findings):
        if "containers" in only:
            findings += checks.container_findings(doc)
        if "binaries" in only:
            findings += checks.binary_findings(doc)
        if "keys" in only:
            findings += checks.key_findings(doc)
    for finding in findings:
        print(finding)
    errors = sum(f.severity == checks.ERROR for f in findings)
    warnings = len(findings) - errors
    mode = "offline" if offline else "network: " + ", ".join(only)
    print(f"lock-check ({mode}): {errors} error(s), {warnings} warning(s)", file=sys.stderr)
    return 1 if errors else 0


def tracked_files() -> list[str]:
    """List the files git tracks or would track (tracked plus untracked, not ignored).

    Returns:
        Paths relative to the current directory.
    """
    out = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],  # noqa: S607
        check=True,
        capture_output=True,
    ).stdout
    return sorted({p for p in out.decode().split("\0") if p})


def _todos(excludes: Sequence[str]) -> int:
    count = 0
    for name in tracked_files():
        if any(name == e or (e.endswith("/") and name.startswith(e)) for e in excludes):
            continue
        path = Path(name)
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if checks.MARKER in line:
                print(f"{name}:{number}: {line.strip()}")
                count += 1
    print(f"verify-todos: {count} unverified marker(s)", file=sys.stderr)
    return 1 if count else 0


def main(argv: Sequence[str]) -> int:
    """Run a lock checker command.

    Args:
        argv: Arguments without the program name.

    Returns:
        0 if everything passed, 1 on findings, 2 on usage errors.
    """
    parser = argparse.ArgumentParser(prog="pokus-lockcheck", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate", help="validate YAML files against JSON Schemas")
    validate.add_argument("pairs", nargs="+", metavar="FILE=SCHEMA")
    check = sub.add_parser("check", help="check versions.lock.yml")
    check.add_argument("--lock", type=Path, default=Path("versions.lock.yml"))
    check.add_argument("--offline", action="store_true", help="schema and markers only")
    check.add_argument(
        "--only",
        default=",".join(CATEGORIES),
        help=f"network checks to run (comma-separated: {', '.join(CATEGORIES)})",
    )
    todos = sub.add_parser("todos", help="list TODO(verify) markers in the repository")
    todos.add_argument("--exclude", action="append", default=list(TODO_EXCLUDES))
    args = parser.parse_args(list(argv))
    if args.command == "validate":
        return _validate(args.pairs)
    if args.command == "check":
        only = [c for c in args.only.split(",") if c]
        unknown = sorted(set(only) - set(CATEGORIES))
        if unknown:
            parser.error(f"unknown check(s): {', '.join(unknown)}")
        return _check(args.lock, offline=args.offline, only=only)
    return _todos(args.exclude)
