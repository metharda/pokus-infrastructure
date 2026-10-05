"""Privacy scan: no personal or installation-specific data in the repository.

Implements the CI privacy scan required by the privacy invariant (infra §13.12,
platform §16.9, docs spec §A.4 and §A.6). The scanner reports:

* e-mail addresses outside the RFC 2606 example domains;
* public (globally routable) IPv4 and IPv6 addresses;
* MAC addresses outside the RFC 7042 documentation range ``00:00:5e:00:53:00/24``;
* non-nil UUIDs (serials and UUIDs in fixtures must be zeroed);
* SSH and PGP key material outside test-key fixtures (a path that contains both
  a ``tests`` and a ``test-keys`` directory);
* credential and identity tokens (Tailscale auth and node keys, Hugging Face,
  GitHub and AWS access keys);
* terms from the optional maintainer denylist in ``POKUS_PRIVACY_DENYLIST``
  (newline- or comma-separated, matched case-insensitively as whole words, so a
  term inside a longer word or a hash does not match). The denylist comes from
  a CI secret and is never committed; findings never print the term.

Findings print the rule and position but not the matched value, so CI logs do
not spread personal data further. ``--show-matches`` prints values for local
debugging.

This module uses the Python standard library only.
"""

from __future__ import annotations

import ipaddress
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

MAX_FILE_BYTES = 10 * 1024 * 1024
IPV6_FULL_FORM_COLONS = 7
GIT_LS_FILES = ("git", "ls-files", "-z", "--cached", "--others", "--exclude-standard")
DENYLIST_ENV = "POKUS_PRIVACY_DENYLIST"

RFC2606_DOMAINS = ("example.com", "example.org", "example.net")
RFC2606_TLDS = ("example", "test", "invalid", "localhost")
DOC_MAC_PREFIX = "00:00:5e:00:53:"

EMAIL_RE = re.compile(
    r"(?<![\w.+%-])(?P<local>[A-Za-z0-9._%+-]+)@"
    r"(?P<domain>(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})(?![\w-])"
)
OPENSSH_ALGORITHM_RE = re.compile(r"^[a-z0-9.]+-[a-z0-9.-]+@openssh\.com$")
IPV4_RE = re.compile(r"(?<![\w.])(?P<addr>\d{1,3}(?:\.\d{1,3}){3})(?:/(?P<len>\d{1,2}))?(?![\w.-])")
IPV6_RE = re.compile(
    r"(?<![\w:.])(?P<addr>(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4})"
    r"(?:/(?P<len>\d{1,3}))?(?![\w:.])"
)
MAC_RE = re.compile(r"(?<![\w:-])(?P<mac>(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2})(?![\w:-])")
UUID_RE = re.compile(
    r"(?<![0-9A-Fa-f-])(?P<uuid>[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})(?![0-9A-Fa-f-])"
)
KEY_MATERIAL_RES = (
    re.compile(
        r"\b(?:ssh-(?:rsa|dss|ed25519)|ecdsa-sha2-nistp(?:256|384|521)|"
        r"sk-(?:ssh-ed25519|ecdsa-sha2-nistp256)@openssh\.com)\s+AAAA[0-9A-Za-z+/]{16,}"
    ),
    re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----"),
    re.compile(r"-----BEGIN PGP (?:PUBLIC|PRIVATE) KEY BLOCK-----"),
    re.compile(r"PuTTY-User-Key-File-\d"),
)
TOKEN_RES = (
    ("tailscale-auth-key", re.compile(r"\btskey-[A-Za-z0-9-]{10,}")),
    ("tailscale-node-identity", re.compile(r"\b(?:nodekey|mkey|discokey):[0-9a-f]{64}\b")),
    ("huggingface-token", re.compile(r"\bhf_[A-Za-z0-9]{30,}\b")),
    (
        "github-token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})\b"),
    ),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
)


@dataclass(frozen=True)
class Finding:
    """One privacy finding.

    Attributes:
        path: File that contains the finding.
        line: 1-based line number.
        column: 1-based column of the match.
        rule: Stable rule identifier.
        message: Human-readable description without the matched value.
        value: The matched text, printed only with ``--show-matches``.
    """

    path: Path
    line: int
    column: int
    rule: str
    message: str
    value: str


