"""``python -m pokus_installer``."""

import sys

from pokus_installer.cli import main

sys.exit(main(sys.argv[1:]))
