"""Every conditional of the plays and roles has a boolean result.

ansible-core 2.19 and later refuse a ``when``, ``that``, ``failed_when``,
``changed_when`` or ``until`` whose result is not a boolean, at run time:
``when: some_dict`` fails even where the dict is empty. A value that may be a
mapping, a string or null is tested with ``is truthy`` (or compared). The scan
below finds every bare variable used as a condition (alone, or as an operand
of ``and``/``or``; ``not x`` is a boolean anyway) and requires a known
boolean source for it; the evaluation tests run ansible-core's own check.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from ansible.errors import AnsibleError
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

from ansible_testlib import ANSIBLE, ROLES, load_yaml

KEYS = ("when", "that", "failed_when", "changed_when", "until")
PATH = r"[a-z_][a-z0-9_]*(?:\.[a-z0-9_]+)*"
BARE = re.compile(rf"^\(?\s*({PATH})\s*\)?$")
NEGATED = re.compile(rf"^\(?\s*not\s+\(?\s*{PATH}\s*\)?\s*\)?$")
# Results that are booleans by definition (stat, async_status, registered results).
BOOLEAN_SUFFIXES = (
    ".stat.exists",
    ".stat.isblk",
    ".stat.isdir",
    ".stat.isreg",
    ".stat.islnk",
    ".stat.executable",
    ".finished",
    ".changed",
    ".failed",
    ".skipped",
)
# Booleans that are not role options of type bool, and where each comes from.
BOOLEAN_SOURCES = {
    "ansible_check_mode": "Ansible",
    "host_profile.gpu.present": "the host profile (preflight's schema: boolean)",
    "host_profile.gpu.usable": "the host profile (preflight's schema: boolean)",
    "host_profile.init.systemd": "the host profile (preflight's schema: boolean)",
    "host_profile.overrides.accept_degraded": "the host profile (boolean)",
    "host_profile.overrides.accept_experimental": "the host profile (boolean)",
    "host_profile.overrides.force_unsupported": "the host profile (boolean)",
    "backup_configured": "backup vars: a comparison",
    "backup_sftp": "backup vars: a test (is match)",
    "doctor_serves_inference": "doctor vars: boolean feature and a membership test",
    "lab_session_gpu": "lab_session vars: boolean feature and a membership test",
    "lab_session_window_expiry": "lab_session vars: a comparison and | bool",
    "observability_stores": "observability vars: a membership test",
    "observability_vm_listener": "observability vars: | bool and a boolean",
    "images_upstream_present": "set_fact: a membership test",
    "pinka_community_repos_allowed": "set_fact: | bool or | bool",
    "tailscale_joined": "set_fact: comparisons and a membership test",
    "tailscale_adopted": "set_fact: a comparison",
    "tailscale_package_preexisting": "set_fact: comparisons joined by or",
    "docker_preexisting": "set_fact: a comparison",
    "nvidia_container_toolkit_preexisting": "set_fact: a membership test",
    "provider_baremetal_driver_preexisting": "set_fact: a comparison",
    "provider_baremetal_gpu_supported": "set_fact: the pinka_driver_supports_gpu filter (bool)",
    "platform_switch.release_changed": "pinka-platform deploy switch-plan: JSON boolean",
    "platform_switch.content_changed": "pinka-platform deploy switch-plan: JSON boolean",
}


def conditions(node: Any) -> Iterator[str]:
    """Every condition string below a task file's YAML."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in KEYS:
                for item in value if isinstance(value, list) else [value]:
                    if isinstance(item, str):
                        yield item
            yield from conditions(value)
    elif isinstance(node, list):
        for value in node:
            yield from conditions(value)


def task_files() -> list[Path]:
    """The plays and every role's task and handler files."""
    files = [p for p in sorted(ANSIBLE.glob("*.yml")) if p.name != "requirements.yml"]
    for kind in ("tasks", "handlers"):
        files += sorted(ROLES.glob(f"*/{kind}/*.yml"))
    return files


def bool_options() -> set[str]:
    """Role options declared ``type: bool`` in the argument specs."""
    found: set[str] = set()
    for spec in ROLES.glob("*/meta/argument_specs.yml"):
        for entry in (load_yaml(spec) or {}).get("argument_specs", {}).values():
            for name, option in (entry.get("options") or {}).items():
                if option.get("type") == "bool":
                    found.add(name)
    return found


def bare_operands(expression: str) -> list[str]:
    """The bare variables an expression's result can be (``x``, ``x and y``)."""
    found = []
    for part in re.split(r"\s+(?:and|or)\s+", " ".join(expression.split())):
        if NEGATED.match(part):
            continue
        match = BARE.match(part)
        if match and match.group(1) not in ("true", "false"):
            found.append(match.group(1))
    return found


def is_boolean(name: str, options: set[str]) -> bool:
    return name in options or name in BOOLEAN_SOURCES or name.endswith(BOOLEAN_SUFFIXES)


def test_every_condition_has_a_boolean_result() -> None:
    options = bool_options()
    problems = []
    for path in task_files():
        for expression in conditions(load_yaml(path)):
            for name in bare_operands(expression):
                if not is_boolean(name, options):
                    rel = path.relative_to(ANSIBLE)
                    problems.append(f"{rel}: {expression!r} ({name} may not be a boolean)")
    assert problems == []


def test_the_scan_finds_a_bare_mapping() -> None:
    assert bare_operands("observability_platform_dashboards") == [
        "observability_platform_dashboards"
    ]
    assert bare_operands("a.b and not c") == ["a.b"]
    assert bare_operands("x is truthy") == []
    assert bare_operands("not (y)") == []
    assert not is_boolean("active_platform.database", set())
    assert is_boolean("registered.stat.exists", set())


def evaluate(expression: str, variables: dict[str, Any]) -> bool:
    templar = Templar(loader=DataLoader(), variables=variables)
    return bool(templar.evaluate_conditional(trust_as_template(expression)))


@pytest.mark.parametrize(
    ("active", "expected"),
    [({}, False), ({"dashboards": None}, False), ({"dashboards": {"dir": "/x"}}, True)],
)
def test_the_platform_dashboards_condition_without_and_with_a_platform(
    active: dict[str, Any], expected: bool
) -> None:
    # The task that stopped every extended check-mode run of 0d72e54.
    defaults = load_yaml(ROLES / "observability" / "defaults" / "main.yml")
    task = load_yaml(ROLES / "observability" / "tasks" / "platform_dashboards.yml")
    when = next(t["when"] for t in task if "when" in t)
    variables = {
        "active_platform": active,
        "observability_platform_dashboards": trust_as_template(
            defaults["observability_platform_dashboards"]
        ),
    }
    assert evaluate(when, variables) is expected
    with pytest.raises(AnsibleError, match="Conditionals must have a boolean result"):
        evaluate("observability_platform_dashboards", variables)
