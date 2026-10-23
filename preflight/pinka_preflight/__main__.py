"""Entry point for ``python -m pinka_preflight``."""

from __future__ import annotations

import sys

from pinka_preflight.cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
