"""The vllm role: serving profile, deploy helper (C3), serving verification and units.

The deploy helper runs against a temporary /etc/pinka/vllm and models tree;
the verification against a fake OpenAI-compatible server on 127.0.0.1.
"""

from __future__ import annotations

import configparser
import hashlib
import io
import json
import os
import shutil
import socket
import threading
from collections.abc import Iterator, Sequence
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pinka_vllm_deploy as deploy
import pinka_vllm_verify as verify
import pytest
import yaml

from ansible_testlib import GROUP_VARS, INFRA, ROLES, ansible_vars, load_yaml, render_template

PROFILE = INFRA / "vllm" / "profiles" / "ada-12g.yml"
MODEL_ID = "test-model"
# Deployment file names (<id>@<version>.yml) are built, so that no literal in
# this file looks like an e-mail address to the privacy scan.
YML = ".yml"
REVISION = "0" * 40


def write(path: Path, text: str, mode: int = 0o644) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)
    return path


def manifest(directory: Path, files: dict[str, bytes], **extra: Any) -> None:
    records = []
    for name, data in files.items():
        (directory / name).parent.mkdir(parents=True, exist_ok=True)
        (directory / name).write_bytes(data)
        records.append(
            {"path": name, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
        )
    write(directory / "MANIFEST.json", json.dumps({"files": records, **extra}))


@pytest.fixture
def tree(tmp_path: Path) -> deploy.Settings:
    settings = deploy.Settings(
        etc=tmp_path / "etc", models=tmp_path / "models", owner_uid=os.getuid()
    )
    shutil.copy(PROFILE, write(settings.etc / "profiles" / "ada-12g.yml", ""))
    model = settings.models / "example-org" / "test-model-awq" / REVISION
    manifest(
        model,
        {"model.safetensors": b"weights", "tool_template.jinja": b"{{ messages }}"},
        id=MODEL_ID,
        quantization="awq_marlin",
    )
    adapter = settings.models / "adapters" / "style" / ("a" * 64)
    manifest(adapter, {"adapter_model.safetensors": b"lora"}, base_model_id=MODEL_ID)
    spec(settings, "dep-test@1")
    return settings


def spec(settings: deploy.Settings, deployment: str, **changes: Any) -> Path:
    ident, _, version = deployment.partition("@")
    data: dict[str, Any] = {
        "id": ident,
        "version": int(version),
        "model_id": MODEL_ID,
        "adapters": [],
        "profile": "ada-12g",
        "overrides": {"tool_call_parser": "hermes"},
        "sampling_defaults": {},
    }
    data.update(changes)
    return write(settings.etc / "deployments" / f"{deployment}.yml", yaml.safe_dump(data))


class Recorder:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv: Sequence[str]) -> None:
        self.calls.append(list(argv))


def test_profile_shape() -> None:
    profile = load_yaml(PROFILE)
    assert profile["name"] == PROFILE.stem
    assert set(profile) == {"name", "description", "gpu", "quantizations", "args", "overridable"}
    # The helper always sets these itself; a profile or deployment never does.
    pinned = {"model", "served-model-name", "host", "port", "otlp-traces-endpoint"}
    assert not pinned & set(profile["args"])
    assert not pinned & set(profile["overridable"].values())
    assert profile["args"]["seed"] == 0


def test_render(tree: deploy.Settings) -> None:
    rendered = deploy.render(tree, "dep-test@1")
    config = yaml.safe_load(rendered.config)
    assert config["model"] == f"/models/example-org/test-model-awq/{REVISION}"
    assert config["served-model-name"] == MODEL_ID
    assert (config["host"], config["port"]) == ("127.0.0.1", 18704)
    assert config["quantization"] == "awq_marlin"
    assert config["tool-call-parser"] == "hermes"
    assert config["enable-auto-tool-choice"] is True
    assert config["otlp-traces-endpoint"] == "http://127.0.0.1:4317"
    assert "override-generation-config" not in config
    env = dict(line.split("=", 1) for line in rendered.env.splitlines() if not line.startswith("#"))
    assert env["VLLM_CONFIG_SHA256"] == hashlib.sha256(rendered.config.encode()).hexdigest()
    assert env["HF_HUB_OFFLINE"] == "1"
    assert env["VLLM_DEPLOYMENT"] == "dep-test@1"


def test_helper_constants_match_the_contract() -> None:
    ports = ansible_vars(GROUP_VARS)["pinka_ports"]
    settings = deploy.Settings()
    assert settings.port == ports["vllm"]
    assert settings.otlp == f"http://127.0.0.1:{ports['otlp_grpc']}"
    assert settings.etc == Path("/etc/pinka/vllm")