def is_rfc2606_domain(domain: str) -> bool:
    """Tell whether an e-mail domain is reserved for documentation (RFC 2606).

    Args:
        domain: Domain part of an e-mail address.

    Returns:
        True for ``example.com``, ``example.org``, ``example.net``, their
        subdomains, and the reserved top-level domains.
    """
    d = domain.lower().rstrip(".")
    if any(d == base or d.endswith("." + base) for base in RFC2606_DOMAINS):
        return True
    return d.rsplit(".", 1)[-1] in RFC2606_TLDS


def is_test_key_path(path: Path) -> bool:
    """Tell whether key material is allowed in this path.

    Key material is allowed only in clearly marked test fixtures: a path with a
    ``tests`` directory and, below it, a ``test-keys`` directory.

    Args:
        path: Path of the scanned file.

    Returns:
        True if the path is a marked test-key fixture.
    """
    parts = path.parts
    if "tests" not in parts:
        return False
    return "test-keys" in parts[parts.index("tests") + 1 :]


def load_denylist(environ: dict[str, str] | None = None) -> list[str]:
    """Read the optional maintainer denylist from the environment.

    Args:
        environ: Environment mapping; defaults to ``os.environ``.

    Returns:
        Lower-cased, non-empty terms.
    """
    env = os.environ if environ is None else environ
    raw = env.get(DENYLIST_ENV, "")
    terms = [t.strip().lower() for t in re.split(r"[\n,]", raw)]
    return [t for t in terms if t]


def _scan_emails(line: str) -> Iterator[tuple[int, str, str, str]]:
    for m in EMAIL_RE.finditer(line):
        local, domain = m.group("local"), m.group("domain")
        end = m.end()
        if local == "git" and line[end : end + 1] == ":":
            continue  # scp-like SSH remote such as git@host:org/repo.git
        if OPENSSH_ALGORITHM_RE.match(m.group(0)):
            continue  # OpenSSH algorithm identifier, not an address
        if is_rfc2606_domain(domain):
            continue
        yield m.start(), "email", "e-mail address outside RFC 2606 example domains", m.group(0)


def _scan_ipv4(line: str) -> Iterator[tuple[int, str, str, str]]:
    for m in IPV4_RE.finditer(line):
        text = m.group(0)
        try:
            if m.group("len") is not None:
                net = ipaddress.ip_network(text, strict=False)
                public = net.is_global
            else:
                public = ipaddress.ip_address(m.group("addr")).is_global
        except ValueError:
            continue
        if public:
            yield m.start(), "public-ip", "public IPv4 address (use 192.0.2.0/24 etc.)", text


def _scan_ipv6(line: str) -> Iterator[tuple[int, str, str, str]]:
    for m in IPV6_RE.finditer(line):
        addr = m.group("addr")
        if "::" not in addr and addr.count(":") != IPV6_FULL_FORM_COLONS:
            continue
        try:
            public = ipaddress.ip_address(addr).is_global
        except ValueError:
            continue
        if public:
            yield m.start(), "public-ip", "public IPv6 address (use 2001:db8::/32)", m.group(0)


def _scan_macs(line: str) -> Iterator[tuple[int, str, str, str]]:
    for m in MAC_RE.finditer(line):
        mac = m.group("mac").lower().replace("-", ":")
        if mac.startswith(DOC_MAC_PREFIX) or mac == "00:00:00:00:00:00":
            continue
        yield m.start(), "mac", "MAC address outside 00:00:5e:00:53:00/24", m.group(0)


def _scan_uuids(line: str) -> Iterator[tuple[int, str, str, str]]:
    for m in UUID_RE.finditer(line):
        if set(m.group("uuid").replace("-", "")) == {"0"}:
            continue
        yield m.start(), "uuid", "non-nil UUID (zero serials and UUIDs in fixtures)", m.group(0)


def _scan_keys(line: str, path: Path) -> Iterator[tuple[int, str, str, str]]:
    if is_test_key_path(path):
        return
    for pattern in KEY_MATERIAL_RES:
        for m in pattern.finditer(line):
            yield (
                m.start(),
                "key-material",
                "SSH/PGP key material outside tests/**/test-keys/",
                m.group(0),
            )


