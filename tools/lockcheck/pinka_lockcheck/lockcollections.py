"""Pin the Ansible collections: Galaxy's artifact of every locked collection, checked and printed.

Maintainer tooling (``make lock-collections``), run only by a maintainer when
pinning or upgrading collections; users and bootstrap never run it. For every
collection in ``ansible_collections.collections`` of ``versions.lock.yml``
(and every ``--with NAME==VERSION``, for a transitive dependency not locked
yet) it asks Galaxy for that version, the way ``ansible-galaxy`` does
(``<server>/api/`` names the v3 API; ``collections/<namespace>/<name>/
versions/<version>/`` gives ``download_url`` and ``artifact.sha256``),
downloads the artifact and computes its sha256 itself, which must equal
Galaxy's. Then it checks the artifact with :mod:`pinka_lockcheck.artifact`,
the rule ``make setup`` and bootstrap apply too (``FILES.json`` and
``MANIFEST.json`` as ``ansible-galaxy collection verify`` checks them, and
only relative symbolic links that stay inside the collection). Every
dependency an artifact declares must be locked at a version that satisfies
it. It prints the pins in the lock's format, with each artifact's
size and license files, and installs or changes nothing; the output is
reviewed before it is committed, and an upgrade lands only after the full
gate and CI pass with the new versions.

With ``--check`` (``make lock-collections-check``, the ``lock-collections``
CI job) it also compares the pins with the lock's ``artifacts`` (URL, sha256,
size) and fails on any difference, so a pin that no longer matches Galaxy,
or a check that refuses an artifact the lock pins, fails CI.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
import tarfile
import urllib.parse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pinka_lockcheck import artifact, checks, net, retry

GALAXY = "https://galaxy.ansible.com"
NAME_RE = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
CONSTRAINT_RE = re.compile(r"^(==|!=|>=|<=|>|<)?\s*(\S+)$")

Fetch = Callable[[str], bytes]


class PinError(RuntimeError):
    """A collection cannot be pinned; the message says why."""


class GalaxyAnswerError(PinError, retry.MalformedAnswerError):
    """Galaxy's answer is not what its API promises (tried again)."""


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
        raise GalaxyAnswerError(f"{url}: not JSON") from exc
    if not isinstance(value, dict):
        raise GalaxyAnswerError(f"{url}: not a JSON object")
    return value


def v3_root(fetch: Fetch, server: str, policy: retry.Policy = retry.DEFAULT) -> str:
    """The v3 API of a Galaxy server, as ``ansible-galaxy`` finds it.

    Args:
        fetch: Downloads a URL.
        server: The Galaxy server.
        policy: Tries again after a transport error or a malformed answer.

    Raises:
        PinError: If the server names no v3 API.
    """
    api = server.rstrip("/") + "/api/"

    def ask() -> str:
        versions = _json(fetch, api).get("available_versions") or {}
        if "v3" not in versions:
            raise GalaxyAnswerError(f"{api}: available_versions names no v3 API")
        return urllib.parse.urljoin(api, str(versions["v3"]))

    return policy.run(ask, f"Galaxy: GET {api}")


def _field(data: Mapping[str, Any], path: str, url: str) -> Any:  # noqa: ANN401 - JSON value
    value: Any = data
    for key in path.split("."):
        if not isinstance(value, Mapping) or key not in value:
            raise GalaxyAnswerError(f"{url}: the answer has no {path}")
        value = value[key]
    return value


def galaxy_artifact(
    fetch: Fetch, v3: str, name: str, version: str, policy: retry.Policy = retry.DEFAULT
) -> tuple[str, bytes]:
    """One collection version's artifact from Galaxy, with Galaxy's sha256.

    Galaxy's answer and the download are each tried again after a transport
    error or a malformed answer; a sha256 that differs from Galaxy's is not.

    Args:
        fetch: Downloads a URL.
        v3: Galaxy's v3 API (:func:`v3_root`).
        name: ``namespace.name``.
        version: Its version.
        policy: Tries again after a transport error or a malformed answer.

    Returns:
        Galaxy's download URL and the artifact.

    Raises:
        PinError: If Galaxy's answer stays malformed or the artifact's sha256
            differs from Galaxy's (with the expected and the actual value).
    """
    if not NAME_RE.match(name):
        raise PinError(f"{name!r} is not namespace.name")
    namespace, collection = name.split(".")
    url = urllib.parse.urljoin(v3, f"collections/{namespace}/{collection}/versions/{version}/")

    def ask() -> tuple[str, str]:
        answer = _json(fetch, url)
        download = urllib.parse.urljoin(url, str(_field(answer, "download_url", url)))
        return download, str(_field(answer, "artifact.sha256", url))

    download, expected = policy.run(ask, f"Galaxy: GET {url}")
    data = policy.run(lambda: fetch(download), f"Galaxy: download {download}")
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise PinError(
            f"{name} {version}: {download} does not match Galaxy's sha256 (not retried):"
            f" expected {expected}, actual {actual}"
        )
    return download, data


