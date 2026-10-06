"""Jinja filters of pokus-infra (BUILD_PROMPT §19.1, §19.4, §7.8).

Pure functions, unit-tested in ansible/tests/. Only the platform_facts role
calls the distribution filters (``pokus_release_identity``); every other role
reads the facts it sets.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
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


class PokusFilterError(ValueError):
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
        PokusFilterError: If the profile does not identify a supported family.
    """
    family = profile.get("family")
    os_info = profile.get("os") or {}
    distro = str(os_info.get("id") or "")
    if family not in FAMILY_DEFAULT_GROUPS:
        raise PokusFilterError(f"unsupported family {family!r}")
    group = SOURCE_GROUPS.get(distro)
    version = str(os_info.get("version_id") or "")
    codename = str(os_info.get("codename") or "")
    parent = str(os_info.get("parent_release") or "")
    if family == "debian":
        key = parent or codename
        if key not in codenames:
            raise PokusFilterError(f"unknown Debian-family release {key!r}")
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
        PokusFilterError: If the component is unknown.
    """
    if component not in matrix:
        raise PokusFilterError(f"unknown component {component!r}")
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
        PokusFilterError: If a fixed release has no verified pins.
    """
    if not release_key:
        return {}
    entry = (packages.get(component) or {}).get(release_key)
    if not isinstance(entry, Mapping) or any(MARKER in str(v) for v in entry.values()):
        raise PokusFilterError(
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
        PokusFilterError: For an unknown package manager.
    """
    if manager not in ("apt", "dnf", "zypper", "pacman"):
        raise PokusFilterError(f"unknown package manager {manager!r}")
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
        PokusFilterError: If a placeholder has no value.
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
        raise PokusFilterError(f"no value for placeholder {exc} in {template!r}") from exc


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
        raise PokusFilterError("database passwords must be non-empty printable ASCII")
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
        PokusFilterError: If the password is not printable ASCII or the salt is not base64.
    """
    try:
        raw_salt = (
            base64.b64decode(salt, validate=True)
            if salt is not None
            else secrets.token_bytes(SCRAM_SALT_BYTES)
        )
    except binascii.Error as exc:
        raise PokusFilterError(f"salt is not base64: {salt!r}") from exc
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
        PokusFilterError: If the password is not printable ASCII.
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
        PokusFilterError: If a file lies outside ``root`` or directly in it.
    """
    prefix = root.rstrip("/") + "/"
    entries: dict[str, list[str]] = {}
    for item in files:
        path = str(item["path"])
        relative = path[len(prefix) :] if path.startswith(prefix) else ""
        directory, _, rest = relative.partition("/")
        if not rest:
            raise PokusFilterError(f"{path} is not in a directory below {root}")
        entries.setdefault(directory, []).append(f"{rest}\0{item['checksum']}")
    return {
        f"{name.upper().replace('-', '_')}_CONFIG_HASH": hashlib.sha256(
            "\n".join(sorted(lines)).encode("utf-8")
        ).hexdigest()
        for name, lines in sorted(entries.items())
    }


class FilterModule:
    """Ansible filter plugin entry point."""

    def filters(self) -> dict[str, Any]:
        """Return the filters.

        Returns:
            Filter name to function.
        """
        return {
            "pokus_release_identity": release_identity,
            "pokus_component_source": component_source,
            "pokus_lock_packages": lock_packages,
            "pokus_package_specs": package_specs,
            "pokus_repo_url": repo_url,
            "pokus_apt_pin_block": apt_pin_block,
            "pokus_fingerprints": fingerprint_from_colons,
            "pokus_scram_verifier": scram_verifier,
            "pokus_scram_matches": scram_matches,
            "pokus_config_hashes": config_hashes,
        }
