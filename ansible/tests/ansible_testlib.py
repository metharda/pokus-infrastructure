"""Shared helpers for the Ansible unit tests.

The tests never run a play: they check the data files, call the filter
plugin's functions directly, and render role templates the way
``ansible.builtin.template`` does (trusted template text, backslashes kept,
``trim_blocks``), with variables loaded the way Ansible loads vars files.
Host profiles come from the preflight fixtures (BUILD_PROMPT §11).
"""

from __future__ import annotations

import contextlib
import copy
import functools
import io
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

INFRA = Path(__file__).resolve().parents[2]
ANSIBLE = INFRA / "ansible"
ROLES = ANSIBLE / "roles"
GROUP_VARS = ANSIBLE / "group_vars" / "all" / "main.yml"
FIXTURES = INFRA / "preflight" / "tests" / "fixtures"
FAMILIES = ("debian", "redhat", "suse", "archlinux")
HEADER = "Managed by pinka ({path}). Local changes will be overwritten."

# Ansible reads its configuration when first imported; keep its state below
# build/ like the Makefile does, not in the home directory.
os.environ.setdefault("ANSIBLE_HOME", str(INFRA / "build" / "ansible-home"))


def load_yaml(path: Path) -> Any:
    """Load a YAML file with PyYAML (plain data, no Ansible trust tags).

    Args:
        path: The file.

    Returns:
        The parsed document.
    """
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def role_names() -> list[str]:
    """List the roles.

    Returns:
        Role directory names, sorted.
    """
    return sorted(p.name for p in ROLES.iterdir() if p.is_dir())


def debian_codenames() -> dict[str, Any]:
    """Return the Debian-family codename table of platform_facts.

    Returns:
        ``{codename: {distro, version}}``.
    """
    data = load_yaml(ROLES / "platform_facts" / "vars" / "debian.yml")
    codenames: dict[str, Any] = data["platform_facts_family"]["codenames"]
    return codenames


@functools.cache
def _fixture_profile(name: str) -> str:
    from pinka_preflight import cli  # noqa: PLC0415 - preflight is only needed here

    args_file = FIXTURES / name / "args.json"
    args = json.loads(args_file.read_text(encoding="utf-8")) if args_file.exists() else []
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        cli.main(["--root", str(FIXTURES / name), "--offline", "--quiet", *args])
    return out.getvalue()


def fixture_profile(name: str) -> dict[str, Any]:
    """Run preflight on a fixture root (with its ``args.json``) and return the host profile.

    Args:
        name: Fixture directory name under preflight/tests/fixtures.

    Returns:
        A fresh copy of the host profile.
    """
    profile: dict[str, Any] = json.loads(_fixture_profile(name))
    return copy.deepcopy(profile)


def fixture_names() -> list[str]:
    """List the preflight fixtures.

    Returns:
        Fixture names, sorted.
    """
    return sorted(p.name for p in FIXTURES.iterdir() if p.is_dir())


def ansible_vars(*paths: Path, **extra: Any) -> dict[str, Any]:
    """Load vars files the way Ansible does (strings trusted as templates).

    Args:
        *paths: Vars files, later files win.
        **extra: Variables set last (like ``-e``).

    Returns:
        The merged variables.
    """
    from ansible.parsing.dataloader import DataLoader  # noqa: PLC0415 - heavy import

    loader = DataLoader()
    merged: dict[str, Any] = {}
    for path in paths:
        merged.update(loader.load_from_file(str(path), trusted_as_template=True) or {})
    merged.update(extra)
    return merged


def role_vars(role: str, family: str | None = None, **extra: Any) -> dict[str, Any]:
    """Variables a role sees: group_vars, role defaults, family vars, extras.

    Args:
        role: Role name.
        family: Family whose ``vars/<family>.yml`` is loaded, if the role has one.
        **extra: Facts and overrides set last.

    Returns:
        The merged variables.
    """
    paths = [GROUP_VARS, ROLES / role / "defaults" / "main.yml"]
    if family and (ROLES / role / "vars" / f"{family}.yml").is_file():
        paths.append(ROLES / role / "vars" / f"{family}.yml")
    return ansible_vars(*paths, **extra)


@functools.cache
def init_plugins() -> None:
    """Set up Ansible's plugin loader like the CLI does (ansible.builtin lookups, our filters)."""
    from ansible.plugins.loader import filter_loader, init_plugin_loader  # noqa: PLC0415

    init_plugin_loader([str(INFRA / ".ansible-collections")])
    filter_loader.add_directory(str(ANSIBLE / "filter_plugins"))


def _templar(variables: Mapping[str, Any]) -> Any:
    from ansible.parsing.dataloader import DataLoader  # noqa: PLC0415 - heavy import
    from ansible.template import Templar  # noqa: PLC0415

    init_plugins()
    return Templar(loader=DataLoader(), variables=dict(variables))


def render_template(role: str, name: str, variables: Mapping[str, Any]) -> str:
    """Render a role template like ``ansible.builtin.template``.

    Args:
        role: Role name.
        name: File name under the role's templates/ directory.
        variables: Template variables.

    Returns:
        The rendered text.
    """
    return render_file(ROLES / role / "templates" / name, variables)


def render_file(path: Path, variables: Mapping[str, Any]) -> str:
    """Render any template file like ``ansible.builtin.template``.

    Args:
        path: Template file.
        variables: Template variables.

    Returns:
        The rendered text.
    """
    from ansible.template import trust_as_template  # noqa: PLC0415 - heavy import

    result = _templar(variables).template(
        trust_as_template(path.read_text(encoding="utf-8")),
        escape_backslashes=False,
        overrides={"trim_blocks": True, "lstrip_blocks": False, "newline_sequence": "\n"},
    )
    return str(result)


def evaluate(expression: str, variables: Mapping[str, Any]) -> Any:
    """Evaluate a Jinja expression with Ansible's templating.

    Args:
        expression: Expression without the braces.
        variables: Variables.

    Returns:
        The native result.
    """
    from ansible.template import trust_as_template  # noqa: PLC0415 - heavy import

    return _templar(variables).template(trust_as_template("{{ " + expression + " }}"))