def pin(
    fetch: Fetch, v3: str, name: str, version: str, policy: retry.Policy = retry.DEFAULT
) -> Pin:
    """Pin one collection version from Galaxy.

    Raises:
        PinError: If Galaxy's answer or the artifact does not check out.
    """
    download, data = galaxy_artifact(fetch, v3, name, version, policy)
    try:
        info = artifact.check(name, version, data)
    except artifact.ArtifactError as exc:
        raise PinError(str(exc)) from exc
    return Pin(
        name=name,
        version=version,
        url=download,
        sha256=hashlib.sha256(data).hexdigest(),
        size=len(data),
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


def differences(lock: Mapping[str, Any], pins: Sequence[Pin]) -> list[str]:
    """Every difference between the lock's artifact pins and the ones just made.

    Args:
        lock: ``versions.lock.yml``.
        pins: The pins of the lock's collections, from Galaxy.

    Returns:
        One line per difference; empty when the lock matches.
    """
    locked = lock["ansible_collections"].get("artifacts") or {}
    found = []
    for name in sorted(set(locked) - {p.name for p in pins}):
        found.append(f"{name}: the lock has an artifact of a collection that is not locked")
    for p in pins:
        entry = locked.get(p.name)
        if entry is None:
            found.append(f"{p.name} {p.version}: the lock has no artifact")
            continue
        for key, made in (("url", p.url), ("sha256", p.sha256), ("size", p.size)):
            if entry.get(key) != made:
                found.append(
                    f"{p.name} {p.version}: {key} is {made!r} on Galaxy, "
                    f"{entry.get(key)!r} in the lock"
                )
    return found


def pin_all(
    lock: Mapping[str, Any],
    extra: Mapping[str, str],
    *,
    server: str = GALAXY,
    fetch: Fetch | None = None,
    policy: retry.Policy = retry.DEFAULT,
) -> list[Pin]:
    """Pin every locked collection and every extra one.

    Args:
        lock: ``versions.lock.yml``.
        extra: ``{name: version}`` from ``--with``.
        server: The Galaxy server.
        fetch: Downloads a URL (default: :func:`net.download`; tests replace it).
        policy: Tries each request again after a transport error or a
            malformed answer (:mod:`pinka_lockcheck.retry`).

    Returns:
        The pins, sorted by name.

    Raises:
        PinError: If any collection cannot be pinned or a dependency is open.
    """
    get = fetch or net.download
    wanted = {str(k): str(v) for k, v in lock["ansible_collections"]["collections"].items()}
    for name, version in extra.items():
        if name in wanted:
            raise PinError(f"{name} is locked already; change its version in the lock")
        wanted[name] = version
    v3 = v3_root(get, server, policy)
    pins = [pin(get, v3, name, version, policy) for name, version in sorted(wanted.items())]
    problems = dependency_problems(pins)
    if problems:
        raise PinError("; ".join(problems))
    return pins


def run(
    lock: Mapping[str, Any],
    extra: Mapping[str, str],
    *,
    server: str = GALAXY,
    fetch: Fetch | None = None,
    now: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
    policy: retry.Policy = retry.DEFAULT,
) -> str:
    """The output for the lock: :func:`pin_all`, rendered (``now`` is the clock)."""
    pins = pin_all(lock, extra, server=server, fetch=fetch, policy=policy)
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
    parser.add_argument(
        "--check",
        action="store_true",
        help="also fail when the pins differ from the lock's artifacts (CI)",
    )
    args = parser.parse_args(argv)
    if args.check and args.extra:
        parser.error("--check compares the lock's own collections; --with does not apply")
    try:
        lock = checks.load(args.lock)
        pins = pin_all(lock, _extra(args.extra), server=args.server)
    except (PinError, net.FetchError, tarfile.TarError, ValueError) as exc:
        sys.stderr.write(f"lock-collections: {exc}\n")
        return 1
    sys.stdout.write(render(pins, args.server, dt.datetime.now(dt.UTC)))
    if not args.check:
        return 0
    found = differences(lock, pins)
    for line in found:
        sys.stderr.write(f"lock-collections: differs from the lock: {line}\n")
    if found:
        return 1
    sys.stdout.write(f"# check: the lock's {len(pins)} artifact pins match Galaxy\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
