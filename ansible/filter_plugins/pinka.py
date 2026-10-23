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
import ipaddress
import json
import re
import secrets
from collections.abc import Iterable, Mapping, Sequence
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


def driver_supports_gpu(
    packages: Mapping[str, Any], release_key: str, open_module_drivers: Iterable[str]
) -> bool:
    """Whether bootstrap may install the NVIDIA driver automatically for this host's GPU.

    Only for a GPU the open kernel module of the release's pinned driver
    supports (BUILD_PROMPT §2.2, §10.1): preflight lists the pinned driver
    releases that support the GPU (``host_profile.gpu.open_module_drivers``).
    Where the release has no verified driver pin, any of them counts; the play
    then stops for a manual install as before.

    Args:
        packages: ``packages`` section of versions.lock.yml.
        release_key: Release key from ``release_identity``.
        open_module_drivers: Driver releases whose open module supports the GPU.

    Returns:
        True when a driver may be installed automatically.
    """
    drivers = {str(d) for d in open_module_drivers or []}
    entry = (packages.get("nvidia_driver") or {}).get(release_key) if release_key else None
    if not isinstance(entry, Mapping) or any(MARKER in str(v) for v in entry.values()):
        return bool(drivers)
    return any(_strip_epoch(str(v)).split("-", 1)[0] in drivers for v in entry.values())


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


# The --showformat of dpkg-query in the images role's package record
# (roles/images/tasks/build.yml; the tests keep the two identical).
DPKG_FORMAT = "${Status}\\t${Package}\\t${Version}\\t${Architecture}\\n"
DPKG_FIELDS = 4


def dpkg_packages(text: str | None) -> list[dict[str, str]]:
    """The installed packages of a golden image, from dpkg-query.

    Args:
        text: Output of ``dpkg-query --show`` with the format ``DPKG_FORMAT``
            (the status, the package name, its version and its architecture,
            tab-separated, one package per line).

    Returns:
        ``{"name", "version", "architecture"}`` of every installed package
        (status ``installed``; removed packages that left their configuration
        files are not installed), sorted by name and architecture.

    Raises:
        PinkaFilterError: If a line does not have the four fields, a field is
            empty, or a package appears twice.
    """
    packages: dict[tuple[str, str], dict[str, str]] = {}
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) != DPKG_FIELDS or not all(field.strip() for field in fields):
            raise PinkaFilterError(f"not a dpkg-query line of four fields: {line!r}")
        status, name, version, architecture = (field.strip() for field in fields)
        if status.split()[-1] != "installed":
            continue
        key = (name, architecture)
        if key in packages:
            raise PinkaFilterError(f"package {name}:{architecture} listed twice")
        packages[key] = {"name": name, "version": version, "architecture": architecture}
    return [packages[key] for key in sorted(packages)]


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


def vault_keys(
    postgres_passwords: Mapping[str, Any] | None, platform_secrets: Mapping[str, Any] | None
) -> list[str]:
    """The keys the vault holds for a platform, never their values.

    Args:
        postgres_passwords: ``vault_postgres_passwords``.
        platform_secrets: ``vault_platform_secrets`` (per service, per secret).

    Returns:
        Dotted keys such as ``vault_postgres_passwords.<role>`` and
        ``vault_platform_secrets.<service>.<name>``, sorted.
    """
    keys = [f"vault_postgres_passwords.{role}" for role in (postgres_passwords or {})]
    for service, named in (platform_secrets or {}).items():
        if isinstance(named, Mapping):
            keys += [f"vault_platform_secrets.{service}.{name}" for name in named]
    return sorted(keys)


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


def researcher_memberships(
    researchers: Sequence[Mapping[str, Any]], existing: Sequence[str]
) -> list[dict[str, str]]:
    """The lab groups pinka adds to researcher accounts that existed before it (§6.1, §7.21).

    Args:
        researchers: The ``researchers`` list (``name``, ``admin``).
        existing: Names of the accounts that existed before pinka.

    Returns:
        ``{user, group}`` for ``pinka`` and, for admins, ``pinka-admin``.
    """
    found: list[dict[str, str]] = []
    for researcher in researchers:
        name = str(researcher["name"])
        if name not in existing:
            continue
        groups = ["pinka", "pinka-admin"] if researcher.get("admin") else ["pinka"]
        found.extend({"user": name, "group": group} for group in groups)
    return found


def key_entries(text: str, user: str) -> list[dict[str, str]]:
    """The keys of a ``users/<name>.pub`` file as install record entries (§7.21).

    Args:
        text: The file's content.
        user: The account.

    Returns:
        ``{user, key}`` for every non-empty line that is not a comment.
    """
    lines = (line.strip() for line in text.splitlines())
    return [{"user": user, "key": line} for line in lines if line and not line.startswith("#")]