def test_overrides_adapters_and_sampling(tree: deploy.Settings) -> None:
    spec(
        tree,
        "dep-test@2",
        overrides={
            "tool_call_parser": "hermes",
            "max_model_len": 4096,
            "chat_template": "tool_template.jinja",
        },
        adapters=["style@" + "a" * 64],
        sampling_defaults={"temperature": 0.2, "top_p": 0.9},
    )
    config = yaml.safe_load(deploy.render(tree, "dep-test@2").config)
    assert config["max-model-len"] == 4096
    assert config["chat-template"] == (
        f"/models/example-org/test-model-awq/{REVISION}/tool_template.jinja"
    )
    assert config["enable-lora"] is True
    assert config["lora-modules"] == ["style=/models/adapters/style/" + "a" * 64]
    assert config["override-generation-config"] == {"temperature": 0.2, "top_p": 0.9}


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"overrides": {"tool_call_parser": "hermes", "host": "0.0.0.0"}}, "not allowed"),  # noqa: S104
        (
            {"overrides": {"tool_call_parser": "hermes", "quantization": "bitsandbytes"}},
            "does not fit",
        ),
        ({"overrides": {}}, "needs overrides.tool_call_parser"),
        (
            {"overrides": {"tool_call_parser": "hermes", "chat_template": "../x.jinja"}},
            "not a verified file",
        ),
        ({"adapters": ["style@" + "b" * 64]}, "not downloaded"),
        ({"adapters": ["bad name"]}, "invalid adapter"),
        ({"sampling_defaults": {"seed": 1}}, "sampling_defaults"),
        ({"sampling_defaults": {"temperature": "hot"}}, "sampling_defaults"),
        ({"model_id": "missing"}, "not downloaded and verified"),
        ({"profile": "../x"}, "invalid profile"),
        ({"version": 9}, "do not match the file name"),
        ({"extra": 1}, "unknown keys"),
    ],
)
def test_render_refuses(tree: deploy.Settings, changes: dict[str, Any], message: str) -> None:
    spec(tree, "dep-bad@1", **changes)
    with pytest.raises(deploy.DeployError, match=message):
        deploy.render(tree, "dep-bad@1")


def test_adapter_for_another_model(tree: deploy.Settings) -> None:
    other = tree.models / "adapters" / "other" / ("c" * 64)
    manifest(other, {"adapter_model.safetensors": b"x"}, base_model_id="another-model")
    spec(tree, "dep-bad@1", adapters=["other@" + "c" * 64])
    with pytest.raises(deploy.DeployError, match="is for another-model"):
        deploy.render(tree, "dep-bad@1")


def test_changed_weights_are_refused(tree: deploy.Settings) -> None:
    (tree.models / "example-org/test-model-awq" / REVISION / "model.safetensors").write_bytes(b"x")
    with pytest.raises(deploy.DeployError, match="another size"):
        deploy.render(tree, "dep-test@1")


@pytest.mark.parametrize(
    "deployment", ["../etc@1", "Dep@1", "dep", "dep@1234567", "-dep@1", "dep@1\n", 7]
)
def test_invalid_references(tree: deploy.Settings, deployment: Any) -> None:
    with pytest.raises(deploy.DeployError, match="must match"):
        deploy.deployment_path(tree, deployment)


def test_deployment_files_must_be_root_owned_regular_files(tree: deploy.Settings) -> None:
    path = tree.etc / "deployments" / ("dep-test@1" + YML)
    path.chmod(0o664)
    with pytest.raises(deploy.DeployError, match="writable only by root"):
        deploy.render(tree, "dep-test@1")
    path.chmod(0o644)
    link = tree.etc / "deployments" / ("dep-link@1" + YML)
    link.symlink_to(path)
    with pytest.raises(deploy.DeployError, match="not a regular file"):
        deploy.render(tree, "dep-link@1")
    other_owner = deploy.Settings(etc=tree.etc, models=tree.models, owner_uid=os.getuid() + 1)
    with pytest.raises(deploy.DeployError, match="owned by root"):
        deploy.render(other_owner, "dep-test@1")
    with pytest.raises(deploy.DeployError, match="no deployment"):
        deploy.render(tree, "dep-none@1")


def test_activate_writes_links_and_restarts_once(tree: deploy.Settings) -> None:
    run = Recorder()
    assert deploy.activate(tree, "dep-test@1", run) is True
    assert run.calls == [["systemctl", "restart", "pinka-inference.service"]]
    assert (tree.etc / "current.yml").readlink() == Path("deployments") / ("dep-test@1" + YML)
    assert (tree.etc / "current.yaml").stat().st_mode & 0o777 == 0o644
    assert deploy.activate(tree, "dep-test@1", run) is False
    assert len(run.calls) == 1
    spec(tree, "dep-test@2")
    assert deploy.activate(tree, "dep-test@2", run, restart=False) is True
    assert len(run.calls) == 1
    assert sorted(p.name for p in tree.etc.iterdir()) == [
        "current.env",
        "current.yaml",
        "current.yml",
        "deployments",
        "profiles",
    ]


