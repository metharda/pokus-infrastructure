"""The installer's round-trip YAML writer.

Every file the installer writes with ruamel.yaml's round-trip mode
(host_vars, the researchers of ``group_vars/all/main.yml``, the answers file,
``models.lock.yml``) comes from :func:`round_trip`, so a file's bytes depend
only on its content: an answers replay (``bootstrap.sh --answers``, a process
of its own) writes the same bytes as the TUI, however often the TUI planned
the files before. ``null`` is written out; round-trip mode alone would leave
the value empty. A string that YAML 1.1 reads as a boolean (``off``, ``on``,
``yes``, ``no``) is quoted: round-trip mode follows YAML 1.2 and would write it
plain, and Ansible's loader (PyYAML, YAML 1.1) would then read ``inference:
off`` as ``false``. The representers are registered on a subclass:
``add_representer`` is a class method, and on ``RoundTripRepresenter`` itself
it would change every later dump in the process.
"""

from __future__ import annotations

from ruamel.yaml import YAML
from ruamel.yaml.nodes import ScalarNode
from ruamel.yaml.representer import RoundTripRepresenter

# The words YAML 1.1 (PyYAML) resolves as booleans that YAML 1.2 reads as strings.
YAML11_BOOLEANS = frozenset(
    {"yes", "Yes", "YES", "no", "No", "NO", "on", "On", "ON", "off", "Off", "OFF"}
)


def _null(representer: RoundTripRepresenter, _: None) -> ScalarNode:
    """Write ``null`` as ``null``."""
    return representer.represent_scalar("tag:yaml.org,2002:null", "null")


def _str(representer: RoundTripRepresenter, value: str) -> ScalarNode:
    """Quote a string that YAML 1.1 would read as a boolean."""
    if value in YAML11_BOOLEANS:
        return representer.represent_scalar("tag:yaml.org,2002:str", value, style='"')
    node: ScalarNode = representer.represent_str(value)
    return node


class Representer(RoundTripRepresenter):
    """Round-trip with ``null`` written out and YAML 1.1 booleans quoted."""


Representer.add_representer(type(None), _null)
Representer.add_representer(str, _str)


def round_trip() -> YAML:
    """A round-trip YAML instance that writes ``null`` out and quotes YAML 1.1 booleans.

    Returns:
        ruamel.yaml's round-trip mode with :class:`Representer`; callers set
        the indentation and the line width.
    """
    yaml = YAML(typ="rt")
    yaml.Representer = Representer
    return yaml
