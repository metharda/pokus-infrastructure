"""Secure Boot MOK flow of the Ansible side (approved deviation I1).

The expressions are taken from the task files themselves and evaluated with
Ansible's templating, so the tests follow the code: the signal file names the
key bootstrap imports, the module signing key is picked from the candidates
that exist, and preflight_gate runs preflight with bootstrap's choices.
"""

from __future__ import annotations

import json
from typing import Any

from ansible_testlib import ROLES, evaluate, fixture_profile, load_yaml

CONSENT = "ubuntu2404-baremetal-nvidia-no-driver-secureboot-consent"


def task(role: str, name: str, file: str = "main.yml") -> dict[str, Any]:
    """The task called ``name`` in a role's task file (blocks included)."""
    pending = list(load_yaml(ROLES / role / "tasks" / file))
    while pending:
        item = pending.pop(0)
        if item.get("name") == name:
            found: dict[str, Any] = item
            return found
        pending.extend(item.get("block", []))
    raise AssertionError(f"{role}/{file}: no task {name!r}")


def expression(text: str) -> str:
    """The Jinja expression inside ``{{ ... }}``."""
    text = text.strip()
    assert text.startswith("{{") and text.endswith("}}"), text
    return text[2:-2]


def test_the_signal_file_names_the_key_only_when_given() -> None:
    content = task("bootstrap_signal", "Write the bootstrap signal file")["ansible.builtin.copy"][
        "content"
    ]
    base = {"bootstrap_signal_code": "76", "bootstrap_signal_message": "Reboot."}
    plain = evaluate(expression(content), {**base, "bootstrap_signal_mok_key": ""})
    assert json.loads(plain) == {"code": 76, "message": "Reboot."}
    key = "/var/lib/shim-signed/mok/MOK.der"
    named = evaluate(expression(content), {**base, "bootstrap_signal_mok_key": key})
    assert json.loads(named) == {"code": 76, "message": "Reboot.", "mok_key": key}
    assert content.endswith("\n")


def test_the_first_existing_signing_key_is_picked() -> None:
    pick = task("provider_baremetal", "Pick the module signing key", "nvidia.yml")
    text = pick["ansible.builtin.set_fact"]["provider_baremetal_mok_key"]
    results = [
        {"item": "/a", "stat": {"exists": False}},
        {"item": "/b", "stat": {"exists": True}},
        {"item": "/c", "stat": {"exists": True}},
    ]
    found = {"provider_baremetal_mok_key_stat": {"results": results}}
    assert evaluate(expression(text), found) == "/b"
    skipped = {"provider_baremetal_mok_key_stat": {"results": [{"item": "/a", "skipped": True}]}}
    assert evaluate(expression(text), skipped) == ""
    assert evaluate(expression(text), {"provider_baremetal_mok_key_stat": {"skipped": True}}) == ""


def test_the_import_is_asked_for_only_while_the_key_is_not_enrolled() -> None:
    name = "Ask bootstrap to import the module signing key and reboot"
    ask = task("provider_baremetal", name, "nvidia.yml")
    assert ask["vars"]["bootstrap_signal_code"] == 76
    conditions = ask["when"]

    def asks(key: str, stdout: str) -> bool:
        variables = {
            "provider_baremetal_mok_key": key,
            "provider_baremetal_mok_test": {"stdout": stdout},
        }
        return all(evaluate(c, variables) for c in conditions)

    key = "/var/lib/shim-signed/mok/MOK.der"
    assert asks(key, f"{key} is not enrolled")
    assert not asks(key, f"{key} is already enrolled")
    assert not asks("", "")


def gate_argv(profile: dict[str, Any], mode: str) -> list[str]:
    run = task("preflight_gate", "Run preflight without storing anything")
    variables = {
        "preflight_gate_command": "/srv/checkout/preflight/pinka-preflight",
        "preflight_gate_tmp": {"path": "/run/pinka/fresh-profile.json"},
        "pinka_root": "/srv/pinka",
        "pinka_bootstrap": {},
        "install_nvidia_driver": mode,
        "host_profile": profile,
        "pinka_preflight_args": ["--port=grafana=18750", "--vm-collector-address", "198.18.0.1"],
    }
    argv: list[str] = evaluate(expression(run["ansible.builtin.command"]["argv"]), variables)
    return argv


def test_the_gate_runs_preflight_with_bootstraps_choices() -> None:
    consent = gate_argv(fixture_profile(CONSENT), "auto")
    assert consent[consent.index("--nvidia-driver") + 1] == "auto"
    assert "--accept-mok-enrollment" in consent
    plain = gate_argv(fixture_profile("ubuntu2404-baremetal-no-gpu"), "manual")
    assert plain[plain.index("--nvidia-driver") + 1] == "manual"
    assert "--accept-mok-enrollment" not in plain
    # The machine's port overrides and VM collector address, as bootstrap passes them.
    assert plain[-3:] == ["--port=grafana=18750", "--vm-collector-address", "198.18.0.1"]


def test_the_gate_stops_when_the_driver_setting_differs() -> None:
    stop = task("preflight_gate", "Stop when preflight judged the GPU with another driver setting")
    assert stop["vars"]["bootstrap_signal_code"] == 77
    profile = fixture_profile(CONSENT)  # preflight ran with --nvidia-driver auto
    assert evaluate(stop["when"], {"install_nvidia_driver": "manual", "host_profile": profile})
    assert not evaluate(stop["when"], {"install_nvidia_driver": "auto", "host_profile": profile})
    del profile["gpu"]["install_driver"]  # a profile from before the setting was recorded
    assert not evaluate(stop["when"], {"install_nvidia_driver": "skip", "host_profile": profile})
