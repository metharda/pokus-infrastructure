# Managed by pinka (ansible/roles/install_record/library/pinka_install_record.py). Local changes will be overwritten.
"""Ansible module: the install record.

The record ``/var/lib/pinka/install-record.json`` says what existed on the host
before pinka and what pinka added, so that no role changes a pre-existing
component and uninstall removes only pinka's part.

* ``state: init`` writes the record at the first install: the
  ``preexisting`` part (the components preflight found, the full package list
  with versions, the enabled and the masked units, the accounts and the groups
  with their members, and the content of each file pinka manages that already
  existed with its mode and owner, and the access to the host that existed:
  preflight's ``access``) and an empty ``added`` part. On a host that pinka provisioned
  before the record existed (``/etc/pinka/bootstrap-state.json`` names a run
  that reached the play; a run that stopped before it changed nothing) the
  record is marked ``legacy``: its ``preexisting`` part then includes
  pinka's own items, so it decides nothing and uninstall removes by pinka's
  names only. The ``preexisting`` part of an existing record is never
  rewritten.

  An uninstall without ``--purge-data`` keeps the accounts and groups that
  own the lab's data, the accounts locked, and notes them in the
  kept-accounts file (``/var/lib/pinka/kept-accounts.json``) with their IDs
  and what the lock changed. ``init`` takes them over: each that still has
  the noted UID or GID is pinka's again (in ``added``, not in
  ``preexisting``; a group of Docker's or Incus's packages only leaves
  ``preexisting``), and the accounts' notes go into the record
  (``kept_accounts``). An existing record (an uninstall that did not
  finish) gets them added. Then the file goes. No lock is undone here.
* ``state: unlock`` undoes uninstall's lock of the kept accounts a role
  names (``accounts``): the roles that manage them (users_groups, the
  platform role), before they change them. The login shell and the expiry
  come back as noted; the password is unlocked only where uninstall locked
  it. An account whose lock someone changed since, or another account with
  the name, is left as it is. Each named note then leaves the record; a
  kept account no role names stays locked.
* ``state: add`` merges items into ``added``: components pinka installs or
  joins (recorded before it acts), packages (only those absent at the first
  install), holds, accounts, groups, memberships and keys added to
  pre-existing accounts, ``tailscale serve`` entries, the repository keys
  pinka imported into the package manager's keyring (``repo_keys``), the
  Incus objects pinka created on an Incus it did not install
  (``incus_objects``), the pre-existing access closed with the owner's
  consent (``closed_access``: ``tcp/8080``, ``install_path``), and
  ``changed``: a setting of the host as it was before pinka first changed it
  (the time zone, a sysctl value, a firewalld zone, Incus's server address),
  for uninstall to set back; the first value recorded for a setting
  stays. It writes only when something is new, so a second run reports no
  change.
* ``state: remove`` takes listeners out of ``closed_access`` that are
  reachable again because the owner no longer consents to close them; it
  removes nothing else (what pinka added stays recorded until uninstall),
  and never ``install_path``.
* ``state: read`` returns the record without changing anything.

Check mode writes nothing and returns the record a real run would write.
"""

from __future__ import annotations

import datetime as dt
import grp
import json
import os
import pwd
import stat
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

DOCUMENTATION = r"""
module: pinka_install_record
short_description: Write and extend pinka's install record
description:
  - Records what existed before pinka's first install and what pinka added.
    Supports check mode.
options:
  path:
    description: The record file.
    type: path
    default: /var/lib/pinka/install-record.json
  state:
    description: >-
      init writes the record at the first install; add merges items into added; remove takes
      listeners reachable again out of closed_access; unlock undoes uninstall's lock of the kept
      accounts named; read returns it.
    type: str
    choices: [init, add, remove, unlock, read]
    default: read
  components:
    description: init only; the components section of the host profile.
    type: dict
    default: {}
  access:
    description: init only; the access section of the host profile (listeners, firewall manager, session).
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
    description: >-
      init only; bootstrap's state file, which names a run that reached the play only on
      hosts pinka provisioned before.
    type: path
    default: /etc/pinka/bootstrap-state.json
  originals:
    description: >-
      init only; managed files that bootstrap changed before the record existed, mapped to the
      copy it kept of the original (an empty file named <copy>.absent: there was none).
    type: dict
    default: {}
  kept_accounts:
    description: >-
      init only; the kept-accounts file an uninstall without --purge-data wrote: the accounts
      and groups it kept with the lab's data, taken over by init.
    type: path
    default: /var/lib/pinka/kept-accounts.json
  accounts:
    description: unlock only; the kept accounts the calling role manages.
    type: list
    elements: str
    default: []
  added:
    description: >-
      add only; the items to merge: components (name to true), packages, holds, accounts,
      groups, tailscale_serve, repo_keys, incus_objects, closed_access (lists of names),
      memberships ({user, group}), keys ({user, key}) and changed (setting to its value
      before pinka changed it; the first value recorded stays). remove only; closed_access,
      the listeners reachable again.
    type: dict
    default: {}
author: pinka
"""

