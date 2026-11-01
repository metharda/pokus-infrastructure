#!/opt/pinka/python/bin/python3 -I
# Managed by pinka (ansible/roles/inference_control/files/pinka_inference_control.py). Local changes will be overwritten.
"""The inference control endpoint of an inference node.

Installed as ``/usr/local/libexec/pinka/inference-control`` and run as
``pinka-inference-control.service`` (user pinka-inference) on ``node_role:
inference``. It listens on the node's tailnet address only, with mutual TLS
1.3: the client must present exactly the certificate of the paired lab node
(``/etc/pinka/inference/peer.crt``, exchanged during pairing). Two listeners:

* the API port (18704) forwards every accepted connection byte for byte to
  vLLM on 127.0.0.1, so the lab node's clients use the OpenAI-compatible
  API unchanged;
* the control port (18710) answers exactly these requests, as JSON:
  ``GET /health`` (with this node's fingerprint hash), ``GET /model`` (the
  served model, its deployment and the ``MANIFEST.json`` whose hashes the
  models role verified on this node),
  ``GET /metrics`` (vLLM's and the GPU exporter's Prometheus text),
  ``POST /activate`` with ``{"deployment": "<id>@<version>"}`` (a ``serve``
  request to the GPU arbiter's socket) and ``POST /verify`` (the serving verification).
  Everything else is 404. Standard library only.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import os
import re
import socket
import ssl
import sys
import urllib.error
import urllib.request
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEPLOYMENT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}@[0-9]{1,6}$")
MAX_BODY = 4096
MAX_HEADER_LINES = 64
CHUNK = 65536
READ_TIMEOUT = 30.0
HTTP_OK = 200


@dataclass(frozen=True)
class Config:
    """Settings from ``/etc/pinka/inference/control.json``.

    Attributes:
        listen: Tailnet address to listen on.
        api_port: Port of the forwarded vLLM API (and of vLLM on 127.0.0.1).
        control_port: Port of the control API.
        cert: This node's certificate.
        key: This node's private key.
        peer: The paired lab node's certificate (the only accepted client).
        gpu_socket: The GPU arbiter's socket.
        activate_timeout: Seconds ``POST /activate`` waits for the arbiter.
        gpu_metrics: URL of the GPU exporter's metrics.
        current: The current deployment spec (``current.yml``).
        verify: Command of the serving verification, before its arguments.
        verify_wait: Seconds the verification waits for vLLM's health.
        state_dir: Writable directory for verification reports.
        models: Root of the verified model weights.
        fingerprint: The cached environment fingerprint (C9c).
    """

    listen: str
    api_port: int = 18704
    control_port: int = 18710
    cert: Path = Path("/etc/pinka/inference/self.crt")
    key: Path = Path("/etc/pinka/inference/self.key")
    peer: Path = Path("/etc/pinka/inference/peer.crt")
    gpu_socket: Path = Path("/run/pinka/gpu.sock")
    activate_timeout: int = 1500
    gpu_metrics: str = "http://127.0.0.1:18726/metrics"
    current: Path = Path("/etc/pinka/vllm/current.yml")
    verify: tuple[str, ...] = ("/usr/local/libexec/pinka/vllm-verify",)
    verify_wait: int = 60
    state_dir: Path = Path("/var/lib/pinka/inference-control")
    models: Path = Path("/srv/pinka/models")
    fingerprint: Path = Path("/var/lib/pinka/fingerprint/current.json")

    @classmethod
    def load(cls, path: Path) -> Config:
        """Read the configuration file.

        Args:
            path: JSON file.

        Returns:
            The configuration.
        """
        data = json.loads(path.read_text(encoding="utf-8"))
        paths = {
            "cert",
            "key",
            "peer",
            "gpu_socket",
            "current",
            "state_dir",
            "models",
            "fingerprint",
        }
        values: dict[str, Any] = {k: Path(v) if k in paths else v for k, v in data.items()}
        if "verify" in values:
            values["verify"] = tuple(values["verify"])
        return cls(**values)


def peer_fingerprint(peer: Path) -> str:
    """SHA-256 of the paired certificate's DER encoding.

    Args:
        peer: PEM certificate file.

    Returns:
        Lowercase hex digest.
    """
    der = ssl.PEM_cert_to_DER_cert(peer.read_text(encoding="ascii"))
    return hashlib.sha256(der).hexdigest()


def server_context(config: Config) -> ssl.SSLContext:
    """TLS 1.3 server context that requires the paired lab node's certificate.

    Args:
        config: Settings.

    Returns:
        The context.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.verify_mode = ssl.CERT_REQUIRED
    context.load_cert_chain(config.cert, config.key)
    context.load_verify_locations(cafile=str(config.peer))
    return context


