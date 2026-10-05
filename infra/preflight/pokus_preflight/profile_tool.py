"""Host profile helper for bootstrap and the ``preflight_gate`` role (BUILD_PROMPT §2.6, §5.2).

``python -m pokus_preflight.profile_tool`` reads a profile written by
``pokus-preflight --no-write --output FILE`` and offers what the shell side
needs without a JSON parser:

* ``get PROFILE KEY``: print one value (dotted key; strings raw, other values
  as JSON, ``null`` as an empty line).
* ``consent PROFILE degraded|experimental``: print the text an operator
  confirms: every disabled feature with its research impact, or the tier and
  its notes.
* ``diff STORED NEW``: compare outcome, provider and feature flags; exit 0 if
  equal, 1 if they differ (differences on stdout), 3 if a file is unreadable.
* ``override PROFILE NAME...``: set the named overrides to true (consent given
  interactively) and rewrite the file atomically.
* ``store PROFILE``: validate against the schema and store the profile in
  ``/etc/pokus/host-profile.json`` with its archive copy.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import sys
from collections.abc import Sequence
from typing import Any

from pokus_preflight import schema_check
from pokus_preflight.report import (
    ARCHIVE_DIR,
    PROFILE_PATH,
    Overrides,
    atomic_write,
    load_schema,
    to_json,
    write_profile,
)

EXIT_DIFFERENT = 1
EXIT_ERROR = 3
OVERRIDE_NAMES = tuple(f.name for f in dataclasses.fields(Overrides))
COMPARED_KEYS = ("outcome", "provider")


def load_profile(path: str) -> dict[str, Any]:
    """Read a profile.

    Args:
        path: Profile path.

    Returns:
        The profile.

    Raises:
        ValueError: If the file does not contain a JSON object.
    """
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: not a JSON object")
    return data


def lookup(profile: dict[str, Any], key: str) -> Any:  # noqa: ANN401 - JSON value
    """Return the value at a dotted key.

    Args:
        profile: The profile.
        key: Dotted key such as ``overrides.force_unsupported``.

    Returns:
        The value.

    Raises:
        KeyError: If the key does not exist.
    """
    value: Any = profile
    for part in key.split("."):
        if not isinstance(value, dict) or part not in value:
            raise KeyError(key)
        value = value[part]
    return value


def format_value(value: Any) -> str:  # noqa: ANN401 - JSON value
    """Format a value for the shell.

    Args:
        value: Any JSON value.

    Returns:
        Strings unchanged, ``None`` as an empty string, everything else as JSON.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True)


def consent_text(profile: dict[str, Any], kind: str) -> list[str]:
    """Build the lines an operator confirms.

    Args:
        profile: The profile.
        kind: ``degraded`` or ``experimental``.

    Returns:
        Text lines.
    """
    if kind == "degraded":
        lines = ["The following optional features are disabled on this host:"]
        lines += [f"  - {d['feature']}: {d['impact']}" for d in profile["disabled_features"]]
        return lines
    lines = [
        f"This host is on the experimental tier (OS tier: {profile['os_tier']}, "
        f"architecture tier: {profile['arch_tier']}).",
    ]
    lines += [f"  - {note}" for note in profile["notes"]]
    return lines


def differences(stored: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """Compare the fields that must not change silently (§2.6).

    Args:
        stored: Profile stored by the last bootstrap.
        new: Freshly generated profile.

    Returns:
        One line per difference, empty if outcome, provider and features match.
    """
    lines = [
        f"{key}: {format_value(stored.get(key))} -> {format_value(new.get(key))}"
        for key in COMPARED_KEYS
        if stored.get(key) != new.get(key)
    ]
    old_features = stored.get("features") or {}
    new_features = new.get("features") or {}
    for name in sorted(set(old_features) | set(new_features)):
        if old_features.get(name) != new_features.get(name):
            lines.append(
                f"features.{name}: {format_value(old_features.get(name))} -> "
                f"{format_value(new_features.get(name))}"
            )
    return lines


def validated(profile: dict[str, Any]) -> dict[str, Any]:
    """Check a profile against the schema.

    Args:
        profile: The profile.

    Returns:
        The same profile.

    Raises:
        ValueError: If it does not match the schema.
    """
    errors = schema_check.validate(profile, load_schema())
    if errors:
        raise ValueError("host profile does not match its schema: " + "; ".join(errors))
    return profile


def set_overrides(path: str, names: Sequence[str]) -> None:
    """Set overrides to true in a profile file (atomic rewrite, mode 0600).

    Args:
        path: Profile file.
        names: Override names.
    """
    profile = load_profile(path)
    for name in names:
        profile["overrides"][name] = True
    atomic_write(path, to_json(validated(profile)), mode=0o600)


def store(profile: dict[str, Any], paths: Sequence[str] = ()) -> list[str]:
    """Validate and store the profile and its archive copy.

    Args:
        profile: The profile.
        paths: ``(profile, archive_dir)`` for tests.

    Returns:
        Paths written.
    """
    validated(profile)
    when = dt.datetime.strptime(profile["generated_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=dt.timezone.utc
    )
    return write_profile(profile, when, paths)


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser.

    Returns:
        The parser.
    """
    parser = argparse.ArgumentParser(
        prog="pokus-profile", description="Read, compare and store Pokus host profiles."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    get = sub.add_parser("get", help="print one value of a profile")
    get.add_argument("profile")
    get.add_argument("key")
    consent = sub.add_parser("consent", help="print the text an operator confirms")
    consent.add_argument("profile")
    consent.add_argument("kind", choices=("degraded", "experimental"))
    diff = sub.add_parser("diff", help="compare outcome, provider and features")
    diff.add_argument("stored")
    diff.add_argument("new")
    override = sub.add_parser("override", help="set overrides to true in a profile file")
    override.add_argument("profile")
    override.add_argument("names", nargs="+", choices=OVERRIDE_NAMES, metavar="NAME")
    put = sub.add_parser("store", help="store the profile in /etc/pokus")
    put.add_argument("profile")
    put.add_argument("--profile-path", default=PROFILE_PATH, help=argparse.SUPPRESS)
    put.add_argument("--archive-dir", default=ARCHIVE_DIR, help=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str]) -> int:
    """Run one command.

    Args:
        argv: Arguments without the program name.

    Returns:
        0 on success, 1 if ``diff`` found differences, 3 on errors.
    """
    args = build_parser().parse_args(list(argv))
    try:
        if args.command == "get":
            sys.stdout.write(format_value(lookup(load_profile(args.profile), args.key)) + "\n")
        elif args.command == "consent":
            sys.stdout.write("\n".join(consent_text(load_profile(args.profile), args.kind)) + "\n")
        elif args.command == "diff":
            lines = differences(load_profile(args.stored), load_profile(args.new))
            if lines:
                sys.stdout.write("\n".join(lines) + "\n")
                return EXIT_DIFFERENT
        elif args.command == "override":
            set_overrides(args.profile, args.names)
        else:
            written = store(load_profile(args.profile), (args.profile_path, args.archive_dir))
            sys.stdout.write("Profile written: " + ", ".join(written) + "\n")
    except (OSError, ValueError, KeyError) as exc:
        sys.stderr.write(f"pokus-profile: {exc}\n")
        return EXIT_ERROR
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