SCHEMA_VERSION = 1
LIST_KEYS = (
    "packages",
    "holds",
    "accounts",
    "groups",
    "tailscale_serve",
    "repo_keys",
    "incus_objects",
    "closed_access",
)
# Settings pinka changes, with their value before the first change.
CHANGED = "changed"
PAIR_KEYS = {"memberships": ("user", "group"), "keys": ("user", "key")}
PACKAGE_LIST = {
    "apt": ["dpkg-query", "-W", "-f", "${Package}\t${Version}\n"],
    "dnf": ["rpm", "-qa", "--qf", "%{NAME}\t%{EPOCH}:%{VERSION}-%{RELEASE}\n"],
    "zypper": ["rpm", "-qa", "--qf", "%{NAME}\t%{EPOCH}:%{VERSION}-%{RELEASE}\n"],
    "pacman": ["pacman", "-Q"],
}
UNITS = ["systemctl", "list-unit-files", "--no-legend", "--plain", "--state"]
# What an uninstall without --purge-data kept with the lab's data
# (pinka_uninstall's NAMES["kept_accounts"]), and the shadow database the
# undoing of its lock reads.
KEPT_ACCOUNTS = "/var/lib/pinka/kept-accounts.json"
SHADOW = "/etc/shadow"
# The only items state=remove takes out: listeners reachable again.
REMOVABLE = "closed_access"
ONE_WAY = "install_path"
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
    return {"components": {}, CHANGED: {}, **{key: [] for key in (*LIST_KEYS, *PAIR_KEYS)}}


def file_mode(path: str) -> dict[str, str]:
    """A managed file's mode, owner and group (the names, or the numbers without one)."""
    info = os.lstat(path)
    try:
        owner = pwd.getpwuid(info.st_uid).pw_name
    except KeyError:
        owner = str(info.st_uid)
    try:
        group = grp.getgrgid(info.st_gid).gr_name
    except KeyError:
        group = str(info.st_gid)
    return {"mode": f"{stat.S_IMODE(info.st_mode):04o}", "owner": owner, "group": group}


# Stages of a bootstrap run that ran the play, so changed the host.
PROVISIONED_STAGES = ("ansible", "restore", "doctor")


def provisioned_before(state_file: str) -> bool:
    """Whether bootstrap's state file names a run that reached the play.

    A run that stopped before the play (pre-preflight, tooling, preflight)
    changed nothing, so its state file does not make the record legacy. A
    state file that cannot be read counts as provisioned: a legacy record
    decides nothing, which is the safe side.

    Args:
        state_file: ``/etc/pinka/bootstrap-state.json``.

    Returns:
        True when the host was provisioned before the record existed.
    """
    if not Path(state_file).exists():
        return False
    try:
        with Path(state_file).open(encoding="utf-8") as handle:
            state = json.load(handle)
        return str(state.get("stage", "")) in PROVISIONED_STAGES
    except (OSError, ValueError, AttributeError):
        return True


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


