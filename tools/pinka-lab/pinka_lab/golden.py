"""Golden images: an alias is taken as golden only with the export infra wrote.

The projects ``build`` and ``bench`` use the default project's image store
(``features.images: "false"``), so every client that may create images and
aliases in them (a platform controller's restricted certificate among them)
can create an alias of the golden form ``<blueprint>/<version>`` before infra
builds that version. A client restricted to those projects can neither
delete an image or an alias of the default store, nor point an alias
elsewhere, nor change an image's files; it can rewrite an image's record,
its properties among them. So an alias counts as golden only when its image
is the one its export record (``<export_root>/<blueprint>/<version>/
build.json``, written by infra after its build) names, without an update
source; the fingerprint is the proof. What a client changed in a golden
image's record (its properties, ``public``, ``auto_update``) bootstrap puts
back from the export record, and doctor reports it as a warning. The images
role applies the same rules (the filters
``pinka_golden_image_problems`` and ``pinka_golden_image_drift``; the tests
keep them equal).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from pinka_lab.system import System

# The schema of the export records infra reads: a record names every
# property the build published, read from the export's metadata.yaml
# (ansible/roles/images/tasks/export.yml).
RECORD_SCHEMA = 2
# Values longer than this are shortened in a problem.
SHOWN = 80
PLAIN = re.compile(r"[\x21-\x7e]+")


def shown(value: Any) -> str:  # noqa: ANN401 - any JSON value
    """A value for a message: printable, quoted unless plain, and shortened.

    Args:
        value: A value from Incus or a record (an alias or a property may be
            chosen by any client of the image store).

    Returns:
        The value as it can be printed.
    """
    text = value if isinstance(value, str) and PLAIN.fullmatch(value) else json.dumps(value)
    return text if len(text) <= SHOWN else text[: SHOWN - 3] + "..."


def record_problems(record: Mapping[str, Any] | None) -> list[str]:
    """Why infra does not read an export record (empty when it does, or there is none).

    Args:
        record: An export record, ``build.json`` (empty if none).

    Returns:
        The problems: a record of another schema than ``RECORD_SCHEMA`` (an
        older one names only some of the image's properties).
    """
    if not record:
        return []
    version = record.get("schema_version")
    if type(version) is not int or version != RECORD_SCHEMA:
        return [
            f"its export record has schema {shown(json.dumps(version))}, not {RECORD_SCHEMA};"
            " build the version again"
        ]
    return []


def problems(image: Mapping[str, Any] | None, record: Mapping[str, Any] | None) -> list[str]:
    """Why the image a golden alias points to is not the golden image of its export.

    The projects build and bench use the default project's image store, so
    every client that may create images there (a platform controller's
    restricted certificate among them) can create an alias of the form
    ``<blueprint>/<version>`` before infra builds that version, or put a copy
    of an exported image into the store. Restricted to those projects, it can
    neither delete an image or an alias of the store, nor point an alias
    elsewhere, nor change an image's files, so the fingerprint is the proof:
    an alias is golden only when its image is the one its export record (of
    the schema infra reads) names, without an update source. A client can
    rewrite an image's record (its properties, ``public``, ``auto_update``):
    that is not a refusal, see :func:`drift`.

    Args:
        image: The image the alias points to (empty if none).
        record: The alias's export record, ``build.json`` (empty if none).

    Returns:
        The problems; empty when the image is golden or the alias points to none.
    """
    if not image:
        return []
    fingerprint = shown(image.get("fingerprint"))
    if not record:
        return [f"it points to {fingerprint}, which has no export record"]
    if record.get("fingerprint") != image.get("fingerprint"):
        recorded_fingerprint = shown(record.get("fingerprint"))
        return [f"it points to {fingerprint}, but its export recorded {recorded_fingerprint}"]
    unread = record_problems(record)
    if unread:
        return unread
    # Infra publishes or imports its images from files; only a download has a source.
    source = image.get("update_source")
    if source:
        server = source.get("server") if isinstance(source, Mapping) else source
        return [f"it has an update source ({shown(server)}), which infra's images never have"]
    return []


def drift(image: Mapping[str, Any] | None, record: Mapping[str, Any] | None) -> dict[str, Any]:
    """What a client of the image store changed in a golden image's record.

    A restricted client can rewrite an existing image's record through a new
    image request that names its fingerprint. The export record names every
    property the build published: bootstrap puts back a missing or changed
    one, removes one the build did not set, and sets ``public`` and
    ``auto_update`` to false; doctor reports the change as a warning. An empty
    value is no property, on either side: Incus stores one when it creates an
    image's record, never when it updates one.

    Args:
        image: The image the alias points to (empty if none).
        record: The alias's export record, ``build.json`` (empty if none).

    Returns:
        ``changes``, each change in words, and ``body``, the body of the
        ``PUT /1.0/images/<fingerprint>`` that puts it back (empty when nothing
        changed): the record's properties, ``public`` and ``auto_update``
        false, and the image's profiles as they are. Both are empty for an
        image that is not golden.
    """
    if not image or not record or problems(image, record):
        return {"changes": [], "body": {}}
    wanted = {
        key: value
        for key, value in (record.get("properties") or {}).items()
        if isinstance(value, str) and value
    }
    found = {key: value for key, value in (image.get("properties") or {}).items() if value != ""}
    changes = []
    for key in sorted(set(wanted) | set(found)):
        if key not in wanted:
            changes.append(
                f"it has the property {shown(key)} ({shown(found.get(key))}),"
                " which its build did not set"
            )
        elif key not in found:
            changes.append(
                f"it lacks its property {shown(key)} ({shown(wanted[key])} in its export record)"
            )
        elif found[key] != wanted[key]:
            changes.append(
                f"its property {shown(key)} is {shown(found[key])},"
                f" not {shown(wanted[key])} as in its export record"
            )
    if image.get("public"):
        changes.append("it is public")
    if image.get("auto_update"):
        changes.append("its auto_update is on")
    if not changes:
        return {"changes": [], "body": {}}
    body: dict[str, Any] = {"properties": wanted, "public": False, "auto_update": False}
    profiles = image.get("profiles")
    if isinstance(profiles, list):
        body["profiles"] = profiles
    return {"changes": changes, "body": body}


def review(
    system: System, export_root: str, images: list[dict[str, Any]], blueprints: set[str]
) -> dict[str, dict[str, list[str]]]:
    """Every alias of a blueprint's form in the image store, with what is wrong with it.

    Args:
        system: Host access.
        export_root: Where the exports are (``images.export_root``).
        images: ``incus image list --format json``.
        blueprints: The blueprints of the lab.

    Returns:
        Each alias ``<blueprint>/<version>`` of a known blueprint, with its
        ``problems`` (empty for a golden image) and the ``changes`` a client
        made to a golden image's record (empty when none).
    """
    found: dict[str, dict[str, list[str]]] = {}
    for image in images:
        for alias in image.get("aliases") or []:
            name = str(alias.get("name", ""))
            blueprint, _, version = name.partition("/")
            if blueprint not in blueprints:
                continue
            record: dict[str, Any] = {}
            # Only a version that names one directory below the blueprint's
            # has a record; any other alias of the form is not golden.
            if PLAIN.fullmatch(version) and "/" not in version and version not in (".", ".."):
                path = f"{export_root}/{blueprint}/{version}/build.json"
                text = system.read_text(path)
                if text is None and system.info(path) is not None:
                    found[name] = {
                        "problems": [f"its export record {path} cannot be read"],
                        "changes": [],
                    }
                    continue
                try:
                    loaded = json.loads(text) if text else {}
                except ValueError:
                    loaded = {}
                record = loaded if isinstance(loaded, dict) else {}
            found[name] = {
                "problems": problems(image, record),
                "changes": drift(image, record)["changes"],
            }
    return found
