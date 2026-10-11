"""systemd units of a platform: its target, services and sockets (§7.14, §12.3).

Every service unit carries the hardening baseline of §7.14, rendered from the
profile; a manifest can add only its state directories, limits within infra's
caps and the one fixed relaxation. Sockets are socket units: infra creates
each socket with its owner, client group and mode, adds the ACL entries of
the human classes, and hands it to the service by socket activation
(``LISTEN_FDS``; ``LISTEN_FDNAMES`` carries the socket names). Services never
bind their sockets themselves.

Deviation V4 from the baseline (approved with the "commit" for 3d.3): the
socket directory ``/run/pinka/platform/<service>/`` is no ``RuntimeDirectory=``.
systemd removes a runtime directory at stop and chowns it recursively to the
service account at start, which would delete the sockets or reset their
client groups; the socket units create it (root, 0755) instead. Each service
gets its own runtime directory apart from it, with no sockets in it:
``RuntimeDirectory=pinka/platform-runtime/<service>`` (0700,
``$RUNTIME_DIRECTORY``).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from pinka_platform import layout, names
from pinka_platform.contract import Contract

HEADER = "# Managed by pinka (platform {platform}, contract v2). Local changes will be overwritten."
TARGET = "pinka-platform.target"
SLICE = "pinka-services.slice"
HEALTH_GATE = "pinka-health-gate.service"
# Infra's agent OTLP socket (§7.9), bound into the agent's file system.
AGENT_OTLP_UNIT = "pinka-otlp-agent.socket"
# The units of the facilities a manifest may require (ordering only).
FACILITY_UNITS = {
    "postgres": "pinka-core.service",
    "opa": "pinka-core.service",
    "telemetry": "pinka-observability.service",
    "inference": "pinka-inference.service",
}
_PLAIN = re.compile(r"^[A-Za-z0-9_./:=@+,-]+$")
# The fixed part of the hardening baseline (§7.14), in the order of the spec.
BASELINE = (
    ("NoNewPrivileges", "yes"),
    ("ProtectSystem", "strict"),
    ("ProtectHome", "yes"),
    ("PrivateTmp", "yes"),
    ("PrivateDevices", "yes"),
    ("ProtectKernelTunables", "yes"),
    ("ProtectKernelModules", "yes"),
    ("ProtectControlGroups", "yes"),
    ("RestrictSUIDSGID", "yes"),
    ("LockPersonality", "yes"),
    ("RestrictRealtime", "yes"),
    ("CapabilityBoundingSet", ""),
    ("AmbientCapabilities", ""),
    ("UMask", "0077"),
    ("Slice", SLICE),
    ("ProtectClock", "yes"),
    ("ProtectHostname", "yes"),
    ("ProtectKernelLogs", "yes"),
    ("ProtectProc", "invisible"),
    ("ProcSubset", "pid"),
    ("RestrictNamespaces", "yes"),
    ("SystemCallArchitectures", "native"),
    ("SystemCallFilter", "@system-service"),
    ("IPAddressDeny", "any"),
    ("SocketBindDeny", "any"),
)
# The lab directories the agent sees only through its allowlist (§7.14).
AGENT_HIDDEN = (layout.SRV, layout.ETC, layout.VAR_LIB, layout.RUN)


# Control characters are written as \xNN: everything below a space, and DEL.
_SPACE = 0x20
_DELETE = 0x7F


def quote(arg: str) -> str:
    r"""Quote one word of a systemd command line.

    ``%`` and ``$`` are escaped so systemd expands neither specifiers nor
    environment variables; control characters become ``\xNN``. The
    executable is always an absolute path, so no word can be read as one of
    systemd's executable prefixes (``-``, ``@``, ``+``, ...).

    Args:
        arg: The word.

    Returns:
        The word as systemd reads it back.
    """
    if _PLAIN.match(arg):
        return arg
    out = []
    for char in arg:
        if char in '\\"':
            out.append("\\" + char)
        elif char == "%":
            out.append("%%")
        elif char == "$":
            out.append("$$")
        elif ord(char) < _SPACE or ord(char) == _DELETE:
            out.append(f"\\x{ord(char):02x}")
        else:
            out.append(char)
    return '"' + "".join(out) + '"'


def command_line(words: Sequence[str]) -> str:
    """Join words into a systemd command line.

    Args:
        words: Executable (an absolute path) and arguments.

    Returns:
        The quoted command line.

    Raises:
        ValueError: The executable is not an absolute path.
    """
    if not words or not words[0].startswith("/"):
        raise ValueError("the executable must be an absolute path")
    return " ".join(quote(w) for w in words)


def _unit_text(platform: str, sections: Sequence[tuple[str, Iterable[tuple[str, str]]]]) -> str:
    lines = [HEADER.format(platform=platform)]
    for title, entries in sections:
        lines += ["", f"[{title}]"]
        lines += [f"{key}={value}" for key, value in entries]
    return "\n".join(lines) + "\n"


def service_unit(platform: str, service: str) -> str:
    """Name of a service's unit."""
    return f"{names.account(platform, service)}.service"


