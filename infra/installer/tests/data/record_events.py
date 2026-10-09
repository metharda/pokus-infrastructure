"""Record the event streams the installer's runner tests replay (BUILD_PROMPT §18.9).

Run from ``infra/`` with the development environment::

    .venv/bin/python installer/tests/data/record_events.py

Every file is a real recording: bootstrap's own ``emit_event``
(``bootstrap/lib/log.sh``) writes the stage, signal and result events, and
``ansible-playbook --check`` with the ``pinka_events`` callback runs a
stand-in play on localhost. Its roles carry the names of site.yml's roles
(and of the helper roles they include); its tasks only print (``debug``) or
fail (``fail``), so nothing is provisioned. Timestamps are those of the
recording.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
INFRA = HERE.parents[2]
sys.path.insert(0, str(INFRA / "installer"))

from pinka_installer.tui.events import site_roles  # noqa: E402

STAGES_BEFORE = ("git", "prepreflight", "tooling", "frontend", "preflight", "ansible-env")
LOGIN_URL = "https://login.tailscale.com/a/0123456789abcdef"
DEPLOY_KEY = "ssh-ed25519 KEYPLACEHOLDER pinka-deploy@pinka-lab"
SIGNAL_MESSAGE = "WSL must restart to apply /etc/wsl.conf: run 'wsl --terminate <distro>'"
MOK_MESSAGE = (
    "Secure Boot is on and the NVIDIA module is signed with /var/lib/shim-signed/mok/MOK.der, "
    "which is not enrolled. Bootstrap imports it with your one-time MOK password. Reboot, "
    'choose "Enroll MOK" at the blue MOK Manager screen, enter that password, then re-run '
    "bootstrap."
)


def debug(name: str, message: str, changed: bool = False) -> str:
    """A task that prints."""
    return (
        f"- name: {name}\n  ansible.builtin.debug:\n    msg: {message!r}\n"
        f"  changed_when: {str(changed).lower()}\n"
    )


def failing(name: str, message: str) -> str:
    """A task that fails."""
    return f"- name: {name}\n  ansible.builtin.fail:\n    msg: {message!r}\n"


def include(role: str) -> str:
    """A task that includes a helper role."""
    return f"- name: Use {role}\n  ansible.builtin.include_role:\n    name: {role}\n"


def role_tasks(role: str) -> str:
    """Tasks of a stand-in role of the successful run."""
    tasks = debug(f"Configure {role}", f"{role} configured", changed=role != "doctor")
    if role == "docker":
        tasks += include("pkg")
    if role == "tailscale":
        tasks += debug(
            "Show the login URL", f"Open this URL to add pinka-lab to the tailnet: {LOGIN_URL}"
        )
        tasks += debug("Wait for the login to complete", "logged in")
    if role == "platform":
        tasks += include("deploy_source")
    return tasks


def bootstrap_events(events: Path, lines: list[tuple[str, ...]]) -> None:
    """Emit bootstrap events with bootstrap's own function."""
    script = 'set -Eeuo pipefail\nsource "$1"\nshift\n' + "\n".join(
        "emit_event " + " ".join(shlex.quote(part) for part in line) for line in lines
    )
    subprocess.run(
        ["bash", "-c", script, "record", str(INFRA / "bootstrap" / "lib" / "log.sh")],
        env={**os.environ, "PINKA_EVENTS_FILE": str(events)},
        check=True,
    )


