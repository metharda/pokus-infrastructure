"""The doctor checks (contracts.md).

Each check reads what it compares against from the lab description
(``/etc/pinka/lab.json``) and asks the host through :class:`System`. Checks
that concern a part the lab does not have (no GPU, no platform yet, another
provider) are skipped, not passed.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import json
import re
import ssl
import time
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from pinka_lab import commands, window
from pinka_lab.doctor import Check, Context, Outcome, failed, passed, skipped
from pinka_preflight.detect import access as access_detect
from pinka_preflight.detect import gpu
from pinka_preflight.detect.lab import LAB_SLICE, LAB_UNIT_PREFIX
from pinka_preflight.detect.timesync import detect_timesync
from pinka_preflight.profile_tool import differences
from pinka_preflight.runner import CommandResult, Runner

PSQL_SCRIPT = (
    'PGPASSWORD="$(cat /run/secrets/postgres-superuser)" exec psql --no-psqlrc '
    "--set=ON_ERROR_STOP=1 --host=/var/run/postgresql --username=postgres --dbname=postgres "
    '"$@"'
)
KEY_MODE = 0o600
# The zones the firewall role creates on firewalld hosts (tasks/firewalld.yml).
FIREWALLD_ZONES = ("pinka-tailnet", "pinka-run")
SQL_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
PROCESS_RE = re.compile(r'\(\("([^"]+)"')
PID_RE = re.compile(r"pid=([0-9]+)")
LOOPBACK_V4 = "127."
# What infra rendered for the active platform (pinka_platform, contract v2).
RENDERED_PLATFORM = "/etc/pinka/platform/rendered.json"
# The key the socket-ACL helper keeps in it.
SOCKET_ACL_FALLBACK = "socket_acl_fallback"
FALLBACK_MODE = 0o666
# The install record, written by the install_record role.
INSTALL_RECORD = "/var/lib/pinka/install-record.json"
WRITABLE_BY_OTHERS = 0o022
# ss shows a dual-stack socket on every address as "*"; preflight reads it as "::".
SS_ANY_ADDRESS = {"*": "::"}


# --- helpers ---------------------------------------------------------------------------


def _profile(ctx: Context) -> dict[str, Any]:
    if "profile" not in ctx.memo:
        path = str(ctx.config.get("paths", "host_profile", default="/etc/pinka/host-profile.json"))
        text = ctx.system.read_text(path)
        ctx.memo["profile"] = json.loads(text) if text else {}
    profile: dict[str, Any] = ctx.memo["profile"]
    return profile


def _install_record(ctx: Context) -> dict[str, Any] | None:
    """Read the install record once; None when it is missing or not a JSON object."""
    if "install_record" not in ctx.memo:
        path = str(ctx.config.get("paths", "install_record", default=INSTALL_RECORD))
        text = ctx.system.read_text(path)
        try:
            data = json.loads(text) if text else None
        except ValueError:
            data = None
        ctx.memo["install_record"] = data if isinstance(data, dict) else None
    record: dict[str, Any] | None = ctx.memo["install_record"]
    return record


def _adopted_tailscale(ctx: Context) -> dict[str, Any] | None:
    """What the install record noted of a Tailscale node logged in before pinka.

    None when pinka joined the node, and where the record is missing or legacy
    (pinka provisioned the host before the record existed and joined the node).
    """
    record = _install_record(ctx)
    if record is None or record.get("legacy"):
        return None
    if ((record.get("added") or {}).get("components") or {}).get("tailscale"):
        return None
    before = ((record.get("preexisting") or {}).get("components") or {}).get("tailscale") or {}
    return dict(before) if before.get("origin") == "pre-existing" else None


def _preexisting_access(ctx: Context) -> tuple[dict[str, Any], list[str]] | None:
    """The access to the host before pinka and what of it was closed with consent.

    Returns:
        The record's ``preexisting.access`` and ``added.closed_access``; None
        where the record is missing, legacy (it decides nothing) or older than
        the access it notes.
    """
    record = _install_record(ctx)
    if record is None or record.get("legacy"):
        return None
    access = (record.get("preexisting") or {}).get("access")
    if not isinstance(access, dict) or not access:
        return None
    closed = [str(name) for name in (record.get("added") or {}).get("closed_access") or []]
    return access, closed


def _record_root_only(ctx: Context) -> bool:
    """Whether only root can change the install record (owner root, no group or other write)."""
    path = str(ctx.config.get("paths", "install_record", default=INSTALL_RECORD))
    found = ctx.system.info(path)
    return found is not None and found.owner == "root" and not found.mode & WRITABLE_BY_OTHERS


def _canonical_address(address: str) -> str:
    """An address as preflight writes it (``::`` for ss's ``*``), compared exactly."""
    address = SS_ANY_ADDRESS.get(address, address)
    try:
        return str(ipaddress.ip_address(address))
    except ValueError:
        return address


def _cgroup_parts(ctx: Context, pid: str) -> list[str]:
    parts: list[str] = []
    for line in (ctx.system.read_text(f"/proc/{pid}/cgroup") or "").splitlines():
        parts.extend(part for part in line.split(":", 2)[-1].split("/") if part)
    return parts


def _owner(
    ctx: Context, port: int, process: str, pids: Sequence[str]
) -> tuple[str | None, str | None, bool]:
    """A listener's owner as preflight records it: ``(unit, process, pinka's own)``.

    A socket systemd holds (PID 1) is named by its socket unit and the service
    it activates, from ``systemctl list-sockets``; any other by the unit of its
    first process's cgroup and the process name.
    """
    if "1" in pids:
        if "socket_units" not in ctx.memo:
            ctx.memo["socket_units"] = access_detect.socket_units(_PreflightRunner(ctx))
        held: dict[int, tuple[str, str]] = ctx.memo["socket_units"]
        if port in held:
            unit, activates = held[port]
            name = "sshd" if activates in access_detect.SSHD_UNITS else activates.rsplit(".", 1)[0]
            return unit, name, unit.startswith(LAB_UNIT_PREFIX)
    if not pids:
        return None, process or None, False
    pid = sorted(pids, key=int)[0]
    parts = _cgroup_parts(ctx, pid)
    ours = LAB_SLICE in parts or any(part.startswith(LAB_UNIT_PREFIX) for part in parts)
    return access_detect.service_unit(parts), process or None, ours


def _recorded_listener(
    ctx: Context, sock: tuple[str, str, int, str, list[str]], access: Mapping[str, Any]
) -> bool:
    """Whether a listener is one the install record notes, exactly, and not pinka's own.

    The exception for a recorded listener holds only on a
    shared host; protocol, address, port, owning unit and process all equal the
    record's; pinka's own services and the daemons of components pinka
    installed never use the exception.
    """
    if access.get("host") != "shared":
        return False
    proto, address, port, process, pids = sock
    unit, name, ours = _owner(ctx, port, process, pids)
    record = _install_record(ctx) or {}
    if ours or (unit is not None and unit in access_detect.pinka_units(record)):
        return False
    found = (proto, _canonical_address(address), port, unit, name)
    return any(
        (
            item.get("proto"),
            _canonical_address(str(item.get("address"))),
            item.get("port"),
            item.get("unit"),
            item.get("process"),
        )
        == found
        for item in access.get("listeners") or []
        if isinstance(item, dict)
    )


def _tailscale_status(ctx: Context) -> dict[str, Any]:
    if "tailscale_status" not in ctx.memo:
        status = ctx.system.run(["tailscale", "status", "--json"]).json()
        ctx.memo["tailscale_status"] = status if isinstance(status, dict) else {}
    found: dict[str, Any] = ctx.memo["tailscale_status"]
    return found


def _preflight(ctx: Context) -> dict[str, Any] | None:
    """Run preflight once (read-only) and keep its report."""
    if "preflight" not in ctx.memo:
        done = ctx.system.run(
            [*commands.preflight_argv(ctx.system, ctx.config), "--quiet"], timeout=300
        )
        parsed = done.json() if done.returncode in (0, 10, 20, 30) else None
        ctx.memo["preflight"] = parsed if isinstance(parsed, dict) else None
    report: dict[str, Any] | None = ctx.memo["preflight"]
    return report


def psql(ctx: Context, query: str, database: str = "postgres") -> Any:  # noqa: ANN401 - JSON
    """Run a query as the database superuser inside the PostgreSQL container.

    Args:
        ctx: Check context.
        query: SQL returning one JSON value.
        database: The database to connect to.

    Returns:
        The parsed value.

    Raises:
        RuntimeError: If psql fails.
    """
    container = str(ctx.config.get("postgres", "container", default="pinka-postgres"))
    argv = [
        "docker",
        "exec",
        "--user",
        "postgres",
        container,
        "sh",
        "-c",
        PSQL_SCRIPT,
        "psql",
        "--tuples-only",
        "--no-align",
        f"--dbname={database}",
        "--command",
        query,
    ]
    done = ctx.system.run(argv)
    if not done.ok:
        raise RuntimeError(done.stderr.strip() or f"psql exited with {done.returncode}")
    return json.loads(done.stdout.strip() or "null")


def sql_names(names: Sequence[str]) -> str:
    """Quote role names for an SQL list.

    Args:
        names: Role names from the lab description.

    Returns:
        ``'a','b'``.

    Raises:
        ValueError: If a name is not a plain lowercase identifier.
    """
    bad = [n for n in names if not SQL_NAME_RE.match(n)]
    if bad:
        raise ValueError(f"not a role name: {', '.join(bad)}")
    return ",".join(f"'{n}'" for n in names) or "NULL"


def sockets(ss_output: str) -> list[tuple[str, str, int, str, list[str]]]:
    """Parse ``ss -H -lntup`` output, with the processes that hold each socket.

    Args:
        ss_output: The command's output.

    Returns:
        ``(protocol, address, port, process, pids)`` per listening socket.
    """
    found = []
    for line in ss_output.splitlines():
        fields = line.split()
        if len(fields) < 5:  # noqa: PLR2004 - netid state recv send local
            continue
        local = fields[4]
        address, _, port = local.rpartition(":")
        address = address.strip("[]").split("%")[0]
        names = PROCESS_RE.findall(line)
        found.append(
            (
                fields[0],
                address,
                int(port) if port.isdigit() else 0,
                names[0] if names else "",
                PID_RE.findall(line),
            )
        )
    return found


def listeners(ss_output: str) -> list[tuple[str, str, int, str]]:
    """Parse ``ss -H -lntup`` output.

    Args:
        ss_output: The command's output.

    Returns:
        ``(protocol, address, port, process)`` per listening socket.
    """
    return [sock[:4] for sock in sockets(ss_output)]


def is_loopback(address: str) -> bool:
    """Whether an address is a loopback address.

    Args:
        address: IPv4 or IPv6 text.

    Returns:
        True for 127.0.0.0/8 and ::1.
    """
    return address.startswith(LOOPBACK_V4) or address == "::1"


def default_acl(ctx: Context, path: str) -> set[str] | None:
    """Named default ACL entries of a directory (``group:pinka:rwx``).

    Args:
        ctx: Check context.
        path: Directory.

    Returns:
        The entries, or None if getfacl failed.
    """
    done = ctx.system.run(["getfacl", "--omit-header", "--absolute-names", "--default", path])
    if not done.ok:
        return None
    entries = set()
    for line in done.stdout.splitlines():
        line = line.split("#")[0].strip()  # noqa: PLW2901 - drop effective-rights comments
        kind, _, rest = line.partition(":")
        qualifier, _, perms = rest.partition(":")
        if kind in ("user", "group") and qualifier:
            entries.add(f"{kind}:{qualifier}:{perms}")
    return entries


def _acl_wanted(entry: str) -> str:
    # setfacl's X means "x on directories"; getfacl shows the resulting x.
    kind, qualifier, perms = entry.split(":")
    return f"{kind}:{qualifier}:{perms.replace('X', 'x')}"


# --- preflight -------------------------------------------------------------------------


def check_preflight_outcome(ctx: Context) -> Outcome:
    """A fresh read-only preflight still allows the lab on this host."""
    report = _preflight(ctx)
    if report is None:
        return failed("preflight did not produce a report")
    outcome = str(report.get("outcome"))
    if outcome in ("supported", "degraded"):
        return passed(f"outcome {outcome}")
    if outcome == "unsupported" and report.get("overrides", {}).get("force_unsupported"):
        return passed("outcome unsupported, forced by the operator (recorded)")
    return failed(f"outcome {outcome}")


def check_preflight_drift(ctx: Context) -> Outcome:
    """Outcome, provider and features still match the stored host profile."""
    report = _preflight(ctx)
    profile = _profile(ctx)
    if report is None or not profile:
        return skipped("no preflight report or no stored host profile")
    diff = differences(profile, report)
    return failed("; ".join(diff)) if diff else passed("matches /etc/pinka/host-profile.json")


# --- services ----------------------------------------------------------------------------


def check_units(ctx: Context) -> Outcome:
    """Every unit of pinka-lab.target is active, the platform's services and sockets included."""
    rendered = _rendered_platform(ctx)
    platform = (
        [*rendered.get("service_units", []), *rendered.get("socket_units", [])]
        if isinstance(rendered, dict)
        else []
    )
    units = [*ctx.config.get("units", default=[]), *(str(u) for u in platform)]
    inactive = []
    for unit in units:
        done = ctx.system.run(["systemctl", "is-active", unit])
        if done.stdout.strip() != "active":
            inactive.append(f"{unit} ({done.stdout.strip() or 'unknown'})")
    if inactive:
        return failed("not active: " + ", ".join(inactive))
    return passed(f"{len(units)} unit(s) active")


def _inspect(ctx: Context, container: str) -> dict[str, Any] | None:
    done = ctx.system.run(["docker", "inspect", container])
    data = done.json() if done.ok else None
    return data[0] if isinstance(data, list) and data else None


def check_containers(ctx: Context) -> Outcome:
    """Lab containers run, are healthy where they have a health check, use their pinned digest."""
    containers: dict[str, str] = dict(ctx.config.get("containers", default={}))
    problems = []
    for name, reference in sorted(containers.items()):
        data = _inspect(ctx, name)
        if data is None:
            problems.append(f"{name}: missing")
            continue
        state = data.get("State", {})
        if not state.get("Running"):
            problems.append(f"{name}: {state.get('Status', 'not running')}")
        health = (state.get("Health") or {}).get("Status")
        if health not in (None, "healthy"):
            problems.append(f"{name}: {health}")
        image = str(data.get("Config", {}).get("Image", ""))
        digest = reference.rpartition("@")[2]
        if not image.endswith("@" + digest):
            problems.append(f"{name}: runs {image}, pinned {reference}")
    if problems:
        return failed("; ".join(problems))
    return passed(f"{len(containers)} container(s) running their pinned digests")


# --- network ----------------------------------------------------------------------------


def check_listeners(ctx: Context) -> Outcome:
    """Nothing listens outside loopback but sshd, host services and listeners from before pinka.

    On a shared host, a listener the install record notes as the host's before
    pinka is expected when it matches the record exactly,
    whether the firewall keeps it reachable or closed it with the owner's
    consent: pinka never stops a pre-existing service. A record that someone
    other than root can change is not trusted for it.
    """
    done = ctx.system.run(["ss", "-H", "-lntup"])
    if not done.ok:
        return failed(f"ss failed: {done.stderr.strip()}")
    allowed_processes = set(ctx.config.get("listeners", "processes", default=["sshd"]))
    allowed_addresses = set(ctx.config.get("listeners", "addresses", default=[]))
    # Split inference: the control endpoint or the collector on the tailnet address.
    tailnet_ports = set(ctx.config.get("listeners", "tailnet_ports", default=[]))
    tailnet: set[str] = set()
    if tailnet_ports:
        shown = ctx.system.run(["tailscale", "ip"])
        tailnet = set(shown.stdout.split()) if shown.ok else set()
    access, closed = _preexisting_access(ctx) or ({}, [])
    untrusted = bool(access) and not _record_root_only(ctx)
    unexpected: set[str] = set()
    preexisting: set[str] = set()
    for sock in sockets(done.stdout):
        proto, address, port, process, _ = sock
        if (
            is_loopback(address)
            or process in allowed_processes
            or address in allowed_addresses
            or (address in tailnet and port in tailnet_ports)
        ):
            continue
        if access and not untrusted and _recorded_listener(ctx, sock, access):
            name = f"{proto}/{port}"
            preexisting.add(f"{name} ({'closed by the firewall' if name in closed else 'kept'})")
            continue
        unexpected.add(f"{proto} {address}:{port} ({process or 'unknown process'})")
    if unexpected:
        note = (
            "; the install record is not root-only, so no listener is accepted as pre-existing"
            if untrusted
            else ""
        )
        return failed("unexpected listeners: " + ", ".join(sorted(unexpected)) + note)
    if preexisting:
        return passed(
            "only loopback listeners, expected host services and the listeners the host had "
            "before pinka: " + ", ".join(sorted(preexisting))
        )
    return passed("only loopback listeners and expected host services")


def check_install_path(ctx: Context) -> Outcome:
    """The access path of the install from outside the tailnet is closed.

    It closes only with the owner's consent and after a researcher logged in
    over the tailnet in the same run; until then it stays open and this check
    warns.
    """
    found = _preexisting_access(ctx)
    if found is None:
        return skipped("the install record notes no access to the host before pinka")
    access, closed = found
    session = access.get("session") or {}
    if session.get("via") != "ssh":
        return skipped("the install ran on the console; there is no access path to close")
    path = f"SSH to {session.get('address')}:{session.get('port')}"
    if session.get("tailnet"):
        return skipped(f"the install came over the tailnet ({path}), which the lab keeps")
    if "install_path" in closed:
        return passed(f"the install's access path ({path}) is closed")
    user = session.get("user") or "an account not in /etc/passwd"
    return failed(f"the install's access path is open: {path} from outside the tailnet, as {user}")


def check_firewall(ctx: Context) -> Outcome:
    """The lab's firewall rules are in place (backend, access guard, Docker compatibility rules)."""
    backend = str(ctx.config.get("firewall", "backend", default="nftables"))
    problems = []
    if backend == "nftables":
        table = ctx.system.run(["nft", "list", "table", "inet", "pinka_filter"])
        if not table.ok:
            problems.append("nftables table inet pinka_filter is missing")
    elif backend == "firewalld":
        for name in FIREWALLD_ZONES:
            if not ctx.system.run(["firewall-cmd", "--info-zone", name]).ok:
                problems.append(f"firewalld zone {name} is missing")
    elif backend == "ufw":
        status = ctx.system.run(["ufw", "status"])
        if "Status: active" not in status.stdout:
            problems.append("ufw is not active")
    # What the owner consented to close, on every backend.
    if not ctx.system.run(["nft", "list", "table", "inet", "pinka_access_guard"]).ok:
        problems.append("nftables table inet pinka_access_guard is missing")
    problems += _docker_user_rules(ctx)
    if problems:
        return failed("; ".join(problems))
    return passed(f"{backend} rules and Docker compatibility rules present")


def _docker_user_rules(ctx: Context) -> list[str]:
    # The rules pinka-docker-incus-compat.service inserts, per table that has DOCKER-USER.
    problems = []
    for tool in ("iptables", "ip6tables"):
        if not ctx.system.run([tool, "-n", "-L", "DOCKER-USER"]).ok:
            continue
        for pattern in ctx.config.get("firewall", "incus_bridge_patterns", default=[]):
            interface = str(pattern).replace("*", "+")
            rules = (
                ["-i", interface, "-j", "ACCEPT"],
                [
                    *("-o", interface, "-m", "conntrack"),
                    *("--ctstate", "RELATED,ESTABLISHED", "-j", "ACCEPT"),
                ],
            )
            for rule in rules:
                if not ctx.system.run([tool, "-C", "DOCKER-USER", *rule]).ok:
                    problems.append(f"{tool} DOCKER-USER lacks {' '.join(rule)}")
    return problems


def check_tailscale(ctx: Context) -> Outcome:
    """Tailscale is logged in with the expected name and tags; Funnel and Tailscale SSH are off.

    On a node pinka joined, the expected name and tag are the configured ones;
    on a node adopted as it was, the ones the install record
    noted. Tailscale SSH is read from ``tailscale debug prefs`` (``RunSSH``),
    the query of preflight's components detector, whose output format is
    still to be verified there.
    """
    if not ctx.config.get("tailscale", "enabled", default=True):
        return skipped("Tailscale is disabled in this lab")
    status = _tailscale_status(ctx)
    problems = []
    if status.get("BackendState") != "Running":
        problems.append(f"backend state {status.get('BackendState', 'unknown')}")
    me = status.get("Self") or {}
    tags = sorted(str(t) for t in me.get("Tags") or [])
    adopted = _adopted_tailscale(ctx)
    if adopted is None:
        hostname = ctx.config.get("tailscale", "hostname", default=ctx.config.machine)
        tag = ctx.config.get("tailscale", "tag")
        if tag and tag not in tags:
            problems.append(f"tag {tag} missing")
        expected = f"{hostname} with {tag}"
    else:
        hostname = adopted.get("hostname")
        recorded = sorted(str(t) for t in adopted.get("tags") or [])
        if tags != recorded:
            problems.append(f"tags {tags}, recorded {recorded}")
        expected = f"{hostname} as recorded (adopted node)"
    if me.get("HostName") != hostname:
        problems.append(f"host name {me.get('HostName')!r}, expected {hostname!r}")
    serve = ctx.system.run(["tailscale", "serve", "status", "--json"]).json() or {}
    if isinstance(serve, dict) and serve.get("AllowFunnel"):
        problems.append("Funnel is enabled")
    prefs = ctx.system.run(["tailscale", "debug", "prefs"]).json() or {}
    if isinstance(prefs, dict) and prefs.get("RunSSH"):
        problems.append("Tailscale SSH is on")
    if problems:
        return failed("; ".join(problems))
    return passed(f"logged in as {expected}, Funnel and Tailscale SSH off")


def check_tailscale_identity(ctx: Context) -> Outcome:
    """A node adopted as it was carries the lab's tag and a key that does not expire.

    Both are warnings: the tailnet ACL can name the
    node another way, and an expired key only takes the lab off the tailnet
    until the owner signs in again (docs/security.md).
    """
    if not ctx.config.get("tailscale", "enabled", default=True):
        return skipped("Tailscale is disabled in this lab")
    if _adopted_tailscale(ctx) is None:
        return skipped("pinka joined this node with the lab's tag")
    me = _tailscale_status(ctx).get("Self") or {}
    problems = []
    tag = ctx.config.get("tailscale", "tag")
    if tag and tag not in (me.get("Tags") or []):
        problems.append(f"no {tag}, so the tailnet ACL must name this node another way")
    expiry = me.get("KeyExpiry")
    if expiry:
        problems.append(
            f"its key expires at {expiry}; the lab then leaves the tailnet until the owner "
            "signs in again"
        )
    if problems:
        return failed("; ".join(problems))
    return passed(f"adopted node with {tag} and no key expiry")


# --- GPU and inference ---------------------------------------------------------------------


def check_gpu(ctx: Context) -> Outcome:
    """A container of the pinned CUDA image sees the GPU."""
    if not ctx.config.serves_inference:
        return skipped("this node serves no inference")
    image = str(ctx.config.get("gpu", "image"))
    device_args = list(ctx.config.get("gpu", "device_args", default=[]))
    argv = [
        "docker",
        "run",
        "--rm",
        "--pull",
        "never",
        *device_args,
        image,
        "nvidia-smi",
        "--list-gpus",
    ]
    done = ctx.system.run(argv, timeout=120)
    gpus = [line for line in done.stdout.splitlines() if line.startswith("GPU ")]
    if not done.ok or not gpus:
        return failed(done.stderr.strip() or "no GPU visible in a container")
    return passed(f"{len(gpus)} GPU(s) visible in a container")


def check_docker_cdi(ctx: Context) -> Outcome:
    """Docker gives containers the GPU through CDI.

    The NVIDIA runtime passes only where bootstrap recorded it as the method,
    on a Docker pinka installed that has no CDI support.
    """
    gpu_feature = ctx.config.feature("gpu_inference") or ctx.config.feature("gpu_training")
    if not gpu_feature or ctx.config.node_role not in ("full", "inference"):
        return skipped("this node runs no GPU containers")
    done = ctx.system.run(["docker", "info", "--format", "{{json .CDISpecDirs}}"])
    if not done.ok:
        return failed(f"docker info failed: {done.stderr.strip() or done.returncode}")
    dirs = done.json()
    if isinstance(dirs, list) and dirs:
        return passed(f"CDI is on ({', '.join(str(d) for d in dirs)})")
    if ctx.config.get("gpu", "method", default="cdi") == "runtime":
        return passed("the NVIDIA runtime, recorded at bootstrap: this Docker has no CDI support")
    return failed("CDI is off in Docker (docker info lists no CDI spec directories)")


def check_driver_cuda(ctx: Context) -> Outcome:
    """The driver supports the CUDA version of the pinned vLLM image."""
    if not ctx.config.serves_inference:
        return skipped("this node serves no inference")
    smi = str(ctx.config.get("gpu", "nvidia_smi", default="nvidia-smi"))
    required = str(ctx.config.get("gpu", "vllm_cuda"))
    needed = tuple(int(n) for n in required.split(".")[:2])
    shown = ".".join(str(n) for n in needed)
    where = (
        "update the NVIDIA driver on Windows"
        if ctx.config.provider == "wsl2"
        else f"install a driver branch that supports CUDA {shown}"
    )
    actual = None
    for args in (gpu.VERSION_QUERY, []):
        done = ctx.system.run([smi, *args], timeout=30)
        actual = gpu.parse_driver_cuda(done.stdout) if done.ok else None
        if actual:
            break
    if actual is None:
        return failed(f"{smi} does not report the CUDA version the driver supports: {where}")
    if tuple(int(n) for n in actual.split(".")) < needed:
        return failed(
            f"the NVIDIA driver supports CUDA {actual}, "
            f"the pinned vLLM image needs {shown}: {where}"
        )
    return passed(f"the driver supports CUDA {actual}; the vLLM image needs {shown}")


WINDOW_RECORD = "/etc/pinka/maintenance-window.json"


def check_window(ctx: Context) -> Outcome:
    """Whether a maintenance window is recorded, and with which end."""
    path = str(ctx.config.get("window", "record", default=WINDOW_RECORD))
    text = ctx.system.read_text(path)
    staging = ctx.config.get("lab_mode", default="production") == "staging"
    shared = staging and bool(ctx.config.get("window", "shared_hardware", default=False))
    if text is None:
        if shared:
            return passed("no maintenance window: this staging lab may not start")
        return passed("no maintenance window")
    try:
        recorded = window.from_text(text)
    except window.WindowError as exc:
        return failed(str(exc))
    is_open = recorded.is_open(ctx.system.now())
    if not staging:
        return failed(
            f"a maintenance window is recorded ({recorded.describe()}): production stays stopped "
            "until sudo pinka window close"
        )
    if is_open:
        return passed(f"maintenance window open {recorded.describe()}")
    return failed(f"the maintenance window {recorded.describe()} is not open now")


DOCS_DIR = "/usr/local/share/pinka/docs"


def check_docs(ctx: Context) -> Outcome:
    """The docs service serves the installed release."""
    if not ctx.config.get("docs", "configured", default=False):
        return skipped("no documentation release is configured (docs_release)")
    base = str(ctx.config.get("docs", "dir", default=DOCS_DIR))
    version = ctx.system.docs_current(base)
    if version is None:
        return failed(f"no documentation release is installed in {base}")
    unit = str(ctx.config.get("docs", "unit", default="pinka-docs.service"))
    state = ctx.system.run(["systemctl", "is-active", unit]).stdout.strip() or "unknown"
    if state != "active":
        return failed(f"{unit} is {state}; {version} is installed but not served")
    port = ctx.config.get("docs", "port", default=18706)
    return passed(f"serving {version} on 127.0.0.1:{port}")


def check_vllm(ctx: Context) -> Outcome:
    """The inference server answers its health check when a deployment is active."""
    if not ctx.config.serves_inference:
        return skipped("this node serves no inference")
    current = ctx.system.info(
        str(ctx.config.get("vllm", "current", default="/etc/pinka/vllm/current.yml"))
    )
    if current is None:
        return skipped("no deployment is active")
    response = ctx.system.http("GET", str(ctx.config.get("vllm", "health_url")))
    if response.status != 200:  # noqa: PLR2004
        return failed(f"/health answered {response.status or 'nothing'}")
    return passed("vLLM is healthy")


def _days_left(ctx: Context, path: str) -> int | None:
    """Whole days until a certificate expires, or None if openssl cannot tell."""
    done = ctx.system.run(["openssl", "x509", "-noout", "-enddate", "-in", path])
    if not done.ok or not done.stdout.startswith("notAfter="):
        return None
    try:
        end = ssl.cert_time_to_seconds(done.stdout.strip().split("=", 1)[1])
    except ValueError:
        return None
    return int((end - ctx.system.now().timestamp()) // 86400)


def _key_problems(ctx: Context, path: str) -> list[str]:
    """The node's private key: 0640 root:<account> on an inference node, 0600 root on a lab node."""
    key = ctx.system.info(path)
    if key is None:
        return []
    inference = ctx.config.node_role == "inference"
    group = str(ctx.config.get("inference", "key_group", default="" if inference else "root"))
    default_mode = "0640" if inference else "0600"
    mode = int(str(ctx.config.get("inference", "key_mode", default=default_mode)), 8)
    if (key.owner, key.group, key.mode) == ("root", group, mode):
        return []
    return [f"{path} is {key.owner}:{key.group} {key.mode:04o}, expected root:{group} {mode:04o}"]


def check_pairing(ctx: Context) -> Outcome:
    """Split inference: the node's certificate is valid; it is paired with the configured node."""
    if ctx.config.node_role not in ("lab", "inference"):
        return skipped("a full node serves inference itself")
    directory = str(ctx.config.get("inference", "dir", default="/etc/pinka/inference"))
    configured = str(ctx.config.get("inference", "peer", default=""))
    variable = (
        "inference_lab_node" if ctx.config.node_role == "inference" else "inference_remote_node"
    )
    problems = [
        f"{directory}/{name} missing"
        for name in ("self.crt", "self.key")
        if ctx.system.info(f"{directory}/{name}") is None
    ]
    key = _key_problems(ctx, f"{directory}/self.key")
    try:
        record = json.loads(ctx.system.read_text(f"{directory}/peer.json") or "{}")
    except ValueError:
        record = {}
    paired = str(record.get("node", "")) if isinstance(record, dict) else ""
    if not configured and not problems:
        if paired:
            key.insert(0, f"paired with {paired}, but {variable} is not set in the host_vars")
        if key:
            return failed("; ".join(key))
        return skipped("not paired yet")
    problems += key
    if configured and paired != configured:
        problems.append(
            f"{variable} is {configured}, but the node is paired with {paired or 'none'}"
        )
    if configured and ctx.system.info(f"{directory}/peer.crt") is None:
        problems.append(f"{directory}/peer.crt missing")
    renew = int(ctx.config.get("inference", "renew_days", default=30))
    for name in ("self.crt", "peer.crt"):
        days = _days_left(ctx, f"{directory}/{name}")
        if days is not None and days < renew:
            problems.append(f"{name} expires in {days} day(s)")
    if problems:
        return failed("; ".join(problems))
    return passed(f"paired with {configured}, certificates valid for {renew}+ days")


SPLIT_GUARD_TABLE = "pinka_split_guard"


def _set_elements(items: list[dict[str, Any]], name: str) -> list[Any] | None:
    found = [i["set"] for i in items if i.get("set", {}).get("name") == name]
    return list(found[0].get("elem", [])) if found else None


def check_split_guard(ctx: Context) -> Outcome:
    """Split inference: the tailnet ports admit only the paired peer's address."""
    spec = ctx.config.get("split_guard", default={}) or {}
    ports = [int(p) for p in spec.get("ports", [])]
    if ctx.config.node_role not in ("lab", "inference") or not ports:
        return skipped("a full node has no split-inference ports")
    items = _loaded_table(ctx, SPLIT_GUARD_TABLE)
    if items is None:
        return failed(f"the nftables table inet {SPLIT_GUARD_TABLE} is not loaded")
    directory = str(ctx.config.get("inference", "dir", default="/etc/pinka/inference"))
    try:
        record = json.loads(ctx.system.read_text(f"{directory}/peer.json") or "{}")
    except ValueError:
        record = {}
    record = record if isinstance(record, dict) else {}
    address = str(record.get("address") or "")
    problems = []
    if record.get("node") and not address:
        problems.append("the pairing record has no tailnet address; pair the nodes again")
    loaded = sorted(str(e) for e in (_set_elements(items, "peer") or []))
    if loaded != ([address] if address else []):
        problems.append(
            f"the guard admits {', '.join(loaded) or 'nobody'}, expected {address or 'nobody'}"
        )
    rules = [json.dumps(i["rule"]) for i in items if "rule" in i]
    drop = next((r for r in rules if '"drop"' in r), None)
    closed = [p for p in ports if drop is not None and str(p) in drop]
    if sorted(closed) != sorted(ports):
        open_ports = ", ".join(str(p) for p in ports if p not in closed)
        problems.append(f"the guard leaves ports open to the tailnet: {open_ports}")
    if _set_elements(items, "pairing"):
        problems.append(
            "the pairing port is open to the tailnet; it closes when pinka pair show ends "
            "(or by itself shortly after)"
        )
    if problems:
        return failed("; ".join(problems))
    who = address or "nobody until the nodes pair"
    return passed(f"ports {', '.join(map(str, ports))} admit only {who}")


# --- Incus ---------------------------------------------------------------------------------


def _incus_query(ctx: Context, path: str) -> Any:  # noqa: ANN401 - JSON value
    done = ctx.system.run(["incus", "query", path])
    if not done.ok:
        raise RuntimeError(done.stderr.strip() or f"incus query {path} failed")
    return done.json()


def check_incus_pool(ctx: Context) -> Outcome:
    """The storage pool has the backend preflight chose and enough free space."""
    if not ctx.config.runs_lab:
        return skipped("no Incus on this node")
    pool = str(ctx.config.get("incus", "pool"))
    info = _incus_query(ctx, f"/1.0/storage-pools/{pool}") or {}
    driver = info.get("driver")
    wanted = {"btrfs": "btrfs", "lvm": "lvm"}.get(str(ctx.config.get("incus", "backend")), "")
    if driver != wanted:
        return failed(f"pool {pool} uses {driver}, expected {wanted}")
    space = (_incus_query(ctx, f"/1.0/storage-pools/{pool}/resources") or {}).get("space", {})
    total, used = int(space.get("total", 0)), int(space.get("used", 0))
    free_gib = (total - used) / 2**30
    minimum = float(ctx.config.get("incus", "min_free_gib", default=20))
    if free_gib < minimum:
        return failed(f"pool {pool} has {free_gib:.1f} GiB free, less than {minimum:g} GiB")
    return passed(f"pool {pool} ({driver}), {free_gib:.1f} GiB free")


# Free space per mount point: the runs kept, the trend's window.
DISK_HISTORY_DEFAULT = "/var/lib/pinka/doctor/disk-history.json"
DISK_HISTORY_KEEP = 60
DISK_TREND_DAYS = 7
DISK_TREND_MIN_HOURS = 6


def _df(ctx: Context, paths: Sequence[str]) -> dict[str, tuple[int, int]]:
    """Size and free bytes per mount point of ``paths`` (df's target)."""
    done = ctx.system.run(["df", "--output=target,size,avail", "-B1", *paths])
    found: dict[str, tuple[int, int]] = {}
    for line in done.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 3 and parts[-1].isdigit() and parts[-2].isdigit():  # noqa: PLR2004
            found[" ".join(parts[:-2])] = (int(parts[-2]), int(parts[-1]))
    return found


def _disk_history(ctx: Context, path: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(ctx.system.read_text(path) or "{}")
    except ValueError:
        return []
    runs = data.get("runs") if isinstance(data, dict) else None
    return [r for r in runs or [] if isinstance(r, dict) and isinstance(r.get("free"), dict)]


def _days_until_full(
    runs: Sequence[Mapping[str, Any]], mount: str, free: int, now: dt.datetime
) -> float | None:
    """Days until ``mount`` is full at the rate since the oldest run of the trend window."""
    since = now - dt.timedelta(days=DISK_TREND_DAYS)
    for run_ in runs:
        at = dt.datetime.strptime(str(run_.get("at")), "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc
        )
        before = run_["free"].get(mount)
        if at < since or not isinstance(before, int):
            continue
        hours = (now - at).total_seconds() / 3600
        if hours < DISK_TREND_MIN_HOURS or before <= free:
            return None
        return free / ((before - free) / (hours / 24))
    return None


def check_disk_free_space(ctx: Context) -> Outcome:
    """Each mount point the lab uses keeps free space, and its trend does not fill it soon."""
    report = _preflight(ctx) or {}
    mounts = sorted({str(m["mount"]) for m in (report.get("requirements") or {}).get("disk", [])})
    if not mounts:
        mounts = [ctx.config.pinka_root]
    measured = _df(ctx, mounts)
    if not measured:
        return failed("df measured none of " + ", ".join(mounts))
    history = str(ctx.config.get("disk", "history", default=DISK_HISTORY_DEFAULT))
    warn_percent = float(ctx.config.get("disk", "warn_free_percent", default=15))
    full_days = float(ctx.config.get("disk", "full_within_days", default=DISK_TREND_DAYS))
    now = ctx.system.now()
    runs = _disk_history(ctx, history)
    problems, states = [], []
    for mount, (size, free) in sorted(measured.items()):
        percent = 100 * free / size if size else 0.0
        days = _days_until_full(runs, mount, free, now)
        states.append(f"{mount} {free / 2**30:.1f} GiB free ({percent:.1f} %)")
        if percent < warn_percent:
            problems.append(f"{mount} has {percent:.1f} % free, below {warn_percent:g} %")
        if days is not None and days < full_days:
            problems.append(f"{mount} fills in about {days:.1f} days at its recent rate")
    if ctx.system.euid() == 0:
        stamp = now.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        entry = {"at": stamp, "free": {m: free for m, (_, free) in measured.items()}}
        kept = [*runs, entry][-DISK_HISTORY_KEEP:]
        text = json.dumps({"schema_version": 1, "runs": kept}, indent=2, sort_keys=True)
        ctx.system.write_text(history, text + "\n", 0o644)
    if problems:
        return failed("; ".join(problems))
    return passed("; ".join(states))


def check_incus_objects(ctx: Context) -> Outcome:
    """The lab's projects and profiles exist."""
    if not ctx.config.runs_lab:
        return skipped("no Incus on this node")
    projects = {p["name"] for p in _incus_query(ctx, "/1.0/projects?recursion=1") or []}
    missing = [p for p in ctx.config.get("incus", "projects", default=[]) if p not in projects]
    for entry in ctx.config.get("incus", "profiles", default=[]):
        profiles = _incus_query(ctx, f"/1.0/profiles?project={entry['project']}") or []
        names = {str(p).rsplit("/", 1)[-1].split("?")[0] for p in profiles}
        if entry["name"] not in names:
            missing.append(f"profile {entry['name']} ({entry['project']})")
    if missing:
        return failed("missing: " + ", ".join(missing))
    return passed("projects and profiles present")


def check_incus_certificates(ctx: Context) -> Outcome:
    """Every service's client certificate exists, belongs to the service and is trusted."""
    if not ctx.config.runs_lab:
        return skipped("no Incus on this node")
    trusted = {c.get("name") for c in _incus_query(ctx, "/1.0/certificates?recursion=1") or []}
    base = str(ctx.config.get("incus", "cert_dir", default="/etc/pinka/incus"))
    problems = []
    for cert in ctx.config.get("incus", "certificates", default=[]):
        for name in ("client.crt", "client.key"):
            path = f"{base}/{cert['short']}/{name}"
            info = ctx.system.info(path)
            if info is None:
                problems.append(f"{path} missing")
            elif info.owner != cert["account"] or info.mode != KEY_MODE:
                problems.append(f"{path} is {info.owner} {info.mode:04o}")
        if cert["name"] not in trusted:
            problems.append(f"{cert['name']} is not trusted")
    if problems:
        return failed("; ".join(problems))
    return passed("client certificates present, owned and trusted")


def check_incus_metrics(ctx: Context) -> Outcome:
    """Prometheus scrapes the Incus metrics endpoint."""
    if not ctx.config.runs_lab:
        return skipped("no Incus on this node")
    prometheus = str(ctx.config.get("observability", "prometheus"))
    response = ctx.system.http("GET", f"{prometheus}/api/v1/query?query=up%7Bjob%3D%22incus%22%7D")
    results = ((response.json() or {}).get("data") or {}).get("result") or []
    if not results:
        return failed("Prometheus has no incus target")
    if results[0].get("value", [0, "0"])[1] != "1":
        return failed("the incus target is down")
    return passed("Prometheus scrapes Incus")


def check_incus_events(ctx: Context) -> Outcome:
    """The Incus event feed runs."""
    if not ctx.config.runs_lab:
        return skipped("no Incus on this node")
    unit = str(ctx.config.get("incus", "events_unit", default="pinka-incus-events.service"))
    state = ctx.system.run(["systemctl", "is-active", unit]).stdout.strip()
    return (
        passed(f"{unit} active") if state == "active" else failed(f"{unit} is {state or 'unknown'}")
    )


# --- layout and accounts ---------------------------------------------------------------------


def check_layout(ctx: Context) -> Outcome:
    """Lab directories have the owners, modes and default ACLs of the layout."""
    problems = []
    entries = list(ctx.config.get("layout", default=[]))
    for entry in entries:
        info = ctx.system.info(entry["path"])
        if info is None or not info.is_dir:
            problems.append(f"{entry['path']} missing")
            continue
        want_mode = int(str(entry["mode"]), 8)
        if (info.owner, info.group, info.mode) != (entry["owner"], entry["group"], want_mode):
            problems.append(
                f"{entry['path']} is {info.owner}:{info.group} {info.mode:04o}, "
                f"expected {entry['owner']}:{entry['group']} {want_mode:04o}"
            )
        wanted_acl = {_acl_wanted(a) for a in entry.get("default_acl", [])}
        if wanted_acl:
            actual = default_acl(ctx, entry["path"]) or set()
            if not wanted_acl <= actual:
                problems.append(
                    f"{entry['path']} lacks default ACL {', '.join(sorted(wanted_acl - actual))}"
                )
    if problems:
        return failed("; ".join(problems))
    return passed(f"{len(entries)} directories match the layout")


def check_accounts(ctx: Context) -> Outcome:
    """Researchers are not in docker or the Incus admin group, platform accounts not in docker."""
    privileged = list(ctx.config.get("accounts", "privileged_groups", default=["docker"]))
    break_glass = set(ctx.config.get("accounts", "break_glass_docker", default=[]))
    problems = []
    for user in ctx.config.get("accounts", "researchers", default=[]):
        for group in ctx.system.user_groups(user) or []:
            if group in privileged and not (group == "docker" and user in break_glass):
                problems.append(f"researcher {user} is in {group}")
    for user in ctx.config.get("accounts", "platform_accounts", default=[]):
        if "docker" in (ctx.system.user_groups(user) or []):
            problems.append(f"platform account {user} is in docker")
    if problems:
        return failed("; ".join(problems))
    note = f" (break-glass: {', '.join(sorted(break_glass))})" if break_glass else ""
    return passed("no researcher or platform account in a privileged group" + note)


def check_deployed_paths(ctx: Context) -> Outcome:
    """No .git in deployed paths, no restricted package outside the restricted area."""
    root = ctx.config.pinka_root
    problems = []
    for top in (f"{root}/platforms", f"{root}/restricted"):
        for directory in ctx.system.walk_dirs(top, max_depth=8):
            if directory.rsplit("/", 1)[-1] == ".git":
                problems.append(f"{directory} exists")
    for directory in ctx.system.walk_dirs(f"{root}/platforms", max_depth=5):
        text = ctx.system.read_text(f"{directory}/DEPLOYED_FROM.json")
        provenance = json.loads(text) if text else {}
        if provenance.get("restricted"):
            problems.append(f"restricted package {provenance.get('package')} in {directory}")
    if problems:
        return failed("; ".join(problems))
    return passed("no .git in deployed paths, restricted packages only in the restricted area")


ACTIVE_PLATFORM = "/etc/pinka/platform/active.json"
PLATFORM_TOOL = "/usr/local/libexec/pinka/pinka-platform"
MANIFEST_FILE = "platform-manifest.yaml"
PROVENANCE_FILE = "DEPLOYED_FROM.json"


def check_platform_manifest(ctx: Context) -> Outcome:
    """The active release's manifest is valid and its SHA-256 matches the records."""
    path = str(ctx.config.get("paths", "active_platform", default=ACTIVE_PLATFORM))
    text = ctx.system.read_text(path)
    if not text:
        return skipped("no platform is active")
    try:
        active = json.loads(text)
        target = str(active["release"]["target"])
        recorded = str(active["manifest_sha256"])
    except (ValueError, KeyError, TypeError):
        return failed(f"{path} is not an active-platform record")
    problems = []
    done = ctx.system.run(["sha256sum", f"{target}/{MANIFEST_FILE}"])
    digest = done.stdout.split()[0] if done.ok and done.stdout.split() else ""
    if not digest:
        problems.append(f"{target}/{MANIFEST_FILE} cannot be read")
    elif digest != recorded:
        problems.append(f"the manifest's SHA-256 is {digest}, {path} records {recorded}")
    provenance_text = ctx.system.read_text(f"{target}/{PROVENANCE_FILE}")
    try:
        provenance = json.loads(provenance_text) if provenance_text else {}
    except ValueError:
        provenance = {}
    deployed = str((provenance.get("platform") or {}).get("manifest_sha256", ""))
    if digest and deployed != digest:
        problems.append(f"{PROVENANCE_FILE} records {deployed or 'no manifest hash'}")
    tool = str(ctx.config.get("paths", "platform_tool", default=PLATFORM_TOOL))
    check = ctx.system.run([tool, "validate", target, "--built", "--no-host-check", "--json"])
    result = check.json() if check.returncode in (0, 65) else None
    if not isinstance(result, dict):
        problems.append(f"the validator failed: {check.stderr.strip() or check.returncode}")
    elif not result.get("valid"):
        reasons = [str(p.get("message")) for p in result.get("problems", [])][:3]
        problems.append("the release no longer validates: " + "; ".join(reasons))
    if problems:
        return failed("; ".join(problems))
    return passed(f"{active.get('name')} {active.get('version')}: manifest valid, hash recorded")


def check_hardening(ctx: Context) -> Outcome:
    """Units carry the hardening of their profile: `systemctl show` against rendering."""
    rendered = _rendered_platform(ctx)
    units = [
        *ctx.config.get("hardening", default=[]),
        *(rendered.get("hardening", []) if isinstance(rendered, dict) else []),
    ]
    if not units:
        return skipped("no platform and no hardened infra unit on this node")
    problems = []
    for unit in units:
        props = [*unit.get("properties", {}), "InaccessiblePaths"]
        if "address_families" in unit:
            props.append("RestrictAddressFamilies")
        done = ctx.system.run(["systemctl", "show", unit["unit"], "--property=" + ",".join(props)])
        values = dict(line.split("=", 1) for line in done.stdout.splitlines() if "=" in line)
        for name, wanted in unit.get("properties", {}).items():
            if values.get(name) != wanted:
                problems.append(f"{unit['unit']}: {name}={values.get(name)!r}, expected {wanted!r}")
        if "address_families" in unit:
            # systemctl show prints an allow list as names, a deny list with "~".
            shown = values.get("RestrictAddressFamilies", "")
            families = set(shown.split()) if not shown.startswith("~") else set()
            if families != set(unit["address_families"]):
                problems.append(
                    f"{unit['unit']}: RestrictAddressFamilies={shown!r}, expected "
                    f"{' '.join(unit['address_families'])!r}"
                )
        # systemd prefixes paths that may be missing with "-"; hyphens inside paths stay.
        paths = {p.lstrip("-") for p in values.get("InaccessiblePaths", "").split()}
        missing = [p for p in unit.get("inaccessible_paths", []) if p not in paths]
        if missing:
            problems.append(f"{unit['unit']}: InaccessiblePaths lacks {', '.join(missing)}")
    if problems:
        return failed("; ".join(problems))
    return passed(f"{len(units)} unit(s) hardened as their profiles require")


def check_sockets(ctx: Context) -> Outcome:
    """Platform sockets have the owners, modes and ACLs of their rendering."""
    rendered = _rendered_platform(ctx)
    sockets = [s for s in (rendered or {}).get("sockets", []) if isinstance(s, dict)]
    if not sockets:
        return skipped("the platform is not deployed or has no sockets")
    fell_back = {str(p) for p in (rendered or {}).get(SOCKET_ACL_FALLBACK) or []}
    problems = []
    for entry in sockets:
        path = str(entry["path"])
        info = ctx.system.info(path)
        if info is None:
            problems.append(f"{path} missing")
            continue
        # Without ACL support on /run the helper opens a socket with ACL entries
        # to 0666 (sockets.acl_fallback reports it).
        acls = [] if path in fell_back else list(entry.get("acl") or [])
        mode = FALLBACK_MODE if path in fell_back else int(str(entry["mode"]), 8)
        want = (entry["owner"], entry["group"], mode)
        if (info.owner, info.group, info.mode & 0o7777) != want:
            problems.append(
                f"{path} is {info.owner}:{info.group} {info.mode:04o}, "
                f"expected {want[0]}:{want[1]} {want[2]:04o}"
            )
        if acls:
            done = ctx.system.run(["getfacl", "--omit-header", "--absolute-names", path])
            lines = {line.split()[0] for line in done.stdout.splitlines() if line.strip()}
            problems += [f"{path} lacks ACL {acl}" for acl in acls if acl not in lines]
    if problems:
        return failed("; ".join(problems))
    agent = sorted(str(s["path"]) for s in sockets if s.get("untrusted_clients"))
    note = f"; dedicated agent sockets: {', '.join(agent)}" if agent else ""
    return passed(f"{len(sockets)} socket(s) as rendered{note}")


def _main_pid(ctx: Context, unit: str) -> str:
    done = ctx.system.run(["systemctl", "show", unit, "--property=MainPID", "--value"])
    pid = done.stdout.strip()
    return pid if done.ok and pid.isdigit() and pid != "0" else ""


# How many unexpected entries a message names.
SHOWN = 5


def _release_view_problems(
    ctx: Context, unit: str, root: str, view: Mapping[str, Any]
) -> list[str]:
    """What the running agent sees of the release beyond its package and environment (V6)."""
    release = str(view.get("release") or "")
    allowed = [str(p).strip("/") for p in view.get("release_paths", [])]
    if not release:
        return []
    top = f"{root}{release}"
    depth = max((len(p.split("/")) for p in allowed), default=1)
    done = ctx.system.run(
        ["find", top, "-mindepth", "1", "-maxdepth", str(depth), "-print"], timeout=60
    )
    if not done.ok and not done.stdout.strip():
        return [f"{unit}: cannot list its view of the release ({done.stderr.strip()})"]
    seen = [line[len(top) + 1 :] for line in done.stdout.splitlines() if line.startswith(top + "/")]
    extra = [
        s
        for s in seen
        if not any(a == s or a.startswith(s + "/") or s.startswith(a + "/") for a in allowed)
    ]
    if not extra:
        return []
    shown = ", ".join(extra[:SHOWN]) + (f" and {len(extra) - SHOWN} more" if extra[SHOWN:] else "")
    return [f"{unit} sees parts of the release beyond its package and environment: {shown}"]


def check_agent_view(ctx: Context) -> Outcome:
    """The running agent sees no socket beyond its allowlist in its mount namespace.

    Of the release it sees only its own package and environment (V6).
    """
    rendered = _rendered_platform(ctx)
    agents = [
        h
        for h in ((rendered or {}).get("hardening") or [])
        if isinstance(h, dict) and "agent_view" in h
    ]
    if not agents:
        return skipped("no untrusted-agent service is deployed")
    problems, checked = [], []
    for agent in agents:
        unit = str(agent["unit"])
        pid = _main_pid(ctx, unit)
        if not pid:
            continue
        root = f"/proc/{pid}/root"
        # Through /proc/<pid>/root the walk resolves paths in the agent's own
        # mount namespace; bind-mounted sockets are separate mounts, so no -xdev.
        done = ctx.system.run(
            [
                "find",
                root,
                "-path",
                f"{root}/proc",
                "-prune",
                "-o",
                "-path",
                f"{root}/sys",
                "-prune",
                "-o",
                "-type",
                "s",
                "-print",
            ],
            timeout=300,
        )
        if not done.ok and not done.stdout.strip():
            problems.append(f"{unit}: cannot inspect its file system ({done.stderr.strip()})")
            continue
        view = agent["agent_view"]
        allowed = set(view.get("sockets", []))
        own = [str(d).rstrip("/") + "/" for d in view.get("own_dirs", [])]
        seen = sorted(
            line[len(root) :] for line in done.stdout.splitlines() if line.startswith(root)
        )
        extra = [s for s in seen if s not in allowed and not any(s.startswith(d) for d in own)]
        if extra:
            problems.append(f"{unit} sees sockets beyond its allowlist: {', '.join(extra)}")
        problems += _release_view_problems(ctx, unit, root, view)
        checked.append(f"{unit} ({len(seen)} socket(s))")
    if problems:
        return failed("; ".join(problems))
    if not checked:
        return skipped("no untrusted-agent service is running")
    return passed("agent view within its allowlist: " + ", ".join(checked))


def check_socket_acl_fallback(ctx: Context) -> Outcome:
    """No platform socket fell back to mode 0666 for want of ACL support on /run."""
    path = str(ctx.config.get("paths", "rendered_platform", default=RENDERED_PLATFORM))
    text = ctx.system.read_text(path)
    try:
        rendered = json.loads(text) if text else None
    except ValueError:
        return failed(f"{path} is not JSON")
    if not isinstance(rendered, dict):
        return skipped("the platform is not deployed")
    human = [s for s in rendered.get("sockets", []) if isinstance(s, dict) and s.get("acl")]
    if not human:
        return skipped("no platform socket has researchers or admins among its clients")
    # The helper's record, and what the sockets show now (a start without the helper).
    fell_back = {p for p in rendered.get(SOCKET_ACL_FALLBACK) or [] if isinstance(p, str)}
    for entry in human:
        info = ctx.system.info(str(entry.get("path")))
        if info is not None and info.mode & 0o777 == FALLBACK_MODE:
            fell_back.add(str(entry["path"]))
    if fell_back:
        return failed(
            "; ".join(
                f"{p} is 0666 without ACL entries (no ACL support on /run): only the "
                "platform's peer-credential check separates researchers, admins and services"
                for p in sorted(fell_back)
            )
        )
    return passed(f"{len(human)} socket(s) with ACL entries for researchers or admins")


QUERY_SOCKET_MODE = 0o660
# The query socket's directory (observability role, tmpfiles.d).
QUERY_DIR_MODE = 0o750
QUERY_GROUP = "pinka-query"
AGENT_SOCKET_MODE = 0o666


GUARD_TABLE = "pinka_telemetry_guard"
# The guard's sets (ADR-0016) with the keys of their accounts and ports in the
# specification: infra's clients of the stores, and the query port's clients.
GUARD_SETS = (
    ("telemetry_clients", "clients", "ports"),
    ("query_port_clients", "query_clients", "query_ports"),
)


def _guard_set_problems(
    ctx: Context,
    items: list[dict[str, Any]],
    guard: str,
    *,
    name: str,
    accounts: list[Any],
    ports: list[Any],
) -> list[str]:
    """One set of a loaded guard: exactly these accounts' UIDs, its rule over these ports."""
    problems = []
    elements = next(
        (i["set"].get("elem", []) for i in items if i.get("set", {}).get("name") == name),
        None,
    )
    expected = {}
    for account in accounts:
        uid = ctx.system.uid_of(str(account))
        if uid is None:
            problems.append(f"the {guard} client {account} does not exist")
        else:
            expected[uid] = str(account)
    if elements is None:
        problems.append(f"the {guard} guard has no set {name}")
    else:
        loaded = {e for e in elements if isinstance(e, int)}
        extra = sorted(loaded - set(expected))
        missing = sorted(set(expected) - loaded)
        if extra:
            problems.append(f"{name} also admits UIDs " + ", ".join(map(str, extra)))
        if missing:
            names = ", ".join(expected[u] for u in missing)
            problems.append(f"{name} does not admit {names}")
    # The set's own rule: a reject for the UIDs outside it, over its ports.
    rules = [json.dumps(i["rule"]) for i in items if "rule" in i]
    rule = next((r for r in rules if f'"right": "@{name}"' in r and '"op": "!="' in r), None)
    if rule is None:
        problems.append(f"the {guard} guard has no rule for the accounts outside {name}")
        return problems
    open_ports = [str(p) for p in ports if str(p) not in rule]
    if open_ports:
        problems.append(f"{name} leaves ports open: {', '.join(open_ports)}")
    return problems


def _loaded_table(ctx: Context, table: str) -> list[dict[str, Any]] | None:
    """The items of a loaded nftables table (``nft -j``), or None if it is not loaded."""
    done = ctx.system.run(["nft", "-j", "list", "table", "inet", table])
    try:
        items = [i for i in json.loads(done.stdout)["nftables"] if isinstance(i, dict)]
    except (ValueError, KeyError, TypeError):
        items = []
    return items if done.ok and items else None


def _guard_problems(ctx: Context, spec: Mapping[str, Any]) -> list[str]:
    """The loaded telemetry guard against its specification (ADR-0016)."""
    items = _loaded_table(ctx, GUARD_TABLE)
    if items is None:
        return [f"the nftables table inet {GUARD_TABLE} is not loaded"]
    problems = []
    for name, accounts, ports in GUARD_SETS:
        if spec.get(ports):
            problems += _guard_set_problems(
                ctx,
                items,
                "telemetry",
                name=name,
                accounts=list(spec.get(accounts, [])),
                ports=list(spec[ports]),
            )
    return problems


def _query_link_problems(ctx: Context, spec: Mapping[str, Any]) -> list[str]:
    """The query socket's link, its target and the target directory's owner."""
    link = str(spec.get("query_link") or "")
    if not link:
        return []
    problems = []
    target = ctx.system.readlink(link)
    expected = str(spec.get("query_target", ""))
    if target is None:
        problems.append(f"{link} is not a link")
    elif target != expected:
        problems.append(f"{link} points to {target}, not {expected}")
    directory = str(spec.get("query_dir", ""))
    owner = str(spec.get("query_owner", ""))
    info = ctx.system.info(directory) if directory else None
    if directory and info is None:
        problems.append(f"{directory} is missing")
    elif info is not None and (
        not info.is_dir
        or info.owner != owner
        or info.group != QUERY_GROUP
        or info.mode != QUERY_DIR_MODE
    ):
        problems.append(
            f"{directory} is {info.owner}:{info.group} {info.mode:04o}, "
            f"not {owner}:{QUERY_GROUP} {QUERY_DIR_MODE:04o}"
        )
    return problems


def check_telemetry_isolation(ctx: Context) -> Outcome:
    """Only infra's own clients reach the stores; the query and agent sockets are in place."""
    spec = ctx.config.get("telemetry_isolation", default={}) or {}
    if not spec.get("ports"):
        return skipped("this host has no telemetry isolation to check")
    problems = _guard_problems(ctx, spec)
    problems += _query_link_problems(ctx, spec)
    for key, mode, group in (
        ("query_socket", QUERY_SOCKET_MODE, QUERY_GROUP),
        ("agent_socket", AGENT_SOCKET_MODE, None),
    ):
        path = spec.get(key)
        if not path:
            continue
        info = ctx.system.info(str(path))
        if info is None:
            problems.append(f"{path} is missing")
        elif info.mode & 0o777 != mode or (group and info.group != group):
            problems.append(f"{path} is {info.group} {info.mode & 0o777:04o}")
    if problems:
        return failed("; ".join(problems))
    return passed(
        "the telemetry guard admits only infra's clients; the query link and the query and "
        "agent sockets are in place"
    )


# --- the GPU arbiter, training and the inference guard --------------------------

INFERENCE_GUARD_TABLE = "pinka_inference_guard"
GPU_MODES = ("idle", "serving", "training")
GPU_SOCKET_MODE = 0o600
REGISTERED_DIR_MODE = 0o555
DATASET_DIR_MODE = 0o750
DATASETS_GROUP = "pinka-datasets"
FORBIDDEN_SPLIT = "test"
JOB_TMP = "/tmp"  # noqa: S108 - a job container's own tmpfs
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _arbiter(ctx: Context) -> dict[str, Any]:
    section = ctx.config.get("gpu_arbiter", default={}) or {}
    return dict(section) if isinstance(section, dict) else {}


def _rendered(ctx: Context) -> dict[str, Any]:
    if "rendered" not in ctx.memo:
        text = ctx.system.read_text(RENDERED_PLATFORM)
        try:
            data = json.loads(text) if text else {}
        except ValueError:
            data = {}
        ctx.memo["rendered"] = data if isinstance(data, dict) else {}
    rendered: dict[str, Any] = ctx.memo["rendered"]
    return rendered


def _json_file(ctx: Context, path: str) -> Any:  # noqa: ANN401 - parsed JSON
    text = ctx.system.read_text(path)
    try:
        return json.loads(text) if text else None
    except ValueError:
        return None


def check_gpu_arbiter(ctx: Context) -> Outcome:
    """The arbiter's socket belongs to the allowed account; its mode and log are sound."""
    arbiter = _arbiter(ctx)
    if not arbiter:
        return skipped(f"a {ctx.config.node_role} node has no GPU arbiter")
    problems = []
    peer = str(arbiter.get("peer") or _rendered(ctx).get("controller") or "root")
    socket = str(arbiter["socket"])
    info = ctx.system.info(socket)
    if info is None:
        problems.append(f"{socket} is missing (pinka-gpu-arbiter.socket)")
    elif info.owner != peer or info.mode & 0o777 != GPU_SOCKET_MODE:
        problems.append(f"{socket} is {info.owner} {info.mode & 0o777:04o}, not {peer} 0600")
    text = ctx.system.read_text(str(arbiter["state"]))
    state = _json_file(ctx, str(arbiter["state"])) if text else {"mode": "idle"}
    mode = state.get("mode") if isinstance(state, dict) else None
    if mode not in GPU_MODES:
        problems.append(f"{arbiter['state']} holds no valid mode")
        mode = "unknown"
    elif mode == "training":
        unit = f"pinka-gpu-job@{state.get('run')}.service"
        active = ctx.system.run(["systemctl", "is-active", unit]).stdout.strip() or "unknown"
        if active not in ("active", "activating"):
            problems.append(f"the GPU is training run {state.get('run')}, but {unit} is {active}")
    log = ctx.system.read_text(str(arbiter["transitions"])) or ""
    entries = []
    for number, line in enumerate(log.splitlines(), start=1):
        try:
            entries.append(json.loads(line))
        except ValueError:
            problems.append(f"{arbiter['transitions']} line {number} is not JSON")
            break
    if problems:
        return failed("; ".join(problems))
    target = state.get("deployment") if mode == "serving" else state.get("run")
    now = f"{mode} {target}" if target and mode != "idle" else str(mode)
    last = entries[-1] if entries else None
    if last:
        now += f"; last transition {last.get('from')} -> {last.get('to')} ({last.get('outcome')})"
    return passed(now)


def _expected_mounts(arbiter: Mapping[str, Any], run: str) -> tuple[list[str], list[str]]:
    jobs = str(arbiter.get("jobs", ""))
    read_only = [str(arbiter.get("models", "")) + "/", str(arbiter.get("datasets", "")) + "/"]
    read_write = [f"{jobs}/{run}/output", f"{jobs}/{run}/checkpoints"]
    return read_only, read_write


def _host_option_problems(host: Mapping[str, Any]) -> list[str]:
    """A job container's HostConfig against infra's fixed options."""
    problems = []
    if host.get("NetworkMode") != "none":
        problems.append(f"network {host.get('NetworkMode')}")
    if not host.get("ReadonlyRootfs"):
        problems.append("a writable root file system")
    if "ALL" not in (host.get("CapDrop") or []) or host.get("CapAdd"):
        problems.append("capabilities")
    options = host.get("SecurityOpt") or []
    if not any(str(o).startswith("no-new-privileges") for o in options):
        problems.append("no no-new-privileges")
    if any("unconfined" in str(o) for o in options):
        problems.append("an unconfined profile")
    if host.get("Privileged"):
        problems.append("privileged")
    problems += [
        f"the host's {key[:-4].lower()} namespace"
        for key in ("PidMode", "IpcMode", "UsernsMode", "UTSMode")
        if str(host.get(key) or "") == "host"
    ]
    tmpfs = host.get("Tmpfs") or {}
    if set(tmpfs) != {JOB_TMP} or "size=" not in str(tmpfs.get(JOB_TMP)):
        problems.append("no size-limited tmpfs as its only /tmp")
    return problems


def _container_problems(
    arbiter: Mapping[str, Any], name: str, inspect: Mapping[str, Any]
) -> list[str]:
    """A running job container's options against infra's fixed ones."""
    problems = _host_option_problems(inspect.get("HostConfig") or {})
    user = str((inspect.get("Config") or {}).get("User") or "")
    if user.split(":", maxsplit=1)[0] in ("", "0", "root"):
        problems.append("runs as root")
    read_only, read_write = _expected_mounts(arbiter, name.removeprefix("pinka-job-"))
    for mount in inspect.get("Mounts") or []:
        source, writable = str(mount.get("Source", "")), bool(mount.get("RW"))
        if source in read_write and writable:
            continue
        if any(source.startswith(r) for r in read_only) and not writable:
            continue
        problems.append(f"the mount {source}" + (" (read-write)" if writable else ""))
    return [f"{name}: {p}" for p in problems]


def check_training_containers(ctx: Context) -> Outcome:
    """A running training job's container has exactly infra's options and mounts."""
    arbiter = _arbiter(ctx)
    if not arbiter or not arbiter.get("training"):
        return skipped("this host does not train")
    names = ctx.system.run(
        ["docker", "ps", "--filter", "name=^pinka-job-", "--format", "{{.Names}}"]
    ).stdout.split()
    if not names:
        return skipped("no training run is running")
    problems = []
    for name in names:
        inspected = ctx.system.run(["docker", "inspect", name]).json()
        if not isinstance(inspected, list) or not inspected:
            problems.append(f"{name}: docker inspect gave nothing")
            continue
        problems += _container_problems(arbiter, name, inspected[0])
    if problems:
        return failed("; ".join(problems))
    return passed(f"{len(names)} running job container(s) with infra's options")


def _registered(ctx: Context, root: str, depth: int) -> list[str]:
    """Directories at ``depth`` below ``root``, without the arbiter's temporary ones."""
    found = [root]
    for _ in range(depth):
        found = [f"{d}/{n}" for d in found for n in ctx.system.listdir(d) if not n.startswith(".")]
    return found


def check_training_adapters(ctx: Context) -> Outcome:
    """Every registered adapter is read-only and its files match its hash and manifest."""
    arbiter = _arbiter(ctx)
    if not arbiter:
        return skipped(f"a {ctx.config.node_role} node has no GPU arbiter")
    directories = _registered(ctx, str(arbiter["adapters"]), 2)
    if not directories:
        return skipped("no adapter is registered")
    problems = []
    for directory in directories:
        digest = directory.rsplit("/", 1)[1]
        manifest = _json_file(ctx, f"{directory}/MANIFEST.json")
        if not SHA256_RE.fullmatch(digest) or not isinstance(manifest, dict):
            problems.append(f"{directory} is not a registered adapter")
            continue
        if manifest.get("sha256") != digest or ctx.system.info(f"{directory}/LINEAGE.json") is None:
            problems.append(f"{directory}: MANIFEST.json and LINEAGE.json do not match")
        actual = ctx.system.tree_digest(directory, ("MANIFEST.json", "LINEAGE.json"))
        if actual != digest:
            problems.append(f"{directory}: its files do not match its hash")
        info = ctx.system.info(directory)
        if info is None or info.owner != "root" or info.mode & 0o777 != REGISTERED_DIR_MODE:
            problems.append(f"{directory} is not root's and read-only")
    if problems:
        return failed("; ".join(problems))
    return passed(f"{len(directories)} adapter(s) registered with matching hashes")


def check_training_datasets(ctx: Context) -> Outcome:
    """Every published dataset matches its hash; none lists an instance of the test split."""
    arbiter = _arbiter(ctx)
    if not arbiter or not arbiter.get("training"):
        return skipped("this host does not train")
    directories = _registered(ctx, str(arbiter["datasets"]), 1)
    if not directories:
        return skipped("no dataset is published")
    problems = []
    for directory in directories:
        digest = directory.rsplit("/", 1)[1]
        provenance = _json_file(ctx, f"{directory}/PROVENANCE.json")
        if not SHA256_RE.fullmatch(digest) or not isinstance(provenance, dict):
            problems.append(f"{directory} has no PROVENANCE.json")
            continue
        if provenance.get("dataset_sha256") != digest:
            problems.append(f"{directory}: PROVENANCE.json names another dataset")
        tested = [
            str(i.get("id"))
            for i in provenance.get("instances") or []
            if isinstance(i, dict) and i.get("split") == FORBIDDEN_SPLIT
        ]
        if tested:
            problems.append(f"{directory} lists instances of the test split: {', '.join(tested)}")
        if ctx.system.tree_digest(directory, ("PROVENANCE.json",)) != digest:
            problems.append(f"{directory}: its files do not match its hash")
        info = ctx.system.info(directory)
        if (
            info is None
            or (info.owner, info.group) != ("root", DATASETS_GROUP)
            or info.mode & 0o777 != DATASET_DIR_MODE
        ):
            problems.append(f"{directory} is not root:{DATASETS_GROUP} 0750")
    if problems:
        return failed("; ".join(problems))
    return passed(f"{len(directories)} dataset(s) with provenance, none from the test split")


def _size(value: object) -> int:
    match = re.fullmatch(r"([0-9]+)([KMGT]?)", str(value))
    if match is None:
        return -1
    return int(match[1]) << (10 * "_KMGT".index(match[2] or "_"))


def check_training_caps(ctx: Context) -> Outcome:
    """The active platform's jobs stay within this host's training_caps."""
    arbiter = _arbiter(ctx)
    if not arbiter or not arbiter.get("training"):
        return skipped("this host does not train")
    jobs = (_rendered(ctx).get("training") or {}).get("jobs") or []
    if not jobs:
        return skipped("the active platform declares no training job")
    caps = arbiter.get("caps") or {}
    over = []
    for job in jobs:
        name = job.get("name")
        if "memory_max" in caps and _size(job.get("memory_max")) > _size(caps["memory_max"]):
            over.append(f"{name}: memory_max {job.get('memory_max')} > {caps['memory_max']}")
        if "cpus" in caps and float(job.get("cpus", 0)) > float(caps["cpus"]):
            over.append(f"{name}: cpus {job.get('cpus')} > {caps['cpus']}")
        if "timeout_seconds" in caps and int(job.get("timeout_seconds", 0)) > int(
            caps["timeout_seconds"]
        ):
            over.append(
                f"{name}: timeout_seconds {job.get('timeout_seconds')} > {caps['timeout_seconds']}"
            )
    if over:
        return failed("the GPU arbiter refuses every run of " + "; ".join(over))
    return passed(f"{len(jobs)} job(s) within this host's training_caps")


def check_training_availability(ctx: Context) -> Outcome:
    """Whether this host trains: only a full node does, where training is on."""
    if ctx.config.node_role in ("lab", "inference"):
        return passed(
            f"training is unavailable on this host (node_role {ctx.config.node_role}); it runs "
            "only on a full node, split-node training is future work"
        )
    if not _arbiter(ctx).get("training", True):
        return passed(
            "training is off on this host (training: off, or auto without a usable GPU; "
            "pinka preflight shows why)"
        )
    return passed("training runs on this host through the GPU arbiter")


def check_inference_isolation(ctx: Context) -> Outcome:
    """Only the inference API's clients reach vLLM or the forwarder on the loopback."""
    spec = ctx.config.get("inference_isolation", default={}) or {}
    if not spec.get("ports"):
        return skipped("this host has no inference guard to check")
    items = _loaded_table(ctx, INFERENCE_GUARD_TABLE)
    if items is None:
        return failed(f"the nftables table inet {INFERENCE_GUARD_TABLE} is not loaded")
    rendered = _rendered(ctx)
    clients = [*spec.get("clients", []), *rendered.get("inference_clients", [])]
    problems = _guard_set_problems(
        ctx,
        items,
        "inference",
        name="inference_clients",
        accounts=list(dict.fromkeys(clients)),
        ports=list(spec["ports"]),
    )
    if spec.get("control_ports"):
        control = ["root", *([rendered["controller"]] if rendered.get("controller") else [])]
        problems += _guard_set_problems(
            ctx,
            items,
            "inference",
            name="inference_control_clients",
            accounts=control,
            ports=list(spec["control_ports"]),
        )
    if problems:
        return failed("; ".join(problems))
    return passed(f"the inference guard admits only {', '.join(dict.fromkeys(clients))}")


# --- database and policy -------------------------------------------------------------------


def check_postgres(ctx: Context) -> Outcome:
    """Database roles exist, reader and Grafana cannot use schema restricted, durability is on."""
    if not ctx.config.runs_lab:
        return skipped("no PostgreSQL on this node")
    roles = list(ctx.config.get("postgres", "roles", default=[]))
    existing = psql(
        ctx,
        "SELECT coalesce(json_agg(rolname), '[]') FROM pg_roles "  # noqa: S608 - names checked
        f"WHERE rolname IN ({sql_names(roles)})",
    )
    problems = [f"role {r} missing" for r in roles if r not in (existing or [])]
    restricted = list(ctx.config.get("postgres", "restricted_roles", default=[]))
    database = str(ctx.config.get("postgres", "database", default="") or "")
    if database and not SQL_NAME_RE.match(database):
        return failed(f"database name {database!r} in the lab description is not a plain name")
    if restricted and database:
        reads = psql(
            ctx,
            "SELECT coalesce(json_object_agg(r, has_schema_privilege(r, 'restricted', 'USAGE')),"  # noqa: S608
            f" '{{}}') FROM unnest(ARRAY[{sql_names(restricted)}]) AS r WHERE EXISTS "
            "(SELECT 1 FROM pg_namespace WHERE nspname = 'restricted')",
            database=database,
        )
        problems += [
            f"{r} can use schema restricted" for r, allowed in (reads or {}).items() if allowed
        ]
    settings = psql(
        ctx,
        "SELECT json_object_agg(name, setting) FROM pg_settings WHERE name IN "
        "('fsync', 'synchronous_commit', 'full_page_writes', 'data_checksums')",
    )
    problems += [f"{k} is {v}" for k, v in sorted((settings or {}).items()) if v != "on"]
    if problems:
        return failed("; ".join(problems))
    return passed(
        f"{len(roles)} roles, schema restricted closed to {', '.join(restricted) or 'nobody'},"
        " durability on"
    )


def check_opa(ctx: Context) -> Outcome:
    """OPA answers health but rejects every policy and data write (C6)."""
    if not ctx.config.runs_lab:
        return skipped("no OPA on this node")
    base = str(ctx.config.get("opa", "url", default="http://127.0.0.1:8181"))
    if ctx.system.http("GET", f"{base}/health").status != 200:  # noqa: PLR2004
        return failed("/health does not answer 200")
    problems = []
    for method, path in (
        ("PUT", "/v1/policies/doctor"),
        ("PUT", "/v1/data/pinka/doctor"),
        ("GET", "/v1/policies"),
    ):
        status = ctx.system.http(
            method, base + path, body=b"{}" if method == "PUT" else None
        ).status
        if 200 <= status < 300:  # noqa: PLR2004
            problems.append(f"{method} {path} answered {status}")
    if problems:
        return failed("writes or listings are allowed: " + "; ".join(problems))
    return passed("health answers, writes and policy listings are rejected")


# --- telemetry -------------------------------------------------------------------------------


def _otlp_payloads(run_id: str, trace_id: str, now_ns: int) -> dict[str, dict[str, Any]]:
    resource = {
        "attributes": [
            {"key": "service.name", "value": {"stringValue": "pinka-doctor"}},
            {"key": "pinka.probe_id", "value": {"stringValue": run_id}},
        ]
    }
    return {
        "logs": {
            "resourceLogs": [
                {
                    "resource": resource,
                    "scopeLogs": [
                        {
                            "logRecords": [
                                {
                                    "timeUnixNano": str(now_ns),
                                    "severityText": "INFO",
                                    "body": {"stringValue": f"pinka doctor probe {run_id}"},
                                }
                            ]
                        }
                    ],
                }
            ]
        },
        "traces": {
            "resourceSpans": [
                {
                    "resource": resource,
                    "scopeSpans": [
                        {
                            "spans": [
                                {
                                    "traceId": trace_id,
                                    "spanId": trace_id[:16],
                                    "name": "pinka-doctor-probe",
                                    "kind": 1,
                                    "startTimeUnixNano": str(now_ns),
                                    "endTimeUnixNano": str(now_ns + 1_000_000),
                                }
                            ]
                        }
                    ],
                }
            ]
        },
        "metrics": {
            "resourceMetrics": [
                {
                    "resource": resource,
                    "scopeMetrics": [
                        {
                            "metrics": [
                                {
                                    "name": "pinka_doctor_probe",
                                    "gauge": {
                                        "dataPoints": [{"asInt": "1", "timeUnixNano": str(now_ns)}]
                                    },
                                }
                            ]
                        }
                    ],
                }
            ]
        },
    }


def check_telemetry(ctx: Context) -> Outcome:
    """A log record, a span and a metric sent to the collector arrive in the stores."""
    obs = ctx.config.get("observability", default={})
    if not obs:
        return skipped("no observability stack on this node")
    run_id = "doctor-" + uuid.uuid4().hex[:12]
    trace_id = uuid.uuid4().hex
    now = ctx.system.now()
    payloads = _otlp_payloads(run_id, trace_id, int(now.timestamp() * 1e9))
    for signal, payload in payloads.items():
        response = ctx.system.http(
            "POST", f"{obs['otlp_http']}/v1/{signal}", json.dumps(payload).encode()
        )
        if response.status != 200:  # noqa: PLR2004
            return failed(
                f"the collector rejected the {signal} probe ({response.status or response.body})"
            )
    start = int(now.timestamp()) - 60
    query = f'{{service_name="pinka-doctor"}} | pinka_probe_id="{run_id}"'
    pending = {
        "Loki": f"{obs['loki']}/loki/api/v1/query_range?start={start}&query=" + _quote(query),
        "Tempo": f"{obs['tempo']}/api/traces/{trace_id}",
        "Prometheus": f"{obs['prometheus']}/api/v1/query?query="
        + _quote(f'pinka_doctor_probe{{pinka_probe_id="{run_id}"}}'),
    }
    deadline = time.monotonic() + float(obs.get("wait_seconds", 60))
    while pending:
        for store, url in list(pending.items()):
            response = ctx.system.http("GET", url)
            if response.status == 200 and _found(store, response.json()):  # noqa: PLR2004
                del pending[store]
        if not pending or time.monotonic() > deadline:
            break
        time.sleep(float(obs.get("poll_seconds", 5)))
    if pending:
        return failed(f"probe {run_id} did not arrive in {', '.join(sorted(pending))}")
    return passed(f"probe {run_id} arrived in Loki, Tempo and Prometheus")


def _quote(text: str) -> str:
    from urllib.parse import quote  # noqa: PLC0415 - only needed here

    return quote(text, safe="")


def _found(store: str, data: Any) -> bool:  # noqa: ANN401 - JSON value
    if store == "Tempo":
        return bool(data)
    result = ((data or {}).get("data") or {}).get("result") or []
    return len(result) > 0


# --- time, backups, session -------------------------------------------------------------------


def check_time(ctx: Context) -> Outcome:
    """The clock is NTP-synchronized and its offset is small."""
    shown = ctx.system.run(["timedatectl", "show", "--property=NTPSynchronized", "--value"])
    if shown.stdout.strip() != "yes":
        return failed("the system clock is not NTP-synchronized")

    sync = detect_timesync(_PreflightRunner(ctx))
    limit = float(ctx.config.get("time", "max_offset_seconds", default=1.0))
    if sync.offset_s is None:
        return passed(f"NTP-synchronized ({sync.source or 'source unknown'}, offset unknown)")
    if abs(sync.offset_s) > limit:
        return failed(f"clock offset {sync.offset_s:+.3f} s exceeds {limit:g} s")
    return passed(f"NTP-synchronized, offset {sync.offset_s:+.3f} s")


class _PreflightRunner(Runner):
    """Lets preflight's detectors run their commands through the doctor's System."""

    def __init__(self, ctx: Context) -> None:
        self._system = ctx.system

    def run(self, argv: Sequence[str], timeout: float = 10.0) -> CommandResult:
        done = self._system.run(argv, timeout=timeout)
        return CommandResult(tuple(argv), done.returncode, done.stdout, done.stderr)


def check_backup(ctx: Context) -> Outcome:
    """Backups are configured, the last one succeeded recently, and the repository answers."""
    if not ctx.config.get("backup", "configured", default=False):
        return failed("no backup repository is configured (configure later)")
    text = ctx.system.read_text(
        str(ctx.config.get("backup", "state", default="/var/lib/pinka/backup/state.json"))
    )
    state = json.loads(text) if text else {}
    last = (state.get("backup") or {}).get("last_success")
    max_age = float(ctx.config.get("backup", "max_age_hours", default=26))
    if last is None:
        return failed("no backup has succeeded yet")
    age = (ctx.system.now().timestamp() - float(last)) / 3600
    if age > max_age:
        return failed(f"the last successful backup is {age:.0f} h old (limit {max_age:g} h)")
    helper = str(ctx.config.get("backup", "helper", default="/usr/local/libexec/pinka/backup"))
    if not ctx.system.run([helper, "snapshots"], timeout=120).ok:
        return failed("the repository does not answer")
    return passed(f"last backup {age:.1f} h ago, repository reachable")


def _rendered_platform(ctx: Context) -> Any:  # noqa: ANN401 - JSON
    path = str(ctx.config.get("paths", "rendered_platform", default=RENDERED_PLATFORM))
    text = ctx.system.read_text(path)
    try:
        return json.loads(text) if text else None
    except ValueError:
        return None


def check_backup_platform_state(ctx: Context) -> Outcome:
    """Every platform state directory is in the manifest's backup.paths."""
    rendered = _rendered_platform(ctx)
    if not isinstance(rendered, dict):
        return skipped("the platform is not deployed")
    state = [str(d) for d in rendered.get("state_dirs", [])]
    if not state:
        return skipped("the platform declares no state directories")
    backed_up = {str(p) for p in (rendered.get("backup") or {}).get("paths", [])}
    left_out = [d for d in state if d not in backed_up]
    if left_out:
        return failed(
            "not backed up (the manifest's backup.paths leaves them out): " + ", ".join(left_out)
        )
    return passed(f"{len(state)} state director{'y' if len(state) == 1 else 'ies'} backed up")


def check_health_gate(ctx: Context) -> Outcome:
    """The startup health gate passed."""
    text = ctx.system.read_text(
        str(ctx.config.get("paths", "health_gate_status", default="/run/pinka/health-gate.json"))
    )
    if not text:
        return failed("the health gate has not run since boot")
    status = json.loads(text)
    if status.get("passed"):
        return passed(f"passed at {status.get('checked_at')}")
    failing = [c["name"] for c in status.get("checks", []) if not c.get("ok")]
    return failed("failing: " + ", ".join(failing))


def _boot_id(ctx: Context) -> str:
    return (ctx.system.read_text("/proc/sys/kernel/random/boot_id") or "").strip()


def check_session(ctx: Context) -> Outcome:
    """This boot's session record exists."""
    sessions = str(ctx.config.get("paths", "sessions", default="/var/lib/pinka/sessions"))
    boot_id = _boot_id(ctx)
    text = ctx.system.read_text(f"{sessions}/{boot_id}.json")
    if not text:
        return failed(f"no session record for boot {boot_id}")
    record = json.loads(text)
    missing = sorted(record.get("errors", {}))
    if missing:
        return failed("recorded without " + ", ".join(missing))
    return passed(f"session {boot_id} recorded at {record.get('started_at')}")


def check_rolling_drift(ctx: Context) -> Outcome:
    """On a rolling release, the running system still matches the session's fingerprint."""
    if _profile(ctx).get("release_model") != "rolling":
        return skipped("not a rolling release")
    sessions = str(ctx.config.get("paths", "sessions", default="/var/lib/pinka/sessions"))
    text = ctx.system.read_text(f"{sessions}/{_boot_id(ctx)}.json")
    recorded = (json.loads(text) if text else {}).get("fingerprint_hash")
    if not recorded:
        return skipped("no session fingerprint to compare with")
    from pinka_lab.fingerprint import compute  # noqa: PLC0415 - avoids an import cycle

    current = compute(ctx.system, ctx.config)["hash"]
    if current != recorded:
        return failed(f"fingerprint {current} differs from the session's {recorded}")
    return passed("unchanged since the session started")


# --- resources -------------------------------------------------------------------------------


def parse_cpuset(spec: str) -> set[int]:
    """The CPUs of a Linux cpuset list such as ``0-5,8`` (empty: none).

    Args:
        spec: The list, as in host_vars or a cgroup's ``cpuset.cpus.effective``.

    Returns:
        The CPU numbers.

    Raises:
        ValueError: If the list is malformed.
    """
    cpus: set[int] = set()
    for part in spec.replace(" ", "").strip().split(","):
        if not part:
            continue
        first, _, last = part.partition("-")
        low, high = int(first), int(last or first)
        if low > high:
            raise ValueError(f"reversed range {part!r}")
        cpus.update(range(low, high + 1))
    return cpus


def format_cpuset(cpus: set[int]) -> str:
    """A compact cpuset list (``0-3,8``).

    Args:
        cpus: CPU numbers.

    Returns:
        The list.
    """
    ranges: list[str] = []
    for cpu in sorted(cpus):
        if ranges and ranges[-1].split("-")[-1] == str(cpu - 1):
            ranges[-1] = ranges[-1].split("-")[0] + f"-{cpu}"
        else:
            ranges.append(str(cpu))
    return ",".join(ranges)


def _smt_split(ctx: Context, services: set[int], targets: set[int]) -> list[str]:
    """Cores whose SMT siblings fall into different sets (P3a): a warning."""
    split: set[tuple[int, ...]] = set()
    for cpu in sorted(services | targets):
        path = f"/sys/devices/system/cpu/cpu{cpu}/topology/thread_siblings_list"
        try:
            core = parse_cpuset((ctx.system.read_text(path) or "").strip())
        except ValueError:
            continue
        if core & services and core & targets:
            split.add(tuple(sorted(core)))
    if not split:
        return []
    cores = "; ".join(format_cpuset(set(core)) for core in sorted(split))
    return [
        f"SMT siblings of one core are split between cpuset_services and cpuset_targets "
        f"({cores}): target measurements share cores with the lab services"
    ]


def check_cpusets(ctx: Context) -> Outcome:
    """The lab services run on cpuset_services, and both CPU sets exist on this host."""
    services = str(ctx.config.get("cpusets", "services", default="") or "")
    targets = str(ctx.config.get("cpusets", "targets", default="") or "")
    if not services and not targets:
        return skipped("no CPU sets configured (cpuset_services, cpuset_targets)")
    online = parse_cpuset((ctx.system.read_text("/sys/devices/system/cpu/online") or "").strip())
    problems = []
    for name, spec in (("cpuset_services", services), ("cpuset_targets", targets)):
        missing = parse_cpuset(spec) - online
        if missing:
            problems.append(f"{name} names CPUs this host does not have: {format_cpuset(missing)}")
    problems += _smt_split(ctx, parse_cpuset(services) & online, parse_cpuset(targets) & online)
    if services:
        slice_name = str(ctx.config.get("cpusets", "slice", default="pinka-services.slice"))
        path = f"/sys/fs/cgroup/{slice_name}/cpuset.cpus.effective"
        effective = ctx.system.read_text(path)
        wanted = parse_cpuset(services) & online
        if effective is None:
            problems.append(f"{slice_name} has no cgroup (no lab service is running)")
        elif parse_cpuset(effective.strip()) != wanted:
            problems.append(
                f"{slice_name} runs on CPUs {effective.strip() or 'none'}, "
                f"expected {format_cpuset(wanted)}"
            )
    if problems:
        return failed("; ".join(problems))
    return passed(f"services on CPUs {services or 'any'}, targets on CPUs {targets or 'any'}")


# --- WSL ---------------------------------------------------------------------------------------


def check_wsl(ctx: Context) -> Outcome:
    """WSL: wsl.conf as managed, effective memory and CPUs as expected, /dev/kvm present."""
    if ctx.config.provider != "wsl2":
        return skipped("not a WSL host")
    problems = []
    conf = ctx.system.read_text("/etc/wsl.conf") or ""
    for section, key, wanted in (
        ("interop", "enabled", "false"),
        ("automount", "enabled", "false"),
        ("boot", "systemd", "true"),
    ):
        if not re.search(rf"\[{section}\][^\[]*\b{key}\s*=\s*{wanted}\b", conf, flags=re.S):
            problems.append(f"wsl.conf [{section}] {key} is not {wanted}")
    meminfo = ctx.system.read_text("/proc/meminfo") or ""
    match = re.search(r"^MemTotal:\s+(\d+) kB", meminfo, flags=re.M)
    memory_gib = int(match.group(1)) / 2**20 if match else 0.0
    expected_memory = ctx.config.get("wsl", "memory_gib")
    if expected_memory and memory_gib < float(expected_memory) * 0.95:
        problems.append(f"{memory_gib:.1f} GiB memory, expected {expected_memory} GiB (.wslconfig)")
    cpus = ctx.system.run(["nproc"]).stdout.strip()
    expected_cpus = ctx.config.get("wsl", "cpus")
    if expected_cpus and cpus.isdigit() and int(cpus) < int(expected_cpus):
        problems.append(f"{cpus} CPUs, expected {expected_cpus} (.wslconfig)")
    if ctx.config.get("wsl", "kvm_expected", default=False) and ctx.system.info("/dev/kvm") is None:
        problems.append("/dev/kvm is missing (nested virtualization off)")
    if problems:
        return failed("; ".join(problems))
    return passed(f"wsl.conf as managed, {memory_gib:.1f} GiB, {cpus} CPUs")


# --- install record ------------------------------------------------------------------------


def check_install_record(ctx: Context) -> Outcome:
    """The install record is present, readable and only root can change it."""
    path = str(ctx.config.get("paths", "install_record", default=INSTALL_RECORD))
    if ctx.system.read_text(path) is None:
        return failed(f"{path} is missing")
    record = _install_record(ctx)
    if record is None:
        return failed(f"{path} is not a JSON object")
    missing = [k for k in ("created_at", "legacy", "preexisting", "added") if k not in record]
    if missing:
        return failed(f"{path} lacks {', '.join(missing)}")
    found = ctx.system.info(path)
    if found is None or found.owner != "root" or found.mode & WRITABLE_BY_OTHERS:
        shown = "unknown" if found is None else f"{found.owner} {found.mode:04o}"
        return failed(f"{path} is {shown}; only root may change it")
    kind = "first install"
    if record["legacy"]:
        kind = "legacy (pinka provisioned this host before the record existed)"
    return passed(f"{path} from {record['created_at']}, {kind}")


# --- registry ----------------------------------------------------------------------------------

CHECKS: tuple[Check, ...] = (
    Check(
        "install.record",
        "The install record is present, readable and only root can change it.",
        "error",
        "Run bootstrap: it writes a missing record (a legacy one on a host pinka provisioned "
        "before); never edit the record by hand.",
        check_install_record,
    ),
    Check(
        "preflight.outcome",
        "A fresh read-only preflight still allows the lab on this host.",
        "error",
        "Run `pinka preflight` and fix what it reports; see the support matrix.",
        check_preflight_outcome,
    ),
    Check(
        "preflight.drift",
        "Outcome, provider and features match /etc/pinka/host-profile.json.",
        "warning",
        "If the change is intended, run bootstrap with --refresh-profile.",
        check_preflight_drift,
    ),
    Check(
        "services.units",
        "Every unit of pinka-lab.target is active.",
        "error",
        "`systemctl status UNIT` and `journalctl -u UNIT`; `pinka start`.",
        check_units,
    ),
    Check(
        "services.containers",
        "Lab containers run healthy with their pinned digests.",
        "error",
        "Run bootstrap again; it recreates containers from the pinned digests.",
        check_containers,
    ),
    Check(
        "network.listeners",
        "Nothing listens outside loopback except sshd, expected host services and the "
        "listeners the host had before pinka.",
        "error",
        "Stop the listed service or bind it to 127.0.0.1.",
        check_listeners,
    ),
    Check(
        "network.install_path",
        "The install's access path from outside the tailnet is closed.",
        "warning",
        "Give consent to close it (firewall_close_install_path: true, or the Remote access "
        "screen), run bootstrap and, while it runs, log in as a researcher over the tailnet; "
        "the run closes the path at its end.",
        check_install_path,
    ),
    Check(
        "network.firewall",
        "The lab's firewall, its access guard and the Docker compatibility rules are present.",
        "error",
        "Run bootstrap with --tags firewall.",
        check_firewall,
    ),
    Check(
        "network.tailscale",
        "Tailscale is logged in with the expected name and tags; Funnel and Tailscale SSH off.",
        "error",
        "`tailscale up` per the Tailscale how-to (on an adopted node the owner restores the "
        "recorded name and tags); `tailscale funnel reset` removes Funnel, "
        "`tailscale set --ssh=false` Tailscale SSH.",
        check_tailscale,
    ),
    Check(
        "network.tailscale_identity",
        "An adopted Tailscale node carries the lab's tag and a key that does not expire.",
        "warning",
        "Tag the node in the admin console or disable its key expiry; otherwise name it in the "
        "tailnet ACL another way and sign in again when the key expires.",
        check_tailscale_identity,
    ),
    Check(
        "gpu.docker",
        "A container of the pinned CUDA image sees the GPU.",
        "error",
        "Check the driver (`nvidia-smi`) and run bootstrap with --tags nvidia_container_toolkit.",
        check_gpu,
    ),
    Check(
        "gpu.cdi",
        "Docker gives containers the GPU through CDI.",
        "error",
        'Remove `"features": {"cdi": false}` from /etc/docker/daemon.json (on Docker older than '
        '28.2 add `"features": {"cdi": true}`) and restart Docker; on a Docker pinka installed, '
        "run bootstrap with --tags docker.",
        check_docker_cdi,
    ),
    Check(
        "gpu.driver_cuda",
        "The NVIDIA driver supports the CUDA version the pinned vLLM image is built on.",
        "error",
        "Install a driver branch that supports that CUDA version; on WSL2 update the NVIDIA "
        "driver on Windows (never a Linux driver inside WSL).",
        check_driver_cuda,
    ),
    Check(
        "gpu.vllm",
        "vLLM answers /health when a deployment is active.",
        "error",
        "`journalctl -u pinka-inference.service`; reactivate the deployment.",
        check_vllm,
    ),
    Check(
        "gpu.arbiter",
        "The GPU arbiter's socket belongs to the controller (or the control endpoint); its mode "
        "and transition log are sound.",
        "error",
        "Run bootstrap with --tags gpu_arbiter; `journalctl -u 'pinka-gpu-*'` shows the arbiter.",
        check_gpu_arbiter,
    ),
    Check(
        "training.containers",
        "A running training job's container has infra's fixed options and only its mounts.",
        "error",
        "Stop the run (`sudo pinka gpu idle`) and run bootstrap with --tags gpu_arbiter.",
        check_training_containers,
    ),
    Check(
        "training.adapters",
        "Registered adapters are read-only and match their hash, MANIFEST.json and LINEAGE.json.",
        "error",
        "Remove the damaged adapter directory and train it again; never edit a registered one.",
        check_training_adapters,
    ),
    Check(
        "training.datasets",
        "Published datasets match their hash and list no instance of the test split.",
        "error",
        "Remove the dataset directory and publish it again from the platform.",
        check_training_datasets,
    ),
    Check(
        "training.caps",
        "The active platform's training jobs stay within this host's training_caps.",
        "warning",
        "Raise training_caps in host_vars (and run bootstrap with --tags gpu_arbiter,doctor), "
        "or deploy a release whose jobs ask for less.",
        check_training_caps,
    ),
    Check(
        "training.availability",
        "Whether this host trains: only a full node does.",
        "warning",
        "Training needs a full node; split-node training is future work.",
        check_training_availability,
    ),
    Check(
        "inference.isolation",
        "Only root, infra's scrapers and the platform's controller and inference broker reach "
        "the inference API on the loopback; only the controller the forwarder's control port.",
        "error",
        "Run bootstrap with --tags firewall,platform (`systemctl restart "
        "pinka-inference-guard.service` reloads it).",
        check_inference_isolation,
    ),
    Check(
        "lab.window",
        "Whether a maintenance window is recorded, and until when.",
        "warning",
        "Production: `sudo pinka window close` once staging is done, then `sudo pinka start`. "
        "Staging: open a window on both sides or close the stale record.",
        check_window,
    ),
    Check(
        "docs.service",
        "The docs service serves the installed documentation release.",
        "warning",
        "Install the release (`sudo pinka docs install TARBALL --sha256 SUM`, or bootstrap with "
        "docs_release set), then `sudo systemctl start pinka-docs.service`.",
        check_docs,
    ),
    Check(
        "inference.pairing",
        "Lab and inference nodes: certificate valid, private key root-only on a lab node, paired "
        "with the node the host_vars name.",
        "error",
        "Pair the nodes and set the peer in host_vars (pair-inference-node how-to).",
        check_pairing,
    ),
    Check(
        "inference.split_guard",
        "Lab and inference nodes: the split-inference ports on the tailnet admit only the paired "
        "peer's address, nobody before a pairing.",
        "error",
        "Pair the nodes again if the record has no address, then run bootstrap with --tags "
        "firewall (`systemctl restart pinka-split-guard.service` reloads it).",
        check_split_guard,
    ),
    Check(
        "disk.free_space",
        "Each mount point the lab uses has free space left, and its trend does not fill it soon.",
        "warning",
        "Free space or lower the retention settings; `pinka preflight` shows the growth.",
        check_disk_free_space,
    ),
    Check(
        "incus.pool",
        "The storage pool has the chosen backend and enough free space.",
        "warning",
        "Delete unused instances and images or grow the pool.",
        check_incus_pool,
    ),
    Check(
        "incus.objects",
        "The bench and build projects and the lab's profiles exist.",
        "error",
        "Run bootstrap with --tags incus.",
        check_incus_objects,
    ),
    Check(
        "incus.certificates",
        "Service client certificates exist, are owned by the service and trusted.",
        "error",
        "Run bootstrap with --tags incus, or rotate.yml --tags incus.",
        check_incus_certificates,
    ),
    Check(
        "incus.metrics",
        "Prometheus scrapes the Incus metrics endpoint.",
        "warning",
        "Check the metrics certificate and the Prometheus target page.",
        check_incus_metrics,
    ),
    Check(
        "incus.events",
        "The Incus event feed into the journal runs.",
        "warning",
        "`systemctl restart pinka-incus-events.service`.",
        check_incus_events,
    ),
    Check(
        "layout.tree",
        "Lab directories have the layout's owners, modes and default ACLs.",
        "error",
        "Run bootstrap with --tags layout.",
        check_layout,
    ),
    Check(
        "accounts.groups",
        "No researcher in docker or the Incus admin group; no service account in docker.",
        "error",
        "`gpasswd -d USER GROUP`, or list the admin in break_glass_docker.",
        check_accounts,
    ),
    Check(
        "deploy.provenance",
        "No .git in deployed paths; restricted packages only in the restricted area.",
        "error",
        "Remove the deployment and deploy again from the mirror.",
        check_deployed_paths,
    ),
    Check(
        "platform.manifest",
        "The active release's manifest validates; active.json and its provenance record its hash.",
        "error",
        "Deploy the release again with bootstrap (--tags platform); never edit a deployed release.",
        check_platform_manifest,
    ),
    Check(
        "systemd.hardening",
        "Platform units carry the hardening of their profiles; infra's hardened units theirs.",
        "error",
        "Run bootstrap with --tags platform.",
        check_hardening,
    ),
    Check(
        "sockets.permissions",
        "Platform sockets have the owners, modes and ACLs of their rendering.",
        "error",
        "Restart the owning service; run bootstrap with --tags platform.",
        check_sockets,
    ),
    Check(
        "sockets.agent_view",
        "The running agent sees no socket beyond its allowlist inside its mount namespace, and "
        "of the release only its own package and environment.",
        "error",
        "Run bootstrap with --tags platform; if a socket stays visible, report it as a bug.",
        check_agent_view,
    ),
    Check(
        "sockets.acl_fallback",
        "No platform socket fell back to mode 0666 for want of ACL support on /run.",
        "warning",
        "Enable POSIX ACLs on the /run tmpfs (kernel CONFIG_TMPFS_POSIX_ACL), then restart the "
        "platform's sockets (`sudo pinka stop`, `sudo pinka start`).",
        check_socket_acl_fallback,
    ),
    Check(
        "telemetry.isolation",
        "Only infra's own clients reach the telemetry stores; the query socket's link, its "
        "directory and the query and agent sockets are right.",
        "error",
        "Run bootstrap with --tags firewall,observability.",
        check_telemetry_isolation,
    ),
    Check(
        "postgres.roles",
        "Roles exist, reader and Grafana cannot use schema restricted, durability is on.",
        "error",
        "Run bootstrap with --tags postgres.",
        check_postgres,
    ),
    Check(
        "opa.authz",
        "OPA answers health and rejects policy and data writes (C6).",
        "error",
        "Run bootstrap with --tags opa.",
        check_opa,
    ),
    Check(
        "telemetry.roundtrip",
        "A probe log, span and metric arrive in Loki, Tempo and Prometheus.",
        "error",
        "`journalctl -u pinka-observability.service`; check the collector's logs.",
        check_telemetry,
    ),
    Check(
        "time.sync",
        "The clock is NTP-synchronized with a small offset.",
        "error",
        "Check timesyncd or chrony; on WSL run `wsl --shutdown` after resume.",
        check_time,
    ),
    Check(
        "backup.recent",
        "Backups are configured, recent and the repository answers.",
        "warning",
        "Configure the repository (backup how-to) or check `journalctl -u pinka-backup.service`.",
        check_backup,
    ),
    Check(
        "backup.platform_state",
        "Every platform state directory is listed in the manifest's backup.paths.",
        "warning",
        "Add the directory to backup.paths in the platform's manifest and deploy the release, "
        "or accept that it is not backed up.",
        check_backup_platform_state,
    ),
    Check(
        "lab.health_gate",
        "The startup health gate passed.",
        "error",
        "`journalctl -u pinka-health-gate.service`; fix the failing check and `pinka start`.",
        check_health_gate,
    ),
    Check(
        "lab.session",
        "This boot's session record exists and is complete.",
        "warning",
        "`systemctl restart pinka-session-start.service` after fixing the recorded errors.",
        check_session,
    ),
    Check(
        "lab.rolling_drift",
        "On a rolling release, the system matches the session's fingerprint.",
        "warning",
        "Use `pinka update-host` for updates; reports show the fingerprint diff.",
        check_rolling_drift,
    ),
    Check(
        "resources.cpusets",
        "Lab services run on cpuset_services, both CPU sets exist on this host, and no core's "
        "SMT siblings are split between them.",
        "warning",
        "Set cpuset_services and cpuset_targets in host_vars to CPUs the host has (on WSL: "
        "processors= in .wslconfig) and run bootstrap; then restart the lab "
        "(`sudo pinka stop`, `sudo pinka start`) so services move to their CPUs.",
        check_cpusets,
    ),
    Check(
        "wsl.config",
        "WSL: wsl.conf, memory, CPUs and /dev/kvm as expected.",
        "warning",
        "Copy the recommended .wslconfig on Windows and run `wsl --shutdown`.",
        check_wsl,
    ),
)
