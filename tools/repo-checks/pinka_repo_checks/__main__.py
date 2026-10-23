"""Command-line entry point: ``python -m pinka_repo_checks <command> ...``."""

from __future__ import annotations

import sys

from pinka_repo_checks.cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
