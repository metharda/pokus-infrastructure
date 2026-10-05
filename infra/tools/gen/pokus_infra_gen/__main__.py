"""Entry point for ``python -m pokus_infra_gen``."""

from __future__ import annotations

import sys

from pokus_infra_gen.cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
