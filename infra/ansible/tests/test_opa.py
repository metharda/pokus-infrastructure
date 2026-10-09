"""The OPA policies of config/opa/ with the pinned OPA release (resolution C6).

``make setup`` installs the OPA CLI of ``versions.lock.yml`` (binaries.opa,
the release of the pinned container image) after checking its sha256. The
Rego unit tests cover the authorization policy and the placeholder; the server
test runs OPA exactly as the Compose file does and checks over HTTP that
decision queries work and every write is rejected (contracts.md#policy-engine-opa).
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from ansible_testlib import INFRA

OPA = Path(os.environ.get("PINKA_OPA", str(INFRA / "build" / "tools" / "opa")))
CONFIG = INFRA / "config" / "opa"
TESTS = Path(__file__).parent / "opa"
HTTP_UNAUTHORIZED = 401
# 127.0.0.1 only: never through the environment's proxy.
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def opa(*args: str) -> subprocess.CompletedProcess[str]:
    assert OPA.is_file(), f"{OPA} is missing: run `make setup` (installs the pinned OPA)"
    return subprocess.run([str(OPA), *args], capture_output=True, text=True, check=False)


def test_pinned_cli_matches_the_container_image() -> None:
    lock = yaml.safe_load((INFRA / "versions.lock.yml").read_text(encoding="utf-8"))
    image_version = lock["containers"]["images"]["opa"]["tag"].split("-")[0]
    assert lock["binaries"]["opa"]["version"] == image_version
    assert f"Version: {image_version}" in opa("version").stdout


def test_policies_are_formatted_and_strict() -> None:
    paths = [str(CONFIG / "authz.rego"), str(CONFIG / "placeholder"), str(TESTS)]
    check = opa("check", "--strict", *paths)
    assert check.returncode == 0, check.stdout + check.stderr
    fmt = opa("fmt", "--list", *paths)
    assert fmt.stdout == "", f"not formatted with opa fmt: {fmt.stdout}"


def test_rego_unit_tests() -> None:
    result = opa("test", str(CONFIG / "authz.rego"), str(CONFIG / "placeholder"), str(TESTS))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS: 7/7" in result.stdout


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


@pytest.fixture(scope="module")
def server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    port = free_port()
    log = tmp_path_factory.mktemp("opa") / "opa.log"
    with log.open("w") as out:
        # The arguments of compose/core/compose.yml, with host paths.
        process = subprocess.Popen(
            [
                str(OPA),
                "run",
                "--server",
                f"--addr=127.0.0.1:{port}",
                "--authorization=basic",
                f"--config-file={CONFIG / 'opa.yaml'}",
                str(CONFIG / "authz.rego"),
                str(CONFIG / "placeholder"),
            ],
            stdout=out,
            stderr=subprocess.STDOUT,
        )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                if request(base, "GET", "/health")[0] == 200:
                    break
            except urllib.error.URLError:
                pass
            assert process.poll() is None, log.read_text()
            assert time.monotonic() < deadline, log.read_text()
            time.sleep(0.2)
        yield base
    finally:
        process.terminate()
        process.wait(timeout=10)


def request(base: str, method: str, path: str, body: Any = None) -> tuple[int, Any]:
    data = (
        None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    )
    req = urllib.request.Request(base + path, data=data, method=method)  # noqa: S310 - loopback
    try:
        with OPENER.open(req, timeout=10) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"null")


def test_decision_queries_and_health_are_served(server: str) -> None:
    assert request(server, "GET", "/health")[0] == 200
    status, body = request(server, "POST", "/v1/data/potka/action/decision", {"input": {}})
    assert status == 200
    assert body["result"]["allow"] is False
    status, body = request(server, "GET", "/v1/data/pinka/placeholder/active")
    assert (status, body["result"]) == (200, True)


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("PUT", "/v1/policies/takeover", b"package system.authz\n\nallow := true\n"),
        ("DELETE", "/v1/policies/config/opa/authz.rego", None),
        ("PUT", "/v1/data/potka/action/decision", {"allow": True}),
        ("PATCH", "/v1/data/pinka", [{"op": "add", "path": "/x", "value": 1}]),
        ("DELETE", "/v1/data/pinka/placeholder", None),
        ("GET", "/v1/policies", None),
        ("GET", "/v1/data/system/authz", None),
        ("GET", "/v1/data/pinka", None),
        ("POST", "/v1/data/potka/action", {"input": {}}),
        ("GET", "/v1/data/pinka/placeholder", None),
        ("POST", "/v1/query", {"query": "data"}),
        ("GET", "/v1/config", None),
        ("GET", "/metrics", None),
    ],
)
def test_writes_and_other_apis_are_rejected(server: str, method: str, path: str, body: Any) -> None:
    assert request(server, method, path, body)[0] == HTTP_UNAUTHORIZED
    # Still denying afterwards: nothing was replaced.
    status, result = request(server, "POST", "/v1/data/potka/action/decision", {"input": {}})
    assert (status, result["result"]["allow"]) == (200, False)
