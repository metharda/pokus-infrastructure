"""Validation rules of the installer (BUILD_PROMPT §18.8).

Every rule is a function that takes the raw value and returns an error message,
or ``None`` when the value is valid. The TUI shows the message next to the
field; ``answers.py`` collects them for an answers file. Nothing here touches
the host except :func:`ssh_keygen_fingerprint`, which runs ``ssh-keygen`` when
it is installed.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import ipaddress
import re
import shutil
import subprocess
import urllib.parse
from collections.abc import Iterable
from dataclasses import dataclass

HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
LABEL_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
TAG_RE = re.compile(r"^tag:[a-z][a-z0-9-]{0,62}$")
HF_REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
SIZE_RE = re.compile(r"^([1-9][0-9]*)(B|KB|MB|GB|TB|KiB|MiB|GiB|TiB)$")
TIME_RE = re.compile(r"^([01][0-9]|2[0-3]):([0-5][0-9])$")
INTERFACE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,14}$")
SNAPSHOT_RE = re.compile(r"^(latest|[0-9a-f]{8,64})$")
SSH_SOURCE_RE = re.compile(
    r"^(?:ssh://[^@\s/]+@[^\s/:]+(?::[0-9]+)?/\S+|[a-z0-9_.-]+@[^\s:/]+:\S+)$"
)
PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
SFTP_RE = re.compile(r"^sftp:(?P<user>[a-z_][a-z0-9_.-]*)@(?P<host>[^\s:/]+):(?P<path>/\S*)$")

# Accounts that exist on every Linux host or belong to the lab itself.
RESERVED_USERNAMES = frozenset(
    {
        "root",
        "daemon",
        "bin",
        "sys",
        "sync",
        "games",
        "man",
        "lp",
        "mail",
        "news",
        "uucp",
        "proxy",
        "www-data",
        "backup",
        "list",
        "irc",
        "gnats",
        "nobody",
        "systemd-network",
        "systemd-resolve",
        "systemd-timesync",
        "messagebus",
        "sshd",
        "polkitd",
        "syslog",
        "adm",
        "operator",
        "halt",
        "shutdown",
        "ftp",
        "tss",
        "dnsmasq",
        "docker",
        "incus",
        "incus-admin",
        "tailscale",
        "postgres",
        "grafana",
    }
)
# Prefixes of infra's and the platform's accounts; the old project prefix stays refused.
# Infra's own accounts and groups start with pinka- (§6.1); the platform's are
# checked against the host when it is deployed (§12.1).
SERVICE_ACCOUNT_PREFIXES = ("pinka-",)

# Key types accepted for researchers (ssh-keygen -t; the sk- variants are FIDO keys).
SSH_KEY_TYPES = frozenset(
    {
        "ssh-ed25519",
        "ecdsa-sha2-nistp256",
        "ecdsa-sha2-nistp384",
        "ecdsa-sha2-nistp521",
        "ssh-rsa",
        "sk-ssh-ed25519@openssh.com",
        "sk-ecdsa-sha2-nistp256@openssh.com",
    }
)
RSA_MIN_BITS = 3072
SIZE_FACTORS = {
    "B": 1,
    "KB": 10**3,
    "MB": 10**6,
    "GB": 10**9,
    "TB": 10**12,
    "KiB": 2**10,
    "MiB": 2**20,
    "GiB": 2**30,
    "TiB": 2**40,
}


def hostname(value: str) -> str | None:
    """A machine name: a lower-case DNS label (it becomes the tailnet host name).

    Args:
        value: The name.

    Returns:
        The error, or None.
    """
    if not HOSTNAME_RE.fullmatch(value):
        return (
            "lower-case letters, digits and '-', at most 63 characters, "
            "not starting or ending with '-'"
        )
    return None


def username(value: str, service_accounts: Iterable[str] = ()) -> str | None:
    """A researcher's Linux user name.

    Args:
        value: The name.
        service_accounts: The lab's own account names (``infra_accounts``).

    Returns:
        The error, or None.
    """
    if not USERNAME_RE.fullmatch(value):
        return "lower-case letters, digits, '_' and '-', starting with a letter or '_', at most 32"
    if value in RESERVED_USERNAMES:
        return f"'{value}' is reserved on Linux or by the lab"
    if value in set(service_accounts) or value.startswith(SERVICE_ACCOUNT_PREFIXES):
        return f"'{value}' clashes with the lab's own accounts (pinka-*)"
    return None


def absolute_path(value: str) -> str | None:
    """An absolute, normalized path without whitespace.

    Args:
        value: The path.

    Returns:
        The error, or None.
    """
    if not value.startswith("/"):
        return "must be an absolute path"
    if any(c.isspace() or not c.isprintable() for c in value):
        return "must not contain whitespace or control characters"
    parts = value.split("/")
    if ".." in parts or "." in parts or "//" in value or (len(value) > 1 and value.endswith("/")):
        return "must be normalized (no '.', '..', '//' or trailing '/')"
    return None


def parse_cpuset(value: str) -> set[int]:
    """The CPUs of a cpuset list such as ``0-5,8``.

    Args:
        value: The list.

    Returns:
        The CPU numbers.

    Raises:
        ValueError: For a malformed list.
    """
    cpus: set[int] = set()
    if not value:
        raise ValueError("empty")
    for part in value.split(","):
        first, sep, last = part.partition("-")
        if not first.isdigit() or (sep and not last.isdigit()):
            raise ValueError(part)
        low, high = int(first), int(last) if sep else int(first)
        if high < low:
            raise ValueError(part)
        cpus.update(range(low, high + 1))
    return cpus


def cpuset(value: str, cpu_count: int | None = None) -> str | None:
    """A cpuset list (``0-5,8``) within the host's CPUs.

    Args:
        value: The list.
        cpu_count: Number of CPUs, if known.

    Returns:
        The error, or None.
    """
    try:
        cpus = parse_cpuset(value)
    except ValueError:
        return "a list of CPU numbers and ranges, such as 0-5,8"
    if cpu_count is not None and max(cpus) >= cpu_count:
        return f"CPU {max(cpus)} does not exist (this host has CPUs 0-{cpu_count - 1})"
    return None


def cpusets_disjoint(services: str, targets: str) -> str | None:
    """Lab services and Incus targets never share cores.

    Args:
        services: ``cpuset_services``.
        targets: ``cpuset_targets``.

    Returns:
        The error, or None (also None when either list is malformed; see :func:`cpuset`).
    """
    try:
        shared = parse_cpuset(services) & parse_cpuset(targets)
    except ValueError:
        return None
    if shared:
        return f"services and targets share CPUs {format_cpuset(shared)}"
    return None


# Split cores named in the SMT warning; the rest are counted.
SMT_CORES_SHOWN = 4


def smt_split(services: str, targets: str, cores: Iterable[Iterable[int]]) -> str | None:
    """A warning when SMT siblings of one core fall into different CPU sets.

    Two threads of one core share its execution units: a target on one
    thread is measured with the lab services running on the other.

    Args:
        services: ``cpuset_services``.
        targets: ``cpuset_targets``.
        cores: The CPUs of each core with more than one thread
            (``thread_siblings_list``).

    Returns:
        The warning, or None (also None when either list is empty or malformed).
    """
    try:
        s, t = parse_cpuset(services), parse_cpuset(targets)
    except ValueError:
        return None
    split = sorted({tuple(sorted(core)) for core in cores if s & set(core) and t & set(core)})
    if not split:
        return None
    shown = "; ".join(format_cpuset(core) for core in split[:SMT_CORES_SHOWN])
    more = f" and {len(split) - SMT_CORES_SHOWN} more" if len(split) > SMT_CORES_SHOWN else ""
    return (
        f"SMT siblings of one core are split between services and targets ({shown}{more}): "
        "target measurements would share cores with the lab services; keep both threads "
        "of a core in the same set"
    )


def format_cpuset(cpus: Iterable[int]) -> str:
    """Render CPU numbers as a cpuset list.

    Args:
        cpus: CPU numbers.

    Returns:
        The list, such as ``0-5,8``.
    """
    ordered = sorted(set(cpus))
    parts: list[str] = []
    i = 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and ordered[j + 1] == ordered[j] + 1:
            j += 1
        parts.append(str(ordered[i]) if i == j else f"{ordered[i]}-{ordered[j]}")
        i = j + 1
    return ",".join(parts)


def size(value: str) -> str | None:
    """A size with a unit, such as ``300GiB`` or ``2GB``.

    Args:
        value: The size.

    Returns:
        The error, or None.
    """
    if not SIZE_RE.fullmatch(value):
        return "a whole number with a unit: B, KB, MB, GB, TB, KiB, MiB, GiB or TiB"
    return None


def size_bytes(value: str) -> int:
    """The number of bytes of a valid size.

    Args:
        value: A size accepted by :func:`size`.

    Returns:
        Bytes.
    """
    match = SIZE_RE.fullmatch(value)
    if match is None:
        raise ValueError(value)
    return int(match.group(1)) * SIZE_FACTORS[match.group(2)]


def https_url(value: str) -> str | None:
    """An ``https://`` URL with a host and no credentials.

    Args:
        value: The URL.

    Returns:
        The error, or None.
    """
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname:
        return "must be an https:// URL"
    if parsed.username or parsed.password:
        return "must not contain credentials"
    if any(c.isspace() for c in value):
        return "must not contain whitespace"
    return None


def source_repo(value: str) -> str | None:
    """The Git URL of the pokus-platform repository (owner answer E3).

    Args:
        value: An SSH URL (``git@host:path``, ``ssh://user@host/path``) or an
            anonymous ``https://`` URL.

    Returns:
        The error, or None.
    """
    if value.startswith("https://"):
        return https_url(value)
    if SSH_SOURCE_RE.fullmatch(value):
        return None
    return "an SSH URL (git@host:path or ssh://user@host/path) or an anonymous https:// URL"


def git_ref(value: str) -> str | None:
    """A Git ref name or a full commit (``git check-ref-format`` rules).

    Args:
        value: A tag, branch or 40-hex commit.

    Returns:
        The error, or None.
    """
    if REVISION_RE.fullmatch(value):
        return None
    bad = (
        not value
        or value == "@"
        or value.startswith(("/", "-", "."))
        or value.endswith(("/", ".", ".lock"))
        or ".." in value
        or "//" in value
        or "@{" in value
        or "/." in value
        or any(c in value for c in " ~^:?*[\\")
        or not value.isprintable()
    )
    return "not a valid Git ref (see git check-ref-format)" if bad else None


def tailscale_tag(value: str) -> str | None:
    """A Tailscale ACL tag.

    Args:
        value: Such as ``tag:pinka-lab``.

    Returns:
        The error, or None.
    """
    if not TAG_RE.fullmatch(value):
        return "tag:<name> with lower-case letters, digits and '-', starting with a letter"
    return None


def hf_repo(value: str) -> str | None:
    """A Hugging Face model repository id (``<owner>/<name>``).

    Args:
        value: The id.

    Returns:
        The error, or None.
    """
    if not HF_REPO_RE.fullmatch(value) or ".." in value or "--" in value:
        return "<owner>/<name> with letters, digits, '.', '_' and '-'"
    return None


def revision(value: str) -> str | None:
    """A full 40-hex commit of a model repository.

    Args:
        value: The revision.

    Returns:
        The error, or None.
    """
    if not REVISION_RE.fullmatch(value):
        return "the full 40-character commit hash (lower-case hex), not a branch or tag"
    return None


def time_of_day(value: str) -> str | None:
    """A UTC time of day (``HH:MM``).

    Args:
        value: The time.

    Returns:
        The error, or None.
    """
    return None if TIME_RE.fullmatch(value) else "HH:MM (24 hours, UTC)"


def interface(value: str) -> str | None:
    """A network interface name.

    Args:
        value: Such as ``enp5s0``.

    Returns:
        The error, or None.
    """
    if not INTERFACE_RE.fullmatch(value) or value in {"lo", "tailscale0"}:
        return "an interface name of this host (not lo or tailscale0)"
    return None


def snapshot(value: str) -> str | None:
    """A restic snapshot id or ``latest``.

    Args:
        value: The snapshot.

    Returns:
        The error, or None.
    """
    return None if SNAPSHOT_RE.fullmatch(value) else "latest or a snapshot id (8-64 hex)"


def restic_repository(value: str) -> str | None:
    """A restic repository URL: ``sftp:user@host:/path``, ``rest:https://...`` or a local path.

    Args:
        value: The repository.

    Returns:
        The error, or None.
    """
    if value.startswith("sftp:"):
        match = SFTP_RE.fullmatch(value)
        if match is None:
            return "sftp:<user>@<host>:/<absolute path>"
        return host(match.group("host"))
    if value.startswith("rest:"):
        return https_url(value.removeprefix("rest:"))
    if value.startswith("/"):
        return absolute_path(value)
    return "sftp:<user>@<host>:/<path>, rest:https://<host>/<path> or an absolute path"


def host(value: str) -> str | None:
    """A host name or IP address.

    Args:
        value: The host.

    Returns:
        The error, or None.
    """
    try:
        ipaddress.ip_address(value)
    except ValueError:
        labels = value.rstrip(".").split(".")
        if not all(LABEL_RE.fullmatch(label) for label in labels):
            return "a host name or IP address"
    return None


@dataclass(frozen=True)
class SshKey:
    """One parsed SSH public key.

    Attributes:
        key_type: Such as ``ssh-ed25519``.
        blob: The decoded key.
        comment: Text after the key.
        fingerprint: ``SHA256:...`` as ssh-keygen prints it.
        bits: Key size of RSA keys, else 0.
    """

    key_type: str
    blob: bytes
    comment: str
    fingerprint: str
    bits: int

    @property
    def line(self) -> str:
        """The key as one authorized_keys line."""
        encoded = base64.b64encode(self.blob).decode("ascii")
        return f"{self.key_type} {encoded}" + (f" {self.comment}" if self.comment else "")


def _ssh_string(blob: bytes, offset: int) -> tuple[bytes, int]:
    if offset + 4 > len(blob):
        raise ValueError("truncated")
    length = int.from_bytes(blob[offset : offset + 4], "big")
    end = offset + 4 + length
    if end > len(blob):
        raise ValueError("truncated")
    return blob[offset + 4 : end], end


def parse_ssh_key(line: str) -> SshKey:
    """Parse one OpenSSH public key line.

    Args:
        line: ``<type> <base64> [comment]``.

    Returns:
        The key.

    Raises:
        ValueError: With the reason it is not a usable public key.
    """
    if PRIVATE_KEY_RE.search(line):
        raise ValueError("this is a PRIVATE key; paste the public key (the .pub file) instead")
    key_type, _, rest = line.strip().partition(" ")
    encoded, _, comment = rest.strip().partition(" ")
    if not encoded:
        raise ValueError("expected '<type> <key> [comment]'")
    if key_type not in SSH_KEY_TYPES:
        raise ValueError(f"unsupported key type {key_type!r}")
    try:
        blob = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("the key data is not valid base64") from exc
    embedded, offset = _ssh_string(blob, 0)
    if embedded.decode("ascii", "replace") != key_type:
        raise ValueError("the key data does not match its type")
    bits = 0
    if key_type == "ssh-rsa":
        _exponent, offset = _ssh_string(blob, offset)
        modulus, offset = _ssh_string(blob, offset)
        bits = int.from_bytes(modulus, "big").bit_length()
        if bits < RSA_MIN_BITS:
            raise ValueError(f"RSA keys need at least {RSA_MIN_BITS} bits (this one has {bits})")
    digest = base64.b64encode(hashlib.sha256(blob).digest()).decode("ascii").rstrip("=")
    return SshKey(key_type, blob, comment.strip(), f"SHA256:{digest}", bits)


def parse_ssh_keys(text: str) -> tuple[list[SshKey], list[str]]:
    """Parse pasted or read public keys, one per line.

    Args:
        text: The keys; blank lines and ``#`` comments are ignored.

    Returns:
        The keys and one error per bad line (``line N: reason``).
    """
    keys: list[SshKey] = []
    errors: list[str] = []
    if PRIVATE_KEY_RE.search(text):
        return [], ["this is a PRIVATE key; paste the public key (the .pub file) instead"]
    for number, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        try:
            key = parse_ssh_key(raw)
        except ValueError as exc:
            errors.append(f"line {number}: {exc}")
            continue
        if any(k.blob == key.blob for k in keys):
            errors.append(f"line {number}: duplicate key")
            continue
        keys.append(key)
    if not keys and not errors:
        errors.append("no public key found")
    return keys, errors


def ssh_keygen_fingerprint(line: str) -> str | None:
    """The fingerprint ``ssh-keygen -lf`` reports, if ssh-keygen is installed.

    Args:
        line: One public key line.

    Returns:
        ``SHA256:...``, or None without ssh-keygen.

    Raises:
        ValueError: If ssh-keygen rejects the key.
    """
    binary = shutil.which("ssh-keygen")
    if binary is None:
        return None
    done = subprocess.run(  # noqa: S603 - fixed arguments, the key on stdin
        [binary, "-lf", "-"], input=line + "\n", capture_output=True, text=True, check=False
    )
    if done.returncode != 0:
        raise ValueError(done.stderr.strip() or "ssh-keygen rejected the key")
    fields = done.stdout.split()
    return fields[1] if len(fields) > 1 else None
