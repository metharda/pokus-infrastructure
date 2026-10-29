"""External link check for documentation.

Runs weekly in CI as a non-blocking job: it reports broken external links in
Markdown files but never gates a merge. Links to RFC 2606 example domains,
loopback addresses and URLs that contain ``<placeholders>`` are skipped.

This module uses the Python standard library only.
"""

from __future__ import annotations

import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pinka_repo_checks.privacy import is_rfc2606_domain, list_files

URL_RE = re.compile(r"https?://[^\s<>()\"'`\]\[]+")
TRAILING = ".,;:!?*_"
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
USER_AGENT = "pinka-link-check/0.1"
HTTP_CLIENT_ERROR = 400
HEAD_UNSUPPORTED = frozenset({403, 405, 501})


@dataclass(frozen=True)
class Link:
    """One external link found in a file.

    Attributes:
        path: File containing the link.
        line: 1-based line number.
        url: The URL.
    """

    path: Path
    line: int
    url: str


def extract_links(path: Path, text: str) -> list[Link]:
    """Extract checkable external links from Markdown text.

    Args:
        path: File path, for reporting.
        text: File content.

    Returns:
        Links in file order, without skipped ones.
    """
    links: list[Link] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for m in URL_RE.finditer(line):
            url = m.group(0).rstrip(TRAILING)
            if is_skipped(url, line[m.end() : m.end() + 1]):
                continue
            links.append(Link(path, lineno, url))
    return links


def is_skipped(url: str, next_char: str = "") -> bool:
    """Tell whether a URL is a placeholder or local address.

    Args:
        url: The URL.
        next_char: Character right after the match (``<`` marks a placeholder).

    Returns:
        True if the URL must not be fetched.
    """
    if next_char == "<":
        return True
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return not host or host in LOOPBACK_HOSTS or is_rfc2606_domain(host)


def check_url(url: str, timeout: float) -> str | None:
    """Fetch a URL and report a problem, if any.

    Tries ``HEAD`` first and falls back to ``GET`` for servers that reject it.

    Args:
        url: URL to check.
        timeout: Timeout in seconds per request.

    Returns:
        None if the link works, otherwise a short error description.
    """
    error: str | None = None
    for method in ("HEAD", "GET"):
        req = urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT})  # noqa: S310 - http(s) only
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - http(s) only
                if resp.status < HTTP_CLIENT_ERROR:
                    return None
                error = f"HTTP {resp.status}"
        except urllib.error.HTTPError as exc:
            error = f"HTTP {exc.code}"
            if method == "HEAD" and exc.code in HEAD_UNSUPPORTED:
                continue
            return error
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return f"unreachable: {exc}"
    return error


def run(paths: Sequence[Path], *, timeout: float = 15.0) -> int:
    """Check every external link in the Markdown files under ``paths``.

    Args:
        paths: Files or directories to scan.
        timeout: Timeout in seconds per request.

    Returns:
        0 when all links work, 1 otherwise.
    """
    links: list[Link] = []
    for f in list_files(paths):
        if f.suffix == ".md":
            links.extend(extract_links(f, f.read_text(encoding="utf-8", errors="replace")))
    results: dict[str, str | None] = {}
    for link in links:
        if link.url not in results:
            results[link.url] = check_url(link.url, timeout)
    broken = [link for link in links if results[link.url] is not None]
    for link in broken:
        print(f"{link.path}:{link.line}: broken link {link.url} ({results[link.url]})")
    print(f"link check: {len(results)} unique links, {len(broken)} broken", file=sys.stderr)
    return 1 if broken else 0
