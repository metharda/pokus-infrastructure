"""Jinja filters of pokus-infrastructure (BUILD_PROMPT §19.1, §19.4, §7.8).

Pure functions, unit-tested in ansible/tests/. Only the platform_facts role
calls the distribution filters (``pinka_release_identity``); every other role
reads the facts it sets.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
from collections.abc import Iterable, Mapping
from typing import Any

MARKER = "TODO(verify)"
SOURCE_GROUPS = {
    "ubuntu": "ubuntu",
    "debian": "debian",
    "fedora": "fedora",
    "rhel": "rhel_like",
    "almalinux": "rhel_like",
    "rocky": "rhel_like",
    "centos": "rhel_like",
    "ol": "rhel_like",
    "opensuse-leap": "opensuse",
    "opensuse-tumbleweed": "opensuse",
    "sles": "opensuse",
    "arch": "arch",
}
FAMILY_DEFAULT_GROUPS = {
    "debian": "debian",
    "redhat": "rhel_like",
    "suse": "opensuse",
    "archlinux": "arch",
}
ROLLING = "rolling"
COLON_FINGERPRINT_FIELD = 9  # field 10 of a gpg --with-colons "fpr" record
# PostgreSQL's SCRAM-SHA-256 secret: SCRAM-SHA-256$<iterations>:<salt>$<StoredKey>:<ServerKey>
# (PostgreSQL 18 src/backend/libpq/auth-scram.c, RFC 5802, RFC 7677).
SCRAM_RE = re.compile(
    r"SCRAM-SHA-256\$(?P<iterations>[0-9]+):(?P<salt>[A-Za-z0-9+/=]+)"
    r"\$(?P<stored>[A-Za-z0-9+/=]+):(?P<server>[A-Za-z0-9+/=]+)"
)
SCRAM_ITERATIONS = 4096  # PostgreSQL's scram_iterations default
SCRAM_SALT_BYTES = 16  # PostgreSQL's SCRAM_DEFAULT_SALT_LEN


class PinkaFilterError(ValueError):
    """Raised for inputs the filters cannot interpret."""


def _major(version: str | None) -> str:
    return str(version or "").split(".", 1)[0]


def release_identity(profile: Mapping[str, Any], codenames: Mapping[str, Any]) -> dict[str, Any]:
    """Derive the release identity used for sources and pins from a host profile.

    Args:
        profile: Host profile written by preflight.
        codenames: Debian-family codename table: ``{codename: {distro, version}}``.

    Returns:
        ``distro`` (the parent distribution for derivatives), ``release`` (its
        version), ``codename``, ``major``, ``source_group`` and ``release_key``
        (``<distro>-<release>`` as used in versions.lock.yml, or ``""`` for
        rolling releases, which are not pinned).

    Raises:
        PinkaFilterError: If the profile does not identify a supported family.
    """
    family = profile.get("family")
    os_info = profile.get("os") or {}
    distro = str(os_info.get("id") or "")
    if family not in FAMILY_DEFAULT_GROUPS:
        raise PinkaFilterError(f"unsupported family {family!r}")
    group = SOURCE_GROUPS.get(distro)
    version = str(os_info.get("version_id") or "")
    codename = str(os_info.get("codename") or "")
    parent = str(os_info.get("parent_release") or "")
    if family == "debian":
        key = parent or codename
        if key not in codenames:
            raise PinkaFilterError(f"unknown Debian-family release {key!r}")
        entry = codenames[key]
        distro, version, codename = str(entry["distro"]), str(entry["version"]), key
        group = distro
    elif group is None:  # derivative of another family: use the parent identity
        group = FAMILY_DEFAULT_GROUPS[family]
        version = parent or version
    if profile.get("release_model") == ROLLING:
        key = ""
    elif group == "rhel_like":
        key = f"el-{_major(version)}"
    elif group == "opensuse":
        key = f"opensuse-leap-{version}"
    else:
        key = f"{distro}-{version}"
    return {
        "distro": distro,
        "release": version,
        "codename": codename,
        "major": _major(version),
        "source_group": group,
        "release_key": key,
    }


def component_source(matrix: Mapping[str, Any], component: str, group: str) -> dict[str, Any]:
    """Return the source of a component for a source group.

    Args:
        matrix: ``components`` of ansible/component-sources.yml.
        component: Component name, for example ``docker``.
        group: Source group from ``release_identity``.

    Returns:
        The entry for the group, else the component's ``default`` entry. An
        entry with a ``verify`` key is not verified yet; callers refuse it.

    Raises:
        PinkaFilterError: If the component is unknown.
    """
    if component not in matrix:
        raise PinkaFilterError(f"unknown component {component!r}")
    entries = matrix[component]
    return dict(entries.get(group) or entries["default"])


def lock_packages(packages: Mapping[str, Any], component: str, release_key: str) -> dict[str, str]:
    """Return the pinned package versions of a component for one release.

    Args:
        packages: ``packages`` section of versions.lock.yml.
        component: Component name.
        release_key: Release key from ``release_identity`` (``""`` for rolling releases).

    Returns:
        ``{package: version}``; empty for rolling releases.

    Raises:
        PinkaFilterError: If a fixed release has no verified pins.
    """
    if not release_key:
        return {}
    entry = (packages.get(component) or {}).get(release_key)
    if not isinstance(entry, Mapping) or any(MARKER in str(v) for v in entry.values()):
        raise PinkaFilterError(
            f"versions.lock.yml has no verified pins for {component} on {release_key}; "
            "verify them (docs/how-to/update-versions.md) before provisioning this release"
        )
    return {str(k): str(v) for k, v in entry.items()}


def _strip_epoch(version: str) -> str:
    return version.split(":", 1)[1] if ":" in version else version


def package_specs(names: Iterable[str], versions: Mapping[str, str], manager: str) -> list[str]:
    """Format package names with their pinned versions for a package manager.

    Args:
        names: Package names.
        versions: Pinned versions (may be empty, or lack some names).
        manager: ``apt``, ``dnf``, ``zypper`` or ``pacman``.

    Returns:
        Install specifications: ``name=version`` (apt, zypper), ``name-version``
        with the epoch kept (dnf), plain names (pacman, unpinned packages).

    Raises:
        PinkaFilterError: For an unknown package manager.
    """
    if manager not in ("apt", "dnf", "zypper", "pacman"):
        raise PinkaFilterError(f"unknown package manager {manager!r}")
    specs = []
    for name in names:
        version = versions.get(name)
        if not version or manager == "pacman":
            specs.append(name)
        elif manager == "dnf":
            specs.append(f"{name}-{version}")
        elif manager == "zypper":
            specs.append(f"{name}={_strip_epoch(version)}")
        else:
            specs.append(f"{name}={version}")
    return specs


def repo_url(
    template: str, identity: Mapping[str, Any], extra: Mapping[str, str] | None = None
) -> str:
    """Fill the placeholders of a repository URL template.

    Args:
        template: URL with ``{distro}``, ``{codename}``, ``{major}`` (and ``extra`` keys).
        identity: Result of ``release_identity``.
        extra: Additional placeholder values.

    Returns:
        The URL.

    Raises:
        PinkaFilterError: If a placeholder has no value.
    """
    values = {
        "distro": identity.get("distro", ""),
        "codename": identity.get("codename", ""),
        "major": identity.get("major", ""),
        **(extra or {}),
    }
    try:
        return template.format(**values)
    except KeyError as exc:
        raise PinkaFilterError(f"no value for placeholder {exc} in {template!r}") from exc


def apt_pin_block(versions: Mapping[str, str]) -> str:
    """Render apt preferences stanzas that hold packages at their pinned versions.

    Args:
        versions: ``{package: version}``.

    Returns:
        Stanzas separated by blank lines (priority 1001 keeps the version even
        when a newer one is available).
    """
    stanzas = [
        f"Package: {name}\nPin: version {version}\nPin-Priority: 1001"
        for name, version in sorted(versions.items())
    ]
    return "\n\n".join(stanzas)


def fingerprint_from_colons(output: str) -> list[str]:
    """Extract primary key fingerprints from ``gpg --with-colons`` output.

    Args:
        output: Output of ``gpg --show-keys --with-colons --with-fingerprint``.

    Returns:
        The fingerprint of every primary key (``pub`` record), in order.
    """
    fingerprints = []
    expect_primary = False
    for line in output.splitlines():
        fields = line.split(":")
        if fields[0] == "pub":
            expect_primary = True
        elif fields[0] == "sub":
            expect_primary = False
        elif fields[0] == "fpr" and expect_primary and len(fields) > COLON_FINGERPRINT_FIELD:
            fingerprints.append(fields[COLON_FINGERPRINT_FIELD].upper())
            expect_primary = False
    return fingerprints


def _scram_keys(password: str, salt: bytes, iterations: int) -> tuple[bytes, bytes]:
    # SASLprep leaves printable ASCII unchanged, so PostgreSQL hashes these bytes.
    if not password or not all(" " <= c <= "~" for c in password):
        raise PinkaFilterError("database passwords must be non-empty printable ASCII")
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("ascii"), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", "sha256").digest()
    server_key = hmac.new(salted, b"Server Key", "sha256").digest()
    return hashlib.sha256(client_key).digest(), server_key


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def scram_verifier(
    password: str, salt: str | None = None, iterations: int = SCRAM_ITERATIONS
) -> str:
    """Return the PostgreSQL SCRAM-SHA-256 secret of a password.

    ``ALTER ROLE ... PASSWORD '<secret>'`` stores a secret in this format as
    is, so the clear-text password never reaches the server or its logs.

    Args:
        password: Printable ASCII password.
        salt: Base64 salt; ``None`` draws 16 random bytes like PostgreSQL.
        iterations: PBKDF2 iteration count.

    Returns:
        ``SCRAM-SHA-256$<iterations>:<salt>$<StoredKey>:<ServerKey>``.

    Raises:
        PinkaFilterError: If the password is not printable ASCII or the salt is not base64.
    """
    try:
        raw_salt = (
            base64.b64decode(salt, validate=True)
            if salt is not None
            else secrets.token_bytes(SCRAM_SALT_BYTES)
        )
    except binascii.Error as exc:
        raise PinkaFilterError(f"salt is not base64: {salt!r}") from exc
    stored, server = _scram_keys(password, raw_salt, iterations)
    return f"SCRAM-SHA-256${iterations}:{_b64(raw_salt)}${_b64(stored)}:{_b64(server)}"


def scram_matches(password: str, secret: str | None) -> bool:
    """Tell whether a stored PostgreSQL SCRAM-SHA-256 secret belongs to a password.

    Args:
        password: Printable ASCII password.
        secret: ``pg_authid.rolpassword`` (empty or ``None`` when the role has no password).

    Returns:
        True if logging in with the password would succeed.

    Raises:
        PinkaFilterError: If the password is not printable ASCII.
    """
    match = SCRAM_RE.fullmatch(secret or "")
    if match is None:
        _scram_keys(password, b"", 1)  # still reject invalid passwords
        return False
    try:
        salt = base64.b64decode(match["salt"], validate=True)
        stored = base64.b64decode(match["stored"], validate=True)
        server = base64.b64decode(match["server"], validate=True)
    except binascii.Error:
        return False
    want_stored, want_server = _scram_keys(password, salt, int(match["iterations"]))
    return hmac.compare_digest(stored, want_stored) and hmac.compare_digest(server, want_server)


def config_hashes(files: Iterable[Mapping[str, Any]], root: str) -> dict[str, str]:
    """Hash a Compose project's configuration, one value per directory under config/.

    The compose role writes the values into the project's .env file and each
    service carries its directory's hash as a label, so ``docker compose up``
    recreates exactly the services whose bind-mounted configuration changed.

    Args:
        files: ``ansible.builtin.find`` results (``path``, ``checksum``) below ``root``.
        root: The project's config directory.

    Returns:
        ``{"<DIR>_CONFIG_HASH": <sha256>}`` with ``<DIR>`` the upper-cased
        top-level directory name (``-`` becomes ``_``).

    Raises:
        PinkaFilterError: If a file lies outside ``root`` or directly in it.
    """
    prefix = root.rstrip("/") + "/"
    entries: dict[str, list[str]] = {}
    for item in files:
        path = str(item["path"])
        relative = path[len(prefix) :] if path.startswith(prefix) else ""
        directory, _, rest = relative.partition("/")
        if not rest:
            raise PinkaFilterError(f"{path} is not in a directory below {root}")
        entries.setdefault(directory, []).append(f"{rest}\0{item['checksum']}")
    return {
        f"{name.upper().replace('-', '_')}_CONFIG_HASH": hashlib.sha256(
            "\n".join(sorted(lines)).encode("utf-8")
        ).hexdigest()
        for name, lines in sorted(entries.items())
    }


def image_by_alias(images: Iterable[Mapping[str, Any]] | None, alias: str) -> dict[str, Any]:
    """Find the image an Incus alias points to.

    Args:
        images: Output of ``incus image list --format json``.
        alias: Alias name, such as ``_base/1.0.0``.

    Returns:
        The image, or an empty dict if no image carries the alias.
    """
    for image in images or []:
        if any(entry.get("name") == alias for entry in image.get("aliases") or []):
            return dict(image)
    return {}


TOML_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def _toml_key(key: str) -> str:
    return key if TOML_BARE_KEY.match(key) else json.dumps(key)


def _toml_value(value: Any) -> str:  # noqa: ANN401 - TOML value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    if isinstance(value, Mapping):
        pairs = (f"{_toml_key(str(k))} = {_toml_value(v)}" for k, v in value.items())
        return "{" + ", ".join(pairs) + "}"
    raise TypeError(f"no TOML representation for {type(value).__name__}: {value!r}")


def _toml_table(data: Mapping[str, Any], path: list[str], out: list[str]) -> None:
    scalars = [(k, v) for k, v in data.items() if not isinstance(v, Mapping)]
    tables = [(k, v) for k, v in data.items() if isinstance(v, Mapping)]
    if path and (scalars or not tables):
        out.append(f"[{'.'.join(_toml_key(p) for p in path)}]")
    out.extend(f"{_toml_key(str(k))} = {_toml_value(v)}" for k, v in scalars)
    for key, value in tables:
        if out and out[-1] != "":
            out.append("")
        _toml_table(value, [*path, str(key)], out)


def to_toml(data: Mapping[str, Any], header: str = "") -> str:
    """Serialize a mapping as TOML (tables for nested mappings, inline arrays).

    Args:
        data: The document; None values are not representable.
        header: Comment lines put first.

    Returns:
        TOML text ending with a newline.

    Raises:
        TypeError: For values TOML cannot represent (such as None).
    """
    out: list[str] = [f"# {line}" if line else "#" for line in header.splitlines()]
    if out:
        out.append("")
    _toml_table(data, [], out)
    return "\n".join(out).rstrip("\n") + "\n"


def _perm_bits(perms: str) -> int:
    return sum(bit for char, bit in zip(perms, (4, 2, 1), strict=True) if char != "-")


def _perm_text(bits: int) -> str:
    return "".join(char if bits & bit else "-" for char, bit in zip("rwx", (4, 2, 1), strict=True))


def socket_specs(sockets: Iterable[Mapping[str, Any]], acls: bool) -> list[dict[str, Any]]:
    """The effective permissions of platform sockets (contracts.md#sockets, C8).

    Without POSIX ACL support on /run a socket with ACL entries uses its
    fallback group and mode. With ACLs, setfacl recalculates the mask, so the
    group bits ``stat`` reports become the mask (the union of the group class
    entries), and getfacl shows the owning group's own entry.

    Args:
        sockets: Socket entries (path, owner, group, mode, acl, optional fallback).
        acls: Whether /run supports POSIX ACLs.

    Returns:
        One entry per socket: path, owner, group, mode and acl to apply, and
        expect_mode and expect_acl as doctor reads them back.
    """
    specs = []
    for socket in sockets:
        entry = dict(socket)
        if entry.get("acl") and not acls:
            entry.update(entry.get("fallback") or {})
            entry["acl"] = []
        entry.pop("fallback", None)
        mode = int(str(entry["mode"]), 8)
        acl = list(entry.get("acl") or [])
        expect_mode, expect_acl = mode, []
        if acl:
            group_bits = (mode >> 3) & 0o7
            mask = group_bits
            for item in acl:
                mask |= _perm_bits(item.rsplit(":", 1)[1])
            expect_mode = (mode & 0o7707) | (mask << 3)
            expect_acl = [*acl, f"group::{_perm_text(group_bits)}", f"mask::{_perm_text(mask)}"]
        entry["acl"] = acl
        entry["expect_mode"] = f"{expect_mode:04o}"
        entry["expect_acl"] = expect_acl
        specs.append(entry)
    return specs


# systemd hardening every platform service unit carries (BUILD_PROMPT §7.14), as
# `systemctl show` reports it; doctor compares them (systemd.hardening).
UNIT_HARDENING = {
    "NoNewPrivileges": "yes",
    "ProtectSystem": "strict",
    "ProtectHome": "yes",
    "PrivateTmp": "yes",
    "PrivateDevices": "yes",
    "RestrictSUIDSGID": "yes",
    "LockPersonality": "yes",
}


def unit_hardening(
    units: Iterable[Mapping[str, Any]],
    denied: Iterable[str],
    agent_denied: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """What doctor expects of hardened service units.

    Args:
        units: Units with ``unit``, ``account`` and optionally
            ``memory_deny_write_execute`` (default true) and ``agent``.
        denied: InaccessiblePaths of every unit.
        agent_denied: Further InaccessiblePaths of agent units.

    Returns:
        One entry per unit: unit, properties (``systemctl show`` values) and
        inaccessible_paths.
    """
    expected = []
    for unit in units:
        mdwe = unit.get("memory_deny_write_execute", True)
        properties = {
            **UNIT_HARDENING,
            "User": str(unit["account"]),
            "MemoryDenyWriteExecute": "yes" if mdwe else "no",
        }
        paths = [*denied, *(agent_denied if unit.get("agent") else ())]
        expected.append(
            {"unit": unit["unit"], "properties": properties, "inaccessible_paths": paths}
        )
    return expected


CPUSET_RE = re.compile(r"^[0-9]+(-[0-9]+)?(,[0-9]+(-[0-9]+)?)*$")


def cpuset_cpus(spec: str | None) -> list[int]:
    """The CPUs of a Linux cpuset list (``cpuset_services``, ``cpuset_targets``).

    Args:
        spec: Such as ``0-5`` or ``0-3,8``; empty or None for none.

    Returns:
        The CPU numbers, sorted, without duplicates.

    Raises:
        PinkaFilterError: If the list is malformed or a range is reversed.
    """
    text = (spec or "").replace(" ", "")
    if not text:
        return []
    if not CPUSET_RE.match(text):
        raise PinkaFilterError(f"not a cpuset list: {spec!r}")
    cpus: set[int] = set()
    for part in text.split(","):
        first, _, last = part.partition("-")
        low, high = int(first), int(last or first)
        if low > high:
            raise PinkaFilterError(f"reversed range {part!r} in {spec!r}")
        cpus.update(range(low, high + 1))
    return sorted(cpus)


class FilterModule:
    """Ansible filter plugin entry point."""

    def filters(self) -> dict[str, Any]:
        """Return the filters.

        Returns:
            Filter name to function.
        """
        return {
            "pinka_release_identity": release_identity,
            "pinka_component_source": component_source,
            "pinka_lock_packages": lock_packages,
            "pinka_package_specs": package_specs,
            "pinka_repo_url": repo_url,
            "pinka_apt_pin_block": apt_pin_block,
            "pinka_fingerprints": fingerprint_from_colons,
            "pinka_scram_verifier": scram_verifier,
            "pinka_scram_matches": scram_matches,
            "pinka_config_hashes": config_hashes,
            "pinka_image_by_alias": image_by_alias,
            "pinka_to_toml": to_toml,
            "pinka_socket_specs": socket_specs,
            "pinka_unit_hardening": unit_hardening,
            "pinka_cpuset_cpus": cpuset_cpus,
        }
