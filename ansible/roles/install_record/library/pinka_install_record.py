# Managed by pinka (ansible/roles/install_record/library/pinka_install_record.py). Local changes will be overwritten.
"""Ansible module: the install record (BUILD_PROMPT §7.21).

The record ``/var/lib/pinka/install-record.json`` says what existed on the host
before pinka and what pinka added, so that no role changes a pre-existing
component and uninstall removes only pinka's part (§5.4).

* ``state: init`` writes the record once, at the first install: the
  ``preexisting`` part (the components preflight found, the full package list
  with versions, the enabled and the masked units, the accounts and the groups
  with their members, and the content of each file pinka manages that already
  existed) and an empty ``added`` part. On a host that pinka provisioned
  before the record existed (``/etc/pinka/bootstrap-state.json`` is present)
  the record is marked ``legacy``: its ``preexisting`` part then includes
  pinka's own items, so it decides nothing and uninstall removes by pinka's
  names only. An existing record is never rewritten.
* ``state: add`` merges items into ``added``: components pinka installs or
  joins (recorded before it acts), packages (only those absent at the first
  install), holds, accounts, groups, memberships and keys added to
  pre-existing accounts, and ``tailscale serve`` entries. It writes only when
  something is new, so a second run reports no change.
* ``state: read`` returns the record without changing anything.

Check mode writes nothing and returns the record a real run would write.
"""

from __future__ import annotations

import datetime as dt
import grp
import json
import os
import pwd
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

DOCUMENTATION = r"""
module: pinka_install_record
short_description: Write and extend pinka's install record
description:
  - Records what existed before pinka's first install and what pinka added
    (BUILD_PROMPT §7.21). Supports check mode.
options:
  path:
    description: The record file.
    type: path
    default: /var/lib/pinka/install-record.json
  state:
    description: init writes the record once; add merges items into added; read returns it.
    type: str
    choices: [init, add, read]
    default: read
  components:
    description: init only; the components section of the host profile.
    type: dict
    default: {}
  pkg_manager:
    description: init only; the package manager whose package list is recorded.
    type: str
    choices: [apt, dnf, zypper, pacman]
  managed_files:
    description: init only; files pinka writes or edits, recorded with their content when they existed.
    type: list
    elements: path
    default: []
  bootstrap_state:
    description: init only; a file that exists only on hosts pinka provisioned before.
    type: path
    default: /etc/pinka/bootstrap-state.json
  added:
    description: >-
      add only; the items to merge: components (name to true), packages, holds, accounts,
      groups, tailscale_serve (lists of names), memberships ({user, group}) and keys ({user, key}).
    type: dict
    default: {}
author: pinka
"""

SCHEMA_VERSION = 1
LIST_KEYS = ("packages", "holds", "accounts", "groups", "tailscale_serve")
PAIR_KEYS = {"memberships": ("user", "group"), "keys": ("user", "key")}
PACKAGE_LIST = {
    "apt": ["dpkg-query", "-W", "-f", "${Package}\t${Version}\n"],
    "dnf": ["rpm", "-qa", "--qf", "%{NAME}\t%{EPOCH}:%{VERSION}-%{RELEASE}\n"],
    "zypper": ["rpm", "-qa", "--qf", "%{NAME}\t%{EPOCH}:%{VERSION}-%{RELEASE}\n"],
    "pacman": ["pacman", "-Q"],
}
UNITS = ["systemctl", "list-unit-files", "--no-legend", "--plain", "--state"]
MAX_FILE_BYTES = 65536

RunCommand = Callable[[Sequence[str]], tuple[int, str, str]]


def utc_now() -> str:
    """The current time, UTC ISO 8601 with Z.

    The module runs on the target's Python, which is 3.9 in the check-mode
    containers of the Red Hat family: ``datetime.UTC`` needs 3.11.
    """
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: UP017


def parse_packages(text: str) -> dict[str, str]:
    """Parse ``name<whitespace>version`` lines; rpm's ``(none):`` epoch is dropped.

    Args:
        text: The package manager's listing.

    Returns:
        Package names and versions.
    """
    found: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:  # noqa: PLR2004 - name and version
            found[parts[0]] = parts[1].strip().replace("(none):", "")
    return found


