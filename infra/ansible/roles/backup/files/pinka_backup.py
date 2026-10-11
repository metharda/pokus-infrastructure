#!/opt/pinka/ansible-venv/bin/python3 -I
# Managed by pinka (ansible/roles/backup/files/pinka_backup.py). Local changes will be overwritten.
"""Backups of the lab (BUILD_PROMPT §7.15).

Commands, all run as root by the backup role's units or by ``restore.yml``:

- ``pgdump``: ``pg_dump -Fc`` of the lab database through the PostgreSQL
  container into ``/srv/pinka/backups/pg/``, checked with ``pg_restore
  --list``; only the newest dumps are kept.
- ``run``: a restic backup of the configured paths (the repository is
  initialized on first use).
- ``prune``: ``restic forget --prune`` with the retention policy.
- ``snapshots``: the snapshots of this host as JSON.
- ``restore``: restore a snapshot's files in place.
- ``platform-record``: print the active platform's record (``active.json``)
  as the snapshot holds it, or ``null`` without one (``restic dump``).
- ``pgrestore``: load a dump into the lab database (``pg_restore --clean``).
- ``passwd``: change the repository password (``restic key passwd``).

The repository location, its password and (for SFTP) the SSH key are files in
the secrets directory and reach restic only through ``RESTIC_REPOSITORY_FILE``
and ``RESTIC_PASSWORD_FILE``, never on a command line. Every run records its
result as Prometheus metrics in the node exporter's textfile directory.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import IO, Any

REPO_MISSING = 10  # restic: repository does not exist (since 0.17.0)
INCOMPLETE = 3  # restic backup: some source data could not be read
# The active platform's record, and restic dump's message for a path the
# snapshot lacks (cmd/restic/cmd_dump.go of restic 0.19.1).
PLATFORM_RECORD = "/etc/pinka/platform/active.json"
DUMP_NOT_FOUND = "not found in snapshot"
Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def run(
    argv: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    stdin: IO[bytes] | None = None,
    stdout: IO[bytes] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command without a shell.

    Args:
        argv: Command and arguments.
        env: Extra environment variables.
        stdin: Binary input stream.
        stdout: Binary output stream (the command's output is not captured then).

    Returns:
        The completed process with text output (empty when ``stdout`` is given).
    """
    environment = {**os.environ, **(env or {})}
    try:
        done = subprocess.run(  # noqa: S603 - fixed argv
            list(argv),
            env=environment,
            stdin=stdin,
            stdout=stdout if stdout is not None else subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        return subprocess.CompletedProcess(list(argv), 127, "", str(exc))
    out = done.stdout.decode("utf-8", "replace") if isinstance(done.stdout, bytes) else ""
    return subprocess.CompletedProcess(
        list(argv), done.returncode, out, done.stderr.decode("utf-8", "replace")
    )


# --- metrics -------------------------------------------------------------------


def write_metrics(
    config: Mapping[str, Any], job: str, ok: bool, extra: Mapping[str, float]
) -> None:
    """Record a job's result as Prometheus text metrics.

    The last success time survives failed runs: it is kept in the state file.

    Args:
        config: Helper configuration (``state``, ``textfile_dir``).
        job: ``pgdump``, ``backup`` or ``prune``.
        ok: Whether the run succeeded.
        extra: Further gauges of this job, without the ``pinka_backup_`` prefix.
    """
    now = time.time()
    state_path = Path(config["state"])
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    entry = state.setdefault(job, {})
    entry["last_run"] = now
    if ok:
        entry["last_success"] = now
    state_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(state_path, json.dumps(state, indent=1) + "\n", 0o600)
    labels = f'{{job="{job}"}}'
    lines = [
        "# HELP pinka_backup_job_success 1 if the last run of the job succeeded.",
        "# TYPE pinka_backup_job_success gauge",
        f"pinka_backup_job_success{labels} {int(ok)}",
        "# HELP pinka_backup_job_last_run_timestamp_seconds Time of the job's last run.",
        "# TYPE pinka_backup_job_last_run_timestamp_seconds gauge",
        f"pinka_backup_job_last_run_timestamp_seconds{labels} {entry['last_run']:.0f}",
        "# HELP pinka_backup_job_last_success_timestamp_seconds Time of the job's last success.",
        "# TYPE pinka_backup_job_last_success_timestamp_seconds gauge",
        f"pinka_backup_job_last_success_timestamp_seconds{labels} {entry.get('last_success', 0):.0f}",
    ]
    for name, value in sorted(extra.items()):
        lines += [f"# TYPE pinka_backup_{name} gauge", f"pinka_backup_{name}{labels} {value}"]
    directory = Path(config["textfile_dir"])
    directory.mkdir(parents=True, exist_ok=True)
    _atomic_write(directory / f"pinka_backup_{job}.prom", "\n".join(lines) + "\n", 0o644)


def _atomic_write(path: Path, text: str, mode: int) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.chmod(mode)
    tmp.replace(path)


# --- restic --------------------------------------------------------------------


def restic_env(config: Mapping[str, Any]) -> dict[str, str]:
    """Environment that points restic at the repository and password files.

    Args:
        config: Helper configuration.

    Returns:
        Environment variables.
    """
    secrets = Path(config["secrets_dir"])
    return {
        "RESTIC_REPOSITORY_FILE": str(secrets / "repository"),
        "RESTIC_PASSWORD_FILE": str(secrets / "password"),
        "RESTIC_CACHE_DIR": str(config["cache_dir"]),
    }


def restic_argv(config: Mapping[str, Any], repository: str) -> list[str]:
    """The restic command with the SFTP options this repository needs.

    Args:
        config: Helper configuration.
        repository: Repository location (to recognize SFTP).

    Returns:
        Command prefix.
    """
    argv = [str(config["restic"])]
    if repository.startswith("sftp:"):
        ssh = [
            "-i",
            str(Path(config["secrets_dir"]) / "ssh_key"),
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={config['known_hosts']}",
        ]
        argv += ["-o", "sftp.args=" + " ".join(ssh)]
    return argv


def repository(config: Mapping[str, Any]) -> str:
    """Read the configured repository location.

    Args:
        config: Helper configuration.

    Returns:
        The location, or "" when backups are not configured.
    """
    try:
        return (Path(config["secrets_dir"]) / "repository").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def ensure_repository(config: Mapping[str, Any], base: Sequence[str], runner: Runner) -> str | None:
    """Initialize the repository if it does not exist yet.

    Args:
        config: Helper configuration.
        base: restic command prefix.
        runner: Command runner.

    Returns:
        None on success, else the error.
    """
    env = restic_env(config)
    found = runner([*base, "cat", "config"], env=env)
    if found.returncode == 0:
        return None
    if found.returncode != REPO_MISSING:
        return f"restic cat config: exit {found.returncode}: {found.stderr.strip()}"
    created = runner([*base, "init"], env=env)
    if created.returncode != 0:
        return f"restic init: exit {created.returncode}: {created.stderr.strip()}"
    return None


def summary_of(output: str) -> dict[str, Any]:
    """Find the summary message of ``restic backup --json``.

    Args:
        output: restic's standard output.

    Returns:
        The summary, or an empty dict.
    """
    for line in reversed(output.splitlines()):
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if isinstance(message, dict) and message.get("message_type") == "summary":
            return message
    return {}


def cmd_run(config: Mapping[str, Any], runner: Runner) -> int:
    """Back up the configured paths.

    Args:
        config: Helper configuration.
        runner: Command runner.

    Returns:
        0 on success, 1 on failure, 2 when backups are not configured.
    """
    location = repository(config)
    if not location:
        print("backup: no repository is configured (vault_restic_repository)", file=sys.stderr)
        return 2
    base = restic_argv(config, location)
    error = ensure_repository(config, base, runner)
    if error:
        print(f"backup: {error}", file=sys.stderr)
        write_metrics(config, "backup", ok=False, extra={})
        return 1
    paths = [p for p in config["paths"] if Path(p).exists()]
    with tempfile.TemporaryDirectory(prefix="pinka-backup-") as tmp:
        files_from = Path(tmp) / "paths"
        files_from.write_text("".join(f"{p}\n" for p in paths), encoding="utf-8")
        excludes = Path(tmp) / "excludes"
        excludes.write_text("".join(f"{p}\n" for p in config["excludes"]), encoding="utf-8")
        argv = [
            *base,
            "backup",
            "--json",
            "--host",
            str(config["host"]),
            "--tag",
            str(config["tag"]),
            "--files-from",
            str(files_from),
            "--exclude-file",
            str(excludes),
        ]
        done = runner(argv, env=restic_env(config))
    summary = summary_of(done.stdout)
    ok = done.returncode == 0
    if done.returncode == INCOMPLETE:
        print("backup: some files could not be read; the snapshot is incomplete", file=sys.stderr)
    elif not ok:
        print(
            f"backup: restic backup: exit {done.returncode}: {done.stderr.strip()}", file=sys.stderr
        )
    extra = {
        "data_added_bytes": float(summary.get("data_added", 0)),
        "files_processed": float(summary.get("total_files_processed", 0)),
        "duration_seconds": float(summary.get("total_duration", 0)),
    }
    write_metrics(config, "backup", ok=ok, extra=extra if summary else {})
    if ok:
        print(
            f"backup: snapshot {summary.get('snapshot_id', '?')} of {len(paths)} path(s)",
            file=sys.stderr,
        )
    return 0 if ok else 1


def cmd_prune(config: Mapping[str, Any], runner: Runner) -> int:
    """Forget old snapshots of this host and prune the repository.

    Args:
        config: Helper configuration.
        runner: Command runner.

    Returns:
        0 on success, 1 on failure, 2 when backups are not configured.
    """
    location = repository(config)
    if not location:
        print("backup: no repository is configured", file=sys.stderr)
        return 2
    keep = config["retention"]
    argv = [
        *restic_argv(config, location),
        "forget",
        "--host",
        str(config["host"]),
        "--tag",
        str(config["tag"]),
        "--keep-daily",
        str(keep["daily"]),
        "--keep-weekly",
        str(keep["weekly"]),
        "--keep-monthly",
        str(keep["monthly"]),
        "--prune",
    ]
    done = runner(argv, env=restic_env(config))
    if done.returncode != 0:
        print(
            f"backup: restic forget: exit {done.returncode}: {done.stderr.strip()}", file=sys.stderr
        )
    write_metrics(config, "prune", ok=done.returncode == 0, extra={})
    return 0 if done.returncode == 0 else 1


def cmd_snapshots(config: Mapping[str, Any], runner: Runner) -> int:
    """Print this host's snapshots as JSON.

    Args:
        config: Helper configuration.
        runner: Command runner.

    Returns:
        restic's exit status (2 when not configured).
    """
    location = repository(config)
    if not location:
        print("backup: no repository is configured", file=sys.stderr)
        return 2
    argv = [
        *restic_argv(config, location),
        "snapshots",
        "--json",
        "--host",
        str(config["host"]),
        "--tag",
        str(config["tag"]),
    ]
    done = runner(argv, env=restic_env(config))
    if done.returncode != 0:
        print(
            f"backup: restic snapshots: exit {done.returncode}: {done.stderr.strip()}",
            file=sys.stderr,
        )
        return done.returncode
    sys.stdout.write(done.stdout)
    return 0


def cmd_restore(
    config: Mapping[str, Any], snapshot: str, includes: Sequence[str], runner: Runner
) -> int:
    """Restore files of a snapshot in place (never deleting newer files).

    Args:
        config: Helper configuration.
        snapshot: Snapshot ID or ``latest``.
        includes: Absolute paths to restore.
        runner: Command runner.

    Returns:
        restic's exit status (2 when not configured).
    """
    location = repository(config)
    if not location:
        print("backup: no repository is configured", file=sys.stderr)
        return 2
    argv = [*restic_argv(config, location), "restore", snapshot, "--target", "/"]
    if snapshot == "latest":
        argv += ["--host", str(config["host"]), "--tag", str(config["tag"])]
    for path in includes:
        argv += ["--include", path]
    done = runner(argv, env=restic_env(config))
    if done.returncode != 0:
        print(
            f"backup: restic restore: exit {done.returncode}: {done.stderr.strip()}",
            file=sys.stderr,
        )
    return done.returncode


def cmd_platform_record(config: Mapping[str, Any], snapshot: str, runner: Runner) -> int:
    """Print the active platform's record as the snapshot holds it.

    restore.yml redeploys the release and the content that this record names.

    Args:
        config: Helper configuration.
        snapshot: Snapshot ID or ``latest``.
        runner: Command runner.

    Returns:
        0 with the record (``null`` when the snapshot has none), restic's
        exit status otherwise (2 when not configured).
    """
    location = repository(config)
    if not location:
        print("backup: no repository is configured", file=sys.stderr)
        return 2
    argv = [*restic_argv(config, location), "dump", snapshot, PLATFORM_RECORD]
    if snapshot == "latest":
        argv += ["--host", str(config["host"]), "--tag", str(config["tag"])]
    done = runner(argv, env=restic_env(config))
    if done.returncode != 0:
        if DUMP_NOT_FOUND in done.stderr:
            sys.stdout.write("null\n")
            return 0
        print(
            f"backup: restic dump: exit {done.returncode}: {done.stderr.strip()}", file=sys.stderr
        )
        return done.returncode
    try:
        record = json.loads(done.stdout)
    except ValueError:
        print(f"backup: {PLATFORM_RECORD} in the snapshot is not JSON", file=sys.stderr)
        return 1
    sys.stdout.write(json.dumps(record if isinstance(record, dict) else None) + "\n")
    return 0


def cmd_passwd(config: Mapping[str, Any], new_password_file: Path, runner: Runner) -> int:
    """Replace the repository key of the current password with one of a new password.

    Args:
        config: Helper configuration.
        new_password_file: File with the new password.
        runner: Command runner.

    Returns:
        restic's exit status (2 when not configured).
    """
    location = repository(config)
    if not location:
        print("backup: no repository is configured", file=sys.stderr)
        return 2
    argv = [
        *restic_argv(config, location),
        "key",
        "passwd",
        "--new-password-file",
        str(new_password_file),
    ]
    done = runner(argv, env=restic_env(config))
    if done.returncode != 0:
        print(
            f"backup: restic key passwd: exit {done.returncode}: {done.stderr.strip()}",
            file=sys.stderr,
        )
    return done.returncode


# --- PostgreSQL ----------------------------------------------------------------


def pg_command(
    config: Mapping[str, Any], program: str, *args: str, stdin: bool = False
) -> list[str]:
    """A PostgreSQL client command inside the container, as the superuser.

    The password is read inside the container from its mounted secret.

    Args:
        config: Helper configuration (``pgdump.container``).
        program: ``pg_dump`` or ``pg_restore``.
        *args: Arguments of the program.
        stdin: Whether the command reads standard input.

    Returns:
        The docker command.
    """
    script = (
        'PGPASSWORD="$(cat /run/secrets/postgres-superuser)" exec "$0" '
        '--host=/var/run/postgresql --username=postgres "$@"'
    )
    interactive = ["--interactive"] if stdin else []
    container = str(config["pgdump"]["container"])
    return [
        "docker",
        "exec",
        *interactive,
        "--user",
        "postgres",
        container,
        "sh",
        "-c",
        script,
        program,
        *args,
    ]


def cmd_pgdump(config: Mapping[str, Any], runner: Runner) -> int:
    """Dump the lab database, check the dump and keep the newest ones.

    Args:
        config: Helper configuration.
        runner: Command runner.

    Returns:
        0 on success, 1 on failure.
    """
    settings = config["pgdump"]
    directory = Path(settings["dir"])
    directory.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    final = directory / f"{settings['database']}-{stamp}.dump"
    partial = directory / f".{final.name}.partial"
    started = time.monotonic()
    with partial.open("wb") as out:
        partial.chmod(0o600)
        dumped = runner(
            pg_command(config, "pg_dump", "--format=custom", f"--dbname={settings['database']}"),
            stdout=out,
        )
    error = None
    if dumped.returncode != 0:
        error = f"pg_dump: exit {dumped.returncode}: {dumped.stderr.strip()}"
    else:
        with partial.open("rb") as dump:
            listed = runner(pg_command(config, "pg_restore", "--list", stdin=True), stdin=dump)
        if listed.returncode != 0:
            error = f"pg_restore --list: exit {listed.returncode}: {listed.stderr.strip()}"
    if error:
        partial.unlink(missing_ok=True)
        print(f"backup: {error}", file=sys.stderr)
        write_metrics(config, "pgdump", ok=False, extra={})
        return 1
    partial.replace(final)
    dumps = sorted(directory.glob(f"{settings['database']}-*.dump"))
    for old in dumps[: max(0, len(dumps) - int(settings["keep"]))]:
        old.unlink()
    extra = {
        "dump_bytes": float(final.stat().st_size),
        "duration_seconds": round(time.monotonic() - started, 3),
    }
    write_metrics(config, "pgdump", ok=True, extra=extra)
    print(f"backup: {final}", file=sys.stderr)
    return 0


def cmd_pgrestore(config: Mapping[str, Any], dump: Path, runner: Runner) -> int:
    """Load a dump into the lab database, replacing the objects it contains.

    Args:
        config: Helper configuration.
        dump: Custom-format dump.
        runner: Command runner.

    Returns:
        pg_restore's exit status.
    """
    database = str(config["pgdump"]["database"])
    argv = pg_command(
        config,
        "pg_restore",
        "--clean",
        "--if-exists",
        "--single-transaction",
        "--exit-on-error",
        f"--dbname={database}",
        stdin=True,
    )
    with dump.open("rb") as handle:
        done = runner(argv, stdin=handle)
    if done.returncode != 0:
        print(f"backup: pg_restore: exit {done.returncode}: {done.stderr.strip()}", file=sys.stderr)
    return done.returncode


def main(argv: Sequence[str], runner: Runner = run) -> int:
    """Dispatch a command.

    Args:
        argv: Arguments without the program name.
        runner: Command runner.

    Returns:
        The command's exit status, 2 on usage or configuration errors.
    """
    parser = argparse.ArgumentParser(prog="backup", description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=Path("/etc/pinka/backup/backup.json"))
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("pgdump", "run", "prune", "snapshots"):
        commands.add_parser(name)
    restore = commands.add_parser("restore")
    restore.add_argument("--snapshot", required=True)
    restore.add_argument("--include", action="append", default=[], required=True)
    record = commands.add_parser("platform-record")
    record.add_argument("--snapshot", required=True)
    pgrestore = commands.add_parser("pgrestore")
    pgrestore.add_argument("--dump", type=Path, required=True)
    passwd = commands.add_parser("passwd")
    passwd.add_argument("--new-password-file", type=Path, required=True)
    args = parser.parse_args(list(argv))
    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"backup: {args.config}: {exc}", file=sys.stderr)
        return 2
    commands_by_name: dict[str, Callable[[], int]] = {
        "pgdump": lambda: cmd_pgdump(config, runner),
        "run": lambda: cmd_run(config, runner),
        "prune": lambda: cmd_prune(config, runner),
        "snapshots": lambda: cmd_snapshots(config, runner),
        "restore": lambda: cmd_restore(config, args.snapshot, args.include, runner),
        "platform-record": lambda: cmd_platform_record(config, args.snapshot, runner),
        "pgrestore": lambda: cmd_pgrestore(config, args.dump, runner),
        "passwd": lambda: cmd_passwd(config, args.new_password_file, runner),
    }
    return commands_by_name[args.command]()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
