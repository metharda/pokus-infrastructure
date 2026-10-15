"""Inside a test VM: the harness's helpers for the reference platform (BUILD_PROMPT §19.9).

Run by the harness with the Ansible venv's Python (PyYAML and Ansible's vault
API), as a script, never imported by the lab: ``python -I guest.py
<command> ...``; every command prints one JSON line.

* ``enable-platform`` sets ``platform``, ``content`` and ``platform_settings``
  in the machine's host_vars and adds the secrets the platform needs to the
  vault: a password for each of its database roles
  (``vault_postgres_passwords``) and a value for each secret its services
  name (``vault_platform_secrets``). Values already in the vault are kept,
  new ones are random and never printed. Answers cannot enable a contract v2
  platform yet (plan step 3d.7), so the distribution tests do it here.
* ``set-host-vars`` sets other host_vars keys (``restore_snapshot``).
* ``wait-report``, ``wait-run`` and ``wait-checkpoint`` wait for the agent's
  probe report, for a training run's end and for a run's checkpoint.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

HOST_VARS_HEADER = "# Platform settings added by make test-distros (tools/test-distros/guest.py).\n"
VAULT_HEADER = "# Managed by the pinka installer (group_vars/all/vault.yml).\n"
CLI = "/usr/local/bin/refplat"
JOBS = Path("/srv/pinka/training/jobs")
# The reference platform's final run statuses (refplat.training.FINAL).
FINAL = ("succeeded", "failed", "interrupted", "ended")
INTERVAL = 5.0


def set_host_vars(path: Path, values: Mapping[str, Any]) -> None:
    """Set top-level keys of a host_vars file, keeping the others.

    Args:
        path: The host_vars file.
        values: The keys to set.
    """
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise SystemExit(f"{path} is not a mapping")
    data.update(values)
    text = HOST_VARS_HEADER + yaml.safe_dump(data, sort_keys=False, default_flow_style=False)
    path.write_text(text, encoding="utf-8")


def add_secrets(
    vault: dict[str, Any], roles: Sequence[str], platform_secrets: Sequence[str]
) -> list[str]:
    """Add the platform's missing secrets to the vault's data.

    Args:
        vault: The decrypted vault (changed in place).
        roles: Database roles that need a password.
        platform_secrets: ``<service>/<name>`` of each secret.

    Returns:
        The names of the entries added (never their values).
    """
    added = []
    passwords = dict(vault.get("vault_postgres_passwords") or {})
    for role in roles:
        if not passwords.get(role):
            passwords[role] = secrets.token_urlsafe(24)
            added.append(f"vault_postgres_passwords.{role}")
    vault["vault_postgres_passwords"] = passwords
    services = {k: dict(v) for k, v in (vault.get("vault_platform_secrets") or {}).items()}
    for item in platform_secrets:
        service, _, name = item.partition("/")
        if not service or not name:
            raise SystemExit(f"a secret is <service>/<name>, not {item!r}")
        if not services.setdefault(service, {}).get(name):
            services[service][name] = secrets.token_urlsafe(32)
            added.append(f"vault_platform_secrets.{service}.{name}")
    vault["vault_platform_secrets"] = services
    return added


def update_vault(
    path: Path, password_file: Path, roles: Sequence[str], platform_secrets: Sequence[str]
) -> list[str]:
    """Decrypt the vault, add the platform's secrets and encrypt it again (0600).

    Returns:
        The names of the entries added.
    """
    from ansible.parsing.vault import VaultLib, VaultSecret  # noqa: PLC0415 - heavy import

    password = password_file.read_text(encoding="utf-8").strip().encode("utf-8")
    lib = VaultLib([("default", VaultSecret(password))])
    data = yaml.safe_load(lib.decrypt(path.read_bytes()).decode("utf-8")) or {}
    if not isinstance(data, dict):
        raise SystemExit(f"{path} does not hold a mapping")
    added = add_secrets(data, roles, platform_secrets)
    if added:
        plaintext = VAULT_HEADER + yaml.safe_dump(data, sort_keys=False)
        temp = path.with_name(f".{path.name}.tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(lib.encrypt(plaintext.encode("utf-8")))
        temp.replace(path)
    return added


def cli(*args: str) -> dict[str, Any] | None:
    """One answer of the reference platform's CLI as root, or None if it gave none."""
    done = subprocess.run([CLI, *args], capture_output=True, text=True, check=False)  # noqa: S603
    try:
        value = json.loads(done.stdout)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def poll(
    probe: Callable[[], dict[str, Any] | None],
    timeout: float,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any] | None:
    """Call ``probe`` until it returns something or the time is up."""
    deadline = time.monotonic() + timeout
    while True:
        found = probe()
        if found is not None or time.monotonic() >= deadline:
            return found
        sleep(INTERVAL)


