"""``python -m pinka_test_distros {list,run}`` (``make test-distros``)."""

from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from pinka_lockcheck import bundle
from pinka_test_distros import checkmode, harness, results, targets
from pinka_test_distros.incus import Incus, IncusError, Runner, run_command

INFRA = Path(__file__).resolve().parents[3]
TARGETS = INFRA / "tests" / "distros" / "targets.yml"
RESULTS = INFRA / "tests" / "distros" / "results.yml"
ANSWERS = INFRA / "tests" / "answers" / "ci.yml"
AUTHKEY_ENV = "PINKA_TEST_TAILSCALE_AUTHKEY"


def build_parser() -> argparse.ArgumentParser:
    """The command line.

    Returns:
        The parser.
    """
    parser = argparse.ArgumentParser(
        prog="pinka-test-distros",
        description="Bootstrap the lab in disposable Incus VMs, one per test target.",
        epilog=f"The VMs join the test tailnet with the ephemeral auth key in ${AUTHKEY_ENV}.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="list the test targets and their last results")
    run = sub.add_parser("run", help="test the given targets (all without --target)")
    run.add_argument("--target", action="append", default=[], help="target id (repeatable)")
    run.add_argument("--project", default="pinka-test-distros", help="Incus project of the VMs")
    run.add_argument("--profile", default="default", help="Incus profile with network and disk")
    run.add_argument(
        "--out",
        type=Path,
        default=INFRA / "build" / "test-distros",
        help="directory for logs (default: build/test-distros)",
    )
    run.add_argument(
        "--keep",
        choices=harness.KEEP,
        default="failed",
        help="keep VMs never, after a failed test (default) or always; kept VMs are stopped",
    )
    run.add_argument(
        "--keep-running",
        action="store_true",
        help="leave kept VMs running (then more than one test VM can run at a time)",
    )
    run.add_argument(
        "--accept-unverified-images",
        action="store_true",
        help="run although the image aliases are not verified yet",
    )
    run.add_argument(
        "--no-record", action="store_true", help="do not write tests/distros/results.yml"
    )
    check = sub.add_parser(
        "check-mode",
        help="CI fast lane: the provider-agnostic roles (or, extended, the whole play) "
        "in check mode in a container",
    )
    check.add_argument("--image", required=True, help="key under containers.ci_images")
    check.add_argument(
        "--extended",
        action="store_true",
        help="run the whole play and skip components whose sources are not verified "
        "(listed as not tested)",
    )
    check.add_argument(
        "--summary",
        type=Path,
        help="file the extended run appends its Markdown summary to "
        "(default: $GITHUB_STEP_SUMMARY when set)",
    )
    check.add_argument("--keep", action="store_true", help="keep the container afterwards")
    return parser


def _git(*args: str) -> str:
    done = subprocess.run(  # noqa: S603 - fixed git arguments
        ["git", "-C", str(INFRA), *args],  # noqa: S607 - git from PATH
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def lab_running(run: Runner) -> bool:
    """Whether this host is a lab host whose lab is up (experiments may be running).

    Args:
        run: Command runner.

    Returns:
        True if ``pinka-lab.target`` is active.
    """
    if not Path("/etc/pinka/lab.json").exists():
        return False
    return run(["systemctl", "is-active", "--quiet", "pinka-lab.target"], timeout=30).ok


def repo_archive(directory: Path) -> Path:
    """A tar archive of a clone of the repository at HEAD (with .git, without uncommitted files).

    Args:
        directory: Where to write it.

    Returns:
        The archive.
    """
    clone = directory / "pinka"
    subprocess.run(  # noqa: S603 - fixed git arguments
        ["git", "clone", "--quiet", "--no-local", str(INFRA), str(clone)],  # noqa: S607 - git from PATH
        check=True,
    )
    archive = directory / "pinka.tar"
    with tarfile.open(archive, "w") as tar:
        tar.add(clone, arcname="pinka")
    return archive


def cmd_list(file: targets.TargetFile) -> int:
    """Print the targets and their last results.

    Args:
        file: The targets.

    Returns:
        0.
    """
    recorded = {r["target"]: r for r in results.load(RESULTS)}
    for t in file.targets:
        record = recorded.get(t.id)
        when = f" ({record['tested_on']})" if record else ""
        variant = t.firewall + (", SELinux enforcing" if t.selinux else "")
        print(f"{t.id:24} {t.image_ref:32} {variant:30} {results.status(record)}{when}")
    return 0


def cmd_run(  # noqa: PLR0911 - one exit per refusal
    args: argparse.Namespace, file: targets.TargetFile, run: Runner
) -> int:
    """Test the selected targets.

    Args:
        args: Parsed options.
        file: The targets.
        run: Command runner.

    Returns:
        0 if every test passed, 1 otherwise, 2 if the run was refused.
    """
    selected = file.select(args.target)
    if file.unverified and not args.accept_unverified_images:
        sys.stderr.write(f"refused: {file.unverified}; pass --accept-unverified-images\n")
        return 2
    authkey = os.environ.get(AUTHKEY_ENV, "")
    if not authkey:
        sys.stderr.write(
            f"refused: set {AUTHKEY_ENV} to an ephemeral auth key of the test tailnet\n"
        )
        return 2
    if lab_running(run):
        sys.stderr.write("refused: the lab is running on this host; stop it first (pinka stop)\n")
        return 2
    if _git("status", "--porcelain"):
        sys.stderr.write("note: uncommitted changes are not tested; the VMs get HEAD\n")
    commit = _git("rev-parse", "HEAD")
    incus = Incus(run, args.project)
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = args.out / stamp
    records = []
    with tempfile.TemporaryDirectory() as tmp:
        try:
            package = bundle.build(INFRA, Path(tmp, "release"), ref="HEAD")
        except bundle.PackageError as exc:
            sys.stderr.write(f"refused: the release package of HEAD: {exc}\n")
            return 2
        if problems := bundle.check_package(package):
            sys.stderr.write(bundle.describe(problems) + "\n")
            return 2
        secrets_env = Path(tmp, "secrets.env")
        secrets_env.touch(mode=0o600)
        secrets_env.write_text(f"PINKA_SECRET_TAILSCALE_AUTHKEY={authkey}\n", encoding="utf-8")
        settings = harness.Settings(
            repo_tar=repo_archive(Path(tmp)),
            package=package,
            answers=ANSWERS,
            commit=commit,
            out_dir=out_dir,
            secrets_env=secrets_env,
            profile=args.profile,
            keep=args.keep,
            keep_running=args.keep_running,
        )
        try:
            incus.ensure_project()
        except IncusError as exc:
            sys.stderr.write(f"incus: {exc}\n")
            return 2
        for target in selected:
            print(f"== {target.id}: {target.image_ref}", flush=True)
            test = harness.Test(target, incus, settings)
            record = test.run()
            records.append(record)
            kept = f" (kept: {test.name})" if test.kept else ""
            print(f"   {results.status(record)}{kept}; logs in {out_dir / target.id}", flush=True)
    if not args.no_record:
        results.update(RESULTS, records, [t.id for t in file.targets])
        print(
            f"recorded in {RESULTS.relative_to(INFRA)} (tested commit {commit[:12]});"
            " run make generate and commit both"
        )
    return 0 if all(results.status(r) == "passed" for r in records) else 1


def main(argv: list[str], run: Runner = run_command) -> int:
    """Entry point.

    Args:
        argv: Arguments without the program name.
        run: Command runner (tests replace it).

    Returns:
        Exit status.
    """
    args = build_parser().parse_args(argv)
    if args.command == "check-mode":
        step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
        summary = args.summary or (Path(step_summary) if step_summary else None)
        return checkmode.main(
            args.image,
            run,
            INFRA / "build" / "check-mode",
            extended=args.extended,
            commit=_git("rev-parse", "HEAD"),
            summary=summary,
            keep=args.keep,
        )
    try:
        file = targets.load(TARGETS)
        if args.command == "list":
            return cmd_list(file)
        return cmd_run(args, file, run)
    except targets.TargetError as exc:
        sys.stderr.write(f"pinka-test-distros: {exc}\n")
        return 2
