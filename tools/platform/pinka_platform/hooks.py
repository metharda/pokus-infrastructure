"""The platform's hooks.

Infra drives a platform only through four hooks, commands inside its release:
``migrate`` (at deployment, before the new release starts), ``reconcile`` (at
every start, before the services: the oneshot unit of
:func:`pinka_platform.units.reconcile_unit_text`), ``drain`` (before stopping,
with ``--timeout <seconds>``) and ``health`` (after start until healthy,
``pinka status``, doctor).

Each run is a transient unit ``pinka-hook@<platform>-<hook>-<id>.service``
under a fixed account with that account's hardening: ``migrate`` runs as
``<platform>-migrate`` (the database socket group only, no network, the
migrator's password file), the others as the controller with the
controller's own unit settings. The unit runs the hook through
``pinka-platform hook-exec``, which captures the one JSON line the hook
prints on stdout and judges it: ``"ok": true`` (``"drained": true`` for
``drain``) with exit code 0 is success. The hook's stderr goes to the
journal. The judged result is the unit's stdout, a file in the root-only
:data:`pinka_platform.layout.HOOK_RESULTS_DIR` that systemd opens as root
before the unit drops to its account (``StandardOutput=file:``; verified in
the systemd v252 and v255 sources, like every property passed here).
``systemd-run --pipe`` is not used: it passes the caller's descriptors after
the ``--property`` settings, so the hook's stderr would not reach the
journal. ``RuntimeMaxSec`` is the hook's timeout; systemd then sends SIGTERM
and, after ``TimeoutStopSec``, SIGKILL.
"""

from __future__ import annotations

import json
import secrets
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import IO, Any, TextIO, cast

from pinka_platform import accounts, deploy, layout, names, units
from pinka_platform.contract import Contract
from pinka_platform.loader import MANIFEST_NAME, load_file
from pinka_platform.problems import ManifestError

HOOKS = ("migrate", "reconcile", "drain", "health")
# A hook's stdout is one JSON line; more than this is not one.
MAX_OUTPUT_BYTES = 64 * 1024
# After RuntimeMaxSec, systemd sends SIGTERM, then SIGKILL after this.
STOP_GRACE_SECONDS = 10
# How long the caller waits beyond the hook's timeout and stop grace for
# systemd-run itself before it stops the unit.
RUNNER_SLACK_SECONDS = 60
# The migrate hook's unit limits (the account has no profile of its own).
MIGRATE_MEMORY_MAX = "1G"
MIGRATE_TASKS_MAX = "256"
HEALTH_INTERVAL_SECONDS = 5.0
_CHUNK = 64 * 1024
# Every property a hook unit passes to systemd-run, each found in the
# transient-unit parser (src/shared/bus-unit-util.c) of systemd v252 and v255;
# the tests fail on a property outside this set.
TRANSIENT_PROPERTIES = frozenset(
    {
        "AmbientCapabilities",
        "BindPaths",
        "BindReadOnlyPaths",
        "CapabilityBoundingSet",
        "Group",
        "IPAddressAllow",
        "IPAddressDeny",
        "InaccessiblePaths",
        "LockPersonality",
        "MemoryDenyWriteExecute",
        "MemoryMax",
        "NoNewPrivileges",
        "PrivateDevices",
        "PrivateNetwork",
        "PrivateTmp",
        "ProcSubset",
        "ProtectClock",
        "ProtectControlGroups",
        "ProtectHome",
        "ProtectHostname",
        "ProtectKernelLogs",
        "ProtectKernelModules",
        "ProtectKernelTunables",
        "ProtectProc",
        "ProtectSystem",
        "ReadOnlyPaths",
        "ReadWritePaths",
        "RestrictAddressFamilies",
        "RestrictNamespaces",
        "RestrictRealtime",
        "RestrictSUIDSGID",
        "RuntimeMaxSec",
        "Slice",
        "SocketBindAllow",
        "SocketBindDeny",
        "StandardError",
        "StandardInput",
        "StandardOutput",
        "SupplementaryGroups",
        "SystemCallArchitectures",
        "SystemCallFilter",
        "TasksMax",
        "TemporaryFileSystem",
        "TimeoutStopSec",
        "UMask",
        "User",
        "WorkingDirectory",
    }
)

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