def agent_report() -> dict[str, Any] | None:
    """The agent's latest probe report, once it sent one."""
    answer = cli("probe-report") or {}
    report = answer.get("report") or {}
    return report if report.get("outcomes") else None


def run_end(run: str) -> dict[str, Any] | None:
    """A run's record in the controller once it is final."""
    item = ((cli("runs") or {}).get("runs") or {}).get(run)
    return {"run": run, **item} if item and item.get("status") in FINAL else None


def checkpoint(run: str, step: int, jobs: Path = JOBS) -> dict[str, Any] | None:
    """A run's checkpoint once it reached ``step``."""
    try:
        saved = json.loads((jobs / run / "checkpoints" / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return saved if isinstance(saved, dict) and int(saved.get("step", 0)) >= step else None


def emit(value: Mapping[str, Any] | None, what: str) -> int:
    """Print the result line; 1 when nothing came."""
    if value is None:
        sys.stdout.write(json.dumps({"ok": False, "error": f"no {what} in time"}) + "\n")
        return 1
    sys.stdout.write(json.dumps({"ok": True, **value}, sort_keys=True) + "\n")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run a command; prints one JSON line."""
    parser = argparse.ArgumentParser(prog="guest.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    host_vars = sub.add_parser("set-host-vars")
    host_vars.add_argument("--host-vars", type=Path, required=True)
    host_vars.add_argument("--values", required=True, help="the keys to set, as JSON")
    report = sub.add_parser("wait-report")
    report.add_argument("--timeout", type=float, default=600)
    run = sub.add_parser("wait-run")
    run.add_argument("run")
    run.add_argument("--timeout", type=float, default=1800)
    step = sub.add_parser("wait-checkpoint")
    step.add_argument("run")
    step.add_argument("--step", type=int, required=True)
    step.add_argument("--timeout", type=float, default=600)
    enable = sub.add_parser("enable-platform")
    enable.add_argument("--host-vars", type=Path, required=True)
    enable.add_argument("--vault", type=Path, required=True)
    enable.add_argument("--vault-password-file", type=Path, required=True)
    enable.add_argument("--platform", required=True, help="the platform: setting, as JSON")
    enable.add_argument("--content", default="{}", help="the content: setting, as JSON")
    enable.add_argument("--settings", default="{}", help="platform_settings, as JSON")
    enable.add_argument("--postgres-role", action="append", default=[])
    enable.add_argument("--secret", action="append", default=[], help="<service>/<name>")
    args = parser.parse_args(argv)
    if args.command == "set-host-vars":
        set_host_vars(args.host_vars, json.loads(args.values))
        return emit({}, "host_vars")
    if args.command == "wait-report":
        return emit(poll(agent_report, args.timeout), "probe report of the agent")
    if args.command == "wait-run":
        ended = poll(lambda: run_end(args.run), args.timeout)
        code = emit(ended, f"end of run {args.run}")
        return code if code or (ended or {}).get("status") == "succeeded" else 1
    if args.command == "wait-checkpoint":
        found = poll(lambda: checkpoint(args.run, args.step), args.timeout)
        return emit(found, f"checkpoint of run {args.run} at step {args.step}")
    values = {
        "platform": json.loads(args.platform),
        "content": json.loads(args.content),
        "platform_settings": json.loads(args.settings),
    }
    set_host_vars(args.host_vars, values)
    added = update_vault(args.vault, args.vault_password_file, args.postgres_role, args.secret)
    sys.stdout.write(json.dumps({"ok": True, "added": added}, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
