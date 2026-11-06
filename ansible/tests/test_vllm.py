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
from ansible.errors import AnsibleValueOmittedError

from ansible_testlib import GROUP_VARS, INFRA, ROLES, ansible_vars, load_yaml, template_value

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


def test_vllm_runs_as_its_account_and_writes_only_its_mounts() -> None:
    # Not as the image's root: the stores' and the inference API's guards let
    # root through. Its root file system is read-only; it writes only the
    # cache directory (pinka-vllm's) and a tmpfs /tmp. The arbiter puts
    # every cache and the home there (test_gpu_arbiter.py,
    # test_vllm_writes_only_below_its_writable_mounts).
    for service in vllm_services():
        assert service["user"] == "${VLLM_UID:?}:${VLLM_GID:?}"
        assert service["read_only"] is True
        assert service["cap_drop"] == ["ALL"]
        assert writable_mounts(service) == ["/cache", "/tmp"]  # noqa: S108 - no file is made
        assert "${PINKA_ROOT:?}/state/vllm-cache:/cache" in service["volumes"]
        assert not any("/root" in v for v in service["volumes"])


def vllm_services() -> list[dict[str, Any]]:
    """vLLM's two services (one per GPU method)."""
    compose = load_yaml(INFRA / "compose/inference/compose.yml")
    return [compose["services"][name] for name in ("vllm", "vllm_runtime")]


def writable_mounts(service: dict[str, Any]) -> list[str]:
    """Where a container may write: its binds without ``:ro``, then its tmpfs mounts."""
    binds = [v.rsplit(":", 1)[1] for v in service["volumes"] if not v.endswith(":ro")]
    return binds + list(service.get("tmpfs", []))


def test_the_cache_directory_belongs_to_vllms_account() -> None:
    tasks = load_yaml(ROLES / "vllm" / "tasks" / "main.yml")
    by_name = {t["name"]: t for t in tasks}
    passwd = {"pinka-vllm": ["x", "951", "941", "", "/var/lib/pinka/pinka-vllm", ""]}
    variables = ansible_vars(GROUP_VARS, ROLES / "vllm" / "defaults" / "main.yml") | {
        "ansible_facts": {"getent_passwd": passwd}
    }
    found = by_name["Find the UID and GID of vLLM's account"]["ansible.builtin.set_fact"]
    ids = template_value(found["vllm_ids"], variables)
    assert ids == {"vllm": {"uid": "951", "gid": "941"}}
    create = by_name["Create the vLLM directories"]
    (cache,) = [i for i in create["loop"] if str(i["path"]).endswith("/state/vllm-cache")]
    # Ansible templates the loop item first, then the task's arguments.
    with_ids = variables | {"vllm_ids": ids, "pinka_root": "/srv/pinka"}
    item = with_ids | {"item": {k: template_value(v, with_ids) for k, v in cache.items()}}
    assert template_value(create["ansible.builtin.file"]["owner"], item) == "951"
    assert template_value(create["ansible.builtin.file"]["group"], item) == "941"
    assert cache["mode"] == "0700"
    # The other directories stay root's.
    for other in create["loop"]:
        if other is not cache:
            assert "owner" not in other and "group" not in other, other["path"]
    check = by_name["Check that vLLM's account exists"]
    assert check["when"] == "not ansible_check_mode"
    # Check mode on a fresh host (no account yet): the owner is left out,
    # and the item keeps its key (an omit inside an item would drop it).
    empty = variables | {"vllm_ids": {"vllm": {"uid": "", "gid": ""}}, "pinka_root": "/srv/pinka"}
    # The loop as Ansible templates it, then the item.
    loop = template_value(create["loop"], empty)
    assert [i["owner"] for i in loop if "owner" in i] == [""]
    templated = {k: template_value(v, empty) for k, v in cache.items()}
    assert templated["owner"] == ""
    for key in ("owner", "group"):
        with pytest.raises(AnsibleValueOmittedError):
            template_value(create["ansible.builtin.file"][key], empty | {"item": templated})
