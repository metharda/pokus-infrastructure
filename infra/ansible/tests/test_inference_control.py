"""The inference_control role and the inference control endpoint (BUILD_PROMPT §19.10).

The endpoint runs here for real: mutual TLS 1.3 with certificates shaped like
the role's (P-256, the tailnet name as subject alternative name, server and
client authentication, critical CA:FALSE), a fake vLLM and GPU exporter on
127.0.0.1, a fake deploy helper socket and a fake verification command. The
endpoint listens on 127.0.0.2, standing in for the tailnet address.
"""

from __future__ import annotations

import asyncio
import contextlib
import http.server
import json
import shutil
import socket
import ssl
import subprocess
import sys
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

import pinka_inference_control as control
import pytest

import pinka
from ansible_testlib import GROUP_VARS, ROLES, ansible_vars, evaluate, load_yaml, render_template

ROLE = ROLES / "inference_control"
HOST = "127.0.0.2"
needs_openssl = pytest.mark.skipif(shutil.which("openssl") is None, reason="needs openssl")


def scope(**extra: Any) -> dict[str, Any]:
    variables: dict[str, Any] = {"machine_name": "pinka-gpu", "node_role": "inference", **extra}
    return ansible_vars(
        GROUP_VARS, ROLE / "defaults" / "main.yml", ROLE / "vars" / "main.yml", **variables
    )


def test_expected_peer_by_node_role() -> None:
    inference = scope(inference_lab_node="pinka-lab", inference_remote_node="other")
    assert evaluate("inference_control_expected_peer", inference) == "pinka-lab"
    lab = scope(node_role="lab", inference_lab_node="other", inference_remote_node="pinka-gpu")
    assert evaluate("inference_control_expected_peer", lab) == "pinka-gpu"
    assert evaluate("inference_control_expected_peer", scope()) == ""


def test_settings_fit_the_endpoint(tmp_path: Path) -> None:
    variables = scope(inference_control_tailnet={"stdout_lines": ["100.64.0.7"]})
    path = tmp_path / "control.json"
    path.write_text(json.dumps(evaluate("inference_control_settings", variables)))
    config = control.Config.load(path)
    assert config.listen == "100.64.0.7"
    assert (config.api_port, config.control_port, config.verify_wait) == (18704, 18710, 60)
    assert (
        config.cert.parent
        == config.key.parent
        == config.peer.parent
        == Path("/etc/pinka/inference")
    )
    assert config.gpu_metrics == "http://127.0.0.1:18726/metrics"
    assert config.models == Path("/srv/pinka/models")
    socket_unit = render_template("vllm", "pinka-vllm-deploy.socket.j2", {"vllm_deploy_peer": "x"})
    assert f"ListenStream={config.deploy_socket}\n" in socket_unit
    verify_task = load_yaml(ROLES / "vllm" / "tasks" / "verify.yml")[1]
    assert verify_task["ansible.builtin.command"]["argv"][0] == config.verify[0]
    # Before the tailnet is up (check mode only) the address falls back to loopback.
    fallback = evaluate("inference_control_settings", scope(inference_control_tailnet={}))
    assert fallback["listen"] == "127.0.0.1"


def test_unit() -> None:
    unit = render_template("inference_control", "pinka-inference-control.service.j2", scope())
    assert unit.startswith("# Managed by pinka (ansible/roles/inference_control/templates/")
    lines = set(unit.splitlines())
    for expected in (
        "ExecStart=/usr/local/libexec/pinka/inference-control"
        " --config /etc/pinka/inference/control.json",
        "ConditionPathExists=/etc/pinka/inference/peer.crt",
        "User=pinka-inference",
        "CapabilityBoundingSet=",
        "NoNewPrivileges=yes",
        "ProtectSystem=strict",
        "MemoryDenyWriteExecute=yes",
        "StateDirectory=pinka/inference-control",
        "WantedBy=pinka-lab.target",
        "PartOf=pinka-lab.target",
    ):
        assert expected in lines, expected
    denied = next(line for line in lines if line.startswith("InaccessiblePaths="))
    assert "-/etc/pinka/secrets" in denied.split("=", 1)[1].split()


