"""Pin the Ansible collections: Galaxy's artifact of every locked collection, checked and printed.

Maintainer tooling (``make lock-collections``), run only by a maintainer when
pinning or upgrading collections; users and bootstrap never run it. For every
collection in ``ansible_collections.collections`` of ``versions.lock.yml``
(and every ``--with NAME==VERSION``, for a transitive dependency not locked
yet) it asks Galaxy for that version, the way ``ansible-galaxy`` does
(``<server>/api/`` names the v3 API; ``collections/<namespace>/<name>/
versions/<version>/`` gives ``download_url`` and ``artifact.sha256``),
downloads the artifact and computes its sha256 itself, which must equal
Galaxy's. Then it checks the artifact as ``ansible-galaxy collection
verify`` checks an installed collection: ``MANIFEST.json`` names this
collection and version, ``FILES.json`` matches its checksum there, every
file is listed with its checksum and nothing unlisted is in the tarball.
Every dependency an artifact declares must be locked at a version that
satisfies it. It prints the pins in the lock's format, with each artifact's
size and license files, and installs or changes nothing; the output is
reviewed before it is committed, and an upgrade lands only after the full
gate and CI pass with the new versions.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import re
import sys
import tarfile
import urllib.parse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pinka_lockcheck import checks, net

GALAXY = "https://galaxy.ansible.com"
NAME_RE = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
CONSTRAINT_RE = re.compile(r"^(==|!=|>=|<=|>|<)?\s*(\S+)$")
SPECIAL = ("MANIFEST.json", "FILES.json")
LICENSE_RE = re.compile(r"(^|/)(COPYING|LICENSE[^/]*|LICENSES/[^/]+)$")

Fetch = Callable[[str], bytes]


class PinError(RuntimeError):
    """A collection cannot be pinned; the message says why."""


@dataclass
class Pin:
    """One collection's artifact.

    Attributes:
        name: ``namespace.name``.
        version: Its version.
        url: Galaxy's download URL.
        sha256: The artifact's sha256 (computed, equal to Galaxy's).
        size: Its size in bytes.
        licenses: ``collection_info.license`` of its ``MANIFEST.json``.
        license_files: Its license files.
        dependencies: ``collection_info.dependencies`` of its ``MANIFEST.json``.
    """

    name: str
    version: str
    url: str
    sha256: str
    size: int
    licenses: list[str] = field(default_factory=list)
    license_files: list[str] = field(default_factory=list)
    dependencies: dict[str, str] = field(default_factory=dict)


def _version(text: str) -> tuple[int, int, int]:
    match = VERSION_RE.match(text)
    if not match:
        raise PinError(f"version {text!r} is not MAJOR.MINOR.PATCH")
    major, minor, patch = (int(g) for g in match.groups())
    return major, minor, patch


def satisfies(version: str, requirement: str) -> bool:
    """Whether a version meets a Galaxy requirement (``*``, ``>=1.0.0``, ``>=1,<2``, ...).

    Args:
        version: ``MAJOR.MINOR.PATCH``.
        requirement: Comma-separated constraints; ``*`` is any version.

    Returns:
        Whether every constraint holds.

    Raises:
        PinError: On a malformed version or constraint.
    """
    have = _version(version)
    for part in (p.strip() for p in requirement.split(",")):
        if part in ("", "*"):
            continue
        match = CONSTRAINT_RE.match(part)
        if not match:
            raise PinError(f"constraint {part!r} is not understood")
        operator, wanted = match.group(1) or "==", _version(match.group(2))
        ok = {
            "==": have == wanted,
            "!=": have != wanted,
            ">=": have >= wanted,
            "<=": have <= wanted,
            ">": have > wanted,
            "<": have < wanted,
        }[operator]
        if not ok:
            return False
    return True


def _json(fetch: Fetch, url: str) -> dict[str, Any]:
    try:
        value = json.loads(fetch(url))
    except ValueError as exc:
        raise PinError(f"{url}: not JSON") from exc
    if not isinstance(value, dict):
        raise PinError(f"{url}: not a JSON object")
    return value


def v3_root(fetch: Fetch, server: str) -> str:
    """The v3 API of a Galaxy server, as ``ansible-galaxy`` finds it.

    Raises:
        PinError: If the server names no v3 API.
    """
    api = server.rstrip("/") + "/api/"
    versions = _json(fetch, api).get("available_versions") or {}
    if "v3" not in versions:
        raise PinError(f"{api}: available_versions names no v3 API")
    return urllib.parse.urljoin(api, str(versions["v3"]))


def _field(data: Mapping[str, Any], path: str, url: str) -> Any:  # noqa: ANN401 - JSON value
    value: Any = data
    for key in path.split("."):
        if not isinstance(value, Mapping) or key not in value:
            raise PinError(f"{url}: the answer has no {path}")
        value = value[key]
    return value


def _members(artifact: bytes) -> dict[str, bytes]:
    """The regular files of a collection artifact by path; anything else is refused."""
    files: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(artifact), mode="r:gz") as tar:
        for member in tar.getmembers():
            name = member.name.removeprefix("./")
            if member.isdir():
                continue
            if not member.isfile():
                raise PinError(f"member {name} is not a regular file or directory")
            if name.startswith("/") or ".." in name.split("/"):
                raise PinError(f"member {name} lies outside the collection")
            extracted = tar.extractfile(member)
            if extracted is None:
                raise PinError(f"member {name} cannot be read")
            files[name] = extracted.read()
    return files


def check_artifact(name: str, version: str, artifact: bytes) -> dict[str, Any]:
    """Check an artifact like ``ansible-galaxy collection verify``; returns its collection_info.

    Raises:
        PinError: On any difference.
    """
    files = _members(artifact)
    for special in SPECIAL:
        if special not in files:
            raise PinError(f"{name} {version}: the artifact has no {special}")
    manifest = json.loads(files["MANIFEST.json"])
    info = manifest.get("collection_info") or {}
    found = f"{info.get('namespace')}.{info.get('name')}"
    if found != name or info.get("version") != version:
        raise PinError(f"{name} {version}: MANIFEST.json names {found} {info.get('version')}")
    listing = manifest.get("file_manifest_file") or {}
    if listing.get("chksum_type") != "sha256" or listing.get("chksum_sha256") != (
        hashlib.sha256(files["FILES.json"]).hexdigest()
    ):
        raise PinError(f"{name} {version}: FILES.json does not match MANIFEST.json")
    listed: dict[str, str] = {}
    for entry in json.loads(files["FILES.json"]).get("files", []):
        if entry.get("ftype") == "file":
            if entry.get("chksum_type") != "sha256":
                raise PinError(f"{name} {version}: {entry.get('name')} has no sha256 in FILES.json")
            listed[str(entry["name"])] = str(entry["chksum_sha256"])
    present = {path for path in files if path not in SPECIAL}
    if missing := sorted(set(listed) - present):
        raise PinError(f"{name} {version}: listed but missing: {', '.join(missing[:5])}")
    if extra := sorted(present - set(listed)):
        raise PinError(f"{name} {version}: in the tarball but not listed: {', '.join(extra[:5])}")
    if wrong := sorted(
        p for p, digest in listed.items() if hashlib.sha256(files[p]).hexdigest() != digest
    ):
        raise PinError(
            f"{name} {version}: checksum differs from FILES.json: {', '.join(wrong[:5])}"
        )
    info = dict(info)
    info["license_files"] = sorted(p for p in present if LICENSE_RE.search(p))
    return info


def pin(fetch: Fetch, v3: str, name: str, version: str) -> Pin:
    """Pin one collection version from Galaxy.

    Raises:
        PinError: If Galaxy's answer or the artifact does not check out.
    """
    if not NAME_RE.match(name):
        raise PinError(f"{name!r} is not namespace.name")
    namespace, collection = name.split(".")
    url = urllib.parse.urljoin(v3, f"collections/{namespace}/{collection}/versions/{version}/")
    answer = _json(fetch, url)
    download = urllib.parse.urljoin(url, str(_field(answer, "download_url", url)))
    expected = str(_field(answer, "artifact.sha256", url))
    artifact = fetch(download)
    digest = hashlib.sha256(artifact).hexdigest()
    if digest != expected:
        raise PinError(f"{name} {version}: {download} has sha256 {digest}, Galaxy says {expected}")
    info = check_artifact(name, version, artifact)
    return Pin(
        name=name,
        version=version,
        url=download,
        sha256=digest,
        size=len(artifact),
        licenses=[str(x) for x in info.get("license") or []],
        license_files=list(info["license_files"]),
        dependencies={str(k): str(v) for k, v in (info.get("dependencies") or {}).items()},
    )


def dependency_problems(pins: Sequence[Pin]) -> list[str]:
    """Every declared dependency that is not pinned at a satisfying version."""
    versions = {p.name: p.version for p in pins}
    problems = []
    for p in pins:
        for dep, requirement in sorted(p.dependencies.items()):
            if dep not in versions:
                problems.append(
                    f"{p.name} {p.version} needs {dep} {requirement}, which is not locked "
                    f"(add it to the lock, or try it with --with {dep}==<version>)"
                )
            elif not satisfies(versions[dep], requirement):
                problems.append(
                    f"{p.name} {p.version} needs {dep} {requirement}; the lock has {versions[dep]}"
                )
    return problems


def render(pins: Sequence[Pin], server: str, now: dt.datetime) -> str:
    """The pins in the lock's format, to paste under ``ansible_collections``."""
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        f"# make lock-collections {stamp} from {server}: each artifact's sha256 computed",
        "# from the download and equal to Galaxy's; MANIFEST.json, FILES.json and every",
        "# file checked; every dependency locked at a satisfying version. Review before",
        "# committing; paste under ansible_collections in versions.lock.yml.",
        f"verified_on: {now.date().isoformat()}",
        "collections:",
        *(f'  {p.name}: "{p.version}"' for p in pins),
        "artifacts:",
    ]
    for p in pins:
        lines += [
            f"  {p.name}:",
            f'    url: "{p.url}"',
            f'    sha256: "{p.sha256}"',
            f"    size: {p.size}",
            f"    # licenses: {', '.join(p.licenses) or 'none declared'}",
            f"    # license files: {', '.join(p.license_files) or 'none'}",
        ]
    total = sum(p.size for p in pins)
    lines.append(f"# {len(pins)} artifacts, {total} bytes together")
    return "\n".join(lines) + "\n"


