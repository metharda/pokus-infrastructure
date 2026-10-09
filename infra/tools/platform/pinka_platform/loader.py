"""Reading a manifest safely: size limit, UTF-8, a YAML subset without surprises.

The manifest comes from a platform release, so it is untrusted input. It is
read with PyYAML's safe loader plus three restrictions: anchors and aliases
are refused (no alias expansion attacks, nothing hidden by reference),
duplicate keys are refused (a later key cannot silently replace an earlier
one), and every mapping key must be a string.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from pinka_platform.problems import ManifestError, Problem

MANIFEST_NAME = "platform-manifest.yaml"


class _StrictLoader(yaml.SafeLoader):
    """PyYAML's safe loader without anchors, aliases, duplicate or non-string keys."""

    def compose_node(self, parent: Any, index: Any) -> Any:  # noqa: ANN401 - PyYAML's API
        event: Any = self.peek_event()  # type: ignore[no-untyped-call]
        mark: Any = event.start_mark
        if isinstance(event, yaml.AliasEvent):
            raise yaml.constructor.ConstructorError(None, None, "aliases are not allowed", mark)
        if getattr(event, "anchor", None) is not None:
            raise yaml.constructor.ConstructorError(None, None, "anchors are not allowed", mark)
        return super().compose_node(parent, index)

    def construct_mapping(self, node: Any, deep: bool = False) -> dict[Any, Any]:  # noqa: ANN401
        seen: set[Any] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str):
                raise yaml.constructor.ConstructorError(
                    None, None, f"mapping key {key!r} is not a string", key_node.start_mark
                )
            if key in seen:
                raise yaml.constructor.ConstructorError(
                    None, None, f"duplicate key {key!r}", key_node.start_mark
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def load_text(text: str) -> dict[str, Any]:
    """Parse manifest text.

    Args:
        text: The manifest.

    Returns:
        The manifest as a mapping.

    Raises:
        ManifestError: Not valid YAML, or not a mapping at the top.
    """
    try:
        data = yaml.load(text, Loader=_StrictLoader)  # noqa: S506 - a subclass of SafeLoader
    except yaml.YAMLError as exc:
        raise ManifestError(Problem("yaml", MANIFEST_NAME, " ".join(str(exc).split()))) from exc
    if not isinstance(data, dict):
        raise ManifestError(Problem("yaml", MANIFEST_NAME, "the manifest is not a mapping"))
    return data


def load_file(path: Path, max_bytes: int) -> dict[str, Any]:
    """Read and parse a manifest file.

    Args:
        path: The manifest file.
        max_bytes: Size limit.

    Returns:
        The manifest as a mapping.

    Raises:
        ManifestError: Missing, not a regular file, too large, not UTF-8 or not valid YAML.
    """
    if path.is_symlink() or not path.is_file():
        raise ManifestError(Problem("manifest-missing", MANIFEST_NAME, "no regular file"))
    size = path.stat().st_size
    if size > max_bytes:
        raise ManifestError(
            Problem("manifest-size", MANIFEST_NAME, f"{size} bytes, more than {max_bytes}")
        )
    try:
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ManifestError(Problem("manifest-encoding", MANIFEST_NAME, "not UTF-8")) from exc
    return load_text(text)
