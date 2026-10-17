"""The release package: the repository's tree at a tag with its pinned collections.

Owner decisions of 3d.7 and 3d.8 ("Full package"): a CI job on each
``vX.Y.Z`` tag builds a tarball of the repository's tree at the tag
(``git archive``) and adds ``release/``:

* ``release/collections/``: the five Galaxy artifacts pinned in
  ``ansible_collections.artifacts`` of ``versions.lock.yml``, the same bytes
  (each downloaded, its sha256 and size compared with the lock, and checked
  with :mod:`pinka_lockcheck.artifact`);
* ``release/NOTICE``: their licenses, built from the files in each artifact
  (license texts, ``SPDX-License-Identifier`` lines, ``REUSE.toml``
  annotations), not from the licenses a collection declares; a declared
  license that no file carries is listed as such;
* ``release/RELEASE.json``: the tag, the commit, the tree hash and the
  collections.

Bootstrap runs from the unpacked package without ``.git``: it computes the
tree hash of the unpacked files with git (a temporary index, the tree's own
``.gitignore`` and ``.gitattributes``, ``release/`` left out), treats a
difference from ``RELEASE.json`` as a dirty tree, and installs the
collections offline from ``release/collections/``. :func:`check_tree`
checks the same things here (``make release-package-check``, ``make
lock-check`` inside an unpacked package, ``make test-distros``). The
tarballs are never committed to git.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import posixpath
import re
import subprocess
import tarfile
import tempfile
import tomllib
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pinka_lockcheck import artifact, checks, net

RELEASE_DIR = "release"
COLLECTIONS_DIR = f"{RELEASE_DIR}/collections"
NOTICE = f"{RELEASE_DIR}/NOTICE"
RELEASE_JSON = f"{RELEASE_DIR}/RELEASE.json"
PACKAGE_NAME = "pokus-infrastructure"
FORMAT = 1
# The first line of a license text is shown up to this many characters.
TITLE_WIDTH = 72
TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
HEX40_RE = re.compile(r"^[0-9a-f]{40}$")
SPDX_LINE_RE = re.compile(r"SPDX-License-Identifier:[ \t]*([^\r\n]+)")
REUSE_ID_RE = re.compile(r'SPDX-License-Identifier\s*=\s*"([^"]+)"')
SPDX_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+-]*$")
# Characters that end the identifier part of a comment line (closing markers).
SPDX_END_RE = re.compile(r"\s*(?:\*/|-->|#\}|%\}|\"\"\"|''').*$")

Fetch = Callable[[str], bytes]


class PackageError(RuntimeError):
    """A release package cannot be built; the message says why."""


@dataclass(frozen=True)
class Bundled:
    """One pinned collection artifact in a package.

    Attributes:
        name: ``namespace.name``.
        version: Its locked version.
        url: Galaxy's download URL (the lock's pin).
        sha256: Its sha256 (the lock's pin).
        size: Its size in bytes.
        data: The artifact.
    """

    name: str
    version: str
    url: str
    sha256: str
    size: int
    data: bytes = field(repr=False)

    @property
    def file(self) -> str:
        """Its file name, as ``ansible-galaxy collection download`` names it."""
        return f"{self.name.replace('.', '-')}-{self.version}.tar.gz"


# --- what an artifact's files say about its licenses ------------------------------------------


def _ids(expression: str) -> list[str]:
    """The license identifiers of an SPDX expression (``A OR (B AND C)``).

    The expression is read token by token and ends at the first token that
    does not continue it, so prose after the identifier (a changelog that
    mentions ``SPDX-License-Identifier:``) names no license. An exception
    after ``WITH`` is not a license.
    """
    found: list[str] = []
    expect, exception = True, False
    for word in re.findall(r"[()]|[^\s()]+", expression):
        if word in "()":
            continue
        if expect:
            if not SPDX_ID_RE.fullmatch(word) or word in ("AND", "OR", "WITH"):
                break
            if not exception:
                found.append(word)
            expect, exception = False, False
        elif word in ("AND", "OR", "WITH"):
            expect, exception = True, word == "WITH"
        else:
            break
    return found


def _spdx(text: str) -> list[str]:
    found = []
    for match in SPDX_LINE_RE.finditer(text):
        found += _ids(SPDX_END_RE.sub("", match.group(1)))
    return found


@dataclass(frozen=True)
class Survey:
    """The license facts of one artifact, read from its files.

    Attributes:
        declared: ``collection_info.license`` of ``MANIFEST.json``.
        texts: License texts in the artifact (path and first line).
        named: Every license identifier its files name, with the number of
            files naming it (a file's own ``SPDX-License-Identifier`` line,
            its REUSE ``.license`` file, or a ``REUSE.toml`` annotation).
        uncarried: Declared licenses that no file names and no license text
            is named after.
        files: The number of files the artifact lists in ``FILES.json``.
        unnamed: Its files that name no license and are no license text.
    """

    declared: tuple[str, ...]
    texts: tuple[tuple[str, str], ...]
    named: tuple[tuple[str, int], ...]
    uncarried: tuple[str, ...]
    files: int
    unnamed: tuple[str, ...]


def survey(name: str, version: str, data: bytes) -> Survey:  # noqa: PLR0912 - one branch per kind of license fact
    """Read an artifact's license facts from its files.

    Args:
        name: ``namespace.name``.
        version: Its version.
        data: The artifact.

    Returns:
        The survey.

    Raises:
        artifact.ArtifactError: If the artifact does not pass its check.
    """
    info = artifact.check(name, version, data)
    files = artifact.members(data)
    texts = []
    for path in info["license_files"]:
        first = next((s.strip() for s in _text(files[path]).splitlines() if s.strip()), "")
        if len(first) > TITLE_WIDTH:
            first = first[: TITLE_WIDTH - 3].rstrip() + "..."
        texts.append((path, first))
    counts: dict[str, set[str]] = {}
    annotations: list[tuple[list[str], list[str]]] = []
    if "REUSE.toml" in files:
        try:
            reuse = tomllib.loads(_text(files["REUSE.toml"]))
        except tomllib.TOMLDecodeError:
            reuse = {}
        for entry in reuse.get("annotations") or []:
            paths = entry.get("path") or []
            paths = [paths] if isinstance(paths, str) else list(paths)
            ident = str(entry.get("SPDX-License-Identifier") or "")
            annotations.append(([str(p) for p in paths], _ids(ident)))
    unnamed: list[str] = []
    for path, content in files.items():
        if path in artifact.SPECIAL:
            continue
        if path.endswith(".license") and path.removesuffix(".license") in files:
            # A REUSE annotation, counted for the file it annotates.
            if not _spdx(_text(content)):
                unnamed.append(path)
            continue
        ids = _spdx(_text(content))
        if not ids and f"{path}.license" in files:
            ids = _spdx(_text(files[f"{path}.license"]))
        if not ids:
            for globs, annotated in annotations:
                if any(_glob(path, g) for g in globs):
                    ids = annotated
                    break
        for ident in ids:
            counts.setdefault(ident, set()).add(path)
        if not ids and path not in info["license_files"]:
            unnamed.append(path)
    declared = tuple(str(x) for x in info.get("license") or [])
    stems = {posixpath.splitext(posixpath.basename(p))[0] for p, _ in texts}
    uncarried = tuple(d for d in declared if d not in counts and d not in stems)
    named = tuple(sorted((ident, len(paths)) for ident, paths in counts.items()))
    total = sum(1 for path in files if path not in artifact.SPECIAL)
    return Survey(declared, tuple(texts), named, uncarried, total, tuple(sorted(unnamed)))


def _text(content: bytes) -> str:
    return content.decode("utf-8", errors="replace")


def _glob(path: str, pattern: str) -> bool:
    """REUSE.toml's path globs: ``*`` within a directory, ``**`` across them."""
    regex = re.escape(pattern).replace(r"\*\*", "\0").replace(r"\*", "[^/]*").replace("\0", ".*")
    return re.fullmatch(regex, path) is not None


def notice(bundled: Sequence[Bundled]) -> str:
    """``release/NOTICE``: the licenses of the bundled artifacts.

    Args:
        bundled: The artifacts, in the lock's order.

    Returns:
        The file's text.
    """
    lines = [
        "NOTICE: the Ansible collections of this release package",
        "",
        "release/collections/ holds the Ansible Galaxy artifacts pinned in",
        "versions.lock.yml (ansible_collections.artifacts), unchanged: the same",
        "bytes and the same sha256 as on Galaxy. Each artifact keeps its own",
        "license texts inside it. This list is built from the files of each",
        "artifact (its license texts, the SPDX-License-Identifier lines of its",
        "files and its REUSE.toml annotations), not from the licenses the",
        "collection declares in its metadata.",
    ]
    for b in bundled:
        facts = survey(b.name, b.version, b.data)
        lines += [
            "",
            f"{b.name} {b.version}",
            f"  file: {COLLECTIONS_DIR}/{b.file}",
            f"  sha256: {b.sha256}",
            f"  source: {b.url}",
            "  license texts in the artifact:",
        ]
        lines += [f"    {path}: {first}" for path, first in facts.texts] or ["    none"]
        lines.append("  licenses named by its files (SPDX identifiers, files naming each):")
        lines += [f"    {ident}: {count}" for ident, count in facts.named] or ["    none"]
        declared = ", ".join(facts.declared) or "none"
        lines.append(f"  licenses declared in its metadata (MANIFEST.json): {declared}")
        if facts.uncarried:
            lines.append(
                "  declared in its metadata but carried by no file in the artifact: "
                + ", ".join(facts.uncarried)
            )
        lines.append(f"  files in the artifact (FILES.json): {facts.files}")
        lines.append("  files naming no license and carrying no license text:")
        lines += [f"    {path}" for path in facts.unnamed] or ["    none"]
    return "\n".join(lines) + "\n"


# --- the collections of the lock --------------------------------------------------------------


def pinned(doc: Mapping[str, Any]) -> list[tuple[str, str, str, str, int]]:
    """The lock's pins: ``(name, version, url, sha256, size)`` in the lock's order.

    Raises:
        PackageError: If a locked collection has no verified pin.
    """
    section = doc["ansible_collections"]
    artifacts = section.get("artifacts") or {}
    found = []
    for name, version in section["collections"].items():
        entry = artifacts.get(name) or {}
        url, sha256, size = entry.get("url"), entry.get("sha256"), entry.get("size")
        if not checks._verified(url, sha256) or not isinstance(size, int):
            raise PackageError(f"{name} {version} has no verified artifact pin in the lock")
        found.append((str(name), str(version), str(url), str(sha256), size))
    return found


def collect(doc: Mapping[str, Any], fetch: Fetch = net.download) -> list[Bundled]:
    """Download every pinned artifact and keep it only if it matches its pin.

    Args:
        doc: Lock document.
        fetch: ``url -> content``.

    Returns:
        The artifacts, in the lock's order.

    Raises:
        PackageError: If a pin is unverified, a download fails, or an artifact
            differs from its pin or fails its check.
    """
    bundled = []
    for name, version, url, sha256, size in pinned(doc):
        try:
            data = fetch(url)
        except net.FetchError as exc:
            raise PackageError(f"{name} {version}: download failed: {exc}") from exc
        bundled.append(verify(name, version, url, sha256, size, data))
    return bundled


def verify(  # noqa: PLR0917 - the fields of one pin, and the artifact
    name: str, version: str, url: str, sha256: str, size: int, data: bytes
) -> Bundled:
    """An artifact checked against its pin and with :func:`artifact.check`.

    Raises:
        PackageError: On any difference.
    """
    actual = hashlib.sha256(data).hexdigest()
    if actual != sha256:
        raise PackageError(f"{name} {version}: sha256 is {actual}, pinned {sha256}")
    if len(data) != size:
        raise PackageError(f"{name} {version}: size is {len(data)}, pinned {size}")
    try:
        artifact.check(name, version, data)
    except (artifact.ArtifactError, tarfile.TarError) as exc:
        raise PackageError(f"{name} {version}: the artifact is refused: {exc}") from exc
    return Bundled(name, version, url, sha256, size, data)


# --- git ----------------------------------------------------------------------------------------


def _git(args: Sequence[str], *, cwd: Path, env: Mapping[str, str] | None = None) -> bytes:
    done = subprocess.run(  # noqa: S603 - git with fixed arguments
        ["git", *args],  # noqa: S607 - git from PATH
        cwd=cwd,
        env=dict(env) if env is not None else None,
        capture_output=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    if done.returncode != 0:
        message = done.stderr.decode(errors="replace").strip()
        raise PackageError(f"git {' '.join(args[:2])}: {message}")
    return done.stdout


def _isolated_env(home: str) -> dict[str, str]:
    """Git without the user's or the system's configuration."""
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": home,
        "LC_ALL": "C",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }


def tree_hash(root: Path) -> str:
    """The git tree hash of an unpacked tree, ``release/`` left out.

    The same computation as bootstrap's: a temporary repository whose index
    takes every file the tree's ``.gitignore`` files do not ignore, with its
    ``.gitattributes``; no configuration of the user or the system applies.

    Args:
        root: The unpacked tree.

    Returns:
        The tree hash.

    Raises:
        PackageError: If git fails.
    """
    with tempfile.TemporaryDirectory() as tmp:
        env = _isolated_env(tmp)
        base = [
            "-c",
            f"safe.directory={root}",
            "-c",
            "core.fsmonitor=false",
            f"--git-dir={tmp}/git",
            f"--work-tree={root}",
        ]
        _git([*base, "init", "-q"], cwd=root, env=env)
        Path(tmp, "git", "info").mkdir(exist_ok=True)
        Path(tmp, "git", "info", "exclude").write_text(f"/{RELEASE_DIR}/\n", encoding="utf-8")
        _git([*base, "add", "-A"], cwd=root, env=env)
        return _git([*base, "write-tree"], cwd=root, env=env).decode().strip()


def _repo_root(start: Path) -> Path:
    return Path(_git(["rev-parse", "--show-toplevel"], cwd=start).decode().strip())


@dataclass(frozen=True)
class Source:
    """What a package is built from.

    Attributes:
        tag: The ``vX.Y.Z`` tag, or None for a test build of a commit.
        commit: The commit.
        tree: The repository's tree hash at the commit.
        time: The commit time (seconds), every member's mtime.
    """

    tag: str | None
    commit: str
    tree: str
    time: int

    @property
    def tree_ish(self) -> str:
        """The repository's tree at the commit, for ``git archive``."""
        return f"{self.commit}:"

    def path(self, name: str) -> str:
        """A file of the repository's tree at the commit, for ``git show``."""
        return f"{self.commit}:{name}"

    @property
    def version(self) -> str:
        """The package's version: the tag's ``X.Y.Z``, or ``0.0.0+<commit>``."""
        match = TAG_RE.match(self.tag or "")
        return ".".join(match.groups()) if match else f"0.0.0+{self.commit[:12]}"


def source(infra: Path, *, tag: str | None = None, ref: str = "HEAD") -> Source:
    """Resolve what to package.

    Args:
        infra: The root of a git checkout.
        tag: A ``vX.Y.Z`` tag (a release), or None.
        ref: The commit to package when no tag is given (test builds).

    Returns:
        The source.

    Raises:
        PackageError: On a malformed or unknown tag or ref, or a directory
            that is not the root of its checkout.
    """
    if tag is not None and not TAG_RE.match(tag):
        raise PackageError(f"{tag!r} is not a release tag (vX.Y.Z)")
    root = _repo_root(infra)
    if infra.resolve() != root.resolve():
        raise PackageError(f"{infra} is not the root of its git checkout ({root})")
    want = f"refs/tags/{tag}^{{commit}}" if tag else f"{ref}^{{commit}}"
    try:
        commit = _git(["rev-parse", "--verify", "--quiet", want], cwd=root).decode().strip()
    except PackageError:
        commit = ""
    if not HEX40_RE.match(commit):
        raise PackageError(f"{tag or ref} does not name a commit of this repository")
    tree = _git(["rev-parse", f"{commit}^{{tree}}"], cwd=root).decode().strip()
    time = int(_git(["show", "-s", "--format=%ct", commit], cwd=root).decode().strip())
    return Source(tag, commit, tree, time)


# --- the package ----------------------------------------------------------------------------------


def release_json(src: Source, bundled: Sequence[Bundled], notice_text: str) -> str:
    """``release/RELEASE.json``: one key per line, so bootstrap reads it with sed."""
    record = {
        "format": FORMAT,
        "tag": src.tag,
        "version": src.version,
        "commit": src.commit,
        "infra_tree": src.tree,
        "notice_sha256": hashlib.sha256(notice_text.encode()).hexdigest(),
        "collections": [
            {
                "name": b.name,
                "version": b.version,
                "file": f"{COLLECTIONS_DIR}/{b.file}",
                "url": b.url,
                "sha256": b.sha256,
                "size": b.size,
            }
            for b in bundled
        ],
    }
    return json.dumps(record, indent=2) + "\n"


def _normalized(info: tarfile.TarInfo, name: str, mtime: int) -> tarfile.TarInfo:
    info.name = name
    info.mtime = mtime
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.pax_headers = {}
    if info.isdir():
        info.mode = 0o755
    elif info.isfile():
        info.mode = 0o755 if info.mode & 0o111 else 0o644
    return info


def build(
    infra: Path,
    out: Path,
    *,
    tag: str | None = None,
    ref: str = "HEAD",
    fetch: Fetch = net.download,
) -> Path:
    """Build the release package of a tag (or of a commit, for tests).

    The repository's tree comes from ``git archive`` (tracked files only); the
    lock read is the one at that commit, so the package is consistent with
    itself. The tarball is reproducible: sorted members, the commit time as
    every mtime, owner root, no gzip timestamp.

    Args:
        infra: The root of a git checkout.
        out: Output directory; the package and its ``.sha256`` go there.
        tag: The release tag, or None.
        ref: The commit when no tag is given.
        fetch: ``url -> content`` (Galaxy).

    Returns:
        The package.

    Raises:
        PackageError: If anything cannot be resolved, downloaded or checked.
    """
    src = source(infra, tag=tag, ref=ref)
    root_dir = _repo_root(infra)
    archive = _git(["archive", "--format=tar", src.tree_ish], cwd=root_dir)
    doc = checks.load_text(_git(["show", src.path("versions.lock.yml")], cwd=root_dir).decode())
    bundled = collect(doc, fetch)
    notice_text = notice(bundled)
    record = release_json(src, bundled, notice_text)
    prefix = f"{PACKAGE_NAME}-{src.version}"
    extra: dict[str, bytes] = {NOTICE: notice_text.encode(), RELEASE_JSON: record.encode()}
    extra.update({f"{COLLECTIONS_DIR}/{b.file}": b.data for b in bundled})
    out.mkdir(parents=True, exist_ok=True)
    package = out / f"{prefix}.tar.gz"
    members: list[tuple[tarfile.TarInfo, bytes | None]] = []
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
        for info in tar.getmembers():
            if info.type == tarfile.XGLTYPE:  # git archive's pax comment
                continue
            name = posixpath.normpath(f"{prefix}/{info.name}")
            extracted = tar.extractfile(info) if info.isfile() else None
            content = extracted.read() if extracted is not None else None
            members.append((_normalized(info, name, src.time), content))
    for directory in (RELEASE_DIR, COLLECTIONS_DIR):
        info = tarfile.TarInfo(f"{prefix}/{directory}")
        info.type = tarfile.DIRTYPE
        members.append((_normalized(info, info.name, src.time), None))
    for path, content in extra.items():
        info = tarfile.TarInfo(f"{prefix}/{path}")
        info.size = len(content)
        members.append((_normalized(info, info.name, src.time), content))
    members.sort(key=lambda m: m[0].name)
    raw = io.BytesIO()
    with (
        gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz,
        tarfile.open(fileobj=gz, mode="w:", format=tarfile.PAX_FORMAT) as tar,
    ):
        for info, content in members:
            tar.addfile(info, io.BytesIO(content) if content is not None else None)
    package.write_bytes(raw.getvalue())
    digest = hashlib.sha256(raw.getvalue()).hexdigest()
    package.with_name(package.name + ".sha256").write_text(f"{digest}  {package.name}\n")
    return package


def unpack(package: Path, dest: Path) -> Path:
    """Unpack a package safely; the root directory of its tree.

    Raises:
        PackageError: If the tarball has more than one root, or a member
            that the ``data`` extraction filter refuses (absolute paths,
            ``..``, links leaving the tree, devices).
    """
    try:
        with tarfile.open(package, mode="r:gz") as tar:
            roots = {m.name.split("/", 1)[0] for m in tar.getmembers()}
            if len(roots) != 1:
                raise PackageError(
                    f"{package.name}: expected one top directory, found {sorted(roots)}"
                )
            tar.extractall(dest, filter="data")
    except (tarfile.TarError, OSError) as exc:
        raise PackageError(f"{package.name}: {exc}") from exc
    return dest / roots.pop()


def check_tree(root: Path) -> list[str]:  # noqa: PLR0912, PLR0915 - one check per part of the package
    """Every problem of an unpacked package (empty: it checks out).

    Checks ``RELEASE.json`` against the tree (its tree hash), against the
    package's own lock (every collection pinned there, nothing else, the
    same sha256 and size), every bundled artifact (its sha256 and
    :func:`artifact.check`), and ``NOTICE`` against the one built again
    from the artifacts.

    Args:
        root: The unpacked package.

    Returns:
        One line per problem.
    """
    problems: list[str] = []
    try:
        record = json.loads((root / RELEASE_JSON).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"{RELEASE_JSON}: {exc}"]
    if record.get("format") != FORMAT:
        problems.append(f"{RELEASE_JSON}: format {record.get('format')!r}, expected {FORMAT}")
    tag = record.get("tag")
    if tag is not None and not TAG_RE.match(str(tag)):
        problems.append(f"{RELEASE_JSON}: tag {tag!r} is not vX.Y.Z")
    for key in ("commit", "infra_tree"):
        if not HEX40_RE.match(str(record.get(key) or "")):
            problems.append(f"{RELEASE_JSON}: {key} is not a 40-hex hash")
    try:
        actual = tree_hash(root)
    except PackageError as exc:
        problems.append(f"the tree hash cannot be computed: {exc}")
    else:
        if actual != record.get("infra_tree"):
            problems.append(
                f"the tree is {actual}, RELEASE.json records {record.get('infra_tree')}"
                " (files changed, added or removed)"
            )
    try:
        doc = checks.load(root / "versions.lock.yml")
        pins = pinned(doc)
    except (OSError, ValueError, PackageError) as exc:
        return [*problems, f"versions.lock.yml: {exc}"]
    listed = {str(c.get("name")): c for c in record.get("collections") or []}
    present = (
        sorted(p.name for p in (root / COLLECTIONS_DIR).glob("*"))
        if (root / COLLECTIONS_DIR).is_dir()
        else []
    )
    bundled: list[Bundled] = []
    for name, version, url, sha256, size in pins:
        entry = Bundled(name, version, url, sha256, size, b"")
        want = {
            "name": name,
            "version": version,
            "file": f"{COLLECTIONS_DIR}/{entry.file}",
            "url": url,
            "sha256": sha256,
            "size": size,
        }
        if listed.get(name) != want:
            problems.append(
                f"{RELEASE_JSON}: {name} is {listed.get(name)!r}, the lock pins {want!r}"
            )
        path = root / COLLECTIONS_DIR / entry.file
        try:
            bundled.append(verify(name, version, url, sha256, size, path.read_bytes()))
        except (OSError, PackageError) as exc:
            problems.append(f"{COLLECTIONS_DIR}/{entry.file}: {exc}")
    extra = sorted(set(present) - {Bundled(*p, b"").file for p in pins})
    if extra:
        problems.append(f"{COLLECTIONS_DIR}: not pinned in the lock: {', '.join(extra)}")
    if unlisted := sorted(set(listed) - {p[0] for p in pins}):
        problems.append(f"{RELEASE_JSON}: collections not locked: {', '.join(unlisted)}")
    if len(bundled) == len(pins):
        expected = notice(bundled)
        try:
            text = (root / NOTICE).read_text(encoding="utf-8")
        except OSError as exc:
            problems.append(f"{NOTICE}: {exc}")
        else:
            if text != expected:
                problems.append(f"{NOTICE} is not the notice built from the bundled artifacts")
            if hashlib.sha256(text.encode()).hexdigest() != record.get("notice_sha256"):
                problems.append(f"{RELEASE_JSON}: notice_sha256 does not match {NOTICE}")
    return problems


def check_package(package: Path) -> list[str]:
    """:func:`check_tree` of a package tarball, unpacked into a temporary directory."""
    with tempfile.TemporaryDirectory() as tmp:
        try:
            root = unpack(package, Path(tmp))
        except PackageError as exc:
            return [str(exc)]
        return check_tree(root)


def artifacts_of(location: str, fetch: Fetch = net.download) -> dict[str, bytes]:
    """The bundled artifacts of a package (a tarball, its URL, or an unpacked tree).

    Only members under ``release/collections/`` are read, into memory;
    nothing is extracted. The caller checks each against the lock.

    Args:
        location: A ``.tar.gz`` file, an ``https://`` URL of one, or a directory.
        fetch: ``url -> content``.

    Returns:
        Content by file name.

    Raises:
        PackageError: If the package cannot be read.
    """
    if location.startswith("https://"):
        try:
            data = fetch(location)
        except net.FetchError as exc:
            raise PackageError(f"download failed: {exc}") from exc
        return _artifacts_in(io.BytesIO(data), location)
    package = Path(location)
    if package.is_dir():
        directory = package / COLLECTIONS_DIR
        return {p.name: p.read_bytes() for p in sorted(directory.glob("*.tar.gz")) if p.is_file()}
    try:
        with package.open("rb") as handle:
            return _artifacts_in(handle, package.name)
    except OSError as exc:
        raise PackageError(f"{package}: {exc}") from exc


def _artifacts_in(handle: Any, label: str) -> dict[str, bytes]:  # noqa: ANN401 - a binary file
    found: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=handle, mode="r:gz") as tar:
            for info in tar:
                parts = info.name.split("/")
                if len(parts) == 4 and "/".join(parts[1:3]) == COLLECTIONS_DIR and info.isfile():  # noqa: PLR2004 - root/release/collections/file
                    extracted = tar.extractfile(info)
                    if extracted is not None:
                        found[parts[3]] = extracted.read()
    except (tarfile.TarError, OSError) as exc:
        raise PackageError(f"{label}: {exc}") from exc
    return found


def describe(problems: Iterable[str]) -> str:
    """One line per problem, for the command's output."""
    return "\n".join(f"package: {p}" for p in problems)
