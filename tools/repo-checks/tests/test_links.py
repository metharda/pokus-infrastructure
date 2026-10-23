"""Tests for the external link check, against a local HTTP server."""

from __future__ import annotations

import http.server
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from pinka_repo_checks import links


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
    )
    found = links.extract_links(Path("a.md"), text)
    assert [(link.line, link.url) for link in found] == [(2, "https://pypi.org/project/ruff/")]


def test_check_url_ok_head_fallback_and_404(server: str) -> None:
    assert links.check_url(f"{server}/ok", 5) is None
    assert links.check_url(f"{server}/no-head", 5) is None
    assert links.check_url(f"{server}/missing", 5) == "HTTP 404"


def test_check_url_unreachable() -> None:
    assert (links.check_url("http://127.0.0.1:9/", 2) or "").startswith("unreachable")


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