def parse_units(text: str) -> list[str]:
    """The unit names of ``systemctl list-unit-files --no-legend --plain``."""
    return sorted(line.split()[0] for line in text.splitlines() if line.split())


def empty_added() -> dict[str, Any]:
    """The ``added`` part of a new record."""
    return {"components": {}, **{key: [] for key in (*LIST_KEYS, *PAIR_KEYS)}}


def read_file(path: str) -> str | None:
    """A managed file's text, or a note when it is too large or not text."""
    try:
        with Path(path).open("rb") as handle:
            raw = handle.read(MAX_FILE_BYTES + 1)
    except OSError as exc:
        return f"<unreadable: {exc.strerror}>"
    if len(raw) > MAX_FILE_BYTES:
        return f"<larger than {MAX_FILE_BYTES} bytes, not kept>"
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return "<not UTF-8 text, not kept>"


def gather(run: RunCommand, pkg_manager: str, managed_files: Sequence[str]) -> dict[str, Any]:
    """Read the host's state for a new record.

    Args:
        run: Runs a command, returning ``(rc, stdout, stderr)``.
        pkg_manager: ``apt``, ``dnf``, ``zypper`` or ``pacman``.
        managed_files: Files pinka writes or edits.

    Returns:
        ``packages``, ``units_enabled``, ``units_masked``, ``users``,
        ``groups`` and ``files``.

    Raises:
        RuntimeError: If the package list cannot be read.
    """
    rc, out, err = run(PACKAGE_LIST[pkg_manager])
    if rc != 0:
        raise RuntimeError(f"cannot list the installed packages: {err.strip()}")
    units: dict[str, list[str]] = {}
    for state in ("enabled", "masked"):
        rc, out_units, _ = run([*UNITS, state])
        units[state] = parse_units(out_units) if rc == 0 else []
    files = {}
    for path in managed_files:
        if os.path.lexists(path):
            files[path] = read_file(path)
    return {
        "packages": parse_packages(out),
        "units_enabled": units["enabled"],
        "units_masked": units["masked"],
        "users": [p.pw_name for p in pwd.getpwall()],
        "groups": {g.gr_name: list(g.gr_mem) for g in grp.getgrall()},
        "files": files,
    }