def gather(
    run: RunCommand,
    pkg_manager: str,
    managed_files: Sequence[str],
    originals: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Read the host's state for a new record.

    Args:
        run: Runs a command, returning ``(rc, stdout, stderr)``.
        pkg_manager: ``apt``, ``dnf``, ``zypper`` or ``pacman``.
        managed_files: Files pinka writes or edits.
        originals: Managed files bootstrap changed before the record existed,
            with the copy it kept of each original (``<copy>.absent``: there
            was none).

    Returns:
        ``packages``, ``units_enabled``, ``units_masked``, ``users``,
        ``groups``, ``files`` and ``file_modes``.

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
    files, modes = {}, {}
    for path in managed_files:
        source = (originals or {}).get(path, path)
        if source != path and os.path.lexists(f"{source}.absent"):
            continue
        if not os.path.lexists(source):
            source = path
        if os.path.lexists(source):
            files[path] = read_file(source)
            modes[path] = file_mode(source)
    return {
        "packages": parse_packages(out),
        "units_enabled": units["enabled"],
        "units_masked": units["masked"],
        "users": [p.pw_name for p in pwd.getpwall()],
        "groups": {g.gr_name: list(g.gr_mem) for g in grp.getgrall()},
        "files": files,
        "file_modes": modes,
    }


def read_kept(path: str) -> dict[str, Any] | None:
    """The kept-accounts file, or None when there is none.

    Raises:
        ValueError: If the file is no regular file of this process's user
            (root on a host) that only it can write, or not a JSON object.
    """
    if not os.path.lexists(path):
        return None
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
        raise ValueError(
            f"{path} is no regular file of root's that only root can write: check it, then "
            "delete it or make it root's (chown root:root, chmod 0600)"
        )
    with Path(path).open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    return data


def noted(kept: Mapping[str, Any], key: str, ident: str) -> list[dict[str, Any]]:
    """The kept accounts or groups that still exist with the noted ID (``uid``, ``gid``)."""
    found = []
    for entry in kept.get(key) or []:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("name"), str):
            continue
        try:
            now = (
                pwd.getpwnam(entry["name"]).pw_uid
                if ident == "uid"
                else grp.getgrnam(entry["name"]).gr_gid
            )
        except KeyError:
            continue
        if str(now) == str(entry.get(ident)):
            found.append(dict(entry))
    return found


def shadow_entry(name: str) -> list[str]:
    """An account's fields in the shadow database (password, ..., expire at 6), or []."""
    text = Path(SHADOW).read_text(encoding="utf-8")
    for line in text.splitlines():
        fields = line.split(":")
        if fields[0] == name:
            return fields[1:]
    return []


def undo_lock(
    run: RunCommand, entry: Mapping[str, Any], lock: Mapping[str, Any], *, check_mode: bool
) -> bool:
    """Undo uninstall's lock of a kept account, where the account still carries it.

    The login shell and the expiry come back as noted; the password is
    unlocked only where uninstall locked it. An account whose shell or
    expiry is no longer the lock's (someone changed it since) is left as it
    is.

    Returns:
        Whether the account changed (in check mode: would change).

    Raises:
        RuntimeError: If usermod fails.
    """
    name = str(entry["name"])
    shell = pwd.getpwnam(name).pw_shell
    shadow = shadow_entry(name) + [""] * 7
    if (shell, shadow[6]) != (str(lock.get("shell")), str(lock.get("expire"))):
        return False
    before_shell, before_expire = str(entry.get("shell") or ""), str(entry.get("expire") or "")
    unlock = bool(entry.get("password_locked")) and shadow[0].startswith("!")
    if (shell, shadow[6]) == (before_shell, before_expire) and not unlock:
        return False
    if check_mode:
        return True
    argv = ["usermod", "-s", before_shell, "-e", before_expire]
    rc, out, err = run([*argv, *(["-U"] if unlock else []), name])
    if rc != 0:
        raise RuntimeError(
            f"cannot undo the lock of the kept account {name}: {(err or out).strip()}"
        )
    return True


def take_over(kept: Mapping[str, Any] | None) -> tuple[dict[str, list[str]], list[dict[str, Any]]]:
    """The kept accounts and groups that are pinka's again: those with the noted UID or GID.

    Args:
        kept: The kept-accounts file, or None.

    Returns:
        ``accounts``, ``groups`` (pinka's own) and ``component_groups``
        (Docker's or Incus's), the names taken over; and the notes of the
        accounts, for state=unlock.
    """
    taken: dict[str, list[str]] = {"accounts": [], "groups": [], "component_groups": []}
    if not kept:
        return taken, []
    notes = noted(kept, "accounts", "uid")
    taken["accounts"] = [e["name"] for e in notes]
    for entry in noted(kept, "groups", "gid"):
        key = "component_groups" if entry.get("component") else "groups"
        taken[key].append(entry["name"])
    return {k: sorted(v) for k, v in taken.items()}, notes


def keep_notes(
    record: dict[str, Any], kept: Mapping[str, Any], notes: Sequence[Mapping[str, Any]]
) -> None:
    """Put the taken accounts' notes into the record (``kept_accounts``).

    The note file's entry of an account wins over the record's: the later
    uninstall noted it.
    """
    if not notes:
        return
    part = record.setdefault(
        "kept_accounts", {"lock": dict(kept.get("lock") or {}), "accounts": []}
    )
    newer = {e["name"] for e in notes}
    part["accounts"] = [e for e in part["accounts"] if str(e.get("name")) not in newer] + [
        dict(e) for e in notes
    ]
    part["accounts"].sort(key=lambda e: str(e.get("name")))