def is_peer(writer: asyncio.StreamWriter, fingerprint: str) -> bool:
    """Whether the TLS client presented exactly the paired certificate.

    Args:
        writer: The connection.
        fingerprint: Expected SHA-256 of the client certificate.

    Returns:
        True for the paired lab node.
    """
    ssl_object = writer.get_extra_info("ssl_object")
    der = ssl_object.getpeercert(binary_form=True) if ssl_object else None
    return der is not None and hashlib.sha256(der).hexdigest() == fingerprint


async def _pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(CHUNK):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.IncompleteReadError, ssl.SSLError):
        pass
    finally:
        with contextlib.suppress(Exception):
            writer.close()


async def forward(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, config: Config, fingerprint: str
) -> None:
    """Forward one API connection to vLLM on 127.0.0.1.

    Args:
        reader: Client stream.
        writer: Client stream.
        config: Settings.
        fingerprint: Paired certificate.
    """
    if not is_peer(writer, fingerprint):
        writer.close()
        return
    try:
        up_reader, up_writer = await asyncio.open_connection("127.0.0.1", config.api_port)
    except OSError:
        writer.close()
        return
    await asyncio.gather(_pump(reader, up_writer), _pump(up_reader, writer))


@dataclass(frozen=True)
class Request:
    """One control request.

    Attributes:
        method: HTTP method.
        path: Request path without query.
        body: Request body.
    """

    method: str
    path: str
    body: bytes


async def read_request(reader: asyncio.StreamReader) -> Request | None:
    """Read one HTTP/1.1 request with a small body.

    Args:
        reader: Client stream.

    Returns:
        The request, or None if it is malformed or too large.
    """
    line = (await asyncio.wait_for(reader.readline(), READ_TIMEOUT)).decode("latin-1").strip()
    parts = line.split(" ")
    if len(parts) != 3 or not parts[2].startswith("HTTP/1."):  # noqa: PLR2004 - method, target, version
        return None
    length = 0
    for _ in range(MAX_HEADER_LINES):
        header = (await asyncio.wait_for(reader.readline(), READ_TIMEOUT)).decode("latin-1")
        if header in ("\r\n", "\n", ""):
            break
        name, _, value = header.partition(":")
        if name.strip().lower() == "content-length":
            if not value.strip().isdigit():
                return None
            length = int(value.strip())
    else:
        return None
    if length > MAX_BODY:
        return None
    body = await asyncio.wait_for(reader.readexactly(length), READ_TIMEOUT) if length else b""
    return Request(parts[0], parts[1].split("?", 1)[0], body)


def _fetch(url: str, timeout: float = 10.0) -> tuple[int, bytes]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as response:
            return int(response.status), response.read()
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read()
    except (OSError, ValueError):
        return 0, b""


def current_deployment(config: Config) -> dict[str, Any]:
    """The active deployment spec (id, version, model_id), or an empty dict.

    Args:
        config: Settings.

    Returns:
        The fields of ``current.yml`` that the control API reports.
    """
    try:
        text = config.current.read_text(encoding="utf-8")
    except OSError:
        return {}
    fields: dict[str, Any] = {}
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep and key in ("id", "version", "model_id", "profile") and not line.startswith(" "):
            fields[key] = value.strip().strip("'\"")
    return fields


def served_manifest(config: Config, model_id: str) -> dict[str, Any] | None:
    """The ``MANIFEST.json`` of a verified model (adapters excluded).

    Args:
        config: Settings.
        model_id: Model id from models.lock.yml.

    Returns:
        The manifest, or None if no verified model has this id.
    """
    for path in sorted(config.models.glob("*/*/*/MANIFEST.json")):
        if path.relative_to(config.models).parts[0] == "adapters":
            continue
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(manifest, dict) and manifest.get("id") == model_id:
            return manifest
    return None