@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="needs systemd-analyze")
def test_unit_passes_systemd_analyze(tmp_path: Path) -> None:
    system = tmp_path / "etc" / "systemd" / "system"
    system.mkdir(parents=True)
    shutil.copytree(
        "/usr/lib/systemd/system", tmp_path / "usr" / "lib" / "systemd" / "system", symlinks=True
    )
    (system / "pinka-lab.target").write_text("[Unit]\nDescription=stub\n")
    program = tmp_path / "usr/local/libexec/pinka/inference-control"
    program.parent.mkdir(parents=True)
    program.write_text("#!/bin/sh\n")
    program.chmod(0o755)
    name = "pinka-inference-control.service"
    (system / name).write_text(
        render_template("inference_control", "pinka-inference-control.service.j2", scope())
    )
    done = subprocess.run(
        [
            "systemd-analyze",
            f"--root={tmp_path}",
            "verify",
            "--man=no",
            f"etc/systemd/system/{name}",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert done.stderr.strip() == "", done.stderr


# --- the endpoint -------------------------------------------------------------------------------


def make_identity(directory: Path, name: str) -> tuple[Path, Path]:
    """A key and self-signed certificate with the extensions the role requests."""
    config = directory / f"{name}.cnf"
    config.write_text(
        "[req]\ndistinguished_name = dn\nx509_extensions = ext\nprompt = no\n"
        f"[dn]\nCN = {name}\n"
        "[ext]\nbasicConstraints = critical,CA:FALSE\n"
        "extendedKeyUsage = serverAuth,clientAuth\n"
        f"subjectAltName = DNS:{name}\nsubjectKeyIdentifier = hash\n"
    )
    key, cert = directory / f"{name}.key", directory / f"{name}.crt"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "ec",
            "-pkeyopt",
            "ec_paramgen_curve:P-256",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-config",
            str(config),
        ],
        check=True,
        capture_output=True,
    )
    return cert, key


def free_port(host: str = "127.0.0.1") -> int:
    with socket.socket() as probe:
        probe.bind((host, 0))
        port: int = probe.getsockname()[1]
        return port


class FakeHttp(http.server.BaseHTTPRequestHandler):
    """vLLM's and the GPU exporter's endpoints."""

    routes: ClassVar[dict[str, bytes]] = {
        "/health": b"",
        "/v1/models": json.dumps({"data": [{"id": "model-a"}]}).encode(),
        "/metrics": b"vllm_up 1\n",
        "/gpu": b"gpu_up 1\n",
    }

    def do_GET(self) -> None:
        body = self.routes.get(self.path)
        self.send_response(404 if body is None else 200)
        self.send_header("Content-Length", str(len(body or b"")))
        self.end_headers()
        self.wfile.write(body or b"")

    def log_message(self, format: str, *args: Any) -> None:
        """Quiet."""


@dataclass
class Endpoint:
    config: control.Config
    lab: tuple[Path, Path]
    inference_cert: Path
    mallory: tuple[Path, Path]
    deploy_requests: list[dict[str, Any]] = field(default_factory=list)

    def context(self, identity: tuple[Path, Path] | None) -> ssl.SSLContext:
        context = ssl.create_default_context(cafile=str(self.inference_cert))
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.verify_flags |= ssl.VERIFY_X509_STRICT
        if identity:
            context.load_cert_chain(*identity)
        return context

    def call(
        self,
        method: str,
        path: str,
        body: bytes = b"",
        *,
        port: int | None = None,
        identity: tuple[Path, Path] | None = None,
        anonymous: bool = False,
        raw: bytes | None = None,
    ) -> tuple[int, bytes]:
        """One request over mutual TLS; status 0 when the endpoint refuses the connection."""
        context = self.context(None if anonymous else identity or self.lab)
        request = (
            raw
            or (
                f"{method} {path} HTTP/1.1\r\nHost: pinka-gpu\r\nContent-Length: {len(body)}\r\n"
                "Connection: close\r\n\r\n"
            ).encode()
            + body
        )
        data = b""
        try:
            with (
                socket.create_connection((HOST, port or self.config.control_port), 10) as plain,
                context.wrap_socket(plain, server_hostname="pinka-gpu") as conn,
            ):
                conn.sendall(request)
                while chunk := conn.recv(65536):
                    data += chunk
        except (ssl.SSLError, OSError):
            return 0, b""
        head, _, payload = data.partition(b"\r\n\r\n")
        return (int(head.split()[1]), payload) if head else (0, b"")

    def json(self, method: str, path: str, body: bytes = b"") -> tuple[int, Any]:
        status, payload = self.call(method, path, body)
        return status, json.loads(payload)


