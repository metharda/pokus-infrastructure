"""The release package: its build, its NOTICE, its check, and reading its collections."""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import tarfile
from collections.abc import Mapping
from pathlib import Path

import pytest
import yaml
from collection_testlib import artifact

from pinka_lockcheck import bundle, cli, net

GALAXY = "https://galaxy.example.com/download"
ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "researcher1",
    "GIT_AUTHOR_EMAIL": "researcher1@example.com",
    "GIT_COMMITTER_NAME": "researcher1",
    "GIT_COMMITTER_EMAIL": "researcher1@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}
POSIX = artifact(
    "ansible.posix",
    "2.2.2",
    {
        "COPYING": b"                    GNU GENERAL PUBLIC LICENSE\n",
        "PSF-license.txt": b"PYTHON SOFTWARE FOUNDATION LICENSE VERSION 2\n",
        "plugins/module_utils/_version.py": b"# PSF License (see PSF-license.txt)\n",
        ".ansible-lint": b"# SPDX-License-Identifier: GPL-3.0-or-later\n",
    },
    licenses=(),
)
CRYPTO = artifact(
    "community.crypto",
    "3.4.0",
    {
        "COPYING": b"GNU GENERAL PUBLIC LICENSE\n",
        "LICENSES/Apache-2.0.txt": b"Apache License\n",
        "LICENSES/BSD-3-Clause.txt": b"BSD 3-Clause\n",
        "LICENSES/GPL-3.0-or-later.txt": b"GNU GENERAL PUBLIC LICENSE\n",
        "plugins/module_utils/_obj2txt.py": (
            b"# SPDX-License-Identifier: Apache-2.0 OR BSD-3-Clause\n"
        ),
        "plugins/modules/a.py": b"# SPDX-License-Identifier: GPL-3.0-or-later\n",
        "CHANGELOG.md": b"The SPDX-License-Identifier`` is used ... BSD-2-Clause\n",
        "CHANGELOG.md.license": b"SPDX-License-Identifier: GPL-3.0-or-later\n",
        "changelogs/fragments/x.yml": b"bugfixes: []\n",
        "REUSE.toml": (
            b'version = 1\n[[annotations]]\npath = "changelogs/fragments/**"\n'
            b'SPDX-License-Identifier = "GPL-3.0-or-later"\n'
        ),
    },
    licenses=("GPL-3.0-or-later", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "PSF-2.0"),
)
ARTIFACTS = {"ansible.posix": ("2.2.2", POSIX), "community.crypto": ("3.4.0", CRYPTO)}


def lock(artifacts: Mapping[str, tuple[str, bytes]] = ARTIFACTS) -> dict[str, object]:
    return {
        "ansible_collections": {
            "collections": {name: version for name, (version, _) in artifacts.items()},
            "artifacts": {
                name: {
                    "url": f"{GALAXY}/{name.replace('.', '-')}-{version}.tar.gz",
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "size": len(data),
                }
                for name, (version, data) in artifacts.items()
            },
        }
    }


class Galaxy:
    def __init__(self, artifacts: Mapping[str, tuple[str, bytes]] = ARTIFACTS) -> None:
        self.files = {
            f"{GALAXY}/{n.replace('.', '-')}-{v}.tar.gz": d for n, (v, d) in artifacts.items()
        }

    def __call__(self, url: str) -> bytes:
        if url not in self.files:
            raise net.FetchError(f"{url}: HTTP 404")
        return self.files[url]


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(  # noqa: S603 - git with fixed arguments
        ["git", "-C", str(repo), *args],  # noqa: S607 - git from PATH
        env=ENV,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def repository(tmp_path: Path, doc: Mapping[str, object] | None = None) -> Path:
    """A repository tagged v1.2.3, with an untracked file in its checkout."""
    repo = tmp_path / "repo"
    (repo / "bootstrap").mkdir(parents=True)
    (repo / "bootstrap" / "bootstrap.sh").write_text("#!/bin/sh\necho hi\n")
    (repo / "bootstrap" / "bootstrap.sh").chmod(0o755)
    (repo / "README.md").write_text("infra\n")
    (repo / ".gitignore").write_text("/ansible/inventory/host_vars/*\n/release/\n")
    (repo / "versions.lock.yml").write_text(yaml.safe_dump(dict(doc or lock())))
    (repo / "docs").mkdir()
    (repo / "docs" / "link.md").symlink_to("../README.md")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "infra")
    git(repo, "tag", "v1.2.3")
    (repo / "untracked.txt").write_text("not committed\n")
    return repo


