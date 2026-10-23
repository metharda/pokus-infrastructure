"""Files generated from ``versions.lock.yml`` beyond bootstrap's pins (BUILD_PROMPT §10.1).

* ``ansible/requirements.yml``: the pinned Ansible collections that bootstrap
  installs (§5.2 step 6).
* ``docs/reference/versions.md``: every pinned artifact, for readers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pinka_gen import pages
from pinka_gen.sources import jsonable, load_yaml

LOCK = "versions.lock.yml"
MARKER = "TODO(verify)"

# Keys under ``python`` in the lock and the environment that holds their packages.
PYTHON_ENVIRONMENTS = (
    ("packages", "/opt/pinka/ansible-venv"),
    ("model_tools", "/opt/pinka/models-venv"),
    ("installer", "/opt/pinka/installer-venv"),
)
GIB = 1024**3


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
        "# Managed by pinka (ansible/requirements.yml). Local changes will be overwritten.",
        "# Generated from versions.lock.yml (ansible_collections) by tools/gen (make generate).",
        "# Bootstrap installs these collections into /opt/pinka/ansible-collections",
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
    rows = [["`/opt/pinka/python`", "CPython", _code(py["python_version"])]]
    for key, environment in PYTHON_ENVIRONMENTS:
        rows += [
            [f"`{environment}`", name, _code(version)] for name, version in sorted(py[key].items())
        ]
    return [
        "\n## Python\n\n",
        "CPython installed by uv into `/opt/pinka/python` and the packages of its "
        "environments: Ansible (hash-locked in `bootstrap/requirements.lock`), the model "
        "download tool (`ansible/roles/models/files/requirements.lock`) and the installer, "
        "which also holds the Ansible packages (`installer/requirements.lock`). ",
        _section_note(py),
        pages.table(["Environment", "Name", "Version"], rows),
    ]


def _containers(lock: dict[str, Any]) -> list[str]:
    section = lock["containers"]
    rows = [
        [
            name,
            f"`{e['image']}`",
            _code(e["tag"]),
            _code(e["digest"]),
            e["platforms"],
            ", ".join(f"{size / GIB:.2f}" for _, size in sorted(e["sizes"].items())),
        ]
        for name, e in section["images"].items()
    ]
    targets = [
        [name, f"`{e['image']}`", _code(e["tag"]), _code(e["digest"]), e["platforms"]]
        for name, e in section.get("target_images", {}).items()
    ]
    built_on = [
        f"`{name}` is built on CUDA {_code(e['cuda'])}"
        for name, e in section["images"].items()
        if "cuda" in e
    ]
    parts = [
        "\n## Container images\n\n",
        "Every image is used by digest; the tag is informational. ",
        _section_note(section),
        pages.table(
            ["Name", "Image", "Tag", "Digest", "Platforms", "Compressed GiB (per platform)"], rows
        ),
        "\nThe requirements computation estimates an image's unpacked size as 2.5 times its "
        "compressed size until it is pulled on the host (BUILD_PROMPT §2.3).\n",
    ]
    if built_on:
        parts.append(
            f"\n{'; '.join(built_on)}: preflight and doctor compare it with the CUDA version "
            "the NVIDIA driver supports (BUILD_PROMPT §2.2).\n"
        )
    if targets:
        parts += [
            "\nPulled inside golden images by blueprints with a container engine "
            "(`docker_images` of `images/<blueprint>/image.yaml`), never on the host:\n\n",
            pages.table(["Name", "Image", "Tag", "Digest", "Platforms"], targets),
        ]
    ci = [
        [name, f"`{e['image']}`", _code(e["tag"]), _code(e["digest"]), e["platforms"]]
        for name, e in section.get("ci_images", {}).items()
    ]
    if ci:
        parts += [
            "\nBase images of the CI fast lane's systemd containers (the provisioning play "
            "in check mode, one per family), never on a lab host:\n\n",
            pages.table(["Name", "Image", "Tag", "Digest", "Platforms"], ci),
        ]
    return parts


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


def _gpu_lists(lock: dict[str, Any]) -> list[str]:
    section = lock["nvidia_driver_gpus"]
    rows = [
        [
            _code(version),
            _code(e["path"]),
            _code(e["sha256"]),
            str(len(e["open_module"])),
            str(len(e["proprietary_only"])),
        ]
        for version, e in section["releases"].items()
    ]
    return [
        "\n## NVIDIA driver GPU lists\n\n",
        _section_note(section),
        "A driver is installed automatically only for a GPU whose PCI device ID is in "
        "the open-module list of the pinned release "
        "([Bare-metal hosts](../providers/baremetal.md#which-gpus-get-a-driver)).\n\n",
        pages.table(
            ["Driver release", "File", "sha256", "Open-module IDs", "Proprietary-only IDs"], rows
        ),
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
    parts += _base_images(lock) + _gpu_lists(lock) + _ssh_keys(lock)
    return "".join(parts)
