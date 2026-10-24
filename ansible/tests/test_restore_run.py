"""restore.yml runs without site.yml's preflight gate (BUILD_PROMPT §5.2 step 10, §7.15).

A real ansible-playbook run of restore.yml on its own, in check mode against
localhost and limited to its always-tagged tasks, which change nothing: the
feature flags and the training settings must come from the host profile
bootstrap passes, as the gate's do, before any task reads them.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from ansible_testlib import ANSIBLE, INFRA, evaluate, fixture_profile, load_yaml

GPU = "ubuntu2404-baremetal-nvidia-driver-loaded"
SWITCHED_OFF = "ubuntu2404-gpu-switched-off"
FLAGS = ("pinka_host_profile", "pinka_features", "pinka_training", "pinka_training_caps")


def restore_facts(tmp_path: Path, profile: dict[str, Any]) -> dict[str, Any]:
    """Run restore.yml's always-tagged tasks; the facts its set_fact tasks set."""
    extra = tmp_path / "vars.json"
    extra.write_text(
        json.dumps({"host_profile": profile, "pinka_bootstrap": {"interactive": False}}),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "ANSIBLE_CONFIG": str(ANSIBLE / "ansible.cfg"),
        "ANSIBLE_HOME": str(tmp_path / "home"),
        "ANSIBLE_COLLECTIONS_PATH": str(INFRA / ".ansible-collections"),
        "ANSIBLE_STDOUT_CALLBACK": "ansible.posix.json",
        "ANSIBLE_LOCALHOST_WARNING": "False",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    binary = Path(sys.executable).with_name("ansible-playbook")
    done = subprocess.run(
        [
            str(binary),
            str(ANSIBLE / "restore.yml"),
            "--check",
            "--tags",
            "always",
            "-e",
            "machine=example-machine",
            "-e",
            f"@{extra}",
            "-e",
            f"ansible_python_interpreter={sys.executable}",
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=ANSIBLE,
        check=False,
    )
    assert done.returncode == 0, done.stdout[-4000:] + done.stderr[-4000:]
    facts: dict[str, Any] = {}
    for task in json.loads(done.stdout)["plays"][0]["tasks"]:
        facts.update(task["hosts"]["localhost"].get("ansible_facts", {}))
    return facts


@pytest.mark.parametrize(
    ("fixture", "serves", "runs"), [(GPU, True, "gpu"), (SWITCHED_OFF, False, "off")]
)
def test_restore_sets_the_flags_without_the_gate(
    tmp_path: Path, fixture: str, serves: bool, runs: str
) -> None:
    profile = fixture_profile(fixture)
    switches = profile["requirements"]["switches"]
    assert (switches["inference"]["serves"], switches["training"]["runs"]) == (serves, runs)
    facts = restore_facts(tmp_path, profile)
    assert set(FLAGS) <= set(facts)
    assert facts["pinka_features"]["gpu_inference"] is serves
    assert facts["pinka_features"]["gpu_training"] is (runs == "gpu")
    assert facts["pinka_training"] == runs
    assert facts["pinka_training_caps"]["memory_max"] == switches["training"]["memory_max"]
    # The model re-verification and the re-render of the restored deployment
    # run exactly where vLLM serves.
    block = next(
        t
        for t in load_yaml(ANSIBLE / "restore.yml")[0]["tasks"]
        if t["name"] == "Restore the snapshot"
    )
    scope = {**facts, "node_role": "full"}
    for name in ("Re-verify the model files", "Render the restored vLLM deployment"):
        task = next(t for t in block["block"] if t["name"] == name)
        conditions = [c for c in task["when"] if "pinka_features" in c]
        assert conditions, name
        assert all(evaluate(c, scope) is serves for c in conditions), name


def test_the_gate_and_restore_share_the_facts() -> None:
    # One definition of the flags: the gate passes its fresh profile, restore
    # the profile bootstrap passes, before any of its tasks reads them.
    gate = load_yaml(ANSIBLE / "roles" / "preflight_gate" / "tasks" / "main.yml")
    imported = [t for t in gate if t.get("ansible.builtin.import_tasks") == "facts.yml"]
    assert len(imported) == 1
    assert "preflight_gate_fresh" in imported[0]["vars"]["preflight_gate_profile"]
    tasks = load_yaml(ANSIBLE / "restore.yml")[0]["tasks"]
    position = next(
        i
        for i, t in enumerate(tasks)
        if (t.get("ansible.builtin.import_role") or {}).get("tasks_from") == "facts"
    )
    facts = tasks[position]
    assert facts["ansible.builtin.import_role"]["name"] == "preflight_gate"
    assert facts["vars"] == {"preflight_gate_profile": "{{ host_profile }}"}
    assert "always" in facts["tags"]
    text = [json.dumps(t) for t in tasks]
    first = min(i for i, t in enumerate(text) if any(f in t for f in FLAGS[1:]) and i != position)
    assert position < first
