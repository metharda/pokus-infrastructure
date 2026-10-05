"""Command-line entry point: ``python -m pokus_repo_checks <command> ...``."""

from __future__ import annotations

import sys

from pokus_repo_checks.cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
