"""``python -m pinka_installer``."""

import sys

from pinka_installer.cli import main

sys.exit(main(sys.argv[1:]))
