"""The reference platform in a test VM (BUILD_PROMPT §7.18, §19.9): what its stages run.

The platform is deployed from the VM's clone of the repository
(``file:///root/pinka`` at the tested commit), its content package
``cases`` from the same clone. The renamed copy that replaces it lives in a
repository of its own in the VM, so the tested clone never changes. Every
command here runs as root in the VM; the guest helper (``guest.py``) runs
with the Ansible venv's Python. The platform is enabled, replaced and
restored through the installer's answers (``bootstrap.sh --answers``), as an
operator does it.
"""

from __future__ import annotations

import base64
import copy
import json
import shlex
import urllib.parse
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from pinka_platform import deploy, loader

INFRA = Path(__file__).resolve().parents[3]
RELEASE = INFRA / "tests" / "reference-platform"
GUEST_REPO = "/root/pinka"
RELEASE_PATH = "tests/reference-platform"
CASES_PATH = "tests/reference-platform-cases"
RENAMED_REPO = "/root/refplat-renamed"
RENAMED_NAME = "refplatb"
GUEST_PYTHON = "/opt/pinka/ansible-venv/bin/python"
GUEST_HELPER = f"{GUEST_REPO}/tools/test-distros/pinka_test_distros/guest.py"
CLI = "/usr/local/bin/refplat"
ENDPOINTS = "/etc/pinka/platform/endpoints.toml"
LAB = "/etc/pinka/lab.json"
ACTIVE = "/etc/pinka/platform/active.json"
TRANSITIONS = "/var/lib/pinka/gpu/transitions.jsonl"
# The job of hosts without a GPU (the test VMs have none).
SETTINGS = {"job": "tunecpu"}
# The run the reboot interrupts: long enough to be stopped in the middle.
RESUME_STEPS = 30
RESUME_STEP_SECONDS = 4
RESUME_AFTER_STEP = 3
STANDIN_UNIT = "pinka-test-inference-standin"
STANDIN_MODEL = "standin-model"
# The inference API's stand-in: /health and /v1/models of vLLM's OpenAI API.
STANDIN_CODE = """import http.server
import json
import sys

MODELS = json.dumps({"object": "list", "data": [{"id": sys.argv[2], "object": "model"}]}).encode()


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = {"/health": b"", "/v1/models": MODELS}.get(self.path)
        if body is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


http.server.ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
"""
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
    """A ``platform`` or ``content`` source of the answers."""
    return {"repo": repo, "ref": ref, "path": path}


def guest(*args: str) -> list[str]:
    """A command of the guest helper."""
    return [GUEST_PYTHON, "-I", GUEST_HELPER, *args]


def platform_answers(
    platform: Mapping[str, str], content: Mapping[str, Mapping[str, str]]
) -> dict[str, Any]:
    """The answers' platform sections for a release and its content packages.

    The installer's answers replay inspects the release and adds its database
    roles and secrets to the vault (generated, never printed), as for any lab.

    Args:
        platform: The release's source.
        content: The content packages' sources.

    Returns:
        ``platform``, ``content`` and ``platform_settings``.
    """
    return {
        "platform": dict(platform),
        "content": {name: dict(entry) for name, entry in content.items()},
        "platform_settings": {"ctl": dict(SETTINGS)},
    }


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


def vllm_port(lab: Mapping[str, Any]) -> int:
    """The inference API's port from ``/etc/pinka/lab.json`` (its vLLM health URL).

    Raises:
        ValueError: If the file names no port.
    """
    port = urllib.parse.urlsplit(str((lab.get("vllm") or {}).get("health_url", ""))).port
    if port is None:
        raise ValueError("lab.json names no vLLM port")
    return port


def standin_command(port: int, python: str = deploy.PYTHON) -> str:
    """The stand-in's ``ExecStart``: its code travels base64-encoded (no unit escaping)."""
    encoded = base64.b64encode(STANDIN_CODE.encode("utf-8")).decode("ascii")
    code = f"import base64, sys; exec(base64.b64decode('{encoded}'))"
    return f'{python} -I -c "{code}" {port} {STANDIN_MODEL}'


def standin_script(port: int, python: str = deploy.PYTHON) -> str:
    """Bash: a root HTTP server on the vLLM port while nothing serves there (no GPU).

    It answers ``/health`` and ``/v1/models`` like vLLM, so the health gate
    passes for a platform that requires ``inference`` and the broker gets a
    model list. It is an enabled unit, so it survives the reboot of the
    ``resume`` stage. The inference guard decides who reaches it, as it
    decides who reaches vLLM. Prints ``running``, ``started`` or ``busy``.
    """
    unit = f"/etc/systemd/system/{STANDIN_UNIT}.service"
    text = (
        "[Unit]\n"
        "Description=Stand-in for the inference API (the test VM has no GPU)\n"
        "After=network.target\n\n"
        "[Service]\n"
        f"ExecStart={standin_command(port, python)}\n"
        "Restart=on-failure\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )
    return (
        f"if systemctl is-active --quiet {STANDIN_UNIT}; then echo running;"
        f" elif ss -Hltn 'sport = :{port}' | grep -q .; then echo busy;"
        f" else printf %s {shlex.quote(text)} >{unit}"
        f" && systemctl daemon-reload && systemctl enable --now --quiet {STANDIN_UNIT}"
        f" && for _ in $(seq 50); do (exec 3<>/dev/tcp/127.0.0.1/{port}) 2>/dev/null && break;"
        " sleep 0.2; done && echo started; fi"
    )


def standin_removal() -> str:
    """Bash: the stand-in goes again, before uninstall (it is the harness's, not pinka's)."""
    unit = f"/etc/systemd/system/{STANDIN_UNIT}.service"
    return (
        f"systemctl disable --now --quiet {STANDIN_UNIT} 2>/dev/null;"
        f" rm -f {unit} && systemctl daemon-reload"
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
