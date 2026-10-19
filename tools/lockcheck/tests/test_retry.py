"""Another attempt for a transport error or a malformed answer, and only for those."""

from __future__ import annotations

import hashlib
import http.client
import io
import ssl
import urllib.error
import urllib.request
from email.message import Message
from typing import Any

import pytest
from retry_testlib import quick

from pinka_lockcheck import bundle, checks, net, retry

URL = "https://example.com/artifact.tar.gz"


def failing(*errors: Exception, result: str = "done") -> tuple[Any, list[int]]:
    """An action that raises ``errors`` one by one, then returns ``result``."""
    calls: list[int] = []
    queue = list(errors)

    def action() -> str:
        calls.append(1)
        if queue:
            raise queue.pop(0)
        return result

    return action, calls


def test_a_transport_error_is_tried_again_with_increasing_delays() -> None:
    log: list[str] = []
    waits: list[float] = []
    action, calls = failing(
        net.FetchError("reset", transient=True), net.FetchError("HTTP 503", transient=True)
    )
    assert quick(log, waits).run(action, "GET x") == "done"
    assert len(calls) == 3
    assert waits == [5.0, 15.0]
    assert log == [
        "GET x: attempt 1 of 3",
        "GET x: attempt 1 of 3 failed: reset; next attempt in 5 s",
        "GET x: attempt 2 of 3",
        "GET x: attempt 2 of 3 failed: HTTP 503; next attempt in 15 s",
        "GET x: attempt 3 of 3",
    ]


def test_a_malformed_answer_is_tried_again() -> None:
    action, calls = failing(retry.MalformedAnswerError("no results"))
    assert quick().run(action, "GET x") == "done"
    assert len(calls) == 2


def test_three_attempts_at_most() -> None:
    log: list[str] = []
    error = net.FetchError("timed out", transient=True)
    action, calls = failing(error, error, error, error)
    with pytest.raises(net.FetchError, match="timed out"):
        quick(log).run(action, "GET x")
    assert len(calls) == 3
    assert log[-1] == "GET x: attempt 3 of 3 failed: timed out; none left"


@pytest.mark.parametrize(
    "error",
    [net.FetchError("HTTP 404"), ValueError("sha256 mismatch"), RuntimeError("anything else")],
)
def test_any_other_error_stops_at_once(error: Exception) -> None:
    waits: list[float] = []
    action, calls = failing(error)
    with pytest.raises(type(error)):
        quick(waits=waits).run(action, "GET x")
    assert len(calls) == 1
    assert waits == []


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(URL, code, "error", Message(), io.BytesIO())


class BrokenResponse(io.BytesIO):
    """A response whose body breaks off."""

    def read(self, size: int | None = -1) -> bytes:
        raise http.client.IncompleteRead(b"part", 10)


@pytest.mark.parametrize(
    ("raised", "transient"),
    [
        (http_error(503), True),
        (http_error(502), True),
        (http_error(429), True),
        (http_error(408), True),
        (http_error(404), False),
        (http_error(403), False),
        (urllib.error.URLError(ConnectionRefusedError(111, "refused")), True),
        (urllib.error.URLError(TimeoutError("timed out")), True),
        (TimeoutError("timed out"), True),
        (
            urllib.error.URLError(ssl.SSLCertVerificationError(1, "certificate verify failed")),
            False,
        ),
        (None, True),  # the body breaks off while it is read
    ],
)
def test_which_download_errors_are_transient(
    monkeypatch: pytest.MonkeyPatch, raised: BaseException | None, transient: bool
) -> None:
    def urlopen(request: urllib.request.Request, timeout: float) -> Any:
        if raised is not None:
            raise raised
        return BrokenResponse()

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    for download in (net.download, net.sha256_of_url):
        with pytest.raises(net.FetchError) as info:
            download(URL)
        assert info.value.transient is transient
        assert retry.transient(info.value) is transient


def collection_lock(sha256: str) -> dict[str, Any]:
    return {
        "binaries": {},
        "ansible_collections": {
            "artifacts": {"ansible.posix": {"url": URL, "sha256": sha256, "size": 1}}
        },
    }


def test_lock_check_downloads_a_collection_again_after_a_transport_error() -> None:
    digest = hashlib.sha256(b"artifact").hexdigest()
    hasher, calls = failing(net.FetchError("HTTP 502", transient=True), result=digest)
    findings = checks.binary_findings(collection_lock(digest), lambda url: hasher(), quick())
    assert findings == []
    assert len(calls) == 2


def test_lock_check_does_not_retry_a_sha256_mismatch() -> None:
    hasher, calls = failing(result="1" * 64)
    findings = checks.binary_findings(collection_lock("0" * 64), lambda url: hasher(), quick())
    assert [f.message for f in findings] == [f"sha256 is {'1' * 64}, pinned {'0' * 64}"]
    assert len(calls) == 1


def test_the_release_package_downloads_a_collection_again_after_a_transport_error() -> None:
    lock = {
        "ansible_collections": {
            "collections": {"ansible.posix": "2.2.2"},
            "artifacts": {"ansible.posix": {"url": URL, "sha256": "0" * 64, "size": 5}},
        }
    }
    fetch, calls = failing(net.FetchError("HTTP 503", transient=True), result="other")
    # The second attempt answers with content that differs from the pin:
    # refused with both values, and not tried a third time.
    actual = hashlib.sha256(b"other").hexdigest()
    with pytest.raises(bundle.PackageError, match=f"sha256 is {actual}, pinned {'0' * 64}"):
        bundle.collect(lock, lambda url: fetch().encode(), quick())
    assert len(calls) == 2