def run(
    lock: Mapping[str, Any],
    extra: Mapping[str, str],
    *,
    server: str = GALAXY,
    fetch: Fetch | None = None,
    now: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
) -> str:
    """Pin every locked collection and every extra one.

    Args:
        lock: ``versions.lock.yml``.
        extra: ``{name: version}`` from ``--with``.
        server: The Galaxy server.
        fetch: Downloads a URL (default: :func:`net.download`; tests replace it).
        now: Clock.

    Returns:
        The output for the lock.

    Raises:
        PinError: If any collection cannot be pinned or a dependency is open.
    """
    get = fetch or net.download
    wanted = {str(k): str(v) for k, v in lock["ansible_collections"]["collections"].items()}
    for name, version in extra.items():
        if name in wanted:
            raise PinError(f"{name} is locked already; change its version in the lock")
        wanted[name] = version
    v3 = v3_root(get, server)
    pins = [pin(get, v3, name, version) for name, version in sorted(wanted.items())]
    problems = dependency_problems(pins)
    if problems:
        raise PinError("; ".join(problems))
    return render(pins, server, now())


def _extra(values: Sequence[str]) -> dict[str, str]:
    found = {}
    for value in values:
        name, sep, version = value.partition("==")
        if not sep or not NAME_RE.match(name) or not VERSION_RE.match(version):
            raise PinError(f"--with takes NAMESPACE.NAME==MAJOR.MINOR.PATCH, not {value!r}")
        found[name] = version
    return found


def main(argv: Sequence[str] | None = None) -> int:
    """Print the pins; 1 when a collection cannot be pinned."""
    parser = argparse.ArgumentParser(prog="lock-collections", description=__doc__.splitlines()[0])
    parser.add_argument("--lock", type=Path, default=Path("versions.lock.yml"))
    parser.add_argument("--server", default=GALAXY, help="the Galaxy server (https)")
    parser.add_argument(
        "--with", dest="extra", action="append", default=[], metavar="NAME==VERSION"
    )
    args = parser.parse_args(argv)
    try:
        text = run(checks.load(args.lock), _extra(args.extra), server=args.server)
    except (PinError, net.FetchError, tarfile.TarError, ValueError) as exc:
        sys.stderr.write(f"lock-collections: {exc}\n")
        return 1
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