def unlock(
    params: Mapping[str, Any],
    record: dict[str, Any] | None,
    *,
    check_mode: bool,
    run: RunCommand,
) -> dict[str, Any]:
    """Undo uninstall's lock of the kept accounts a role names; their notes leave the record.

    Returns:
        ``changed``, ``record``, ``exists`` and ``unlocked`` (the accounts
        whose lock is undone; in check mode, would be).

    Raises:
        ValueError: If the record is missing (outside check mode).
        RuntimeError: If usermod fails.
    """
    path = str(params["path"])
    if record is None:
        if check_mode:
            return {"changed": False, "record": {}, "exists": False, "unlocked": []}
        raise ValueError(f"{path} does not exist; the install_record role writes it first")
    names = {str(n) for n in params.get("accounts") or []}
    part = record.get("kept_accounts") or {}
    lock = part.get("lock") or {}
    left, unlocked, handled = [], [], False
    for entry in part.get("accounts") or []:
        name = str(entry.get("name"))
        if name not in names:
            left.append(entry)
            continue
        handled = True
        try:
            uid = pwd.getpwnam(name).pw_uid
        except KeyError:
            continue  # gone since: nothing to undo
        if str(uid) == str(entry.get("uid")) and undo_lock(run, entry, lock, check_mode=check_mode):
            unlocked.append(name)
    if handled and not check_mode:
        if left:
            record["kept_accounts"]["accounts"] = left
        else:
            del record["kept_accounts"]
        write_record(path, record)
    return {"changed": handled, "record": record, "exists": True, "unlocked": sorted(unlocked)}


def build_record(
    *,
    components: Mapping[str, Any],
    found: Mapping[str, Any],
    legacy: bool,
    created_at: str,
    access: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble a new record.

    Args:
        components: The host profile's ``components``.
        access: The host profile's ``access``.
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
            "access": dict(access or {}),
            "packages": dict(sorted(found["packages"].items())),
            "units_enabled": sorted(found["units_enabled"]),
            "units_masked": sorted(found["units_masked"]),
            "users": sorted(found["users"]),
            "groups": {n: sorted(m) for n, m in sorted(found["groups"].items())},
            "files": dict(sorted(found["files"].items())),
            "file_modes": dict(sorted(found.get("file_modes", {}).items())),
        },
        "added": empty_added(),
    }


def merge_components(added: dict[str, Any], value: Mapping[str, Any] | None) -> bool:
    """Note the components pinka joined or installed (a flag once set stays).

    Args:
        added: The record's ``added`` part, changed in place.
        value: ``{component: true}``.

    Returns:
        Whether a component was new.
    """
    current = added.setdefault("components", {})
    new = False
    for name, flag in (value or {}).items():
        if flag and not current.get(name):
            current[name] = True
            new = True
    return new


def merge_changed(added: dict[str, Any], value: object) -> bool:
    """Note settings with their values before pinka changed them; the first value stays.

    Args:
        added: The record's ``added`` part, changed in place.
        value: ``{setting: value before}``, each value text or null.

    Returns:
        Whether a setting was new.

    Raises:
        ValueError: If ``value`` is not such a mapping.
    """
    if not isinstance(value, Mapping):
        raise ValueError(f"{CHANGED} needs a mapping of settings to their values before")
    settings = added.setdefault(CHANGED, {})
    new = False
    for name, before in value.items():
        if before is not None and not isinstance(before, str):
            raise ValueError(f"{CHANGED}: the value of {name} before must be text or null")
        if str(name) not in settings:
            settings[str(name)] = before
            new = True
    return new


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
            changed = merge_components(added, value) or changed
        elif key == CHANGED:
            changed = merge_changed(added, value) or changed
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


def unmerge(record: dict[str, Any], items: Mapping[str, Any]) -> bool:
    """Take listeners that are reachable again out of ``record['added']['closed_access']``.

    Args:
        record: The record, changed in place.
        items: ``{"closed_access": [names]}``.

    Returns:
        Whether anything was removed.

    Raises:
        ValueError: For any other kind of item, or for ``install_path``, which
            once closed stays closed.
    """
    for key in items:
        if key != REMOVABLE:
            raise ValueError(f"state=remove takes only {REMOVABLE}, not {key}")
    names = record.setdefault("added", empty_added()).setdefault(REMOVABLE, [])
    changed = False
    for item in items.get(REMOVABLE) or []:
        text = str(item)
        if text == ONE_WAY:
            raise ValueError(f"{ONE_WAY} stays closed once closed")
        if text in names:
            names.remove(text)
            changed = True
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


