"""The one rule for collection artifacts (make lock-collections, make setup, bootstrap)."""

from __future__ import annotations

import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
from collection_testlib import artifact, member

from pinka_lockcheck import artifact as rule

NAME, VERSION = "community.general", "13.4.0"
FILES = {
    "COPYING": b"gpl",
    "tests/utils/shippable/remote.sh": b"#!/bin/sh\necho remote\n",
    "plugins/modules/a.py": b"a = 1\n",
}
SCRIPT = Path(rule.__file__)


@pytest.mark.parametrize(
    "links",
    [
        {"tests/utils/shippable/aix.sh": "remote.sh"},  # as community.general has it
        {"tests/linked.sh": "utils/shippable/remote.sh"},
        {"plugins/b.py": "../plugins/modules/a.py"},  # leaves its directory, stays inside
        {"tests/utils/x.sh": "shippable/aix.sh", "tests/utils/shippable/aix.sh": "remote.sh"},
        {"plugins/same": "modules"},  # a link to a directory that has no member of its own
        {"tests/top": ".."},  # the collection's own directory
    ],
)
def test_an_internal_relative_link_passes(links: dict[str, str]) -> None:
    info = rule.check(NAME, VERSION, artifact(NAME, VERSION, FILES, links=links))
    assert info["name"] == "general"
    files = rule.members(artifact(NAME, VERSION, FILES, links=links))
    for path, linkname in links.items():
        target = rule.link_target(path, linkname)
        if target in FILES:
            assert files[path] == FILES[target]  # counted with its target's content


@pytest.mark.parametrize(
    ("links", "message"),
    [
        ({"tests/aix.sh": "/etc/passwd"}, "link to the absolute path /etc/passwd"),
        ({"tests/aix.sh": "../../outside.sh"}, "outside the collection"),
        ({"aix.sh": "../general/COPYING"}, "outside the collection"),
        ({"tests/aix.sh": "missing.sh"}, "tests/missing.sh, which is not in the artifact"),
        ({"a": "b", "b": "a"}, "do not end at a file or directory"),
    ],
)
def test_an_absolute_escaping_or_dangling_link_is_refused(
    links: dict[str, str], message: str
) -> None:
    with pytest.raises(rule.ArtifactError, match=message):
        rule.check(NAME, VERSION, artifact(NAME, VERSION, FILES, links=links))


@pytest.mark.parametrize(
    ("special", "message"),
    [
        (member("plugins/hard.py", tarfile.LNKTYPE, "plugins/modules/a.py"), "a hard link"),
        (member("tests/fifo", tarfile.FIFOTYPE), "a FIFO"),
        (member("tests/tty", tarfile.CHRTYPE), "a device"),
        (member("tests/disk", tarfile.BLKTYPE), "a device"),
        (member("../evil", tarfile.REGTYPE), "lies outside the collection"),
        (member("/abs", tarfile.REGTYPE), "lies outside the collection"),
    ],
)
def test_hard_links_devices_fifos_and_outside_members_are_refused(
    special: tarfile.TarInfo, message: str
) -> None:
    with pytest.raises(rule.ArtifactError, match=message):
        rule.members(artifact(NAME, VERSION, FILES, special=[special]))


def test_a_link_to_a_fifo_is_refused() -> None:
    data = artifact(
        NAME,
        VERSION,
        FILES,
        links={"tests/l": "fifo"},
        special=[member("tests/fifo", tarfile.FIFOTYPE)],
    )
    with pytest.raises(rule.ArtifactError, match="a link to a FIFO"):
        rule.members(data)


def run_script(*args: str) -> subprocess.CompletedProcess[str]:
    # As bootstrap runs it: isolated, standard library only, no package on the path.
    return subprocess.run(  # noqa: S603 - this interpreter and the checked-in script
        [sys.executable, "-I", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )


def test_the_script_checks_an_artifact_as_bootstrap_runs_it(tmp_path: Path) -> None:
    good = tmp_path / "good.tar.gz"
    good.write_bytes(artifact(NAME, VERSION, FILES, links={"tests/aix.sh": "x/../README"}))
    bad = tmp_path / "bad.tar.gz"
    bad.write_bytes(artifact(NAME, VERSION, FILES, links={"tests/aix.sh": "/etc/passwd"}))
    found = run_script(NAME, VERSION, str(good))
    assert found.returncode == 1 and "README, which is not in the artifact" in found.stderr
    good.write_bytes(artifact(NAME, VERSION, FILES, links={"tests/aix.sh": "../COPYING"}))
    assert run_script(NAME, VERSION, str(good)).returncode == 0
    refused = run_script(NAME, VERSION, str(bad))
    assert refused.returncode == 1
    assert "absolute path /etc/passwd" in refused.stderr
    assert run_script(NAME, "9.9.9", str(good)).returncode == 1  # MANIFEST.json names 13.4.0
    assert run_script(NAME).returncode == 2


def test_every_license_text_is_listed_and_reuse_annotations_are_not() -> None:
    # Names from the five pinned artifacts: ansible.posix ships PSF-license.txt
    # (for plugins/module_utils/_version.py and mount.py); the community
    # collections annotate files with REUSE's .license files.
    paths = [
        "COPYING",
        "PSF-license.txt",
        "LICENSES/GPL-3.0-or-later.txt",
        "LICENSES/BSD-2-Clause.txt",
        "docs/LICENSES/MIT.txt",
        "LICENSE",
        "LICENSE-MIT",
        "NOTICE.md",
        "CHANGELOG.md.license",
        "tests/sanity/ignore-2.21.txt.license",
        "plugins/modules/license_manager.py",
        "plugins/module_utils/_version.py",
        "README.md",
    ]
    assert rule.license_texts(paths) == [
        "COPYING",
        "LICENSE",
        "LICENSE-MIT",
        "LICENSES/BSD-2-Clause.txt",
        "LICENSES/GPL-3.0-or-later.txt",
        "NOTICE.md",
        "PSF-license.txt",
        "docs/LICENSES/MIT.txt",
    ]


def test_the_check_reports_a_license_text_not_named_license() -> None:
    files = {**FILES, "PSF-license.txt": b"PSF", "CHANGELOG.md.license": b"GPL"}
    info = rule.check(NAME, VERSION, artifact(NAME, VERSION, files))
    assert info["license_files"] == ["COPYING", "PSF-license.txt"]
