"""The pokus-lab commands other than doctor and fingerprint (BUILD_PROMPT §9, §19.5).

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
from collections.abc import Sequence
from typing import Any

from pokus_lab.config import LabConfig
from pokus_lab.system import System

ORCH_API = "/run/pokus/orch/api.sock"
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
        raise CommandError(f"{what} needs root (sudo pokus-lab ...)")


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


def preflight(system: System, config: LabConfig, as_json: bool) -> int:
    """Run preflight read-only.

    Args:
        system: Host access.
        config: The lab description.
        as_json: Print the JSON report instead of the summary.

    Returns:
        Preflight's exit code.
    """
    command = str(config.get("paths", "preflight"))
    argv = [command, "--no-write", "--srv-path", config.pokus_root]
    if as_json:
        done = system.run([*argv, "--quiet"], timeout=300)
        sys.stdout.write(done.stdout)
        sys.stderr.write(done.stderr)
        return done.returncode
    return system.interactive([*argv, "--output", os.devnull])


def start(system: System) -> int:
    """Start pokus-lab.target (it waits for the health gate).

    Args:
        system: Host access.

    Returns:
        0 on success.
    """
    require_root(system, "start")
    done = system.run(["systemctl", "start", "pokus-lab.target"], timeout=1200)
    if not done.ok:
        sys.stderr.write(done.stderr)
        return 1
    out("pokus-lab.target started")
    return 0


def drain(system: System, timeout: float) -> str:
    """Ask the orchestrator to drain and wait until no run is active.

    Args:
        system: Host access.
        timeout: Seconds to wait.

    Returns:
        What happened, for the operator.
    """
    if system.info(ORCH_API) is None:
        return "no orchestrator socket; nothing to drain"
    body = json.dumps({"timeout_seconds": int(timeout)}).encode()
    started = system.unix_http(ORCH_API, "POST", "/lab/drain", body)
    if started.status not in (200, 202):
        return f"the orchestrator did not accept the drain ({started.status or started.body})"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = system.unix_http(ORCH_API, "GET", "/lab/status").json() or {}
        if status.get("active_runs", 1) == 0:
            return "drained: no active runs"
        time.sleep(5)
    return f"timeout after {timeout:g} s; runs still active become interrupted"


def stop(system: System, timeout: float) -> int:
    """Drain the orchestrator, then stop pokus-lab.target.

    Args:
        system: Host access.
        timeout: Seconds the drain may take.

    Returns:
        0 on success.
    """
    require_root(system, "stop")
    out(drain(system, timeout))
    done = system.run(["systemctl", "stop", "pokus-lab.target"], timeout=600)
    if not done.ok:
        sys.stderr.write(done.stderr)
        return 1
    out("pokus-lab.target stopped")
    return 0


def _active(system: System, unit: str) -> str:
    return system.run(["systemctl", "is-active", unit]).stdout.strip() or "unknown"


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
            "pokus-lab: containers and Incus instances are only visible to root "
            "(sudo pokus-lab status)\n"
        )
    out("units:")
    for unit in ["pokus-lab.target", *config.get("units", default=[])]:
        out(f"  {unit:<40} {_active(system, unit)}")
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
    df = system.run(
        ["df", "--human-readable", "--output=avail,size", config.pokus_root]
    ).stdout.splitlines()
    out(f"disk {config.pokus_root}: {df[-1].strip() if df else 'unknown'} (available, size)")
    state_text = system.read_text(
        str(config.get("backup", "state", default="/var/lib/pokus/backup/state.json"))
    )
    last = ((json.loads(state_text) if state_text else {}).get("backup") or {}).get("last_success")
    when = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(last))) if last else "never"
    out(
        f"last backup: {when}" + ("" if config.get("backup", "configured") else " (not configured)")
    )
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
    units = (["pokus-pgdump.service"] if config.runs_lab else []) + ["pokus-backup.service"]
    done = system.run(["systemctl", "start", *units], timeout=7200)
    if not done.ok:
        sys.stderr.write(done.stderr or "backup failed; see journalctl -u pokus-backup.service\n")
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
    helper = str(config.get("backup", "helper", default="/usr/local/libexec/pokus/backup"))
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
    export_root = str(config.get("images", "export_root", default=f"{config.pokus_root}/images"))
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
            built = (image.get("properties") or {}).get("pokus.built_at", "?")
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
        env["POKUS_IMAGES_BLUEPRINTS"] = ",".join(image_chain(config, blueprint))
        if rebuild:
            env["POKUS_IMAGES_REBUILD"] = blueprint
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
        config.get("images", "files_helper", default="/usr/local/libexec/pokus/image-files")
    )
    export_root = str(config.get("images", "export_root", default=f"{config.pokus_root}/images"))
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
        admin: Whether the researcher gets pokus-admin.

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
    out("  pokus-wsl.ps1          keep the WSL VM alive and the PC awake while the lab runs")
    out(f"  wslconfig.recommended  {config.pokus_root}/services/contrib/wslconfig.recommended")
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
    from pokus_lab import fingerprint  # noqa: PLC0415 - avoids a cycle at import time

    require_root(system, "update-host")
    profile = json.loads(
        system.read_text(
            str(config.get("paths", "host_profile", default="/etc/pokus/host-profile.json"))
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
    if stop(system, timeout=float(config.get("lab", "drain_timeout", default=1800))) != 0:
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
