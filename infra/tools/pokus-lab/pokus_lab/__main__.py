"""``python -m pokus_lab``."""

import sys

from pokus_lab.cli import main

sys.exit(main(sys.argv[1:]))
