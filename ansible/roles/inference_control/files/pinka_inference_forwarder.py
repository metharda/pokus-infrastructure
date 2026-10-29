#!/opt/pinka/python/bin/python3 -I
# Managed by pinka (ansible/roles/inference_control/files/pinka_inference_forwarder.py). Local changes will be overwritten.
"""The inference forwarder of a lab node.

Installed as ``/usr/local/libexec/pinka/inference-forwarder`` by the
inference_control role and run as root by ``pinka-inference-forwarder``:
it alone holds the lab node's mutual-TLS client certificate
(``/etc/pinka/inference/``). It listens on ``127.0.0.1`` on the vLLM port
and the control port, the same addresses as vLLM and the control API on a
full node, and passes every connection through to the paired inference
node's control endpoint over the tailnet: TLS 1.3, this node's certificate as
client certificate, and only the paired node's own certificate accepted
(``peer.crt``, compared by its SHA-256). Platform services handle no TLS and
no keys and stay on the loopback; who among them may connect is the
inference guard's business (ADR-0016). Standard library only.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import ssl
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CHUNK = 65536
CONNECT_TIMEOUT = 15.0


@dataclass(frozen=True)
class Config:
    """Settings from ``/etc/pinka/inference/forwarder.json``.

    Attributes:
        remote: The paired inference node's tailnet name.
        listen: Loopback address to listen on.
        api_port: The vLLM API's port (here and on the inference node).
        control_port: The control API's port (here and on the inference node).
        cert: This node's certificate (the client certificate).
        key: This node's private key.
        peer: The paired inference node's certificate (the only accepted server).
    """

    remote: str
    listen: str = "127.0.0.1"
    api_port: int = 18704
    control_port: int = 18710
    cert: Path = Path("/etc/pinka/inference/self.crt")
    key: Path = Path("/etc/pinka/inference/self.key")
    peer: Path = Path("/etc/pinka/inference/peer.crt")

    @classmethod
    def load(cls, path: Path) -> Config:
        """Read the configuration file.

        Args:
            path: JSON file.

        Returns:
            The configuration.
        """
        data = json.loads(path.read_text(encoding="utf-8"))
        values: dict[str, Any] = {
            k: Path(v) if k in {"cert", "key", "peer"} else v for k, v in data.items()
        }
        return cls(**values)


def peer_fingerprint(peer: Path) -> str:
    """SHA-256 of the paired certificate's DER encoding.

    Args:
        peer: PEM certificate file.

    Returns:
        Lowercase hex digest.
    """
    der = ssl.PEM_cert_to_DER_cert(peer.read_text(encoding="ascii"))
    return hashlib.sha256(der).hexdigest()


def client_context(config: Config) -> ssl.SSLContext:
    """TLS 1.3 client context: this node's certificate, only the paired node trusted.

    The paired certificate is self-signed and pinned, so it is its own trust
    anchor; the name is not checked because the fingerprint is.

    Args:
        config: Settings.

    Returns:
        The context.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.check_hostname = False
    context.verify_mode = ssl.CERT_REQUIRED
    context.load_cert_chain(config.cert, config.key)
    context.load_verify_locations(cafile=str(config.peer))
    return context


def is_peer(writer: asyncio.StreamWriter, fingerprint: str) -> bool:
    """Whether the TLS server presented exactly the paired certificate.

    Args:
        writer: The upstream connection.
        fingerprint: Expected SHA-256 of the server certificate.

    Returns:
        True for the paired inference node.
    """
    ssl_object = writer.get_extra_info("ssl_object")
    der = ssl_object.getpeercert(binary_form=True) if ssl_object else None
    return der is not None and hashlib.sha256(der).hexdigest() == fingerprint


async def _pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(CHUNK):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.IncompleteReadError, ssl.SSLError):
        pass
    finally:
        with contextlib.suppress(Exception):
            writer.close()


async def forward(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    config: Config,
    context: ssl.SSLContext,
    fingerprint: str,
    port: int,
) -> None:
    """Pass one loopback connection through to the paired node.

    Args:
        reader: Client stream.
        writer: Client stream.
        config: Settings.
        context: The client TLS context.
        fingerprint: Paired certificate.
        port: The same port on the inference node.
    """
    try:
        up_reader, up_writer = await asyncio.wait_for(
            asyncio.open_connection(config.remote, port, ssl=context, server_hostname=None),
            CONNECT_TIMEOUT,
        )
    except (OSError, ssl.SSLError, TimeoutError):
        writer.close()
        return
    if not is_peer(up_writer, fingerprint):
        up_writer.close()
        writer.close()
        return
    await asyncio.gather(_pump(reader, up_writer), _pump(up_reader, writer))


async def serve(config: Config) -> None:
    """Run both listeners until the process is stopped.

    Args:
        config: Settings.
    """
    context = client_context(config)
    fingerprint = peer_fingerprint(config.peer)
    servers = []
    for port in (config.api_port, config.control_port):

        async def on_connect(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter, port: int = port
        ) -> None:
            await forward(
                reader,
                writer,
                config=config,
                context=context,
                fingerprint=fingerprint,
                port=port,
            )

        servers.append(await asyncio.start_server(on_connect, config.listen, port))
    async with contextlib.AsyncExitStack() as stack:
        for server in servers:
            await stack.enter_async_context(server)
        await asyncio.gather(*(s.serve_forever() for s in servers))


def main(argv: list[str]) -> int:
    """Start the forwarder.

    Args:
        argv: Arguments without the program name.

    Returns:
        Exit status (only on errors).
    """
    parser = argparse.ArgumentParser(
        prog="inference-forwarder", description=__doc__.splitlines()[0]
    )
    parser.add_argument("--config", type=Path, default=Path("/etc/pinka/inference/forwarder.json"))
    args = parser.parse_args(argv)
    try:
        config = Config.load(args.config)
        if not config.peer.is_file():
            sys.stderr.write("inference-forwarder: not paired yet (no peer.crt)\n")
            return 1
        asyncio.run(serve(config))
    except (OSError, ValueError, TypeError, ssl.SSLError) as exc:
        sys.stderr.write(f"inference-forwarder: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
