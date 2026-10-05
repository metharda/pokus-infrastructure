"""Files generated for bootstrap (BUILD_PROMPT §5.2, §10.1) and its reference pages.

``versions.lock.yml`` is the single source of truth. From it come
``bootstrap/lib/pins.sh`` (the uv and CPython pins bootstrap reads before any
Python exists), ``bootstrap/requirements.in`` (compiled to the hash-locked
``requirements.lock`` by ``make bootstrap-lock``) and
``ansible/group_vars/all/versions.yml``. The CLI reference is rendered from the
usage text in ``bootstrap/lib/args.sh``, the exit codes from
``bootstrap/lib/exitcodes.sh``.
"""

from __future__ import annotations

import hashlib
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from pokus_infra_gen.sources import jsonable, load_yaml

LOCK = "versions.lock.yml"
UV_ARCHES = ("x86_64", "aarch64")
EXIT_LINE = re.compile(r"^readonly (EXIT_[A-Z_]+)=(\d+) +# ([^:]+): (.+)$")
OPTION_LINE = re.compile(r"^  (-\S.*?)\s{2,}(\S.*)$")
OPTION_NAME = re.compile(r"--?[a-z][a-z-]*")
OPTION_WITH_SEPARATOR = re.compile(r"--?[a-z][a-z-]*(,|\s*\|)?")
REQUIREMENT = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==(\S+)$")


@dataclass(frozen=True)
class ExitCode:
    """One bootstrap exit code.

    Attributes:
        name: Constant name in ``exitcodes.sh``.
        code: Numeric value.
        title: Short title.
        meaning: What it means and what to do.
    """

    name: str
    code: int
    title: str
    meaning: str


@dataclass(frozen=True)
class Option:
    """One option of ``bootstrap.sh``.

    Attributes:
        names: Option strings, such as ``("-h", "--help")``.
        value: Value placeholder, empty for flags.
        description: Help text.
    """

    names: tuple[str, ...]
    value: str
    description: str


def lock_sha256(root: Path) -> str:
    """Return the sha256 of ``versions.lock.yml``.

    Args:
        root: Component root.

    Returns:
        Hex digest.
    """
    return hashlib.sha256((root / LOCK).read_bytes()).hexdigest()


def _lock(root: Path) -> dict[str, Any]:
    data = jsonable(load_yaml(root / LOCK))
    if not isinstance(data, dict):
        raise TypeError(f"{LOCK} must be a mapping")
    return data


def pins_sh(root: Path) -> str:
    """Render ``bootstrap/lib/pins.sh``.

    Args:
        root: Component root.

    Returns:
        Shell text.
    """
    lock = _lock(root)
    uv = lock["binaries"]["uv"]
    values = {
        "POKUS_LOCK_SHA256": lock_sha256(root),
        "POKUS_UV_VERSION": uv["version"],
    }
    for arch in UV_ARCHES:
        asset = uv["assets"][arch]
        values[f"POKUS_UV_URL_{arch.upper()}"] = asset["url"]
        values[f"POKUS_UV_SHA256_{arch.upper()}"] = asset["sha256"]
    values["POKUS_PYTHON_VERSION"] = lock["python"]["python_version"]
    lines = [
        "# shellcheck shell=bash",
        "# Managed by pokus-infra (bootstrap/lib/pins.sh). Local changes will be overwritten.",
        "# Generated from versions.lock.yml by tools/gen (make generate); do not edit.",
        "# shellcheck disable=SC2034 # read by tooling.sh",
        "",
    ]
    lines += [f"readonly {name}={shlex.quote(str(value))}" for name, value in values.items()]
    return "\n".join(lines) + "\n"


def requirements_in(root: Path) -> str:
    """Render ``bootstrap/requirements.in``.

    Args:
        root: Component root.

    Returns:
        Requirements text.
    """
    packages = _lock(root)["python"]["packages"]
    lines = [
        "# Managed by pokus-infra (bootstrap/requirements.in). Local changes will be overwritten.",
        "# Generated from versions.lock.yml (python.packages) by tools/gen (make generate).",
        "# Python packages of /opt/pokus/ansible-venv (BUILD_PROMPT §5.2 step 5);",
        "# `make bootstrap-lock` compiles the hash-locked requirements.lock from it.",
    ]
    lines += [f"{name}=={packages[name]}" for name in sorted(packages)]
    return "\n".join(lines) + "\n"


