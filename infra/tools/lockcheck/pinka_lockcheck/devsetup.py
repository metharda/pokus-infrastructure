"""The pinned Ansible collections of ``make setup``, from Galaxy or the fallback.

``make setup`` checks the installed collections against the lock with
``ansible-galaxy collection verify --offline`` (every file against its
``FILES.json``) and, when anything differs, installs them again with
:func:`install_collections`: from Galaxy when it answers (``ansible-galaxy
collection download`` of exactly the locked versions); when it does not, from
the tarballs pinned in ``ansible_collections.fallback`` of
``versions.lock.yml``, each kept only after its sha256 matched. Either way
every artifact passes :mod:`pinka_lockcheck.artifact`, the check
``make lock-collections`` and bootstrap apply, before ``ansible-galaxy``
installs it offline without dependencies. The same verification runs
afterwards.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from pinka_lockcheck import artifact, checks, net

GALAXY = "https://galaxy.ansible.com"

Runner = Callable[[Sequence[str]], int]


def _run(argv: Sequence[str]) -> int:
    return subprocess.run(list(argv), stdin=subprocess.DEVNULL, check=False).returncode  # noqa: S603


def galaxy_server() -> str:
    """The Galaxy server ansible-galaxy uses (``ANSIBLE_GALAXY_SERVER`` or the default)."""
    return os.environ.get("ANSIBLE_GALAXY_SERVER") or GALAXY


def install_collections(
    doc: Mapping[str, Any],
    requirements: Path,
    dest: Path,
    downloads: Path,
    *,
    galaxy: str,
    server: str | None = None,
    reachable: Callable[[str], bool] = net.reachable,
    downloader: checks.Downloader = net.sha256_of_url,
    run: Runner = _run,
) -> str:
    """Install the locked collections into ``dest``.

    Args:
        doc: Lock document.
        requirements: ``ansible/requirements.yml``.
        dest: The collections path (``-p``).
        downloads: Directory for the fallback's tarballs.
        galaxy: The ``ansible-galaxy`` command.
        server: Galaxy server URL (default: :func:`galaxy_server`).
        reachable: ``url -> bool``.
        downloader: ``(url, file) -> sha256``.
        run: ``argv -> exit status``.

    Returns:
        ``galaxy`` or ``fallback``: where the collections came from.

    Raises:
        checks.FetchRefusedError: If an install fails, or the fallback cannot
            use a tarball (not pinned, not verified, a failed download or a
            checksum mismatch).
    """
    url = f"{(server or galaxy_server()).rstrip('/')}/api/"
    wanted = locked(requirements)
    if reachable(url):
        target = downloads / "galaxy"
        shutil.rmtree(target, ignore_errors=True)
        target.mkdir(parents=True)
        argv = [galaxy, "collection", "download", "--no-deps", "-r", str(requirements)]
        if run([*argv, "-p", str(target)]) != 0:
            raise checks.FetchRefusedError(f"ansible-galaxy could not download {requirements}")
        found = sorted(target.glob("*.tar.gz"))
        source = "galaxy"
    else:
        found = checks.fetch_collections(doc, downloads, downloader=downloader)
        source = "fallback"
    by_file = {path.name: path for path in found}
    tarballs = []
    for name, version in wanted:
        tarball = by_file.get(galaxy_file(name, version))
        if tarball is None:
            raise checks.FetchRefusedError(f"no artifact of {name} {version} was downloaded")
        check(name, version, tarball)
        tarballs.append(tarball)
    # Exactly the checked artifacts: --offline contacts no server, --no-deps
    # resolves nothing beyond them.
    argv = [galaxy, "collection", "install", "--force", "--offline", "--no-deps", "-p", str(dest)]
    if run([*argv, *map(str, tarballs)]) != 0:
        raise checks.FetchRefusedError("ansible-galaxy could not install the checked artifacts")
    return source


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
