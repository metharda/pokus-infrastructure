"""Reference pages of the operator CLI ``pinka`` and its doctor checks."""

from __future__ import annotations

import argparse
from collections.abc import Iterator

from pinka_lab.checks import CHECKS
from pinka_lab.cli import EPILOG, build_parser
from pinka_lab.doctor import EXIT_FAILURES, EXIT_OK, EXIT_WARNINGS

from pinka_gen.pages import front_matter, table, usage

CLI_PAGE = "docs/reference/cli/pinka.md"
CHECKS_PAGE = "docs/reference/doctor-checks.md"
FINGERPRINT_PAGE = "docs/reference/fingerprint.md"
FIX_PAGE = "docs/how-to/fix-doctor-checks.md"
FINGERPRINT_SCHEMA = "contract/records/fingerprint.schema.json"
DOCTOR_EXIT_CODES = (
    (EXIT_OK, "every check passed or was skipped"),
    (EXIT_WARNINGS, "only checks of severity warning failed"),
    (EXIT_FAILURES, "a check of severity error failed, or doctor could not run"),
)
CLI_INTRO = (
    "`pinka` operates a provisioned lab host. Bootstrap installs it to "
    "`/usr/local/bin/pinka` and writes the lab description `/etc/pinka/lab.json` it reads "
    "(see [Doctor checks](../doctor-checks.md) and [Exit codes](../exit-codes.md))."
)


def anchor(check_id: str) -> str:
    """Anchor of a check's section in the troubleshooting page.

    Args:
        check_id: Check ID such as ``lab.health_gate``.

    Returns:
        ``check-lab-health_gate``.
    """
    return "check-" + check_id.replace(".", "-")


def _sentence(text: str) -> str:
    text = text[:1].upper() + text[1:]
    return text if text.endswith(".") else text + "."


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction | None:  # type: ignore[type-arg]
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


def _value(action: argparse.Action) -> str:
    if action.choices:
        return ", ".join(f"`{c}`" for c in action.choices)
    if action.nargs == 0:
        return ""
    name = f"`{action.metavar or action.dest.upper()}`"
    if action.nargs in ("*", argparse.REMAINDER):
        return f"{name} ..."
    return name


def _argument_rows(
    parser: argparse.ArgumentParser, *, with_help: bool = False, program: str = "pinka"
) -> Iterator[list[str]]:
    for action in parser._actions:
        if isinstance(action, argparse._HelpAction):
            if with_help:
                names = ", ".join(f"`{s}`" for s in action.option_strings)
                yield [names, "", "", f"show the help of {program} or of a command and exit"]
            continue
        if isinstance(action, argparse._SubParsersAction):
            continue
        if action.option_strings:
            names = ", ".join(f"`{s}`" for s in action.option_strings)
            value = _value(action)
        else:
            names = f"`{action.metavar or action.dest}`" + (
                " (optional)" if action.nargs in ("?", "*") else ""
            )
            value = "" if not action.choices else _value(action)
        default = action.default
        if isinstance(action, argparse._VersionAction) or default in (
            None,
            False,
            argparse.SUPPRESS,
            [],
            "",
        ):
            shown = ""
        else:
            shown = f"`{default}`"
        yield [names, value, shown, action.help or ""]


def commands(parser: argparse.ArgumentParser) -> Iterator[tuple[str, str, argparse.ArgumentParser]]:
    """Every command of a parser, depth-first.

    Args:
        parser: The top-level parser.

    Yields:
        ``(full command, help text, parser)``, for example ``("images build", ..., ...)``.
    """
    sub = _subparsers(parser)
    if sub is None:
        return
    helps = {a.dest: a.help or "" for a in sub._choices_actions}
    for name, child in sub.choices.items():
        prefix = child.prog.split(" ", 1)[1]
        yield prefix, helps.get(name, ""), child
        yield from commands(child)