class _IndentedDumper(yaml.SafeDumper):
    """Indent block sequences under their key (yamllint ``indent-sequences``)."""

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        super().increase_indent(flow, False)


def versions_yml(root: Path) -> str:
    """Render ``ansible/group_vars/all/versions.yml``.

    Args:
        root: Component root.

    Returns:
        YAML text.
    """
    body = yaml.dump(
        {"pokus_versions": _lock(root)},
        Dumper=_IndentedDumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=100,
    )
    return (
        "# Managed by pokus-infra (ansible/group_vars/all/versions.yml). "
        "Local changes will be overwritten.\n"
        "# Generated from versions.lock.yml by tools/gen (make generate); do not edit.\n"
        "# bootstrap.sh stops when pokus_versions_lock_sha256 is not the sha256 of\n"
        "# versions.lock.yml (BUILD_PROMPT §5.2 step 7).\n"
        f'pokus_versions_lock_sha256: "{lock_sha256(root)}"\n'
        f"{body}"
    )


def requirements_lock_problems(root: Path) -> list[str]:
    """Check that ``requirements.lock`` pins exactly the versions of ``requirements.in``.

    Args:
        root: Component root.

    Returns:
        One message per missing or different pin.
    """
    lock_text = (root / "bootstrap/requirements.lock").read_text(encoding="utf-8")
    requirements = (line.split(" ;")[0].rstrip(" \\") for line in lock_text.splitlines())
    pinned = {m.group(1).lower(): m.group(2) for m in map(REQUIREMENT.match, requirements) if m}
    problems = []
    for line in requirements_in(root).splitlines():
        m = REQUIREMENT.match(line)
        if m and pinned.get(m.group(1).lower()) != m.group(2):
            problems.append(
                f"bootstrap/requirements.lock: {m.group(1)} is "
                f"{pinned.get(m.group(1).lower(), 'missing')}, requirements.in pins {m.group(2)}; "
                "run 'make bootstrap-lock'"
            )
    return problems


def exit_codes(root: Path) -> list[ExitCode]:
    """Parse ``bootstrap/lib/exitcodes.sh``.

    Args:
        root: Component root.

    Returns:
        Exit codes in file order.

    Raises:
        ValueError: If the file defines no exit codes.
    """
    text = (root / "bootstrap/lib/exitcodes.sh").read_text(encoding="utf-8")
    codes = [
        ExitCode(m.group(1), int(m.group(2)), m.group(3), m.group(4))
        for m in (EXIT_LINE.match(line) for line in text.splitlines())
        if m
    ]
    if not codes:
        raise ValueError("bootstrap/lib/exitcodes.sh defines no exit codes")
    return codes


def usage_text(root: Path) -> str:
    """Return the usage text of ``bootstrap.sh`` (the here-document in ``args.sh``).

    Args:
        root: Component root.

    Returns:
        Usage text.

    Raises:
        ValueError: If the here-document is missing.
    """
    text = (root / "bootstrap/lib/args.sh").read_text(encoding="utf-8")
    m = re.search(r"cat <<'EOF'\n(.*?)\nEOF\n", text, re.DOTALL)
    if not m:
        raise ValueError("bootstrap/lib/args.sh has no usage here-document")
    return m.group(1)


def options(root: Path) -> list[Option]:
    """Parse the options of ``bootstrap.sh`` from its usage text.

    Args:
        root: Component root.

    Returns:
        Options in usage order.
    """
    found = []
    for line in usage_text(root).splitlines():
        m = OPTION_LINE.match(line)
        if not m:
            continue
        spec, description = m.group(1), m.group(2)
        names = tuple(OPTION_NAME.findall(spec))
        value = OPTION_WITH_SEPARATOR.sub("", spec).strip()
        found.append(Option(names, value, description))
    return found