def init(
    params: Mapping[str, Any],
    record: dict[str, Any] | None,
    *,
    check_mode: bool,
    run: RunCommand,
) -> dict[str, Any]:
    """Write the record at the first install, and take over what an uninstall kept with the data.

    Returns:
        ``changed``, ``record``, ``exists`` and ``taken_over``.

    Raises:
        ValueError: If ``pkg_manager`` is missing or the kept-accounts file is not an object.
    """
    path = str(params["path"])
    kept_path = str(params.get("kept_accounts") or KEPT_ACCOUNTS)
    kept = read_kept(kept_path)
    taken, notes = take_over(kept)
    if record is not None:
        # An uninstall that did not finish kept the record: what it kept is added.
        if kept is None:
            return {"changed": False, "record": record, "exists": True, "taken_over": taken}
        merge(record, {"accounts": taken["accounts"], "groups": taken["groups"]})
        keep_notes(record, kept, notes)
        if not check_mode:
            write_record(path, record)
            Path(kept_path).unlink()
        return {"changed": True, "record": record, "exists": True, "taken_over": taken}
    if not params.get("pkg_manager"):
        raise ValueError("state=init needs pkg_manager")
    found = gather(
        run,
        str(params["pkg_manager"]),
        params.get("managed_files") or [],
        params.get("originals") or {},
    )
    found = {
        **found,
        "users": [u for u in found["users"] if u not in taken["accounts"]],
        "groups": {
            g: m
            for g, m in found["groups"].items()
            if g not in taken["groups"] and g not in taken["component_groups"]
        },
    }
    record = build_record(
        components=params.get("components") or {},
        found=found,
        legacy=provisioned_before(str(params["bootstrap_state"])),
        created_at=utc_now(),
        access=params.get("access") or {},
    )
    record["added"]["accounts"] = list(taken["accounts"])
    record["added"]["groups"] = list(taken["groups"])
    if kept is not None:
        keep_notes(record, kept, notes)
    if not check_mode:
        write_record(path, record)
        if kept is not None:
            Path(kept_path).unlink()
    return {"changed": True, "record": record, "exists": not check_mode, "taken_over": taken}


def run_module(params: Mapping[str, Any], *, check_mode: bool, run: RunCommand) -> dict[str, Any]:
    """Do what the module's parameters ask.

    Args:
        params: The module's parameters.
        check_mode: Ansible's check mode: nothing is written.
        run: Runs a command (init only).

    Returns:
        The result: ``changed``, ``record`` and ``exists``.

    Raises:
        ValueError: If the record is unreadable, missing for ``add`` or
            ``remove``, or an item is malformed.
        RuntimeError: If the host's state cannot be read.
    """
    path, state = str(params["path"]), str(params["state"])
    record = read_record(path)
    if state == "read":
        return {"changed": False, "record": record or {}, "exists": record is not None}
    if state == "init":
        return init(params, record, check_mode=check_mode, run=run)
    if state == "unlock":
        return unlock(params, record, check_mode=check_mode, run=run)
    if record is None:
        if check_mode:
            return {"changed": False, "record": {}, "exists": False}
        raise ValueError(f"{path} does not exist; the install_record role writes it first")
    items = params.get("added") or {}
    changed = unmerge(record, items) if state == "remove" else merge(record, items)
    if changed and not check_mode:
        write_record(path, record)
    return {"changed": changed, "record": record, "exists": True}


def main() -> None:  # pragma: no cover - exercised on hosts
    """Run the module."""
    from ansible.module_utils.basic import AnsibleModule  # noqa: PLC0415 - only on the host

    module = AnsibleModule(
        argument_spec={
            "path": {"type": "path", "default": "/var/lib/pinka/install-record.json"},
            "state": {
                "type": "str",
                "choices": ["init", "add", "remove", "unlock", "read"],
                "default": "read",
            },
            "components": {"type": "dict", "default": {}},
            "access": {"type": "dict", "default": {}},
            "pkg_manager": {"type": "str", "choices": sorted(PACKAGE_LIST)},
            "managed_files": {"type": "list", "elements": "path", "default": []},
            "bootstrap_state": {"type": "path", "default": "/etc/pinka/bootstrap-state.json"},
            "originals": {"type": "dict", "default": {}},
            "kept_accounts": {"type": "path", "default": KEPT_ACCOUNTS},
            "accounts": {"type": "list", "elements": "str", "default": []},
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
