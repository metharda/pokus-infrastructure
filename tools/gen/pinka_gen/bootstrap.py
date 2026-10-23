"""Files generated for bootstrap (BUILD_PROMPT §5.2, §10.1) and its reference pages.

``versions.lock.yml`` is the single source of truth. From it come
``bootstrap/lib/pins.sh`` (the uv and CPython pins bootstrap reads before any
Python exists), ``bootstrap/requirements.in`` (compiled to the hash-locked
``requirements.lock`` by ``make bootstrap-lock``), the models role's
``requirements.in`` (``make models-lock``), the installer's
``requirements.in`` (``make installer-lock``) and
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

from pinka_gen.sources import jsonable, load_yaml

LOCK = "versions.lock.yml"
UV_ARCHES = ("x86_64", "aarch64")
EXIT_LINE = re.compile(r"^readonly (EXIT_[A-Z_]+)=(\d+) +# ([^:]+): (.+)$")
OPTION_LINE = re.compile(r"^  (-\S.*?)\s{2,}(\S.*)$")
OPTION_NAME = re.compile(r"--?[a-z][a-z-]*")
OPTION_WITH_SEPARATOR = re.compile(r"--?[a-z][a-z-]*(,|\s*\|)?")
REQUIREMENT = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==(\S+)$")


@dataclass(frozen=True)
class RequirementSet:
    """A hash-locked Python environment pinned in ``versions.lock.yml``.

    Attributes:
        keys: Keys under ``python`` in the lock whose packages the environment holds.
        source: ``requirements.in`` path, relative to the component root.
        lock: Hash-locked ``requirements.lock`` path.
        purpose: What the environment is for (comment line).
        target: Make target that compiles the lock.
    """

    keys: tuple[str, ...]
    source: str
    lock: str
    purpose: str
    target: str


BOOTSTRAP_REQUIREMENTS = RequirementSet(
    ("packages",),
    "bootstrap/requirements.in",
    "bootstrap/requirements.lock",
    "Python packages of /opt/pinka/ansible-venv (BUILD_PROMPT §5.2 step 5);",
    "bootstrap-lock",
)
MODEL_REQUIREMENTS = RequirementSet(
    ("model_tools",),
    "ansible/roles/models/files/requirements.in",
    "ansible/roles/models/files/requirements.lock",
    "Python packages of /opt/pinka/models-venv, the models role's download tool "
    "(BUILD_PROMPT §7.12);",
    "models-lock",
)
INSTALLER_REQUIREMENTS = RequirementSet(
    ("installer", "packages"),
    "installer/requirements.in",
    "installer/requirements.lock",
    "Python packages of /opt/pinka/installer-venv, the installer TUI (BUILD_PROMPT §18.2),",
    "installer-lock",
)


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
        "PINKA_LOCK_SHA256": lock_sha256(root),
        "PINKA_UV_VERSION": uv["version"],
    }
    for arch in UV_ARCHES:
        asset = uv["assets"][arch]
        values[f"PINKA_UV_URL_{arch.upper()}"] = asset["url"]
        values[f"PINKA_UV_SHA256_{arch.upper()}"] = asset["sha256"]
    values["PINKA_PYTHON_VERSION"] = lock["python"]["python_version"]
    lines = [
        "# shellcheck shell=bash",
        "# Managed by pinka (bootstrap/lib/pins.sh). Local changes will be overwritten.",
        "# Generated from versions.lock.yml by tools/gen (make generate); do not edit.",
        "# shellcheck disable=SC2034 # read by tooling.sh",
        "",
    ]
    lines += [f"readonly {name}={shlex.quote(str(value))}" for name, value in values.items()]
    # Every collection bootstrap installs: "<name> <version> <url> <sha256>".
    section = lock["ansible_collections"]
    artifacts = section["artifacts"]
    pins = [
        " ".join([name, str(version), artifacts[name]["url"], artifacts[name]["sha256"]])
        for name, version in sorted(section["collections"].items())
    ]
    lines.append("readonly -a PINKA_COLLECTIONS=(")
    lines += [f"  {shlex.quote(pin)}" for pin in pins]
    lines.append(")")
    return "\n".join(lines) + "\n"


def requirements_in(root: Path, spec: RequirementSet = BOOTSTRAP_REQUIREMENTS) -> str:
    """Render a ``requirements.in`` from the lock.

    Args:
        root: Component root.
        spec: The environment (the bootstrap's Ansible environment by default).

    Returns:
        Requirements text.
    """
    python = _lock(root)["python"]
    packages = {name: v for key in spec.keys for name, v in python[key].items()}
    groups = ", ".join(f"python.{key}" for key in spec.keys)
    lines = [
        f"# Managed by pinka ({spec.source}). Local changes will be overwritten.",
        f"# Generated from versions.lock.yml ({groups}) by tools/gen (make generate).",
        f"# {spec.purpose}",
        f"# `make {spec.target}` compiles the hash-locked requirements.lock from it.",
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
        {"pinka_versions": _lock(root)},
        Dumper=_IndentedDumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=100,
    )
    return (
        "# Managed by pinka (ansible/group_vars/all/versions.yml). "
        "Local changes will be overwritten.\n"
        "# Generated from versions.lock.yml by tools/gen (make generate); do not edit.\n"
        "# bootstrap.sh stops when pinka_versions_lock_sha256 is not the sha256 of\n"
        "# versions.lock.yml (BUILD_PROMPT §5.2 step 7).\n"
        f'pinka_versions_lock_sha256: "{lock_sha256(root)}"\n'
        f"{body}"
    )


def requirements_lock_problems(
    root: Path, spec: RequirementSet = BOOTSTRAP_REQUIREMENTS
) -> list[str]:
    """Check that a ``requirements.lock`` pins exactly the versions of its ``requirements.in``.

    Args:
        root: Component root.
        spec: The environment (the bootstrap's Ansible environment by default).

    Returns:
        One message per missing or different pin.
    """
    lock_text = (root / spec.lock).read_text(encoding="utf-8")
    requirements = (line.split(" ;")[0].rstrip(" \\") for line in lock_text.splitlines())
    pinned = {_normalize(m.group(1)): m.group(2) for m in map(REQUIREMENT.match, requirements) if m}
    problems = []
    for line in requirements_in(root, spec).splitlines():
        m = REQUIREMENT.match(line)
        if m and pinned.get(_normalize(m.group(1))) != m.group(2):
            problems.append(
                f"{spec.lock}: {m.group(1)} is "
                f"{pinned.get(_normalize(m.group(1)), 'missing')}, requirements.in pins "
                f"{m.group(2)}; run 'make {spec.target}'"
            )
    return problems


def _normalize(name: str) -> str:
    """A package name normalized like pip does (PEP 503): ``ruamel.yaml`` is ``ruamel-yaml``."""
    return re.sub(r"[-_.]+", "-", name).lower()


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