def built(tmp_path: Path, **kwargs: object) -> Path:
    infra = repository(tmp_path)
    return bundle.build(infra, tmp_path / "out", tag="v1.2.3", fetch=Galaxy(), **kwargs)  # type: ignore[arg-type]


def test_the_package_holds_the_tree_at_the_tag_and_the_pinned_artifacts(tmp_path: Path) -> None:
    package = built(tmp_path)
    assert package.name == f"{bundle.PACKAGE_NAME}-1.2.3.tar.gz"
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    assert package.with_name(package.name + ".sha256").read_text() == f"{digest}  {package.name}\n"
    with tarfile.open(package) as tar:
        names = tar.getnames()
        root = f"{bundle.PACKAGE_NAME}-1.2.3"
        assert all(n == root or n.startswith(f"{root}/") for n in names)
        assert f"{root}/untracked.txt" not in names  # tracked files only
        assert tar.getmember(f"{root}/bootstrap/bootstrap.sh").mode == 0o755
        assert tar.getmember(f"{root}/docs/link.md").issym()
        posix = tar.extractfile(f"{root}/release/collections/ansible-posix-2.2.2.tar.gz")
        assert posix is not None and posix.read() == POSIX  # the same bytes
        record = json.load(tar.extractfile(f"{root}/release/RELEASE.json"))  # type: ignore[arg-type]
    repo = tmp_path / "repo"
    assert record["tag"] == "v1.2.3"
    assert record["commit"] == git(repo, "rev-parse", "v1.2.3^{commit}")
    assert record["infra_tree"] == git(repo, "rev-parse", "v1.2.3^{tree}")
    assert [c["name"] for c in record["collections"]] == ["ansible.posix", "community.crypto"]
    # RELEASE.json has one key per line: bootstrap reads it with sed.
    assert '  "commit": "' in json.dumps(record, indent=2)


def test_the_package_is_reproducible(tmp_path: Path) -> None:
    first = built(tmp_path).read_bytes()
    second = bundle.build(
        tmp_path / "repo", tmp_path / "again", tag="v1.2.3", fetch=Galaxy()
    ).read_bytes()
    assert first == second


def test_a_built_package_checks_out(tmp_path: Path) -> None:
    assert bundle.check_package(built(tmp_path)) == []


def test_a_test_build_of_a_commit_has_no_tag(tmp_path: Path) -> None:
    infra = repository(tmp_path)
    package = bundle.build(infra, tmp_path / "out", ref="HEAD", fetch=Galaxy())
    commit = git(tmp_path / "repo", "rev-parse", "HEAD")
    assert package.name == f"{bundle.PACKAGE_NAME}-0.0.0+{commit[:12]}.tar.gz"
    assert bundle.check_package(package) == []


def test_the_notice_comes_from_the_artifacts_files(tmp_path: Path) -> None:
    text = (unpacked(built(tmp_path), tmp_path) / bundle.NOTICE).read_text()
    posix, crypto = text.split("\nansible.posix 2.2.2\n")[1].split("\ncommunity.crypto 3.4.0\n")
    assert "    COPYING: GNU GENERAL PUBLIC LICENSE\n" in posix
    assert "    PSF-license.txt: PYTHON SOFTWARE FOUNDATION LICENSE VERSION 2\n" in posix
    assert "  licenses declared in its metadata (MANIFEST.json): none\n" in posix
    assert "carried by no file" not in posix
    assert "    LICENSES/BSD-3-Clause.txt: BSD 3-Clause\n" in crypto
    # Header, its REUSE .license file, the REUSE.toml annotation, a dual license.
    assert "    Apache-2.0: 1\n    BSD-3-Clause: 1\n    GPL-3.0-or-later: 3\n" in crypto
    assert (
        "  declared in its metadata but carried by no file in the artifact: BSD-2-Clause, PSF-2.0\n"
        in crypto
    )
    # Every file is accounted for: a license text, a named license, or listed.
    assert (
        "  files in the artifact (FILES.json): 4\n"
        "  files naming no license and carrying no license text:\n"
        "    plugins/module_utils/_version.py\n"
    ) in posix
    assert (
        "  files in the artifact (FILES.json): 10\n"
        "  files naming no license and carrying no license text:\n"
        "    REUSE.toml\n"
    ) in crypto


