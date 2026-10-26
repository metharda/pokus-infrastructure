"""References budget: the references lint never lets more through than at the parent commit.

The references lint (:mod:`pinka_repo_checks.references`) passes while the
references that remain equal the budget in ``references.json``. The budget,
the allowlist there, the files the lint does not read (``EXCLUDED`` in
``references.py``), the all-caps Markdown names it takes as conventional
(``CONVENTIONAL`` there) and the identifiers of the contract's Resolutions
table (``docs/contracts.md``) decide what it lets through. This check compares
them with the parent commit: the budget may go down or stay, never up, and no
allowlist entry, excluded path, conventional name or Resolutions identifier
may be added. An allowlist entry is its path, pattern and text (a new reason
is no new entry); an entry whose path, pattern or text changes is a new one.

It compares either the work tree with a commit (no ``--head``; ``--base``, by
default ``HEAD``: the next commit before it is made, in ``make lint``) or, in
CI, every commit of the range ``--base``..``--head`` with each of its parents
(a merge with all of them: whoever merges chooses which one comes first) and
``--head`` with the merge base of ``--base`` and ``--head`` (what the range
changes as a whole). A head behind its base (a pull request's branch from an
older state, a branch moved back) is not compared with the newer base: its
merge into a branch is compared with every parent then. An empty, all-zero or
unknown ``--base`` (a new branch, a force push, a manual run) is replaced by
``--fallback-base`` (CI: the main branch); when that is unknown too, ``--head``
is compared with its parents alone, and the output says so: the commits
before it are compared when they reach a branch whose push has a known base.
When the fallback is the head itself (a force push to the main branch, a
manual run on it), the range is empty and the head is compared with its
parents, as the output says: the commits a force push brings in before the
head are not compared.
A root commit has no parent to compare with, and a head with no commit in
common with ``--base`` has no merge base: such a line is compared with
``--base`` when it is merged into its branch.

Each part is compared only where the parent's first-parent line has had the
lint's ``references.json``: before it there was no budget. The parent's
``references.json`` and ``EXCLUDED`` and ``CONVENTIONAL`` are its own or, when
it has none or one that cannot be read, the last readable version on its
first-parent line; the commit's own must be readable, and neither file may
disappear while the parent has it. The contract is read as each commit has
it: without the file or its Resolutions section, a commit defines no
identifiers, so putting a section back adds its identifiers.
``references.json`` is read for its budget and the path, pattern and text of
each entry, without the lint's further checks (the lint makes them).
``EXCLUDED`` and ``CONVENTIONAL`` are read from the Python source without
running it: each must be bound once in the module (an assignment, a
parameter, a type parameter, an import, an ``except ... as``, a ``del`` and a
store to an attribute of that name all bind it), at its top level,
``EXCLUDED`` to a tuple of ``(path, reason)`` string pairs and
``CONVENTIONAL`` to ``frozenset({...})`` of strings (or ``frozenset()``),
with no star import and no other binding of ``frozenset``. None of the three
files may be a symbolic link or lie below one, since the lint would follow it.

It fails, so that nothing passes unchecked, on a root that is not the top
level of a git work tree, a shallow clone or one with grafts (parents missing
or changed), a missing parent object, an option-like or unknown revision (an
unknown ``--base`` of a range excepted, as above) and any git failure. Replace
refs are ignored.

It does not see, and review keeps the budget from going up there:

- code: the lint's patterns and functions (this check reads the Resolutions
  table of every commit it compares with the ``resolution_ids`` of the
  checkout it runs in, in CI the head's), the third-party trees, the kinds
  of files it skips, the paths of its files, the wiring of ``make lint`` and
  CI, this check, and a change of ``EXCLUDED``, ``CONVENTIONAL`` or
  ``frozenset`` at run time by other means (``globals()``, ``vars()``,
  ``setattr``, ``exec``, another module);
- a file turned into a kind the lint skips without a code change (a NUL byte,
  a symbolic link, a submodule, a move into a third-party tree): its
  references leave the count, and the budget may then go down;
- a reference that replaces another (the count stays);
- text of ``references.json`` the lint does not scan (the reasons, the
  comment, other keys);
- more occurrences of an allowed text in the files of its entry, which by the
  entry are no references.

A commit that fails here is still the parent of the next one: a revert of a
raise passes, a revert of a lowering below the count fails once. This module
uses the Python standard library only.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, TypeVar

from pinka_repo_checks.references import resolution_ids

DATA = "tools/repo-checks/pinka_repo_checks/references.json"
CODE = "tools/repo-checks/pinka_repo_checks/references.py"
CONTRACT = "docs/contracts.md"
NO_COMMIT = "0" * 40
REGULAR_MODES = frozenset({"100644", "100755"})
LINK_MODE = "120000"
# Replace refs would let git show other objects than the commits name.
GIT = ("git", "--no-replace-objects")
# TypeVar and Optional, not the newer syntax: the make targets run the
# system's python3, which may be older than the project's Python.
T = TypeVar("T")
Text = Optional[str]  # noqa: UP045 - evaluated at run time
# Nodes that bind a name held as a string: match patterns (from Python 3.10
# on) and type parameters (from 3.12 on).
NAMED_BINDERS = tuple(
    getattr(ast, n)
    for n in ("MatchAs", "MatchStar", "MatchMapping", "TypeVar", "ParamSpec", "TypeVarTuple")
    if hasattr(ast, n)
)


@dataclass(frozen=True)
class Data:
    """The budget and the allowlist entries of one ``references.json``."""

    budget: int
    allowed: frozenset[tuple[str, str, str]]


@dataclass(frozen=True)
class Limits:
    """What the references lint lets through in one tree; None where a part is absent."""

    data: Data | None
    excluded: frozenset[str] | None
    conventional: frozenset[str] | None
    resolutions: frozenset[str]


def parse_data(text: str) -> Data:
    """Read the budget and the allowlist entries of a ``references.json``.

    Args:
        text: The file's content.

    Returns:
        The budget and the ``(path, pattern, text)`` of every entry.

    Raises:
        ValueError: The content is no JSON object with a non-negative integer
            budget and a list of entries whose path, pattern and text are
            strings.
    """
    try:
        data = json.loads(text)
        budget, entries = data["budget"], data["allowed"]
        allowed = frozenset((e["path"], e["pattern"], e["text"]) for e in entries)
    except (ValueError, KeyError, TypeError, RecursionError) as err:
        raise ValueError(f"{type(err).__name__}: {err}") from err
    if not isinstance(budget, int) or isinstance(budget, bool) or budget < 0:
        raise ValueError(f"the budget must be a non-negative integer, not {budget!r}")
    if not isinstance(entries, list) or not all(isinstance(f, str) for e in allowed for f in e):
        raise ValueError(
            "allowed must be a list of entries whose path, pattern and text are strings"
        )
    return Data(budget, allowed)


def _bindings(tree: ast.Module, name: str) -> list[ast.AST]:
    """Every place the module binds or unbinds NAME."""
    found: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == name and not isinstance(node.ctx, ast.Load):
            found.append(node)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found += [node] if node.name == name else []
        elif isinstance(node, ast.Attribute) and not isinstance(node.ctx, ast.Load):
            found += [node] if node.attr == name else []
        elif isinstance(node, ast.arg):
            found += [node] if node.arg == name else []
        elif isinstance(node, ast.ExceptHandler):
            found += [node] if node.name == name else []
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            found += [node] if name in node.names else []
        elif isinstance(node, ast.alias):
            found += [node] if (node.asname or node.name.split(".")[0]) == name else []
        elif isinstance(node, NAMED_BINDERS):
            bound = (getattr(node, "name", None), getattr(node, "rest", None))
            found += [node] if name in bound else []
    return found


def _parse(source: str) -> ast.Module:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError) as err:
        raise ValueError(f"the source does not parse: {err}") from err
    if any(isinstance(n, ast.alias) and n.name == "*" for n in ast.walk(tree)):
        raise ValueError("a star import may bind any name")
    return tree


def bound_value(tree: ast.Module, name: str) -> ast.expr:
    """The expression NAME is bound to, once in the module, at its top level.

    Args:
        tree: The module.
        name: The name.

    Returns:
        The expression of its one binding.

    Raises:
        ValueError: NAME is bound anywhere else, more than once, or not at all.
    """
    bindings = _bindings(tree, name)
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            target, value = node.target, node.value
        else:
            continue
        if target in bindings and len(bindings) == 1:
            return value
    raise ValueError(f"{name} must be bound once, at the module's top level")


def _literal(value: ast.expr, name: str) -> object:
    try:
        return ast.literal_eval(value)
    except (ValueError, TypeError, SyntaxError, RecursionError) as err:
        raise ValueError(f"{name} is not a literal: {err}") from err


def parse_excluded(source: str) -> frozenset[str]:
    """Read the paths of ``EXCLUDED`` from the source of ``references.py``.

    Args:
        source: The module's source.

    Returns:
        The path patterns.

    Raises:
        ValueError: The source does not parse or has a star import, or
            ``EXCLUDED`` is not bound once at the top level to a tuple of
            ``(path, reason)`` string pairs.
    """
    value = bound_value(_parse(source), "EXCLUDED")
    pairs = _literal(value, "EXCLUDED") if isinstance(value, ast.Tuple) else None
    if not isinstance(pairs, tuple) or not all(
        isinstance(pair, tuple)
        and len(pair) == 2  # noqa: PLR2004 - a path and its reason
        and all(isinstance(item, str) for item in pair)
        for pair in pairs
    ):
        raise ValueError("EXCLUDED must be a tuple of (path, reason) string pairs")
    return frozenset(pair[0] for pair in pairs)


def parse_conventional(source: str) -> frozenset[str]:
    """Read ``CONVENTIONAL`` from the source of ``references.py``.

    Args:
        source: The module's source.

    Returns:
        The names.

    Raises:
        ValueError: The source does not parse or has a star import,
            ``frozenset`` is bound in it, or ``CONVENTIONAL`` is not bound once
            at the top level to ``frozenset({...})`` of strings.
    """
    tree = _parse(source)
    if _bindings(tree, "frozenset"):
        raise ValueError("frozenset must not be bound in the module")
    value = bound_value(tree, "CONVENTIONAL")
    names: object = None
    if (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id == "frozenset"
        and not value.keywords
    ):
        if not value.args:
            names = set()
        elif len(value.args) == 1 and isinstance(value.args[0], ast.Set):
            names = _literal(value.args[0], "CONVENTIONAL")
    if not isinstance(names, set) or not all(isinstance(item, str) for item in names):
        raise ValueError("CONVENTIONAL must be frozenset({...}) of strings or frozenset()")
    return frozenset(names)


def git(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    """Run git in ROOT.

    Args:
        root: The repository root.
        *args: git's arguments.

    Returns:
        The finished process; its exit status is not checked.

    Raises:
        RuntimeError: git cannot be run.
    """
    try:
        return subprocess.run(  # noqa: S603 - git with fixed options and checked revisions
            [*GIT, *args],
            cwd=root,
            capture_output=True,
            check=False,
        )
    except OSError as err:
        raise RuntimeError(f"cannot run git in {root}: {err}") from err


def _failed(what: str, done: subprocess.CompletedProcess[bytes]) -> RuntimeError:
    message = done.stderr.decode(errors="replace").strip() or f"exit status {done.returncode}"
    return RuntimeError(f"{what} failed: {message}")


def _path(output: bytes) -> Path:
    """A path git printed, as the file system names it (any bytes)."""
    return Path(os.fsdecode(output.rstrip(b"\n")))


def _exists(path: Path) -> bool:
    """Whether PATH exists; any error but its absence is raised, not taken as absence."""
    try:
        path.stat()
    except (FileNotFoundError, NotADirectoryError):
        return False
    return True


def check_repository(root: Path) -> None:
    """Refuse a root git cannot compare in: not a work tree's top level, shallow, grafted.

    Args:
        root: The repository root.

    Raises:
        RuntimeError: The repository cannot be compared in.
    """
    top = git(root, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        raise _failed(f"git rev-parse in {root}", top)
    try:
        if _path(top.stdout).resolve() != root.resolve():
            raise RuntimeError(f"{root} is not the top level of its git work tree")
        for name, why in (
            ("shallow", "a shallow clone may lack the parents: fetch the whole history"),
            ("info/grafts", "grafts change the parents: remove the graft file"),
        ):
            done = git(root, "rev-parse", "--git-path", name)
            if done.returncode != 0:
                raise _failed(f"git rev-parse --git-path {name}", done)
            path = _path(done.stdout)
            if _exists(path if path.is_absolute() else root / path):
                raise RuntimeError(f"{why} {path}" if name == "info/grafts" else why)
    except OSError as err:
        raise RuntimeError(f"cannot inspect the repository at {root}: {err}") from err


def commit_id(root: Path, rev: str) -> str | None:
    """The full ID of the commit REV names.

    Args:
        root: The repository root.
        rev: A revision.

    Returns:
        The commit ID, or None when git knows no such commit.

    Raises:
        RuntimeError: REV starts with a dash, or git cannot be run.
    """
    if rev.startswith("-"):
        raise RuntimeError(f"not a revision: {rev!r}")
    done = git(root, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")
    return done.stdout.decode().strip() if done.returncode == 0 else None


def parents(root: Path, commit: str) -> list[str]:
    """The parents of COMMIT.

    Args:
        root: The repository root.
        commit: A commit ID.

    Returns:
        Their IDs; empty for a root commit.

    Raises:
        RuntimeError: git fails, as it does when a parent object is missing.
    """
    done = git(root, "rev-list", "--parents", "-n", "1", commit)
    if done.returncode != 0:
        raise _failed(f"git rev-list --parents {commit[:12]}", done)
    return done.stdout.decode().split()[1:]


def _prefixes(path: str) -> list[str]:
    parts = path.split("/")
    return ["/".join(parts[: n + 1]) for n in range(len(parts))]


def blob(root: Path, commit: str, path: str) -> str | None:
    """The text of PATH at COMMIT.

    Args:
        root: The repository root.
        commit: A commit ID.
        path: A path relative to the repository root.

    Returns:
        The text, or None when COMMIT has no regular file at PATH.

    Raises:
        ValueError: PATH or a directory above it is a symbolic link at COMMIT.
        RuntimeError: git fails.
    """
    listed = git(root, "ls-tree", "-z", "--full-tree", commit, "--", *_prefixes(path))
    if listed.returncode != 0:
        raise _failed(f"git ls-tree {commit[:12]} {path}", listed)
    entries = {}
    for record in listed.stdout.decode(errors="replace").split("\0"):
        if record:
            meta, name = record.split("\t", 1)
            entries[name] = meta.split()
    # ls-tree also lists a named directory's other entries: only PATH and the
    # directories above it count.
    if any(entries.get(prefix, [""])[0] == LINK_MODE for prefix in _prefixes(path)):
        raise ValueError(f"{path} is a symbolic link or lies below one")
    mode, kind, sha = entries.get(path, ["", "", ""])
    if kind != "blob" or mode not in REGULAR_MODES:
        return None
    shown = git(root, "cat-file", "blob", sha)
    if shown.returncode != 0:
        raise _failed(f"git cat-file {sha[:12]}", shown)
    return shown.stdout.decode("utf-8", errors="replace")


def last_version(  # noqa: UP047 - the system's python3 may predate the generic syntax
    root: Path, commit: str, path: str, parse: Callable[[str], T]
) -> T | None:
    """The last readable version of PATH at COMMIT or on its first-parent line.

    Args:
        root: The repository root.
        commit: A commit ID.
        path: A path relative to the repository root.
        parse: Reads the file's text; raises ValueError for one it cannot read.

    Returns:
        The version at COMMIT or, when COMMIT has none or one that cannot be
        read, at the newest earlier commit of COMMIT's first-parent line that
        changed PATH and has a readable one; None when there is none.

    Raises:
        RuntimeError: git fails.
    """

    def version(at: str) -> tuple[bool, T | None]:
        try:
            text = blob(root, at, path)
            return (True, parse(text)) if text is not None else (False, None)
        except ValueError:
            return False, None

    found, value = version(commit)
    if found:
        return value
    done = git(root, "log", "--first-parent", "--format=%H", commit, "--", path)
    if done.returncode != 0:
        raise _failed(f"git log {commit[:12]} -- {path}", done)
    for earlier in done.stdout.decode().split():
        found, value = version(earlier)
        if found:
            return value
    return None


def contract_ids(read: Callable[[str], Text]) -> frozenset[str]:
    """The Resolutions identifiers of a tree's contract, as the tree has it.

    Args:
        read: The tree's reader.

    Returns:
        The identifiers; none without the file or its section.
    """
    text = read(CONTRACT)
    return resolution_ids(text) if text is not None else frozenset()


def limits_before(root: Path, commit: str) -> Limits:
    """What the lint let through at COMMIT, references.json and references.py as last readable.

    Args:
        root: The repository root.
        commit: A commit ID.

    Returns:
        The limits.

    Raises:
        RuntimeError: git fails.
    """

    def read(path: str) -> Text:
        try:
            return blob(root, commit, path)
        except ValueError:
            return None  # a symbolic link: no contract

    return Limits(
        last_version(root, commit, DATA, parse_data),
        last_version(root, commit, CODE, parse_excluded),
        last_version(root, commit, CODE, parse_conventional),
        contract_ids(read),
    )


def limits_of(read: Callable[[str], Text]) -> Limits:
    """What the lint lets through in one tree.

    Args:
        read: Gives a file's text by its path, None when the tree has no such
            file; raises ValueError for a symbolic link.

    Returns:
        The limits.

    Raises:
        ValueError: A file is a symbolic link, or ``references.json``,
            ``EXCLUDED`` or ``CONVENTIONAL`` cannot be read.
    """
    data, code = read(DATA), read(CODE)
    resolutions = contract_ids(read)
    try:
        parsed = parse_data(data) if data is not None else None
    except ValueError as err:
        raise ValueError(f"{DATA}: {err}") from err
    try:
        excluded = parse_excluded(code) if code is not None else None
        conventional = parse_conventional(code) if code is not None else None
    except ValueError as err:
        raise ValueError(f"{CODE}: {err}") from err
    return Limits(parsed, excluded, conventional, resolutions)


def _added(what: str, new: Iterable[str]) -> list[str]:
    return [f"{what} added: {item}" for item in sorted(new)]


def compare(new: Limits, old: Limits) -> list[str]:
    """What NEW lets through beyond OLD.

    Args:
        new: The limits of the commit or the work tree.
        old: The limits it is compared with.

    Returns:
        One line per problem; empty when nothing grew, or when OLD had no
        budget yet.
    """
    if old.data is None:
        return []
    problems: list[str] = []
    if new.data is None:
        problems.append(f"{DATA} is gone")
    else:
        if new.data.budget > old.data.budget:
            problems.append(f"the budget goes up from {old.data.budget} to {new.data.budget}")
        problems += [
            f"allowlist entry added: {path} {pattern} {text!r}"
            for path, pattern, text in sorted(new.data.allowed - old.data.allowed)
        ]
    for what, now, before in (
        ("excluded path", new.excluded, old.excluded),
        ("conventional name", new.conventional, old.conventional),
    ):
        if before is not None and now is None:
            problems.append(f"{CODE} is gone")
            break
        if before is not None and now is not None:
            problems += _added(what, now - before)
    return problems + _added("Resolutions identifier", new.resolutions - old.resolutions)


def summary(new: Limits, old: Limits) -> str:
    """Say what was compared, for a comparison without problems.

    Args:
        new: The limits of the commit or the work tree.
        old: The limits it is compared with.

    Returns:
        The text.
    """
    if old.data is None or new.data is None:
        return "nothing to compare with: no references budget before"
    return (
        f"budget {new.data.budget} ({old.data.budget} before); no allowlist entry, excluded"
        " path, conventional name or Resolutions identifier added"
    )


def work_tree_reader(root: Path) -> Callable[[str], Text]:
    """Read files of the work tree.

    Args:
        root: The repository root.

    Returns:
        A reader for :func:`limits_of`: a regular file's text, None for a
        missing path or anything else that is not a regular file.
    """

    def read(path: str) -> Text:
        try:
            if any((root / prefix).is_symlink() for prefix in _prefixes(path)):
                raise ValueError(f"{path} is a symbolic link or lies below one")
            file = root / path
            return file.read_text(encoding="utf-8", errors="replace") if file.is_file() else None
        except OSError as err:
            raise RuntimeError(f"cannot read {path}: {err}") from err

    return read


@dataclass(frozen=True)
class Outcome:
    """One comparison: what was compared, and what grew."""

    label: str
    detail: str
    problems: list[str]


def _outcome(label: str, read: Callable[[], Limits], old: Limits) -> Outcome:
    try:
        new = read()
    except ValueError as err:
        return Outcome(label, "", [str(err)])
    return Outcome(label, summary(new, old), compare(new, old))


def _commit_limits(root: Path, commit: str) -> Callable[[], Limits]:
    return lambda: limits_of(lambda path: blob(root, commit, path))


def compare_work_tree(root: Path, base: str) -> list[Outcome]:
    """Compare the work tree with BASE.

    Args:
        root: The repository root.
        base: A revision.

    Returns:
        The outcome.

    Raises:
        RuntimeError: BASE names no known commit, a file cannot be read, or git fails.
    """
    base_id = commit_id(root, base)
    if base_id is None:
        raise RuntimeError(f"unknown commit: {base}")
    old = limits_before(root, base_id)
    label = f"the work tree against {base_id[:12]}"
    return [_outcome(label, lambda: limits_of(work_tree_reader(root)), old)]


def resolve_base(root: Path, base: str, fallback: str) -> tuple[str | None, str | None]:
    """The commit a range starts after.

    Args:
        root: The repository root.
        base: The range's base; empty or all zeros for none.
        fallback: Used when BASE names no known commit; empty for none.

    Returns:
        The commit ID or None, and a note when BASE was not used.

    Raises:
        RuntimeError: A revision starts with a dash, or git cannot be run.
    """
    if base and base != NO_COMMIT:
        base_id = commit_id(root, base)
        if base_id is not None:
            return base_id, None
    unknown = f"base {base or '(none)'} is no known commit"
    fallback_id = commit_id(root, fallback) if fallback else None
    if fallback_id is not None:
        return fallback_id, f"{unknown}: the range starts after {fallback} instead"
    nor = f", nor is the fallback {fallback}" if fallback else ""
    return None, f"{unknown}{nor}: the head is compared with its parents alone"


def merge_base(root: Path, base: str, head: str) -> str | None:
    """The best common ancestor of BASE and HEAD.

    Args:
        root: The repository root.
        base: A commit ID.
        head: A commit ID.

    Returns:
        Its ID, or None for unrelated histories.

    Raises:
        RuntimeError: git fails.
    """
    done = git(root, "merge-base", base, head)
    if done.returncode == 1 and not done.stderr:
        return None
    if done.returncode != 0:
        raise _failed("git merge-base", done)
    return done.stdout.decode().strip()


def compare_range(root: Path, base: str, head: str, fallback: str = "") -> list[Outcome]:
    """Compare every commit of BASE..HEAD with each parent, and HEAD with the merge base.

    Args:
        root: The repository root.
        base: The range's base; empty, all zeros or unknown for none.
        head: The range's last commit.
        fallback: The base used when BASE names no known commit.

    Returns:
        The outcomes, one per comparison.

    Raises:
        RuntimeError: HEAD names no known commit, a parent is missing, or git fails.
    """
    head_id = commit_id(root, head)
    if head_id is None:
        raise RuntimeError(f"unknown commit: {head}")
    base_id, note = resolve_base(root, base, fallback)
    if note:
        print(f"references-budget: {note}")
    commits, whole = [head_id], None
    if base_id is not None:
        done = git(root, "rev-list", "--reverse", f"{base_id}..{head_id}")
        if done.returncode != 0:
            raise _failed("git rev-list", done)
        commits = done.stdout.decode().split() or [head_id]
        if commits == [head_id] and not done.stdout.strip():
            print(
                f"references-budget: {base_id[:12]}..{head_id[:12]} is empty:"
                " the head is compared with its parents"
            )
        whole = merge_base(root, base_id, head_id)
    pairs: list[tuple[str, str, str]] = []
    outcomes: list[Outcome] = []
    for commit in commits:
        found = parents(root, commit)
        if not found:
            outcomes.append(Outcome(commit[:12], "a root commit, no parent to compare with", []))
        pairs += [(commit, parent, "") for parent in found]
    if whole is not None and whole != head_id and (head_id, whole, "") not in pairs:
        pairs.append((head_id, whole, "the merge base "))
    for commit, older, role in pairs:
        label = f"{commit[:12]} against {role}{older[:12]}"
        outcomes.append(_outcome(label, _commit_limits(root, commit), limits_before(root, older)))
    return outcomes


def run(root: Path, base: str = "HEAD", head: str | None = None, fallback: str = "") -> int:
    """Check the work tree against BASE, or the range BASE..HEAD.

    Args:
        root: The repository root.
        base: The commit the work tree is compared with, or the range's base.
        head: The range's last commit; None to check the work tree.
        fallback: The range's base when BASE names no known commit.

    Returns:
        0 when nothing grew, 1 otherwise.
    """
    try:
        check_repository(root)
        if head is None:
            outcomes = compare_work_tree(root, base)
        else:
            outcomes = compare_range(root, base, head, fallback)
    except RuntimeError as err:
        print(f"references-budget: {err}", file=sys.stderr)
        return 1
    for outcome in outcomes:
        if not outcome.problems:
            print(f"references-budget: {outcome.label}: {outcome.detail}")
        for problem in outcome.problems:
            print(f"references-budget: {outcome.label}: {problem}", file=sys.stderr)
    return 1 if any(outcome.problems for outcome in outcomes) else 0