@dataclass(frozen=True)
class HookResult:
    """The outcome of one hook run.

    Attributes:
        hook: Hook name.
        ok: Whether it succeeded.
        exit_code: The hook's exit code (None when it did not finish).
        output: The JSON object it printed, if any.
        error: Why it failed, if it did.
        skipped: The manifest declares no such hook.
    """

    hook: str
    ok: bool
    exit_code: int | None = None
    output: dict[str, Any] | None = None
    error: str | None = None
    skipped: bool = False

    def as_dict(self) -> dict[str, Any]:
        """The result as JSON-ready data."""
        return {
            "hook": self.hook,
            "ok": self.ok,
            "exit_code": self.exit_code,
            "output": self.output,
            "error": self.error,
            "skipped": self.skipped,
        }

    @classmethod
    def from_dict(cls, hook: str, data: Mapping[str, Any]) -> HookResult:
        """Read a result written by :meth:`as_dict`.

        Args:
            hook: The hook that ran (wins over the data's own name).
            data: The JSON object.

        Returns:
            The result.
        """
        output = data.get("output")
        code = data.get("exit_code")
        return cls(
            hook=hook,
            ok=data.get("ok") is True,
            exit_code=code if isinstance(code, int) and not isinstance(code, bool) else None,
            output=output if isinstance(output, dict) else None,
            error=str(data["error"]) if data.get("error") is not None else None,
            skipped=data.get("skipped") is True,
        )


