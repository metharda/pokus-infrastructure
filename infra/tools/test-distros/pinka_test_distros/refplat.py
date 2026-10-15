"""The reference platform in a test VM (BUILD_PROMPT §7.18, §19.9): what its stages run.

The platform is deployed from the VM's clone of the repository
(``file:///root/pinka`` at the tested commit), its content package
``cases`` from the same clone. The renamed copy that replaces it lives in a
repository of its own in the VM, so the tested clone never changes. Every
command here runs as root in the VM; the guest helper (``guest.py``) runs
with the Ansible venv's Python.
"""

from __future__ import annotations

import copy
import json
import shlex
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from pinka_platform import database, deploy, loader
from pinka_platform.contract import Contract

INFRA = Path(__file__).resolve().parents[3]
RELEASE = INFRA / "tests" / "reference-platform"
GUEST_REPO = "/root/pinka"
RELEASE_PATH = "infra/tests/reference-platform"
CASES_PATH = "infra/tests/reference-platform-cases"
RENAMED_REPO = "/root/refplat-renamed"
RENAMED_NAME = "refplatb"
GUEST_PYTHON = "/opt/pinka/ansible-venv/bin/python"
GUEST_HELPER = f"{GUEST_REPO}/infra/tools/test-distros/pinka_test_distros/guest.py"
CLI = "/usr/local/bin/refplat"
ENDPOINTS = "/etc/pinka/platform/endpoints.toml"
ACTIVE = "/etc/pinka/platform/active.json"
TRANSITIONS = "/var/lib/pinka/gpu/transitions.jsonl"
# The job of hosts without a GPU (the test VMs have none).
SETTINGS = {"job": "tunecpu"}
# The run the reboot interrupts: long enough to be stopped in the middle.
RESUME_STEPS = 30
RESUME_STEP_SECONDS = 4
RESUME_AFTER_STEP = 3
STANDIN_UNIT = "pinka-test-inference-standin"
# A Git identity for the renamed copy's commit (test data, never pushed).
GIT_IDENTITY = "-c user.name=researcher1 -c user.email=researcher1@example.com"


def manifest(release: Path = RELEASE) -> dict[str, Any]:
    """The reference platform's manifest."""
    data: dict[str, Any] = yaml.safe_load(
        (release / loader.MANIFEST_NAME).read_text(encoding="utf-8")
    )
    return data


def renamed(data: Mapping[str, Any]) -> dict[str, Any]:
    """The manifest of the renamed copy."""
    copied = copy.deepcopy(dict(data))
    copied["name"] = RENAMED_NAME
    return copied


def source(repo: str, ref: str, path: str) -> dict[str, str]:
    """A ``platform`` or ``content`` entry of the host_vars."""
    return {"repo": repo, "ref": ref, "path": path}


def guest(*args: str) -> list[str]:
    """A command of the guest helper."""
    return [GUEST_PYTHON, "-I", GUEST_HELPER, *args]


def enable(
    host_vars: str,
    vault: str,
    vault_password_file: str,
    *,
    platform: Mapping[str, str],
    content: Mapping[str, Mapping[str, str]],
    data: Mapping[str, Any],
    contract: Contract,
) -> list[str]:
    """The guest helper's ``enable-platform`` for a manifest.

    Args:
        host_vars: The machine's host_vars file in the VM.
        vault: The vault in the VM.
        vault_password_file: Its password file in the VM.
        platform: The ``platform`` entry.
        content: The ``content`` entries.
        data: The platform's manifest (its database roles and secrets).
        contract: The contract.

    Returns:
        The command.
    """
    roles = database.plan(data, contract, [])["vault_roles"]
    argv = guest(
        "enable-platform",
        "--host-vars",
        host_vars,
        "--vault",
        vault,
        "--vault-password-file",
        vault_password_file,
        "--platform",
        json.dumps(platform, sort_keys=True),
        "--content",
        json.dumps(content, sort_keys=True),
        "--settings",
        json.dumps({"ctl": SETTINGS}, sort_keys=True),
    )
    for role in roles:
        argv += ["--postgres-role", role]
    for service in data["services"]:
        for name in service.get("secrets", []):
            argv += ["--secret", f"{service['name']}/{name}"]
    return argv


def renamed_copy_script(name: str = RENAMED_NAME) -> str:
    """Bash: a repository with a copy of the release and the cases, renamed; prints its commit."""
    release = shlex.quote(f"{GUEST_REPO}/{RELEASE_PATH}")
    cases = shlex.quote(f"{GUEST_REPO}/{CASES_PATH}")
    repo = shlex.quote(RENAMED_REPO)
    return (
        f"set -e; rm -rf {repo}; mkdir {repo}; cd {repo}; git init -q;"
        f" cp -a {release} release; cp -a {cases} cases;"
        f" sed -i 's/^name: refplat$/name: {name}/' release/platform-manifest.yaml;"
        f" grep -qx 'name: {name}' release/platform-manifest.yaml;"
        f" git add -A; git {GIT_IDENTITY} commit -q -m 'refplat renamed to {name}';"
        " git rev-parse HEAD"
    )


def standin_script(port: int, python: str = deploy.PYTHON) -> str:
    """Bash: a root listener on the vLLM port while nothing serves there (no GPU).

    The inference guard decides who reaches it, as it decides who reaches
    vLLM. Prints ``started`` or ``busy``.
    """
    code = (
        f'import socket\ns = socket.create_server(("127.0.0.1", {port}))\n'
        "while True:\n    s.accept()[0].close()"
    )
    return (
        f"if ss -Hltn 'sport = :{port}' | grep -q .; then echo busy;"
        f" else systemd-run --quiet --unit={STANDIN_UNIT} {python} -c {shlex.quote(code)}"
        " && echo started; fi"
    )


def last_json(stdout: str) -> dict[str, Any]:
    """The last line of output that is a JSON object.

    Raises:
        ValueError: If there is none.
    """
    for line in reversed(stdout.splitlines()):
        if line.startswith("{"):
            value = json.loads(line)
            if isinstance(value, dict):
                return value
    raise ValueError("no JSON answer")


def run_outcomes(transitions: str, run: str) -> list[str]:
    """The outcomes of a run's transitions in the arbiter's log, in order."""
    found = []
    for line in transitions.splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and item.get("run") == run:
            found.append(str(item.get("outcome")))
    return found
