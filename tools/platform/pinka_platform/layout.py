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
# Per-service configuration, checked against the release's config schemas.
CONFIG_DIR = f"{PLATFORM_ETC_DIR}/config"
# What infra rendered for the active platform (infra-internal, not contract).
RENDERED_FILE = f"{PLATFORM_ETC_DIR}/rendered.json"
SECRETS_DIR = f"{ETC}/secrets"
POSTGRES_PASSWORD_DIR = f"{SECRETS_DIR}/postgres"
POSTGRES_READER_CREDENTIAL = f"{ETC}/postgres-reader/pgpass"
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
SYSTEMD_DIR = "/etc/systemd/system"
CLI_BIN_DIR = "/usr/local/bin"
# The researchers' CLI: a wrapper per platform command, linked into CLI_BIN_DIR.
CLI_WRAPPER_DIR = "/usr/local/lib/pinka/platform/cli"
LIBEXEC = "/usr/local/libexec/pinka"
SOCKET_ACL_HELPER = f"{LIBEXEC}/socket-acl"
# The deployment tool; hooks run through its hook-exec wrapper (§12.9).
PLATFORM_TOOL = f"{LIBEXEC}/pinka-platform"
# Root-only (0700): systemd opens a hook unit's stdout here as root before
# the unit drops to its account; the caller reads the result and deletes it.
HOOK_RESULTS_DIR = f"{RUN}/hooks"
SERVICE_HOME_DIR = VAR_LIB
# The UIDs of platform accounts (§7.9 option b, ADR-0016): unused in systemd's
# allocation table (UIDS-GIDS.md, v255); the telemetry guard drops their
# connections to the telemetry backends. Equal to pinka_platform_uids.
PLATFORM_UID_MIN = 60578
PLATFORM_UID_MAX = 61183
# Paths no platform unit may reach (BUILD_PROMPT §7.14 "infra internals").
INFRA_INTERNALS = (
    f"{VAR_LIB}/git",
    f"{VAR_LIB}/staging",
    "/var/lib/incus",
    "/var/lib/docker",
    "/run/docker.sock",
    "/var/run/docker.sock",
    f"{SECRETS_DIR}/containers",
    f"{SECRETS_DIR}/backup",
    f"{ETC}/inference",
    f"{SRV}/backups",
)
# Sockets only the controller may reach among the platform's units.
CONTROLLER_SOCKETS = (GPU_SOCKET, RUN_NETWORK_SOCKET)


def release_current(platform: str) -> str:
    """The active release of a platform through its ``current`` link.

    Args:
        platform: Platform name.

    Returns:
        ``/srv/pinka/platforms/<platform>/releases/current``.
    """
    return f"{PLATFORMS_DIR}/{platform}/releases/current"


def state_dir(platform: str, service: str, directory: str) -> str:
    """A state directory the manifest declares.

    Args:
        platform: Platform name.
        service: Declaring service.
        directory: Directory name.

    Returns:
        ``/srv/pinka/platforms/<platform>/state/<service>/<directory>``.
    """
    return f"{PLATFORMS_DIR}/{platform}/state/{service}/{directory}"


def config_file(service: str) -> str:
    """A service's configuration file.

    Args:
        service: Service name.

    Returns:
        ``/etc/pinka/platform/config/<service>.json``.
    """
    return f"{CONFIG_DIR}/{service}.json"


# Each service's own runtime directory, apart from its sockets (V4, owner
# decision with the "commit" for 3d.3): systemd creates it at start
# (RuntimeDirectory=, 0700, owned by the service's account) and removes it
# at stop; infra puts nothing in it.
PLATFORM_RUNTIME_DIR = f"{RUN}/platform-runtime"


def runtime_dir(service: str) -> str:
    """The runtime directory of a platform service.

    Args:
        service: Service name.

    Returns:
        ``/run/pinka/platform-runtime/<service>``.
    """
    return f"{PLATFORM_RUNTIME_DIR}/{service}"


def socket_path(service: str, socket: str) -> str:
    """Path of a platform socket.

    Args:
        service: Serving service.
        socket: Socket name.

    Returns:
        ``/run/pinka/platform/<service>/<socket>.sock``.
    """
    return f"{PLATFORM_RUN_DIR}/{service}/{socket}.sock"
