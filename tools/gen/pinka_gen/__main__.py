"""Entry point for ``python -m pinka_gen``."""

from __future__ import annotations

import sys

from pinka_gen.cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
