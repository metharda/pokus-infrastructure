"""Run the bootstrap shell libraries from pytest.

The tests source the libraries into a fresh bash and call single functions.
They never run a stage that touches the host: stage functions are either not
called or replaced by stubs (see ``main_with_stubs``).
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

BOOTSTRAP = Path(__file__).resolve().parents[1]
INFRA = BOOTSTRAP.parent
LIBS = ("exitcodes.sh", "log.sh", "args.sh", "gitstate.sh", "pins.sh", "tooling.sh", "stages.sh")


def prelude() -> str:
    """Return the bash code that loads the libraries like bootstrap.sh does."""
    sources = "\n".join(f"source {shlex.quote(str(BOOTSTRAP / 'lib' / lib))}" for lib in LIBS)
    return f"""
set -Eeuo pipefail
shopt -s inherit_errexit
INFRA_DIR={shlex.quote(str(INFRA))}
BOOTSTRAP_DIR={shlex.quote(str(BOOTSTRAP))}
INTERACTIVE=false
USE_TUI=false
{sources}
pinka_python() {{ {shlex.quote(sys.executable)} "$@"; }}
"""


def bash(
    script: str,
    *,
    stdin: str | None = None,
    env: dict[str, str] | None = None,
    load: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a bash script, by default after loading the libraries.

    Args:
        script: Bash code.
        stdin: Text for standard input.
        env: Extra environment variables.
        load: Load the libraries first.

    Returns:
        The completed process (not checked).
    """
    code = (prelude() if load else "") + script
    return subprocess.run(
        ["bash", "-c", code],
        input=stdin if stdin is not None else "",
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
        check=False,
    )


def q(value: object) -> str:
    """Quote a value for bash.

    Args:
        value: Any value; converted with ``str``.

    Returns:
        The quoted text.
    """
    return shlex.quote(str(value))
