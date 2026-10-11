"""Installed documentation releases (BUILD_PROMPT §7.19)."""

from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
import tempfile
import unittest

from pinka_lab import docs_store
from pinka_lab.docs_store import DocsError

NOW = "2026-10-10T12:00:00Z"


def tarball(directory: str, name: str, members: list[tuple[str, str, str]]) -> tuple[str, str]:
    """Write a tarball of ``(kind, name, content_or_target)`` members.

    Args:
        directory: Where to write it.
        name: File name.
        members: ``("file", path, text)``, ``("dir", path, "")``,
            ``("symlink", path, target)``, ``("hardlink", path, target)`` or
            ``("fifo", path, "")``.

    Returns:
        ``(path, sha256)``.
    """
    path = os.path.join(directory, name)
    with tarfile.open(path, "w:gz") as archive:
        for kind, member, value in members:
            info = tarfile.TarInfo(member)
            data = None
            if kind == "file":
                payload = value.encode()
                info.size, data = len(payload), io.BytesIO(payload)
                info.mode = 0o600
            elif kind == "dir":
                info.type, info.mode = tarfile.DIRTYPE, 0o700
            elif kind == "symlink":
                info.type, info.linkname = tarfile.SYMTYPE, value
            elif kind == "hardlink":
                info.type, info.linkname = tarfile.LNKTYPE, value
            else:
                info.type = tarfile.FIFOTYPE
            archive.addfile(info, data)
    with open(path, "rb") as handle:
        return path, hashlib.sha256(handle.read()).hexdigest()


SITE = [("file", "index.html", "<html>v</html>"), ("file", "css/site.css", "body{}")]


class DocsStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = self._tmp.name
        self.base = os.path.join(self.tmp, "docs")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def release(self, name: str, members: list[tuple[str, str, str]] = SITE) -> tuple[str, str]:
        return tarball(self.tmp, name, members)

    def test_install_switches_current_and_normalizes_modes(self) -> None:
        path, digest = self.release("pinka-docs-v1.tar.gz")
        self.assertEqual(
            docs_store.install(self.base, path, digest, None, NOW), "installed pinka-docs-v1"
        )
        self.assertEqual(docs_store.current(self.base), "pinka-docs-v1")
        site = os.path.join(self.base, "current")
        self.assertEqual(os.readlink(site), "pinka-docs-v1")  # relative: switches in one step
        self.assertTrue(os.path.isfile(os.path.join(site, "css", "site.css")))
        self.assertEqual(os.stat(os.path.join(site, "index.html")).st_mode & 0o777, 0o644)
        self.assertEqual(os.stat(os.path.join(site, "css")).st_mode & 0o777, 0o755)
        with open(os.path.join(self.base, "versions.json"), encoding="utf-8") as handle:
            index = json.load(handle)
        self.assertEqual(
            index["versions"],
            [{"version": "pinka-docs-v1", "sha256": digest, "installed_at": NOW}],
        )
        again = docs_store.install(self.base, path, digest, None, NOW)
        self.assertEqual(again, "pinka-docs-v1 is already current")

    def test_a_site_directory_inside_the_tarball(self) -> None:
        path, digest = self.release(
            "v2.tgz", [("dir", "site", ""), ("file", "site/index.html", "<html></html>")]
        )
        docs_store.install(self.base, path, digest, None, NOW)
        self.assertTrue(os.path.isfile(os.path.join(self.base, "v2", "index.html")))

    def test_checksum_and_names_are_checked(self) -> None:
        path, digest = self.release("v1.tar.gz")
        with self.assertRaisesRegex(DocsError, "checksum mismatch"):
            docs_store.install(self.base, path, "0" * 64, None, NOW)
        with self.assertRaisesRegex(DocsError, "64 hex digits"):
            docs_store.install(self.base, path, "abc", None, NOW)
        for bad in ("current", "../x", ".hidden", "a b"):
            with self.assertRaisesRegex(DocsError, "invalid version name"):
                docs_store.install(self.base, path, digest, bad, NOW)
        docs_store.install(self.base, path, digest, "v1", NOW)
        other, other_digest = self.release("other.tar.gz", [("file", "index.html", "x")])
        with self.assertRaisesRegex(DocsError, "another checksum"):
            docs_store.install(self.base, other, other_digest, "v1", NOW)

    def test_unsafe_archives_are_refused(self) -> None:
        cases = {
            "absolute": [("file", "/etc/evil", "x")],
            "dotdot": [("file", "../evil", "x")],
            "nested dotdot": [("file", "a/../../evil", "x")],
            "absolute link": [("symlink", "index.html", "/etc/shadow")],
            "leaving link": [("symlink", "css/x", "../../outside")],
            "leaving hard link": [("hardlink", "index.html", "../outside")],
            "fifo": [("fifo", "pipe", "")],
        }
        for label, members in cases.items():
            path, digest = self.release(f"{label.replace(' ', '-')}.tar.gz", [*SITE, *members])
            with self.assertRaisesRegex(DocsError, "refused", msg=label):
                docs_store.install(self.base, path, digest, None, NOW)
            self.assertIsNone(docs_store.current(self.base), label)
            # Nothing is left behind: no version directory, no work directory.
            self.assertEqual([n for n in os.listdir(self.base) if n != "versions.json"], [], label)

    def test_a_link_inside_the_site_is_kept(self) -> None:
        path, digest = self.release("v1.tar.gz", [*SITE, ("symlink", "css/alias.css", "site.css")])
        docs_store.install(self.base, path, digest, None, NOW)
        self.assertEqual(os.readlink(os.path.join(self.base, "v1", "css", "alias.css")), "site.css")

    def test_a_tarball_without_a_site_is_refused(self) -> None:
        path, digest = self.release("empty.tar.gz", [("file", "README", "x")])
        with self.assertRaisesRegex(DocsError, "index.html is missing"):
            docs_store.install(self.base, path, digest, None, NOW)

    def test_three_versions_are_kept_and_rollback_goes_back(self) -> None:
        for number in range(1, 5):
            path, digest = self.release(
                f"v{number}.tar.gz", [("file", "index.html", f"<html>{number}</html>")]
            )
            docs_store.install(self.base, path, digest, None, NOW)
        self.assertEqual(
            sorted(os.listdir(self.base)), ["current", "v2", "v3", "v4", "versions.json"]
        )
        self.assertFalse(os.path.exists(os.path.join(self.base, "v1")))
        self.assertEqual(docs_store.rollback(self.base), "switched from v4 back to v3")
        self.assertEqual(docs_store.rollback(self.base), "switched from v3 back to v2")
        with self.assertRaisesRegex(DocsError, "oldest installed version"):
            docs_store.rollback(self.base)
        with open(os.path.join(self.base, "current", "index.html")) as handle:
            self.assertEqual(handle.read(), "<html>2</html>")

    def test_rollback_without_a_release(self) -> None:
        with self.assertRaisesRegex(DocsError, "no documentation release is current"):
            docs_store.rollback(self.base)

    def test_version_names_from_file_names(self) -> None:
        self.assertEqual(docs_store.version_from("https://example.com/a/docs-v1.tgz"), "docs-v1")
        self.assertEqual(docs_store.version_from("/tmp/site.tar.xz"), "site")
        self.assertEqual(docs_store.version_from("plain"), "plain")


if __name__ == "__main__":
    unittest.main()
