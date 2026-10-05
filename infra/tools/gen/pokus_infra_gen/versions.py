"""Files generated from ``versions.lock.yml`` beyond bootstrap's pins (BUILD_PROMPT §10.1).

* ``ansible/requirements.yml``: the pinned Ansible collections that bootstrap
  installs (§5.2 step 6).
* ``docs/reference/versions.md``: every pinned artifact, for readers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pokus_infra_gen import pages
from pokus_infra_gen.sources import jsonable, load_yaml

LOCK = "versions.lock.yml"
MARKER = "TODO(verify)"


def _lock(root: Path) -> dict[str, Any]:
    data = jsonable(load_yaml(root / LOCK))
    if not isinstance(data, dict):
        raise TypeError(f"{LOCK} must be a mapping")
    return data


def requirements_yml(root: Path) -> str:
    """Render ``ansible/requirements.yml``.

    Args:
        root: Component root.

    Returns:
        YAML text.
    """
    collections = _lock(root)["ansible_collections"]["collections"]
    lines = [
        "# Managed by pokus-infra (ansible/requirements.yml). Local changes will be overwritten.",
        "# Generated from versions.lock.yml (ansible_collections) by tools/gen (make generate).",
        "# Bootstrap installs these collections into /opt/pokus/ansible-collections",
        "# (BUILD_PROMPT §5.2 step 6).",
        "collections:",
    ]
    for name in sorted(collections):
        lines += [f"  - name: {name}", f'    version: "{collections[name]}"']
    return "\n".join(lines) + "\n"


def _code(value: Any) -> str:  # noqa: ANN401 - YAML value
    text = str(value)
    return text if text == MARKER else f"`{text}`"


def _section_note(section: dict[str, Any]) -> str:
    source = str(section["source"]).replace("<", "&lt;").replace(">", "&gt;")
    return f"Verified on {section['verified_on']}. Source: {source}\n\n"


def _binaries(lock: dict[str, Any]) -> list[str]:
    rows = [
        [name, _code(entry["version"]), arch, _code(asset["url"]), _code(asset["sha256"])]
        for name, entry in lock["binaries"].items()
        for arch, asset in entry["assets"].items()
    ]
    return [
        "## Binaries\n\n",
        "Downloaded by bootstrap or the roles and checked against the sha256.\n\n",
        pages.table(["Name", "Version", "Architecture", "URL", "sha256"], rows),
    ]


def _python(lock: dict[str, Any]) -> list[str]:
    py = lock["python"]
    rows = [["CPython", _code(py["python_version"])]]
    rows += [[name, _code(version)] for name, version in sorted(py["packages"].items())]
    return [
        "\n## Python\n\n",
        "CPython installed by uv into `/opt/pokus/python` and the packages of the Ansible "
        "environment (hash-locked in `bootstrap/requirements.lock`). ",
        _section_note(py),
        pages.table(["Name", "Version"], rows),
    ]


def _containers(lock: dict[str, Any]) -> list[str]:
    section = lock["containers"]
    rows = [
        [name, f"`{e['image']}`", _code(e["tag"]), _code(e["digest"]), e["platforms"]]
        for name, e in section["images"].items()
    ]
    return [
        "\n## Container images\n\n",
        "Every image is used by digest; the tag is informational. ",
        _section_note(section),
        pages.table(["Name", "Image", "Tag", "Digest", "Platforms"], rows),
    ]


def _keys(lock: dict[str, Any]) -> list[str]:
    section = lock["repository_keys"]
    rows = [
        [name, ", ".join(_code(u) for u in e["urls"]), _code(e["fingerprint"])]
        for name, e in section["keys"].items()
    ]
    return [
        "\n## Repository keys\n\n",
        _section_note(section),
        pages.table(["Name", "URLs", "Primary key fingerprint"], rows),
    ]


def _packages(lock: dict[str, Any]) -> list[str]:
    section = lock["packages"]
    out = ["\n## Packages\n\n", _section_note(section)]
    for component, releases in section.items():
        if component in ("verified_on", "source"):
            continue
        rows: list[list[Any]] = []
        for release, versions in releases.items():
            if isinstance(versions, dict):
                rows += [[release, name, _code(v)] for name, v in versions.items()]
            else:
                rows.append([release, "", _code(versions)])
        out += [f"### {component}\n\n", pages.table(["Release", "Package", "Version"], rows), "\n"]
    return out


def _collections(lock: dict[str, Any]) -> list[str]:
    section = lock["ansible_collections"]
    rows = [[name, _code(v)] for name, v in sorted(section["collections"].items())]
    return [
        "## Ansible collections\n\n",
        _section_note(section),
        pages.table(["Collection", "Version"], rows),
    ]


def _base_images(lock: dict[str, Any]) -> list[str]:
    section = lock["incus_base_images"]
    rows = [
        [name, f"`{e['remote']}:{e['alias']}`", e["type"], arch, _code(fp)]
        for name, e in section["images"].items()
        for arch, fp in e["fingerprints"].items()
    ]
    return [
        "\n## Incus base images\n\n",
        _section_note(section),
        pages.table(["Name", "Image", "Type", "Architecture", "Fingerprint"], rows),
    ]


def _ssh_keys(lock: dict[str, Any]) -> list[str]:
    section = lock["github_ssh_host_keys"]
    rows = [[kind, _code(e["fingerprint"])] for kind, e in section["keys"].items()]
    return [
        "\n## GitHub SSH host keys\n\n",
        _section_note(section),
        pages.table(["Key type", "Fingerprint"], rows),
    ]


def versions_page(root: Path) -> str:
    """Render ``docs/reference/versions.md``.

    Args:
        root: Component root.

    Returns:
        Page text.
    """
    lock = _lock(root)
    parts = [
        pages.front_matter(
            "Pinned versions",
            ["operator", "developer", "reader"],
            "Every external artifact pinned in versions.lock.yml.",
        ),
        "# Pinned versions\n\n",
        "Generated from `versions.lock.yml`, the single source of truth for every external "
        f"artifact. A value shown as {MARKER} has not been verified yet; `make lock-check` "
        "fails until it is. How to change a pin: "
        "[How to update pinned versions](../how-to/update-versions.md).\n\n",
        f"Lock file verified on {lock['verified_on']}.\n\n",
    ]
    for build in (_binaries, _python, _containers, _keys, _packages, _collections):
        parts += build(lock)
    parts += _base_images(lock) + _ssh_keys(lock)
    return "".join(parts)
