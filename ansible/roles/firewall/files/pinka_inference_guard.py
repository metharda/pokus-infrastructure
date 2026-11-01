#!/opt/pinka/python/bin/python3 -I
# Managed by pinka (ansible/roles/firewall/files/pinka_inference_guard.py). Local changes will be overwritten.
"""Load the inference guard (ADR-0016).

Installed as ``/usr/local/libexec/pinka/inference-guard`` by the firewall
role and run by ``pinka-inference-guard.service``. Only infra's own clients
(``/etc/pinka/nftables/inference-guard.json``: root, the scrapers, the
control endpoint of an inference node) and the active platform's accounts
whose profile reaches the inference API (``inference_clients`` of
``/etc/pinka/platform/rendered.json``: the controller and the inference
broker) connect to the inference API on the host loopback: vLLM, or the
inference forwarder on a lab node. Only root and the controller connect to
the forwarder's control port. Every other account's connection is reset.
The sets hold UIDs, resolved when the guard loads; the platform role loads
it again after every deployment. Only the table ``inet
pinka_inference_guard`` is replaced. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import pwd
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

TABLE = "pinka_inference_guard"
SPEC = Path("/etc/pinka/nftables/inference-guard.json")
RENDERED = Path("/etc/pinka/platform/rendered.json")
NFT = "/usr/sbin/nft"


def _uid(name: str) -> int | None:
    try:
        return pwd.getpwnam(name).pw_uid
    except KeyError:
        return None


def clients(
    spec: Mapping[str, Any], rendered: Mapping[str, Any] | None
) -> tuple[list[str], list[str]]:
    """The accounts of the API's set and of the control port's set.

    Args:
        spec: ``clients`` (infra's) of the guard's settings.
        rendered: The active platform's rendering, or None without one.

    Returns:
        API clients and control port clients, by name.
    """
    api = list(spec.get("clients", []))
    control = ["root"]
    if rendered:
        api += [str(a) for a in rendered.get("inference_clients", [])]
        if rendered.get("controller"):
            control.append(str(rendered["controller"]))
    return list(dict.fromkeys(api)), control


def ruleset(
    api: Sequence[int], control: Sequence[int], ports: Sequence[int], control_ports: Sequence[int]
) -> str:
    """The guard's nftables table.

    Args:
        api: UIDs that reach the API ports.
        control: UIDs that reach the control ports.
        ports: The API's loopback ports.
        control_ports: The control API's loopback ports (a lab node's forwarder).

    Returns:
        The ruleset (``nft -f`` input).
    """

    def elements(uids: Sequence[int]) -> str:
        return ", ".join(str(u) for u in sorted({0, *uids}))

    def port_list(values: Sequence[int]) -> str:
        return ", ".join(str(p) for p in sorted(set(values)))

    lines = [
        f"table inet {TABLE}",
        f"delete table inet {TABLE}",
        "",
        f"table inet {TABLE} {{",
        "\tset inference_clients {",
        "\t\ttype uid",
        f"\t\telements = {{ {elements(api)} }}",
        "\t}",
    ]
    if control_ports:
        lines += [
            "",
            "\tset inference_control_clients {",
            "\t\ttype uid",
            f"\t\telements = {{ {elements(control)} }}",
            "\t}",
        ]
    lines += [
        "",
        "\tchain output {",
        "\t\ttype filter hook output priority filter; policy accept;",
        f'\t\toifname "lo" tcp dport {{ {port_list(ports)} }} '
        "meta skuid != @inference_clients reject with tcp reset",
    ]
    if control_ports:
        lines.append(
            f'\t\toifname "lo" tcp dport {{ {port_list(control_ports)} }} '
            "meta skuid != @inference_control_clients reject with tcp reset"
        )
    lines += ["\t}", "}", ""]
    return "\n".join(lines)


def build(
    spec: Mapping[str, Any],
    rendered: Mapping[str, Any] | None,
    uid_of: Callable[[str], int | None] = _uid,
) -> tuple[str, list[str]]:
    """The ruleset for this host, and the accounts that do not exist.

    Args:
        spec: The guard's settings.
        rendered: The active platform's rendering, or None.
        uid_of: Account name to UID (None if missing).

    Returns:
        The ruleset and the missing accounts (left out of the sets).
    """
    api_names, control_names = clients(spec, rendered)
    missing = [n for n in dict.fromkeys(api_names + control_names) if uid_of(n) is None]
    api = [u for u in (uid_of(n) for n in api_names) if u is not None]
    control = [u for u in (uid_of(n) for n in control_names) if u is not None]
    text = ruleset(api, control, spec.get("ports", []), spec.get("control_ports", []))
    return text, missing


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    return data if isinstance(data, dict) else None


def main(argv: Sequence[str]) -> int:
    """Load the guard, or print it.

    Args:
        argv: Arguments without the program name.

    Returns:
        Exit status.
    """
    parser = argparse.ArgumentParser(prog="inference-guard", description=__doc__.splitlines()[0])
    parser.add_argument("--spec", type=Path, default=SPEC)
    parser.add_argument("--rendered", type=Path, default=RENDERED)
    parser.add_argument("--print", action="store_true", help="print the ruleset, load nothing")
    args = parser.parse_args(list(argv))
    try:
        spec = _load_json(args.spec)
        if spec is None:
            sys.stderr.write(f"inference-guard: {args.spec} is missing\n")
            return 1
        text, missing = build(spec, _load_json(args.rendered))
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"inference-guard: {exc}\n")
        return 1
    for name in missing:
        sys.stderr.write(f"inference-guard: account {name} does not exist; left out\n")
    if args.print:
        sys.stdout.write(text)
        return 0
    done = subprocess.run([NFT, "-f", "-"], input=text, text=True, check=False)  # noqa: S603
    return done.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