def play(workdir: Path, events: Path, roles: dict[str, str]) -> int:
    """Run the stand-in play with the callback; returns ansible-playbook's status."""
    for role, tasks in roles.items():
        tasks_dir = workdir / "roles" / role / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        (tasks_dir / "main.yml").write_text(tasks, encoding="utf-8")
    top = [r for r in roles if r not in ("pkg", "deploy_source", "bootstrap_signal")]
    body = "".join(
        f"    - name: Role {r}\n      ansible.builtin.import_role:\n        name: {r}\n"
        for r in top
    )
    (workdir / "site.yml").write_text(
        "- name: Provision the Pokus lab host\n  hosts: localhost\n  connection: local\n"
        f"  gather_facts: false\n  tasks:\n{body}",
        encoding="utf-8",
    )
    (workdir / "ansible.cfg").write_text(
        "[defaults]\n"
        f"callback_plugins = {INFRA / 'ansible' / 'callback_plugins'}\n"
        "callbacks_enabled = pinka_events\nretry_files_enabled = False\n"
        f"roles_path = {workdir / 'roles'}\n",
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "ANSIBLE_CONFIG": str(workdir / "ansible.cfg"),
        "ANSIBLE_HOME": str(workdir / "home"),
        "ANSIBLE_LOCALHOST_WARNING": "False",
        "PINKA_EVENTS_FILE": str(events),
    }
    binary = Path(sys.executable).with_name("ansible-playbook")
    done = subprocess.run(
        [str(binary), "--check", "-i", "localhost,", str(workdir / "site.yml")],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return done.returncode


def planned_roles(provider: str = "wsl2") -> list[str]:
    """site.yml's roles with the given provider."""
    roles = []
    for role in site_roles(INFRA / "ansible" / "site.yml"):
        name = f"provider_{provider}" if role.startswith("provider_{{") else role
        if name not in roles:
            roles.append(name)
    return roles


def record(name: str, scenario: str) -> None:
    """Record one scenario into ``<name>``."""
    out = HERE / name
    out.unlink(missing_ok=True)
    before = [("bootstrap.stage", s, status) for s in STAGES_BEFORE for status in ("started", "ok")]
    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        bootstrap_events(out, [*before, ("bootstrap.stage", "ansible", "started")])
        roles = planned_roles()
        if scenario == "success":
            stand_in = {r: role_tasks(r) for r in roles}
            stand_in["pkg"] = debug("Install the packages", "installed", changed=True)
            stand_in["deploy_source"] = debug(
                "Show the deploy key (add it to the Git host as a read-only deploy key)",
                f"Deploy key of this host for git@github.com:example-org/pokus-platform.git "
                f"(read-only): {DEPLOY_KEY}",
            )
            rc = play(workdir, out, stand_in)
            assert rc == 0, rc
            bootstrap_events(
                out,
                [
                    ("bootstrap.stage", "ansible", "ok"),
                    ("bootstrap.stage", "doctor", "started"),
                    ("bootstrap.stage", "doctor", "ok"),
                    ("bootstrap.result", "bootstrap", "doctor_warnings", "1"),
                ],
            )
        elif scenario == "failure":
            upto = roles[: roles.index("docker") + 1]
            stand_in = {r: role_tasks(r) for r in upto}
            stand_in["docker"] += failing(
                "Start Docker", "Unable to start service docker: Job for docker.service failed"
            )
            stand_in["pkg"] = debug("Install the packages", "installed", changed=True)
            rc = play(workdir, out, stand_in)
            assert rc != 0, rc
            bootstrap_events(
                out,
                [
                    ("bootstrap.stage", "ansible", "failed", "60"),
                    ("bootstrap.result", "bootstrap", "failed", "60"),
                ],
            )
        else:
            # signal75: WSL restart; signal76: Secure Boot MOK import and reboot (I1)
            code, provider, message = (
                ("75", "wsl2", SIGNAL_MESSAGE)
                if scenario == "signal75"
                else ("76", "baremetal", MOK_MESSAGE)
            )
            roles = planned_roles(provider)
            upto = roles[: roles.index(f"provider_{provider}") + 1]
            stand_in = {r: role_tasks(r) for r in upto}
            stand_in[f"provider_{provider}"] += include("bootstrap_signal")
            stand_in["bootstrap_signal"] = debug(
                "Write the bootstrap signal file", "written", changed=True
            ) + failing(
                "Stop the play with the bootstrap signal",
                f"PINKA-BOOTSTRAP-SIGNAL {code}: {message}",
            )
            rc = play(workdir, out, stand_in)
            assert rc != 0, rc
            bootstrap_events(
                out,
                [
                    ("bootstrap.signal", "ansible", "signaled", code, message),
                    ("bootstrap.stage", "ansible", "failed", code),
                    ("bootstrap.result", "bootstrap", "signal", code),
                ],
            )
    print(f"recorded {out.relative_to(INFRA)}")


if __name__ == "__main__":
    record("events-success.jsonl", "success")
    record("events-failure.jsonl", "failure")
    record("events-signal75.jsonl", "signal75")
    record("events-signal76.jsonl", "signal76")
