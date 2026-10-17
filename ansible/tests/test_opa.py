"""The OPA policies of config/opa/ with the pinned OPA release (BUILD_PROMPT §7.10).

``make setup`` installs the OPA CLI of ``versions.lock.yml`` (binaries.opa,
the release of the pinned container image) after checking its sha256. The
authorization policy is rendered for a platform whose decision path is
``demo/action/decision`` and for none; the Rego unit tests cover it and the
placeholder; the server test runs OPA exactly as the Compose file does, with
a platform's policy bundle, and checks over HTTP that the two decision
queries work and every write is rejected.
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

from ansible_testlib import INFRA, render_file

OPA = Path(os.environ.get("PINKA_OPA", str(INFRA / "build" / "tools" / "opa")))
CONFIG = INFRA / "config" / "opa"
TESTS = Path(__file__).parent / "opa"
BUNDLE = TESTS / "bundle"
DECISION = "/v1/data/demo/action/decision"


def render_authz(directory: Path, decision_path: str) -> Path:
    path = directory / "authz.rego"
    path.write_text(render_file(CONFIG / "authz.rego.j2", {"opa_decision_path": decision_path}))
    return path


@pytest.fixture(scope="module")
def authz(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return render_authz(tmp_path_factory.mktemp("authz"), "demo/action/decision")


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


@pytest.mark.parametrize("decision_path", ["demo/action/decision", ""])
def test_policies_are_formatted_and_strict(tmp_path: Path, decision_path: str) -> None:
    rendered = render_authz(tmp_path, decision_path)
    paths = [str(rendered), str(CONFIG / "placeholder"), str(TESTS)]
    check = opa("check", "--strict", *paths)
    assert check.returncode == 0, check.stdout + check.stderr
    fmt = opa("fmt", "--list", *paths)
    assert fmt.stdout == "", f"not formatted with opa fmt: {fmt.stdout}"


def test_rego_unit_tests(authz: Path) -> None:
    result = opa("test", str(authz), str(CONFIG / "placeholder"), str(BUNDLE), str(TESTS))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS: 7/7" in result.stdout


def test_without_a_platform_only_the_placeholder_is_allowed(tmp_path: Path) -> None:
    rendered = render_authz(tmp_path, "")
    query = 'data.system.authz.allow with input as {"method": "POST", "path": %s}'
    platform = opa(
        "eval", "--format=raw", "-d", str(rendered), query % json.dumps(DECISION.split("/")[1:])
    )
    assert platform.stdout.strip() == "false", platform.stderr
    placeholder = ["v1", "data", "pinka", "placeholder", "active"]
    allowed = opa("eval", "--format=raw", "-d", str(rendered), query % json.dumps(placeholder))
    assert allowed.stdout.strip() == "true", allowed.stderr


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


@pytest.fixture(scope="module")
def server(tmp_path_factory: pytest.TempPathFactory, authz: Path) -> Iterator[str]:
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
                str(authz),
                str(CONFIG / "placeholder"),
                str(BUNDLE),
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
    status, body = request(server, "POST", DECISION, {"input": {}})
    assert status == 200
    assert body["result"]["allow"] is False
    status, body = request(server, "GET", "/v1/data/pinka/placeholder/active")
    assert (status, body["result"]) == (200, True)


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("PUT", "/v1/policies/takeover", b"package system.authz\n\nallow := true\n"),
        ("DELETE", "/v1/policies/config/opa/authz.rego", None),
        ("PUT", DECISION, {"allow": True}),
        ("PATCH", "/v1/data/pinka", [{"op": "add", "path": "/x", "value": 1}]),
        ("DELETE", "/v1/data/pinka/placeholder", None),
        ("GET", "/v1/policies", None),
        ("GET", "/v1/data/system/authz", None),
        ("GET", "/v1/data/pinka", None),
        ("POST", "/v1/data/demo/action", {"input": {}}),
        ("GET", "/v1/data/pinka/placeholder", None),
        ("POST", "/v1/query", {"query": "data"}),
        ("GET", "/v1/config", None),
        ("GET", "/metrics", None),
    ],
)
def test_writes_and_other_apis_are_rejected(server: str, method: str, path: str, body: Any) -> None:
    assert request(server, method, path, body)[0] == HTTP_UNAUTHORIZED
    # Still denying afterwards: nothing was replaced.
    status, result = request(server, "POST", DECISION, {"input": {}})
    assert (status, result["result"]["allow"]) == (200, False)
