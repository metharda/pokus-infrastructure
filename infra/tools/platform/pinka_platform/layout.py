"""Host paths of contract v2 (BUILD_PROMPT §12) that the validator and renderer use."""

from __future__ import annotations

SRV = "/srv/pinka"
ETC = "/etc/pinka"
VAR_LIB = "/var/lib/pinka"
RUN = "/run/pinka"

PLATFORMS_DIR = f"{SRV}/platforms"
RESTRICTED_DIR = f"{SRV}/restricted"
SIGNING_KEYS_PUBLIC_DIR = f"{SRV}/shared/signing-keys"
PLATFORM_VAR_DIR = f"{VAR_LIB}/platforms"
SESSIONS_DIR = f"{VAR_LIB}/sessions"
PLATFORM_ETC_DIR = f"{ETC}/platform"
SECRETS_DIR = f"{ETC}/secrets"
SIGNING_DIR = f"{ETC}/signing"
# In SIGNING_DIR/<platform>/: the private key (PEM PKCS#8) and its public key (PEM).
SIGNING_KEY_NAME = "signing.key"
SIGNING_PUB_NAME = "signing.pub"
INCUS_CERT_DIR = f"{ETC}/incus"
QA_TRUSTED_KEYS_DIR = f"{ETC}/qa/trusted-keys"
QA_ATTESTATIONS_DIR = f"{ETC}/qa/attestations"
PLATFORM_RUN_DIR = f"{RUN}/platform"
POSTGRES_SOCKET_DIR = f"{RUN}/postgres"
POSTGRES_READER_SOCKET_DIR = f"{RUN}/postgres-reader"
AGENT_OTLP_SOCKET = f"{RUN}/otlp/agent.sock"
QUERY_SOCKET = f"{RUN}/query.sock"
GPU_SOCKET = f"{RUN}/gpu.sock"
RUN_NETWORK_SOCKET = f"{RUN}/run-network.sock"
FINGERPRINT_COMMAND = ("pinka", "fingerprint", "--json")


def socket_path(service: str, socket: str) -> str:
    """Path of a platform socket.

    Args:
        service: Serving service.
        socket: Socket name.

    Returns:
        ``/run/pinka/platform/<service>/<socket>.sock``.
    """
    return f"{PLATFORM_RUN_DIR}/{service}/{socket}.sock"