def subnet(pools: Sequence[Mapping[str, Any]], index: int) -> str:
    """The ``index``-th subnet of the first Docker address pool (§7.6).

    pinka's Compose networks take explicit subnets from its own pool, the
    same on every install, instead of Docker's ``default-address-pools``.

    Args:
        pools: ``docker_address_pools`` (``base``, ``size``).
        index: Which subnet of the pool.

    Returns:
        The subnet, for example ``172.30.0.0/24``.

    Raises:
        ValueError: If the pool has no such subnet.
    """
    pool = pools[0]
    network = ipaddress.ip_network(str(pool["base"]))
    for number, candidate in enumerate(network.subnets(new_prefix=int(pool["size"]))):
        if number == index:
            return str(candidate)
    raise ValueError(f"{network} has no subnet {index} of size /{pool['size']}")


LISTENER_RE = re.compile(r"^(?P<proto>tcp|udp)/(?P<port>[0-9]{1,5})$")
WILDCARDS = ("0.0.0.0", "::")  # noqa: S104 - addresses read from the host profile
DOCKER_PROXY = "docker-proxy"
ACCEPTED_LOGIN_RE = re.compile(
    r"Accepted publickey for (?P<user>[a-z_][a-z0-9_-]*) from (?P<address>[0-9a-fA-F:.]+) port "
)
# The processes that log sshd's authentications (sshd-session from OpenSSH 9.8).
SSHD_COMMS = frozenset({"sshd", "sshd-session"})
TAILNET_V6 = "fd7a:115c:a1e0::/48"


def _family(address: str) -> str:
    """``ip`` or ``ip6``: the nftables address family of an address."""
    return "ip6" if ":" in address else "ip"


def listener_rules(
    access: Mapping[str, Any], close: Sequence[str], closed: Sequence[str] = ()
) -> dict[str, list[Any]]:
    """The pre-existing listeners the firewall keeps and closes (BUILD_PROMPT §7.5).

    On a shared host every listener preflight found stays reachable as it was,
    unless the owner consented to close it; the strict policy already admits
    the listeners marked ``admitted``. A dedicated host keeps and closes none:
    its strict policy is the default. A listener that serves the install's
    access path stays open while that path is open, whatever ``close`` says:
    it closes with the path, once the safeguard held (:func:`install_path`).

    Args:
        access: The install record's (or the host profile's) ``access``.
        close: ``firewall_close_listeners``: ``tcp/8080`` and the like.
        closed: The pre-existing access already closed (``pinka_closed_access``).

    Returns:
        ``kept`` and ``closed``: ``{name, proto, port, address, family}`` per
        listener (``name``: ``tcp/8080``); ``address`` and ``family`` are None
        for a listener on every address, which a dual-stack socket also serves
        over IPv4. ``held``: the names in ``close`` kept open for the install's
        access path.

    Raises:
        PinkaFilterError: If an entry of ``close`` is not ``tcp/<port>`` or
            ``udp/<port>``, names no recorded listener, or names a port Docker
            publishes for a container (forwarded to the container, so the
            host's input policy cannot close it, and a pre-existing Docker is
            never changed).
    """
    listeners = list(access.get("listeners") or [])
    names = {f"{item['proto']}/{item['port']}" for item in listeners}
    for entry in close:
        if not LISTENER_RE.match(str(entry)):
            raise PinkaFilterError(
                f"firewall_close_listeners: {entry!r} is not tcp/PORT or udp/PORT"
            )
        if str(entry) not in names:
            raise PinkaFilterError(
                f"firewall_close_listeners: {entry} is not a listener preflight recorded"
                f" ({', '.join(sorted(names)) or 'none'})"
            )
        if any(
            f"{item['proto']}/{item['port']}" == entry and item.get("process") == DOCKER_PROXY
            for item in listeners
        ):
            raise PinkaFilterError(
                f"firewall_close_listeners: {entry} is a port Docker publishes for a container;"
                " its traffic is forwarded to the container, so pinka's firewall cannot close it"
                " (pinka never changes a Docker Engine that existed before it)"
            )
    rules: dict[str, list[Any]] = {"kept": [], "closed": [], "held": []}
    if access.get("host") != "shared":
        return rules
    path = install_path(access, closed)
    for item in listeners:
        if item.get("admitted"):
            continue
        address = str(item["address"])
        wildcard = address in WILDCARDS
        name = f"{item['proto']}/{item['port']}"
        rule = {
            "name": name,
            "proto": str(item["proto"]),
            "port": int(item["port"]),
            "address": None if wildcard else address,
            "family": None if wildcard else _family(address),
        }
        serves_path = (
            path is not None
            and not path["closed"]
            and rule["proto"] == "tcp"
            and rule["port"] == path["port"]
            and (wildcard or address == path["address"])
        )
        if name in close and serves_path and name not in rules["held"]:
            rules["held"].append(name)
        side = rules["closed" if name in close and not serves_path else "kept"]
        if rule not in side:
            side.append(rule)
    return rules


