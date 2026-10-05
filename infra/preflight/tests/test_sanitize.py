"""Tests for the fixture sanitizer. Sensitive-looking values are assembled at runtime."""

from __future__ import annotations

import os
import unittest

import sanitize_fixture
from helpers import TempRootCase

PUBLIC_V4 = ".".join(["8", "8", "4", "4"])
PUBLIC_V6 = ":".join(["2606", "4700", "", "1111"])
REAL_MAC = ":".join(["02", "42", "ac", "11", "00", "02"])
RANDOM_UUID = "-".join(["12345678", "9abc", "def0", "1234", "56789abcdef0"])
EMAIL = "someone" + "@" + "mail-provider" + ".dev"


class SanitizerTest(TempRootCase):
    def read(self, path: str) -> str:
        with open(os.path.join(self.root, path.lstrip("/")), encoding="utf-8") as handle:
            return handle.read()

    def test_identity_files_are_reset(self) -> None:
        self.put("/etc/hostname", "lab-of-somebody\n")
        self.put("/etc/machine-id", "f" * 32 + "\n")
        self.put("/sys/class/dmi/id/product_serial", "SERIAL123\n")
        changed = sanitize_fixture.sanitize_root(self.root)
        self.assertEqual(self.read("/etc/hostname"), "pokus-lab\n")
        self.assertEqual(self.read("/etc/machine-id"), "0" * 32 + "\n")
        self.assertEqual(self.read("/sys/class/dmi/id/product_serial"), "0\n")
        self.assertIn("etc/hostname", changed)

    def test_addresses_and_names_are_replaced_consistently(self) -> None:
        self.put(
            "/a.txt",
            f"{PUBLIC_V4} {PUBLIC_V4} 10.0.0.1 {PUBLIC_V6} {REAL_MAC} {RANDOM_UUID} {EMAIL} /home/alex/x\n",
        )
        self.put("/b.txt", "owner=SomeOrg\n")
        sanitize_fixture.sanitize_root(self.root, deny=["someorg"])
        a = self.read("/a.txt")
        self.assertEqual(
            a,
            "192.0.2.1 192.0.2.1 10.0.0.1 2001:db8::1 00:00:5e:00:53:01 "
            "00000000-0000-0000-0000-000000000000 user@example.com /home/researcher1/x\n",
        )
        self.assertEqual(self.read("/b.txt"), "owner=redacted\n")

    def test_nul_separated_files_keep_separators(self) -> None:
        self.put("/proc/1/environ", f"HOME=/home/alex\0MAIL={EMAIL}\0".encode())
        sanitize_fixture.sanitize_root(self.root)
        with open(os.path.join(self.root, "proc/1/environ"), "rb") as handle:
            self.assertEqual(handle.read(), b"HOME=/home/researcher1\0MAIL=user@example.com\0")

    def test_main(self) -> None:
        self.put("/etc/hostname", "x\n")
        self.assertEqual(sanitize_fixture.main([self.root]), 0)


if __name__ == "__main__":
    unittest.main()