def judge(hook: str, returncode: int, stdout: str) -> HookResult:
    """Judge a hook by its exit code and its one JSON line.

    Args:
        hook: Hook name.
        returncode: Its exit code (negative: killed by that signal).
        stdout: What it printed on stdout.

    Returns:
        The result.
    """
    lines = [line for line in stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        return HookResult(
            hook,
            ok=False,
            exit_code=returncode,
            error=f"printed {len(lines)} lines on stdout; one JSON line is expected",
        )
    try:
        output = json.loads(lines[0])
    except ValueError:
        return HookResult(hook, ok=False, exit_code=returncode, error="stdout is not JSON")
    if not isinstance(output, dict):
        return HookResult(hook, ok=False, exit_code=returncode, error="stdout is not a JSON object")
    if returncode != 0:
        reason = f"killed by signal {-returncode}" if returncode < 0 else f"exit code {returncode}"
        return HookResult(hook, ok=False, exit_code=returncode, output=output, error=reason)
    key = "drained" if hook == "drain" else "ok"
    if output.get(key) is not True:
        return HookResult(
            hook, ok=False, exit_code=returncode, output=output, error=f'"{key}" is not true'
        )
    return HookResult(hook, ok=True, exit_code=returncode, output=output)


def _supervise(hook: str, argv: Sequence[str], stopped: list[int]) -> HookResult:
    if hook not in HOOKS:
        return HookResult(hook, ok=False, error="no such hook")
    try:
        child = subprocess.Popen(  # noqa: S603 - the release's own hook command, no shell
            list(argv), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE
        )
    except OSError as exc:
        return HookResult(hook, ok=False, error=f"cannot run the hook: {exc}")
    kept = bytearray()
    with child:
        stream = cast(IO[bytes], child.stdout)
        # Read to the end so the hook never blocks on a full pipe; keep the bound.
        while chunk := stream.read1(_CHUNK):  # type: ignore[attr-defined]
            if len(kept) <= MAX_OUTPUT_BYTES:
                kept += chunk[: MAX_OUTPUT_BYTES + 1 - len(kept)]
        code = child.wait()
    if stopped:
        return HookResult(
            hook, ok=False, exit_code=code, error="stopped by systemd at the hook's timeout"
        )
    if len(kept) > MAX_OUTPUT_BYTES:
        return HookResult(
            hook,
            ok=False,
            exit_code=code,
            error=f"printed more than {MAX_OUTPUT_BYTES} bytes on stdout",
        )
    return judge(hook, code, kept.decode("utf-8", errors="replace"))


def exec_hook(hook: str, argv: Sequence[str], *, out: TextIO | None = None) -> int:
    """Run a hook inside its unit and print the judged result (``hook-exec``).

    The hook's stderr stays connected to the unit's (the journal); its stdout
    is captured, bounded and judged. When systemd stops the unit at the
    hook's timeout, the hook gets SIGTERM too and the result says so.

    Args:
        hook: Hook name.
        argv: The release's command with its arguments.
        out: Where the result goes (default: standard output, the unit's).

    Returns:
        0 when the hook succeeded, 1 otherwise.
    """
    stopped: list[int] = []

    def on_term(signum: int, _frame: FrameType | None) -> None:
        stopped.append(signum)

    previous = signal.signal(signal.SIGTERM, on_term)
    try:
        result = _supervise(hook, argv, stopped)
    finally:
        signal.signal(signal.SIGTERM, previous)
    stream = out or sys.stdout
    stream.write(json.dumps(result.as_dict(), sort_keys=True) + "\n")
    stream.flush()
    return 0 if result.ok else 1


def migrate_entries(manifest: Mapping[str, Any]) -> list[tuple[str, str]]:
    """The [Service] entries of the migrate hook's account.

    ``<platform>-migrate`` has no profile: it gets the baseline, the
    database socket group, no network and nothing of the lab it does not
    need (the migrator's password file stays readable).

    Args:
        manifest: A valid manifest.

    Returns:
        Entries.
    """
    p = manifest["name"]
    account = names.account(p, "migrate")
    groups = [accounts.db_group(p)] if "postgres" in manifest.get("requires", []) else []
    entries: list[tuple[str, str]] = [
        ("User", account),
        ("Group", account),
        ("SupplementaryGroups", " ".join(groups)),
    ]
    for key, value in units.BASELINE:
        entries.append((key, value))
        if key == "RestrictRealtime":
            entries.append(("MemoryDenyWriteExecute", "yes"))
    hidden = [
        *layout.INFRA_INTERNALS,
        layout.GPU_SOCKET,
        layout.RUN_NETWORK_SOCKET,
        layout.RESTRICTED_DIR,
        layout.POSTGRES_READER_SOCKET_DIR,
        layout.INCUS_CERT_DIR,
        layout.SIGNING_DIR,
    ]
    entries += [
        ("MemoryMax", MIGRATE_MEMORY_MAX),
        ("TasksMax", MIGRATE_TASKS_MAX),
        ("RestrictAddressFamilies", "AF_UNIX"),
        ("PrivateNetwork", "yes"),
        ("InaccessiblePaths", " ".join(f"-{path}" for path in hidden)),
    ]
    return entries


def account_entries(
    manifest: Mapping[str, Any],
    contract: Contract,
    hook: str,
    rendered_accounts: Mapping[str, Mapping[str, Any]],
) -> list[tuple[str, str]]:
    """The [Service] entries of the account a hook runs as.

    Args:
        manifest: A valid manifest.
        contract: The contract.
        hook: Hook name.
        rendered_accounts: The rendered accounts by name.

    Returns:
        Entries.
    """
    if hook == "migrate":
        return migrate_entries(manifest)
    controller = next(s for s in manifest["services"] if s["profile"] == "controller")
    _, entries = units.service_sections(manifest, controller, contract, rendered_accounts, {})
    return entries


def unit_name(platform: str, hook: str, suffix: str) -> str:
    """The transient unit of one hook run.

    Args:
        platform: Platform name.
        hook: Hook name.
        suffix: A unique suffix.

    Returns:
        ``pinka-hook@<platform>-<hook>-<suffix>.service``.
    """
    return f"pinka-hook@{platform}-{hook}-{suffix}.service"


def hook_command(
    release: str, run: Sequence[str], hook: str, extra: Sequence[str] = ()
) -> list[str]:
    """The command of a hook unit: the wrapper, then the release's command.

    Args:
        release: The release directory.
        run: The manifest's ``run`` of the hook.
        hook: Hook name.
        extra: Arguments after the manifest's.

    Returns:
        The command.
    """
    return [
        layout.PLATFORM_TOOL,
        "hook-exec",
        "--hook",
        hook,
        "--",
        f"{release}/{run[0]}",
        *run[1:],
        *extra,
    ]


def systemd_run_argv(
    manifest: Mapping[str, Any],
    contract: Contract,
    hook: str,
    *,
    release: str,
    rendered_accounts: Mapping[str, Mapping[str, Any]],
    timeout: int,
    result_path: str,
    suffix: str,
    extra: Sequence[str] = (),
) -> list[str]:
    """The ``systemd-run`` command of one hook run.

    Args:
        manifest: A valid manifest (of the release the hook belongs to).
        contract: The contract.
        hook: Hook name (declared by the manifest).
        release: The release directory the hook runs in.
        rendered_accounts: The rendered accounts by name.
        timeout: Seconds before systemd stops the hook.
        result_path: Where the unit's stdout (the judged result) goes.
        suffix: Unit name suffix.
        extra: Arguments after the manifest's.

    Returns:
        The command.
    """
    command = hook_command(release, manifest["hooks"][hook]["run"], hook, extra)
    entries = units.hook_entries(
        account_entries(manifest, contract, hook, rendered_accounts), release, command
    )
    argv = [
        "systemd-run",
        "--quiet",
        "--wait",
        "--collect",
        "--service-type=exec",
        f"--unit={unit_name(manifest['name'], hook, suffix)}",
        f"--description=Platform {manifest['name']}: the {hook} hook",
    ]
    for key, value in entries:
        if key != "ExecStart":
            argv += ["--property", f"{key}={value}"]
    for key, value in (
        ("RuntimeMaxSec", str(timeout)),
        ("TimeoutStopSec", str(STOP_GRACE_SECONDS)),
        ("StandardInput", "null"),
        ("StandardOutput", f"file:{result_path}"),
        ("StandardError", "journal"),
    ):
        argv += ["--property", f"{key}={value}"]
    return [*argv, "--", *command]


def _read_result(hook: str, path: Path, done: subprocess.CompletedProcess[str]) -> HookResult:
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_OUTPUT_BYTES + 1)
    except OSError:
        raw = b""
    lines = [line for line in raw.decode("utf-8", errors="replace").splitlines() if line.strip()]
    try:
        data = json.loads(lines[-1]) if lines and len(raw) <= MAX_OUTPUT_BYTES else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        detail = (done.stderr or "").strip()[-500:]
        return HookResult(
            hook,
            ok=False,
            error=f"the hook unit ended without a result (systemd-run exit code {done.returncode}"
            + (f": {detail}" if detail else "")
            + "); it may have been killed after its timeout",
        )
    return HookResult.from_dict(hook, data)


