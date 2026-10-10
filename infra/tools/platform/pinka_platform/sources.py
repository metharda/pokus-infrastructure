"""Where a platform release and its content packages come from (§7.14 step 1).

The host settings name one source for the release (``platform``) and one per
content package (``content``), each ``{repo, ref, path, tag_prefix}``. The
transports are SSH with pinned host keys, anonymous HTTPS and root-owned
``file://`` repositories; a URL with credentials is refused.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pinka_platform import checks
from pinka_platform.problems import Problem

KEYS = frozenset({"repo", "ref", "path", "tag_prefix"})
REQUIRED_KEYS = ("repo", "ref")
SSH_URL_RE = re.compile(r"^ssh://(?:[A-Za-z0-9._-]+@)?[A-Za-z0-9.-]+(?::[0-9]{1,5})?/\S+$")
SCP_RE = re.compile(r"^[A-Za-z0-9._-]+@[A-Za-z0-9.-]+:[^/\s]\S*$")
HTTPS_RE = re.compile(r"^https://[A-Za-z0-9.-]+(?::[0-9]{1,5})?/\S+$")
FILE_RE = re.compile(r"^file:///\S+$")
REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
TAG_PREFIX_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._/-]{0,63})?$")
PACKAGE_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
RELEASE = "platform"


@dataclass(frozen=True)
class Source:
    """One source: the release or a content package.

    Attributes:
        package: Content package name; None for the release.
        repo: Repository URL.
        ref: Tag, branch or full commit to deploy.
        path: Subdirectory in the repository, normalized ("" is the root).
        tag_prefix: Prefix of this source's release tags ("" for any tag).
    """

    package: str | None
    repo: str
    ref: str
    path: str
    tag_prefix: str

    @property
    def label(self) -> str:
        """Where the source is set, for messages."""
        return RELEASE if self.package is None else f"content.{self.package}"

    @property
    def transport(self) -> str:
        """``ssh``, ``https`` or ``file``."""
        return transport(self.repo) or "invalid"

    def as_dict(self) -> dict[str, Any]:
        """The source as JSON-compatible data."""
        return {
            "repo": self.repo,
            "ref": self.ref,
            "path": self.path,
            "tag_prefix": self.tag_prefix,
        }


def transport(repo: str) -> str | None:
    """Classify a repository URL.

    Args:
        repo: Repository URL.

    Returns:
        ``ssh``, ``https`` or ``file``; None for anything else (``http://``,
        credentials in the URL, relative ``file:`` paths).
    """
    if SSH_URL_RE.match(repo) or SCP_RE.match(repo):
        return "ssh"
    if HTTPS_RE.match(repo):
        return "https"
    if FILE_RE.match(repo) and ".." not in repo.split("/"):
        return "file"
    return None


def normalize_path(path: str) -> str | None:
    """Normalize a subdirectory of a repository.

    Args:
        path: ``""``, ``"."`` or a relative path.

    Returns:
        The path without ``.`` segments and slashes at either end, or None if
        it is absolute or leaves the repository.
    """
    if path.startswith("/") or "\\" in path or "\0" in path:
        return None
    parts = [p for p in path.split("/") if p not in ("", ".")]
    if ".." in parts:
        return None
    return "/".join(parts)


def _source(package: str | None, where: str, raw: Any) -> tuple[Source | None, list[Problem]]:  # noqa: ANN401
    if not isinstance(raw, Mapping):
        return None, [Problem("source-setting", where, "must be a mapping")]
    problems = [
        Problem("source-setting", where, f"unknown key {key!r}")
        for key in sorted(set(raw) - KEYS, key=str)
    ]
    problems += [
        Problem("source-setting", where, f"missing {key!r}")
        for key in REQUIRED_KEYS
        if not isinstance(raw.get(key), str) or not raw.get(key)
    ]
    if problems:
        return None, problems
    repo, ref = str(raw["repo"]), str(raw["ref"])
    path = normalize_path(str(raw.get("path", "") or ""))
    tag_prefix = str(raw.get("tag_prefix", "") or "")
    if transport(repo) is None:
        problems.append(
            Problem(
                "source-transport",
                f"{where}.repo",
                "use an SSH URL, an anonymous https:// URL without credentials, "
                "or a file:/// path of a root-owned repository",
            )
        )
    if not REF_RE.match(ref) or ".." in ref:
        problems.append(Problem("source-setting", f"{where}.ref", f"not a valid ref: {ref!r}"))
    if path is None:
        problems.append(
            Problem("source-setting", f"{where}.path", "must be relative and stay inside")
        )
    if not TAG_PREFIX_RE.match(tag_prefix):
        problems.append(
            Problem("source-setting", f"{where}.tag_prefix", f"not a tag prefix: {tag_prefix!r}")
        )
    if problems or path is None:
        return None, problems
    return Source(package, repo, ref, path, tag_prefix), []


def parse(
    platform: Mapping[str, Any] | None, content: object
) -> tuple[Source | None, dict[str, Source], list[Problem]]:
    """Read the source settings of the host.

    Args:
        platform: The ``platform`` setting.
        content: The ``content`` setting: package name to source (a mapping).

    Returns:
        The release's source, the content packages' sources and the problems.
        Overlapping paths in one repository are problems too (§12.1).
    """
    problems: list[Problem] = []
    release, found = _source(None, RELEASE, platform or {})
    problems += found
    packages: dict[str, Source] = {}
    if content is None:
        content = {}
    if not isinstance(content, Mapping):
        return release, packages, [*problems, Problem("source-setting", "content", "not a mapping")]
    for name, raw in sorted(content.items(), key=lambda item: str(item[0])):
        if not isinstance(name, str) or not PACKAGE_RE.match(name):
            problems.append(Problem("source-setting", "content", f"bad package name {name!r}"))
            continue
        source, found = _source(name, f"content.{name}", raw)
        problems += found
        if source is not None:
            packages[name] = source
    if release is not None:
        problems += checks.check_sources(
            checks.SourceRef(release.repo, release.path),
            {n: checks.SourceRef(s.repo, s.path) for n, s in packages.items()},
        )
    return release, packages, problems


def repo_key(repo: str) -> str:
    """The identity of a repository: the URL without a trailing slash or ``.git``.

    Args:
        repo: Repository URL.

    Returns:
        The key; sources with the same key share one mirror.
    """
    return checks.repo_key(repo)


def mirror_name(repo: str) -> str:
    """Directory name of a repository's bare mirror under ``/var/lib/pinka/git``.

    Args:
        repo: Repository URL.

    Returns:
        ``<last path segment>-<12 hex digits of the URL's SHA-256>.git``.
    """
    key = repo_key(repo)
    slug = re.sub(r"[^a-z0-9-]+", "-", key.rsplit("/", 1)[-1].rsplit(":", 1)[-1].lower())
    digest = hashlib.sha256(key.encode()).hexdigest()[:12]
    return f"{slug.strip('-')[:40] or 'repo'}-{digest}.git"


def file_repo_problem(repo: str, owner_uid: int = 0) -> str | None:
    """Check that a ``file://`` repository cannot be changed by anyone but its owner.

    The repository directory and every parent must be owned by root (or
    ``owner_uid``) and not writable by group or others; a world-writable
    parent with the sticky bit (such as ``/tmp``) is accepted, since nobody
    else can replace an entry in it.

    Args:
        repo: A ``file://`` URL.
        owner_uid: The expected owner besides root (tests).

    Returns:
        Why the repository is refused, or None.
    """
    path = Path(repo.removeprefix("file://"))
    try:
        info = path.stat()
    except OSError:
        return f"{path} does not exist"
    if not stat.S_ISDIR(info.st_mode):
        return f"{path} is not a directory"
    for directory in (path, *path.parents):
        info = directory.stat()
        if info.st_uid not in (0, owner_uid):
            return f"{directory} is not owned by root"
        sticky = bool(info.st_mode & stat.S_ISVTX)
        if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH) and not (sticky and directory != path):
            return f"{directory} is writable by group or others"
    return None


def owner_uid() -> int:
    """The expected owner of ``file://`` repositories besides root: the current user.

    Returns:
        The effective user ID (root on a lab host).
    """
    return os.geteuid()
