"""`pinka images build` and the images role order and refuse blueprints alike.

The CLI hands the role the chain of a blueprint (``PINKA_IMAGES_BLUEPRINTS``);
the role orders a run's blueprints with its filter ``pinka_image_build_order``.
Both refuse a base blueprint that is not listed, or bases that form a cycle,
with the same messages.
"""

from __future__ import annotations

from typing import Any

import pytest
from pinka_lab import commands as lab_commands
from pinka_lab import config as lab_config

import pinka

# Labs of blueprints, each name with its base blueprint ('' for an upstream image, a
# name no blueprint has for a base the lab does not list).
LABS = [
    {"base": "", "web": "base", "pg": "base"},
    {"a": "", "b": "a", "c": "b", "d": "c"},
    {"c": "b", "b": "x"},
    {"a": "b", "b": "a", "d": "a", "e": "d"},
    {"s": "s", "t": "s"},
    {"a": "b", "b": "c", "c": "a"},
]


def cli_lab(bases: dict[str, str]) -> lab_config.LabConfig:
    return lab_config.LabConfig(
        {
            "images": {
                "blueprints": [
                    {"name": n, "type": "container", "base_blueprint": b or None}
                    for n, b in bases.items()
                ]
            }
        }
    )


def walk(bases: dict[str, str], name: str) -> list[str]:
    """The blueprints from NAME down its bases, until one repeats or is not listed."""
    found: list[str] = []
    current = name
    while current and current in bases and current not in found:
        found.append(current)
        current = bases[current]
    return found


@pytest.mark.parametrize("bases", LABS)
def test_pinka_images_build_follows_the_role_s_rule(bases: dict[str, str]) -> None:
    # `pinka images build <blueprint>` hands the role its chain: the CLI orders and
    # refuses as the role's filter does on the blueprints of that chain.
    specs = {
        n: {"base": {"blueprint": b} if b else {"image": "ubuntu-24.04"}} for n, b in bases.items()
    }
    for name in bases:
        names = walk(bases, name)
        try:
            role: Any = pinka.image_build_order(names, specs)
        except pinka.PinkaFilterError as refused:
            role = ("refused", str(refused))
        try:
            cli: Any = lab_commands.image_chain(cli_lab(bases), name)
        except lab_commands.CommandError as refused:
            cli = ("refused", str(refused))
        assert cli == role, (bases, name)