def run_hook(
    manifest: Mapping[str, Any],
    contract: Contract,
    hook: str,
    *,
    release: str,
    rendered_accounts: Mapping[str, Mapping[str, Any]],
    runner: Runner = subprocess.run,
    timeout: int | None = None,
    results_dir: Path = Path(layout.HOOK_RESULTS_DIR),
) -> HookResult:
    """Run one hook in its transient unit and read its result.

    Args:
        manifest: A valid manifest (of the release the hook belongs to).
        contract: The contract.
        hook: Hook name.
        release: The release directory.
        rendered_accounts: The rendered accounts by name.
        runner: Command runner.
        timeout: Seconds (the manifest's ``timeout_seconds`` when None); the
            drain hook also gets them as ``--timeout <seconds>``.
        results_dir: The root-only directory of the results.

    Returns:
        The result; a hook the manifest does not declare is skipped (ok).
    """
    if hook not in HOOKS:
        return HookResult(hook, ok=False, error="no such hook")
    spec = manifest.get("hooks", {}).get(hook)
    if spec is None:
        return HookResult(hook, ok=True, skipped=True)
    seconds = int(timeout if timeout is not None else spec["timeout_seconds"])
    extra = ["--timeout", str(seconds)] if hook == "drain" else []
    results_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    results_dir.chmod(0o700)
    suffix = secrets.token_hex(6)
    result_path = results_dir / f"{manifest['name']}-{hook}-{suffix}.json"
    argv = systemd_run_argv(
        manifest,
        contract,
        hook,
        release=release,
        rendered_accounts=rendered_accounts,
        timeout=seconds,
        result_path=str(result_path),
        suffix=suffix,
        extra=extra,
    )
    try:
        done = runner(
            argv,
            capture_output=True,
            text=True,
            check=False,
            timeout=seconds + STOP_GRACE_SECONDS + RUNNER_SLACK_SECONDS,
        )
    except subprocess.TimeoutExpired:
        runner(
            ["systemctl", "stop", unit_name(manifest["name"], hook, suffix)],
            capture_output=True,
            text=True,
            check=False,
        )
        return HookResult(hook, ok=False, error=f"did not end within {seconds} seconds")
    else:
        return _read_result(hook, result_path, done)
    finally:
        result_path.unlink(missing_ok=True)


