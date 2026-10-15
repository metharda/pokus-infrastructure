"""Tests for the lock checker (no network: every network function is replaced)."""

from __future__ import annotations

import copy
import hashlib
import io
import os
import socket
import subprocess
import sys
import tarfile
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path
from typing import Any

import collection_testlib
import pytest

from pinka_lockcheck import artifact, checks, cli, devsetup, net, openpgp

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


def test_only_unverified_values_carry_the_marker() -> None:
    # lock-check counts a marker on any line; one in a comment would keep it red
    # after every value is verified.
    lines = (INFRA / "versions.lock.yml").read_text().splitlines()
    assert [
        n
        for n, line in enumerate(lines, 1)
        if line.lstrip().startswith("#") and checks.MARKER_RE.search(line)
    ] == []


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


def test_target_and_ci_images_are_checked_too() -> None:
    doc = image_doc()
    doc["containers"]["target_images"] = {"web": doc["containers"]["images"]["x"]}
    doc["containers"]["ci_images"] = {"base": doc["containers"]["images"]["x"]}
    findings = checks.container_findings(doc, lambda image, ref: None)
    assert [f.where for f in findings] == [
        "containers.images.x",
        "containers.target_images.web",
        "containers.ci_images.base",
    ]


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
        (binary_doc("tar.gz"), "tool", "x86_64", "a tar.gz archive: name its executable"),
        (binary_doc("deb"), "tool", "x86_64", "not a single binary or a tar.gz archive"),
        (binary_doc("bzip2"), "tool", "x86_64", "not a single binary or a tar.gz archive"),
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


Member = tuple[str, bytes | None, str | None]


