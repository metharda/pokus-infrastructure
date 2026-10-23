"""Secure Boot MOK flow and the GPU guard of the Ansible side (approved deviation I1, §2.2).

The expressions are taken from the task files themselves and evaluated with
Ansible's templating, so the tests follow the code: the signal file names the
key bootstrap imports, the module signing key is picked from the candidates
that exist, preflight_gate runs preflight with bootstrap's choices, and a
driver is installed only for a GPU the pinned driver's open kernel module
supports.
"""

from __future__ import annotations

import json
from typing import Any

from ansible_testlib import ANSIBLE, ROLES, evaluate, fixture_profile, load_yaml

CONSENT = "ubuntu2404-baremetal-nvidia-no-driver-secureboot-consent"
UBUNTU_SOURCE = {"packages": ["nvidia-driver-580-open"], "source": "distribution"}
UNVERIFIED_SOURCE = {
    "packages": ["nvidia-open"],
    "source": "vendor",
    "verify": "TODO(verify): the repository",
}


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


def driver_variables(
    fixture: str, release_key: str | None, mode: str = "auto", source: Any = None
) -> dict[str, Any]:
    """The variables of the NVIDIA tasks for a fixture's host profile."""
    versions = load_yaml(ANSIBLE / "group_vars" / "all" / "versions.yml")["pinka_versions"]
    variables: dict[str, Any] = {
        "host_profile": fixture_profile(fixture),
        "pinka_versions": versions,
        "pinka_release_key": release_key,
        "install_nvidia_driver": mode,
        "pinka_community_repos_allowed": False,
        "provider_baremetal_driver": UBUNTU_SOURCE if source is None else source,
    }
    facts = task("provider_baremetal", "Look up the NVIDIA driver source", "nvidia.yml")
    for name in ("provider_baremetal_driver_preexisting", "provider_baremetal_gpu_supported"):
        text = facts["ansible.builtin.set_fact"][name]
        variables[name] = evaluate(expression(text), variables)
    return variables


def runs(name: str, variables: dict[str, Any]) -> bool:
    """Whether every condition of an NVIDIA task holds."""
    conditions = task("provider_baremetal", name, "nvidia.yml")["when"]
    return all(evaluate(c, variables) for c in conditions)


INSTALL = "Install the NVIDIA driver"
STOP = "Stop when the driver must be installed by hand"
UNSUPPORTED = "Install no driver for a GPU the pinned driver does not support"
MOK = "Check the Machine Owner Key enrollment"


def test_a_driver_is_installed_only_for_a_gpu_the_open_module_supports() -> None:
    for fixture, supported in (
        ("ubuntu2404-baremetal-nvidia-kepler", False),  # Kepler 10de:1287: legacy branch 470
        ("ubuntu2404-baremetal-nvidia-pascal", False),  # Pascal 10de:1d01: proprietary module only
        ("ubuntu2404-baremetal-nvidia-turing", True),  # RTX 2080 Ti
        (CONSENT, True),  # RTX 4070
    ):
        variables = driver_variables(fixture, "ubuntu-24.04")
        assert variables["provider_baremetal_gpu_supported"] is supported, fixture
        assert runs(INSTALL, variables) is supported, fixture
        assert runs(UNSUPPORTED, variables) is not supported, fixture
        # auto with a verified source never stops for a manual install.
        assert not runs(STOP, variables), fixture


def test_an_unsupported_gpu_needs_nothing_by_hand_on_any_family() -> None:
    # Without a verified pin the play stops for a manual install, but only for
    # a GPU a driver would be installed for (the rolling families included).
    for release_key in ("debian-13", "fedora-44", None):
        kepler = driver_variables(
            "ubuntu2404-baremetal-nvidia-kepler", release_key, source=UNVERIFIED_SOURCE
        )
        assert not runs(STOP, kepler) and runs(UNSUPPORTED, kepler), release_key
        assert not runs(INSTALL, kepler), release_key
        turing = driver_variables(
            "ubuntu2404-baremetal-nvidia-turing", release_key, source=UNVERIFIED_SOURCE
        )
        assert runs(STOP, turing) and not runs(UNSUPPORTED, turing), release_key
    # manual is the operator's choice: the play stops and says why the GPU may stay unused.
    manual = driver_variables("ubuntu2404-baremetal-nvidia-pascal", "ubuntu-24.04", "manual")
    assert runs(STOP, manual) and not runs(UNSUPPORTED, manual)
    message = task("provider_baremetal", STOP, "nvidia.yml")["vars"]["bootstrap_signal_message"]
    assert "compute capability is 7.5" in evaluate(
        expression(message[message.index("{{ '' if") :]), manual
    )


def test_no_machine_owner_key_for_a_gpu_without_a_driver() -> None:
    kepler = driver_variables("ubuntu2404-baremetal-nvidia-kepler-secureboot", "ubuntu-24.04")
    assert not runs(MOK, kepler)
    assert runs(MOK, driver_variables(CONSENT, "ubuntu-24.04"))
