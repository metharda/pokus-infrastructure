"""Generated sections of ``README.md`` (BUILD_PROMPT §15).

The README carries an excerpt of the support matrix and the bootstrap exit
codes between ``generated:<name>`` markers; the rest of the file is written
by hand.
"""

from __future__ import annotations

from pathlib import Path

from pinka_gen import bootstrap
from pinka_gen.pages import GENERATED_NOTE, match_text, table
from pinka_gen.sources import jsonable, load_yaml, resolve_lock_refs

README = "README.md"
# Tiers shown in the README; the full matrix is in docs/support-matrix.md.
SHOWN_TIERS = ("supported", "tested")


def marker(name: str, edge: str) -> str:
    """The comment that starts or ends a generated section.

    Args:
        name: Section name.
        edge: ``start`` or ``end``.

    Returns:
        The marker line.
    """
    return f"<!-- generated:{name}:{edge} -->"


def splice(text: str, name: str, body: str) -> str:
    """Replace the body of one generated section.

    Args:
        text: File text.
        name: Section name.
        body: New body (Markdown, ending with a newline).

    Returns:
        The file text with the new body.

    Raises:
        ValueError: If the markers are missing.
    """
    start, end = marker(name, "start"), marker(name, "end")
    head, sep, rest = text.partition(start + "\n")
    _, sep2, tail = rest.partition(end)
    if not sep or not sep2:
        raise ValueError(f"{README}: missing {start} / {end}")
    return f"{head}{start}\n{GENERATED_NOTE}\n\n{body}{end}{tail}"


def environments(root: Path) -> str:
    """The supported and tested distributions, architectures and environments.

    Args:
        root: Component root.

    Returns:
        Markdown tables.
    """
    m = resolve_lock_refs(
        jsonable(load_yaml(root / "preflight/support-matrix.yml")),
        jsonable(load_yaml(root / "versions.lock.yml")),
    )
    distributions = [
        [match_text(rule["match"]), rule.get("family"), rule["tier"]]
        for rule in m["os"]
        if rule["tier"] in SHOWN_TIERS
    ]
    architectures = [
        [match_text(rule["match"]), rule["tier"]]
        for rule in m["arch"]
        if rule["tier"] != "unsupported"
    ]
    providers = [
        [name, entry["tier"]] for name, entry in m["environment"].items() if entry.get("provider")
    ]
    minimums = m["minimums"]
    return "".join(
        [
            table(["Distribution", "Family", "Tier"], distributions),
            "\n",
            table(["Architecture", "Tier"], architectures),
            "\n",
            table(["Environment", "Tier"], providers),
            f"\nAt least {minimums['memory_gib']['floor']} GiB of memory, more as the choices "
            "require (features, node role, concurrent targets, the model), and "
            f"{minimums['cpu_threads']['required']} CPU threads; the disk follows the choices "
            "too. Derivatives are experimental; "
            "everything else, and the full rules, are in "
            "[docs/support-matrix.md](docs/support-matrix.md).\n",
        ]
    )


def exit_codes(root: Path) -> str:
    """The exit codes of ``bootstrap.sh``.

    Args:
        root: Component root.

    Returns:
        A Markdown table.
    """
    rows = [[code.code, code.title, code.meaning] for code in bootstrap.exit_codes(root)]
    return table(["Code", "Title", "Meaning"], rows) + (
        "\nEach code has a section in "
        "[docs/how-to/troubleshoot.md](docs/how-to/troubleshoot.md); the codes of "
        "`pinka-preflight` and `pinka` are in "
        "[docs/reference/exit-codes.md](docs/reference/exit-codes.md).\n"
    )


def readme(root: Path) -> str:
    """Return ``README.md`` with fresh generated sections.

    Args:
        root: Component root.

    Returns:
        File text.
    """
    text = (root / README).read_text(encoding="utf-8")
    text = splice(text, "environments", environments(root))
    return splice(text, "exit-codes", exit_codes(root))
