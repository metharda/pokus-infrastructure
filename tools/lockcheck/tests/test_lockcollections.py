"""make lock-collections against a fake Galaxy: what it checks and what it prints."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml
from collection_testlib import artifact

from pinka_lockcheck import lockcollections as lc
from pinka_lockcheck import net

SERVER = "https://galaxy.example.com"
NOW = dt.datetime(2026, 10, 15, 12, 0, tzinfo=dt.UTC)


class FakeGalaxy:
    """Galaxy's discovery, version and download URLs, as ansible-galaxy reads them."""

    def __init__(self) -> None:
        self.artifacts: dict[tuple[str, str], bytes] = {}
        self.claimed: dict[tuple[str, str], str] = {}
        self.fetched: list[str] = []

    def add(self, name: str, version: str, data: bytes, claimed: str | None = None) -> None:
        self.artifacts[(name, version)] = data
        self.claimed[(name, version)] = claimed or hashlib.sha256(data).hexdigest()

    def __call__(self, url: str) -> bytes:
        self.fetched.append(url)
        if url == f"{SERVER}/api/":
            return json.dumps({"available_versions": {"v3": "v3/"}}).encode()
        prefix = f"{SERVER}/api/v3/collections/"
        if url.startswith(prefix):
            namespace, collection, _, version, _ = url[len(prefix) :].split("/")
            key = (f"{namespace}.{collection}", version)
            if key not in self.artifacts:
                raise net.FetchError(f"{url}: HTTP 404")
            filename = f"{namespace}-{collection}-{version}.tar.gz"
            answer = {
                "download_url": f"/download/{filename}",
                "artifact": {"filename": filename, "sha256": self.claimed[key]},
            }
            return json.dumps(answer).encode()
        for (name, version), data in self.artifacts.items():
            if url == f"{SERVER}/download/{name.replace('.', '-')}-{version}.tar.gz":
                return data
        raise net.FetchError(f"{url}: HTTP 404")


LOCK = {
    "ansible_collections": {"collections": {"community.docker": "5.3.0", "ansible.posix": "2.2.2"}}
}
DEP = "community.library_inventory_filtering_v1"


def galaxy() -> FakeGalaxy:
    fake = FakeGalaxy()
    fake.add(
        "community.docker",
        "5.3.0",
        artifact(
            "community.docker",
            "5.3.0",
            {"plugins/a.py": b"a", "COPYING": b"gpl", "LICENSES/Apache-2.0.txt": b"apache"},
            deps={DEP: ">=1.0.0"},
            licenses=("GPL-3.0-or-later", "Apache-2.0"),
        ),
    )
    fake.add("ansible.posix", "2.2.2", artifact("ansible.posix", "2.2.2", {"COPYING": b"gpl"}))
    fake.add(DEP, "1.1.5", artifact(DEP, "1.1.5", {"plugin_utils/f.py": b"f"}))
    return fake


def run(fake: FakeGalaxy, extra: Mapping[str, str] | None = None) -> str:
    return lc.run(LOCK, extra or {}, server=SERVER, fetch=fake, now=lambda: NOW)


def test_an_artifact_with_an_internal_link_is_pinned() -> None:
    # community.general links tests/utils/shippable/aix.sh to remote.sh; the
    # owner's first run stopped there ("not a regular file or directory").
    fake = galaxy()
    fake.add(
        "ansible.posix",
        "2.2.2",
        artifact(
            "ansible.posix",
            "2.2.2",
            {"COPYING": b"gpl", "tests/utils/shippable/remote.sh": b"#!/bin/sh\n"},
            links={"tests/utils/shippable/aix.sh": "remote.sh"},
        ),
    )
    data: dict[str, Any] = yaml.safe_load(run(fake, {DEP: "1.1.5"}))
    assert data["collections"]["ansible.posix"] == "2.2.2"


@pytest.mark.parametrize(
    ("linkname", "message"),
    [
        ("/etc/passwd", "member tests/x is a link to the absolute path /etc/passwd"),
        (
            "../../../outside",
            "member tests/x is a link to ../../../outside, outside the collection",
        ),
    ],
)
def test_an_artifact_with_an_absolute_or_escaping_link_is_refused(
    linkname: str, message: str
) -> None:
    fake = galaxy()
    fake.add(
        "ansible.posix",
        "2.2.2",
        artifact("ansible.posix", "2.2.2", {"COPYING": b"gpl"}, links={"tests/x": linkname}),
    )
    with pytest.raises(lc.PinError, match=re.escape(message)):
        run(fake, {DEP: "1.1.5"})


