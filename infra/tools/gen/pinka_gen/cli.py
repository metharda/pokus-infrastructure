"""``python -m pinka_gen {generate,check,coverage}``."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

from pinka_gen import (
    ansible_docs,
    bootstrap,
    contract,
    installer,
    lab,
    pages,
    readme,
    sources,
    versions,
)
from pinka_preflight.cli import build_parser as preflight_parser
from pinka_preflight.matrix import EXIT_CODES, OUTCOME_MEANINGS

PREFLIGHT_INTRO = (
    "`pinka-preflight` detects the host and decides whether the lab is supported on it "
    "(see [Support matrix](../../support-matrix.md) and [Host profile schema](../host-profile.md))."
)


def targets(root: Path) -> dict[str, Callable[[], str]]:
    """Map every generated file to the function that produces it.

    Args:
        root: Component root.

    Returns:
        Relative path to producer.
    """
    found: dict[str, Callable[[], str]] = {
        "preflight/support-matrix.json": lambda: sources.support_matrix_json(root),
        "preflight/endpoints.json": lambda: sources.endpoints_json(root),
        "preflight/lab-defaults.json": lambda: sources.lab_defaults_json(root),
        "docs/support-matrix.md": lambda: pages.support_matrix_page(root),
        "docs/reference/cli/pinka-preflight.md": lambda: pages.cli_page(
            pages.relative_defaults(preflight_parser(), root),
            title="pinka-preflight command",
            intro=PREFLIGHT_INTRO,
        ),
        "docs/reference/exit-codes.md": lambda: pages.exit_codes_page(
            OUTCOME_MEANINGS, EXIT_CODES, bootstrap.exit_codes(root), lab.exit_code_rows()
        ),
        lab.CLI_PAGE: lab.cli_page,
        lab.FINGERPRINT_PAGE: lambda: pages.schema_page(
            root,
            lab.FINGERPRINT_SCHEMA,
            "Fingerprint schema",
            "Every field of the environment fingerprint printed by pinka fingerprint.",
        ),
        lab.CHECKS_PAGE: lab.checks_page,
        "docs/reference/host-profile.md": lambda: pages.host_profile_page(root),
        "docs/reference/cli/bootstrap.md": lambda: pages.bootstrap_cli_page(root),
        "bootstrap/lib/pins.sh": lambda: bootstrap.pins_sh(root),
        "bootstrap/requirements.in": lambda: bootstrap.requirements_in(root),
        bootstrap.MODEL_REQUIREMENTS.source: lambda: bootstrap.requirements_in(
            root, bootstrap.MODEL_REQUIREMENTS
        ),
        bootstrap.INSTALLER_REQUIREMENTS.source: lambda: bootstrap.requirements_in(
            root, bootstrap.INSTALLER_REQUIREMENTS
        ),
        "ansible/group_vars/all/versions.yml": lambda: bootstrap.versions_yml(root),
        "ansible/requirements.yml": lambda: versions.requirements_yml(root),
        "docs/reference/versions.md": lambda: versions.versions_page(root),
        "docs/reference/ansible-variables.md": lambda: ansible_docs.ansible_variables_page(root),
        "docs/reference/component-sources.md": lambda: ansible_docs.component_sources_page(root),
        "docs/reference/accounts.md": lambda: ansible_docs.accounts_page(root),
        "docs/architecture.md": lambda: ansible_docs.architecture_page(root),
        readme.README: lambda: readme.readme(root),
        installer.CLI_PAGE: installer.cli_page,
        installer.ANSWERS_PAGE: lambda: installer.answers_page(root),
        contract.RESERVED: lambda: contract.reserved_yml(root),
        contract.SCHEMA: lambda: contract.schema_json(root),
        contract.MANIFEST_PAGE: lambda: contract.manifest_page(root),
        contract.PROFILES_PAGE: lambda: contract.profiles_page(root),
        contract.CLI_PAGE: lambda: contract.cli_page(root),
        contract.RECORDS_PAGE: lambda: contract.records_page(root),
        contract.FILES_PAGE: lambda: contract.files_page(root),
    }
    for name in installer.SCREENSHOTS:
        found[installer.screenshot(name)] = (
            lambda name=name: installer.screenshot_source(root, name)  # type: ignore[misc]
        )
    return found


def generate(root: Path) -> int:
    """Write every generated file.

    Args:
        root: Component root.

    Returns:
        0.
    """
    for rel, produce in targets(root).items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        text = produce()
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")
            print(f"generated {rel}")
    return 0


def check(root: Path) -> int:
    """Fail if a committed generated file differs from a fresh generation (drift check).

    Args:
        root: Component root.

    Returns:
        0 if everything is current, 1 otherwise.
    """
    stale = []
    for rel, produce in targets(root).items():
        path = root / rel
        if not path.exists() or path.read_text(encoding="utf-8") != produce():
            stale.append(rel)
    for rel in stale:
        print(f"{rel}: out of date; run 'make generate' and commit the result")
    lock_problems = bootstrap.requirements_lock_problems(root)
    lock_problems += bootstrap.requirements_lock_problems(root, bootstrap.MODEL_REQUIREMENTS)
    lock_problems += bootstrap.requirements_lock_problems(root, bootstrap.INSTALLER_REQUIREMENTS)
    for problem in lock_problems:
        print(problem)
    print(f"drift check: {len(targets(root))} generated files, {len(stale)} stale", file=sys.stderr)
    return 1 if stale or lock_problems else 0


def reference_entries(root: Path) -> list[tuple[str, str, str]]:
    """List every entry the reference pages must contain.

    Args:
        root: Component root.

    Returns:
        ``(label, page, needle)`` triples: ``needle`` must occur in the page at ``page``.
    """
    preflight_cli = "docs/reference/cli/pinka-preflight.md"
    bootstrap_cli = "docs/reference/cli/bootstrap.md"
    codes = "docs/reference/exit-codes.md"
    entries = [
        (f"pinka-preflight option {opt}", preflight_cli, f"`{opt}`")
        for action in preflight_parser()._actions
        for opt in action.option_strings
    ]
    entries += [
        (f"pinka-preflight exit code {code} ({outcome})", codes, f"| {code} | `{outcome}` |")
        for outcome, code in EXIT_CODES.items()
    ]
    entries += [
        (f"bootstrap exit code {c.code} ({c.name})", codes, f"| {c.code} | `{c.name}` |")
        for c in bootstrap.exit_codes(root)
    ]
    entries += [
        (f"bootstrap.sh option {name}", bootstrap_cli, f"`{name}`")
        for option in bootstrap.options(root)
        for name in option.names
    ]
    schema = json.loads(
        (root / "preflight/schema/host-profile.schema.json").read_text(encoding="utf-8")
    )
    entries += [
        (f"host profile field {row[0]}", "docs/reference/host-profile.md", str(row[0]))
        for row in pages.schema_rows(schema, "")
    ]
    entries += ansible_docs.ansible_variable_entries(root)
    entries += lab.reference_entries()
    entries += installer.reference_entries(root)
    fingerprint_schema = json.loads((root / lab.FINGERPRINT_SCHEMA).read_text(encoding="utf-8"))
    entries += [
        (f"fingerprint field {row[0]}", lab.FINGERPRINT_PAGE, str(row[0]))
        for row in pages.schema_rows(fingerprint_schema, "")
    ]
    layout = load_layout(root)
    entries += [(f"layout path {path}", "docs/architecture.md", f"`{path}`") for path in layout]
    entries += contract.reference_entries(root)
    return entries


def load_layout(root: Path) -> list[str]:
    """List the layout paths the architecture page must show.

    Args:
        root: Component root.

    Returns:
        Absolute paths with a trailing slash.
    """
    data = sources.load_yaml(root / ansible_docs.GROUP_VARS)
    base = data["pinka_root"]
    paths = [f"{e['path']}/" for e in data["pinka_system_layout"]]
    paths += [f"{base}/{e['path']}/" if e["path"] else f"{base}/" for e in data["pinka_layout"]]
    return paths


def coverage(root: Path) -> int:
    """Check that every CLI option, exit code and schema field has a reference entry.

    Covers pinka-preflight, bootstrap.sh and pinka options and commands, the exit
    code tables, the doctor checks and the host profile schema.

    Args:
        root: Component root.

    Returns:
        0 if coverage is 100 %, 1 otherwise.
    """
    entries = reference_entries(root)
    texts: dict[str, str] = {}
    missing = []
    for label, page, needle in entries:
        if page not in texts:
            texts[page] = (root / page).read_text(encoding="utf-8")
        if needle not in texts[page]:
            missing.append(label)
    for item in missing:
        print(f"missing reference entry: {item}")
    covered = len(entries) - len(missing)
    percent = 100.0 * covered / len(entries) if entries else 100.0
    print(
        f"docs coverage: {covered}/{len(entries)} reference entries ({percent:.1f} %)",
        file=sys.stderr,
    )
    return 1 if missing else 0


def main(argv: list[str]) -> int:
    """Run a generator command.

    Args:
        argv: Arguments without the program name.

    Returns:
        Exit code.
    """
    parser = argparse.ArgumentParser(
        prog="pinka-gen", description="pokus-infrastructure generators"
    )
    parser.add_argument("command", choices=("generate", "check", "coverage"))
    parser.add_argument("--root", type=Path, default=Path(), help="component root (default: .)")
    args = parser.parse_args(argv)
    command = {"generate": generate, "check": check, "coverage": coverage}[args.command]
    return command(args.root)
