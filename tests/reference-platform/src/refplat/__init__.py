"""refplat: the reference platform of Pokus Infrastructure's tests.

A platform written with the Python standard library only, deployed by infra's
own deployment code like any other platform: the controller ``ctl``
(:mod:`refplat.ctl`), its hooks (:mod:`refplat.admin`), the researchers' CLI
(:mod:`refplat.cli`) and the toy training job of ``jobs/tune.py``. The agent
is a package of its own (``packages/refplat-agent``).
"""

from __future__ import annotations

# The manifest's name. The code reads the deployed name from endpoints.toml
# (the agent from the session), so a renamed copy runs unchanged.
PLATFORM = "refplat"
