"""Unit tests for the pre-existing access."""

from __future__ import annotations

import json
import unittest

from helpers import TempRootCase, runner

from pinka_preflight.detect import access
from pinka_preflight.fs import FileSystem

HEADER = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid\n"
TAILNET = "100.64.0.0/10"


def row(address: str, port: int, inode: str, state: str = "0A") -> str:
    return (
        f"   0: {address}:{port:04X} {'0' * len(address)}:0000 {state} 00000000:00000000"
        f" 00:00000000 00000000     0        0 {inode} 1 0000000000000000 100 0 0 10 0\n"
    )


class AccessCase(TempRootCase):
    """A fixture root with sockets, processes and accounts."""

    def proc(self, pid: str, comm: str, unit: str, *inodes: str, **status: str) -> None:
        self.put(f"/proc/{pid}/comm", comm + "\n")
        self.put(f"/proc/{pid}/cgroup", f"0::/system.slice/{unit}\n")
        lines = [f"Name:\t{comm}"] + [f"{k}:\t{v}" for k, v in status.items()]
        self.put(f"/proc/{pid}/status", "\n".join(lines) + "\n")
        self.mkdir(f"/proc/{pid}/fd")
        self.links.update({f"/proc/{pid}/fd/{n}": f"socket:[{i}]" for n, i in enumerate(inodes, 3)})
        self.meta(symlinks=self.links)

    def setUp(self) -> None:
        """An empty root."""
        super().setUp()
        self.links: dict[str, str] = {}
        self.meta(symlinks={})

    def fs(self) -> FileSystem:
        return FileSystem(self.root)


class ListenerTest(AccessCase):
    """Which sockets are listeners outside the lab."""

    def test_loopback_and_lab_sockets_are_not_listed(self) -> None:
        self.put(
            "/proc/net/tcp",
            HEADER
            + row("0100007F", 631, "1")  # 127.0.0.1
            + row("00000000", 18700, "2")  # a lab service
            + row("0A0200C0", 8080, "3"),  # 192.0.2.10
        )
        self.put(
            "/proc/net/tcp6",
            HEADER + row("0000000000000000FFFF00000100007F", 25, "4"),  # ::ffff:127.0.0.1
        )
        self.proc("10", "cupsd", "cups.service", "1")
        self.proc("11", "grafana", "pinka-grafana.service", "2")
        self.proc("12", "example-web", "example-web.service", "3")
        self.proc("13", "exim4", "exim4.service", "4")
        found = access.detect_listeners(self.fs(), runner())
        self.assertEqual(
            [(i.proto, i.address, i.port, i.process) for i in found],
            [("tcp", "192.0.2.10", 8080, "example-web")],
        )

    def test_a_daemon_pinka_installed_is_not_listed_a_preexisting_one_is(self) -> None:
        self.put("/proc/net/udp", HEADER + row("00000000", 41641, "7", state="07"))
        self.proc("20", "tailscaled", "tailscaled.service", "7")
        record = {"added": {"components": {"tailscale": True}}}
        self.assertEqual(access.detect_listeners(self.fs(), runner(), record=record), [])
        found = access.detect_listeners(self.fs(), runner(), admitted_udp=[41641])
        self.assertEqual(
            [(i.proto, i.port, i.unit, i.admitted) for i in found],
            [("udp", 41641, "tailscaled.service", True)],
        )

    def test_a_listener_whose_process_is_unseen_is_listed_and_counts(self) -> None:
        self.put("/proc/net/tcp", HEADER + row("00000000", 9100, "9"))
        found = access.detect_listeners(self.fs(), runner())
        self.assertEqual([(i.port, i.process, i.unit) for i in found], [(9100, None, None)])
        host, reasons = access.classify(found, [], [])
        self.assertEqual((host, reasons), ("shared", ["tcp/9100 on 0.0.0.0 (unknown process)"]))

    def test_socket_activation_names_the_socket_unit_and_the_service(self) -> None:
        self.put("/proc/net/tcp", HEADER + row("00000000", 22, "5") + row("00000000", 631, "6"))
        self.mkdir("/proc/1/fd")
        self.links.update({"/proc/1/fd/3": "socket:[5]", "/proc/1/fd/4": "socket:[6]"})
        self.meta(symlinks=self.links)
        units = runner(
            {
                "systemctl list-sockets --all --no-legend --full": (
                    "0.0.0.0:22 ssh.socket ssh.service\n0.0.0.0:631 cups.socket cups.service\n"
                )
            }
        )
        found = access.detect_listeners(self.fs(), units)
        self.assertEqual(
            [(i.port, i.process, i.unit, i.sshd) for i in found],
            [(22, "sshd", "ssh.socket", True), (631, "cups", "cups.socket", False)],
        )


