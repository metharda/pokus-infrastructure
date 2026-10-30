"""The installer environment's pins."""

from __future__ import annotations

import re
import tomllib

from ruamel.yaml import YAML

from pinka_installer.paths import INFRA


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def test_dev_environment_pins_the_installer_packages() -> None:
    lock = YAML(typ="safe").load((INFRA / "versions.lock.yml").read_text(encoding="utf-8"))
    project = tomllib.loads((INFRA / "pyproject.toml").read_text(encoding="utf-8"))
    dev = dict(
        (normalize(name), version)
        for name, version in (spec.split("==") for spec in project["dependency-groups"]["dev"])
    )
    for name, version in {**lock["python"]["installer"], **lock["python"]["packages"]}.items():
        if normalize(name) in dev:
            assert dev[normalize(name)] == version, name
    assert {"textual", "ruamel-yaml", "jsonschema"} <= set(dev)


def test_installer_lock_is_hash_locked() -> None:
    text = (INFRA / "installer" / "requirements.lock").read_text(encoding="utf-8")
    pinned = re.findall(r"^([a-z0-9][a-z0-9._-]*)==", text, re.MULTILINE)
    assert {"textual", "ruamel-yaml", "jsonschema", "ansible-core"} <= set(pinned)
    assert text.count("--hash=sha256:") >= len(pinned)