def tar_gz(members: list[Member]) -> bytes:
    """A tar.gz of ``(name, content, link target)``: a file, a symlink or a directory."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, content, link in members:
            info = tarfile.TarInfo(name)
            if link is not None:
                info.type, info.linkname = tarfile.SYMTYPE, link
                tar.addfile(info)
            elif content is None:
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            else:
                info.size, info.mode = len(content), 0o755
                tar.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def archive_doc(archive: bytes, sha256: str | None = None) -> dict[str, Any]:
    digest = sha256 or hashlib.sha256(archive).hexdigest()
    asset = {"url": "https://example.com/tool.tar.gz", "sha256": digest}
    return {"binaries": {"tool": {"format": "tar.gz", "assets": {"x86_64": asset}}}}


UV_LIKE: list[Member] = [
    ("uv-x86_64-unknown-linux-gnu", None, None),
    ("uv-x86_64-unknown-linux-gnu/uv", b"uv binary", None),
    ("uv-x86_64-unknown-linux-gnu/uvx", b"uvx binary", None),
]


def test_fetch_binary_installs_the_executable_of_an_archive(tmp_path: Path) -> None:
    archive = tar_gz(UV_LIKE)
    dest = tmp_path / "tools" / "uv"
    digest = checks.fetch_binary(
        archive_doc(archive),
        "tool",
        dest,
        member="uv",
        machine="x86_64",
        downloader=fake_downloader(archive),
    )
    assert digest == hashlib.sha256(archive).hexdigest()
    assert dest.read_bytes() == b"uv binary"
    assert dest.stat().st_mode & 0o777 == 0o755
    assert sorted(p.name for p in dest.parent.iterdir()) == ["uv"]  # no download left behind


@pytest.mark.parametrize(
    ("members", "sha256", "message"),
    [
        (UV_LIKE, "0" * 64, "sha256 is"),
        (UV_LIKE[:1], None, "0 member"),
        ([*UV_LIKE, ("other/uv", b"second", None)], None, "2 member"),
        ([("top/uv", None, "/usr/bin/uv")], None, "link or directory"),
        ([("top/uv", None, None)], None, "link or directory"),
    ],
)
def test_fetch_binary_refuses_an_archive(
    tmp_path: Path,
    members: list[Member],
    sha256: str | None,
    message: str,
) -> None:
    archive = tar_gz(members)
    dest = tmp_path / "uv"
    dest.write_bytes(b"old")
    with pytest.raises(checks.FetchRefusedError, match=message):
        checks.fetch_binary(
            archive_doc(archive, sha256),
            "tool",
            dest,
            member="uv",
            machine="x86_64",
            downloader=fake_downloader(archive),
        )
    assert dest.read_bytes() == b"old"  # never replaced by unverified content
    assert sorted(p.name for p in tmp_path.iterdir()) == ["uv"]


def test_the_pinned_uv_is_installed_like_bootstrap_does() -> None:
    # make setup installs the uv of versions.lock.yml (for uv sync when the
    # uv on PATH is another version, and for the V6 tests) with bootstrap's
    # checksum-verified install_uv, which needs no Python environment.
    makefile = (INFRA / "Makefile").read_text(encoding="utf-8")
    rule = makefile[makefile.index("$(UV_PINNED): ") :].split("\n\n", 1)[0]
    assert rule.startswith("$(UV_PINNED): bootstrap/lib/pins.sh bootstrap/lib/tooling.sh\n")
    assert 'install_uv "$$1"' in rule
    assert "UV_PINNED := $(CURDIR)/$(BUILD)/tools/uv" in makefile  # absolute
    assert "export PINKA_UV := $(UV_PINNED)" in makefile
    entry = checks.load(INFRA / "versions.lock.yml")["binaries"]["uv"]
    assert entry["format"] == "tar.gz"


# --- the collections' Galaxy fallback (make setup) -----------------------------------------


GALAXY_BIN = Path(sys.executable).parent / "ansible-galaxy"
DEMO = "pinkatest.demo"
DEMO_URL = "https://github.com/example-org/demo/releases/download/1.0.0/pinkatest-demo-1.0.0.tar.gz"


def closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    return port  # closed again: nothing listens there


def galaxy_env(tmp_path: Path) -> dict[str, str]:
    return {
        **os.environ,
        "ANSIBLE_HOME": str(tmp_path / "ansible-home"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }


@pytest.fixture
def demo(tmp_path: Path) -> dict[str, Any]:
    """A collection built like a release tarball, its lock and its requirements."""
    env = galaxy_env(tmp_path)
    source = tmp_path / "source"
    subprocess.run(  # noqa: S603 - the venv's ansible-galaxy
        [str(GALAXY_BIN), "collection", "init", DEMO, "--init-path", str(source)],
        check=True,
        capture_output=True,
        env=env,
    )
    # An internal relative link, as community.general links its test scripts.
    shippable = source / "pinkatest" / "demo" / "tests" / "utils" / "shippable"
    shippable.mkdir(parents=True)
    (shippable / "remote.sh").write_text("#!/bin/sh\necho remote\n", encoding="utf-8")
    (shippable / "aix.sh").symlink_to("remote.sh")
    dist = tmp_path / "dist"
    subprocess.run(  # noqa: S603 - the venv's ansible-galaxy
        [
            str(GALAXY_BIN),
            "collection",
            "build",
            str(source / "pinkatest" / "demo"),
            "--output-path",
            str(dist),
        ],
        check=True,
        capture_output=True,
        env=env,
    )
    tarball = dist / "pinkatest-demo-1.0.0.tar.gz"
    content = tarball.read_bytes()
    requirements = tmp_path / "requirements.yml"
    requirements.write_text(
        f'collections:\n  - name: {DEMO}\n    version: "1.0.0"\n', encoding="utf-8"
    )
    doc = {
        "ansible_collections": {
            "collections": {DEMO: "1.0.0"},
            "fallback": {DEMO: {"url": DEMO_URL, "sha256": hashlib.sha256(content).hexdigest()}},
        }
    }
    return {"doc": doc, "content": content, "requirements": requirements, "env": env}


def recorder(calls: list[list[str]]) -> devsetup.Runner:
    def run(argv: Any) -> int:
        calls.append([str(a) for a in argv])
        return 0

    return run


def run_galaxy(env: dict[str, str]) -> Any:
    def run(argv: Any) -> int:
        command = [str(GALAXY_BIN), *list(argv)[1:]]
        done = subprocess.run(  # noqa: S603 - the venv's ansible-galaxy
            command, check=False, env=env, stdin=subprocess.DEVNULL
        )
        return done.returncode

    return run


def test_galaxy_unreachable_installs_and_verifies_the_fallback(
    tmp_path: Path, demo: dict[str, Any]
) -> None:
    # Galaxy does not answer (nothing listens on its port): setup downloads the
    # pinned release tarball, checks its sha256, installs it, and the same
    # FILES.json check as after a Galaxy install passes.
    asked: list[str] = []

    def github(url: str, out: Any) -> str:
        asked.append(url)
        out.write(demo["content"])
        return hashlib.sha256(demo["content"]).hexdigest()

    dest = tmp_path / "collections"
    source = devsetup.install_collections(
        demo["doc"],
        demo["requirements"],
        dest,
        tmp_path / "downloads",
        galaxy="ansible-galaxy",
        server=f"https://127.0.0.1:{closed_port()}",
        downloader=github,
        run=run_galaxy(demo["env"]),
    )
    assert source == "fallback"
    assert asked == [DEMO_URL]
    assert (dest / "ansible_collections" / "pinkatest" / "demo" / "FILES.json").is_file()
    verify = subprocess.run(  # noqa: S603 - the venv's ansible-galaxy
        [
            str(GALAXY_BIN),
            "collection",
            "verify",
            "--offline",
            "-r",
            str(demo["requirements"]),
            "-p",
            str(dest),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=demo["env"],
        stdin=subprocess.DEVNULL,
    )
    assert verify.returncode == 0, verify.stdout + verify.stderr


def test_a_wrong_tarball_checksum_stops_setup(tmp_path: Path, demo: dict[str, Any]) -> None:
    tampered = demo["content"] + b"x"

    def github(url: str, out: Any) -> str:
        out.write(tampered)
        return hashlib.sha256(tampered).hexdigest()

    calls: list[list[str]] = []
    dest, downloads = tmp_path / "collections", tmp_path / "downloads"
    with pytest.raises(checks.FetchRefusedError, match=r"sha256 is .*, pinned"):
        devsetup.install_collections(
            demo["doc"],
            demo["requirements"],
            dest,
            downloads,
            galaxy="ansible-galaxy",
            server=f"https://127.0.0.1:{closed_port()}",
            downloader=github,
            run=recorder(calls),
        )
    assert calls == []  # nothing was installed
    assert not dest.exists()
    assert list(downloads.iterdir()) == []  # and no tarball is kept
    # An unverified pin never downloads either.
    doc = copy.deepcopy(demo["doc"])
    doc["ansible_collections"]["fallback"][DEMO]["sha256"] = "TODO(verify)"
    with pytest.raises(checks.FetchRefusedError, match=r"pinkatest\.demo is not verified yet"):
        checks.fetch_collections(doc, downloads, downloader=github)
    del doc["ansible_collections"]["fallback"][DEMO]
    with pytest.raises(checks.FetchRefusedError, match="no release tarball pinned"):
        checks.fetch_collections(doc, downloads, downloader=github)


def galaxy_download(calls: list[list[str]], content: bytes) -> devsetup.Runner:
    """ansible-galaxy whose ``collection download`` writes the demo artifact."""

    def run(argv: Any) -> int:
        args = [str(a) for a in argv]
        calls.append(args)
        if args[1:3] == ["collection", "download"]:
            target = Path(args[args.index("-p") + 1])
            (target / "pinkatest-demo-1.0.0.tar.gz").write_bytes(content)
        return 0

    return run


def test_the_demo_artifact_carries_an_internal_link(demo: dict[str, Any]) -> None:
    # What the tests below check: ansible-galaxy kept the link as a link.
    with tarfile.open(fileobj=io.BytesIO(demo["content"]), mode="r:gz") as tar:
        link = tar.getmember("tests/utils/shippable/aix.sh")
    assert link.issym() and link.linkname == "remote.sh"
    assert artifact.check(DEMO, "1.0.0", demo["content"])["name"] == "demo"


def test_galaxy_reachable_downloads_checks_and_installs_offline(
    tmp_path: Path, demo: dict[str, Any]
) -> None:
    calls: list[list[str]] = []
    downloads = tmp_path / "downloads"
    source = devsetup.install_collections(
        demo["doc"],
        demo["requirements"],
        tmp_path / "collections",
        downloads,
        galaxy="ansible-galaxy",
        reachable=lambda url: url == "https://galaxy.ansible.com/api/",
        downloader=lambda url, out: pytest.fail("no fallback download when Galaxy answers"),
        run=galaxy_download(calls, demo["content"]),
    )
    assert source == "galaxy"
    target = downloads / "galaxy"
    download = ["collection", "download", "--no-deps", "-r", str(demo["requirements"])]
    install = ["collection", "install", "--force", "--offline", "--no-deps"]
    tarball = str(target / "pinkatest-demo-1.0.0.tar.gz")
    assert calls == [
        ["ansible-galaxy", *download, "-p", str(target)],
        ["ansible-galaxy", *install, "-p", str(tmp_path / "collections"), tarball],
    ]


@pytest.mark.parametrize("how", ["absolute", "escaping"])
def test_a_refused_artifact_from_galaxy_installs_nothing(
    tmp_path: Path, demo: dict[str, Any], how: str
) -> None:
    linkname = "/etc/passwd" if how == "absolute" else "../../outside.sh"
    bad = collection_testlib.artifact(
        DEMO, "1.0.0", {"README.md": b"demo"}, links={"tests/link.sh": linkname}
    )
    calls: list[list[str]] = []
    with pytest.raises(checks.FetchRefusedError, match="the artifact is refused"):
        devsetup.install_collections(
            demo["doc"],
            demo["requirements"],
            tmp_path / "collections",
            tmp_path / "downloads",
            galaxy="ansible-galaxy",
            reachable=lambda url: True,
            run=galaxy_download(calls, bad),
        )
    assert [c[2] for c in calls] == ["download"]  # nothing was installed


def test_the_collection_artifacts_are_checked_by_lock_check(lock: dict[str, Any]) -> None:
    # make lock-check downloads them like the binaries once they are verified:
    # every locked collection's artifact, and every fallback tarball pinned.
    # The fallback has no entry for a collection added since 3d.6 until the
    # owner decides on its source; it refuses such a collection by name.
    wheres = [where for where, _, _ in checks._binary_assets(lock)]
    section = lock["ansible_collections"]
    for name in section["collections"]:
        assert f"ansible_collections.artifacts.{name}" in wheres
    for name in section.get("fallback", {}):
        assert f"ansible_collections.fallback.{name}" in wheres


def test_every_locked_collection_has_exactly_one_artifact(lock: dict[str, Any]) -> None:
    assert checks.collection_findings(lock) == []
    section = lock["ansible_collections"]
    broken = {
        "ansible_collections": {
            "collections": {**section["collections"], "community.extra": "1.0.0"},
            "artifacts": {
                **section["artifacts"],
                "community.stray": section["artifacts"]["ansible.posix"],
            },
        }
    }
    found = [(f.where, f.message) for f in checks.collection_findings(broken)]
    assert found == [
        ("ansible_collections.artifacts.community.extra", "a locked collection has no artifact"),
        ("ansible_collections.artifacts.community.stray", "an artifact of a collection not locked"),
    ]


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
    assert "a tar.gz archive: name its executable" in capsys.readouterr().err


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
    monkeypatch.setattr("pinka_lockcheck.net.time.sleep", waits.append)
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
