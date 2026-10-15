"""``refplat-agent``: say hello on the session, probe the isolation, report it.

The agent sees only its package, its environment, the dedicated ``session``
socket and infra's agent OTLP socket (V6); everything else the probe tries
must fail. It reports the probe's result to the controller over the session
and repeats the probe every hour while it runs.
"""

from __future__ import annotations

import json
import socket
import sys
import time
from typing import Any

from refplat_agent import probe

SESSION = "/run/pinka/platform/ctl/session.sock"
REPEAT_SECONDS = 3600.0


def session(value: dict[str, Any]) -> dict[str, Any]:
    """One request on the session socket."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(30)
        conn.connect(SESSION)
        conn.sendall((json.dumps(value) + "\n").encode())
        conn.shutdown(socket.SHUT_WR)
        data = b""
        while chunk := conn.recv(65536):
            data += chunk
    answer = json.loads(data)
    return answer if isinstance(answer, dict) else {"ok": False}


def run_once(platform: str = probe.PLATFORM) -> dict[str, Any]:
    """Probe and report; returns the report.

    Args:
        platform: The deployed name the controller's hello gave.
    """
    endpoints, principals = probe.load(platform=platform)
    document = probe.report(probe.probe(endpoints, principals), "untrusted-agent")
    print(json.dumps(document, sort_keys=True), flush=True)
    session({"command": "report", "report": document})
    return document


def main() -> int:
    """Run until stopped."""
    try:
        hello = session({"command": "hello"})
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"refplat-agent: no session: {exc}\n")
        return 1
    if not hello.get("ok"):
        sys.stderr.write(f"refplat-agent: the controller refused: {hello}\n")
        return 1
    platform = str(hello.get("platform") or probe.PLATFORM)
    while True:
        try:
            run_once(platform)
        except (OSError, ValueError) as exc:
            sys.stderr.write(f"refplat-agent: report failed: {exc}\n")
        time.sleep(REPEAT_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
