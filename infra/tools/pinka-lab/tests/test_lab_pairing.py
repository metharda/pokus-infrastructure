"""Tests for pairing a lab node with an inference node."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import unittest
from typing import Any

from labfakes import FakeSystem, lab

from pinka_lab import commands, pairing


def make_cert(directory: str, name: str) -> tuple[str, str, str]:
    key = os.path.join(directory, f"{name}.key")
    cert = os.path.join(directory, f"{name}.crt")
    subprocess.run(  # noqa: S603 - fixed argv
        [
            shutil.which("openssl") or "openssl",
            "req",
            "-x509",
            "-newkey",
            "ec",
            "-pkeyopt",
            "ec_paramgen_curve:P-256",
            "-nodes",
            "-keyout",
            key,
            "-out",
            cert,
            "-days",
            "1",
            "-subj",
            f"/CN={name}",
        ],
        check=True,
        capture_output=True,
    )
    with open(cert, encoding="ascii") as handle:
        pem = handle.read()
    return cert, key, pem


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


@unittest.skipUnless(shutil.which("openssl"), "needs the openssl command to make certificates")
class PairingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.inf_cert, self.inf_key, inf_pem = make_cert(d, "pinka-gpu")
        self.lab_cert, self.lab_key, lab_pem = make_cert(d, "pinka-lab")
        self.mallory_cert, self.mallory_key, mallory_pem = make_cert(d, "mallory")
        self.inference = pairing.Identity("pinka-gpu", "production", inf_pem)
        self.lab = pairing.Identity("pinka-lab", "production", lab_pem)
        self.mallory = pairing.Identity("pinka-gpu", "production", mallory_pem)
        self.code = pairing.normalize(pairing.new_code())

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_codes(self) -> None:
        code = pairing.new_code()
        self.assertRegex(code, r"^[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}$")
        self.assertEqual(pairing.normalize(code.lower().replace("-", " ")), code.replace("-", ""))
        for bad in ("short", "O" * 12, "ABCD-EFGH-JKL1"):
            with self.assertRaises(pairing.PairingError):
                pairing.normalize(bad)

    def exchange(
        self, server: pairing.Identity, code_lab: str, lab_identity: Any = None
    ) -> pairing.Peer:
        lab_identity = lab_identity or self.lab
        server_sha = pairing.cert_sha256(server.cert_pem)
        hello = pairing.lab_hello(code_lab, lab_identity, server_sha)
        peer, answer = pairing.accept_lab(self.code, hello, self.inference)
        self.assertEqual(peer.cert_pem, lab_identity.cert_pem)
        return pairing.accept_inference(code_lab, answer, lab_identity, server_sha)

    def test_messages(self) -> None:
        peer = self.exchange(self.inference, self.code)
        self.assertEqual((peer.node, peer.lab_mode), ("pinka-gpu", "production"))
        record = peer.record("2026-01-01T12:00:00Z")
        self.assertEqual(record["cert_sha256"], pairing.cert_sha256(self.inference.cert_pem))

    def test_wrong_code_is_refused(self) -> None:
        other = pairing.normalize(pairing.new_code())
        with self.assertRaisesRegex(pairing.PairingError, "wrong code"):
            self.exchange(self.inference, other)

    def test_interception_is_detected(self) -> None:
        # The lab node talked TLS to someone else's certificate: its HMAC binds that fingerprint.
        with self.assertRaisesRegex(pairing.PairingError, "intercepted"):
            self.exchange(self.mallory, self.code)

    def test_lab_modes_must_match(self) -> None:
        staging = pairing.Identity("pinka-lab", "staging", self.lab.cert_pem)
        with self.assertRaisesRegex(pairing.PairingError, "cannot pair"):
            self.exchange(self.inference, self.code, staging)

    def test_answer_with_another_certificate(self) -> None:
        server_sha = pairing.cert_sha256(self.inference.cert_pem)
        hello = pairing.lab_hello(self.code, self.lab, server_sha)
        _, answer = pairing.accept_lab(self.code, hello, self.inference)
        answer["cert"] = self.mallory.cert_pem
        with self.assertRaisesRegex(pairing.PairingError, "another certificate"):
            pairing.accept_inference(self.code, answer, self.lab, server_sha)
        with self.assertRaisesRegex(pairing.PairingError, "malformed"):
            pairing.accept_lab(self.code, {"node": "x"}, self.inference)

    def run_server(self, port: int, result: dict[str, Any], **kwargs: Any) -> threading.Thread:
        def target() -> None:
            try:
                result["peer"] = pairing.serve_once(
                    self.code,
                    self.inference,
                    self.inf_cert,
                    self.inf_key,
                    "127.0.0.1",
                    port,
                    kwargs.get("timeout", 10.0),
                )
            except pairing.PairingError as exc:
                result["error"] = str(exc)

        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        return thread

    def wait_listening(self, port: int) -> None:
        for _ in range(100):
            with contextlib.suppress(OSError), socket.create_connection(("127.0.0.1", port), 0.1):
                return
            threading.Event().wait(0.05)

    def test_tls_round_trip(self) -> None:
        port = free_port()
        result: dict[str, Any] = {}
        thread = self.run_server(port, result)
        self.wait_listening(port)
        # The probe above counted as a failed attempt; a real lab node follows.
        peer = pairing.connect(self.code, self.lab, "127.0.0.1", port)
        thread.join(10)
        self.assertEqual(peer.cert_pem, self.inference.cert_pem)
        self.assertEqual(result["peer"].cert_pem, self.lab.cert_pem)

    def test_server_gives_up_after_failures(self) -> None:
        port = free_port()
        result: dict[str, Any] = {}
        thread = self.run_server(port, result)
        self.wait_listening(port)
        wrong = pairing.normalize(pairing.new_code())
        for _ in range(pairing.MAX_ATTEMPTS):
            with contextlib.suppress(pairing.PairingError):
                pairing.connect(wrong, self.lab, "127.0.0.1", port, timeout=5)
        thread.join(10)
        self.assertIn("failed attempts", result.get("error", ""))

    def test_connect_to_nothing(self) -> None:
        with self.assertRaisesRegex(pairing.PairingError, "cannot reach"):
            pairing.connect(self.code, self.lab, "127.0.0.1", free_port(), timeout=2)


class PairCommandsTest(unittest.TestCase):
    CERT = "-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n"
    PEER_CERT = "-----BEGIN CERTIFICATE-----\nBBBB\n-----END CERTIFICATE-----"

    def system(self, euid: int = 0) -> FakeSystem:
        return FakeSystem(
            files={
                "/etc/pinka/inference/self.crt": self.CERT,
                "/etc/pinka/inference/control.json": json.dumps(
                    {"listen": "100.64.0.1", "control_port": 18710}
                ),
            },
            commands={"systemctl *": (0, "")},
            euid=euid,
        )

    def peer(self) -> Any:
        class FakePeer:
            node = "pinka-lab"
            lab_mode = "production"
            cert_pem = PairCommandsTest.PEER_CERT

            def record(self, paired_at: str) -> dict[str, Any]:
                return {"node": self.node, "paired_at": paired_at}

        return FakePeer()

    def run_quiet(self, call: Any) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = call()
        return code, out.getvalue()

    def test_show_on_the_inference_node(self) -> None:
        system = self.system()
        seen: dict[str, Any] = {}

        def serve(*args: Any, log: Any) -> Any:
            code, me, cert, key, address, port, timeout = args
            seen.update(code=code, node=me.node, address=address, port=port, timeout=timeout)
            seen.update(cert=cert, key=key, log=log)
            return self.peer()

        config = lab(node_role="inference")
        code, out = self.run_quiet(lambda: commands.pair_show(system, config, 30, serve=serve))
        self.assertEqual(code, 0)
        self.assertEqual(seen["address"], "100.64.0.1")
        self.assertEqual(seen["port"], 18710)
        self.assertEqual(len(seen["code"]), 12)
        self.assertEqual(seen["cert"], "/etc/pinka/inference/self.crt")
        self.assertEqual(seen["key"], "/etc/pinka/inference/self.key")
        self.assertIn("Pairing code:", out)
        self.assertIn("inference_lab_node: pinka-lab", out)
        self.assertEqual(
            system.written["/etc/pinka/inference/peer.crt"], (self.PEER_CERT + "\n", 0o644)
        )
        self.assertIn("/etc/pinka/inference/peer.json", system.written)
        self.assertEqual(
            [c[:2] for c in system.calls],
            [["systemctl", "stop"], ["systemctl", "restart"]],
        )

    def test_connect_on_the_lab_node(self) -> None:
        system = self.system()
        seen: dict[str, Any] = {}

        def connect(code: str, me: Any, host: str, port: int) -> Any:
            seen.update(code=code, host=host, port=port)
            return self.peer()

        config = lab(node_role="lab")
        code, out = self.run_quiet(
            lambda: commands.pair_connect(
                system, config, "pinka-gpu", "abcd-efgh-jkmn", connect=connect
            )
        )
        self.assertEqual(code, 0)
        self.assertEqual(seen, {"code": "ABCDEFGHJKMN", "host": "pinka-gpu", "port": 18710})
        self.assertIn("inference_remote_node: pinka-lab", out)
        self.assertEqual(
            system.written["/etc/pinka/inference/peer.crt"], (self.PEER_CERT + "\n", 0o644)
        )

    def test_refusals(self) -> None:
        with self.assertRaisesRegex(commands.CommandError, "needs root"):
            commands.pair_show(self.system(euid=1000), lab(node_role="inference"), 1)
        with self.assertRaisesRegex(commands.CommandError, "runs on the inference node"):
            commands.pair_show(self.system(), lab(node_role="lab"), 1)
        with self.assertRaisesRegex(commands.CommandError, "runs on a lab node"):
            commands.pair_connect(self.system(), lab(node_role="full"), "x", "y")
        with self.assertRaisesRegex(commands.CommandError, "not a tailnet node name"):
            commands.pair_connect(self.system(), lab(node_role="lab"), "-bad", "y")
        with self.assertRaisesRegex(commands.CommandError, "not a pairing code"):
            commands.pair_connect(self.system(), lab(node_role="lab"), "pinka-gpu", "short")
        empty = FakeSystem()
        with self.assertRaisesRegex(commands.CommandError, "self.crt is missing"):
            commands.pair_connect(empty, lab(node_role="lab"), "pinka-gpu", "ABCD-EFGH-JKMN")


if __name__ == "__main__":
    unittest.main()
