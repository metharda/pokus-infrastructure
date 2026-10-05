"""Role structure and conventions (BUILD_PROMPT §7, §14, §19.1).

Every role has a README, documented defaults, meta and argument specs; every
managed file carries the managed header with its own path; family-specific
files exist for all four families; every dynamically included file, role and
notified handler exists.
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from ansible_testlib import ANSIBLE, FAMILIES, HEADER, INFRA, ROLES, load_yaml, role_names

ROLE_NAMES = role_names()
REQUIRED_FILES = (
    "README.md",
    "defaults/main.yml",
    "meta/main.yml",
    "meta/argument_specs.yml",
    "tasks/main.yml",
)
# Roles that are only included by other roles, never applied by site.yml.
HELPER_ROLES = {"pkg", "bootstrap_signal"}
HOLD_METHODS = sorted(
    {
        load_yaml(ROLES / "platform_facts" / "vars" / f"{f}.yml")["platform_facts_family"][
            "version_hold_method"
        ]
        for f in FAMILIES
    }
)
# Possible values of the variables used in dynamic include names.
VALUES = {
    "pokus_family": FAMILIES,
    "host_profile.family": FAMILIES,
    "pokus_pkg_manager": ("apt", "dnf", "zypper", "pacman"),
    "pokus_version_hold_method": HOLD_METHODS,
    "pokus_firewall_backend": ("nftables", "firewalld", "ufw"),
    "pokus_provider": ("baremetal", "wsl2", "vm"),
    "pkg_repo.type": ("apt", "rpm"),
}
EXPRESSION_RE = re.compile(r"\{\{\s*([\w.]+)\s*\}\}")
INCLUDE_TASKS = ("ansible.builtin.include_tasks", "ansible.builtin.import_tasks")
INCLUDE_ROLE = ("ansible.builtin.include_role", "ansible.builtin.import_role")


def managed_files() -> list[Path]:
    files = [ANSIBLE / "site.yml", ANSIBLE / "restore.yml", ANSIBLE / "component-sources.yml"]
    files += sorted((ANSIBLE / "group_vars").rglob("*.yml*"))
    files += sorted((ANSIBLE / "inventory").rglob("*.yml"))
    for role in ROLE_NAMES:
        files += sorted(
            p
            for p in (ROLES / role).rglob("*")
            if p.is_file() and p.suffix not in (".md", ".pyc") and "__pycache__" not in p.parts
        )
    return files


def expand(name: str) -> list[str]:
    """Expand a templated file or role name over the possible values of its variables."""
    matches = list(EXPRESSION_RE.finditer(name))
    for match in matches:
        assert match.group(1) in VALUES, f"no value list for {match.group(1)!r} in {name!r}"
    names = []
    for combination in itertools.product(*(VALUES[m.group(1)] for m in matches)):
        result = name
        for match, value in zip(matches, combination, strict=True):
            result = result.replace(match.group(0), value, 1)
        names.append(result)
    return names


def walk_tasks(tasks: list[dict[str, Any]] | None) -> Iterator[dict[str, Any]]:
    for task in tasks or []:
        yield task
        for section in ("block", "rescue", "always"):
            yield from walk_tasks(task.get(section))


def task_files(role: str) -> list[Path]:
    return sorted((ROLES / role / "tasks").glob("*.yml"))


def all_tasks() -> Iterator[tuple[str, Path, dict[str, Any]]]:
    for role in ROLE_NAMES:
        for path in task_files(role):
            for task in walk_tasks(load_yaml(path)):
                yield role, path, task
    for play in load_yaml(ANSIBLE / "site.yml"):
        for section in ("pre_tasks", "tasks", "post_tasks"):
            for task in walk_tasks(play.get(section)):
                yield "", ANSIBLE / "site.yml", task


@pytest.mark.parametrize("role", ROLE_NAMES)
def test_role_has_required_files(role: str) -> None:
    missing = [f for f in REQUIRED_FILES if not (ROLES / role / f).is_file()]
    assert missing == []


@pytest.mark.parametrize("path", managed_files(), ids=lambda p: p.relative_to(ANSIBLE).as_posix())
def test_managed_header(path: Path) -> None:
    relative = path.relative_to(INFRA).as_posix()
    head = path.read_text(encoding="utf-8").splitlines()[:3]
    assert any(HEADER.format(path=relative) in line for line in head), relative


def argument_specs(role: str) -> dict[str, Any]:
    specs: dict[str, Any] = load_yaml(ROLES / role / "meta" / "argument_specs.yml")[
        "argument_specs"
    ]
    return specs


@pytest.mark.parametrize("role", ROLE_NAMES)
def test_defaults_are_prefixed_and_documented(role: str) -> None:
    defaults = load_yaml(ROLES / role / "defaults" / "main.yml") or {}
    specs = argument_specs(role)
    options = {name for entry in specs.values() for name in (entry.get("options") or {})}
    assert [k for k in defaults if not k.startswith(f"{role}_")] == []
    assert sorted(set(defaults) - options) == []
    for entry_point, entry in specs.items():
        assert entry.get("short_description"), (role, entry_point)
        for name, option in (entry.get("options") or {}).items():
            assert option.get("description"), (role, entry_point, name)
            assert "type" in option, (role, entry_point, name)


@pytest.mark.parametrize("role", ROLE_NAMES)
def test_entry_points_exist(role: str) -> None:
    for entry_point in argument_specs(role):
        assert (ROLES / role / "tasks" / f"{entry_point}.yml").is_file(), (role, entry_point)


@pytest.mark.parametrize("role", ROLE_NAMES)
def test_meta(role: str) -> None:
    meta = load_yaml(ROLES / role / "meta" / "main.yml")
    assert meta["galaxy_info"]["role_name"] == role
    assert meta["galaxy_info"]["license"] == "Apache-2.0"


@pytest.mark.parametrize("role", ROLE_NAMES)
def test_family_files_are_complete(role: str) -> None:
    vars_dir = ROLES / role / "vars"
    family_vars = sorted(p.stem for p in vars_dir.glob("*.yml") if p.stem in FAMILIES)
    if family_vars:
        assert family_vars == sorted(FAMILIES)
        documents = [load_yaml(vars_dir / f"{f}.yml") for f in FAMILIES]
        assert all(set(d) == set(documents[0]) for d in documents), (
            "family vars define different variables"
        )
    for path in task_files(role):
        stem, _, family = path.stem.rpartition("_")
        if family in FAMILIES:
            assert all((path.parent / f"{stem}_{f}.yml").is_file() for f in FAMILIES), path.name


def test_dynamic_includes_exist() -> None:
    checked = 0
    for role, path, task in all_tasks():
        for module in INCLUDE_TASKS:
            if module in task:
                value = task[module]
                name = value if isinstance(value, str) else value["file"]
                for candidate in expand(name):
                    assert (path.parent / candidate).is_file(), (path.name, candidate)
                    checked += 1
        for module in INCLUDE_ROLE:
            if module in task:
                for candidate in expand(task[module]["name"]):
                    tasks_from = task[module].get("tasks_from", "main")
                    assert (ROLES / candidate / "tasks" / f"{tasks_from}.yml").is_file(), (
                        role,
                        candidate,
                    )
                    checked += 1
        if "ansible.builtin.include_vars" in task and role:
            name = task["ansible.builtin.include_vars"]["file"]
            if "playbook_dir" not in name:
                for candidate in expand(name):
                    assert (ROLES / role / "vars" / candidate).is_file(), (role, candidate)
                    checked += 1
    assert checked > 0


def handler_names(role: str) -> set[str]:
    path = ROLES / role / "handlers" / "main.yml"
    names: set[str] = set()
    for handler in load_yaml(path) if path.is_file() else []:
        names.add(handler["name"])
        listen = handler.get("listen", [])
        names.update([listen] if isinstance(listen, str) else listen)
    return names


def test_notified_handlers_exist() -> None:
    for role, path, task in all_tasks():
        notify = task.get("notify", [])
        for name in [notify] if isinstance(notify, str) else notify:
            assert name in handler_names(role), (path.name, name)


def test_ignore_errors_is_justified() -> None:
    for role in ROLE_NAMES:
        for path in task_files(role):
            lines = path.read_text(encoding="utf-8").splitlines()
            for number, line in enumerate(lines):
                if re.search(r"\bignore_errors:\s*true", line):
                    previous = lines[number - 1] if number else ""
                    assert "#" in line or previous.strip().startswith("#"), f"{path}:{number + 1}"


def test_site_applies_every_role_tagged_with_its_name() -> None:
    applied: dict[str, list[str]] = {}
    for _, path, task in all_tasks():
        if path.name != "site.yml":
            continue
        for module in INCLUDE_ROLE:
            if module in task:
                for name in expand(task[module]["name"]):
                    applied.setdefault(name, []).extend(task.get("tags", []))
    assert sorted(set(ROLE_NAMES) - HELPER_ROLES - set(applied)) == []
    for name, tags in applied.items():
        assert name in tags, name
