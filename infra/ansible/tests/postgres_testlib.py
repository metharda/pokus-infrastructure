"""The PostgreSQL servers installed on the machine, for the tests against a real server.

``ansible/tests/test_postgres_sql.py`` (infra's configuration and the platform
database) and ``tools/platform/tests/test_refplat.py`` (the reference
platform's client and migrations) start throwaway clusters from them. CI
installs 18, the major the lab pins, from PGDG next to the runner's own and
requires it (``PINKA_POSTGRES_REQUIRED``); ``test_postgres_sql.py`` fails when
a required major is missing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def server_major(bindir: Path) -> str:
    """The major version of a server installation (``initdb --version``)."""
    out = subprocess.run(
        [str(bindir / "initdb"), "--version"], capture_output=True, text=True, check=True
    ).stdout
    return out.split()[-1].split(".")[0]


def server_bindirs() -> dict[str, Path]:
    """Every installed server major and its binaries, newest first."""
    candidates = sorted(
        Path("/usr/lib/postgresql").glob("*/bin"), key=lambda p: int(p.parent.name), reverse=True
    )
    if shutil.which("pg_ctl"):
        candidates.insert(0, Path(str(shutil.which("pg_ctl"))).parent)
    found: dict[str, Path] = {}
    for bindir in candidates:
        if (bindir / "initdb").is_file():
            found.setdefault(server_major(bindir), bindir)
    return found


BINDIRS = server_bindirs()
# The majors that must be tested: CI sets 18, the major the lab pins
# (versions.lock.yml containers.postgres), installed from PGDG.
REQUIRED = os.environ.get("PINKA_POSTGRES_REQUIRED", "").split()
