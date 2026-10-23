"""The pinka_events callback (BUILD_PROMPT §18.5).

A real ansible-playbook run in check mode against localhost with tasks that
touch nothing (debug, set_fact, fail); the events must describe it and must
not contain the values of no_log tasks.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from ansible_testlib import ANSIBLE

SECRET = "not-a-real-secret-0000"  # noqa: S105 - must never appear in the events

PLAY = f"""
- name: Events
  hosts: localhost
  connection: local
  gather_facts: false
  tasks:
    - name: Say hello
      ansible.builtin.debug:
        msg: hello
      changed_when: true
      notify: Handler

    - name: Remember a secret
      ansible.builtin.set_fact:
        secret_value: "{SECRET}"
      no_log: true

    - name: Fail with a secret
      ansible.builtin.fail:
        msg: "{SECRET}"
      no_log: true
      ignore_errors: true  # the event must hide the message

    - name: Fail visibly
      ansible.builtin.fail:
        msg: visible failure
      ignore_errors: true  # the event carries the message

    - name: Skip me
      ansible.builtin.debug:
        msg: never
      when: false

  handlers:
    - name: Handler
      ansible.builtin.debug:
        msg: handled
"""


ROLE_PLAY = """
- name: Roles
  hosts: localhost
  connection: local
  gather_facts: false
  roles:
    - demo
"""


def run_play(
    tmp_path: Path, env_extra: dict[str, str], text: str = PLAY
) -> subprocess.CompletedProcess[str]:
    play = tmp_path / "play.yml"
    play.write_text(text, encoding="utf-8")
    config = tmp_path / "ansible.cfg"
    config.write_text(
        "[defaults]\n"
        f"callback_plugins = {ANSIBLE / 'callback_plugins'}\n"
        "callbacks_enabled = pinka_events\n"
        "retry_files_enabled = False\n",
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "ANSIBLE_CONFIG": str(config),
        "ANSIBLE_HOME": str(tmp_path / "home"),
        "ANSIBLE_LOCALHOST_WARNING": "False",
        **env_extra,
    }
    binary = Path(sys.executable).with_name("ansible-playbook")
    return subprocess.run(
        [str(binary), "--check", "-i", "localhost,", str(play)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def events(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_events_describe_the_play(tmp_path: Path) -> None:
    out = tmp_path / "events.jsonl"
    done = run_play(tmp_path, {"PINKA_EVENTS_FILE": str(out)})
    assert done.returncode == 0, done.stdout + done.stderr
    found = events(out)
    assert SECRET not in out.read_text(encoding="utf-8")
    assert found[0]["type"] == "ansible.play_start" and found[0]["play"] == "Events"
    assert all(e["ts"].endswith("Z") for e in found)
    ends = {e["task"]: e for e in found if e["type"] == "ansible.task_end"}
    assert ends["Say hello"]["status"] == "changed" and ends["Say hello"]["changed"] is True
    assert ends["Say hello"]["duration"] >= 0
    assert ends["Remember a secret"]["status"] == "ok"
    assert ends["Fail with a secret"]["status"] == "ignored"
    assert ends["Fail with a secret"]["message"] == "the output is hidden (no_log)"
    assert ends["Fail visibly"]["message"] == "visible failure"
    assert ends["Skip me"]["status"] == "skipped"
    assert ends["Handler"]["status"] == "ok"
    starts = [e for e in found if e["type"] == "ansible.task_start"]
    assert [e["handler"] for e in starts if e["task"] == "Handler"] == [True]
    stats = [e for e in found if e["type"] == "ansible.stats"]
    assert stats == [
        {
            "ts": stats[0]["ts"],
            "type": "ansible.stats",
            "host": "localhost",
            "ok": stats[0]["ok"],
            "changed": 1,
            "failures": 0,
            "skipped": 1,
            "unreachable": 0,
        }
    ]
    messages = [e for e in found if e["type"] == "ansible.message"]
    assert [(e["task"], e["message"]) for e in messages] == [
        ("Say hello", "hello"),
        ("Handler", "handled"),
    ]
    assert "Say hello" in done.stdout  # the human-readable output is unchanged


def test_task_names_carry_no_role_prefix(tmp_path: Path) -> None:
    tasks = tmp_path / "roles" / "demo" / "tasks"
    tasks.mkdir(parents=True)
    (tasks / "main.yml").write_text(
        "- name: Wait for the login to complete\n  ansible.builtin.debug:\n    msg: done\n",
        encoding="utf-8",
    )
    out = tmp_path / "events.jsonl"
    done = run_play(tmp_path, {"PINKA_EVENTS_FILE": str(out)}, ROLE_PLAY)
    assert done.returncode == 0, done.stdout + done.stderr
    found = events(out)
    assert [e["role"] for e in found if e["type"] == "ansible.role_start"] == ["demo"]
    ends = [e for e in found if e["type"] == "ansible.task_end"]
    # the installer matches task names (tailscale's login), so no "demo : " prefix
    assert [e["task"] for e in ends] == ["Wait for the login to complete"]
    assert "demo : Wait for the login to complete" in done.stdout  # Ansible's own output keeps it


def test_no_stream_no_events(tmp_path: Path) -> None:
    env = {k: "" for k in ("PINKA_EVENTS_FILE", "PINKA_EVENTS_FD")}
    done = run_play(tmp_path, env)
    assert done.returncode == 0, done.stdout + done.stderr
    assert not list(tmp_path.glob("*.jsonl"))


def test_events_to_a_file_descriptor(tmp_path: Path) -> None:
    read_end, write_end = os.pipe()
    play = tmp_path / "play.yml"
    play.write_text(PLAY, encoding="utf-8")
    with os.fdopen(read_end, encoding="utf-8") as reader:
        os.set_inheritable(write_end, True)
        done = subprocess.Popen(
            [
                str(Path(sys.executable).with_name("ansible-playbook")),
                "--check",
                "-i",
                "localhost,",
                str(play),
            ],
            env={
                **os.environ,
                "ANSIBLE_HOME": str(tmp_path / "home"),
                "ANSIBLE_CALLBACK_PLUGINS": str(ANSIBLE / "callback_plugins"),
                "ANSIBLE_CALLBACKS_ENABLED": "pinka_events",
                "ANSIBLE_CONFIG": str(tmp_path / "none.cfg"),
                "ANSIBLE_LOCALHOST_WARNING": "False",
                "PINKA_EVENTS_FD": str(write_end),
            },
            pass_fds=(write_end,),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        os.close(write_end)
        lines = reader.read().splitlines()
        assert done.wait(timeout=300) == 0
    assert json.loads(lines[0])["type"] == "ansible.play_start"
    assert SECRET not in "\n".join(lines)