def wait_healthy(
    run: Callable[[], HookResult],
    deadline_seconds: float,
    *,
    interval: float = HEALTH_INTERVAL_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> HookResult:
    """Run the health hook until it answers ok or the deadline passes.

    Args:
        run: One health hook run.
        deadline_seconds: How long to keep trying.
        interval: Seconds between runs.
        clock: Monotonic clock.
        sleep: Sleep.

    Returns:
        The first healthy result, or the last one.
    """
    end = clock() + deadline_seconds
    while True:
        result = run()
        if result.ok or clock() + interval > end:
            return result
        sleep(interval)


@dataclass(frozen=True)
class Target:
    """A release whose hooks run: the manifest, its directory, its accounts.

    Attributes:
        platform: Platform name.
        manifest: The release's manifest.
        release: The release directory (resolved, not the ``current`` link).
        accounts: The rendered accounts by name.
    """

    platform: str
    manifest: dict[str, Any]
    release: str
    accounts: dict[str, dict[str, Any]]


def _accounts(document: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {a["name"]: a for a in document.get("accounts", [])}


def active_target(paths: deploy.Paths, contract: Contract) -> Target | None:
    """The active platform's release, from ``active.json`` and ``rendered.json``.

    Args:
        paths: Host paths.
        contract: The contract.

    Returns:
        The target, or None when no platform is active.

    Raises:
        DeployError: The records do not describe a usable release.
    """
    active = deploy.read_active(paths)
    if active is None:
        return None
    name = active.get("name")
    rendered = deploy.read_rendered(paths)
    if rendered is None or rendered.get("platform") != name:
        raise deploy.DeployError(f"no rendering of the active platform {name!r}")
    release = str((active.get("release") or {}).get("target", ""))
    try:
        manifest = load_file(Path(release) / MANIFEST_NAME, int(contract.caps["manifest_bytes"]))
    except ManifestError as exc:
        raise deploy.DeployError(f"cannot read the active release's manifest: {exc}") from exc
    return Target(str(name), manifest, release, _accounts(rendered))


def plan_target(plan: Mapping[str, Any], contract: Contract) -> Target:
    """The release a deployment publishes, with the run's rendering.

    Args:
        plan: The deployment plan (after publish and render).
        contract: The contract.

    Returns:
        The target.

    Raises:
        DeployError: The run has no rendering.
    """
    document = deploy.read_json(Path(plan["run_dir"]) / deploy.RENDER)
    if document is None:
        raise deploy.DeployError("the run has no rendering; run the render step first")
    return Target(
        plan["platform"]["name"],
        deploy.published_manifest(plan, contract),
        plan["release"]["target"],
        _accounts(document),
    )


def run_for(
    target: Target,
    contract: Contract,
    hook: str,
    *,
    runner: Runner = subprocess.run,
    timeout: int | None = None,
    results_dir: Path = Path(layout.HOOK_RESULTS_DIR),
) -> HookResult:
    """Run a hook of a target release.

    Args:
        target: The release.
        contract: The contract.
        hook: Hook name.
        runner: Command runner.
        timeout: Seconds (the manifest's when None).
        results_dir: The root-only directory of the results.

    Returns:
        The result.
    """
    return run_hook(
        target.manifest,
        contract,
        hook,
        release=target.release,
        rendered_accounts=target.accounts,
        runner=runner,
        timeout=timeout,
        results_dir=results_dir,
    )


def migrate_step(
    plan: Mapping[str, Any],
    contract: Contract,
    *,
    runner: Runner = subprocess.run,
    results_dir: Path = Path(layout.HOOK_RESULTS_DIR),
) -> dict[str, Any]:
    """The new release's ``migrate`` hook, before it starts.

    Args:
        plan: The deployment plan (after publish and render).
        contract: The contract.
        runner: Command runner.
        results_dir: The root-only directory of the results.

    Returns:
        ``{"changed": bool, "hook": result}``.

    Raises:
        DeployError: The hook failed: the deployment stops and the platform
            stays stopped.
    """
    result = run_for(
        plan_target(plan, contract), contract, "migrate", runner=runner, results_dir=results_dir
    )
    if not result.ok:
        raise deploy.DeployError(f"the migrate hook failed: {result.error}")
    return {"changed": not result.skipped, "hook": result.as_dict()}
