"""Tests for the external link check, against a local HTTP server."""

from __future__ import annotations

import http.client
import http.server
import socket
import ssl
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from email.message import Message
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from pinka_repo_checks import links
from pinka_repo_checks.privacy import list_files

ROOT = Path(__file__).resolve().parents[3]


class Handler(http.server.BaseHTTPRequestHandler):
    def do_HEAD(self) -> None:
        if self.path == "/no-head":
            self.send_response(405)
        elif self.path == "/ok":
            self.send_response(200)
        else:
            self.send_response(404)
        self.end_headers()

    def do_GET(self) -> None:
        self.send_response(200 if self.path in {"/ok", "/no-head"} else 404)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture
def server() -> Iterator[str]:
    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_extract_skips_placeholders_and_example_domains() -> None:
    text = (
        "See https://docs.example.com/x and http://localhost:1/x and "
        "https://<host>/x and https://upstream.invalid/a. and (https://real.test/b)\n"
        "Docs at https://pypi.org/project/ruff/.\n"
        "`https://download.docker.com/linux/{distro}` and "
        "`https://nvidia.github.io/libnvidia-container/stable/rpm/$basearch` and "
        "https://mirror.test.org/${release}/x\n"
        "Kept: https://mirror.test.org/v1/users?$select=id and https://mirror.test.org/x.json#/$defs/y\n"
    )
    found = links.extract_links(Path("a.md"), text)
    assert [(link.line, link.url) for link in found] == [
        (2, "https://pypi.org/project/ruff/"),
        (4, "https://mirror.test.org/v1/users?$select=id"),
        (4, "https://mirror.test.org/x.json#/$defs/y"),
    ]


def test_check_url_ok_head_fallback_and_404(server: str) -> None:
    assert links.check_url(f"{server}/ok", 5) is None
    assert links.check_url(f"{server}/no-head", 5) is None
    assert links.check_url(f"{server}/missing", 5) == "HTTP 404"


def test_a_refused_connection_is_tried_again_then_reported_unreachable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    waits: list[float] = []
    result = links.check_url("http://127.0.0.1:9/", 2, sleep=waits.append)
    assert (result or "").startswith("unreachable")
    assert waits == list(links.RETRY_DELAYS)
    err = capsys.readouterr().err
    assert "HEAD http://127.0.0.1:9/" in err
    assert "attempt 2 of 3 in 2 s" in err
    assert "attempt 3 of 3 in 5 s" in err


def test_a_timeout_is_tried_again(capsys: pytest.CaptureFixture[str]) -> None:
    # A listening socket that never answers: the request times out.
    with socket.create_server(("127.0.0.1", 0)) as silent:
        port = silent.getsockname()[1]
        waits: list[float] = []
        result = links.check_url(f"http://127.0.0.1:{port}/", 0.2, sleep=waits.append)
    assert (result or "").startswith("unreachable")
    assert "timed out" in (result or "")
    assert waits == list(links.RETRY_DELAYS)
    err = capsys.readouterr().err
    assert "attempt 2 of 3 in 2 s" in err
    assert "attempt 3 of 3 in 5 s" in err


class Opener:
    """Stands in for ``urllib.request.urlopen``: each call takes the next outcome."""

    def __init__(self, outcomes: list[BaseException | int]) -> None:
        self.outcomes = outcomes
        self.methods: list[str] = []

    def __call__(self, req: urllib.request.Request, timeout: float) -> Any:
        self.methods.append(req.get_method())
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        response = MagicMock()
        response.__enter__.return_value.status = outcome
        return response


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://pypi.org/x", code, "answer", Message(), None)


@pytest.mark.parametrize(
    "transport",
    [
        urllib.error.URLError(ConnectionRefusedError(111, "Connection refused")),
        urllib.error.URLError(socket.gaierror(-2, "Name or service not known")),
        urllib.error.URLError(TimeoutError("timed out")),
        TimeoutError("timed out"),
    ],
    ids=["refused", "dns", "connect-timeout", "read-timeout"],
)
def test_a_transport_error_is_tried_again_until_it_works(
    monkeypatch: pytest.MonkeyPatch, transport: BaseException
) -> None:
    opener = Opener([transport, transport, 200])
    monkeypatch.setattr(urllib.request, "urlopen", opener)
    waits: list[float] = []
    assert links.check_url("https://pypi.org/x", 5, sleep=waits.append) is None
    assert opener.methods == ["HEAD", "HEAD", "HEAD"]
    assert waits == list(links.RETRY_DELAYS)


def test_one_retry_that_works_waits_once(monkeypatch: pytest.MonkeyPatch) -> None:
    refused = urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
    opener = Opener([refused, 200])
    monkeypatch.setattr(urllib.request, "urlopen", opener)
    waits: list[float] = []
    assert links.check_url("https://pypi.org/x", 5, sleep=waits.append) is None
    assert opener.methods == ["HEAD", "HEAD"]
    assert waits == [2.0]


def test_an_http_answer_after_a_transport_error_ends_the_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refused = urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
    opener = Opener([refused, http_error(404)])
    monkeypatch.setattr(urllib.request, "urlopen", opener)
    waits: list[float] = []
    assert links.check_url("https://pypi.org/x", 5, sleep=waits.append) == "HTTP 404"
    assert opener.methods == ["HEAD", "HEAD"]
    assert waits == [2.0]


