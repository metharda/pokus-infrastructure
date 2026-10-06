"""Tests for the lock checker (no network: every network function is replaced)."""

from __future__ import annotations

import copy
import hashlib
import io
import subprocess
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path
from typing import Any

import pytest

from pokus_lockcheck import checks, cli, net, openpgp

INFRA = Path(__file__).resolve().parents[3]
KEYS = Path(__file__).resolve().parent / "test-keys"
ED25519 = "0C9588366CE49F1110F0765A2C8EF08BE65CD95B"
RSA2048 = "8B9D7CD7839090E0F289F83B6546D7A9D5156A80"
DIGEST = "sha256:" + "a" * 64


@pytest.fixture
def lock() -> dict[str, Any]:
    return checks.load(INFRA / "versions.lock.yml")


# --- OpenPGP ---------------------------------------------------------------------------


def test_fingerprints_of_armored_and_binary_keys() -> None:
    assert openpgp.primary_fingerprints((KEYS / "ed25519.asc").read_bytes()) == [ED25519]
    assert openpgp.primary_fingerprints((KEYS / "ed25519.gpg").read_bytes()) == [ED25519]
    assert openpgp.primary_fingerprints((KEYS / "rsa2048.asc").read_bytes()) == [RSA2048]


def test_several_keys_in_one_file() -> None:
    data = (KEYS / "ed25519.gpg").read_bytes() + openpgp.dearmor((KEYS / "rsa2048.asc").read_text())
    assert openpgp.primary_fingerprints(data) == [ED25519, RSA2048]
    armored = (KEYS / "ed25519.asc").read_bytes() + (KEYS / "rsa2048.asc").read_bytes()
    assert openpgp.primary_fingerprints(armored) == [ED25519, RSA2048]


def test_old_format_packet_headers() -> None:
    body = bytes([4]) + b"\x00" * 9
    old = bytes([0x80 | (6 << 2)]) + bytes([len(body)]) + body
    assert openpgp.primary_fingerprints(old) == [openpgp.fingerprint(body)]
    two_octet = bytes([0x80 | (6 << 2) | 1]) + len(body).to_bytes(2, "big") + body
    assert openpgp.primary_fingerprints(two_octet) == [openpgp.fingerprint(body)]


def test_v6_fingerprint_uses_sha256() -> None:
    body = bytes([6]) + b"\x00" * 4 + bytes([27]) + (32).to_bytes(4, "big") + b"\x01" * 32
    expected = hashlib.sha256(b"\x9b" + len(body).to_bytes(4, "big") + body).hexdigest().upper()
    assert openpgp.fingerprint(body) == expected


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"not a key",
        (KEYS / "bad-base64.asc").read_bytes(),
        bytes([0xC6, 0x10, 4]),  # truncated body
        bytes([0xC6, 0x01, 3]),  # key version 3
    ],
)
def test_malformed_key_data(data: bytes) -> None:
    with pytest.raises(openpgp.OpenPGPError):
        openpgp.primary_fingerprints(data)


# --- schema and markers ------------------------------------------------------------------


def test_committed_lock_matches_its_schema(lock: dict[str, Any]) -> None:
    assert checks.schema_findings(lock) == []


@pytest.mark.parametrize(
    ("path", "value", "where"),
    [
        (("containers", "images", "grafana", "tag"), "latest", "containers.images.grafana.tag"),
        (
            ("containers", "images", "grafana", "digest"),
            "sha256:abc",
            "containers.images.grafana.digest",
        ),
        (
            ("binaries", "restic", "assets", "x86_64", "url"),
            "http://example.com/x",
            "binaries.restic.assets.x86_64.url",
        ),
        (
            ("containers", "target_images", "docker_host_web", "tag"),
            "latest",
            "containers.target_images.docker_host_web.tag",
        ),
        (
            ("repository_keys", "keys", "docker-deb", "fingerprint"),
            "9dc8",
            "repository_keys.keys.docker-deb.fingerprint",
        ),
        (
            ("ansible_collections", "collections", "community.general"),
            "13.x",
            "ansible_collections.collections.community.general",
        ),
    ],
)
def test_schema_rejects_bad_values(
    lock: dict[str, Any], path: tuple[str, ...], value: str, where: str
) -> None:
    node = lock
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    assert [f.where for f in checks.schema_findings(lock)] == [where]