def serve_unix(path: Path, requests: list[dict[str, Any]]) -> socket.socket:
    """The vLLM deploy helper's socket: records each request, answers ok."""
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    server.listen()

    def loop() -> None:
        while True:
            try:
                conn, _ = server.accept()
            except OSError:
                return
            with conn:
                line = conn.makefile("rb").readline()
                requests.append(json.loads(line))
                conn.sendall(b'{"ok": true}\n')

    threading.Thread(target=loop, daemon=True).start()
    return server


@pytest.fixture
def endpoint(tmp_path: Path) -> Iterator[Endpoint]:
    inference = make_identity(tmp_path, "pinka-gpu")
    lab = make_identity(tmp_path, "pinka-lab")
    mallory_dir = tmp_path / "mallory"
    mallory_dir.mkdir()
    mallory = make_identity(mallory_dir, "pinka-lab")  # same name, other key
    peer = tmp_path / "peer.crt"
    shutil.copy(lab[0], peer)
    api_port = free_port()
    vllm = http.server.ThreadingHTTPServer(("127.0.0.1", api_port), FakeHttp)
    gpu = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeHttp)
    for server in (vllm, gpu):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    (tmp_path / "current.yml").write_text(
        "id: dep-a\nversion: 2\nmodel_id: model-a\nprofile: ada-12g\noverrides:\n  x: 1\n"
    )
    manifest = tmp_path / "models" / "org" / "model-a" / "0123abc" / "MANIFEST.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"id": "model-a", "files": [{"path": "w", "sha256": "0" * 64}]}))
    adapter = tmp_path / "models" / "adapters" / "a" / ("1" * 64) / "MANIFEST.json"
    adapter.parent.mkdir(parents=True)
    adapter.write_text(json.dumps({"id": "model-a", "kind": "adapter"}))
    (tmp_path / "fingerprint.json").write_text(
        json.dumps({"hash": "f" * 64, "computed_at": "2026-01-01T00:00:00Z", "fingerprint": {}})
    )
    verify = tmp_path / "verify.py"
    verify.write_text(
        "import json, sys\nargs = sys.argv[1:]\n"
        "with open(args[args.index('--out') + 1], 'w') as out:\n"
        "    json.dump({'ok': True, 'args': args}, out)\n"
    )
    config = control.Config(
        listen=HOST,
        api_port=api_port,
        control_port=free_port(HOST),
        cert=inference[0],
        key=inference[1],
        peer=peer,
        deploy_socket=tmp_path / "deploy.sock",
        gpu_metrics=f"http://127.0.0.1:{gpu.server_address[1]}/gpu",
        current=tmp_path / "current.yml",
        verify=(sys.executable, str(verify)),
        state_dir=tmp_path / "state",
        models=tmp_path / "models",
        fingerprint=tmp_path / "fingerprint.json",
    )
    result = Endpoint(config, lab, inference[0], mallory)
    helper = serve_unix(config.deploy_socket, result.deploy_requests)
    loop = asyncio.new_event_loop()
    task = loop.create_task(control.serve(config))

    def run() -> None:
        with contextlib.suppress(asyncio.CancelledError):
            loop.run_until_complete(task)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    for _ in range(100):
        with (
            contextlib.suppress(OSError),
            socket.create_connection((HOST, config.control_port), 0.1),
        ):
            break
        threading.Event().wait(0.05)
    yield result
    loop.call_soon_threadsafe(task.cancel)
    thread.join(10)
    loop.close()
    helper.close()
    for server in (vllm, gpu):
        server.shutdown()
        server.server_close()