def socket_unit(platform: str, service: str, socket: str) -> str:
    """Name of a socket's unit."""
    return f"{names.client_group(platform, service, socket)}.socket"


def target_unit(platform: str) -> str:
    """``pinka-platform.target``: the platform's services, after the health gate.

    Args:
        platform: Platform name.

    Returns:
        Unit text.
    """
    return _unit_text(
        platform,
        [
            (
                "Unit",
                [
                    ("Description", f"Platform {platform} of the lab"),
                    ("Requires", HEALTH_GATE),
                    ("After", HEALTH_GATE),
                    ("PartOf", "pinka-lab.target"),
                ],
            ),
            ("Install", [("WantedBy", "pinka-lab.target")]),
        ],
    )


def human_groups(socket: Mapping[str, Any], contract: Contract) -> list[str]:
    """The groups of the human classes among a socket's clients (its ACL entries).

    Args:
        socket: The manifest's socket entry.
        contract: The contract.

    Returns:
        Group names (``pinka``, ``pinka-admin``).
    """
    return [
        contract.human_classes[c]["group"] for c in socket["clients"] if c in contract.human_classes
    ]


def socket_unit_text(platform: str, socket: Mapping[str, Any], contract: Contract) -> str:
    """A socket unit (§12.3).

    The socket is owned by the serving account, its group is the client
    group, mode 0660; the human classes get ACL entries from the helper,
    which runs as root after the socket is bound.

    Args:
        platform: Platform name.
        socket: The manifest's socket entry.
        contract: The contract.

    Returns:
        Unit text.
    """
    service, name = socket["service"], socket["name"]
    path = layout.socket_path(service, name)
    entries = [
        ("ListenStream", path),
        ("SocketUser", names.account(platform, service)),
        ("SocketGroup", names.client_group(platform, service, name)),
        ("SocketMode", "0660"),
        ("DirectoryMode", "0755"),
        ("RemoveOnStop", "yes"),
        ("FileDescriptorName", name),
        ("Service", service_unit(platform, service)),
    ]
    humans = human_groups(socket, contract)
    if humans:
        entries.append(("ExecStartPost", command_line([layout.SOCKET_ACL_HELPER, path, *humans])))
    agent = " for the agent" if socket.get("untrusted_clients") else ""
    return _unit_text(
        platform,
        [
            (
                "Unit",
                [
                    ("Description", f"Platform {platform}: socket {name} of {service}{agent}"),
                    ("PartOf", TARGET),
                ],
            ),
            ("Socket", entries),
            ("Install", [("WantedBy", TARGET)]),
        ],
    )


def _limits(service: Mapping[str, Any], contract: Contract) -> tuple[str, str]:
    limits = service.get("limits", {})
    memory = str(limits.get("memory_max", contract.defaults["service_memory_max"]))
    tasks = str(limits.get("tasks_max", contract.defaults["service_tasks_max"]))
    return memory, tasks


def _own_sockets(manifest: Mapping[str, Any], service: str) -> list[Mapping[str, Any]]:
    return [s for s in manifest.get("sockets", []) if s["service"] == service]