def test_schema_rejects_unknown_keys(lock: dict[str, Any]) -> None:
    lock["containers"]["mirror"] = "x"
    lock["containers"]["images"]["grafana"]["pull_policy"] = "always"
    assert len(checks.schema_findings(lock)) == 2


def test_marker_findings() -> None:
    text = 'a: 1\nb: "TODO(verify)"\nc: <verify>\n# d\n'
    assert [f.where for f in checks.marker_findings(text)] == ["line 2", "line 3"]


# --- network checks with fakes -------------------------------------------------------------


def image_doc(**entry: str) -> dict[str, Any]:
    base = {
        "image": "docker.io/library/x",
        "tag": "1.0",
        "digest": DIGEST,
        "platforms": ["linux/amd64"],
    }
    return {"containers": {"images": {"x": {**base, **entry}}}}


def test_container_digest_and_moved_tag() -> None:
    assert checks.container_findings(image_doc(), lambda image, ref: DIGEST) == []
    moved = checks.container_findings(
        image_doc(), lambda image, ref: DIGEST if ref == DIGEST else "sha256:" + "b" * 64
    )
    assert [(f.severity, f.where) for f in moved] == [("warning", "containers.images.x")]


def test_missing_digest_and_registry_errors() -> None:
    missing = checks.container_findings(image_doc(), lambda image, ref: None)
    assert [f.severity for f in missing] == ["error"]

    def failing(image: str, ref: str) -> str:
        raise net.FetchError("HTTP 500")

    assert "registry request failed" in checks.container_findings(image_doc(), failing)[0].message


def test_target_images_are_checked_too() -> None:
    doc = image_doc()
    doc["containers"]["target_images"] = {"web": doc["containers"]["images"]["x"]}
    findings = checks.container_findings(doc, lambda image, ref: None)
    assert [f.where for f in findings] == ["containers.images.x", "containers.target_images.web"]


def test_committed_target_images_and_agent_package(lock: dict[str, Any]) -> None:
    targets = lock["containers"]["target_images"]
    assert targets, "the docker-host blueprint needs a pinned image"
    for entry in targets.values():
        assert entry["digest"].startswith("sha256:")
        assert entry["tag"] != "latest"
    agent = lock["binaries"]["otelcol_contrib"]
    assert agent["format"] == "deb"
    assert sorted(agent["assets"]) == ["aarch64", "x86_64"]
    assert all(a["url"].endswith(".deb") for a in agent["assets"].values())


def test_unverified_images_are_skipped() -> None:
    def never(image: str, ref: str) -> str:
        raise AssertionError("must not be called")

    assert checks.container_findings(image_doc(digest="TODO(verify)"), never) == []


def test_binary_checksums(lock: dict[str, Any]) -> None:
    expected = {url: sha for _, url, sha in checks._binary_assets(lock)}
    assert checks.binary_findings(lock, expected.__getitem__) == []
    broken = copy.deepcopy(lock)
    broken["binaries"]["quarto"]["assets"]["aarch64"]["sha256"] = "0" * 64
    findings = checks.binary_findings(broken, expected.__getitem__)
    assert [f.where for f in findings] == ["binaries.quarto.assets.aarch64"]


def binary_doc(fmt: str = "binary", sha256: str | None = None) -> dict[str, Any]:
    content = b"#!/bin/sh\nexit 0\n"
    digest = sha256 or hashlib.sha256(content).hexdigest()
    asset = {"url": "https://example.com/tool", "sha256": digest}
    return {
        "binaries": {"tool": {"format": fmt, "assets": {"x86_64": asset, "aarch64": asset}}},
        "_content": content,
    }


