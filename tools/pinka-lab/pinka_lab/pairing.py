"""Pairing a lab node with an inference node.

``pinka pair show`` on the inference node prints a one-time code and
listens on its tailnet address (the control port, while the control endpoint
is stopped); ``pinka pair connect <node> <code>`` on the lab node
connects to it. Each node already has a self-signed certificate
(``/etc/pinka/inference/self.crt``, from the inference_control role); the two
exchange them over TLS, and each proves knowledge of the code with an HMAC
over both certificates' SHA-256 fingerprints, its node name and its
``lab_mode``. A certificate swapped in by anyone in between changes a
fingerprint and breaks the HMAC; staging and production nodes never pair.
Afterwards each node trusts exactly the other's certificate
(``peer.crt``) for mutual TLS, and its split guard admits only the other's
tailnet address, as the pairing connection saw it (``peer.json``), to the
split-inference ports. Standard library only.
"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import secrets
import socket
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

PROTOCOL = "pinka-pair-v1"
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 32 symbols, no 0/O or 1/I
CODE_LENGTH = 12  # 60 bits
MAX_ATTEMPTS = 5
MAX_MESSAGE = 16384


class PairingError(RuntimeError):
    """Pairing failed; the message says why."""


def new_code() -> str:
    """A fresh one-time code, grouped for reading aloud.

    Returns:
        ``XXXX-XXXX-XXXX``.
    """
    raw = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
    return "-".join(raw[i : i + 4] for i in range(0, CODE_LENGTH, 4))


def normalize(code: str) -> str:
    """Canonical form of a typed code.

    Args:
        code: The code as typed (any case, with or without dashes and spaces).

    Returns:
        Upper case without separators.

    Raises:
        PairingError: If it is not a code.
    """
    value = code.replace("-", "").replace(" ", "").upper()
    if len(value) != CODE_LENGTH or any(c not in ALPHABET for c in value):
        raise PairingError("not a pairing code (12 characters, as shown on the inference node)")
    return value


def cert_sha256(pem: str) -> str:
    """SHA-256 of a PEM certificate's DER encoding.

    Args:
        pem: Certificate.

    Returns:
        Lowercase hex digest.
    """
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()


def mac(  # noqa: PLR0917 - the fields of one proof
    code: str, side: str, own_sha: str, other_sha: str, node: str, lab_mode: str
) -> str:
    """The proof of the code for one side.

    Args:
        code: Normalized code.
        side: ``lab`` or ``inference``.
        own_sha: Fingerprint of the sender's certificate.
        other_sha: Fingerprint of the receiver's certificate as the sender saw it.
        node: Sender's node name.
        lab_mode: Sender's lab mode.

    Returns:
        Hex HMAC-SHA256.
    """
    message = "|".join((PROTOCOL, side, own_sha, other_sha, node, lab_mode)).encode()
    return hmac.new(code.encode(), message, hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class Identity:
    """One node's side of the pairing.

    Attributes:
        node: Tailnet name of the node.
        lab_mode: ``production`` or ``staging``.
        cert_pem: Its certificate.
    """

    node: str
    lab_mode: str
    cert_pem: str


@dataclass(frozen=True)
class Peer:
    """What a node keeps about the other.

    Attributes:
        node: The peer's tailnet name.
        lab_mode: Its lab mode.
        cert_pem: Its certificate.
        address: Its tailnet address, as the pairing connection saw it; the
            split guard admits only this address to the split-inference ports.
    """

    node: str
    lab_mode: str
    cert_pem: str
    address: str = ""

    def record(self, paired_at: str) -> dict[str, Any]:
        """The peer.json document.

        Args:
            paired_at: UTC time of the pairing.

        Returns:
            Node, lab mode, certificate fingerprint, tailnet address and time.
        """
        return {
            "schema_version": 1,
            "node": self.node,
            "lab_mode": self.lab_mode,
            "cert_sha256": cert_sha256(self.cert_pem),
            "address": self.address,
            "paired_at": paired_at,
        }


def lab_hello(code: str, lab: Identity, server_sha: str) -> dict[str, Any]:
    """The lab node's message.

    Args:
        code: Normalized code.
        lab: The lab node.
        server_sha: Fingerprint of the certificate the TLS server presented.

    Returns:
        The message.
    """
    own = cert_sha256(lab.cert_pem)
    return {
        "protocol": PROTOCOL,
        "node": lab.node,
        "lab_mode": lab.lab_mode,
        "cert": lab.cert_pem,
        "mac": mac(code, "lab", own, server_sha, lab.node, lab.lab_mode),
    }


def accept_lab(
    code: str, message: dict[str, Any], inference: Identity
) -> tuple[Peer, dict[str, Any]]:
    """Check the lab node's message on the inference node and build the answer.

    Args:
        code: Normalized code.
        message: The lab node's message.
        inference: The inference node.

    Returns:
        The lab node as peer and the answer to send.

    Raises:
        PairingError: On a wrong code, a certificate swapped in between, or a
            lab mode mismatch.
    """
    try:
        lab_cert = str(message["cert"])
        lab_sha = cert_sha256(lab_cert)
        node, lab_mode = str(message["node"]), str(message["lab_mode"])
    except (KeyError, ValueError, TypeError) as exc:
        raise PairingError(f"malformed pairing request: {exc}") from exc
    own_sha = cert_sha256(inference.cert_pem)
    expected = mac(code, "lab", lab_sha, own_sha, node, lab_mode)
    if message.get("protocol") != PROTOCOL or not hmac.compare_digest(
        expected, str(message.get("mac", ""))
    ):
        raise PairingError("wrong code, or the connection was intercepted")
    if lab_mode != inference.lab_mode:
        raise PairingError(f"lab mode {lab_mode} cannot pair with {inference.lab_mode}")
    answer = {
        "protocol": PROTOCOL,
        "node": inference.node,
        "lab_mode": inference.lab_mode,
        "cert": inference.cert_pem,
        "mac": mac(code, "inference", own_sha, lab_sha, inference.node, inference.lab_mode),
    }
    return Peer(node, lab_mode, lab_cert), answer


def accept_inference(code: str, answer: dict[str, Any], lab: Identity, server_sha: str) -> Peer:
    """Check the inference node's answer on the lab node.

    Args:
        code: Normalized code.
        answer: The answer.
        lab: The lab node.
        server_sha: Fingerprint of the certificate the TLS server presented.

    Returns:
        The inference node as peer.

    Raises:
        PairingError: If the answer does not prove the code, names another
            certificate than the TLS server used, or has another lab mode.
    """
    try:
        cert = str(answer["cert"])
        sha = cert_sha256(cert)
        node, lab_mode = str(answer["node"]), str(answer["lab_mode"])
    except (KeyError, ValueError, TypeError) as exc:
        raise PairingError(f"malformed pairing answer: {exc}") from exc
    if sha != server_sha:
        raise PairingError("the answer names another certificate than the TLS server used")
    expected = mac(code, "inference", sha, cert_sha256(lab.cert_pem), node, lab_mode)
    if answer.get("protocol") != PROTOCOL or not hmac.compare_digest(
        expected, str(answer.get("mac", ""))
    ):
        raise PairingError("the inference node did not prove the code")
    if lab_mode != lab.lab_mode:
        raise PairingError(f"lab mode {lab_mode} cannot pair with {lab.lab_mode}")
    return Peer(node, lab_mode, cert)


def _send(conn: Any, value: dict[str, Any]) -> None:  # noqa: ANN401 - socket or SSL socket
    conn.sendall(json.dumps(value).encode() + b"\n")


def _receive(conn: Any) -> dict[str, Any]:  # noqa: ANN401 - socket or SSL socket
    data = b""
    while not data.endswith(b"\n"):
        chunk = conn.recv(4096)
        if not chunk:
            break
        data += chunk
        if len(data) > MAX_MESSAGE:
            raise PairingError("pairing message too large")
    try:
        value = json.loads(data)
    except ValueError as exc:
        raise PairingError("malformed pairing message") from exc
    if not isinstance(value, dict):
        raise PairingError("malformed pairing message")
    return value


def serve_once(  # noqa: PLR0917 - one value per setting
    code: str,
    inference: Identity,
    cert_file: str,
    key_file: str,
    address: str,
    port: int,
    timeout: float,
    log: Callable[[str], None] = lambda _m: None,
) -> Peer:
    """Wait for the lab node on the inference node (TLS, no client certificate).

    Args:
        code: Normalized code.
        inference: This node.
        cert_file: This node's certificate file.
        key_file: Its key file.
        address: Tailnet address to listen on.
        port: Port.
        timeout: Seconds to wait in total.
        log: Progress messages.

    Returns:
        The paired lab node.

    Raises:
        PairingError: After MAX_ATTEMPTS failed attempts or the timeout.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.load_cert_chain(cert_file, key_file)
    failures = 0
    with socket.create_server((address, port)) as server:
        server.settimeout(timeout)
        while failures < MAX_ATTEMPTS:
            try:
                raw, peer_address = server.accept()
            except socket.timeout as exc:
                raise PairingError("no lab node connected in time") from exc
            raw.settimeout(30)
            try:
                with context.wrap_socket(raw, server_side=True) as conn:
                    message = _receive(conn)
                    try:
                        peer, answer = accept_lab(code, message, inference)
                    except PairingError as exc:
                        failures += 1
                        log(f"refused {peer_address[0]}: {exc}")
                        _send(conn, {"protocol": PROTOCOL, "error": "refused"})
                        continue
                    _send(conn, answer)
                    return dataclasses.replace(peer, address=str(peer_address[0]))
            except (OSError, ssl.SSLError) as exc:
                failures += 1
                log(f"connection from {peer_address[0]} failed: {exc}")
    raise PairingError(f"{MAX_ATTEMPTS} failed attempts; start pairing again with a new code")