def fingerprint_summary(config: Config) -> dict[str, Any] | None:
    """Hash and time of this node's cached fingerprint.

    Args:
        config: Settings.

    Returns:
        ``{"hash", "computed_at"}``, or None without a cache.
    """
    try:
        cached = json.loads(config.fingerprint.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(cached, dict) or not cached.get("hash"):
        return None
    return {"hash": cached["hash"], "computed_at": cached.get("computed_at")}


def ask_arbiter(config: Config, deployment: str) -> dict[str, Any]:
    """Serve a deployment through the GPU arbiter.

    The arbiter answers when the deployment answers on /health or failed.

    Args:
        config: Settings.
        deployment: ``<id>@<version>``.

    Returns:
        The arbiter's JSON answer.
    """
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(config.activate_timeout)
        client.connect(str(config.gpu_socket))
        request = {"request": "serve", "deployment": deployment}
        client.sendall(json.dumps(request).encode() + b"\n")
        data = b""
        while not data.endswith(b"\n"):
            chunk = client.recv(4096)
            if not chunk:
                break
            data += chunk
    answer: dict[str, Any] = json.loads(data or b"{}")
    return answer


async def run_verify(config: Config) -> tuple[int, dict[str, Any]]:
    """Run the serving verification for the current deployment.

    Args:
        config: Settings.

    Returns:
        HTTP status and the report.
    """
    spec = current_deployment(config)
    if not spec.get("id"):
        return 409, {"ok": False, "error": "no deployment is active"}
    deployment = f"{spec['id']}@{spec['version']}"
    report = config.state_dir / f"verify-{deployment}.json"
    process = await asyncio.create_subprocess_exec(
        *config.verify,
        "--port",
        str(config.api_port),
        "--model",
        str(spec["model_id"]),
        "--deployment",
        deployment,
        "--out",
        str(report),
        "--wait",
        str(config.verify_wait),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    code = await process.wait()
    try:
        result = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        result = {"ok": False, "error": "the verification wrote no report"}
    return (200 if code == 0 else 502), result


Handler = Callable[[Config, Request], Awaitable[tuple[int, str, bytes]]]


def _json(status: int, value: Any) -> tuple[int, str, bytes]:  # noqa: ANN401 - JSON value
    return status, "application/json", json.dumps(value, sort_keys=True).encode() + b"\n"


def _vllm(config: Config, path: str) -> str:
    return f"http://127.0.0.1:{config.api_port}{path}"


async def _in_thread(function: Callable[..., Any], *args: Any) -> Any:  # noqa: ANN401 - any result
    return await asyncio.get_running_loop().run_in_executor(None, function, *args)


async def health(config: Config, _request: Request) -> tuple[int, str, bytes]:
    """``GET /health``: vLLM's health, the deployment and this node's fingerprint.

    Args:
        config: Settings.
        _request: The request.

    Returns:
        Status, content type and body.
    """
    status, _ = await _in_thread(_fetch, _vllm(config, "/health"))
    return _json(
        200,
        {
            "ok": status == HTTP_OK,
            "vllm_status": status,
            "deployment": current_deployment(config) or None,
            "fingerprint": fingerprint_summary(config),
        },
    )


async def model(config: Config, _request: Request) -> tuple[int, str, bytes]:
    """``GET /model``: the served model, its deployment and its verified manifest.

    Args:
        config: Settings.
        _request: The request.

    Returns:
        Status, content type and body (503 while vLLM serves nothing).
    """
    status, body = await _in_thread(_fetch, _vllm(config, "/v1/models"))
    try:
        models = json.loads(body) if status == HTTP_OK else None
    except ValueError:
        models = None
    spec = current_deployment(config)
    manifest = served_manifest(config, str(spec["model_id"])) if spec.get("model_id") else None
    return _json(
        HTTP_OK if models else 503,
        {"deployment": spec or None, "models": models, "manifest": manifest},
    )


async def metrics(config: Config, _request: Request) -> tuple[int, str, bytes]:
    """``GET /metrics``: vLLM's and the GPU exporter's Prometheus text.

    Args:
        config: Settings.
        _request: The request.

    Returns:
        Status, content type and body.
    """
    parts = []
    for url in (_vllm(config, "/metrics"), config.gpu_metrics):
        status, body = await _in_thread(_fetch, url) if url else (0, b"")
        if status == HTTP_OK:
            parts.append(body.rstrip(b"\n") + b"\n")
    return HTTP_OK, "text/plain; version=0.0.4", b"".join(parts)


async def activate(config: Config, request: Request) -> tuple[int, str, bytes]:
    """``POST /activate``: ``serve <id>@<version>`` through the GPU arbiter.

    Args:
        config: Settings.
        request: The request, body ``{"deployment": "<id>@<version>"}``.

    Returns:
        Status, content type and the arbiter's answer.
    """
    try:
        deployment = str(json.loads(request.body or b"{}").get("deployment", ""))
    except (ValueError, AttributeError):
        deployment = ""
    if not DEPLOYMENT_RE.match(deployment):
        return _json(400, {"ok": False, "error": "deployment must be <id>@<version>"})
    try:
        answer = await _in_thread(ask_arbiter, config, deployment)
    except (OSError, ValueError) as exc:
        return _json(502, {"ok": False, "error": f"GPU arbiter: {exc}"})
    return _json(HTTP_OK if answer.get("ok") else 409, answer)


async def verify(config: Config, _request: Request) -> tuple[int, str, bytes]:
    """``POST /verify``: the serving verification of the current deployment.

    Args:
        config: Settings.
        _request: The request.

    Returns:
        Status, content type and the report.
    """
    status, report = await run_verify(config)
    return _json(status, report)


ROUTES: dict[tuple[str, str], Handler] = {
    ("GET", "/health"): health,
    ("GET", "/model"): model,
    ("GET", "/metrics"): metrics,
    ("POST", "/activate"): activate,
    ("POST", "/verify"): verify,
}


async def route(config: Config, request: Request) -> tuple[int, str, bytes]:
    """Answer one control request; everything outside ROUTES is 404.

    Args:
        config: Settings.
        request: The request.

    Returns:
        Status, content type and body.
    """
    handler = ROUTES.get((request.method, request.path))
    if handler is None:
        return _json(404, {"ok": False, "error": "not found"})
    return await handler(config, request)


async def control(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    config: Config,
    fingerprint: str,
    handler: Handler = route,
) -> None:
    """Serve one control connection (one request).

    Args:
        reader: Client stream.
        writer: Client stream.
        config: Settings.
        fingerprint: Paired certificate.
        handler: Request router.
    """
    try:
        if not is_peer(writer, fingerprint):
            return
        try:
            request = await read_request(reader)
        except (TimeoutError, asyncio.IncompleteReadError, ConnectionError, ssl.SSLError):
            return
        if request is None:
            status, ctype, body = _json(400, {"ok": False, "error": "bad request"})
        else:
            status, ctype, body = await handler(config, request)
        reason = {200: "OK", 400: "Bad Request", 404: "Not Found"}.get(status, "Status")
        head = (
            f"HTTP/1.1 {status} {reason}\r\nContent-Type: {ctype}\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
        )
        writer.write(head.encode("latin-1") + body)
        with contextlib.suppress(ConnectionError, ssl.SSLError):
            await writer.drain()
    finally:
        with contextlib.suppress(Exception):
            writer.close()


async def serve(config: Config) -> None:
    """Run both listeners until the process is stopped.

    Args:
        config: Settings.
    """
    context = server_context(config)
    fingerprint = peer_fingerprint(config.peer)
    config.state_dir.mkdir(parents=True, exist_ok=True)

    async def on_api(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await forward(reader, writer, config, fingerprint)

    async def on_control(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await control(reader, writer, config, fingerprint)

    api = await asyncio.start_server(on_api, config.listen, config.api_port, ssl=context)
    ctl = await asyncio.start_server(on_control, config.listen, config.control_port, ssl=context)
    async with api, ctl:
        await asyncio.gather(api.serve_forever(), ctl.serve_forever())


def main(argv: list[str]) -> int:
    """Start the endpoint.

    Args:
        argv: Arguments without the program name.

    Returns:
        Exit status (only on errors).
    """
    parser = argparse.ArgumentParser(prog="inference-control", description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=Path("/etc/pinka/inference/control.json"))
    args = parser.parse_args(argv)
    try:
        config = Config.load(args.config)
        if not config.peer.is_file():
            sys.stderr.write("inference-control: not paired yet (no peer.crt)\n")
            return 1
        asyncio.run(serve(config))
    except (OSError, ValueError, ssl.SSLError) as exc:
        sys.stderr.write(f"inference-control: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    os.umask(0o077)
    sys.exit(main(sys.argv[1:]))
