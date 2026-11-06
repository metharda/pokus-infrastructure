"""Which CI lanes and local gate steps a change needs.

The ``changes`` job of ``.github/workflows/ci.yml`` runs this with the range
of the push (``github.event.before``..``github.sha``) or of the pull request
(its base..head) and writes ``ansible=true|false`` to ``$GITHUB_OUTPUT``. The
``check-mode`` lanes run only with ``ansible=true``
(GitHub Actions minutes). Anything it cannot decide, such as
a new branch, a range git does not know (after a force push), a schedule or a
manual run, counts as a change that needs every lane.

The local gate (``make gate``) asks it which of its slow steps a local tree
needs (:func:`local_skips`): the docs build and the Mermaid check only with a
change of the docs or of what builds them, the random order and the isolated
files only with a change of the tests or of what runs them. CI runs every
step on every push; anything the local classifier cannot decide needs every
step.
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
# What the docs build (mkdocs, docs/ only) and the Mermaid check read besides
# the docs: the tools' pins and the checks' own code.
DOC_TOOLING = frozenset(
    {"pyproject.toml", "uv.lock", "package.json", "package-lock.json", ".puppeteerrc.cjs"}
)
# Tests: test files, fixtures, conftest files and test helpers wherever they
# are, and what runs them (the isolated workflow's inputs and more).
TEST_TOOLING = frozenset({"pyproject.toml", "uv.lock", "Makefile"})
TEST_NAME_RE = re.compile(r"(?:^|/)(?:test_[^/]*|[^/]*_test)\.py$")
# Either: the gate's own tooling.
CHECKS_DIRS = ("tools/repo-checks/",)
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


def touches_docs(paths: Iterable[str]) -> bool:
    """Whether a path is a doc or something the docs build or the Mermaid check reads."""
    return any(
        p.endswith(".md")
        or p.startswith(DOC_DIRS + CHECKS_DIRS)
        or p in DOC_FILES
        or p in DOC_TOOLING
        or p == "Makefile"
        for p in paths
    )


def touches_tests(paths: Iterable[str]) -> bool:
    """Whether a path is a test file, a fixture, a conftest file, a test helper or their tooling."""
    return any(
        f"/{p}".find("/tests/") >= 0
        or f"/{p}".find("/fixtures/") >= 0
        or p.endswith("conftest.py")
        or TEST_NAME_RE.search(p) is not None
        or p.startswith(CHECKS_DIRS)
        or p in TEST_TOOLING
        for p in paths
    )


# The local gate's steps that a change may not need: what they need, and
# where CI runs them (on every push).
ON_EVERY_PUSH = "CI runs it on every push"
LOCAL_STEPS = {
    "docs-build": ("docs", touches_docs, ON_EVERY_PUSH),
    "lint-mermaid": ("docs", touches_docs, ON_EVERY_PUSH),
    "test-random-order": ("tests", touches_tests, ON_EVERY_PUSH + " (the test job)"),
    "test-isolated": ("tests", touches_tests, ON_EVERY_PUSH + " (infra-isolated)"),
}


def _git(*argv: str, nul: bool = False) -> list[str] | None:
    done = subprocess.run(  # noqa: S603 - git with fixed arguments
        ["git", *argv],  # noqa: S607
        capture_output=True,
        text=True,
        errors="surrogateescape",  # a name that is not UTF-8 is read, not refused
        check=False,
    )
    if done.returncode != 0:
        return None
    return done.stdout.split("\0") if nul else done.stdout.splitlines()


def local_changed(base: str) -> list[str] | None:
    """The paths a local tree changes against a base branch.

    Committed since the merge base, staged, unstaged and untracked (not
    ignored); a renamed file counts with both names.

    Args:
        base: The branch or commit the change is to go onto (``origin/moving/on``).

    Returns:
        The paths, or None when git cannot tell.
    """
    if not base or base.startswith("-"):
        return None  # not a branch or a commit: never an option of git's
    merge = _git("merge-base", "HEAD", base)
    if not merge:
        return None
    # NUL-separated: git quotes a name with other than plain ASCII otherwise.
    diff = _git("diff", "--name-only", "--no-renames", "-z", merge[0], nul=True)
    untracked = _git("ls-files", "--others", "--exclude-standard", "-z", nul=True)
    if diff is None or untracked is None:
        return None
    return sorted({p for p in diff + untracked if p})


def local_skips(steps: Iterable[str], paths: Sequence[str] | None, base: str) -> dict[str, str]:
    """The local gate's steps a change does not need, each with why.

    Args:
        steps: The gate's steps.
        paths: The changed paths (None: unknown, so every step runs).
        base: What they were compared with, for the reason.

    Returns:
        Step to reason, for the steps to skip.
    """
    if paths is None:
        return {}
    skips = {}
    for step in steps:
        if step in LOCAL_STEPS:
            what, needed, in_ci = LOCAL_STEPS[step]
            if not needed(paths):
                skips[step] = (
                    f"no change of the {what} or what checks them against {base} "
                    f"({len(paths)} changed path(s)); {in_ci}"
                )
    return skips


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
