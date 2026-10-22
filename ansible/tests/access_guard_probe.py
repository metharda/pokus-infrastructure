"""What the access guard does to real packets, in a network namespace of its own.

Run as root inside a fresh network namespace (``unshare -n``) by
``test_templates.test_the_access_guard_against_the_kernel``; standard library
only, no ``ip`` command. Two TUN devices stand for the host's interfaces:
``eth9`` (a local network, 192.0.2.1, peer 192.0.2.2) and ``tailscale0``
(198.51.100.1, peer 198.51.100.2); TCP segments from the peers are written
into them and the kernel's answers read back, so the kernel's own conntrack
and nftables decide. Listeners: 22 (the install's access path, and closed
with consent elsewhere than over the tailnet), 8080 (closed with consent)
and 8081 (kept). Before the guard is loaded, one connection
to 8080 over ``eth9`` is established while an earlier guard (closing
another port) is loaded, as on a host where conntrack sees every connection
from its start; then the guard under test replaces it with ``nft -f``, in a
namespace that has no other table, and each case below is tried. Prints
one JSON object: case name to whether the host answered.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import select
import socket
import struct
import subprocess
import sys
import threading
import time

TUNSETIFF = 0x400454CA
IFF_TUN = 0x0001
IFF_NO_PI = 0x1000
SIOCSIFADDR = 0x8916
SIOCSIFNETMASK = 0x891C
SIOCGIFFLAGS = 0x8913
SIOCSIFFLAGS = 0x8914
IFF_UP = 0x1
SYN, ACK, PSH, RST = 0x02, 0x10, 0x08, 0x04
WAIT = 1.0  # seconds to wait for an answer that should come at once
HOST_ETH, PEER_ETH = "192.0.2.1", "192.0.2.2"
HOST_TS, PEER_TS = "198.51.100.1", "198.51.100.2"
PORTS = (22, 8080, 8081)


def ifreq_flags(name: str, flags: int = 0) -> bytes:
    return struct.pack("16sH", name.encode(), flags)


def ifreq_addr(name: str, address: str) -> bytes:
    return struct.pack(
        "16sH2s4s8s", name.encode(), socket.AF_INET, b"\0\0", socket.inet_aton(address), b"\0" * 8
    )


def up(control: socket.socket, name: str) -> None:
    flags = struct.unpack("16sH", fcntl.ioctl(control, SIOCGIFFLAGS, ifreq_flags(name)))[1]
    fcntl.ioctl(control, SIOCSIFFLAGS, ifreq_flags(name, flags | IFF_UP))


def tun(control: socket.socket, name: str, address: str) -> int:
    """A TUN device with an address in a /24, up; its file descriptor."""
    fd = os.open("/dev/net/tun", os.O_RDWR)
    fcntl.ioctl(fd, TUNSETIFF, ifreq_flags(name, IFF_TUN | IFF_NO_PI))
    fcntl.ioctl(control, SIOCSIFADDR, ifreq_addr(name, address))
    fcntl.ioctl(control, SIOCSIFNETMASK, ifreq_addr(name, "255.255.255.0"))
    up(control, name)
    return fd


def checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\0"
    total = int(sum(struct.unpack(f"!{len(data) // 2}H", data)))
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return ~total & 0xFFFF


def segment(
    src: str,
    dst: str,
    sport: int,
    dport: int,
    *,
    seq: int,
    ack: int,
    flags: int,
    payload: bytes = b"",
) -> bytes:
    """An IPv4 packet with one TCP segment."""
    tcp = struct.pack("!HHIIBBHHH", sport, dport, seq, ack, 5 << 4, flags, 64240, 0, 0) + payload
    pseudo = socket.inet_aton(src) + socket.inet_aton(dst) + struct.pack("!BBH", 0, 6, len(tcp))
    tcp = tcp[:16] + struct.pack("!H", checksum(pseudo + tcp)) + tcp[18:]
    header = struct.pack(
        "!BBHHHBBH4s4s",
        0x45,
        0,
        20 + len(tcp),
        0,
        0x4000,
        64,
        6,
        0,
        socket.inet_aton(src),
        socket.inet_aton(dst),
    )
    header = header[:10] + struct.pack("!H", checksum(header)) + header[12:]
    return header + tcp


def answer(
    fd: int, sport: int, wanted: int, timeout: float = WAIT
) -> tuple[int, int, bytes] | None:
    """The host's next segment to the peer's ``sport`` with all ``wanted`` flags.

    Returns:
        ``(seq, ack, payload)``, or None when none came in time.
    """
    end = time.monotonic() + timeout
    while (left := end - time.monotonic()) > 0:
        ready, _, _ = select.select([fd], [], [], left)
        if not ready:
            break
        packet = os.read(fd, 65535)
        if len(packet) < 40 or packet[9] != 6:  # IPv4 and TCP headers; TCP
            continue
        ihl = (packet[0] & 0x0F) * 4
        dport, seq, ack, offset, flags = struct.unpack("!2xHIIBB", packet[ihl : ihl + 14])
        if dport == sport and flags & wanted == wanted:
            return seq, ack, packet[ihl + (offset >> 4) * 4 :]
    return None


class Peer:
    """One TCP connection a peer starts by writing segments into a TUN device."""

    def __init__(self, fd: int, src: str, dst: str, sport: int, dport: int) -> None:
        self.fd, self.src, self.dst, self.sport, self.dport = fd, src, dst, sport, dport
        self.seq = 1000
        self.ack = 0

    def send(self, flags: int, payload: bytes = b"") -> None:
        os.write(
            self.fd,
            segment(
                self.src,
                self.dst,
                self.sport,
                self.dport,
                seq=self.seq,
                ack=self.ack,
                flags=flags,
                payload=payload,
            ),
        )
        self.seq += len(payload) + (1 if flags & SYN else 0)

    def connect(self) -> bool:
        """The three-way handshake; whether the host answered the SYN."""
        self.send(SYN)
        got = answer(self.fd, self.sport, SYN | ACK)
        if got is None:
            return False
        self.ack = got[0] + 1
        self.send(ACK)
        return True

    def echo(self, data: bytes) -> bool:
        """Send data on the established connection; whether it comes back."""
        self.send(PSH | ACK, data)
        end = time.monotonic() + WAIT
        while time.monotonic() < end:
            got = answer(self.fd, self.sport, ACK, end - time.monotonic())
            if got is None:
                return False
            seq, _, payload = got
            if payload:
                self.ack = seq + len(payload)
                self.send(ACK)
                return payload == data
        return False

    def reset(self) -> None:
        self.send(RST)


def serve(port: int) -> None:
    """Echo every connection on ``port`` of every address."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", port))  # noqa: S104 - a private network namespace
    server.listen(16)

    def loop() -> None:
        while True:
            conn, _ = server.accept()
            threading.Thread(target=echo, args=(conn,), daemon=True).start()

    def echo(conn: socket.socket) -> None:
        # Every peer resets its connection when its case is done; that ends
        # the connection, nothing to report.
        with conn, contextlib.suppress(OSError):
            while data := conn.recv(4096):
                conn.sendall(data)

    threading.Thread(target=loop, daemon=True).start()


