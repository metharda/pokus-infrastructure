"""Reference pages and screenshots of the installer (BUILD_PROMPT §18, DOCS_SPEC A.3, A.4)."""

from __future__ import annotations

import json
from pathlib import Path

from pokus_infra_gen import lab
from pokus_infra_gen.pages import front_matter, schema_page, schema_rows, table, usage
from pokus_installer.cli import build_parser

CLI_PAGE = "docs/reference/cli/pokus-installer.md"
ANSWERS_PAGE = "docs/reference/answers.md"
ANSWERS_SCHEMA = "installer/schema/answers.schema.json"
SNAPSHOTS = "installer/tests/__snapshots__/test_tui_snapshots"
# Screens shown in the how-to guides, as the snapshot tests render them (120x40, dark).
SCREENSHOTS = ("welcome", "preflight", "storage", "researchers", "review", "progress", "doctor")
CLI_INTRO = (
    "`python -m pokus_installer` is the installer behind `bootstrap.sh`: bootstrap runs it "
    "from the installer environment (`/opt/pokus/installer-venv`) to replay an answers file "
    "(`--answers`) or to start the TUI on a terminal. Operators use `bootstrap.sh` "
    "(see [bootstrap.sh](bootstrap.md) and [Installer answers file](../answers.md))."
)
EXIT_TEXT = (
    "Exit status of validate, describe and apply: 0 done, 1 an operator action is needed (the "
    "reason is printed), 2 usage error. The TUI exits with the installation's status: "
    "doctor's 0, 1 or 2 after Finish, 75 or 76 when WSL or the host must restart, and 77 when "
    "the operator quit before the installation finished or the terminal is unsuitable."
)


def screenshot(name: str) -> str:
    """The published path of a screenshot."""
    return f"docs/assets/installer/{name}.svg"


def screenshot_source(root: Path, name: str) -> str:
    """A screen as the snapshot test renders it.

    Args:
        root: Component root.
        name: Screen name.

    Returns:
        The SVG text.
    """
    path = root / SNAPSHOTS / f"test_screen[{name}-120x40-dark].svg"
    return path.read_text(encoding="utf-8")


def cli_page() -> str:
    """Render the installer's CLI reference from its parser.

    Returns:
        Page text.
    """
    parser = build_parser()
    out = [
        front_matter(
            "pokus-installer command",
            ["operator", "developer"],
            "Every command and option of the installer that bootstrap.sh runs.",
        ),
        f"# pokus-installer command\n\n{CLI_INTRO}\n\n",
        f"```text\n{usage(parser)}\n```\n\n",
        f"{parser.description}\n\n",
        "## Global options\n\n",
        table(
            ["Option", "Value", "Default", "Description"],
            list(lab._argument_rows(parser, with_help=True, program="pokus-installer")),
        ),
        "\n## Commands\n\n",
        table(
            ["Command", "Description"],
            [[f"`pokus-installer {name}`", text] for name, text, _ in lab.commands(parser)],
        ),
    ]
    for name, text, child in lab.commands(parser):
        out.append(f"\n### pokus-installer {name}\n\n{text[:1].upper()}{text[1:]}.\n\n")
        out.append(f"```text\n{usage(child)}\n```\n")
        rows = list(lab._argument_rows(child))
        if rows:
            out.append("\n" + table(["Argument", "Value", "Default", "Description"], rows))
    out.append(f"\n{EXIT_TEXT} See [Exit codes](../exit-codes.md).\n")
    return "".join(out)


def answers_page(root: Path) -> str:
    """Render the answers file reference from its JSON Schema.

    Args:
        root: Component root.

    Returns:
        Page text.
    """
    return schema_page(
        root,
        ANSWERS_SCHEMA,
        "Installer answers file",
        "Every field of installer-answers/<machine>.yml, written by the TUI and replayed by "
        "bootstrap.sh --answers.",
    )


def reference_entries(root: Path) -> list[tuple[str, str, str]]:
    """Entries the installer's reference pages must contain.

    Args:
        root: Component root.

    Returns:
        ``(label, page, needle)`` triples.
    """
    parser = build_parser()
    entries = [
        (f"pokus-installer command {name}", CLI_PAGE, f"### pokus-installer {name}\n")
        for name, _, _ in lab.commands(parser)
    ]
    for name, _, child in lab.commands(parser):
        entries += [
            (f"pokus-installer {name} option {opt}", CLI_PAGE, f"`{opt}`")
            for action in child._actions
            for opt in action.option_strings
        ]
    schema = json.loads((root / ANSWERS_SCHEMA).read_text(encoding="utf-8"))
    entries += [
        (f"answers field {row[0]}", ANSWERS_PAGE, str(row[0])) for row in schema_rows(schema, "")
    ]
    return entries
