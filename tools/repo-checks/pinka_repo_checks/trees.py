"""Third-party and generated trees of the repository.

No check reads them and no formatter or linter may change them: the pinned
Ansible collections (`.ansible-collections`, verified against the lock by
`make setup`), the Python and Node environments, the built site and the build
outputs. Every tool's configuration names them explicitly;
`tests/test_dev_tools.py` keeps the configurations in line with this list.

This module uses the Python standard library only.
"""

from __future__ import annotations

from pathlib import PurePath

THIRD_PARTY_TREES = (".ansible-collections", ".venv", "node_modules", "site", "build")


def in_third_party_tree(relative: PurePath) -> bool:
    """Tell whether a path relative to the repository root lies in such a tree.

    Args:
        relative: Path relative to the repository (component) root.

    Returns:
        True if its first component is one of the trees.
    """
    return bool(relative.parts) and relative.parts[0] in THIRD_PARTY_TREES
