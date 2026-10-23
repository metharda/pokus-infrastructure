"""The pinka commands other than doctor and fingerprint (BUILD_PROMPT §9, §19.5).

Commands that change the host (start, stop, backup now, restore, images
build, update-host) need root. The ``user`` commands never change the host:
they edit the lab's configuration (``users/<name>.pub`` and the host_vars
file) and remind the operator to commit it and run bootstrap.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from pinka_lab import docs_store, window
from pinka_lab.config import LabConfig
from pinka_lab.system import EXIT_NOT_FOUND, System

ACTIVE_PLATFORM = "/etc/pinka/platform/active.json"
CONTROL_UNIT = "pinka-inference-control.service"
NAME_RE = re.compile(r"^[a-z][a-z0-9]{1,31}$")
KEY_RE = re.compile(
    r"^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(256|384|521)|sk-ssh-ed25519@openssh\.com) \S+"
)
ENTRY_RE = re.compile(r"^\s+- \{name: (?P<name>[^,}]+)")
UPDATE_COMMANDS = {
    "debian": [["apt-get", "update"], ["apt-get", "--yes", "upgrade"]],
    "redhat": [["dnf", "--assumeyes", "upgrade"]],
    "suse": [["zypper", "--non-interactive", "update"]],
    "archlinux": [["pacman", "--sync", "--refresh", "--sysupgrade", "--noconfirm"]],
}


# Documentation (owner answer H9): loopback only, the port of contracts.md.
DOCS_ADDRESS = "127.0.0.1"
DOCS_DIR = "/usr/local/share/pinka/docs"
DOCS_PORT = 18706
DOCS_UNIT = "pinka-docs.service"


# The deployment tool (platform role): purges a replaced platform (BUILD_PROMPT
# §7.14) and runs the active platform's hooks in their units (§12.9).
PLATFORM_TOOL = "/usr/local/libexec/pinka/pinka-platform"
PLATFORM_NAME_RE = re.compile(r"^[a-z][a-z0-9]{1,11}$")
# Seconds `pinka start` waits for the health hook, unless lab.json says otherwise.
HEALTH_WAIT = 300
# Seconds a single health hook may take in `pinka status` (systemd-run included).
STATUS_HEALTH_TIMEOUT = 120
# Beyond a hook's own time: systemd-run, the stop grace and the tool itself.
HOOK_SLACK = 120


class CommandError(RuntimeError):
    """A command cannot do what was asked; the message says why."""


def require_root(system: System, what: str) -> None:
    """Refuse to continue without root.

    Args:
        system: Host access.
        what: The command, for the message.

    Raises:
        CommandError: If the caller is not root.
    """
    if system.euid() != 0:
        raise CommandError(f"{what} needs root (sudo pinka ...)")


def out(text: str) -> None:
    """Print a line to standard output.

    Args:
        text: The line.
    """
    sys.stdout.write(text + "\n")


def bootstrap_argv(config: LabConfig, *extra: str) -> list[str]:
    """The bootstrap command for this lab.

    Args:
        config: The lab description.
        *extra: Further options.

    Returns:
        The command.
    """
    return [f"{config.infra_dir}/bootstrap/bootstrap.sh", "--machine", config.host_vars, *extra]


# --- preflight, start, stop, status --------------------------------------------------------


# The requirements plan bootstrap stored with the host profile (BUILD_PROMPT §2.3).
PLAN_FILE = "/etc/pinka/plan.json"


def preflight_argv(system: System, config: LabConfig, plan: str | None = None) -> list[str]:
    """The read-only preflight run of this machine, as doctor and ``pinka preflight`` run it.

    Args:
        system: Host access.
        config: The lab description.
        plan: A plan file (``--plan``); None takes the stored plan when there is one.

    Returns:
        The command line: the machine's port overrides and VM collector address
        (``preflight_args``) and the plan, so the requirements are those of the
        configured choices.
    """
    command = str(config.get("paths", "preflight"))
    argv = [command, "--no-write", "--srv-path", config.pinka_root]
    argv += [str(arg) for arg in config.get("preflight_args", default=[])]
    stored = str(config.get("paths", "plan", default=PLAN_FILE))
    if plan is None and system.info(stored) is not None:
        plan = stored
    return [*argv, "--plan", plan] if plan else argv


def preflight(system: System, config: LabConfig, as_json: bool, plan: str | None = None) -> int:
    """Run preflight read-only.

    Args:
        system: Host access.
        config: The lab description.
        as_json: Print the JSON report instead of the summary.
        plan: A plan file whose requirements to compute (default: the stored one).

    Returns:
        Preflight's exit code.
    """
    argv = preflight_argv(system, config, plan)
    if as_json:
        done = system.run([*argv, "--quiet"], timeout=300)
        sys.stdout.write(done.stdout)
        sys.stderr.write(done.stderr)
        return done.returncode
    return system.interactive([*argv, "--output", os.devnull])


def _platform_tool(config: LabConfig) -> str:
    return str(config.get("paths", "platform_tool", default=PLATFORM_TOOL))


def hook(system: System, config: LabConfig, name: str, *extra: str, timeout: float) -> Any:  # noqa: ANN401 - JSON
    """Run a hook of the active platform through the deployment tool (§12.9).

    Args:
        system: Host access.
        config: The lab description.
        name: ``drain`` or ``health``.
        *extra: Further options of ``pinka-platform hook``.
        timeout: Seconds before the tool itself is given up.

    Returns:
        The tool's JSON result (``platform``, ``ok``, ``skipped``, ``error``,
        ``output``), or a failed result when it printed none.
    """
    done = system.run([_platform_tool(config), "hook", name, *extra], timeout=timeout)
    result = done.json() if done.stdout else None
    if isinstance(result, dict):
        return result
    detail = done.stderr.strip() or f"exit code {done.returncode}"
    return {"platform": None, "ok": False, "skipped": False, "error": detail}


def _platform_label(result: Any) -> str:  # noqa: ANN401 - JSON
    name = result.get("platform") if isinstance(result, dict) else None
    return f"platform {name}" if name else "the platform"


def start(system: System, config: LabConfig) -> int:
    """Start pinka-lab.target, then wait for the platform's health hook.

    The target waits for the health gate; the platform's reconcile unit runs
    before its services. An unhealthy platform is reported (exit 1).

    Args:
        system: Host access.
        config: The lab description.

    Returns:
        0 on success, 1 when the lab did not start or the platform is unhealthy.
    """
    require_root(system, "start")
    may_start(system, config)
    done = system.run(["systemctl", "start", "pinka-lab.target"], timeout=1200)
    if not done.ok:
        sys.stderr.write(done.stderr)
        return 1
    out("pinka-lab.target started")
    if system.info(ACTIVE_PLATFORM) is None:
        return 0
    wait = float(config.get("lab", "health_wait", default=HEALTH_WAIT))
    result = hook(
        system, config, "health", "--until-healthy", str(int(wait)), timeout=wait + HOOK_SLACK
    )
    label = _platform_label(result)
    if result.get("skipped"):
        out(f"{label} has no health hook")
        return 0
    if result.get("ok"):
        out(f"{label} is healthy")
        return 0
    out(f"{label} is unhealthy after {int(wait)} seconds: {result.get('error')}")
    return 1


def _window(system: System, config: LabConfig) -> window.Window | None:
    path = str(config.get("window", "record", default=window.RECORD))
    text = system.read_text(path)
    if text is None:
        return None
    try:
        return window.from_text(text)
    except window.WindowError as exc:
        raise CommandError(str(exc)) from exc


def _shared_staging(config: LabConfig) -> bool:
    return config.get("lab_mode", default="production") == "staging" and bool(
        config.get("window", "shared_hardware", default=False)
    )


def gpu_processes(system: System, config: LabConfig) -> list[str]:
    """Processes that use the GPU (an idle GPU has none).

    Args:
        system: Host access.
        config: The lab description.

    Returns:
        ``<name> (PID <pid>)`` per process; empty without a GPU.

    Raises:
        CommandError: If nvidia-smi exists but fails.
    """
    smi = str(config.get("gpu", "nvidia_smi", default="nvidia-smi"))
    done = system.run([smi, "--query-compute-apps=pid,process_name", "--format=csv,noheader"])
    if done.returncode == EXIT_NOT_FOUND:
        return []
    if not done.ok:
        raise CommandError(f"{smi} failed: {done.stderr.strip() or done.returncode}")
    found = []
    for line in done.stdout.splitlines():
        pid, _, name = (part.strip() for part in line.partition(","))
        if pid:
            found.append(f"{name or 'unknown'} (PID {pid})")
    return found


def may_start(system: System, config: LabConfig) -> None:
    """Refuse a start a maintenance window forbids (§7.20).

    Production does not start while its record exists. Staging on shared
    hardware starts only inside its own open window, with the GPU idle and
    every configured port free.

    Args:
        system: Host access.
        config: The lab description.

    Raises:
        CommandError: When the lab must not start.
    """
    recorded = _window(system, config)
    if config.get("lab_mode", default="production") != "staging":
        if recorded is not None:
            raise CommandError(
                f"a maintenance window is recorded ({recorded.describe()}): production stays "
                "stopped until sudo pinka window close"
            )
        return
    if not _shared_staging(config):
        return
    if recorded is None or not recorded.is_open(system.now()):
        when = f" (recorded: {recorded.describe()})" if recorded else ""
        raise CommandError(
            "staging on shared hardware starts only inside an open maintenance window"
            f"{when}; open one with sudo pinka window open --start ... --end ..."
        )
    busy = gpu_processes(system, config)
    if busy:
        raise CommandError(f"the GPU is in use by {', '.join(busy)}; staging needs it idle")
    ports = {str(k): int(v) for k, v in (config.get("ports", default={}) or {}).items()}
    taken = system.port_conflicts(ports)
    if taken:
        raise CommandError("lab ports are not free: " + "; ".join(taken))


def window_open(system: System, config: LabConfig, start: str, end: str) -> int:
    """Open a maintenance window (§7.20).

    On production the lab is drained and stopped first, exactly like
    ``pinka stop``; it stays stopped until ``pinka window close``.

    Args:
        system: Host access.
        config: The lab description.
        start: ``--start``, UTC.
        end: ``--end``, UTC.

    Returns:
        0, or the exit code of a stop that failed.

    Raises:
        CommandError: Not root, a window already recorded, bad times, or a
            staging host on its own hardware.
    """
    require_root(system, "window open")
    recorded = _window(system, config)
    if recorded is not None:
        raise CommandError(
            f"a maintenance window is already recorded ({recorded.describe()}); "
            "close it first with sudo pinka window close"
        )
    try:
        opened = window.new(start, end, system.now())
    except window.WindowError as exc:
        raise CommandError(str(exc)) from exc
    production = config.get("lab_mode", default="production") != "staging"
    if not production and not _shared_staging(config):
        raise CommandError(
            "this staging host has its own hardware (staging_shared_hardware: false); "
            "it needs no maintenance window"
        )
    if production:
        out("production: draining and stopping the lab first, as pinka stop does")
        code = stop(system, config)
        if code:
            return code
    path = str(config.get("window", "record", default=window.RECORD))
    system.write_text(path, window.to_text(opened), 0o644)
    out(f"maintenance window recorded {opened.describe()}")
    if production:
        out("production stays stopped, also after the end, until sudo pinka window close")
    return 0


def window_close(system: System, config: LabConfig) -> int:
    """Close a maintenance window (§7.20).

    Args:
        system: Host access.
        config: The lab description.

    Returns:
        0, or the exit code of a stop that failed.

    Raises:
        CommandError: Not root, or a broken record.
    """
    require_root(system, "window close")
    recorded = _window(system, config)
    if recorded is None:
        out("no maintenance window is recorded")
        return 0
    if _shared_staging(config) and _active(system, "pinka-lab.target") != "inactive":
        out("staging runs only inside a window: stopping the staging lab first")
        code = stop(system, config)
        if code:
            return code
    system.remove(str(config.get("window", "record", default=window.RECORD)))
    out(f"maintenance window closed (was {recorded.describe()})")
    if config.get("lab_mode", default="production") != "staging":
        out("start production again with sudo pinka start")
    return 0


def window_expire(system: System, config: LabConfig) -> int:
    """Stop staging on shared hardware once its window has ended (the expiry timer).

    Args:
        system: Host access.
        config: The lab description.

    Returns:
        0, or the exit code of a stop that failed.
    """
    require_root(system, "window expire")
    if not _shared_staging(config):
        return 0
    recorded = _window(system, config)
    if recorded is not None and recorded.is_open(system.now()):
        return 0
    if _active(system, "pinka-lab.target") in ("inactive", "failed", "unknown"):
        return 0
    reason = f"the window {recorded.describe()} has ended" if recorded else "no window is open"
    out(f"{reason}: stopping the staging lab")
    return stop(system, config)


def drain(system: System, config: LabConfig, timeout: float) -> str:
    """Drain the active platform before its services stop (contract v2 §12.9).

    Args:
        system: Host access.
        config: The lab description.
        timeout: Seconds the drain may take (the hook gets ``--timeout``).

    Returns:
        What happened, for the operator. A failed drain does not stop the
        stop: the services stop anyway and the platform records what was
        interrupted.
    """
    if system.info(ACTIVE_PLATFORM) is None:
        return "no active platform; nothing to drain"
    seconds = max(1, int(timeout))
    result = hook(system, config, "drain", "--timeout", str(seconds), timeout=seconds + HOOK_SLACK)
    label = _platform_label(result)
    if result.get("skipped"):
        return f"{label} has no drain hook"
    if result.get("ok"):
        return f"{label} drained"
    return f"{label} did not drain ({result.get('error')}); its services stop anyway"


def stop(system: System, config: LabConfig, timeout: float | None = None) -> int:
    """Drain the active platform, then stop its target and pinka-lab.target.

    Args:
        system: Host access.
        config: The lab description.
        timeout: Seconds the drain may take (default: ``lab.drain_timeout``).

    Returns:
        0 on success.
    """
    require_root(system, "stop")
    seconds = (
        float(config.get("lab", "drain_timeout", default=1800)) if timeout is None else timeout
    )
    out(drain(system, config, seconds))
    out(arbiter_idle(system, config))
    units = ["pinka-platform.target", "pinka-lab.target"]
    if system.info(ACTIVE_PLATFORM) is None:
        units = units[1:]
    for unit in units:
        done = system.run(["systemctl", "stop", unit], timeout=600)
        if not done.ok:
            sys.stderr.write(done.stderr)
            return 1
        out(f"{unit} stopped")
    return 0


def _active(system: System, unit: str) -> str:
    return system.run(["systemctl", "is-active", unit]).stdout.strip() or "unknown"


def _platform_status(system: System, config: LabConfig) -> None:
    text = system.read_text(ACTIVE_PLATFORM)
    try:
        active = json.loads(text) if text else None
    except ValueError:
        active = None
    if not isinstance(active, dict):
        out("platform: none")
        return
    release = (active.get("release") or {}).get("name", "?")
    out(f"platform: {active.get('name')} {active.get('version')} (release {release})")
    out(f"  {'pinka-platform.target':<40} {_active(system, 'pinka-platform.target')}")
    if system.euid() != 0:
        out("  health: needs root (sudo pinka status)")
        return
    result = hook(system, config, "health", timeout=STATUS_HEALTH_TIMEOUT)
    if result.get("skipped"):
        out("  health: the platform has no health hook")
    elif result.get("ok"):
        out("  health: healthy")
    else:
        out(f"  health: unhealthy ({result.get('error')})")


def status(system: System, config: LabConfig) -> int:
    """Print units, containers, Incus instances, GPU, disk and the last backup.

    Args:
        system: Host access.
        config: The lab description.

    Returns:
        0.
    """
    if system.euid() != 0:
        sys.stderr.write(
            "pinka: containers and Incus instances are only visible to root (sudo pinka status)\n"
        )
    out("units:")
    for unit in ["pinka-lab.target", *config.get("units", default=[])]:
        out(f"  {unit:<40} {_active(system, unit)}")
    _platform_status(system, config)
    out("containers:")
    for name in sorted(config.get("containers", default={})):
        done = system.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}",
                name,
            ]
        )
        out(f"  {name:<40} {done.stdout.strip() if done.ok else 'missing'}")
    if config.runs_lab:
        instances = (
            system.run(["incus", "list", "--all-projects", "--format", "json"], timeout=60).json()
            or []
        )
        counts: dict[str, dict[str, int]] = {}
        for instance in instances:
            project = counts.setdefault(str(instance.get("project", "default")), {})
            state = str(instance.get("status", "Unknown"))
            project[state] = project.get(state, 0) + 1
        out("incus instances:")
        for name, states in sorted(counts.items()):
            out(f"  {name:<40} " + ", ".join(f"{n} {s.lower()}" for s, n in sorted(states.items())))
    _arbiter_status(system, config)
    if config.serves_inference:
        smi = str(config.get("gpu", "nvidia_smi", default="nvidia-smi"))
        done = system.run(
            [
                smi,
                "--query-gpu=name,memory.used,memory.total,utilization.gpu",
                "--format=csv,noheader",
            ]
        )
        out(f"gpu: {done.stdout.strip() if done.ok else 'not available'}")
    try:
        recorded = _window(system, config)
    except CommandError as exc:
        out(f"maintenance window: {exc}")
    else:
        state = "open" if recorded and recorded.is_open(system.now()) else "recorded"
        out(
            f"maintenance window: {state} {recorded.describe()}"
            if recorded
            else "maintenance window: none"
        )
    version = system.docs_current(_docs_base(config))
    unit = str(config.get("docs", "unit", default=DOCS_UNIT))
    out(f"docs: {version} ({unit} {_active(system, unit)})" if version else "docs: none installed")
    df = system.run(
        ["df", "--human-readable", "--output=avail,size", config.pinka_root]
    ).stdout.splitlines()
    out(f"disk {config.pinka_root}: {df[-1].strip() if df else 'unknown'} (available, size)")
    state_text = system.read_text(
        str(config.get("backup", "state", default="/var/lib/pinka/backup/state.json"))
    )
    last = ((json.loads(state_text) if state_text else {}).get("backup") or {}).get("last_success")
    when = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(last))) if last else "never"
    out(
        f"last backup: {when}" + ("" if config.get("backup", "configured") else " (not configured)")
    )
    return 0


# --- the GPU arbiter (§7.18) ------------------------------------------------------------------

# A serve request waits for the deployment, an idle request for the running job.
GPU_REQUEST_TIMEOUT = 1800.0
TRANSITIONS_SHOWN = 3


def _arbiter(config: LabConfig) -> dict[str, Any] | None:
    section = config.get("gpu_arbiter", default=None)
    return dict(section) if isinstance(section, dict) and section.get("socket") else None


def training_line(config: LabConfig) -> str:
    """Whether this host trains (§19.10).

    Args:
        config: The lab description.

    Returns:
        The line for the operator.
    """
    if config.node_role in ("lab", "inference"):
        return (
            f"training: unavailable on this host (node_role {config.node_role}); training runs "
            "only on a full node, split-node training is future work"
        )
    return "training: runs on this host through the GPU arbiter"


def _describe_mode(state: Mapping[str, Any]) -> str:
    mode = str(state.get("mode") or "idle")
    target = state.get("deployment") if mode == "serving" else state.get("run")
    return f"{mode} {target}" if target and mode != "idle" else mode


def _arbiter_status(system: System, config: LabConfig) -> None:
    arbiter = _arbiter(config)
    if arbiter is None:
        out("gpu arbiter: none on this host")
    else:
        text = system.read_text(str(arbiter.get("state", "")))
        state = json.loads(text) if text else {}
        since = f" since {state['since']}" if state.get("since") else ""
        out(f"gpu arbiter: {_describe_mode(state)}{since}")
        log = system.read_text(str(arbiter.get("transitions", ""))) or ""
        for line in log.splitlines()[-TRANSITIONS_SHOWN:]:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            detail = f": {entry['detail']}" if entry.get("detail") else ""
            out(
                f"  {entry.get('time')} {entry.get('from')} -> {entry.get('to')} "
                f"({entry.get('outcome')}, {entry.get('requester')}){detail}"
            )
    out(training_line(config))


def arbiter_idle(system: System, config: LabConfig) -> str:
    """Ask the GPU arbiter for idle (``pinka stop`` and ``update-host``, after the drain).

    The request carries ``resume``: the lab stops, so a resumable job is
    checkpointed and its run suspended; it resumes when the lab starts again
    (``pinka start`` or a reboot), as after a reboot. Any other job ends
    failed.

    Args:
        system: Host access.
        config: The lab description.

    Returns:
        What happened. A refusal does not stop the stop: vLLM and a running
        job stop with the lab (a stopped job unit suspends a resumable run too).
    """
    arbiter = _arbiter(config)
    if arbiter is None or system.info(str(arbiter["socket"])) is None:
        return "no GPU arbiter here; nothing to idle"
    request = {"request": "idle", "resume": True}
    answer = system.unix_request(str(arbiter["socket"]), request, GPU_REQUEST_TIMEOUT)
    if answer is None:
        return "the GPU arbiter does not answer; vLLM and a running job stop with the lab"
    if not answer.get("ok"):
        return (
            f"the GPU arbiter refused idle ({answer.get('error')}); vLLM and a running job "
            "stop with the lab"
        )
    if answer.get("suspended"):
        return (
            f"run {answer['run']} is suspended; it resumes from its checkpoints when the lab "
            "starts (pinka start or a reboot)"
        )
    stopped = f"; run {answer['run']} stopped" if answer.get("run") else ""
    return f"the GPU is idle{stopped}"


def gpu(
    system: System,
    config: LabConfig,
    action: str,
    *,
    deployment: str | None = None,
    job: str | None = None,
    submission: str | None = None,
) -> int:
    """The GPU arbiter's requests for admins: status, serve, train, idle (§7.18).

    Root reaches the arbiter's socket like the controller does; the arbiter
    checks the request.

    Args:
        system: Host access.
        config: The lab description.
        action: ``status``, ``serve``, ``train`` or ``idle``.
        deployment: ``<id>@<version>`` for ``serve``.
        job: The job's name for ``train``.
        submission: A JSON file with the submission for ``train``.

    Returns:
        0 if the arbiter did what was asked.

    Raises:
        CommandError: Without an arbiter on this host, an unreadable
            submission, or no answer.
    """
    require_root(system, "gpu")
    arbiter = _arbiter(config)
    if arbiter is None:
        raise CommandError(f"this host has no GPU arbiter (node_role {config.node_role})")
    request: dict[str, Any] = {"request": action}
    if action == "serve":
        request["deployment"] = deployment
    elif action == "train":
        text = system.read_text(str(submission))
        try:
            content = json.loads(text) if text is not None else None
        except ValueError:
            content = None
        if not isinstance(content, dict):
            raise CommandError(f"{submission} is not a JSON object (the submission)")
        request.update({"job": job, "submission": content})
    answer = system.unix_request(str(arbiter["socket"]), request, GPU_REQUEST_TIMEOUT)
    if answer is None:
        raise CommandError(f"the GPU arbiter does not answer on {arbiter['socket']}")
    if not answer.get("ok"):
        sys.stderr.write(f"pinka: the GPU arbiter refused: {answer.get('error')}\n")
        return 1
    if action == "status":
        out(
            f"mode: {_describe_mode(answer)}"
            + (f" since {answer['since']}" if answer.get("since") else "")
        )
        if answer.get("job"):
            record = answer["job"]
            out(f"job: {record.get('job')} {record.get('status')} since {record.get('started_at')}")
        for entry in answer.get("transitions", [])[-TRANSITIONS_SHOWN:]:
            out(
                f"  {entry.get('time')} {entry.get('from')} -> {entry.get('to')} "
                f"({entry.get('outcome')}, {entry.get('requester')})"
            )
        out(training_line(config))
    elif action == "train":
        out(f"training run {answer.get('run')} started (job-spec hash {answer.get('spec_sha256')})")
    else:
        out(f"the GPU is {_describe_mode(answer)}")
    return 0


# --- backup and restore ---------------------------------------------------------------------


def backup_now(system: System, config: LabConfig) -> int:
    """Run the database dump and a backup now.

    Args:
        system: Host access.
        config: The lab description.

    Returns:
        0 on success.
    """
    require_root(system, "backup now")
    units = (["pinka-pgdump.service"] if config.runs_lab else []) + ["pinka-backup.service"]
    done = system.run(["systemctl", "start", *units], timeout=7200)
    if not done.ok:
        sys.stderr.write(done.stderr or "backup failed; see journalctl -u pinka-backup.service\n")
        return 1
    out("backup finished")
    return 0


def backup_list(system: System, config: LabConfig) -> int:
    """List this host's snapshots.

    Args:
        system: Host access.
        config: The lab description.

    Returns:
        The helper's exit code.
    """
    require_root(system, "backup list")
    helper = str(config.get("backup", "helper", default="/usr/local/libexec/pinka/backup"))
    done = system.run([helper, "snapshots"], timeout=300)
    if not done.ok:
        sys.stderr.write(done.stderr)
        return done.returncode or 1
    for snap in done.json() or []:
        out(
            f"{snap.get('short_id', '?'):<10} {snap.get('time', '?')[:19]}  "
            + " ".join(snap.get("tags") or [])
        )
    return 0


def restore(system: System, config: LabConfig, extra: Sequence[str]) -> int:
    """Run bootstrap with ``--restore`` (restore.yml after provisioning).

    Args:
        system: Host access.
        config: The lab description.
        extra: Further bootstrap options (such as ``--non-interactive``).

    Returns:
        Bootstrap's exit code.
    """
    require_root(system, "restore")
    return system.interactive(bootstrap_argv(config, "--restore", *extra))


BOOTSTRAP_STATE = "/etc/pinka/bootstrap-state.json"


def uninstall(
    system: System,
    config: LabConfig | None,
    flags: Sequence[str],
    extra: Sequence[str],
) -> int:
    """Run ``bootstrap.sh --uninstall`` from the checkout bootstrap last ran from (§5.4).

    The checkout is the one ``/etc/pinka/bootstrap-state.json`` records, else
    the lab description's. Uninstall works without the lab description, so
    this command does too.

    Args:
        system: Host access.
        config: The lab description, when there is one.
        flags: Uninstall's own options (``--dry-run``, ``--purge-data``, ...).
        extra: Further bootstrap options (such as ``--confirm-machine``).

    Returns:
        Bootstrap's exit code.

    Raises:
        CommandError: Without root, or when no checkout is recorded.
    """
    require_root(system, "uninstall")
    infra = ""
    text = system.read_text(BOOTSTRAP_STATE)
    if text:
        try:
            state = json.loads(text)
        except ValueError:
            state = {}
        infra = str(state.get("infra_dir", "")) if isinstance(state, dict) else ""
    if not infra and config is not None:
        infra = config.infra_dir
    if not infra:
        raise CommandError(
            f"{BOOTSTRAP_STATE} names no checkout; run uninstall from one: "
            "sudo <checkout>/bootstrap/bootstrap.sh --uninstall"
        )
    return system.interactive([f"{infra}/bootstrap/bootstrap.sh", "--uninstall", *flags, *extra])


# --- golden images --------------------------------------------------------------------------


def _blueprints(config: LabConfig) -> list[dict[str, Any]]:
    return list(config.get("images", "blueprints", default=[]))


def image_chain(config: LabConfig, name: str) -> list[str]:
    """A blueprint and the blueprints it starts from, base first.

    Args:
        config: The lab description.
        name: Blueprint name.

    Returns:
        Blueprint names in build order.

    Raises:
        CommandError: If the blueprint is unknown.
    """
    known = {b["name"]: b for b in _blueprints(config)}
    chain: list[str] = []
    current: str | None = name
    while current is not None:
        if current not in known:
            raise CommandError(f"unknown blueprint {current!r}; known: {', '.join(sorted(known))}")
        chain.insert(0, current)
        current = known[current].get("base_blueprint")
    return chain


def images_list(system: System, config: LabConfig) -> int:
    """List golden images with their export state.

    Args:
        system: Host access.
        config: The lab description.

    Returns:
        0.
    """
    images = system.run(["incus", "image", "list", "--format", "json"], timeout=60).json() or []
    names = {b["name"] for b in _blueprints(config)}
    export_root = str(config.get("images", "export_root", default=f"{config.pinka_root}/images"))
    for image in images:
        for alias in image.get("aliases") or []:
            blueprint, _, version = str(alias.get("name", "")).partition("/")
            if blueprint not in names:
                continue
            record_text = system.read_text(f"{export_root}/{blueprint}/{version}/build.json")
            record = json.loads(record_text) if record_text else {}
            exported = (
                "exported"
                if record.get("fingerprint") == image.get("fingerprint")
                else "NOT EXPORTED"
            )
            built = (image.get("properties") or {}).get("pinka.built_at", "?")
            out(
                f"{alias['name']:<32} {str(image.get('fingerprint'))[:12]}  "
                f"built {built}  {exported}"
            )
    return 0


def images_build(
    system: System, config: LabConfig, blueprint: str | None, rebuild: bool, extra: Sequence[str]
) -> int:
    """Build missing golden images (or one blueprint, optionally again) through bootstrap.

    Args:
        system: Host access.
        config: The lab description.
        blueprint: Only this blueprint (and its bases), or None for all.
        rebuild: Build the blueprint again although its image exists.
        extra: Further bootstrap options.

    Returns:
        Bootstrap's exit code.

    Raises:
        CommandError: If ``--rebuild`` is given without a blueprint.
    """
    require_root(system, "images build")
    env = {}
    if blueprint is not None:
        env["PINKA_IMAGES_BLUEPRINTS"] = ",".join(image_chain(config, blueprint))
        if rebuild:
            env["PINKA_IMAGES_REBUILD"] = blueprint
    elif rebuild:
        raise CommandError("--rebuild needs a blueprint")
    return system.interactive(bootstrap_argv(config, "--tags", "images", *extra), env=env)


def images_export(system: System, config: LabConfig, extra: Sequence[str]) -> int:
    """Export every image that lacks an export, then check every export.

    Args:
        system: Host access.
        config: The lab description.
        extra: Further bootstrap options.

    Returns:
        0 when every export matches its fingerprint.
    """
    require_root(system, "images export")
    code = system.interactive(bootstrap_argv(config, "--tags", "images", *extra))
    if code not in (0, 1):
        return code
    helper = str(
        config.get("images", "files_helper", default="/usr/local/libexec/pinka/image-files")
    )
    export_root = str(config.get("images", "export_root", default=f"{config.pinka_root}/images"))
    bad = 0
    for blueprint in sorted(b["name"] for b in _blueprints(config)):
        for version in system.listdir(f"{export_root}/{blueprint}"):
            directory = f"{export_root}/{blueprint}/{version}"
            record = json.loads(system.read_text(f"{directory}/build.json") or "{}")
            done = system.run(
                [
                    helper,
                    "verify",
                    "--dir",
                    directory,
                    "--fingerprint",
                    str(record.get("fingerprint", "")),
                ],
                timeout=3600,
            )
            result = (done.json() or {}).get("ok")
            bad += 0 if result else 1
            out(f"{blueprint}/{version}: {'ok' if result else 'DOES NOT MATCH ITS FINGERPRINT'}")
    return 1 if bad else 0


# --- researchers (configuration only) ---------------------------------------------------------


def _host_vars_path(config: LabConfig) -> str:
    return f"{config.infra_dir}/ansible/inventory/host_vars/{config.host_vars}.yml"


def _read_key_file(system: System, path: str) -> str:
    text = system.read_text(path)
    if text is None:
        raise CommandError(f"cannot read {path}")
    keys = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]
    if not keys or not all(KEY_RE.match(k) for k in keys):
        raise CommandError(f"{path} does not contain only OpenSSH public keys")
    return "\n".join(keys) + "\n"


def researcher_entries(lines: list[str]) -> tuple[int, int, list[str]]:
    """Find the ``researchers:`` list of a host_vars file.

    Args:
        lines: The file's lines.

    Returns:
        ``(start, end, names)``: index of the key line (-1 if absent), index
        after the last entry, and the researchers' names.

    Raises:
        CommandError: If the list is not written one flow mapping per line.
    """
    for index, line in enumerate(lines):
        if re.match(r"^researchers:\s*(#.*)?$", line):
            end, names = index + 1, []
            while end < len(lines) and (lines[end].startswith(" ") or not lines[end].strip()):
                match = ENTRY_RE.match(lines[end])
                if lines[end].strip() and not lines[end].strip().startswith("#") and not match:
                    raise CommandError(
                        "the researchers list must have one {name: ...} entry per line"
                    )
                if match:
                    names.append(match.group("name").strip())
                end += 1
            while end > index + 1 and not lines[end - 1].strip():
                end -= 1
            return index, end, names
    return -1, len(lines), []


def user_add(system: System, config: LabConfig, name: str, key_file: str, admin: bool) -> int:
    """Add a researcher to the configuration.

    Args:
        system: Host access.
        config: The lab description.
        name: Account name.
        key_file: File with the researcher's public key(s).
        admin: Whether the researcher gets pinka-admin.

    Returns:
        0.

    Raises:
        CommandError: On an invalid name, key file or host_vars layout.
    """
    if not NAME_RE.match(name):
        raise CommandError(
            "account names are 2-32 lowercase letters and digits, starting with a letter"
        )
    keys = _read_key_file(system, key_file)
    path = _host_vars_path(config)
    lines = (system.read_text(path) or "").splitlines()
    start_line, end, names = researcher_entries(lines)
    if name in names:
        raise CommandError(f"{name} is already a researcher")
    role = "true" if admin else "false"
    entry = f"  - {{name: {name}, admin: {role}, shell: /bin/bash, keys_file: users/{name}.pub}}"
    if start_line < 0:
        lines += ["", "researchers:", entry]
        out("note: the host_vars file had no researchers list; it now replaces the default list")
    else:
        lines.insert(end, entry)
    system.write_text(f"{config.infra_dir}/users/{name}.pub", keys, 0o644)
    system.write_text(path, "\n".join(lines) + "\n", 0o640)
    _remind(config, "users_groups,layout")
    return 0


def user_remove(system: System, config: LabConfig, name: str) -> int:
    """Move a researcher to researchers_removed (locked, never deleted).

    Args:
        system: Host access.
        config: The lab description.
        name: Account name.

    Returns:
        0.

    Raises:
        CommandError: If the researcher is not in the list.
    """
    path = _host_vars_path(config)
    lines = (system.read_text(path) or "").splitlines()
    start_line, end, names = researcher_entries(lines)
    if name not in names:
        raise CommandError(f"{name} is not in the researchers list of {path}")
    lines = [
        line
        for i, line in enumerate(lines)
        if not (
            start_line < i < end and (m := ENTRY_RE.match(line)) and m.group("name").strip() == name
        )
    ]
    for index, line in enumerate(lines):
        match = re.match(r"^researchers_removed:\s*\[(.*)\]\s*$", line)
        if match:
            removed = [n.strip() for n in match.group(1).split(",") if n.strip()]
            lines[index] = f"researchers_removed: [{', '.join([*removed, name])}]"
            break
    else:
        lines += ["", f"researchers_removed: [{name}]"]
    system.write_text(path, "\n".join(lines) + "\n", 0o640)
    _remind(config, "users_groups")
    return 0


def user_keys(system: System, config: LabConfig, name: str, key_file: str) -> int:
    """Replace a researcher's public keys.

    Args:
        system: Host access.
        config: The lab description.
        name: Account name.
        key_file: File with the new key(s).

    Returns:
        0.
    """
    keys = _read_key_file(system, key_file)
    target = f"{config.infra_dir}/users/{name}.pub"
    if system.info(target) is None:
        raise CommandError(f"{target} does not exist; add the researcher first")
    system.write_text(target, keys, 0o644)
    _remind(config, "users_groups")
    return 0


def _remind(config: LabConfig, tags: str) -> None:
    out("Changed the lab's configuration only. Commit it in the lab's private configuration")
    out(f"and apply it: sudo {' '.join(bootstrap_argv(config, '--tags', tags))}")


# --- pairing (BUILD_PROMPT §19.10) ------------------------------------------------------------


def _identity(system: System, config: LabConfig) -> tuple[str, Any]:
    from pinka_lab import pairing  # noqa: PLC0415 - only for pairing

    directory = str(config.get("inference", "dir", default="/etc/pinka/inference"))
    cert = system.read_text(f"{directory}/self.crt")
    if not cert:
        raise CommandError(f"{directory}/self.crt is missing; run bootstrap first")
    return directory, pairing.Identity(config.machine, str(config.get("lab_mode")), cert)


# The split-inference guard (firewall role, §19.10, interpretation 11.13).
SPLIT_GUARD_TABLE = "pinka_split_guard"
SPLIT_GUARD_UNIT = "pinka-split-guard.service"
# Seconds the pairing port stays open beyond the pairing's own timeout, should
# pinka die before it closes the port: the set element expires by itself.
PAIRING_WINDOW_SLACK = 60


def _pairing_window(system: System, port: int, seconds: int | None) -> None:
    """Open the pairing port to the tailnet (seconds) or close it again (None)."""
    if seconds is None:
        argv = ["nft", "delete", "element", "inet", SPLIT_GUARD_TABLE, "pairing", f"{{ {port} }}"]
        system.run(argv)  # it may have expired already
        return
    element = f"{{ {port} timeout {seconds}s }}"
    done = system.run(["nft", "add", "element", "inet", SPLIT_GUARD_TABLE, "pairing", element])
    if not done.ok:
        raise CommandError(
            f"cannot open the pairing port ({done.stderr.strip() or 'nft failed'}); the split "
            "guard is not loaded: run bootstrap with --tags firewall"
        )


def _reload_split_guard(system: System) -> None:
    """Load the split guard again with the new pairing record."""
    done = system.run(["systemctl", "reload-or-restart", SPLIT_GUARD_UNIT], timeout=120)
    if not done.ok:
        raise CommandError(
            f"paired, but the split guard did not load ({done.stderr.strip() or 'failed'}); run: "
            f"systemctl restart {SPLIT_GUARD_UNIT}"
        )


def _store_peer(system: System, directory: str, peer: Any) -> None:  # noqa: ANN401 - pairing.Peer
    system.write_text(f"{directory}/peer.crt", peer.cert_pem.strip() + "\n", 0o644)
    record = peer.record(system.now().strftime("%Y-%m-%dT%H:%M:%SZ"))
    system.write_text(f"{directory}/peer.json", json.dumps(record, indent=2) + "\n", 0o644)


def pair_show(
    system: System,
    config: LabConfig,
    timeout: float,
    serve: Callable[..., Any] | None = None,
) -> int:
    """On an inference node: show a one-time code and wait for the lab node.

    Args:
        system: Host access.
        config: The lab description.
        timeout: Seconds to wait.
        serve: Pairing server (``pairing.serve_once``; tests replace it).

    Returns:
        0 once paired.

    Raises:
        CommandError: On another node role, missing files or a failed pairing.
    """
    from pinka_lab import pairing  # noqa: PLC0415 - only for pairing

    require_root(system, "pair show")
    if config.node_role != "inference":
        raise CommandError("pair show runs on the inference node; use pair connect on the lab node")
    directory, me = _identity(system, config)
    settings = json.loads(system.read_text(f"{directory}/control.json") or "{}")
    if not settings.get("listen"):
        raise CommandError(f"{directory}/control.json has no tailnet address; run bootstrap first")
    port = int(settings.get("control_port", 18710))
    # The pairing port is open to the tailnet only while this waits (11.13).
    _pairing_window(system, port, int(timeout) + PAIRING_WINDOW_SLACK)
    try:
        system.run(["systemctl", "stop", CONTROL_UNIT])
        code = pairing.new_code()
        out(f"Pairing code: {code}")
        out(f"On the lab node run: sudo pinka pair connect {config.machine} {code}")
        out(f"Waiting up to {timeout:g} s on {settings['listen']}:{port} ...")
        try:
            peer = (serve or pairing.serve_once)(
                pairing.normalize(code),
                me,
                f"{directory}/self.crt",
                f"{directory}/self.key",
                str(settings["listen"]),
                port,
                timeout,
                log=out,
            )
        except pairing.PairingError as exc:
            raise CommandError(str(exc)) from exc
    finally:
        _pairing_window(system, port, None)
    _store_peer(system, directory, peer)
    _reload_split_guard(system)
    system.run(["systemctl", "restart", CONTROL_UNIT])
    out(f"Paired with lab node {peer.node} ({pairing.cert_sha256(peer.cert_pem)[:16]}...).")
    out(f"Set inference_lab_node: {peer.node} in the host_vars file and apply it:")
    tags = "inference_control,observability,firewall"
    out(f"sudo {' '.join(bootstrap_argv(config, '--tags', tags))}")
    return 0


def pair_connect(
    system: System,
    config: LabConfig,
    node: str,
    code: str,
    connect: Callable[..., Any] | None = None,
) -> int:
    """On a lab node: pair with the inference node that shows the code.

    Args:
        system: Host access.
        config: The lab description.
        node: Tailnet name of the inference node.
        code: The code it shows.
        connect: Pairing client (``pairing.connect``; tests replace it).

    Returns:
        0 once paired.

    Raises:
        CommandError: On another node role, missing files or a failed pairing.
    """
    from pinka_lab import pairing  # noqa: PLC0415 - only for pairing

    require_root(system, "pair connect")
    if config.node_role != "lab":
        raise CommandError("pair connect runs on a lab node (node_role: lab)")
    if not re.match(r"^[a-z0-9][a-z0-9.-]{0,62}$", node):
        raise CommandError(f"not a tailnet node name: {node!r}")
    directory, me = _identity(system, config)
    try:
        peer = (connect or pairing.connect)(
            pairing.normalize(code),
            me,
            node,
            int(config.get("inference", "control_port", default=18710)),
        )
    except pairing.PairingError as exc:
        raise CommandError(str(exc)) from exc
    _store_peer(system, directory, peer)
    _reload_split_guard(system)
    out(f"Paired with inference node {peer.node} ({pairing.cert_sha256(peer.cert_pem)[:16]}...).")
    out(f"Set inference_remote_node: {peer.node} in the host_vars file and apply it:")
    out(
        "sudo "
        + " ".join(
            bootstrap_argv(config, "--tags", "inference_control,platform,observability,firewall")
        )
    )
    return 0


# --- WSL and updates ----------------------------------------------------------------------------


def wsl_helpers(config: LabConfig) -> int:
    """Print where the optional Windows helpers are and how to use them.

    Args:
        config: The lab description.

    Returns:
        0, or 1 on another provider.
    """
    if config.provider != "wsl2":
        sys.stderr.write("wsl-helpers: this host is not a WSL host\n")
        return 1
    directory = f"{config.infra_dir}/contrib/windows"
    out(f"Optional Windows helpers (never run by bootstrap): {directory}")
    out("  pinka-wsl.ps1          keep the WSL VM alive and the PC awake while the lab runs")
    out(f"  wslconfig.recommended  {config.pinka_root}/services/contrib/wslconfig.recommended")
    out("Copy them to Windows and follow docs/providers/wsl2.md; the lab never depends on them.")
    return 0


def update_host(system: System, config: LabConfig, yes: bool, extra: Sequence[str]) -> int:
    """Drain, update the OS keeping the holds, run bootstrap and doctor, show the fingerprint diff.

    Args:
        system: Host access.
        config: The lab description.
        yes: Do not ask for confirmation.
        extra: Further bootstrap options.

    Returns:
        0 on success, else the failing step's exit code.

    Raises:
        CommandError: On an unknown family or a refused confirmation.
    """
    from pinka_lab import fingerprint  # noqa: PLC0415 - avoids a cycle at import time

    require_root(system, "update-host")
    profile = json.loads(
        system.read_text(
            str(config.get("paths", "host_profile", default="/etc/pinka/host-profile.json"))
        )
        or "{}"
    )
    family = str(profile.get("family"))
    if family not in UPDATE_COMMANDS:
        raise CommandError(f"unknown family {family!r}")
    if (
        not yes
        and input(f"Update {config.machine} ({family}) now? The lab stops meanwhile [y/N] ")
        .strip()
        .lower()
        != "y"
    ):
        raise CommandError("not confirmed")
    before = fingerprint.current(system, config)
    if stop(system, config) != 0:
        return 1
    for argv in UPDATE_COMMANDS[family]:
        code = system.interactive(argv)
        if code != 0:
            return code
    code = system.interactive(bootstrap_argv(config, *extra))
    if code not in (0, 1):
        return code
    after = fingerprint.current(system, config)
    out("fingerprint changes:")
    for line in fingerprint.diff(before["fingerprint"], after["fingerprint"]) or ["(none)"]:
        out(f"  {line}")
    if system.info("/var/run/reboot-required") is not None:
        out("A reboot is required; reboot when convenient (bootstrap never reboots on its own).")
    return code


def docs_serve(
    system: System, config: LabConfig | None, directory: str | None, port: int | None
) -> int:
    """Serve the lab's documentation on the loopback address (owner answer H9).

    Args:
        system: Host access.
        config: The lab description, if this host has one.
        directory: Built documentation site, else ``docs.dir`` of the lab description.
        port: Port, else ``docs.port`` of the lab description.

    Returns:
        0 after the server was stopped.

    Raises:
        CommandError: Without a built site, or when the port is taken.
    """
    base = (config.get("docs", "dir") if config else None) or DOCS_DIR
    site = directory or f"{base}/{docs_store.CURRENT}"
    number = port or (config.get("docs", "port") if config else None) or DOCS_PORT
    if system.info(f"{site}/index.html") is None:
        raise CommandError(
            f"no built documentation in {site} (index.html is missing): install a release with "
            "sudo pinka docs install, or pass --dir with a site built by make docs"
        )
    out(
        f"Serving {site} at http://{DOCS_ADDRESS}:{number}/ on this host only; Ctrl-C stops.\n"
        f"From another computer: ssh -L {number}:{DOCS_ADDRESS}:{number} <user>@<lab host>, "
        f"then open http://{DOCS_ADDRESS}:{number}/ there."
    )
    try:
        system.serve_static(site, DOCS_ADDRESS, int(number))
    except OSError as exc:
        raise CommandError(f"cannot listen on {DOCS_ADDRESS}:{number}: {exc}") from exc
    return 0


def _docs_base(config: LabConfig | None) -> str:
    return str((config.get("docs", "dir") if config else None) or DOCS_DIR)


def _restart_docs_service(system: System, config: LabConfig | None) -> None:
    unit = str((config.get("docs", "unit") if config else None) or DOCS_UNIT)
    if system.run(["systemctl", "is-enabled", unit]).stdout.strip() == "enabled":
        system.run(["systemctl", "restart", unit], timeout=60)


def docs_install(
    system: System, config: LabConfig | None, tarball: str, sha256: str, version: str | None
) -> int:
    """Install a documentation release and make it current (§7.19).

    Args:
        system: Host access.
        config: The lab description, if this host has one.
        tarball: The release tarball.
        sha256: Its SHA-256, as published with the release.
        version: The version name (default: from the tarball's file name).

    Returns:
        0.

    Raises:
        CommandError: Not root, or the release was refused.
    """
    require_root(system, "docs install")
    try:
        message = system.docs_install(_docs_base(config), tarball, sha256, version)
    except docs_store.DocsError as exc:
        raise CommandError(str(exc)) from exc
    out(message)
    # A service that found nothing to serve at boot starts now.
    _restart_docs_service(system, config)
    return 0


def docs_rollback(system: System, config: LabConfig | None) -> int:
    """Switch the documentation back to the previous release (§7.19).

    Args:
        system: Host access.
        config: The lab description, if this host has one.

    Returns:
        0.

    Raises:
        CommandError: Not root, or there is nothing to roll back to.
    """
    require_root(system, "docs rollback")
    try:
        out(system.docs_rollback(_docs_base(config)))
    except docs_store.DocsError as exc:
        raise CommandError(str(exc)) from exc
    return 0


# The inference guard (firewall role, §7.11, ADR-0016): loading it resolves the
# clients that may reach the inference API from infra's list and the active
# platform's rendering.
INFERENCE_GUARD_UNIT = "pinka-inference-guard.service"
INFERENCE_GUARD_UNIT_FILE = f"/etc/systemd/system/{INFERENCE_GUARD_UNIT}"


def reload_inference_guard(system: System) -> tuple[bool, str]:
    """Load the inference guard again, so it admits only the current clients.

    Args:
        system: Host access.

    Returns:
        Whether it loaded (or there is none here), and what happened.
    """
    if system.info(INFERENCE_GUARD_UNIT_FILE) is None:
        return True, "no inference guard on this host"
    done = system.run(["systemctl", "reload-or-restart", INFERENCE_GUARD_UNIT], timeout=120)
    if not done.ok:
        detail = done.stderr.strip() or f"exit status {done.returncode}"
        return False, f"the inference guard did not load again ({detail})"
    return True, (
        "inference guard loaded again: it admits only infra's and the active platform's clients"
    )


def platform_purge(
    system: System,
    config: LabConfig,
    name: str,
    confirm: str | None,
    remove_secrets: bool = False,
) -> int:
    """Remove a replaced platform's releases, state, restricted area and database.

    The deployment tool refuses the active platform and a platform that was
    never replaced. Its accounts stay locked and its published signing keys
    stay; its secrets stay too unless ``remove_secrets``. What remains is
    printed. Then the inference guard loads again, so no account of the
    purged platform stays on its allowlist.

    Args:
        system: Host access.
        config: The lab description.
        name: The replaced platform.
        confirm: The name typed as confirmation; None asks for it.
        remove_secrets: Also remove its secrets directories, database password
            files and private signing key.

    Returns:
        0 when purged, 1 when the deployment tool failed or the inference
        guard did not load again.

    Raises:
        CommandError: Without root, a bad name or a wrong confirmation.
    """
    require_root(system, "platform purge")
    if not PLATFORM_NAME_RE.match(name):
        raise CommandError(f"{name!r} is not a platform name")
    if confirm is None:
        out(
            f"This removes the releases, content, state, restricted area and database of the "
            f"replaced platform {name}. It cannot be undone; take a backup first."
        )
        confirm = input("Type the platform name to purge it: ").strip()
    if confirm != name:
        raise CommandError("not confirmed: the typed name differs")
    tool = str(config.get("paths", "platform_tool", default=PLATFORM_TOOL))
    argv = [tool, "purge", name, "--confirm", confirm]
    if remove_secrets:
        argv.append("--remove-secrets")
    done = system.run(argv, timeout=1800)
    result = done.json() if done.stdout else None
    if done.returncode != 0 or not isinstance(result, dict):
        detail = result.get("error") if isinstance(result, dict) else done.stderr.strip()
        sys.stderr.write(f"pinka: purge failed: {detail}\n")
        return 1
    for path in result.get("removed", []):
        out(f"removed {path}")
    for database in result.get("databases", []):
        out(f"dropped database {database}")
    remaining = result.get("remaining", {})
    for label, key in (
        ("locked account", "locked_accounts"),
        ("secret", "secrets"),
        ("published signing key", "published_signing_keys"),
    ):
        for item in remaining.get(key, []):
            out(f"remains: {label} {item}")
    if remaining.get("secrets"):
        out(f"Remove the secrets too with: pinka platform purge {name} --remove-secrets")
    loaded, detail = reload_inference_guard(system)
    if not loaded:
        sys.stderr.write(
            f"pinka: {name} is purged, but {detail}; run: "
            f"systemctl restart {INFERENCE_GUARD_UNIT}\n"
        )
        return 1
    out(detail)
    return 0
