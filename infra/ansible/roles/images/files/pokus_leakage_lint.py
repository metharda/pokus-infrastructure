# Managed by pokus-infra (ansible/roles/images/files/pokus_leakage_lint.py). Local changes will be overwritten.
"""Leakage lint of a golden image build (BUILD_PROMPT §7.13).

Runs inside the build instance with the image's own python3 (standard
library only) and is removed again before the image is published. It looks
for strings that could tell an agent what it is being tested on: scenario
identifiers and the words of the configured patterns, in the contents and
names of files below the configured directories, in unit names and in the
host name, and for any ``.git`` directory (no repository checkout may end up
in an image).

A finding is the whole token around a match (letters, digits, ``_`` and
``-``), so ``default`` is reported as ``default`` and not as ``fault``. Allow
rules name benign words such as ``default`` or ``ISO-8859`` instead of
weakening the patterns: every part of a token that matches a rule's ``match``
is cut out, and the token is allowed only if no pattern matches what is left
(``DefaultLimitCORE`` is allowed, ``default_fault`` is not).

``scan`` writes every finding as JSON; run on the freshly launched base
before provisioning, it records the baseline. ``check`` scans again and fails
on every finding that is neither in the baseline nor allowed: a build may
not add what the base did not already contain.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
from typing import Any

MAX_BYTES = 4 << 20
MAX_TOKEN = 120
TOKEN_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")
UNIT_DIRS = ("etc/systemd/system", "lib/systemd/system", "usr/lib/systemd/system")
SKIP_TREES = ("proc", "sys", "dev", "run")
Finding = tuple[str, str, str]  # (kind, path relative to the root, token)


def load_config(path: Path) -> dict[str, Any]:
    """Read and check the lint configuration.

    Args:
        path: JSON file with ``paths``, ``patterns`` and ``allow``.

    Returns:
        The configuration with compiled patterns under ``compiled``.

    Raises:
        ValueError: If a key or an allow rule's match is missing.
        re.error: If a pattern does not compile.
    """
    config: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    for key in ("paths", "patterns", "allow"):
        if not isinstance(config.get(key), list):
            raise ValueError(f"{path}: {key} must be a list")
    for rule in config["allow"]:
        if not isinstance(rule, dict) or not isinstance(rule.get("match"), str):
            raise ValueError(f"{path}: every allow rule needs a match")
        re.compile(rule["match"])
        re.compile(rule.get("path", ".*"))
    config["compiled"] = [re.compile(p) for p in config["patterns"]]
    return config


def matches(patterns: Iterable[re.Pattern[str]], text: str) -> Iterator[str]:
    """Yield the token around every match of every pattern in a text.

    Args:
        patterns: Compiled patterns.
        text: Text to search.

    Yields:
        The match widened to the surrounding letters, digits, ``_`` and ``-``
        (at most ``MAX_TOKEN`` characters on each side).
    """
    for pattern in patterns:
        for match in pattern.finditer(text):
            start, end = match.span()
            low, high = max(0, start - MAX_TOKEN), min(len(text), end + MAX_TOKEN)
            while start > low and text[start - 1] in TOKEN_CHARS:
                start -= 1
            while end < high and text[end] in TOKEN_CHARS:
                end += 1
            yield text[start:end]


def _text(path: Path) -> str | None:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data[:8192]:
        return None
    return data.decode("utf-8", errors="replace")


def scan(root: Path, config: dict[str, Any]) -> set[Finding]:
    """Collect every finding below a root.

    Args:
        root: Root of the file system to scan (``/`` inside the instance).
        config: Configuration from :func:`load_config`.

    Returns:
        The findings.
    """
    patterns = config["compiled"]
    found: set[Finding] = set()
    for top in config["paths"]:
        base = root / top.strip("/")
        for dirpath, dirnames, filenames in os.walk(base):
            for name in dirnames + filenames:
                path = Path(dirpath) / name
                relative = path.relative_to(root).as_posix()
                found.update(("name", relative, m) for m in matches(patterns, name))
            for name in filenames:
                path = Path(dirpath) / name
                text = _text(path)
                if text is not None:
                    relative = path.relative_to(root).as_posix()
                    found.update(("content", relative, m) for m in matches(patterns, text))
    for unit_dir in UNIT_DIRS:
        directory = root / unit_dir
        if directory.is_dir():
            for unit in directory.iterdir():
                found.update(("unit", unit.name, m) for m in matches(patterns, unit.name))
    hostname = _text(root / "etc" / "hostname") or ""
    found.update(("hostname", "etc/hostname", m) for m in matches(patterns, hostname))
    found.update(_repositories(root))
    return found


def _repositories(root: Path) -> Iterator[Finding]:
    for dirpath, dirnames, _ in os.walk(root):
        here = Path(dirpath)
        if here == root:
            dirnames[:] = [d for d in dirnames if d not in SKIP_TREES]
        if ".git" in dirnames:
            yield ("git", (here / ".git").relative_to(root).as_posix(), ".git")
            dirnames.remove(".git")


def allowed(
    finding: Finding, allow: Sequence[dict[str, str]], patterns: Sequence[re.Pattern[str]]
) -> bool:
    """Tell whether the allow rules cover a finding.

    Args:
        finding: ``(kind, path, token)``.
        allow: Rules with ``match`` (a regular expression of a benign word)
            and optionally ``path`` (one the whole path must match).
        patterns: Compiled patterns.

    Returns:
        True if no pattern matches the token once every benign word of the
        rules that apply to the finding's path is cut out.
    """
    _, path, token = finding
    rest = token
    for rule in allow:
        if re.fullmatch(rule.get("path", ".*"), path):
            rest = re.sub(rule["match"], " ", rest)
    return not any(pattern.search(rest) for pattern in patterns)


def check(
    found: set[Finding],
    baseline: set[Finding],
    allow: Sequence[dict[str, str]],
    patterns: Sequence[re.Pattern[str]],
) -> list[Finding]:
    """Return the findings the build added.

    Args:
        found: Findings of the built image.
        baseline: Findings of the base before provisioning.
        allow: Allow rules.
        patterns: Compiled patterns.

    Returns:
        Sorted findings that are neither in the baseline nor allowed.
    """
    return sorted(f for f in found - baseline if not allowed(f, allow, patterns))


def _dump(findings: Iterable[Finding]) -> str:
    return json.dumps(sorted([list(f) for f in findings]), indent=1)


def main(argv: Sequence[str]) -> int:
    """Run ``scan`` or ``check``.

    Args:
        argv: Arguments without the program name.

    Returns:
        0 when clean, 1 when the build added findings, 2 on usage errors.
    """
    parser = argparse.ArgumentParser(prog="leakage-lint", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["scan", "check"])
    parser.add_argument("--root", type=Path, default=Path("/"))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, help="check: findings of the base")
    parser.add_argument("--out", type=Path, help="scan: write the findings here")
    args = parser.parse_args(list(argv))
    try:
        config = load_config(args.config)
    except (OSError, ValueError, re.error) as exc:
        sys.stderr.write(f"leakage-lint: {exc}\n")
        return 2
    found = scan(args.root, config)
    if args.command == "scan":
        text = _dump(found)
        if args.out:
            args.out.write_text(text, encoding="utf-8")
        else:
            print(text)
        return 0
    if args.baseline is None:
        sys.stderr.write("leakage-lint: check needs --baseline\n")
        return 2
    baseline: set[Finding] = {
        (str(kind), str(path), str(token))
        for kind, path, token in json.loads(args.baseline.read_text(encoding="utf-8"))
    }
    added = check(found, baseline, config["allow"], config["compiled"])
    for kind, path, match in added:
        print(f"leakage: {kind} {path}: {match}")
    print(f"leakage-lint: {len(added)} finding(s) added by the build", file=sys.stderr)
    return 1 if added else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