@pytest.mark.parametrize("code", [405, 501])
def test_the_get_after_a_head_fallback_is_tried_again_on_a_transport_error(
    monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    timeout = urllib.error.URLError(TimeoutError("timed out"))
    opener = Opener([http_error(code), timeout, 200])
    monkeypatch.setattr(urllib.request, "urlopen", opener)
    waits: list[float] = []
    assert links.check_url("https://pypi.org/x", 5, sleep=waits.append) is None
    assert opener.methods == ["HEAD", "GET", "GET"]
    assert waits == [2.0]


@pytest.mark.parametrize(
    ("statuses", "expected", "methods"),
    [([404, 200], None, ["HEAD", "GET"]), ([404, 404], "HTTP 404", ["HEAD", "GET"])],
    ids=["get-works", "get-fails"],
)
def test_a_status_of_400_or_more_that_does_not_raise_goes_on_to_get(
    monkeypatch: pytest.MonkeyPatch,
    statuses: list[BaseException | int],
    expected: str | None,
    methods: list[str],
) -> None:
    opener = Opener(statuses)
    monkeypatch.setattr(urllib.request, "urlopen", opener)
    waits: list[float] = []
    assert links.check_url("https://pypi.org/x", 5, sleep=waits.append) == expected
    assert opener.methods == methods
    assert waits == []


@pytest.mark.parametrize("code", [404, 410, 500, 503])
def test_an_http_answer_is_never_tried_again(monkeypatch: pytest.MonkeyPatch, code: int) -> None:
    opener = Opener([http_error(code)])
    monkeypatch.setattr(urllib.request, "urlopen", opener)
    waits: list[float] = []
    assert links.check_url("https://pypi.org/x", 5, sleep=waits.append) == f"HTTP {code}"
    assert opener.methods == ["HEAD"]
    assert waits == []


def test_a_403_falls_back_to_get_once_and_is_not_tried_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opener = Opener([http_error(403), http_error(403)])
    monkeypatch.setattr(urllib.request, "urlopen", opener)
    waits: list[float] = []
    assert links.check_url("https://pypi.org/x", 5, sleep=waits.append) == "HTTP 403"
    assert opener.methods == ["HEAD", "GET"]
    assert waits == []


@pytest.mark.parametrize(
    "other",
    [
        urllib.error.URLError(ssl.SSLCertVerificationError(1, "certificate verify failed")),
        urllib.error.URLError(ConnectionResetError(104, "Connection reset by peer")),
        ConnectionResetError(104, "Connection reset by peer"),
        http.client.RemoteDisconnected("Remote end closed connection without response"),
        urllib.error.URLError(OSError(101, "Network is unreachable")),
        urllib.error.URLError("unknown url type: ftp"),
    ],
    ids=["tls", "reset", "bare-reset", "remote-disconnected", "network-unreachable", "other"],
)
def test_any_other_error_is_reported_at_once(
    monkeypatch: pytest.MonkeyPatch, other: BaseException
) -> None:
    opener = Opener([other])
    monkeypatch.setattr(urllib.request, "urlopen", opener)
    waits: list[float] = []
    assert (links.check_url("https://pypi.org/x", 5, sleep=waits.append) or "").startswith(
        "unreachable"
    )
    assert opener.methods == ["HEAD"]
    assert waits == []


def test_run_reports_broken_links(
    tmp_path: Path, server: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(links, "is_skipped", lambda url, next_char="": False)
    (tmp_path / "a.md").write_text(f"{server}/ok\n{server}/missing\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text(f"{server}/missing\n", encoding="utf-8")
    assert links.run([tmp_path], timeout=5) == 1
    out = capsys.readouterr().out
    assert "a.md:2: broken link" in out
    assert "b.txt" not in out


def test_run_fetches_the_probe_in_place_of_a_url_that_is_not_a_page(
    tmp_path: Path, server: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(links, "is_skipped", lambda url, next_char="": False)
    probes = {f"{server}/missing": links.Probe(f"{server}/ok", "a root")}
    monkeypatch.setattr(links, "CHECK_INSTEAD", probes)
    calls: list[str] = []
    real_check = links.check_url

    def recording(url: str, timeout: float) -> str | None:
        calls.append(url)
        return real_check(url, timeout)

    monkeypatch.setattr(links, "check_url", recording)
    text = f"{server}/ok\n{server}/missing\n{server}/missing\n"
    (tmp_path / "a.md").write_text(text, encoding="utf-8")
    assert links.run([tmp_path], timeout=5) == 0
    captured = capsys.readouterr()
    assert calls == [f"{server}/ok"]
    assert captured.out.splitlines() == [
        f"{tmp_path / 'a.md'}:2: link {server}/missing checked as {server}/ok (a root)",
        f"{tmp_path / 'a.md'}:3: link {server}/missing checked as {server}/ok (a root)",
    ]
    assert captured.err == "link check: 1 unique links, 0 broken, 1 checked through another URL\n"


def test_run_reports_a_url_whose_probe_is_broken(
    tmp_path: Path, server: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(links, "is_skipped", lambda url, next_char="": False)
    probes = {f"{server}/ok": links.Probe(f"{server}/missing", "a root")}
    monkeypatch.setattr(links, "CHECK_INSTEAD", probes)
    (tmp_path / "a.md").write_text(f"{server}/ok\n", encoding="utf-8")
    assert links.run([tmp_path], timeout=5) == 1
    captured = capsys.readouterr()
    assert captured.out.splitlines() == [
        f"{tmp_path / 'a.md'}:1: link {server}/ok checked as {server}/missing (a root)",
        f"{tmp_path / 'a.md'}:1: broken link {server}/ok (HTTP 404, checked as {server}/missing)",
    ]
    assert captured.err == "link check: 1 unique links, 1 broken, 1 checked through another URL\n"


def test_every_url_that_is_not_a_page_is_still_in_the_docs() -> None:
    found = {
        link.url
        for f in list_files([ROOT])
        if f.suffix == ".md"
        for link in links.extract_links(f, f.read_text(encoding="utf-8", errors="replace"))
    }
    assert sorted(set(links.CHECK_INSTEAD) - found) == []
