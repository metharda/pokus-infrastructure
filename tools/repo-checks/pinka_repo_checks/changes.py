"""Which CI lanes a change needs: one that touches only docs skips the Ansible lanes.

The ``changes`` job of ``.github/workflows/ci.yml`` runs this with the range
of the push (``github.event.before``..``github.sha``) or of the pull request
(its base..head) and writes ``ansible=true|false`` to ``$GITHUB_OUTPUT``. The
``check-mode`` lanes run only with ``ansible=true``
(GitHub Actions minutes). Anything it cannot decide, such as
a new branch, a range git does not know (after a force push), a schedule or a
manual run, counts as a change that needs every lane.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence

# Docs: pages, Markdown anywhere (the role READMEs and the files at the root
# included) and the docs' own tooling.
DOC_DIRS = ("docs/",)
DOC_FILES = frozenset({"mkdocs.yml", "cspell.json", "project-words.txt", "LICENSE-docs"})
NO_COMMIT = "0" * 40
COMMIT_RE = re.compile(r"[0-9a-f]{7,64}")


def docs_only(paths: Iterable[str]) -> bool:
    """Whether every changed path is a doc.

    Args:
        paths: Changed paths, relative to the repository root.

    Returns:
        True when there is at least one path and none of them is anything else.
    """
    found = [p for p in paths if p]
    return bool(found) and all(
        p.endswith(".md") or p.startswith(DOC_DIRS) or p in DOC_FILES for p in found
    )


def changed(base: str, head: str) -> list[str] | None:
    """The paths changed between two commits.

    Args:
        base: The earlier commit (empty or all zeros for a new branch).
        head: The later commit.

    Returns:
        The paths, or None when git cannot tell (an unknown commit, or a
        value that is not a commit ID).
    """
    if base == NO_COMMIT or not COMMIT_RE.fullmatch(base) or not COMMIT_RE.fullmatch(head):
        return None
    done = subprocess.run(  # noqa: S603 - git with two checked commit IDs
        ["git", "diff", "--name-only", f"{base}...{head}"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    return done.stdout.splitlines() if done.returncode == 0 else None


def main(argv: Sequence[str] | None = None) -> int:
    """Print ``ansible=true`` or ``ansible=false`` for a range of commits.

    Args:
        argv: ``--base <sha> --head <sha>``.

    Returns:
        0.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="")
    parser.add_argument("--head", default="")
    args = parser.parse_args(argv)
    paths = changed(args.base, args.head)
    ansible = paths is None or not docs_only(paths)
    print(f"ansible={'true' if ansible else 'false'}")
    if paths is not None:
        print(f"{len(paths)} changed path(s); only docs: {not ansible}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