class AccountTest(AccessCase):
    """Login accounts that are not pinka's."""

    def test_the_login_range_shells_and_pinkas_accounts(self) -> None:
        self.put("/etc/login.defs", "UID_MIN 2000\nUID_MAX 3000\n")
        self.put(
            "/etc/passwd",
            "root:x:0:0::/root:/bin/bash\n"
            "old:x:1000:1000::/home/old:/bin/bash\n"  # below UID_MIN here
            "owner1:x:2001:2001::/home/owner1:/bin/zsh\n"
            "svc:x:2002:2002::/srv:/usr/sbin/nologin\n"
            "locked:x:2003:2003::/home/locked:/bin/false\n"
            "researcher2:x:2004:2004::/home/researcher2:/bin/bash\n",
        )
        record = {"added": {"accounts": ["researcher2"]}}
        self.assertEqual(access.detect_accounts(self.fs(), record), ["owner1"])
        self.assertEqual(access.detect_accounts(self.fs()), ["owner1", "researcher2"])


class SessionTest(AccessCase):
    """The install session's access path."""

    def chain(self, environ: bytes, uid: str = "1000") -> None:
        self.put("/etc/passwd", "researcher1:x:1000:1000::/home/researcher1:/bin/bash\n")
        self.proc("self", "pinka-preflight", "session-1.scope", PPid="300", Uid="0")
        self.proc("300", "sudo", "session-1.scope", PPid="290", Uid="0")
        self.proc("290", "bash", "session-1.scope", PPid="1", Uid=f"{uid}\t{uid}\t{uid}\t{uid}")
        self.put("/proc/290/environ", environ)

    def test_ssh_over_the_tailnet(self) -> None:
        self.chain(b"HOME=/home/researcher1\0SSH_CONNECTION=100.100.1.2 40000 100.101.2.3 22\0")
        self.assertEqual(
            access.detect_session(self.fs(), TAILNET),
            {
                "via": "ssh",
                "address": "100.101.2.3",
                "port": 22,
                "user": "researcher1",
                "tailnet": True,
            },
        )

    def test_without_ssh_connection_or_with_a_malformed_one_it_is_the_console(self) -> None:
        self.chain(b"HOME=/root\0")
        self.assertEqual(access.detect_session(self.fs(), TAILNET), {"via": "console"})
        self.chain(b"SSH_CONNECTION=garbage\0")
        self.assertEqual(access.detect_session(self.fs(), TAILNET), {"via": "console"})

    def test_an_account_not_in_passwd_is_null(self) -> None:
        self.chain(b"SSH_CONNECTION=192.0.2.50 1 192.0.2.10 2222\0", uid="4242")
        session = access.detect_session(self.fs(), TAILNET)
        self.assertEqual((session["port"], session["user"]), (2222, None))


class ClassifyTest(unittest.TestCase):
    """Dedicated or shared."""

    def test_sshd_and_admitted_listeners_keep_a_host_dedicated(self) -> None:
        listeners = [
            access.Listener("tcp", "0.0.0.0", 22, "sshd", "ssh.service"),  # noqa: S104
            access.Listener("udp", "0.0.0.0", 68, "dhclient", None, admitted=True),  # noqa: S104
        ]
        self.assertEqual(
            access.classify(listeners, ["researcher1"], ["researcher1"]), ("dedicated", [])
        )
        host, reasons = access.classify(listeners, ["researcher1", "owner1"], ["researcher1"])
        self.assertEqual((host, reasons), ("shared", ["login account owner1 is not a researcher"]))

    def test_the_profile_form_is_json(self) -> None:
        found = access.Access(listeners=[access.Listener("tcp", "::", 22, None, None)])
        self.assertEqual(json.loads(json.dumps(found.as_dict()))["listeners"][0]["process"], None)


if __name__ == "__main__":
    unittest.main()