def fake_downloader(content: bytes) -> checks.Downloader:
    def download(url: str, out: Any) -> str:
        out.write(content)
        return hashlib.sha256(content).hexdigest()

    return download


def test_fetch_binary_installs_verified_executables(tmp_path: Path) -> None:
    doc = binary_doc()
    dest = tmp_path / "bin" / "tool"
    digest = checks.fetch_binary(
        doc, "tool", dest, machine="arm64", downloader=fake_downloader(doc["_content"])
    )
    assert dest.read_bytes() == doc["_content"]
    assert dest.stat().st_mode & 0o777 == 0o755
    assert digest == doc["binaries"]["tool"]["assets"]["aarch64"]["sha256"]
    assert sorted(p.name for p in dest.parent.iterdir()) == ["tool"]


@pytest.mark.parametrize(
    ("doc", "name", "machine", "message"),
    [
        (binary_doc(), "other", "x86_64", "no binary"),
        (binary_doc("tar.gz"), "tool", "x86_64", "not a single binary"),
        (binary_doc("deb"), "tool", "x86_64", "not a single binary"),
        (binary_doc(), "tool", "riscv64", "no asset for riscv64"),
        (binary_doc(sha256="TODO(verify)"), "tool", "x86_64", "not verified"),
        (binary_doc(sha256="0" * 64), "tool", "x86_64", "sha256 is"),
    ],
)
def test_fetch_binary_refuses(
    tmp_path: Path, doc: dict[str, Any], name: str, machine: str, message: str
) -> None:
    dest = tmp_path / "tool"
    dest.write_bytes(b"old")
    with pytest.raises(checks.FetchRefusedError, match=message):
        checks.fetch_binary(
            doc, name, dest, machine=machine, downloader=fake_downloader(doc["_content"])
        )
    assert dest.read_bytes() == b"old"  # never replaced by unverified content
    assert sorted(p.name for p in tmp_path.iterdir()) == ["tool"]


def test_fetch_command_reports_refusals(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(
        [
            "fetch",
            "quarto",
            "--dest",
            str(tmp_path / "q"),
            "--lock",
            str(INFRA / "versions.lock.yml"),
        ]
    )
    assert code == 1
    assert "not a single binary" in capsys.readouterr().err


def test_key_fingerprints() -> None:
    doc = {
        "repository_keys": {
            "keys": {"k": {"urls": ["https://example.com/k"], "fingerprint": ED25519}}
        }
    }
    good = (KEYS / "ed25519.asc").read_bytes()
    other = (KEYS / "rsa2048.asc").read_bytes()
    assert checks.key_findings(doc, lambda url: good) == []
    assert RSA2048 in checks.key_findings(doc, lambda url: other)[0].message
    both = checks.key_findings(doc, lambda url: good + other)
    assert ED25519 in both[0].message
    assert RSA2048 in both[0].message
    assert checks.key_findings(doc, lambda url: b"junk")[0].severity == "error"


# --- registry client -----------------------------------------------------------------------


def test_image_ref_and_challenge() -> None:
    ref = net.ImageRef.parse("docker.io/library/postgres")
    assert (ref.registry, ref.repository, ref.api_host) == (
        "docker.io",
        "library/postgres",
        "registry-1.docker.io",
    )
    assert net.ImageRef.parse("ghcr.io/google/cadvisor").api_host == "ghcr.io"
    with pytest.raises(ValueError, match="registry host"):
        net.ImageRef.parse("postgres")
    challenge = net.parse_challenge(
        'Bearer realm="https://auth.example.com/token",service="registry.example.com"'
    )
    assert challenge == {
        "realm": "https://auth.example.com/token",
        "service": "registry.example.com",
    }
    with pytest.raises(net.FetchError):
        net.parse_challenge('Basic realm="x"')


def test_retry_after() -> None:
    assert net.retry_after("3", 1) == 3
    assert net.retry_after(None, 1) == 5
    assert net.retry_after(None, 3) == 20
    assert net.retry_after("1000", 1) == net.RATE_LIMIT_MAX_WAIT
    assert net.retry_after("soon", 2) == 10


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes = b"", headers: dict[str, str] | None = None) -> None:
        super().__init__(body)
        self.headers = Message()
        for key, value in (headers or {}).items():
            self.headers[key] = value


