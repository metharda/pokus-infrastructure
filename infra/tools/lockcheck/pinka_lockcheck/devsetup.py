"""The pinned Ansible collections of ``make setup``, from Galaxy or a release package.

``make setup`` checks the installed collections against the lock with
``ansible-galaxy collection verify --offline`` (every file against its
``FILES.json``) and, when anything differs, installs them again with
:func:`install_collections`. Galaxy's artifact and its sha256 are the only
pins (owner decision with the "commit" for 3d.7): from Galaxy when it answers
(``ansible-galaxy collection download`` of exactly the locked versions, each
artifact's sha256 and size compared with ``ansible_collections.artifacts``
of ``versions.lock.yml``); when it does not, from the artifacts bundled in a
published release package (``COLLECTIONS_PACKAGE``: its file, its URL or the
unpacked tree), with the same comparison. Either way every artifact passes
:mod:`pinka_lockcheck.artifact`, the check ``make lock-collections`` and
bootstrap apply, before ``ansible-galaxy`` installs it offline without
dependencies. The same verification runs afterwards.
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

from pinka_lockcheck import artifact, bundle, checks, net

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
        fetch: ``url -> content`` (a package given by URL).
        run: ``argv -> exit status``.

    Returns:
        Where the collections came from.

    Raises:
        checks.FetchRefusedError: If Galaxy is unreachable and no package was
            given, or an artifact is missing, differs from its pin or fails
            its check, or an install fails; nothing is installed then.
    """
    url = f"{(server or galaxy_server()).rstrip('/')}/api/"
    wanted = locked(requirements)
    pinned = pins(doc)
    target = downloads / "galaxy"
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    if reachable(url):
        argv = [galaxy, "collection", "download", "--no-deps", "-r", str(requirements)]
        if run([*argv, "-p", str(target)]) != 0:
            raise checks.FetchRefusedError(f"ansible-galaxy could not download {requirements}")
        found = {p.name: p.read_bytes() for p in target.glob("*.tar.gz")}
        result = Installed("galaxy")
    elif package:
        try:
            found = bundle.artifacts_of(package, fetch)
        except bundle.PackageError as exc:
            raise checks.FetchRefusedError(f"the release package: {exc}") from exc
        result = Installed("package")
    else:
        raise checks.FetchRefusedError(
            f"Galaxy is unreachable ({url}) and no release package was given: download the"
            " package of an infra-vX.Y.Z release and run make setup COLLECTIONS_PACKAGE=<its"
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
                    f"{name} {version}: sha256 {actual} and size {len(data)} differ from the"
                    f" lock's pin ({pin[0]}, {pin[1]})"
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
