"""pinka preflight: detect the host and decide whether the lab is supported.

The package implements the preflight gate of pokus-infrastructure. It
uses the Python standard library only and stays source-compatible with
Python 3.9, so it also runs on an old system interpreter. Every filesystem,
command and network access goes through :mod:`pinka_preflight.fs` and
:mod:`pinka_preflight.runner`, which makes detection fully testable against
fixture roots (``--root``).
"""

__version__ = "0.1.0"
