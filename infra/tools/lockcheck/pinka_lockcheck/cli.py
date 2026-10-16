"""``python -m pinka_lockcheck {validate,check,todos}``.

* ``validate FILE=SCHEMA...``: validate YAML files against JSON Schemas
  (``make lint``; no network).
* ``check``: the lock check (``make lock-check``): schema, remaining markers,
  and, unless ``--offline``, every container digest, binary checksum and
  repository key fingerprint over the network.
* ``todos``: list every ``TODO(verify)`` marker in the repository
  (``make verify-todos``).
* ``fetch NAME --dest PATH [--member FILE]``: download a pinned binary of
  the lock for this machine and install it only if its sha256 matches; from
  a tar.gz archive, the one regular file named ``FILE`` (development tools
  such as OPA for ``make test``).
* ``collections --requirements FILE --dest DIR --downloads DIR --galaxy CMD
  [--package PACKAGE]``: install the locked Ansible collections for
  ``make setup``, from Galaxy, or when Galaxy is unreachable from the
  artifacts bundled in a release package (``pinka_lockcheck.devsetup``).
* ``package build (--tag TAG | --ref REF) --out DIR``: build the release
  package (``make release-package``; ``pinka_lockcheck.bundle``).
* ``package check PACKAGE``: check a release package or an unpacked one
  (``make release-package-check``).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from pinka_lockcheck import bundle, checks, devsetup, net

CATEGORIES = ("containers", "binaries", "keys")
# Files that discuss the marker or mirror the lock file, and tests that use it as data.
TODO_EXCLUDES = (
    "CLAUDE.md",
    "docs/BUILD_PROMPT.md",
    "docs/DOCS_SPEC.md",
    "docs/IMPLEMENTATION_NOTES.md",
    "docs/how-to/update-versions.md",
    "docs/reference/versions.md",
    "docs/reference/component-sources.md",
    "ansible/group_vars/all/versions.yml",
    "tools/lockcheck/",
    "tools/gen/pinka_gen/versions.py",
    "bootstrap/tests/",
    "ansible/tests/",
    "ansible/filter_plugins/pinka.py",
    "tools/gen/tests/",
    "docs/distros/adding-a-family.md",
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
    findings += checks.collection_findings(doc)
    # Inside an unpacked release package: its tree, collections and NOTICE.
    if (lock.parent / bundle.RELEASE_JSON).exists():
        findings += [
            checks.Finding(checks.ERROR, bundle.RELEASE_DIR, problem)
            for problem in bundle.check_tree(lock.parent)
        ]
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


def _fetch(lock: Path, name: str, dest: Path, member: str | None) -> int:
    try:
        digest = checks.fetch_binary(checks.load(lock), name, dest, member=member)
    except (checks.FetchRefusedError, net.FetchError) as exc:
        sys.stderr.write(f"fetch: {exc}\n")
        return 1
    print(f"fetch: {dest} sha256 {digest}", file=sys.stderr)
    return 0


def _collections(args: argparse.Namespace) -> int:
    try:
        installed = devsetup.install_collections(
            checks.load(args.lock),
            args.requirements,
            args.dest,
            args.downloads,
            galaxy=args.galaxy,
            package=args.package or None,
        )
    except checks.FetchRefusedError as exc:
        sys.stderr.write(f"collections: {exc}; nothing unverified was installed\n")
        return 1
    if installed.source == "package":
        print(
            f"collections: Galaxy is unreachable; installed the artifacts of {args.package}"
            " after their sha256 matched the lock",
            file=sys.stderr,
        )
    if installed.unpinned:
        print(
            "collections: installed from Galaxy without a verified pin in the lock yet: "
            + ", ".join(installed.unpinned)
            + " (pin them with make lock-collections)",
            file=sys.stderr,
        )
    return 0


def _package(args: argparse.Namespace) -> int:
    if args.action == "build":
        try:
            package = bundle.build(Path.cwd(), args.out, tag=args.tag, ref=args.ref or "HEAD")
        except (bundle.PackageError, checks.FetchRefusedError, ValueError) as exc:
            sys.stderr.write(f"package: {exc}\n")
            return 1
        print(f"package: {package}", file=sys.stderr)
        return 0
    target = args.package
    problems = bundle.check_tree(target) if target.is_dir() else bundle.check_package(target)
    if problems:
        print(bundle.describe(problems))
    print(f"package check: {target}: {len(problems)} problem(s)", file=sys.stderr)
    return 1 if problems else 0


def main(argv: Sequence[str]) -> int:
    """Run a lock checker command.

    Args:
        argv: Arguments without the program name.

    Returns:
        0 if everything passed, 1 on findings, 2 on usage errors.
    """
    parser = argparse.ArgumentParser(prog="pinka-lockcheck", description=__doc__.split("\n")[0])
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
    fetch = sub.add_parser("fetch", help="install a pinned binary after checking it")
    fetch.add_argument("name", help="entry under binaries")
    fetch.add_argument("--dest", type=Path, required=True, help="path of the executable")
    fetch.add_argument("--member", help="file name of the executable in a tar.gz archive")
    fetch.add_argument("--lock", type=Path, default=Path("versions.lock.yml"))
    collections = sub.add_parser(
        "collections", help="install the locked Ansible collections (Galaxy or a release package)"
    )
    collections.add_argument("--requirements", type=Path, required=True)
    collections.add_argument("--dest", type=Path, required=True, help="collections path")
    collections.add_argument("--downloads", type=Path, required=True, help="tarball directory")
    collections.add_argument("--galaxy", required=True, help="the ansible-galaxy command")
    collections.add_argument("--lock", type=Path, default=Path("versions.lock.yml"))
    collections.add_argument(
        "--package", default="", help="release package to use when Galaxy is unreachable"
    )
    package = sub.add_parser("package", help="build or check the release package")
    actions = package.add_subparsers(dest="action", required=True)
    build = actions.add_parser("build", help="build the release package of a tag or commit")
    which = build.add_mutually_exclusive_group(required=True)
    which.add_argument("--tag", help="the release tag (infra-vX.Y.Z)")
    which.add_argument("--ref", help="a commit, for test builds (no tag)")
    build.add_argument("--out", type=Path, required=True, help="output directory")
    check_package = actions.add_parser("check", help="check a release package")
    check_package.add_argument("package", type=Path, help="the package or its unpacked tree")
    args = parser.parse_args(list(argv))
    if args.command == "collections":
        return _collections(args)
    if args.command == "package":
        return _package(args)
    if args.command == "fetch":
        return _fetch(args.lock, args.name, args.dest, args.member)
    if args.command == "validate":
        return _validate(args.pairs)
    if args.command == "check":
        only = [c for c in args.only.split(",") if c]
        unknown = sorted(set(only) - set(CATEGORIES))
        if unknown:
            parser.error(f"unknown check(s): {', '.join(unknown)}")
        return _check(args.lock, offline=args.offline, only=only)
    return _todos(args.exclude)