def loopback(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=WAIT) as conn:
            conn.sendall(b"lo")
            return conn.recv(16) == b"lo"
    except OSError:
        return False


def main(nft: str, first: str, guard: str) -> None:
    control = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    up(control, "lo")
    eth = tun(control, "eth9", HOST_ETH)
    tailnet = tun(control, "tailscale0", HOST_TS)
    for port in PORTS:
        serve(port)
    time.sleep(0.2)
    found: dict[str, object] = {}
    # On a host the guard is loaded at boot, and ufw, firewalld or pinka's own
    # table track connections as well; so conntrack sees every connection
    # from its start. Here the guard of an earlier run (closing another port)
    # stands for that; the guard under test replaces it, as a later run does.
    subprocess.run([nft, "-f", first], check=True)
    # Open before the guard closes its port: it must stay open.
    before = Peer(eth, PEER_ETH, HOST_ETH, 40000, 8080)
    found["open before the guard: connect"] = before.connect()
    found["open before the guard: echo before"] = before.echo(b"one")
    subprocess.run([nft, "-f", guard], check=True)
    listed = subprocess.run([nft, "list", "tables"], check=True, capture_output=True, text=True)
    found["tables"] = listed.stdout.split("\n")[0:-1]
    found["open before the guard: echo after"] = before.echo(b"two")
    before.reset()
    cases = {
        "new on eth9 to the closed listener": (eth, PEER_ETH, HOST_ETH, 40001, 8080),
        "new on eth9 to the kept listener": (eth, PEER_ETH, HOST_ETH, 40002, 8081),
        "new on eth9 to the closed install path": (eth, PEER_ETH, HOST_ETH, 40003, 22),
        "new on tailscale0 to the closed listener": (tailnet, PEER_TS, HOST_TS, 40004, 8080),
        "new on tailscale0 to the kept listener": (tailnet, PEER_TS, HOST_TS, 40006, 8081),
        "new on tailscale0 to SSH, closed elsewhere": (tailnet, PEER_TS, HOST_TS, 40005, 22),
    }
    for name, (fd, src, dst, sport, dport) in cases.items():
        peer = Peer(fd, src, dst, sport, dport)
        found[name] = peer.connect()
        peer.reset()
    found["new on loopback to the closed listener"] = loopback(8080)
    found["new on loopback to the closed install path's port"] = loopback(22)
    print(json.dumps(found), flush=True)
    # The server threads still wait on their sockets: end the process without
    # finalizing the interpreter, which aborts when a thread writes meanwhile.
    os._exit(0)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
