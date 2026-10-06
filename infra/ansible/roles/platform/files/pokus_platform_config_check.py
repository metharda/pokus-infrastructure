# Managed by pokus-infra (ansible/roles/platform/files/pokus_platform_config_check.py). Local changes will be overwritten.
"""Check a rendered platform TOML file against the platform's JSON Schema (C9b).

Run by the platform role with the deployed platform's own interpreter
(``<deployment>/.venv/bin/python -I``), which brings ``jsonschema``, as the
``validate`` command of the task that writes ``/etc/pokus/platform/*.toml``,
so a file that does not conform is never installed. The schema comes from the
deployment being installed (``schemas/config/<name>.schema.json``).
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Any


def problems(config: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    """Validate a configuration.

    Args:
        config: Parsed TOML.
        schema: JSON Schema exported by the platform.

    Returns:
        One line per violation, empty if valid.
    """
    import jsonschema  # noqa: PLC0415 - provided by the deployed platform environment

    validator = jsonschema.Draft202012Validator(schema)
    return sorted(
        f"{'/'.join(str(p) for p in error.absolute_path) or '<root>'}: {error.message}"
        for error in validator.iter_errors(config)
    )


def main(argv: Sequence[str]) -> int:
    """Check one file.

    Args:
        argv: Arguments without the program name.

    Returns:
        0 if valid, 1 if not, 2 on usage errors or a missing schema.
    """
    parser = argparse.ArgumentParser(prog="platform-config-check")
    parser.add_argument("--schema", type=Path, required=True)
    parser.add_argument("config", type=Path)
    args = parser.parse_args(list(argv))
    try:
        schema = json.loads(args.schema.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"platform-config-check: no usable schema {args.schema}: {exc}\n")
        return 2
    try:
        config = tomllib.loads(args.config.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        sys.stderr.write(f"platform-config-check: {args.config}: {exc}\n")
        return 1
    found = problems(config, schema)
    for line in found:
        sys.stderr.write(f"platform-config-check: {line}\n")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
