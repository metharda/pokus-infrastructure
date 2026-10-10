"""The services' configuration, checked against the release's schemas (§7.14 step 7).

``platform_settings.<service>`` in the host settings is the non-secret
configuration of a service. A service that names a ``config`` schema gets its
configuration (``{}`` when the host sets none) checked against that schema
before anything on the host changes; a refusal ends bootstrap with exit code
65 like any other. The schema may use the keywords infra's validator
implements plus annotations; a schema with any other keyword is refused, so no
part of it is silently ignored.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pinka_platform import schema
from pinka_platform.problems import Problem

MAX_SCHEMA_BYTES = 1024 * 1024


def _load(path: Path) -> Any:  # noqa: ANN401 - a JSON value
    with path.open("rb") as handle:
        data = handle.read(MAX_SCHEMA_BYTES + 1)
    if len(data) > MAX_SCHEMA_BYTES:
        raise ValueError(f"is larger than {MAX_SCHEMA_BYTES} bytes")
    return json.loads(data.decode("utf-8"))


def check(manifest: Mapping[str, Any], release: Path, settings: object) -> list[Problem]:
    """Check every service's configuration against its schema.

    Args:
        manifest: A valid manifest.
        release: The staged release directory.
        settings: ``platform_settings`` from the host settings (a mapping when valid).

    Returns:
        Problems.
    """
    problems: list[Problem] = []
    services = {s["name"]: s for s in manifest["services"]}
    if not isinstance(settings, Mapping):
        return [Problem("config", "platform_settings", "must be a mapping of service names")]
    for name in sorted(set(settings) - set(services)):
        problems.append(
            Problem(
                "config-unknown", f"platform_settings.{name}", "the release has no such service"
            )
        )
    for name, service in services.items():
        where = f"platform_settings.{name}"
        value = settings.get(name)
        if "config" not in service:
            if value is not None:
                problems.append(
                    Problem("config-unexpected", where, "the service names no config schema")
                )
            continue
        value = {} if value is None else value
        schema_path = release / service["config"]
        try:
            doc = _load(schema_path)
        except (OSError, ValueError) as exc:
            problems.append(Problem("config-schema", service["config"], f"cannot be read: {exc}"))
            continue
        if not isinstance(doc, Mapping):
            problems.append(Problem("config-schema", service["config"], "is not a JSON object"))
            continue
        unsupported = schema.check_keywords(doc, schema.CONFIG_KEYWORDS)
        if unsupported:
            problems.append(
                Problem(
                    "config-schema",
                    service["config"],
                    f"uses keywords infra does not check: {'; '.join(unsupported)}",
                )
            )
            continue
        try:
            found = list(schema.errors(value, doc, allowed=schema.CONFIG_KEYWORDS))
        except (schema.SchemaError, KeyError, re.error) as exc:
            problems.append(Problem("config-schema", service["config"], str(exc)))
            continue
        problems += [
            Problem("config", f"{where}.{path}" if path else where, message)
            for path, message in found
        ]
    return problems