def build_record(
    *, components: Mapping[str, Any], found: Mapping[str, Any], legacy: bool, created_at: str
) -> dict[str, Any]:
    """Assemble a new record.

    Args:
        components: The host profile's ``components``.
        found: What :func:`gather` read.
        legacy: Pinka provisioned the host before the record existed.
        created_at: UTC time.

    Returns:
        The record.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": created_at,
        "legacy": legacy,
        "preexisting": {
            "components": dict(components),
            "packages": dict(sorted(found["packages"].items())),
            "units_enabled": sorted(found["units_enabled"]),
            "units_masked": sorted(found["units_masked"]),
            "users": sorted(found["users"]),
            "groups": {n: sorted(m) for n, m in sorted(found["groups"].items())},
            "files": dict(sorted(found["files"].items())),
        },
        "added": empty_added(),
    }


def merge(record: dict[str, Any], items: Mapping[str, Any]) -> bool:
    """Merge items into ``record['added']``.

    Args:
        record: The record, changed in place.
        items: Items to add (see the module's ``added`` option).

    Returns:
        Whether anything was new.

    Raises:
        ValueError: If an item has an unknown kind or shape.
    """
    added = record.setdefault("added", empty_added())
    before = record.get("preexisting", {}).get("packages", {})
    changed = False
    for key, value in items.items():
        if key == "components":
            current = added.setdefault("components", {})
            for name, flag in (value or {}).items():
                if flag and not current.get(name):
                    current[name] = True
                    changed = True
        elif key in LIST_KEYS:
            names = added.setdefault(key, [])
            for item in value or []:
                text = str(item)
                if (key == "packages" and text in before) or text in names:
                    continue
                names.append(text)
                changed = True
            names.sort()
        elif key in PAIR_KEYS:
            fields = PAIR_KEYS[key]
            pairs = added.setdefault(key, [])
            for item in value or []:
                if not isinstance(item, Mapping) or sorted(item) != sorted(fields):
                    raise ValueError(f"{key} entries need exactly {', '.join(fields)}")
                entry = {f: str(item[f]) for f in fields}
                if entry not in pairs:
                    pairs.append(entry)
                    changed = True
            pairs.sort(key=lambda e: tuple(e[f] for f in fields))
        else:
            raise ValueError(f"unknown kind of item: {key}")
    return changed


def read_record(path: str) -> dict[str, Any] | None:
    """Read the record, or None when there is none.

    Raises:
        ValueError: If the file is not a JSON object.
    """
    if not Path(path).exists():
        return None
    with Path(path).open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    return data


def write_record(path: str, record: Mapping[str, Any]) -> None:
    """Write the record atomically, readable by root only."""
    directory = Path(path).parent
    directory.mkdir(mode=0o755, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=directory, prefix=".install-record.")
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle, indent=2, sort_keys=True)
            handle.write("\n")
        tmp.chmod(0o600)
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def run_module(params: Mapping[str, Any], *, check_mode: bool, run: RunCommand) -> dict[str, Any]:
    """Do what the module's parameters ask.

    Args:
        params: The module's parameters.
        check_mode: Ansible's check mode: nothing is written.
        run: Runs a command (init only).

    Returns:
        The result: ``changed``, ``record`` and ``exists``.

    Raises:
        ValueError: If the record is unreadable, missing for ``add``, or an
            item is malformed.
        RuntimeError: If the host's state cannot be read.
    """
    path, state = str(params["path"]), str(params["state"])
    record = read_record(path)
    if state == "read":
        return {"changed": False, "record": record or {}, "exists": record is not None}
    if state == "init":
        if record is not None:
            return {"changed": False, "record": record, "exists": True}
        if not params.get("pkg_manager"):
            raise ValueError("state=init needs pkg_manager")
        found = gather(run, str(params["pkg_manager"]), params.get("managed_files") or [])
        record = build_record(
            components=params.get("components") or {},
            found=found,
            legacy=Path(str(params["bootstrap_state"])).exists(),
            created_at=utc_now(),
        )
        if not check_mode:
            write_record(path, record)
        return {"changed": True, "record": record, "exists": not check_mode}
    if record is None:
        if check_mode:
            return {"changed": False, "record": {}, "exists": False}
        raise ValueError(f"{path} does not exist; the install_record role writes it first")
    changed = merge(record, params.get("added") or {})
    if changed and not check_mode:
        write_record(path, record)
    return {"changed": changed, "record": record, "exists": True}


def main() -> None:  # pragma: no cover - exercised on hosts
    """Run the module."""
    from ansible.module_utils.basic import AnsibleModule  # noqa: PLC0415 - only on the host

    module = AnsibleModule(
        argument_spec={
            "path": {"type": "path", "default": "/var/lib/pinka/install-record.json"},
            "state": {"type": "str", "choices": ["init", "add", "read"], "default": "read"},
            "components": {"type": "dict", "default": {}},
            "pkg_manager": {"type": "str", "choices": sorted(PACKAGE_LIST)},
            "managed_files": {"type": "list", "elements": "path", "default": []},
            "bootstrap_state": {"type": "path", "default": "/etc/pinka/bootstrap-state.json"},
            "added": {"type": "dict", "default": {}},
        },
        supports_check_mode=True,
    )

    def run(argv: Sequence[str]) -> tuple[int, str, str]:
        rc, out, err = module.run_command(list(argv))
        return int(rc), str(out), str(err)

    try:
        result = run_module(module.params, check_mode=module.check_mode, run=run)
    except (OSError, ValueError, RuntimeError) as exc:
        module.fail_json(msg=str(exc))
        return
    module.exit_json(**result)


if __name__ == "__main__":
    main()