def test_a_reuse_annotation_without_a_license_line_is_listed() -> None:
    data = artifact(
        "ansible.posix",
        "2.2.2",
        {"COPYING": b"GNU GENERAL PUBLIC LICENSE\n", "a.png": b"x", "a.png.license": b"text\n"},
        licenses=(),
    )
    facts = bundle.survey("ansible.posix", "2.2.2", data)
    assert facts.files == 3
    assert facts.unnamed == ("a.png", "a.png.license")


def unpacked(package: Path, tmp_path: Path) -> Path:
    return bundle.unpack(package, tmp_path / "unpacked")


def test_operator_files_do_not_change_the_tree_but_edits_do(tmp_path: Path) -> None:
    root = unpacked(built(tmp_path), tmp_path)
    (root / "ansible" / "inventory" / "host_vars").mkdir(parents=True)
    (root / "ansible" / "inventory" / "host_vars" / "lab1.yml").write_text("x: 1\n")
    assert bundle.check_tree(root) == []
    (root / "README.md").write_text("changed\n")
    assert any("the tree is" in p for p in bundle.check_tree(root))


@pytest.mark.parametrize("change", ["artifact", "extra", "notice", "record", "missing"])
def test_a_changed_package_is_refused(tmp_path: Path, change: str) -> None:
    root = unpacked(built(tmp_path), tmp_path)
    collections = root / bundle.COLLECTIONS_DIR
    record = root / bundle.RELEASE_JSON
    expected = {
        "artifact": "sha256 is",
        "extra": "not pinned in the lock: x.tar.gz",
        "notice": "is not the notice built from the bundled artifacts",
        "record": "the lock pins",
        "missing": "No such file",
    }[change]
    if change == "artifact":
        (collections / "ansible-posix-2.2.2.tar.gz").write_bytes(CRYPTO)
    elif change == "extra":
        (collections / "x.tar.gz").write_bytes(b"x")
    elif change == "notice":
        (root / bundle.NOTICE).write_text("no licenses\n")
    elif change == "record":
        data = json.loads(record.read_text())
        data["collections"][0]["sha256"] = "0" * 64
        record.write_text(json.dumps(data, indent=2) + "\n")
    else:
        (collections / "community-crypto-3.4.0.tar.gz").unlink()
    problems = bundle.check_tree(root)
    assert any(expected in p for p in problems), problems