def _agent_sockets(manifest: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [s for s in manifest.get("sockets", []) if s.get("untrusted_clients")]


def _internals(profile: str, contract: Contract, platform: str) -> list[str]:
    grant = contract.profile(profile)
    hidden = list(layout.INFRA_INTERNALS)
    if not grant.flag("gpu_arbiter"):
        hidden.append(layout.GPU_SOCKET)
    if not grant.data.get("run_network_helper"):
        hidden.append(layout.RUN_NETWORK_SOCKET)
    if grant.data["restricted_data"] != "read":
        hidden.append(f"{layout.RESTRICTED_DIR}")
    if not grant.flag("database_role"):
        hidden += [layout.POSTGRES_SOCKET_DIR, layout.POSTGRES_READER_SOCKET_DIR]
    if not grant.data["incus_projects"]:
        hidden.append(layout.INCUS_CERT_DIR)
    if profile != "controller":
        hidden.append(f"{layout.SIGNING_DIR}")
    return hidden


def service_sections(
    manifest: Mapping[str, Any],
    service: Mapping[str, Any],
    contract: Contract,
    accounts: Mapping[str, Mapping[str, Any]],
    ports: Mapping[str, int],
    *,
    release: str | None = None,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """The [Unit] and [Service] entries of a service with the baseline of §7.14.

    The hook units reuse the [Service] entries of the account they run as
    (:mod:`pinka_platform.hooks`).

    Args:
        manifest: A valid manifest.
        service: The service's entry.
        contract: The contract.
        accounts: The rendered accounts by name (for the supplementary groups).
        ports: Effective ports (the inference broker's researcher endpoint).
        release: The release being deployed, resolved (``releases/<name>``):
            the only release an ``untrusted-agent`` sees and runs from; the
            other profiles run from the ``current`` link.

    Returns:
        ``(unit, service)`` entries in order.
    """
    p = manifest["name"]
    name, profile = service["name"], service["profile"]
    grant = contract.profile(profile)
    account = names.account(p, name)
    agent = profile == "untrusted-agent"
    # The agent never sees the current link, nor another published release.
    current = release if agent and release else layout.release_current(p)
    own = [socket_unit(p, name, s["name"]) for s in _own_sockets(manifest, name)]
    used = (
        [socket_unit(p, s["service"], s["name"]) for s in _agent_sockets(manifest)] if agent else []
    )
    after_services = [service_unit(p, a) for a in service.get("after", [])]
    facilities = sorted(
        {FACILITY_UNITS[f] for f in manifest.get("requires", []) if f in FACILITY_UNITS}
    )
    reconcile = [reconcile_unit(p)] if "reconcile" in manifest["hooks"] else []
    unit: list[tuple[str, str]] = [
        ("Description", f"Platform {p}: service {name} ({profile})"),
        ("PartOf", " ".join([TARGET, *used])),
        ("Requires", " ".join([HEALTH_GATE, *reconcile, *own, *used])),
        ("After", " ".join([HEALTH_GATE, *facilities, *reconcile, *own, *used, *after_services])),
    ]
    if agent:
        # Its telemetry socket exists before the agent's file system is set up.
        unit += [("Wants", AGENT_OTLP_UNIT), ("After", AGENT_OTLP_UNIT)]
    state = [layout.state_dir(p, name, d) for d in service.get("state", [])]
    config = [layout.config_file(name)] if "config" in service else []
    memory, tasks = _limits(service, contract)
    relaxations = set(service.get("relaxations", []))
    entries: list[tuple[str, str]] = [
        ("Type", "simple"),
        ("ExecStart", command_line([f"{current}/{service['start'][0]}", *service["start"][1:]])),
        ("WorkingDirectory", current),
        ("Restart", "on-failure"),
        ("RestartSec", "5"),
        ("User", account),
        ("Group", account),
        ("SupplementaryGroups", " ".join(accounts[account]["groups"])),
    ]
    for key, value in BASELINE:
        entries.append((key, value))
        if key == "RestrictRealtime":
            wx = "wx-memory" in relaxations and not agent
            entries.append(("MemoryDenyWriteExecute", "no" if wx else "yes"))
        if key == "IPAddressDeny" and grant.data["network"] == "loopback":
            entries.append(("IPAddressAllow", "localhost"))
        if key == "SocketBindDeny" and grant.data["listen_port"] is not None:
            entries.append(("SocketBindAllow", f"tcp:{ports['gateway_adhoc']}"))
    runtime = layout.runtime_dir(name)
    entries += [
        ("MemoryMax", memory),
        ("TasksMax", tasks),
        ("RestrictAddressFamilies", "AF_UNIX" if agent else "AF_UNIX AF_INET AF_INET6"),
        # Not the socket directory (V4): systemd would remove its sockets at
        # stop and reset their client groups at start.
        ("RuntimeDirectory", runtime.removeprefix("/run/")),
        ("RuntimeDirectoryMode", "0700"),
    ]
    if agent:
        sockets = [layout.socket_path(s["service"], s["name"]) for s in _agent_sockets(manifest)]
        entries += [
            ("PrivateNetwork", "yes"),
            ("TemporaryFileSystem", " ".join(f"{d}:ro" for d in AGENT_HIDDEN)),
            (
                "BindReadOnlyPaths",
                " ".join(
                    [
                        current,
                        *config,
                        *sockets,
                        f"-{layout.AGENT_OTLP_SOCKET}",
                    ]
                ),
            ),
        ]
        writable = [runtime, *state]
        entries += [("BindPaths", " ".join(writable)), ("ReadWritePaths", " ".join(writable))]
    else:
        if state:
            entries.append(("ReadWritePaths", " ".join(state)))
        entries.append(("ReadOnlyPaths", " ".join([current, *config])))
    # Infra's internals are hidden from every profile (§7.14), the agent
    # included: some lie outside the lab directories its empty file systems cover.
    entries.append(
        ("InaccessiblePaths", " ".join(f"-{path}" for path in _internals(profile, contract, p)))
    )
    return unit, entries


def service_unit_text(
    manifest: Mapping[str, Any],
    service: Mapping[str, Any],
    contract: Contract,
    accounts: Mapping[str, Mapping[str, Any]],
    ports: Mapping[str, int],
    *,
    release: str | None = None,
) -> str:
    """A service unit with the hardening baseline of §7.14.

    Args:
        manifest: A valid manifest.
        service: The service's entry.
        contract: The contract.
        accounts: The rendered accounts by name (for the supplementary groups).
        ports: Effective ports (the inference broker's researcher endpoint).
        release: The release being deployed, resolved (see :func:`service_sections`).

    Returns:
        Unit text.
    """
    unit, entries = service_sections(manifest, service, contract, accounts, ports, release=release)
    return _unit_text(
        manifest["name"],
        [("Unit", unit), ("Service", entries), ("Install", [("WantedBy", TARGET)])],
    )


def reconcile_unit(platform: str) -> str:
    """Name of the platform's reconcile unit (§12.9)."""
    return f"{platform}-reconcile.service"


# [Service] entries a hook does not take over from its account's service: how
# the service starts and restarts, and its runtime directory (systemd would
# remove the running service's directory when the hook stops).
HOOK_DROPPED = frozenset(
    {
        "Type",
        "ExecStart",
        "WorkingDirectory",
        "Restart",
        "RestartSec",
        "RuntimeDirectory",
        "RuntimeDirectoryMode",
    }
)


def hook_entries(
    service_entries: Sequence[tuple[str, str]], release: str, command: Sequence[str]
) -> list[tuple[str, str]]:
    """The [Service] entries of a hook run under a service's account and hardening.

    Args:
        service_entries: The account's service entries (:func:`service_sections`).
        release: The release directory the hook runs in.
        command: The full command (the hook wrapper and the release's command).

    Returns:
        Entries: the hardening of the service, the hook's command and directory.
    """
    kept = [(k, v) for k, v in service_entries if k not in HOOK_DROPPED]
    return [("ExecStart", command_line(command)), ("WorkingDirectory", release), *kept]


def reconcile_unit_text(
    manifest: Mapping[str, Any],
    contract: Contract,
    accounts: Mapping[str, Mapping[str, Any]],
    ports: Mapping[str, int],
) -> str:
    """The reconcile hook as a oneshot unit before every service (§12.9).

    It runs at every start of the platform under the controller's account and
    hardening, through the hook wrapper, which requires the hook's one JSON
    line with ``"ok": true``; when it fails, no service starts.

    Args:
        manifest: A valid manifest.
        contract: The contract.
        accounts: The rendered accounts by name.
        ports: Effective ports.

    Returns:
        Unit text.
    """
    p = manifest["name"]
    controller = next(s for s in manifest["services"] if s["profile"] == "controller")
    _, entries = service_sections(manifest, controller, contract, accounts, ports)
    hook = manifest["hooks"]["reconcile"]
    current = layout.release_current(p)
    command = [
        layout.PLATFORM_TOOL,
        "hook-exec",
        "--hook",
        "reconcile",
        "--",
        f"{current}/{hook['run'][0]}",
        *hook["run"][1:],
    ]
    facilities = sorted(
        {FACILITY_UNITS[f] for f in manifest.get("requires", []) if f in FACILITY_UNITS}
    )
    service_units = [service_unit(p, s["name"]) for s in manifest["services"]]
    unit = [
        ("Description", f"Platform {p}: the reconcile hook before the services"),
        ("PartOf", TARGET),
        ("Requires", HEALTH_GATE),
        ("After", " ".join([HEALTH_GATE, *facilities])),
        ("Before", " ".join(service_units)),
    ]
    service = [
        ("Type", "oneshot"),
        ("RemainAfterExit", "yes"),
        ("TimeoutStartSec", str(hook["timeout_seconds"])),
        *hook_entries(entries, current, command),
    ]
    return _unit_text(
        p, [("Unit", unit), ("Service", service), ("Install", [("WantedBy", TARGET)])]
    )


# [Service] entries that `systemctl show` prints as the unit file writes them
# (booleans as yes/no, the other values as strings; systemd v252 and v255).
# Doctor compares them per unit (systemd.hardening, §7.17).
SHOWN_AS_WRITTEN = frozenset(
    {
        "User",
        "Group",
        "Slice",
        "NoNewPrivileges",
        "ProtectSystem",
        "ProtectHome",
        "PrivateTmp",
        "PrivateDevices",
        "PrivateNetwork",
        "ProtectKernelTunables",
        "ProtectKernelModules",
        "ProtectControlGroups",
        "ProtectKernelLogs",
        "ProtectClock",
        "ProtectHostname",
        "ProtectProc",
        "ProcSubset",
        "RestrictSUIDSGID",
        "RestrictRealtime",
        "LockPersonality",
        "MemoryDenyWriteExecute",
    }
)


def hardening(
    manifest: Mapping[str, Any],
    contract: Contract,
    accounts: Mapping[str, Mapping[str, Any]],
    ports: Mapping[str, int],
    *,
    release: str | None = None,
) -> list[dict[str, Any]]:
    """What doctor expects of each service unit: the hardening of its profile (§7.17).

    Args:
        manifest: A valid manifest.
        contract: The contract.
        accounts: The rendered accounts by name.
        ports: Effective ports.
        release: The release being deployed, resolved (see :func:`service_sections`).

    Returns:
        Per service unit: its profile, the ``systemctl show`` values of
        :data:`SHOWN_AS_WRITTEN`, its address families and inaccessible paths;
        for the agent also the sockets its allowlist binds into its file
        system and the directories it writes (where it may create its own).
    """
    p = manifest["name"]
    found: list[dict[str, Any]] = []
    for service in manifest["services"]:
        _, entries = service_sections(manifest, service, contract, accounts, ports, release=release)
        written = dict(entries)
        entry: dict[str, Any] = {
            "unit": service_unit(p, service["name"]),
            "profile": service["profile"],
            "properties": {k: v for k, v in entries if k in SHOWN_AS_WRITTEN},
            "address_families": written["RestrictAddressFamilies"].split(),
            "inaccessible_paths": [x.lstrip("-") for x in written["InaccessiblePaths"].split()],
        }
        if service["profile"] == "untrusted-agent":
            entry["agent_view"] = {
                "release": written["WorkingDirectory"],
                "sockets": sorted(
                    [
                        *(
                            layout.socket_path(s["service"], s["name"])
                            for s in _agent_sockets(manifest)
                        ),
                        layout.AGENT_OTLP_SOCKET,
                    ]
                ),
                "own_dirs": written["ReadWritePaths"].split(),
            }
        found.append(entry)
    return found


def render(
    manifest: Mapping[str, Any],
    contract: Contract,
    accounts: Mapping[str, Mapping[str, Any]],
    ports: Mapping[str, int],
    *,
    release: str | None = None,
) -> dict[str, str]:
    """Every unit of a platform.

    Args:
        manifest: A valid manifest.
        contract: The contract.
        accounts: The rendered accounts by name.
        ports: Effective ports.
        release: The release being deployed, resolved (the agent's only release).

    Returns:
        Unit text by unit name: the target, the services, the sockets.
    """
    p = manifest["name"]
    out = {TARGET: target_unit(p)}
    if "reconcile" in manifest["hooks"]:
        out[reconcile_unit(p)] = reconcile_unit_text(manifest, contract, accounts, ports)
    for service in manifest["services"]:
        out[service_unit(p, service["name"])] = service_unit_text(
            manifest, service, contract, accounts, ports, release=release
        )
    for socket in manifest.get("sockets", []):
        out[socket_unit(p, socket["service"], socket["name"])] = socket_unit_text(
            p, socket, contract
        )
    return out