def connect(code: str, lab: Identity, host: str, port: int, timeout: float = 30.0) -> Peer:
    """Pair with an inference node from the lab node.

    The TLS server's certificate is not verified by a CA (both are
    self-signed); the HMACs bind it to the code instead.

    Args:
        code: Normalized code.
        lab: This node.
        host: Tailnet name of the inference node.
        port: Its control port.
        timeout: Seconds for connecting and answering.

    Returns:
        The paired inference node.

    Raises:
        PairingError: If the inference node refuses or does not prove the code.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE  # the HMAC over the fingerprint authenticates the server
    try:
        with (
            socket.create_connection((host, port), timeout=timeout) as raw,
            context.wrap_socket(raw, server_hostname=host) as conn,
        ):
            der = conn.getpeercert(binary_form=True)
            if der is None:
                raise PairingError("the inference node presented no certificate")
            server_sha = hashlib.sha256(der).hexdigest()
            address = str(conn.getpeername()[0])
            _send(conn, lab_hello(code, lab, server_sha))
            answer = _receive(conn)
    except (OSError, ssl.SSLError) as exc:
        raise PairingError(f"cannot reach {host}:{port}: {exc}") from exc
    if "error" in answer:
        raise PairingError("the inference node refused the code")
    peer = accept_inference(code, answer, lab, server_sha)
    return dataclasses.replace(peer, address=address)
