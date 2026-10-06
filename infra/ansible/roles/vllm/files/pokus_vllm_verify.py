#!/opt/pokus/python/bin/python3 -I
# Managed by pokus-infra (ansible/roles/vllm/files/pokus_vllm_verify.py). Local changes will be overwritten.
"""Check that vLLM serves a deployment, including tool calls (BUILD_PROMPT §7.11).

Installed as ``/usr/local/libexec/pokus/vllm-verify`` by the vllm role.
Waits for ``/health``, lists ``/v1/models``, sends one deterministic chat
completion and one request with a dummy ``get_time`` tool, and asserts that a
well-formed tool call comes back. The report goes to the JSON file given with
``--out`` (``/var/lib/pokus/verify/vllm-<deployment>.json``). Standard library
only; requests go to 127.0.0.1 without any proxy.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

TOOL = {
    "type": "function",
    "function": {
        "name": "get_time",
        "description": "Return the current time in a time zone.",
        "parameters": {
            "type": "object",
            "properties": {"timezone": {"type": "string", "description": "IANA time zone"}},
            "required": ["timezone"],
        },
    },
}
TOOL_HINT = (
    "No well-formed tool call came back. The usual cause is the tool-call parser or the chat "
    "template: check overrides.tool_call_parser of the deployment against the model "
    "(models.catalog.yml) and whether the model's chat template supports tools."
)
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
Request = Callable[[str, str, Any], tuple[int, Any]]


def http(
    base: str,
    method: str,
    path: str,
    body: Any = None,  # noqa: ANN401 - any JSON value
    timeout: float = 120,
) -> tuple[int, Any]:
    """Send one JSON request.

    Args:
        base: Base URL (``http://127.0.0.1:<port>``).
        method: HTTP method.
        path: Path.
        body: JSON body or None.
        timeout: Seconds.

    Returns:
        Status and decoded JSON (None for an empty body).
    """
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(  # noqa: S310 - loopback URL built here
        base + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with OPENER.open(request, timeout=timeout) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw.strip() else None
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw) if raw.strip() else None
        except ValueError:
            return exc.code, raw.decode(errors="replace")


def verify(
    request: Request, model: str, wait_seconds: float, sleep: Callable[[float], None] = time.sleep
) -> dict[str, Any]:
    """Run the checks.

    Args:
        request: ``(method, path, body) -> (status, json)``.
        model: Served model name.
        wait_seconds: How long to wait for /health.
        sleep: Sleep function.

    Returns:
        The report; ``ok`` is True only if every check passed.
    """
    report: dict[str, Any] = {"model": model, "ok": False}
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            status, _ = request("GET", "/health", None)
        except (urllib.error.URLError, OSError):
            status = 0
        if status == 200:  # noqa: PLR2004
            break
        if time.monotonic() >= deadline:
            report["error"] = f"/health did not answer 200 within {wait_seconds:.0f} s"
            return report
        sleep(5)
    report["health"] = True
    status, models = request("GET", "/v1/models", None)
    served = [m.get("id") for m in (models or {}).get("data", [])] if status == 200 else []  # noqa: PLR2004
    report["models"] = served
    if model not in served:
        report["error"] = f"/v1/models does not list {model}: {served}"
        return report
    chat = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with the single word: ready"}],
        "temperature": 0,
        "seed": 0,
        "max_tokens": 32,
    }
    status, answer = request("POST", "/v1/chat/completions", chat)
    content = ""
    if status == 200:  # noqa: PLR2004
        content = (answer["choices"][0]["message"].get("content") or "").strip()
    report["chat"] = {"status": status, "content": content}
    if status != 200 or not content:  # noqa: PLR2004
        report["error"] = "the chat completion failed or came back empty"
        return report
    tools = {
        "model": model,
        "messages": [{"role": "user", "content": "What time is it in UTC? Use the get_time tool."}],
        "tools": [TOOL],
        "tool_choice": "auto",
        "temperature": 0,
        "seed": 0,
        "max_tokens": 256,
    }
    status, answer = request("POST", "/v1/chat/completions", tools)
    calls = (answer["choices"][0]["message"].get("tool_calls") or []) if status == 200 else []  # noqa: PLR2004
    call = calls[0]["function"] if calls else {}
    try:
        arguments = json.loads(call.get("arguments") or "null")
    except ValueError:
        arguments = None
    well_formed = call.get("name") == "get_time" and isinstance(arguments, dict)
    report["tool_call"] = {
        "status": status,
        "name": call.get("name"),
        "arguments": arguments,
        "ok": well_formed,
    }
    if not well_formed:
        report["error"] = TOOL_HINT
        return report
    report["ok"] = True
    return report


def main(argv: list[str]) -> int:
    """Verify and write the report.

    Args:
        argv: Arguments without the program name.

    Returns:
        0 if every check passed, 1 otherwise.
    """
    parser = argparse.ArgumentParser(prog="vllm-verify", description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=18704)
    parser.add_argument("--model", required=True, help="served model name (the model id)")
    parser.add_argument("--deployment", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--wait", type=float, default=1200, help="seconds to wait for /health")
    args = parser.parse_args(argv)
    base = f"http://127.0.0.1:{args.port}"
    report = verify(lambda m, p, b: http(base, m, p, b), args.model, args.wait)
    report["deployment"] = args.deployment
    report["checked_at"] = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if not report["ok"]:
        sys.stderr.write(f"vllm-verify: {report.get('error')}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