def install_path(access: Mapping[str, Any], closed: Sequence[str]) -> dict[str, Any] | None:
    """The access path of the install, while it is pinka's to keep (§7.5).

    Args:
        access: The install record's (or the host profile's) ``access``.
        closed: The pre-existing access already closed with consent
            (``pinka_closed_access``).

    Returns:
        ``{address, port, family, user, closed}`` for an SSH session that did
        not come over the tailnet; None for a console install or one over the
        tailnet, which pinka's policy keeps anyway.
    """
    session = access.get("session") or {}
    if session.get("via") != "ssh" or session.get("tailnet"):
        return None
    address = str(session["address"])
    return {
        "address": address,
        "port": int(session["port"]),
        "family": _family(address),
        "user": session.get("user"),
        "closed": "install_path" in closed,
    }


def tailnet_peers(status: str) -> list[str]:
    """The tailnet addresses of the node's peers, never its own.

    Args:
        status: ``tailscale status --json``; anything else gives no peers.

    Returns:
        Each peer's ``TailscaleIPs``, sorted.
    """
    try:
        data = json.loads(status)
    except ValueError:
        return []
    peers = (data.get("Peer") or {}) if isinstance(data, dict) else {}
    found = {
        str(address)
        for peer in peers.values()
        if isinstance(peer, dict)
        for address in peer.get("TailscaleIPs") or []
    }
    return sorted(found)


def tailnet_logins(
    journal: str,
    researchers: Iterable[str],
    peers: Iterable[str],
    tailnet: str = "100.64.0.0/10",
) -> list[dict[str, str]]:
    """The researchers' SSH key logins over the tailnet, from sshd's journal.

    The safeguard of §7.5: the install's access path closes only after a
    successful key login of a researcher from a tailnet address in the same
    run. A line counts only when journald attributes it to root and to sshd
    (``_UID`` 0, ``_COMM`` ``sshd`` or ``sshd-session``: fields the sender
    cannot set), its message is sshd's ``Accepted publickey for <user> from
    <address> port``, the user is a researcher, and the address is one of a
    current tailnet peer's (``tailscale status``): not the host's own, which
    a login over loopback would show, and not an address of the tailnet's
    range on another network (carrier-grade NAT uses it too).

    Args:
        journal: sshd's journal since the run began, ``journalctl --output=json``.
        researchers: The researchers' names.
        peers: The tailnet addresses of the node's peers (:func:`tailnet_peers`).
        tailnet: The tailnet's IPv4 range (its IPv6 range is fd7a:115c:a1e0::/48).

    Returns:
        ``{user, address}`` per such login, in order.
    """
    names = set(researchers)
    known = {str(ipaddress.ip_address(p)) for p in peers}
    networks = [ipaddress.ip_network(tailnet), ipaddress.ip_network(TAILNET_V6)]
    found = []
    for line in journal.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("_UID") != "0":
            continue
        message = entry.get("MESSAGE")
        if entry.get("_COMM") not in SSHD_COMMS or not isinstance(message, str):
            continue
        match = ACCEPTED_LOGIN_RE.match(message)
        if match is None or match.group("user") not in names:
            continue
        try:
            address = ipaddress.ip_address(match.group("address"))
        except ValueError:
            continue
        in_tailnet = any(address.version == n.version and address in n for n in networks)
        if in_tailnet and str(address) in known:
            found.append({"user": match.group("user"), "address": str(address)})
    return found


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
            "pinka_driver_supports_gpu": driver_supports_gpu,
            "pinka_package_specs": package_specs,
            "pinka_repo_url": repo_url,
            "pinka_apt_pin_block": apt_pin_block,
            "pinka_fingerprints": fingerprint_from_colons,
            "pinka_scram_verifier": scram_verifier,
            "pinka_scram_matches": scram_matches,
            "pinka_config_hashes": config_hashes,
            "pinka_image_by_alias": image_by_alias,
            "pinka_dpkg_packages": dpkg_packages,
            "pinka_to_toml": to_toml,
            "pinka_socket_specs": socket_specs,
            "pinka_unit_hardening": unit_hardening,
            "pinka_cpuset_cpus": cpuset_cpus,
            "pinka_vault_keys": vault_keys,
            "pinka_researcher_memberships": researcher_memberships,
            "pinka_key_entries": key_entries,
            "pinka_subnet": subnet,
            "pinka_listener_rules": listener_rules,
            "pinka_install_path": install_path,
            "pinka_tailnet_logins": tailnet_logins,
            "pinka_tailnet_peers": tailnet_peers,
        }