def cli_page() -> str:
    """Render ``docs/reference/cli/pinka.md`` from the parser.

    Returns:
        Page text.
    """
    parser = build_parser()
    out = [
        front_matter(
            "pinka command",
            ["operator", "developer"],
            "Every command and option of pinka.",
        ),
        f"# pinka command\n\n{CLI_INTRO}\n\n",
        f"```text\n{usage(parser)}\n```\n\n",
        f"{parser.description}\n\n",
        "## Global options\n\n",
        "Every command also accepts `-h`, `--help`.\n\n",
        table(
            ["Option", "Value", "Default", "Description"],
            list(_argument_rows(parser, with_help=True)),
        ),
        "\n## Commands\n\n",
        table(
            ["Command", "Description"],
            [[f"`pinka {name}`", text] for name, text, _ in commands(parser)],
        ),
    ]
    for name, text, child in commands(parser):
        out.append(f"\n### pinka {name}\n\n{_sentence(child.description or text)}\n\n")
        out.append(f"```text\n{usage(child)}\n```\n")
        rows = list(_argument_rows(child))
        if rows:
            out.append("\n" + table(["Argument", "Value", "Default", "Description"], rows))
    out.append(f"\n{EPILOG} See [Exit codes](../exit-codes.md).\n")
    return "".join(out)


def checks_page() -> str:
    """Render ``docs/reference/doctor-checks.md`` from the check registry.

    Returns:
        Page text.
    """
    rows = [
        [
            f"`{c.id}`",
            c.severity,
            c.description,
            f"{c.remediation} [How to fix {c.id}](../how-to/fix-doctor-checks.md#{anchor(c.id)})",
        ]
        for c in CHECKS
    ]
    return "".join(
        [
            front_matter(
                "Doctor checks",
                ["operator"],
                "Every check of pinka doctor with its severity and remediation.",
            ),
            "# Doctor checks\n\n",
            "`pinka doctor` runs these checks in this order. A check that does not apply "
            "to the host (no GPU, another node role or provider, no platform yet) is skipped, "
            "not passed. A failed check of severity `error` makes doctor exit with 2, a failed "
            "`warning` with 1 (see [Exit codes](exit-codes.md)).\n\n",
            table(["Check", "Severity", "What it asserts", "Remediation"], rows),
            "\n## Smoke checks\n\n",
            "`pinka doctor --smoke` adds one check `smoke.<blueprint>` (severity `error`) per "
            "golden image blueprint of the lab description. It launches a throwaway target of "
            "the newest image in the project `bench` on a fresh run network without NAT, with "
            "the run ACL and the telemetry proxy device, and asserts that systemd is running, "
            "the blueprint's services are active, a log line tagged with the throwaway run ID "
            "reaches Loki, the internet is unreachable, and the lab host does not answer on the "
            "lab's ports. The target and its network are deleted afterwards, also on failure. "
            "VM blueprints are skipped. See "
            "[How to fix a failed doctor check](../how-to/fix-doctor-checks.md#check-smoke) "
            "when one fails.\n",
        ]
    )


def exit_code_rows() -> list[list[str]]:
    """Rows of the pinka section of the exit code page.

    Returns:
        ``[code, command, meaning]`` rows.
    """
    rows = [[str(code), "`pinka doctor`", text] for code, text in DOCTOR_EXIT_CODES]
    rows += [
        ["0", "other commands", "success"],
        ["1", "other commands", "failure; the message says why"],
        ["2", "any", "usage error (unknown command or option)"],
    ]
    return rows


def reference_entries() -> list[tuple[str, str, str]]:
    """Entries the pinka reference pages must contain.

    Returns:
        ``(label, page, needle)`` triples.
    """
    parser = build_parser()
    entries = [
        (f"pinka command {name}", CLI_PAGE, f"### pinka {name}\n")
        for name, _, _ in commands(parser)
    ]
    for name, _, child in commands(parser):
        entries += [
            (f"pinka {name} option {opt}", CLI_PAGE, f"`{opt}`")
            for action in child._actions
            for opt in action.option_strings
        ]
    entries += [
        (f"pinka option {opt}", CLI_PAGE, f"`{opt}`")
        for action in parser._actions
        for opt in action.option_strings
    ]
    entries += [(f"doctor check {c.id}", CHECKS_PAGE, f"`{c.id}`") for c in CHECKS]
    entries += [
        (f"troubleshooting section for doctor check {c.id}", FIX_PAGE, f"{{#{anchor(c.id)}}}")
        for c in CHECKS
    ]
    entries += [
        (
            f"pinka doctor exit code {code}",
            "docs/reference/exit-codes.md",
            f"| {code} | `pinka doctor` |",
        )
        for code, _ in DOCTOR_EXIT_CODES
    ]
    return entries