def test_the_pins_are_printed_in_the_lock_s_format() -> None:
    fake = galaxy()
    text = run(fake, {DEP: "1.1.5"})
    data: dict[str, Any] = yaml.safe_load(text)
    assert data["verified_on"] == dt.date(2026, 10, 15)
    assert data["collections"] == {
        "ansible.posix": "2.2.2",
        "community.docker": "5.3.0",
        DEP: "1.1.5",
    }
    docker = data["artifacts"]["community.docker"]
    assert docker["url"] == f"{SERVER}/download/community-docker-5.3.0.tar.gz"
    assert (
        docker["sha256"]
        == hashlib.sha256(fake.artifacts[("community.docker", "5.3.0")]).hexdigest()
    )
    assert docker["size"] == len(fake.artifacts[("community.docker", "5.3.0")])
    assert "# licenses: GPL-3.0-or-later, Apache-2.0" in text
    assert "# license files: COPYING, LICENSES/Apache-2.0.txt" in text
    assert f"{SERVER}/api/v3/collections/community/docker/versions/5.3.0/" in fake.fetched


def test_an_unlocked_dependency_is_named() -> None:
    with pytest.raises(lc.PinError, match=f"needs {DEP} >=1.0.0, which is not locked"):
        run(galaxy())


@pytest.mark.parametrize(
    ("version", "requirement", "ok"),
    [
        ("1.1.5", ">=1.0.0", True),
        ("1.1.5", ">=1.0.0,<2.0.0", True),
        ("2.0.0", ">=1.0.0,<2.0.0", False),
        ("1.1.5", "*", True),
        ("1.1.5", "1.1.5", True),
        ("1.1.5", "!=1.1.5", False),
        ("0.9.0", ">1.0.0", False),
    ],
)
def test_requirements(version: str, requirement: str, ok: bool) -> None:
    assert lc.satisfies(version, requirement) is ok


def test_a_dependency_below_its_requirement_is_refused() -> None:
    fake = galaxy()
    fake.add(DEP, "0.9.0", artifact(DEP, "0.9.0", {"f.py": b"f"}))
    with pytest.raises(lc.PinError, match=r"needs \S+ >=1\.0\.0; the lock has 0\.9\.0"):
        run(fake, {DEP: "0.9.0"})


def tampered(how: str) -> Callable[[dict[str, bytes]], None]:
    def tamper(members: dict[str, bytes]) -> None:
        if how == "file":
            members["COPYING"] = b"changed"
        elif how == "extra":
            members["plugins/extra.py"] = b"x"
        elif how == "missing":
            del members["COPYING"]
        elif how == "files-json":
            members["FILES.json"] = members["FILES.json"] + b" "

    return tamper


@pytest.mark.parametrize(
    ("how", "message"),
    [
        ("file", "checksum differs from FILES.json: COPYING"),
        ("extra", "in the tarball but not listed: plugins/extra.py"),
        ("missing", "listed but missing: COPYING"),
        ("files-json", "FILES.json does not match MANIFEST.json"),
    ],
)
def test_the_artifact_is_checked_like_collection_verify(how: str, message: str) -> None:
    fake = galaxy()
    fake.add(
        "ansible.posix",
        "2.2.2",
        artifact("ansible.posix", "2.2.2", {"COPYING": b"gpl"}, tamper=tampered(how)),
    )
    with pytest.raises(lc.PinError, match=message):
        run(fake, {DEP: "1.1.5"})


def test_galaxy_s_sha256_must_equal_the_download_s() -> None:
    fake = galaxy()
    data = fake.artifacts[("ansible.posix", "2.2.2")]
    fake.add("ansible.posix", "2.2.2", data, claimed="0" * 64)
    with pytest.raises(lc.PinError, match="Galaxy says 0000"):
        run(fake, {DEP: "1.1.5"})


def test_a_manifest_naming_another_version_is_refused() -> None:
    fake = galaxy()
    fake.add("ansible.posix", "2.2.2", artifact("ansible.posix", "2.2.3", {"COPYING": b"gpl"}))
    with pytest.raises(lc.PinError, match=r"MANIFEST\.json names ansible\.posix 2\.2\.3"):
        run(fake, {DEP: "1.1.5"})


def test_an_unexpected_answer_names_the_missing_field() -> None:
    def fetch(url: str) -> bytes:
        if url.endswith("/api/"):
            return json.dumps({"available_versions": {"v3": "v3/"}}).encode()
        return json.dumps({"artifact": {}}).encode()

    with pytest.raises(lc.PinError, match="has no download_url"):
        lc.run(LOCK, {}, server=SERVER, fetch=fetch, now=lambda: NOW)
    with pytest.raises(lc.PinError, match="names no v3 API"):
        lc.run(LOCK, {}, server=SERVER, fetch=lambda _u: b"{}", now=lambda: NOW)


def test_with_refuses_a_locked_name_and_bad_syntax() -> None:
    with pytest.raises(lc.PinError, match="locked already"):
        run(galaxy(), {"ansible.posix": "2.2.2"})
    for bad in ("community.x", "community.x==1.0", "x==1.0.0"):
        with pytest.raises(lc.PinError, match="--with takes"):
            lc._extra([bad])


