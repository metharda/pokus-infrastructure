"""The installer's round-trip YAML writer (BUILD_PROMPT §18.3).

Every file the installer writes with ruamel.yaml's round-trip mode
(host_vars, the researchers of ``group_vars/all/main.yml``, the answers file,
``models.lock.yml``) comes from :func:`round_trip`, so a file's bytes depend
only on its content: an answers replay (``bootstrap.sh --answers``, a process
of its own) writes the same bytes as the TUI, however often the TUI planned
the files before. ``null`` is written out; round-trip mode alone would leave
the value empty. The representer is registered on a subclass:
``add_representer`` is a class method, and on ``RoundTripRepresenter`` itself
it would change every later dump in the process.
"""

from __future__ import annotations

from ruamel.yaml import YAML
from ruamel.yaml.nodes import ScalarNode
from ruamel.yaml.representer import RoundTripRepresenter


def _null(representer: RoundTripRepresenter, _: None) -> ScalarNode:
    """Write ``null`` as ``null``."""
    return representer.represent_scalar("tag:yaml.org,2002:null", "null")


class Representer(RoundTripRepresenter):
    """Round-trip with ``null`` written out."""


Representer.add_representer(type(None), _null)


def round_trip() -> YAML:
    """A round-trip YAML instance that writes ``null`` out.

    Returns:
        ruamel.yaml's round-trip mode with :class:`Representer`; callers set
        the indentation and the line width.
    """
    yaml = YAML(typ="rt")
    yaml.Representer = Representer
    return yaml
