"""Sanitize a fixture root captured from a real host (privacy invariant).

Real hosts leak identity: host names, serial numbers, UUIDs, MAC and IP
addresses, user names and e-mail addresses. Before a captured root may become
a fixture, run::

    python preflight/tests/sanitize_fixture.py <captured-root> [--deny TERM ...]

The tool rewrites every text file in place:

* identity files are reset: ``/etc/hostname`` → ``pinka-lab``,
  ``/etc/machine-id`` and DMI serial/UUID files → zeros;
* global IPv4 → ``192.0.2.x``, global IPv6 → ``2001:db8::x``;
* MAC addresses → ``00:00:5e:00:53:xx``; UUIDs → the nil UUID;
* e-mail addresses → ``user@example.com``;
* ``/home/<name>`` → ``/home/researcher1`` (numbered per name);
* every ``--deny`` term (case-insensitive) → ``redacted``.

Afterwards run ``make privacy`` and review the diff by hand; the sanitizer
reduces risk but does not replace review. Standard library only.
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import re
import sys
from collections.abc import Sequence

ZERO_FILES = {
    "/etc/machine-id": "00000000000000000000000000000000\n",
    "/sys/class/dmi/id/product_uuid": "00000000-0000-0000-0000-000000000000\n",
    "/sys/class/dmi/id/product_serial": "0\n",
    "/sys/class/dmi/id/board_serial": "0\n",
    "/sys/class/dmi/id/chassis_serial": "0\n",
    "/etc/hostname": "pinka-lab\n",
}
IPV4 = re.compile(r"(?<![\w.])(\d{1,3}(?:\.\d{1,3}){3})(?![\w.])")
IPV6 = re.compile(r"(?<![\w:.])((?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4})(?![\w:.])")
MAC = re.compile(r"(?<![\w:-])((?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2})(?![\w:-])")
UUID = re.compile(
    r"(?<![0-9A-Fa-f-])[0-9A-Fa-f]{8}-(?:[0-9A-Fa-f]{4}-){3}[0-9A-Fa-f]{12}(?![0-9A-Fa-f-])"
)
EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}")
HOME = re.compile(r"/home/([a-z_][a-z0-9_-]*)")
DOC_MAC_PREFIX = "00:00:5e:00:53:"


class Sanitizer:
    """Stateful replacements, so the same input always maps to the same placeholder."""

    def __init__(self, deny: Sequence[str]) -> None:
        """Create a sanitizer.

        Args:
            deny: Extra terms to redact.
        """
        self.deny = [t.lower() for t in deny if t]
        self.maps: dict[str, dict[str, str]] = {"v4": {}, "v6": {}, "mac": {}, "home": {}}

    def _next(self, kind: str, key: str, fmt: str) -> str:
        table = self.maps[kind]
        if key not in table:
            table[key] = fmt.format(len(table) + 1)
        return table[key]

    def _v4(self, m: re.Match[str]) -> str:
        text = m.group(1)
        try:
            if not ipaddress.ip_address(text).is_global:
                return text
        except ValueError:
            return text
        return self._next("v4", text, "192.0.2.{}")

    def _v6(self, m: re.Match[str]) -> str:
        text = m.group(1)
        try:
            if not ipaddress.ip_address(text).is_global:
                return text
        except ValueError:
            return text
        return self._next("v6", text, "2001:db8::{:x}")

    def _mac(self, m: re.Match[str]) -> str:
        text = m.group(1)
        norm = text.lower().replace("-", ":")
        if norm.startswith(DOC_MAC_PREFIX) or norm == "00:00:00:00:00:00":
            return text
        return self._next("mac", norm, DOC_MAC_PREFIX + "{:02x}")

    def _home(self, m: re.Match[str]) -> str:
        return "/home/" + self._next("home", m.group(1), "researcher{}")

    def text(self, value: str) -> str:
        """Sanitize one text.

        Args:
            value: Original text.

        Returns:
            Sanitized text.
        """
        value = UUID.sub("00000000-0000-0000-0000-000000000000", value)
        value = MAC.sub(self._mac, value)
        value = IPV4.sub(self._v4, value)
        value = IPV6.sub(self._v6, value)
        value = EMAIL.sub("user@example.com", value)
        value = HOME.sub(self._home, value)
        for term in self.deny:
            value = re.sub(re.escape(term), "redacted", value, flags=re.IGNORECASE)
        return value


def sanitize_root(root: str, deny: Sequence[str] = ()) -> list[str]:
    """Sanitize every text file below ``root`` in place.

    Args:
        root: Captured fixture root.
        deny: Extra terms to redact.

    Returns:
        Relative paths of changed files.
    """
    s = Sanitizer(deny)
    changed: list[str] = []
    for target, content in ZERO_FILES.items():
        path = os.path.join(root, target.lstrip("/"))
        if os.path.isfile(path):
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(content)
            changed.append(target.lstrip("/"))
    for dirpath, _dirs, files in os.walk(root):
        for name in sorted(files):
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root)
            if rel in changed:
                continue
            with open(path, "rb") as handle:
                data = handle.read()
            text = data.decode("utf-8", errors="surrogateescape")
            new = "\0".join(s.text(part) for part in text.split("\0"))
            if new != text:
                with open(path, "wb") as handle:
                    handle.write(new.encode("utf-8", errors="surrogateescape"))
                changed.append(rel)
    return sorted(changed)


def main(argv: Sequence[str]) -> int:
    """Command-line entry point.

    Args:
        argv: Arguments without the program name.

    Returns:
        Exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", help="captured fixture root")
    parser.add_argument(
        "--deny", action="append", default=[], help="extra term to redact (repeatable)"
    )
    args = parser.parse_args(list(argv))
    for rel in sanitize_root(args.root, args.deny):
        print(f"sanitized {rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
