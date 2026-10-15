"""The pinned Ansible collections of ``make setup``, from Galaxy or the fallback.

``make setup`` checks the installed collections against the lock with
``ansible-galaxy collection verify --offline`` (every file against its
``FILES.json``) and, when anything differs, installs them again with
:func:`install_collections`: from Galaxy when it answers, exactly as before;
when it does not, from the GitHub release tarballs pinned in
``ansible_collections.fallback`` of ``versions.lock.yml``, each installed only
after its sha256 matched. The same verification runs afterwards either way.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from pinka_lockcheck import checks, net

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
    if reachable(url):
        argv = [
            galaxy,
            "collection",
            "install",
            "--force",
            "-r",
            str(requirements),
            "-p",
            str(dest),
        ]
        if run(argv) != 0:
            raise checks.FetchRefusedError(f"ansible-galaxy could not install {requirements}")
        return "galaxy"
    tarballs = checks.fetch_collections(doc, downloads, downloader=downloader)
    # Exactly the verified artifacts: --offline contacts no server, --no-deps
    # resolves nothing beyond them.
    argv = [galaxy, "collection", "install", "--force", "--offline", "--no-deps", "-p", str(dest)]
    if run([*argv, *map(str, tarballs)]) != 0:
        raise checks.FetchRefusedError("ansible-galaxy could not install the verified tarballs")
    return "fallback"