def test_main_prints_nothing_but_the_pins_and_changes_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    lock = tmp_path / "versions.lock.yml"
    lock.write_text(yaml.safe_dump(LOCK))
    before = lock.read_bytes()
    monkeypatch.setattr(net, "download", galaxy())
    argv = ["--lock", str(lock), "--server", SERVER, "--with", f"{DEP}==1.1.5"]
    assert lc.main(argv) == 0
    out = capsys.readouterr()
    assert yaml.safe_load(out.out)["collections"][DEP] == "1.1.5"
    assert lock.read_bytes() == before
    assert lc.main(["--lock", str(lock), "--server", SERVER]) == 1
    assert "is not locked" in capsys.readouterr().err


def lock_with_pins(fake: FakeGalaxy) -> dict[str, Any]:
    """A lock whose artifacts are exactly what the fake Galaxy serves."""
    made = yaml.safe_load(run(fake, {DEP: "1.1.5"}))
    return {"ansible_collections": {k: made[k] for k in ("collections", "artifacts")}}


def check(
    lock: Mapping[str, Any],
    fake: FakeGalaxy,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> int:
    path = tmp_path / "versions.lock.yml"
    path.write_text(yaml.safe_dump(dict(lock)))
    monkeypatch.setattr(net, "download", fake)
    return lc.main(["--lock", str(path), "--server", SERVER, "--check"])


def test_the_check_passes_when_the_lock_holds_galaxy_s_pins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = galaxy()
    assert check(lock_with_pins(fake), fake, tmp_path, monkeypatch) == 0
    out = capsys.readouterr()
    assert "# check: the lock's 3 artifact pins match Galaxy" in out.out
    assert "# license files:" in out.out  # the whole output stays in the CI log
    assert out.err == ""


def test_the_check_names_every_difference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = galaxy()
    lock = lock_with_pins(fake)
    artifacts = lock["ansible_collections"]["artifacts"]
    artifacts["community.docker"]["sha256"] = "0" * 64
    artifacts["community.docker"]["size"] += 1
    artifacts["ansible.posix"]["url"] = f"{SERVER}/download/elsewhere.tar.gz"
    del artifacts[DEP]
    artifacts["community.extra"] = {"url": "TODO(verify)", "sha256": "TODO(verify)", "size": 1}
    assert check(lock, fake, tmp_path, monkeypatch) == 1
    err = capsys.readouterr().err
    for expected in (
        "community.extra: the lock has an artifact of a collection that is not locked",
        f"{DEP} 1.1.5: the lock has no artifact",
        "community.docker 5.3.0: sha256 is",
        "community.docker 5.3.0: size is",
        "ansible.posix 2.2.2: url is",
    ):
        assert f"lock-collections: differs from the lock: {expected}" in err, expected
    assert "match Galaxy" not in err


def test_the_check_fails_when_galaxy_s_artifact_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = galaxy()
    lock = lock_with_pins(fake)
    fake.add(
        "ansible.posix",
        "2.2.2",
        artifact("ansible.posix", "2.2.2", {"COPYING": b"gpl"}, links={"x": "/etc/passwd"}),
    )
    assert check(lock, fake, tmp_path, monkeypatch) == 1
    assert "absolute path /etc/passwd" in capsys.readouterr().err


def test_the_check_takes_no_extra_collection(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        lc.main(["--lock", str(tmp_path / "x.yml"), "--check", "--with", f"{DEP}==1.1.5"])
    assert exc.value.code == 2  # argparse's usage error


INFRA = Path(__file__).resolve().parents[3]


def test_the_lock_s_artifacts_are_galaxy_s_files_of_the_locked_versions() -> None:
    section = yaml.safe_load((INFRA / "versions.lock.yml").read_text())["ansible_collections"]
    assert set(section["artifacts"]) == set(section["collections"])
    prefix = f"{lc.GALAXY}/api/v3/plugin/ansible/content/published/collections/artifacts/"
    for name, version in section["collections"].items():
        entry = section["artifacts"][name]
        assert entry["url"] == f"{prefix}{name.replace('.', '-')}-{version}.tar.gz", name
        assert re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]), name
        assert isinstance(entry["size"], int) and entry["size"] > 0, name


def test_ci_compares_the_pins_on_every_change_and_weekly() -> None:
    # Owner, with the "commit" for the symbolic-link fix: approved, weekly run
    # kept; a difference the weekly run finds blocks like any CI failure.
    workflow = yaml.safe_load(
        (INFRA / ".github" / "workflows" / "lock-collections.yml").read_text()
    )
    triggers = workflow[True]  # YAML 1.1 reads the key "on" as true
    paths = {"versions.lock.yml", "tools/lockcheck/**"}
    assert paths <= set(triggers["push"]["paths"])
    assert paths <= set(triggers["pull_request"]["paths"])
    assert triggers["schedule"] and "workflow_dispatch" in triggers
    job = workflow["jobs"]["lock-collections"]
    assert "continue-on-error" not in job and "if" not in job
    assert {"run": "make lock-collections-check"} in job["steps"]