@needs_openssl
def test_control_api(endpoint: Endpoint) -> None:
    status, health = endpoint.json("GET", "/health")
    assert status == 200
    assert health == {
        "ok": True,
        "vllm_status": 200,
        "deployment": {"id": "dep-a", "version": "2", "model_id": "model-a", "profile": "ada-12g"},
        "fingerprint": {"hash": "f" * 64, "computed_at": "2026-01-01T00:00:00Z"},
    }
    status, model = endpoint.json("GET", "/model")
    assert status == 200
    assert model["models"] == {"data": [{"id": "model-a"}]}
    assert model["manifest"]["files"] == [{"path": "w", "sha256": "0" * 64}]  # not the adapter's
    status, metrics = endpoint.call("GET", "/metrics")
    assert (status, metrics) == (200, b"vllm_up 1\ngpu_up 1\n")
    status, report = endpoint.json("POST", "/verify")
    assert status == 200
    args = report["args"]
    assert args[args.index("--deployment") + 1] == "dep-a@2"
    assert args[args.index("--model") + 1] == "model-a"
    assert args[args.index("--port") + 1] == str(endpoint.config.api_port)
    assert args[args.index("--wait") + 1] == "60"


@needs_openssl
def test_activate_goes_through_the_deploy_helper(endpoint: Endpoint) -> None:
    for bad in (b'{"deployment": "../x@1"}', b'{"deployment": "dep-a"}', b"[]", b"not json"):
        assert endpoint.json("POST", "/activate", bad)[0] == 400, bad
    assert endpoint.deploy_requests == []
    status, answer = endpoint.json("POST", "/activate", b'{"deployment": "dep-b@3"}')
    assert (status, answer) == (200, {"ok": True})
    assert endpoint.deploy_requests == [{"deployment": "dep-b@3"}]


@needs_openssl
def test_nothing_else_is_answered(endpoint: Endpoint) -> None:
    for method, path in (
        ("GET", "/"),
        ("POST", "/health"),
        ("GET", "/activate"),
        ("DELETE", "/model"),
    ):
        assert endpoint.json(method, path)[0] == 404, (method, path)
    assert endpoint.call("GET", "", raw=b"garbage\r\n\r\n")[0] == 400
    big = b"x" * (control.MAX_BODY + 1)
    assert endpoint.call("POST", "/activate", big)[0] == 400
    many = b"GET /health HTTP/1.1\r\n" + b"X-A: b\r\n" * (control.MAX_HEADER_LINES + 1) + b"\r\n"
    assert endpoint.call("GET", "", raw=many)[0] == 400


@needs_openssl
def test_api_port_forwards_to_vllm(endpoint: Endpoint) -> None:
    status, body = endpoint.call("GET", "/v1/models", port=endpoint.config.api_port)
    assert status == 200
    assert json.loads(body) == {"data": [{"id": "model-a"}]}


@needs_openssl
def test_only_the_paired_lab_node_gets_in(endpoint: Endpoint) -> None:
    for port in (endpoint.config.control_port, endpoint.config.api_port):
        assert endpoint.call("GET", "/health", port=port)[0] == 200
        # Another certificate with the lab node's name, or none at all, is refused.
        assert endpoint.call("GET", "/health", port=port, identity=endpoint.mallory) == (0, b"")
        assert endpoint.call("GET", "/health", port=port, anonymous=True) == (0, b"")


def test_not_paired(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "control.json"
    path.write_text(json.dumps({"listen": HOST, "peer": str(tmp_path / "peer.crt")}))
    assert control.main(["--config", str(path)]) == 1
    assert "not paired" in capsys.readouterr().err


def test_units_carry_what_doctor_checks() -> None:
    """The hardening doctor compares (pinka_unit_hardening) is in the unit template."""
    rendered = [render_template("inference_control", "pinka-inference-control.service.j2", scope())]
    for text in rendered:
        lines = set(text.splitlines())
        for key, value in pinka.UNIT_HARDENING.items():
            assert f"{key}={value}" in lines, (key, text.splitlines()[2])