def http_error(url: str, code: int, headers: dict[str, str]) -> urllib.error.HTTPError:
    message = Message()
    for key, value in headers.items():
        message[key] = value
    return urllib.error.HTTPError(url, code, "error", message, io.BytesIO())


def test_manifest_digest_token_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, str | None]] = []

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> FakeResponse:
        auth = request.get_header("Authorization")
        calls.append((request.get_method(), request.full_url, auth))
        if request.full_url.startswith("https://auth.example.com/"):
            assert "scope=repository%3Aorg%2Fimg%3Apull" in request.full_url
            return FakeResponse(b'{"token": "t0k"}')
        if auth is None:
            raise http_error(
                request.full_url,
                401,
                {"WWW-Authenticate": 'Bearer realm="https://auth.example.com/token",service="reg"'},
            )
        if request.full_url.endswith("/manifests/missing"):
            raise http_error(request.full_url, 404, {})
        return FakeResponse(headers={"Docker-Content-Digest": DIGEST})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert net.manifest_digest("registry.example.com/org/img", "1.0") == DIGEST
    assert [c[0] for c in calls] == ["HEAD", "GET", "HEAD"]
    assert calls[-1][2] == "Bearer t0k"
    assert net.manifest_digest("registry.example.com/org/img", "missing") is None


def test_manifest_digest_backs_off_on_rate_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = [429, 429, 200]
    waits: list[float] = []

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> FakeResponse:
        code = answers.pop(0)
        if code == 429:
            raise http_error(request.full_url, 429, {"Retry-After": "2"})
        return FakeResponse(headers={"Docker-Content-Digest": DIGEST})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr("pokus_lockcheck.net.time.sleep", waits.append)
    assert net.manifest_digest("registry.example.com/org/img", "1.0") == DIGEST
    assert waits == [2.0, 2.0]


def test_non_https_urls_are_refused() -> None:
    with pytest.raises(net.FetchError, match="non-https"):
        net.download("http://example.com/key")


# --- command line ---------------------------------------------------------------------------


def test_offline_check_reports_markers(capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["check", "--offline", "--lock", str(INFRA / "versions.lock.yml")])
    out = capsys.readouterr()
    markers = checks.marker_findings((INFRA / "versions.lock.yml").read_text())
    assert code == (1 if markers else 0)
    assert f"lock-check (offline): {len(markers)} error(s)" in out.err


def test_validate_pairs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    schema = INFRA / "preflight/schema/support-matrix.schema.json"
    good = f"{INFRA / 'preflight/support-matrix.yml'}={schema}"
    assert cli.main(["validate", good]) == 0
    bad = tmp_path / "matrix.yml"
    bad.write_text("schema_version: 2\n")
    assert cli.main(["validate", f"{bad}={schema}"]) == 1
    assert "schema_version" in capsys.readouterr().out
    assert cli.main(["validate", "no-schema-given"]) == 2


def test_todos_scans_tracked_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)  # noqa: S603, S607 - fixed argv
    (tmp_path / "a.yml").write_text('x: "TODO(verify)"\n')
    (tmp_path / "skip").mkdir()
    (tmp_path / "skip" / "b.md").write_text("TODO(verify) in prose\n")
    (tmp_path / "ignored.txt").write_text("TODO(verify)\n")
    (tmp_path / ".gitignore").write_text("ignored.txt\n")
    monkeypatch.chdir(tmp_path)
    assert cli.main(["todos", "--exclude", "skip/"]) == 1
    out = capsys.readouterr()
    assert out.out.splitlines() == ['a.yml:1: x: "TODO(verify)"']
    (tmp_path / "a.yml").write_text("x: 1\n")
    assert cli.main(["todos", "--exclude", "skip/"]) == 0