def _scan_tokens(line: str) -> Iterator[tuple[int, str, str, str]]:
    for rule, pattern in TOKEN_RES:
        for m in pattern.finditer(line):
            yield m.start(), rule, "credential or identity token", m.group(0)


def _scan_denylist(line: str, denylist: Sequence[str]) -> Iterator[tuple[int, str, str, str]]:
    for index, term in enumerate(denylist, start=1):
        pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", re.IGNORECASE)
        m = pattern.search(line)
        if m:
            yield m.start(), "denylist", f"maintainer denylist entry #{index}", "<redacted>"


def scan_text(path: Path, text: str, denylist: Sequence[str] = ()) -> list[Finding]:
    """Scan one text for privacy findings.

    Args:
        path: Path used for reporting and for the test-key fixture rule.
        text: File content.
        denylist: Lower-cased maintainer denylist terms.

    Returns:
        Findings in file order.
    """
    findings: list[Finding] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        hits: list[tuple[int, str, str, str]] = []
        hits.extend(_scan_emails(line))
        hits.extend(_scan_ipv4(line))
        hits.extend(_scan_ipv6(line))
        hits.extend(_scan_macs(line))
        hits.extend(_scan_uuids(line))
        hits.extend(_scan_keys(line, path))
        hits.extend(_scan_tokens(line))
        hits.extend(_scan_denylist(line, denylist))
        for col, rule, message, value in sorted(hits):
            findings.append(Finding(path, lineno, col + 1, rule, message, value))
    return findings


def list_files(paths: Iterable[Path]) -> list[Path]:
    """List the files to scan.

    Inside a git work tree this is every tracked file plus every untracked file
    that is not ignored, so ignored deployment data and build outputs are
    skipped. Outside git, directories are walked recursively.

    Args:
        paths: Files or directories.

    Returns:
        Sorted, de-duplicated file paths.
    """
    result: set[Path] = set()
    for p in paths:
        if p.is_file():
            result.add(p)
            continue
        try:
            out = subprocess.run(  # noqa: S603 - fixed argv, no shell
                [*GIT_LS_FILES, "--", str(p)],
                check=True,
                capture_output=True,
            ).stdout
        except (OSError, subprocess.CalledProcessError):
            result.update(f for f in p.rglob("*") if f.is_file() and ".git" not in f.parts)
            continue
        for raw in out.split(b"\0"):
            if raw:
                f = Path(os.fsdecode(raw))
                if f.is_file():
                    result.add(f)
    return sorted(result)


def read_text(path: Path) -> str | None:
    """Read a file as text, skipping binary and oversized files.

    Args:
        path: File to read.

    Returns:
        The decoded text, or None for binary or oversized files.
    """
    if path.stat().st_size > MAX_FILE_BYTES:
        return None
    data = path.read_bytes()
    if b"\0" in data[:8192]:
        return None
    return data.decode("utf-8", errors="replace")


def run(paths: Sequence[Path], *, show_matches: bool = False) -> int:
    """Scan paths and print findings.

    Args:
        paths: Files or directories to scan.
        show_matches: Print the matched values (local debugging only).

    Returns:
        0 when no findings were made, 1 otherwise.
    """
    denylist = load_denylist()
    files = list_files(paths)
    findings: list[Finding] = []
    skipped: list[Path] = []
    for f in files:
        text = read_text(f)
        if text is None:
            if f.stat().st_size > MAX_FILE_BYTES:
                skipped.append(f)
            continue
        findings.extend(scan_text(f, text, denylist))
    for f in skipped:
        print(f"{f}: warning: larger than {MAX_FILE_BYTES} bytes, not scanned", file=sys.stderr)
    for fd in findings:
        suffix = f" [{fd.value}]" if show_matches and fd.rule != "denylist" else ""
        print(f"{fd.path}:{fd.line}:{fd.column}: {fd.rule}: {fd.message}{suffix}")
    denylist_note = f", denylist terms: {len(denylist)}" if denylist else ", no denylist"
    print(
        f"privacy scan: {len(files)} files, {len(findings)} findings{denylist_note}",
        file=sys.stderr,
    )
    return 1 if findings else 0
