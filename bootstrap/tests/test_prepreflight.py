"""Tests for bootstrap/lib/prepreflight.sh (BUILD_PROMPT §5.2 step 2).

Each scenario builds a fake root (``--root``) and a PATH that holds only stub
commands and the few real tools the script needs, so a missing tool is really
missing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from bashlib import BOOTSTRAP

SCRIPT = BOOTSTRAP / "lib" / "prepreflight.sh"
REAL_TOOLS = ("cat", "grep", "awk", "date", "chmod", "mkdir", "cp")
WSL2_KERNEL = "6.6.87.2-microsoft-standard-WSL2"


class Host:
    """A fake host: files below ``root`` and commands in ``bin``."""

    def __init__(self, base: Path) -> None:
        self.root = base / "root"
        self.bin = base / "bin"
        self.bin.mkdir()
        for tool in REAL_TOOLS:
            real = shutil.which(tool)
            assert real, tool
            (self.bin / tool).symlink_to(real)
        self.file("proc/1/comm", "systemd\n")
        self.file("proc/sys/kernel/osrelease", "6.8.0-45-generic\n")
        (self.root / "opt").mkdir()
        (self.root / "etc").mkdir()
        self.command("id", "echo 0")
        self.command("uname", "echo x86_64")
        self.command("getconf", 'echo "glibc 2.39"')
        self.command("curl", "exit 0")
        self.command("tar", "exit 0")

    def file(self, path: str, content: str) -> Path:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return target

    def command(self, name: str, body: str) -> None:
        stub = self.bin / name
        stub.write_text(f"#!/bin/sh\n{body}\n")
        stub.chmod(0o755)

    def remove(self, name: str) -> None:
        (self.bin / name).unlink()

    def run(
        self, *args: str, answer: str = "", prefix: tuple[str, ...] = ()
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [*prefix, "/bin/sh", str(SCRIPT), "--root", str(self.root), *args],
            input=answer,
            capture_output=True,
            text=True,
            env={"PATH": str(self.bin)},
            check=False,
        )


@pytest.fixture
def host(tmp_path: Path) -> Host:
    return Host(tmp_path)


def test_healthy_host(host: Host) -> None:
    res = host.run("--non-interactive")
    assert res.returncode == 0, res.stderr
    assert "ok: root, systemd, glibc, x86_64" in res.stderr


def test_not_root(host: Host) -> None:
    host.command("id", "echo 1000")
    res = host.run()
    assert res.returncode == 77
    assert "must run as root" in res.stderr


def test_non_systemd_init(host: Host) -> None:
    host.file("proc/1/comm", "openrc-init\n")
    res = host.run()
    assert res.returncode == 20
    assert "PID 1 is 'openrc-init', not systemd" in res.stderr


def test_wsl1(host: Host) -> None:
    host.file("proc/1/comm", "init\n")
    host.file("proc/sys/kernel/osrelease", "4.4.0-19041-Microsoft\n")
    res = host.run()
    assert res.returncode == 20
    assert "wsl --set-version <distro> 2" in res.stderr


def wsl2_without_systemd(host: Host) -> None:
    host.file("proc/1/comm", "init\n")
    host.file("proc/sys/kernel/osrelease", WSL2_KERNEL + "\n")


def test_wsl2_systemd_missing_non_interactive(host: Host) -> None:
    wsl2_without_systemd(host)
    res = host.run("--non-interactive")
    assert res.returncode == 30
    assert "systemd=true" in res.stderr
    assert "wsl --terminate <distro>" in res.stderr
    assert not (host.root / "etc" / "wsl.conf").exists()


def test_wsl2_systemd_enabled_but_not_running(host: Host) -> None:
    wsl2_without_systemd(host)
    host.file("etc/wsl.conf", "[automount]\nenabled = false\n\n[Boot]\nSystemd = True  # on\n")
    res = host.run("--interactive")
    assert res.returncode == 75
    assert "is not running yet" in res.stderr


def test_wsl2_interactive_creates_wsl_conf(host: Host) -> None:
    wsl2_without_systemd(host)
    res = host.run("--interactive", answer="y\n")
    assert res.returncode == 75
    conf = host.root / "etc" / "wsl.conf"
    assert conf.read_text() == "[boot]\nsystemd=true\n"
    # There was none: uninstall removes it again (BUILD_PROMPT §5.4).
    kept = host.root / "var/lib/pinka/originals/etc/wsl.conf"
    assert not kept.exists()
    assert Path(f"{kept}.absent").exists()


def test_wsl2_interactive_appends_boot_section(host: Host) -> None:
    wsl2_without_systemd(host)
    conf = host.file("etc/wsl.conf", "[network]\nhostname = lab\n")
    res = host.run("--interactive", answer="yes\n")
    assert res.returncode == 75
    assert conf.read_text() == "[network]\nhostname = lab\n\n[boot]\nsystemd=true\n"
    # The original, before the install record exists (BUILD_PROMPT §5.4, §7.21).
    kept = host.root / "var/lib/pinka/originals/etc/wsl.conf"
    assert kept.read_text() == "[network]\nhostname = lab\n"


def test_wsl2_interactive_declined(host: Host) -> None:
    wsl2_without_systemd(host)
    res = host.run("--interactive", answer="n\n")
    assert res.returncode == 30
    assert not (host.root / "etc" / "wsl.conf").exists()


def test_wsl2_existing_boot_section_is_not_edited(host: Host) -> None:
    wsl2_without_systemd(host)
    conf = host.file("etc/wsl.conf", "[boot]\nsystemd=false\n")
    res = host.run("--interactive", answer="y\n")
    assert res.returncode == 30
    assert conf.read_text() == "[boot]\nsystemd=false\n"


def test_musl(host: Host) -> None:
    host.file("lib/ld-musl-x86_64.so.1", "")
    res = host.run()
    assert res.returncode == 20
    assert "musl" in res.stderr


def test_glibc_detected_through_ldd(host: Host) -> None:
    host.remove("getconf")
    host.command("ldd", 'echo "ldd (GNU libc) 2.39"')
    assert host.run().returncode == 0
    host.command("ldd", 'echo "musl libc (x86_64)" >&2; exit 1')
    res = host.run()
    assert res.returncode == 20
    assert "glibc was not found" in res.stderr


@pytest.mark.parametrize(("arch", "code"), [("aarch64", 0), ("riscv64", 20), ("i686", 20)])
def test_architecture(host: Host, arch: str, code: int) -> None:
    host.command("uname", f"echo {arch}")
    assert host.run().returncode == code


@pytest.mark.parametrize(
    ("manager", "hint"),
    [
        ("apt-get", "sudo apt-get update && sudo apt-get install -y curl"),
        ("dnf", "sudo dnf install -y curl"),
        ("zypper", "sudo zypper install -y curl"),
        ("pacman", "sudo pacman -S --needed curl"),
    ],
)
def test_missing_download_tool_prints_the_install_command(
    host: Host, manager: str, hint: str
) -> None:
    host.remove("curl")
    host.command(manager, "exit 0")
    res = host.run()
    assert res.returncode == 30
    assert hint in res.stderr


def test_wget_is_enough(host: Host) -> None:
    host.remove("curl")
    host.command("wget", "exit 0")
    assert host.run().returncode == 0


def test_missing_tar(host: Host) -> None:
    host.remove("tar")
    host.command("dnf", "exit 0")
    res = host.run()
    assert res.returncode == 30
    assert "sudo dnf install -y tar" in res.stderr


# Root may write regardless of permission bits, so a root test run drops to an
# unprivileged user that keeps only the capability to read and traverse.
UNPRIVILEGED = (
    (
        shutil.which("setpriv") or "setpriv",
        "--reuid=65534",
        "--regid=65534",
        "--clear-groups",
        "--inh-caps=+dac_read_search",
        "--ambient-caps=+dac_read_search",
    )
    if os.geteuid() == 0
    else ()
)


def test_read_only_opt(host: Host) -> None:
    (host.root / "opt").chmod(0o555)
    res = host.run(prefix=UNPRIVILEGED)
    assert res.returncode == 30
    assert "is not writable" in res.stderr
    (host.root / "opt").chmod(0o777)
    assert host.run(prefix=UNPRIVILEGED).returncode == 0


def test_unknown_option(host: Host) -> None:
    assert host.run("--bogus").returncode == 64
