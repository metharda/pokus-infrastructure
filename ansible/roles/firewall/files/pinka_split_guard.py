#!/opt/pinka/python/bin/python3 -I
# Managed by pinka (ansible/roles/firewall/files/pinka_split_guard.py). Local changes will be overwritten.
"""Load the split-inference guard (BUILD_PROMPT §19.10, interpretation 11.13).

Installed as ``/usr/local/libexec/pinka/split-guard`` by the firewall role on
lab and inference nodes and run by ``pinka-split-guard.service``. The
split-inference ports on the tailnet interface (``/etc/pinka/nftables/
split-guard.json``: the forwarded vLLM API and the control endpoint of an
inference node, the OTLP port of a lab node) accept only the paired peer's
tailnet address, read from ``/etc/pinka/inference/peer.json`` (written by
``pinka pair``) each time the guard loads; before a pairing they accept
nobody. ``pinka pair show`` opens the pairing port (the control port) to
every tailnet address for the pairing only: it adds the port to the set
``pairing`` with a timeout, removes it when the pairing ends, and loads the
guard again with the new peer. Mutual TLS still guards every connection; the
guard keeps every other tailnet node from reaching the ports at all. Only the
table ``inet pinka_split_guard`` is replaced. Standard library only.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

TABLE = "pinka_split_guard"
SPEC = Path("/etc/pinka/nftables/split-guard.json")
NFT = "/usr/sbin/nft"
MAX_PORT = 65535


class GuardError(ValueError):
    """The settings or the pairing record cannot be used."""


def peer_address(record: Mapping[str, Any] | None) -> str | None:
    """The paired peer's tailnet address from ``peer.json``.

    Args:
        record: The pairing record, or None before a pairing.

    Returns:
        The IPv4 address, or None when there is no pairing.

    Raises:
        GuardError: If the record names no IPv4 address (a pairing made
            before the address was recorded: pair the nodes again).
    """
    if record is None:
        return None
    text = str(record.get("address") or "")
    try:
        return str(ipaddress.IPv4Address(text))
    except ValueError:
        raise GuardError(
            f"the pairing record has no IPv4 tailnet address ({text or 'none'}); pair again"
        ) from None


def ruleset(interface: str, ports: Sequence[int], peer: str | None) -> str:
    """The guard's nftables table.

    Args:
        interface: The tailnet interface.
        ports: The split-inference ports on it.
        peer: The paired peer's address, or None (nobody gets in).

    Returns:
        The ruleset (``nft -f`` input).
    """
    port_list = ", ".join(str(p) for p in sorted(set(ports)))
    lines = [
        f"table inet {TABLE}",
        f"delete table inet {TABLE}",
        "",
        f"table inet {TABLE} {{",
        "\tset peer {",
        "\t\ttype ipv4_addr",
    ]
    if peer:
        lines.append(f"\t\telements = {{ {peer} }}")
    lines += [
        "\t}",
        "",
        "\tset pairing {",
        "\t\ttype inet_service",
        "\t\tflags timeout",
        "\t}",
        "",
        "\tchain input {",
        "\t\ttype filter hook input priority filter - 10; policy accept;",
    ]
    if ports:
        lines += [
            f'\t\tiifname "{interface}" tcp dport @pairing accept',
            f'\t\tiifname "{interface}" tcp dport {{ {port_list} }} ip saddr @peer accept',
            f'\t\tiifname "{interface}" tcp dport {{ {port_list} }} drop',
        ]
    lines += ["\t}", "}", ""]
    return "\n".join(lines)


def build(spec: Mapping[str, Any], record: Mapping[str, Any] | None) -> str:
    """The ruleset for this host.

    Args:
        spec: The guard's settings (``interface``, ``ports``).
        record: The pairing record, or None.

    Returns:
        The ruleset.

    Raises:
        GuardError: If the settings or the record cannot be used.
    """
    interface = str(spec.get("interface") or "")
    ports = spec.get("ports", [])
    if (
        not interface
        or not isinstance(ports, list)
        or not all(isinstance(p, int) and 0 < p <= MAX_PORT for p in ports)
    ):
        raise GuardError("the settings need an interface and a list of ports")
    return ruleset(interface, ports, peer_address(record))


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    return data if isinstance(data, dict) else None


def main(argv: Sequence[str]) -> int:
    """Load the guard, or print it.

    Without a usable pairing record the ports accept nobody, and the guard
    still loads; the exit status says so.

    Args:
        argv: Arguments without the program name.

    Returns:
        Exit status: 0 loaded, 1 on an error (a broken record loads the
        guard closed and fails).
    """
    parser = argparse.ArgumentParser(prog="split-guard", description=__doc__.splitlines()[0])
    parser.add_argument("--spec", type=Path, default=SPEC)
    parser.add_argument("--print", action="store_true", help="print the ruleset, load nothing")
    args = parser.parse_args(list(argv))
    status = 0
    try:
        spec = _load_json(args.spec)
        if spec is None:
            sys.stderr.write(f"split-guard: {args.spec} is missing\n")
            return 1
        record_path = Path(str(spec.get("peer_record") or "/etc/pinka/inference/peer.json"))
        try:
            text = build(spec, _load_json(record_path))
        except GuardError as exc:
            if "pair again" not in str(exc):
                raise
            # Fail closed: nobody reaches the ports until the nodes pair again.
            sys.stderr.write(f"split-guard: {exc}\n")
            text, status = build(spec, None), 1
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"split-guard: {exc}\n")
        return 1
    if args.print:
        sys.stdout.write(text)
        return status
    done = subprocess.run([NFT, "-f", "-"], input=text, text=True, check=False)  # noqa: S603
    return done.returncode or status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
