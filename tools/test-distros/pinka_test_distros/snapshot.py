"""The uninstall lane's snapshot of a host (BUILD_PROMPT §19.9).

Before the install and after the uninstall, both right after a reboot (so
the comparison is about what persists), the harness records in the VM: the
installed packages and their versions, holds, repositories and keys,
accounts and groups, enabled and masked units, the nftables ruleset, the
listening ports, the files below pinka's paths, the settings pinka changes
(the kernel settings of the ``base`` role, the time zone, the files it
edits in place), Tailscale's state and preferences, and Docker's
containers, networks and volumes. Any difference fails the lane unless it
is kept on purpose (:data:`KEPT`, :func:`system_account`), and every kept
one is reported with its reason.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

INFRA = Path(__file__).resolve().parents[3]
BASE_DEFAULTS = INFRA / "ansible" / "roles" / "base" / "defaults" / "main.yml"

# One section per kind of state; each prints one item per line. The
# settings section also prints the kernel settings (section_script()).
SECTIONS: dict[str, str] = {
    "packages": (
        "if command -v dpkg-query >/dev/null; then dpkg-query -W -f '${db:Status-Abbrev}"
        " ${binary:Package} ${Version}\\n' | awk '$1 ~ /^[ih]i/ {print $2, $3}';"
        " elif command -v rpm >/dev/null; then"
        " rpm -qa --qf '%{NAME} %{EPOCH}:%{VERSION}-%{RELEASE}\\n' | grep -v '^gpg-pubkey ';"
        " else pacman -Q; fi"
    ),
    "holds": (
        "command -v apt-mark >/dev/null && apt-mark showhold | sed 's/^/apt-mark /';"
        " ls /etc/apt/preferences.d 2>/dev/null | sed 's/^/preferences /';"
        " command -v dnf >/dev/null && dnf -q versionlock list 2>/dev/null"
        " | sed 's/^/versionlock /';"
        " command -v zypper >/dev/null && zypper -q locks 2>/dev/null | sed 's/^/zypper /';"
        " grep -h '^IgnorePkg' /etc/pacman.conf 2>/dev/null; true"
    ),
    "repositories": (
        "for dir in /etc/apt/sources.list.d /etc/apt/keyrings /usr/share/keyrings /etc/yum.repos.d"
        ' /etc/zypp/repos.d /etc/pki/rpm-gpg; do test -d "$dir" && find "$dir" -maxdepth 1 -type f;'
        " done; command -v rpm >/dev/null && rpm -q gpg-pubkey; true"
    ),
    "users": "getent passwd | cut -d: -f1,3,6,7",
    "groups": "getent group | cut -d: -f1,3,4",
    "units": (
        "systemctl list-unit-files --no-legend --plain --state=enabled,masked"
        " | awk '{print $1, $2}'"
    ),
    "nftables": (
        "nft list ruleset 2>/dev/null | sed -E 's/ # handle [0-9]+//;"
        " s/counter packets [0-9]+ bytes [0-9]+/counter/' | grep -v '^[[:space:]]*$'; true"
    ),
    "listening": "ss -H -ltnu | awk '{print $1, $5}'",
    "files": (
        "find /etc/pinka /opt/pinka /var/lib/pinka /var/lib/pinka-kept /run/pinka /var/cache/pinka"
        " /srv/pinka"
        " /var/log/pinka-bootstrap /var/log/pinka-uninstall /usr/local/bin /usr/local/lib"
        " /usr/local/libexec /usr/local/share /etc/systemd/system /etc/ssh /etc/sudoers.d"
        " /etc/sysctl.d /etc/modprobe.d /etc/tmpfiles.d /etc/docker -xdev 2>/dev/null; true"
    ),
    "settings": (
        "sha256sum /etc/ssh/sshd_config /etc/docker/daemon.json /etc/wsl.conf 2>/dev/null;"
        ' echo "localtime $(readlink /etc/localtime)"'
    ),
    "tailscale": (
        "command -v tailscale >/dev/null && tailscale status --json 2>/dev/null | python3 -c '"
        "import json, sys\n"
        "d = json.load(sys.stdin)\n"
        's = d.get("Self") or {}\n'
        'print("state", d.get("BackendState"))\n'
        'print("node", s.get("ID"), s.get("HostName"))\n'
        'print("addresses", ",".join(sorted(s.get("TailscaleIPs") or [])))\n'
        'print("tags", ",".join(sorted(s.get("Tags") or [])))\n'
        "'; command -v tailscale >/dev/null && tailscale debug prefs 2>/dev/null | python3 -c '"
        "import json, sys\n"
        "p = json.load(sys.stdin)\n"
        'for key in ("RunSSH", "ShieldsUp", "CorpDNS", "AdvertiseTags", "Hostname"):\n'
        '    print("prefs", key, json.dumps(p.get(key)))\n'
        "'; true"
    ),
    "docker": (
        "command -v docker >/dev/null && {"
        " docker ps -a --format 'container {{.Names}} {{.Image}}';"
        " docker network ls --format 'network {{.Name}} {{.Driver}}';"
        " docker volume ls --format 'volume {{.Name}}'; }; true"
    ),
}
# Sections whose order means something are not sorted.
UNSORTED = ("nftables",)
MARK = "== "
# After a reboot: until boot has finished, Tailscale has left its starting
# states and Docker answers (each for at most a minute).
SETTLE = (
    "systemctl is-system-running --wait >/dev/null 2>&1;"
    " if command -v tailscale >/dev/null; then for _ in $(seq 60); do"
    ' case "$(tailscale status --json 2>/dev/null | python3 -c'
    ' \'import json, sys; print(json.load(sys.stdin).get("BackendState", ""))\' 2>/dev/null)" in'
    " Running | NeedsLogin | NeedsMachineAuth | Stopped) break ;; esac; sleep 1; done; fi;"
    " if command -v docker >/dev/null; then for _ in $(seq 60); do"
    " docker info >/dev/null 2>&1 && break; sleep 1; done; fi; true"
)
# Linux's default ip_local_port_range: a listener there has a port picked
# at random, which a reboot changes.
EPHEMERAL = range(32768, 61000)
ADDRESS_PORT = re.compile(r"^(?P<proto>\S+) (?P<address>.*):(?P<port>\d+)$")


def sysctl_keys(defaults: Path = BASE_DEFAULTS) -> list[str]:
    """The kernel settings the ``base`` role sets (``base_sysctl``).

    Args:
        defaults: The role's defaults file.

    Returns:
        The keys, sorted.
    """
    data = yaml.safe_load(defaults.read_text(encoding="utf-8")) or {}
    return sorted(str(k) for k in data.get("base_sysctl") or {})


def section_script(name: str, sysctl: Iterable[str] = ()) -> str:
    """The shell commands of one section.

    Args:
        name: The section.
        sysctl: Kernel settings the settings section prints.

    Returns:
        Shell commands that print the section's lines.
    """
    keys = " ".join(sysctl)
    if name == "settings" and keys:
        return f"{SECTIONS[name]}; sysctl {keys} 2>/dev/null; true"
    return SECTIONS[name]


def script(sysctl: Iterable[str] = ()) -> str:
    """The shell script that prints every section, each under its ``== name`` line.

    Args:
        sysctl: Kernel settings the settings section prints.

    Returns:
        A bash script.
    """
    keys = list(sysctl)
    parts = []
    for name in SECTIONS:
        sort = "" if name in UNSORTED else " | LC_ALL=C sort -u"
        parts.append(f"echo '{MARK}{name}'; {{ {section_script(name, keys)}; }}{sort}")
    return "\n".join(parts) + "\n"


def parse(text: str) -> dict[str, list[str]]:
    """The sections of :func:`script`'s output.

    Args:
        text: The output.

    Returns:
        Each section's lines (blank lines dropped).

    Raises:
        ValueError: When a section is missing.
    """
    found: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in text.splitlines():
        if line.startswith(MARK) and line[len(MARK) :] in SECTIONS:
            current = found.setdefault(line[len(MARK) :], [])
        elif current is not None and line.strip():
            current.append(line.rstrip())
    missing = [name for name in SECTIONS if name not in found]
    if missing:
        raise ValueError(f"the snapshot lacks {', '.join(missing)}")
    return found


def normalize(section: str, line: str) -> str:
    """A line as it is compared: a listener on an ephemeral port loses its number.

    Args:
        section: The section.
        line: A line of it.

    Returns:
        The line to compare.
    """
    if section == "listening":
        match = ADDRESS_PORT.match(line)
        if match and int(match["port"]) in EPHEMERAL:
            return f"{match['proto']} {match['address']}:<ephemeral>"
    return line


@dataclass(frozen=True)
class Kept:
    """A difference kept on purpose.

    Attributes:
        section: The section it is in.
        pattern: A regular expression the line (without its ``+`` or ``-``) matches.
        reason: Why uninstall leaves it.
    """

    section: str
    pattern: str
    reason: str

    def matches(self, section: str, line: str) -> bool:
        """Whether this explains a line of a section."""
        return section == self.section and re.search(self.pattern, line) is not None


# What uninstall leaves on purpose (BUILD_PROMPT §5.4).
KEPT = (
    Kept("files", r"^/srv/pinka(/|$)", "the lab's data, kept without --purge-data"),
    Kept(
        "files",
        r"^/var/log/pinka-(bootstrap|uninstall)(/|$)",
        "the logs of the install and of the uninstall",
    ),
    Kept(
        "files",
        r"^/var/lib/pinka-kept(/|$)",
        "what uninstall copies out of pinka's paths: the backups' key and settings, the records",
    ),
)
VERSION_CHANGE = (
    "a package that existed before pinka, at another version now: uninstall never "
    "downgrades (a distribution update, or a newer version a package pinka installed needed)"
)
# Accounts and groups whose names are pinka's are never excused.
PINKA_NAME = re.compile(r"^(pinka|researcher)")
UID_MIN = 1000


def system_account(section: str, line: str, platforms: Iterable[str]) -> str | None:
    """Why a new system account or group is kept, or None.

    A package pinka installed (and uninstall removed) may have created a
    system account or group; distributions keep those after removal.

    Args:
        section: ``users`` or ``groups``.
        line: ``+name:id:...``.
        platforms: The platform names deployed in the test, whose accounts are pinka's.

    Returns:
        The reason, or None when the line is not such an account.
    """
    if section not in ("users", "groups") or not line.startswith("+"):
        return None
    name, _, rest = line[1:].partition(":")
    ident = rest.split(":", 1)[0]
    pinka = PINKA_NAME.match(name) or any(name.startswith(f"{p}-") for p in platforms)
    if pinka or not ident.isdigit() or int(ident) >= UID_MIN:
        return None
    return "a system account or group a package created; distributions keep them after removal"


def versions(lines: Iterable[str]) -> dict[str, str]:
    """``name version`` lines as a mapping."""
    found = {}
    for line in lines:
        name, _, version = line.partition(" ")
        found[name] = version
    return found


def section_differences(section: str, before: Iterable[str], after: Iterable[str]) -> list[str]:
    """The differences in one section.

    Args:
        section: The section.
        before: Its lines before the install.
        after: Its lines now.

    Returns:
        ``-line`` (only before), ``+line`` (only now) and, for packages,
        ``~name before -> now`` for a version that changed.
    """
    old = [normalize(section, line) for line in before]
    new = [normalize(section, line) for line in after]
    if section != "packages":
        return [f"-{line}" for line in sorted(set(old) - set(new))] + [
            f"+{line}" for line in sorted(set(new) - set(old))
        ]
    was, now = versions(old), versions(new)
    return (
        [f"-{n} {v}" for n, v in sorted(was.items()) if n not in now]
        + [f"+{n} {v}" for n, v in sorted(now.items()) if n not in was]
        + [f"~{n} {was[n]} -> {now[n]}" for n in sorted(was) if n in now and was[n] != now[n]]
    )


def differences(
    before: Mapping[str, list[str]],
    after: Mapping[str, list[str]],
    platforms: Iterable[str] = (),
    fixed: Collection[str] = (),
) -> tuple[list[str], list[str]]:
    """The differences between two snapshots: those that fail and those kept on purpose.

    Args:
        before: The snapshot before the install.
        after: The snapshot after the uninstall.
        platforms: The platform names deployed in the test.
        fixed: Packages of pre-existing components, whose version must not
            change either.

    Returns:
        ``(failing, kept)``: lines ``<section>: <difference>``; kept ones
        end with their reason.
    """
    names = list(platforms)
    failing: list[str] = []
    kept: list[str] = []
    for section in SECTIONS:
        for line in section_differences(section, before.get(section, []), after.get(section, [])):
            if section == "packages" and line.startswith("~"):
                package = line[1:].split(" ", 1)[0]
                reason = None if package in fixed else VERSION_CHANGE
            else:
                reason = system_account(section, line, names) or next(
                    (k.reason for k in KEPT if k.matches(section, line[1:])), None
                )
            if reason:
                kept.append(f"{section}: {line} (kept: {reason})")
            else:
                failing.append(f"{section}: {line}")
    return failing, kept
