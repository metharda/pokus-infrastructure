"""Tests for the external link check, against a local HTTP server."""

from __future__ import annotations

import http.server
import threading
from collections.abc import Iterator
from pathlib import Path

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
