"""The pinned Ansible collections of ``make setup``, from Galaxy or a release package.

``make setup`` checks the installed collections against the lock with
``ansible-galaxy collection verify --offline`` (every file against its
``FILES.json``) and, when anything differs, installs them again with
:func:`install_collections`. Galaxy's artifact and its sha256 are the only
pins: from Galaxy when it answers
(exactly the locked versions, asked from Galaxy's v3 API the way
``ansible-galaxy`` does, by :mod:`pinka_lockcheck.lockcollections`, each
artifact's sha256 equal to Galaxy's and its sha256 and size compared with
``ansible_collections.artifacts`` of ``versions.lock.yml``); when it does
not, from the artifacts bundled in a published release package
(``COLLECTIONS_PACKAGE``: its file, its URL or the unpacked tree), with the
same comparison. Either way every artifact passes
:mod:`pinka_lockcheck.artifact`, the check ``make lock-collections`` and
bootstrap apply, before ``ansible-galaxy`` installs it offline without
dependencies. The same verification runs afterwards.

Every request (whether Galaxy answers, its answers, the downloads, a package
given by URL) is tried again after a transport error or a malformed answer,
with every attempt logged (:mod:`pinka_lockcheck.retry`); a sha256 or size
that differs from Galaxy's or the lock's stops at once with the expected and
the actual value. ``ansible-galaxy`` itself downloads nothing: its own
errors are only text, and its response cache keeps an entry of a failed
request, which made the next request of the same run fail with "Missing
expected 'results'" (CI of ``2f27132``).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tarfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from pinka_lockcheck import artifact, bundle, checks, lockcollections, net, retry

GALAXY = "https://galaxy.ansible.com"

Runner = Callable[[Sequence[str]], int]


def _run(argv: Sequence[str]) -> int:
    return subprocess.run(list(argv), stdin=subprocess.DEVNULL, check=False).returncode  # noqa: S603


def galaxy_server() -> str:
    """The Galaxy server ansible-galaxy uses (``ANSIBLE_GALAXY_SERVER`` or the default)."""
    return os.environ.get("ANSIBLE_GALAXY_SERVER") or GALAXY


@dataclass
class Installed:
    """Where the collections came from.

    Attributes:
        source: ``galaxy`` or ``package``.
        unpinned: Collections installed from Galaxy without a verified pin in
            the lock yet (while a maintainer pins an upgrade); never from a
            package.
    """

    source: str
    unpinned: list[str] = field(default_factory=list)


def pins(doc: Mapping[str, Any]) -> dict[str, tuple[str, int]]:
    """The verified pins of ``ansible_collections.artifacts``: ``name -> (sha256, size)``."""
    found = {}
    for name, entry in (doc["ansible_collections"].get("artifacts") or {}).items():
        sha256, size = entry.get("sha256"), entry.get("size")
        if checks._verified(entry.get("url"), sha256) and isinstance(size, int):
            found[str(name)] = (str(sha256), size)
    return found


def install_collections(
    doc: Mapping[str, Any],
    requirements: Path,
    dest: Path,
    downloads: Path,
    *,
    galaxy: str,
    package: str | None = None,
    server: str | None = None,
    reachable: Callable[[str], bool] = net.reachable,
    fetch: bundle.Fetch = net.download,
    run: Runner = _run,
    policy: retry.Policy = retry.DEFAULT,
) -> Installed:
    """Install the locked collections into ``dest``.

    Args:
        doc: Lock document.
        requirements: ``ansible/requirements.yml``.
        dest: The collections path (``-p``).
        downloads: Directory for the artifacts.
        galaxy: The ``ansible-galaxy`` command.
        package: A release package (file, ``https://`` URL or unpacked tree),
            used only when Galaxy is unreachable.
        server: Galaxy server URL (default: :func:`galaxy_server`).
        reachable: ``url -> bool``.
        fetch: ``url -> content`` (Galaxy's API and artifacts, a package
            given by URL).
        run: ``argv -> exit status`` (the offline install).
        policy: Tries each request again after a transport error or a
            malformed answer.

    Returns:
        Where the collections came from.

    Raises:
        checks.FetchRefusedError: If Galaxy is unreachable and no package was
            given, or an artifact is missing, differs from its pin or fails
            its check, or an install fails; nothing is installed then.
    """
    base = (server or galaxy_server()).rstrip("/")
    url = f"{base}/api/"
    wanted = locked(requirements)
    pinned = pins(doc)
    target = downloads / "galaxy"
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    if answers(url, reachable, policy):
        found = from_galaxy(fetch, base, wanted, policy)
        result = Installed("galaxy")
    elif package:
        try:
            found = bundle.artifacts_of(
                package, lambda link: policy.run(lambda: fetch(link), f"download {link}")
            )
        except bundle.PackageError as exc:
            raise checks.FetchRefusedError(f"the release package: {exc}") from exc
        result = Installed("package")
    else:
        raise checks.FetchRefusedError(
            f"Galaxy is unreachable ({url}) and no release package was given: download the"
            " package of a vX.Y.Z release and run make setup COLLECTIONS_PACKAGE=<its"
            " file or https URL>"
        )
    tarballs = []
    for name, version in wanted:
        file = galaxy_file(name, version)
        data = found.get(file)
        if data is None:
            raise checks.FetchRefusedError(f"no artifact of {name} {version} ({file})")
        pin = pinned.get(name)
        if pin is None and result.source == "package":
            raise checks.FetchRefusedError(
                f"{name} {version} has no verified pin in versions.lock.yml; a release"
                " package's artifacts are installed only against verified pins"
            )
        if pin is None:
            result.unpinned.append(f"{name} {version}")
        else:
            actual = hashlib.sha256(data).hexdigest()
            if (actual, len(data)) != pin:
                raise checks.FetchRefusedError(
                    f"{name} {version}: the artifact differs from the lock's pin (not retried):"
                    f" expected sha256 {pin[0]} and size {pin[1]},"
                    f" actual sha256 {actual} and size {len(data)}"
                )
        tarball = target / file
        tarball.write_bytes(data)
        check(name, version, tarball)
        tarballs.append(tarball)
    # Exactly the checked artifacts: --offline contacts no server, --no-deps
    # resolves nothing beyond them.
    argv = [galaxy, "collection", "install", "--force", "--offline", "--no-deps", "-p", str(dest)]
    if run([*argv, *map(str, tarballs)]) != 0:
        raise checks.FetchRefusedError("ansible-galaxy could not install the checked artifacts")
    return result


def from_galaxy(
    fetch: bundle.Fetch, server: str, wanted: Sequence[tuple[str, str]], policy: retry.Policy
) -> dict[str, bytes]:
    """The wanted artifacts from Galaxy, each with Galaxy's sha256.

    Args:
        fetch: ``url -> content``.
        server: The Galaxy server.
        wanted: ``(name, version)`` pairs.
        policy: Tries each request again after a transport error or a
            malformed answer.

    Returns:
        ``file name -> artifact``, named like ``ansible-galaxy collection
        download`` names them.

    Raises:
        checks.FetchRefusedError: If a request fails for good, an answer stays
            malformed or an artifact differs from Galaxy's sha256.
    """
    found = {}
    try:
        v3 = lockcollections.v3_root(fetch, server, policy)
        for name, version in wanted:
            _, data = lockcollections.galaxy_artifact(fetch, v3, name, version, policy)
            found[galaxy_file(name, version)] = data
    except (lockcollections.PinError, net.FetchError) as exc:
        raise checks.FetchRefusedError(f"Galaxy: {exc}") from exc
    return found


def answers(url: str, reachable: Callable[[str], bool], policy: retry.Policy) -> bool:
    """Whether Galaxy answers, asked again while it does not.

    Args:
        url: Galaxy's API.
        reachable: ``url -> bool``.
        policy: How often to ask.

    Returns:
        False when no attempt got an answer.
    """

    def ask() -> bool:
        if not reachable(url):
            raise net.FetchError(f"{url}: no answer", transient=True)
        return True

    try:
        return policy.run(ask, f"Galaxy: is {url} answering")
    except net.FetchError:
        return False


def locked(requirements: Path) -> list[tuple[str, str]]:
    """The ``(name, version)`` pairs of ``ansible/requirements.yml``, in order."""
    data = yaml.safe_load(requirements.read_text(encoding="utf-8")) or {}
    return [(str(c["name"]), str(c["version"])) for c in data.get("collections") or []]


def galaxy_file(name: str, version: str) -> str:
    """The file name ``ansible-galaxy collection download`` gives an artifact."""
    return f"{name.replace('.', '-')}-{version}.tar.gz"


def check(name: str, version: str, tarball: Path) -> None:
    """Refuse an artifact that :func:`pinka_lockcheck.artifact.check` refuses.

    Raises:
        checks.FetchRefusedError: With the reason; nothing is installed then.
    """
    try:
        artifact.check(name, version, tarball.read_bytes())
    except OSError as exc:
        raise checks.FetchRefusedError(f"{name} {version}: {exc}") from exc
    except (artifact.ArtifactError, tarfile.TarError) as exc:
        raise checks.FetchRefusedError(f"{name} {version}: the artifact is refused: {exc}") from exc