def test_a_package_with_two_roots_or_an_escaping_member_is_refused(tmp_path: Path) -> None:
    bad = tmp_path / "bad.tar.gz"
    with tarfile.open(bad, "w:gz") as tar:
        for name in ("a/x", "b/y"):
            info = tarfile.TarInfo(name)
            tar.addfile(info, io.BytesIO(b""))
    assert "expected one top directory" in bundle.check_package(bad)[0]
    evil = tmp_path / "evil.tar.gz"
    with tarfile.open(evil, "w:gz") as tar:
        info = tarfile.TarInfo("a/link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tar.addfile(info)
    assert bundle.check_package(evil)


@pytest.mark.parametrize(
    ("setup", "message"),
    [
        ("unverified", "has no verified artifact pin"),
        ("mismatch", "sha256 is"),
        ("refused", "the artifact is refused"),
        ("unreachable", "download failed"),
    ],
)
def test_the_build_refuses_what_does_not_match_the_lock(
    tmp_path: Path, setup: str, message: str
) -> None:
    doc = lock()
    galaxy = Galaxy()
    pins = doc["ansible_collections"]["artifacts"]  # type: ignore[index]
    url = pins["ansible.posix"]["url"]
    if setup == "unverified":
        pins["ansible.posix"]["sha256"] = "TODO(verify)"
    elif setup == "mismatch":
        galaxy.files[url] = CRYPTO
    elif setup == "refused":
        evil = artifact("ansible.posix", "2.2.2", {"COPYING": b"g"}, links={"x": "/etc/passwd"})
        galaxy.files[url] = evil
        pins["ansible.posix"].update(sha256=hashlib.sha256(evil).hexdigest(), size=len(evil))
    else:
        del galaxy.files[url]
    infra = repository(tmp_path, doc)
    with pytest.raises(bundle.PackageError, match=message):
        bundle.build(infra, tmp_path / "out", tag="v1.2.3", fetch=galaxy)
    assert not (tmp_path / "out" / f"{bundle.PACKAGE_NAME}-1.2.3.tar.gz").exists()


@pytest.mark.parametrize(
    ("tag", "message"),
    [("infra-v1.2.3", "not a release tag"), ("v9.9.9", "does not name a commit")],
)
def test_the_build_needs_an_existing_release_tag(tmp_path: Path, tag: str, message: str) -> None:
    infra = repository(tmp_path)
    with pytest.raises(bundle.PackageError, match=message):
        bundle.build(infra, tmp_path / "out", tag=tag, fetch=Galaxy())


def test_the_build_refuses_a_directory_below_the_checkouts_root(tmp_path: Path) -> None:
    # The package is the whole repository's tree; a subdirectory is no release.
    repo = repository(tmp_path)
    with pytest.raises(bundle.PackageError, match="is not the root of its git checkout"):
        bundle.build(repo / "docs", tmp_path / "out", tag="v1.2.3", fetch=Galaxy())
    assert not (tmp_path / "out").exists()


def test_the_artifacts_of_a_package_from_its_file_url_or_tree(tmp_path: Path) -> None:
    package = built(tmp_path)
    want = {"ansible-posix-2.2.2.tar.gz": POSIX, "community-crypto-3.4.0.tar.gz": CRYPTO}
    assert bundle.artifacts_of(str(package)) == want
    url = f"https://example.com/{bundle.PACKAGE_NAME}-1.2.3.tar.gz"
    assert bundle.artifacts_of(url, fetch={url: package.read_bytes()}.__getitem__) == want
    assert bundle.artifacts_of(str(unpacked(package, tmp_path))) == want
    with pytest.raises(bundle.PackageError):
        bundle.artifacts_of(str(tmp_path / "missing.tar.gz"))


@pytest.mark.parametrize(
    ("line", "ids"),
    [
        ("# SPDX-License-Identifier: GPL-3.0-or-later", ["GPL-3.0-or-later"]),
        ("# SPDX-License-Identifier: Apache-2.0 OR BSD-3-Clause", ["Apache-2.0", "BSD-3-Clause"]),
        ("/* SPDX-License-Identifier: (MIT AND BSD-2-Clause) */", ["MIT", "BSD-2-Clause"]),
        (
            "# SPDX-License-Identifier: GPL-2.0-or-later WITH Classpath-exception-2.0",
            ["GPL-2.0-or-later"],
        ),
        # community.crypto's changelog mentions the tag in prose.
        ("Moreover, ``SPDX-License-Identifier:`` is used to declare the applicable", []),
        ("SPDX-License-Identifier: MIT is used here", ["MIT"]),
    ],
)
def test_spdx_expressions_name_licenses_and_prose_does_not(line: str, ids: list[str]) -> None:
    assert bundle._spdx(line) == ids


def test_the_package_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    infra = repository(tmp_path)
    monkeypatch.chdir(infra)
    out = tmp_path / "out"
    # The default fetch was bound at import; build through the module again.
    monkeypatch.setattr(bundle, "build", _build_with(Galaxy(), bundle.build))
    assert cli.main(["package", "build", "--tag", "v1.2.3", "--out", str(out)]) == 0
    package = out / f"{bundle.PACKAGE_NAME}-1.2.3.tar.gz"
    assert cli.main(["package", "check", str(package)]) == 0
    root = bundle.unpack(package, tmp_path / "unpacked")
    assert cli.main(["package", "check", str(root)]) == 0
    capsys.readouterr()
    (root / "README.md").write_text("changed\n")
    assert cli.main(["package", "check", str(root)]) == 1
    assert "package: the tree is" in capsys.readouterr().out
    # make lock-check inside an unpacked package checks it too (offline here).
    assert cli.main(["check", "--offline", "--lock", str(root / "versions.lock.yml")]) == 1
    assert "error: release: the tree is" in capsys.readouterr().out
    assert cli.main(["package", "build", "--tag", "v1", "--out", str(out)]) == 1


def _build_with(fetch: bundle.Fetch, original: object) -> object:
    def build(*args: object, **kwargs: object) -> Path:
        kwargs.setdefault("fetch", fetch)
        return original(*args, **kwargs)  # type: ignore[operator, no-any-return]

    return build


INFRA = Path(__file__).resolve().parents[3]


def test_ci_builds_checks_and_attaches_the_package_of_each_release_tag() -> None:
    workflow = yaml.safe_load((INFRA / ".github" / "workflows" / "release.yml").read_text())
    assert workflow[True] == {"push": {"tags": ["v*"]}}  # YAML 1.1 reads "on" as true
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["package"]
    assert job["permissions"] == {"contents": "write"}
    assert job["env"]["PACKAGE_NAME"] == bundle.PACKAGE_NAME
    runs = [step.get("run", "") for step in job["steps"]]
    assert runs[2] == 'make release-package TAG="$TAG"'
    assert runs[3].startswith("make release-package-check PACKAGE=")
    assert "gh release upload" in runs[4] and "--clobber" not in runs[4]
    assert all(s.get("with", {}).get("persist-credentials") is False for s in job["steps"][:1])


def test_ci_builds_the_package_twice_from_galaxy_and_uploads_nothing() -> None:
    # Owner, with the "commit" for 3d.8: on pushes that touch the release
    # tooling, the lock or tools/lockcheck/, and weekly; and on demand (owner,
    # with the new plan after 16bf749: GitHub Actions minutes).
    workflow = yaml.safe_load((INFRA / ".github" / "workflows" / "release-package.yml").read_text())
    triggers = workflow[True]  # YAML 1.1 reads the key "on" as true
    # A fork's pull request runs it too; a branch here runs it once, on its
    # push (one run per commit, tools/repo-checks/tests/test_triggers.py).
    assert set(triggers) == {"push", "pull_request", "schedule", "workflow_dispatch"}
    assert {"versions.lock.yml", "tools/lockcheck/**", "Makefile"} <= set(triggers["push"]["paths"])
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["release-package"]
    assert "permissions" not in job and "continue-on-error" not in job
    # Its only condition skips the pull request run of a branch of this
    # repository, whose commit runs on its push.
    assert job["if"] == (
        "(github.event_name != 'pull_request' || "
        "github.event.pull_request.head.repo.full_name != github.repository)"
    )
    runs = [step.get("run", "") for step in job["steps"]]
    assert runs[2] == "make release-package REF=HEAD RELEASE_OUT=build/release-a"
    assert runs[3] == "make release-package REF=HEAD RELEASE_OUT=build/release-b"
    assert runs[4].startswith("make release-package-check PACKAGE=")
    assert "cmp " in runs[5] and "release/NOTICE" in runs[6]
    uses = [step.get("uses", "") for step in job["steps"]]
    assert not any("upload" in u for u in uses) and not any("upload" in r for r in runs)


def test_no_tarball_and_no_release_directory_is_committed() -> None:
    # Owner decision: the bundled artifacts live only in release packages.
    tracked = subprocess.run(  # noqa: S603 - git with fixed arguments
        ["git", "-C", str(INFRA), "ls-files", "*.tar.gz", "*.tgz", "release"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert tracked == []
    ignored = subprocess.run(  # noqa: S603 - git with fixed arguments
        ["git", "-C", str(INFRA), "check-ignore", "-q", "release/collections/x.tar.gz"],  # noqa: S607
        check=False,
    )
    assert ignored.returncode == 0


def test_no_tracked_file_is_ignored() -> None:
    # A package's tree hash takes every file .gitignore does not ignore; a
    # tracked file that an ignore rule matches would be left out of it.
    found = subprocess.run(  # noqa: S603 - git with fixed arguments
        ["git", "-C", str(INFRA), "ls-files", "-ci", "--exclude-standard", "."],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert found == []