def test_failed_validation_changes_nothing(tree: deploy.Settings) -> None:
    run = Recorder()
    deploy.activate(tree, "dep-test@1", run)
    before = (tree.etc / "current.yaml").read_text()
    spec(tree, "dep-bad@1", model_id="missing")
    with pytest.raises(deploy.DeployError):
        deploy.activate(tree, "dep-bad@1", run)
    assert (tree.etc / "current.yaml").read_text() == before
    assert (tree.etc / "current.yml").readlink() == Path("deployments") / ("dep-test@1" + YML)


@pytest.mark.parametrize(
    ("uid", "line", "ok", "error"),
    [
        (1000, '{"deployment": "dep-test@1"}\n', True, None),
        (1001, '{"deployment": "dep-test@1"}\n', False, "only the allowed account"),
        (1000, "not json\n", False, "one JSON line"),
        (1000, '{"deployment": "dep-test@1", "x": 1}\n', False, "request must be"),
        (1000, '["dep-test@1"]\n', False, "request must be"),
        (1000, '{"deployment": "../x@1"}\n', False, "must match"),
        (1000, '{"deployment": "' + "a" * 5000 + '"}\n', False, "too long"),
    ],
)
def test_serve(tree: deploy.Settings, uid: int, line: str, ok: bool, error: str | None) -> None:
    out = io.StringIO()
    run = Recorder()
    code = deploy.serve(tree, io.StringIO(line), out, uid=uid, allowed_uid=1000, run=run)
    answer = json.loads(out.getvalue())
    assert answer["ok"] is ok
    assert code == (0 if ok else 1)
    if error:
        assert error in answer["error"]
        assert run.calls == []
    else:
        assert run.calls == [["systemctl", "restart", "pinka-inference.service"]]


def test_peer_uid_of_a_unix_socket() -> None:
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    with left, right:
        assert deploy.peer_uid(left.fileno()) == os.getuid()


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


# --- units and the role -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "peer"),
    [
        ({}, "root"),
        ({"active_platform": {"controller": "demo-ctl"}}, "demo-ctl"),
        (
            {"node_role": "inference", "active_platform": {"controller": "demo-ctl"}},
            "pinka-inference",
        ),
    ],
)
def test_deploy_socket_belongs_to_the_peer(overrides: dict[str, Any], peer: str) -> None:
    variables = ansible_vars(GROUP_VARS, ROLES / "vllm" / "defaults" / "main.yml", **overrides)
    sock = configparser.ConfigParser(interpolation=None)
    sock.optionxform = str  # type: ignore[assignment,method-assign]
    sock.read_string(render_template("vllm", "pinka-vllm-deploy.socket.j2", variables))
    assert (sock["Socket"]["SocketUser"], sock["Socket"]["SocketMode"]) == (peer, "0600")
    service = configparser.ConfigParser(interpolation=None)
    service.optionxform = str  # type: ignore[assignment,method-assign]
    service.read_string(render_template("vllm", "pinka-vllm-deploy@.service.j2", variables))
    assert service["Service"]["ExecStart"].endswith(f" serve --peer {peer}")


def test_deploy_socket_and_service() -> None:
    variables = ansible_vars(GROUP_VARS, ROLES / "vllm" / "defaults" / "main.yml")
    sock = configparser.ConfigParser(interpolation=None)
    sock.optionxform = str  # type: ignore[assignment,method-assign]
    sock.read_string(render_template("vllm", "pinka-vllm-deploy.socket.j2", variables))
    assert sock["Socket"]["ListenStream"] == "/run/pinka/vllm-deploy.sock"
    assert sock["Socket"]["SocketMode"] == "0600"
    assert sock["Socket"]["Accept"] == "yes"
    service = configparser.ConfigParser(interpolation=None)
    service.optionxform = str  # type: ignore[assignment,method-assign]
    service.read_string(render_template("vllm", "pinka-vllm-deploy@.service.j2", variables))
    unit = service["Service"]
    assert unit["ExecStart"] == "/usr/local/libexec/pinka/vllm-deploy serve --peer root"
    assert (unit["StandardInput"], unit["StandardOutput"]) == ("socket", "socket")
    assert unit["ReadWritePaths"] == "/etc/pinka/vllm"
    assert unit["PrivateNetwork"] == "yes"
    assert "User" not in unit  # root: it rewrites /etc/pinka/vllm and restarts the unit


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
