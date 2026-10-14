"""The vllm role: serving verification and the inference Compose project.

The verification runs against a fake OpenAI-compatible server on 127.0.0.1.
The deployment rendering moved with contract v1's deploy helper into the GPU
arbiter (test_gpu_arbiter.py).
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pinka_vllm_verify as verify
import pytest

from ansible_testlib import INFRA, load_yaml

MODEL_ID = "test-model"


# --- serving verification ----------------------------------------------------------------


def fake_vllm(tool_calls: list[dict[str, Any]] | None, models: list[str]) -> verify.Request:
    def request(method: str, path: str, body: Any) -> tuple[int, Any]:
        if path == "/health":
            return 200, None
        if path == "/v1/models":
            return 200, {"data": [{"id": m} for m in models]}
        assert body["temperature"] == 0
        if "tools" in body:
            return 200, {"choices": [{"message": {"content": None, "tool_calls": tool_calls}}]}
        return 200, {"choices": [{"message": {"content": "ready"}}]}

    return request


GOOD_CALL = [{"function": {"name": "get_time", "arguments": '{"timezone": "UTC"}'}}]


@pytest.mark.parametrize(
    ("calls", "models", "ok", "error"),
    [
        (GOOD_CALL, [MODEL_ID], True, None),
        ([], [MODEL_ID], False, "tool-call parser"),
        (
            [{"function": {"name": "get_time", "arguments": "not json"}}],
            [MODEL_ID],
            False,
            "tool-call parser",
        ),
        (
            [{"function": {"name": "other", "arguments": "{}"}}],
            [MODEL_ID],
            False,
            "tool-call parser",
        ),
        (GOOD_CALL, ["other"], False, "does not list"),
    ],
)
def test_verify(
    calls: list[dict[str, Any]], models: list[str], ok: bool, error: str | None
) -> None:
    report = verify.verify(fake_vllm(calls, models), MODEL_ID, 0)
    assert report["ok"] is ok
    if error:
        assert error in report["error"]
    else:
        assert report["tool_call"] == {
            "status": 200,
            "name": "get_time",
            "arguments": {"timezone": "UTC"},
            "ok": True,
        }


def test_verify_gives_up_when_health_never_answers() -> None:
    def down(method: str, path: str, body: Any) -> tuple[int, Any]:
        raise OSError("connection refused")

    report = verify.verify(down, MODEL_ID, 0, sleep=lambda _: None)
    assert report["ok"] is False
    assert "/health" in report["error"]


class FakeServer(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:
        pass

    def reply(self, body: Any) -> None:
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        self.reply({"data": [{"id": MODEL_ID}]} if self.path == "/v1/models" else {})

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        message = (
            {"content": None, "tool_calls": GOOD_CALL} if "tools" in body else {"content": "ready"}
        )
        self.reply({"choices": [{"message": message}]})


@pytest.fixture
def server() -> Iterator[int]:
    httpd = HTTPServer(("127.0.0.1", 0), FakeServer)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1]
    httpd.shutdown()


def test_verify_main_writes_the_report(server: int, tmp_path: Path) -> None:
    out = tmp_path / "verify" / ("vllm-dep-test@1" + ".json")
    argv = ["--port", str(server), "--model", MODEL_ID, "--deployment", "dep-test@1"]
    assert verify.main([*argv, "--out", str(out), "--wait", "5"]) == 0
    report = json.loads(out.read_text())
    assert report["ok"] is True
    assert report["deployment"] == "dep-test@1"
    assert report["checked_at"].endswith("Z")


# --- the inference project -------------------------------------------------------------


def test_inference_compose() -> None:
    compose = load_yaml(INFRA / "compose/inference/compose.yml")
    for name in ("vllm", "vllm_runtime"):
        service = compose["services"][name]
        assert service["network_mode"] == "host"
        assert service["command"] == ["--config", "/etc/pinka/vllm/current.yaml"]
        assert "/etc/pinka/vllm:/etc/pinka/vllm:ro" in service["volumes"]
        assert "${PINKA_ROOT:?}/models:/models:ro" in service["volumes"]
    assert compose["services"]["vllm"]["profiles"] == ["gpu-cdi"]
    assert compose["services"]["vllm_runtime"]["profiles"] == ["gpu-runtime"]
