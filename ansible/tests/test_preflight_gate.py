"""The preflight gate's facts after the plan's switches (BUILD_PROMPT §2.3, §2.6)."""

from __future__ import annotations

import base64
import json
from typing import Any

from ansible_testlib import GROUP_VARS, ROLES, ansible_vars, load_yaml, template_value

EXPOSE = "Expose the host profile, the feature flags and the training settings"
DETECTED = {"gpu_inference": True, "gpu_training": True, "vm_targets": True}


def facts(switches: dict[str, Any], **extra: Any) -> dict[str, Any]:
    """The gate's facts for a fresh profile with these switches."""
    tasks = load_yaml(ROLES / "preflight_gate" / "tasks" / "main.yml")
    expose = next(t for t in tasks if t.get("name") == EXPOSE)
    profile = {"features": DETECTED, "requirements": {"switches": switches}}
    content = base64.b64encode(json.dumps(profile).encode()).decode()
    scope = ansible_vars(GROUP_VARS, preflight_gate_fresh={"content": content}, **extra)
    for name in ("preflight_gate_profile", "preflight_gate_switches"):
        scope[name] = template_value(expose["vars"][name], scope)
    found: dict[str, Any] = template_value(expose["ansible.builtin.set_fact"], scope)
    return found


def switches(serves: bool, runs: str, memory_max: str = "18G") -> dict[str, Any]:
    return {"inference": {"serves": serves}, "training": {"runs": runs, "memory_max": memory_max}}


def test_the_features_follow_the_switches() -> None:
    found = facts(switches(True, "gpu"))
    assert found["pinka_features"] == DETECTED
    assert found["pinka_training"] == "gpu"
    off = facts(switches(False, "cpu"))
    assert off["pinka_features"] == {**DETECTED, "gpu_inference": False, "gpu_training": False}
    assert off["pinka_training"] == "cpu"


def test_memory_max_auto_takes_the_computed_cap() -> None:
    caps = facts(switches(True, "gpu"))["pinka_training_caps"]
    assert caps == {**ansible_vars(GROUP_VARS)["training_caps"], "memory_max": "18G"}
    # A training_caps without memory_max (host_vars replace the whole value) is auto.
    caps = facts(switches(True, "gpu"), training_caps={"cpus": 4})["pinka_training_caps"]
    assert caps == {"cpus": 4, "memory_max": "18G"}
    # An explicit cap wins.
    explicit = {"memory_max": "12G", "cpus": 4}
    assert facts(switches(True, "gpu"), training_caps=explicit)["pinka_training_caps"] == explicit
