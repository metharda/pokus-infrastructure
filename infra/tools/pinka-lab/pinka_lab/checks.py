"""The doctor checks (BUILD_PROMPT §9, contracts.md).

Each check reads what it compares against from the lab description
(``/etc/pinka/lab.json``) and asks the host through :class:`System`. Checks
that concern a part the lab does not have (no GPU, no platform yet, another
provider) are skipped, not passed.
"""

from __future__ import annotations

import json
import re
import ssl
import time
import uuid
from collections.abc import Sequence
from typing import Any

from pinka_lab.doctor import Check, Context, Outcome, failed, passed, skipped
from pinka_preflight.detect.timesync import detect_timesync
from pinka_preflight.profile_tool import differences
from pinka_preflight.runner import CommandResult, Runner

PSQL_SCRIPT = (
    'PGPASSWORD="$(cat /run/secrets/postgres-superuser)" exec psql --no-psqlrc '
    "--set=ON_ERROR_STOP=1 --host=/var/run/postgresql --username=postgres --dbname=potka "
    '"$@"'
)
KEY_MODE = 0o600
SQL_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
PROCESS_RE = re.compile(r'\(\("([^"]+)"')
LOOPBACK_V4 = "127."


# --- helpers ---------------------------------------------------------------------------


def _profile(ctx: Context) -> dict[str, Any]:
    if "profile" not in ctx.memo:
        path = str(ctx.config.get("paths", "host_profile", default="/etc/pinka/host-profile.json"))
        text = ctx.system.read_text(path)
        ctx.memo["profile"] = json.loads(text) if text else {}
    profile: dict[str, Any] = ctx.memo["profile"]
    return profile


def _preflight(ctx: Context) -> dict[str, Any] | None:
    """Run preflight once (read-only) and keep its report."""
    if "preflight" not in ctx.memo:
        command = str(ctx.config.get("paths", "preflight"))
        done = ctx.system.run(
            [command, "--no-write", "--quiet", "--srv-path", ctx.config.pinka_root], timeout=300
        )
        parsed = done.json() if done.returncode in (0, 10, 20, 30) else None
        ctx.memo["preflight"] = parsed if isinstance(parsed, dict) else None
    report: dict[str, Any] | None = ctx.memo["preflight"]
    return report


def psql(ctx: Context, query: str) -> Any:  # noqa: ANN401 - JSON value
    """Run a query as the database superuser inside the PostgreSQL container.

    Args:
        ctx: Check context.
        query: SQL returning one JSON value.

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


def listeners(ss_output: str) -> list[tuple[str, str, int, str]]:
    """Parse ``ss -H -lntup`` output.

    Args:
        ss_output: The command's output.

    Returns:
        ``(protocol, address, port, process)`` per listening socket.
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
            (fields[0], address, int(port) if port.isdigit() else 0, names[0] if names else "")
        )
    return found


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
    """Every unit of pinka-lab.target is active."""
    units = list(ctx.config.get("units", default=[]))
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
    """Nothing listens outside loopback except sshd and the expected host services."""
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
    unexpected = sorted(
        {
            f"{proto} {address}:{port} ({process or 'unknown process'})"
            for proto, address, port, process in listeners(done.stdout)
            if not is_loopback(address)
            and process not in allowed_processes
            and address not in allowed_addresses
            and not (address in tailnet and port in tailnet_ports)
        }
    )
    if unexpected:
        return failed("unexpected listeners: " + ", ".join(unexpected))
    return passed("only loopback listeners and expected host services")


def check_firewall(ctx: Context) -> Outcome:
    """The lab's firewall rules are in place (nftables table, Docker compatibility rules)."""
    backend = str(ctx.config.get("firewall", "backend", default="nftables"))
    problems = []
    if backend == "nftables":
        table = ctx.system.run(["nft", "list", "table", "inet", "pinka_filter"])
        if not table.ok:
            problems.append("nftables table inet pinka_filter is missing")
    elif backend == "firewalld":
        zone = ctx.system.run(["firewall-cmd", "--info-zone", "pinka"])
        if not zone.ok:
            problems.append("firewalld zone pinka is missing")
    elif backend == "ufw":
        status = ctx.system.run(["ufw", "status"])
        if "Status: active" not in status.stdout:
            problems.append("ufw is not active")
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
    """Tailscale is logged in with the expected host name and tag, and Funnel is off."""
    if not ctx.config.get("tailscale", "enabled", default=True):
        return skipped("Tailscale is disabled in this lab")
    status = ctx.system.run(["tailscale", "status", "--json"]).json() or {}
    problems = []
    if status.get("BackendState") != "Running":
        problems.append(f"backend state {status.get('BackendState', 'unknown')}")
    me = status.get("Self") or {}
    hostname = ctx.config.get("tailscale", "hostname", default=ctx.config.machine)
    if me.get("HostName") != hostname:
        problems.append(f"host name {me.get('HostName')!r}, expected {hostname!r}")
    tag = ctx.config.get("tailscale", "tag")
    if tag and tag not in (me.get("Tags") or []):
        problems.append(f"tag {tag} missing")
    serve = ctx.system.run(["tailscale", "serve", "status", "--json"]).json() or {}
    if isinstance(serve, dict) and serve.get("AllowFunnel"):
        problems.append("Funnel is enabled")
    if problems:
        return failed("; ".join(problems))
    return passed(f"logged in as {hostname} with {tag}, Funnel off")


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
    try:
        record = json.loads(ctx.system.read_text(f"{directory}/peer.json") or "{}")
    except ValueError:
        record = {}
    paired = str(record.get("node", "")) if isinstance(record, dict) else ""
    if not configured and not problems:
        if paired:
            return failed(f"paired with {paired}, but {variable} is not set in the host_vars")
        return skipped("not paired yet")
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
    """Researchers are not in docker or the Incus admin group, service accounts not in docker."""
    privileged = list(ctx.config.get("accounts", "privileged_groups", default=["docker"]))
    break_glass = set(ctx.config.get("accounts", "break_glass_docker", default=[]))
    problems = []
    for user in ctx.config.get("accounts", "researchers", default=[]):
        for group in ctx.system.user_groups(user) or []:
            if group in privileged and not (group == "docker" and user in break_glass):
                problems.append(f"researcher {user} is in {group}")
    for user in ctx.config.get("accounts", "service_accounts", default=[]):
        if "docker" in (ctx.system.user_groups(user) or []):
            problems.append(f"service account {user} is in docker")
    if problems:
        return failed("; ".join(problems))
    note = f" (break-glass: {', '.join(sorted(break_glass))})" if break_glass else ""
    return passed("no researcher or service account in a privileged group" + note)


def check_deployed_paths(ctx: Context) -> Outcome:
    """No .git in deployed paths and no catalog content outside the catalog directory (B2)."""
    root = ctx.config.pinka_root
    problems = []
    for top in (f"{root}/deploy", f"{root}/catalog"):
        for directory in ctx.system.walk_dirs(top, max_depth=8):
            if directory.rsplit("/", 1)[-1] == ".git":
                problems.append(f"{directory} exists")
    for directory in ctx.system.walk_dirs(f"{root}/deploy", max_depth=3):
        text = ctx.system.read_text(f"{directory}/DEPLOYED_FROM.json")
        provenance = json.loads(text) if text else {}
        if provenance.get("component") == "catalog":
            problems.append(f"catalog deployed in {directory}")
    if problems:
        return failed("; ".join(problems))
    return passed("no .git in deployed paths, catalog only in its own directory")


def check_hardening(ctx: Context) -> Outcome:
    """Platform units carry their systemd hardening (InaccessiblePaths of the agent runner)."""
    units = list(ctx.config.get("hardening", default=[]))
    if not units:
        return skipped("the platform is not deployed")
    problems = []
    for unit in units:
        props = [*unit.get("properties", {}), "InaccessiblePaths"]
        done = ctx.system.run(["systemctl", "show", unit["unit"], "--property=" + ",".join(props)])
        values = dict(line.split("=", 1) for line in done.stdout.splitlines() if "=" in line)
        for name, wanted in unit.get("properties", {}).items():
            if values.get(name) != wanted:
                problems.append(f"{unit['unit']}: {name}={values.get(name)!r}, expected {wanted!r}")
        # systemd prefixes paths that may be missing with "-"; hyphens inside paths stay.
        paths = {p.lstrip("-") for p in values.get("InaccessiblePaths", "").split()}
        missing = [p for p in unit.get("inaccessible_paths", []) if p not in paths]
        if missing:
            problems.append(f"{unit['unit']}: InaccessiblePaths lacks {', '.join(missing)}")
    if problems:
        return failed("; ".join(problems))
    return passed(f"{len(units)} unit(s) hardened as required")


def check_sockets(ctx: Context) -> Outcome:
    """Platform sockets have the contract's owners, modes and ACLs (C8)."""
    sockets = list(ctx.config.get("sockets", default=[]))
    if not sockets:
        return skipped("the platform is not deployed")
    problems = []
    for entry in sockets:
        info = ctx.system.info(entry["path"])
        if info is None:
            problems.append(f"{entry['path']} missing")
            continue
        # The platform role records the effective mode and ACL: with ACL entries
        # the group bits show the mask.
        mode = entry.get("expect_mode", entry.get("mode"))
        want = (entry["owner"], entry["group"], int(str(mode), 8))
        if (info.owner, info.group, info.mode) != want:
            problems.append(f"{entry['path']} is {info.owner}:{info.group} {info.mode:04o}")
        acls = entry.get("expect_acl", entry.get("acl")) or []
        if acls:
            done = ctx.system.run(["getfacl", "--omit-header", "--absolute-names", entry["path"]])
            lines = {line.split()[0] for line in done.stdout.splitlines() if line.strip()}
            for acl in acls:
                if acl not in lines:
                    problems.append(f"{entry['path']} lacks ACL {acl}")
    if problems:
        return failed("; ".join(problems))
    return passed(f"{len(sockets)} socket(s) as in the contract")


# --- database and policy -------------------------------------------------------------------


def check_postgres(ctx: Context) -> Outcome:
    """Database roles exist, the reader and Grafana roles cannot read gt, durability is on."""
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
    if restricted:
        reads = psql(
            ctx,
            "SELECT coalesce(json_object_agg(r, has_schema_privilege(r, 'gt', 'USAGE')), '{}') "  # noqa: S608
            f"FROM unnest(ARRAY[{sql_names(restricted)}]) AS r WHERE EXISTS "
            "(SELECT 1 FROM pg_namespace WHERE nspname = 'gt')",
        )
        problems += [f"{r} can use schema gt" for r, allowed in (reads or {}).items() if allowed]
    settings = psql(
        ctx,
        "SELECT json_object_agg(name, setting) FROM pg_settings WHERE name IN "
        "('fsync', 'synchronous_commit', 'full_page_writes', 'data_checksums')",
    )
    problems += [f"{k} is {v}" for k, v in sorted((settings or {}).items()) if v != "on"]
    if problems:
        return failed("; ".join(problems))
    return passed(
        f"{len(roles)} roles, gt closed to {', '.join(restricted) or 'nobody'}, durability on"
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
            {"key": "potka.run_id", "value": {"stringValue": run_id}},
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
    query = f'{{service_name="pinka-doctor"}} | potka_run_id="{run_id}"'
    pending = {
        "Loki": f"{obs['loki']}/loki/api/v1/query_range?start={start}&query=" + _quote(query),
        "Tempo": f"{obs['tempo']}/api/traces/{trace_id}",
        "Prometheus": f"{obs['prometheus']}/api/v1/query?query="
        + _quote(f'pinka_doctor_probe{{potka_run_id="{run_id}"}}'),
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
        return failed("no backup repository is configured (owner answer E4: configure later)")
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


# --- registry ----------------------------------------------------------------------------------

CHECKS: tuple[Check, ...] = (
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
        "Nothing listens outside loopback except sshd and expected host services.",
        "error",
        "Stop the listed service or bind it to 127.0.0.1.",
        check_listeners,
    ),
    Check(
        "network.firewall",
        "The lab's firewall and Docker compatibility rules are present.",
        "error",
        "Run bootstrap with --tags firewall.",
        check_firewall,
    ),
    Check(
        "network.tailscale",
        "Tailscale is logged in with the expected name and tag, Funnel off.",
        "error",
        "`tailscale up` per the Tailscale how-to; `tailscale funnel reset` removes Funnel.",
        check_tailscale,
    ),
    Check(
        "gpu.docker",
        "A container of the pinned CUDA image sees the GPU.",
        "error",
        "Check the driver (`nvidia-smi`) and run bootstrap with --tags nvidia_container_toolkit.",
        check_gpu,
    ),
    Check(
        "gpu.vllm",
        "vLLM answers /health when a deployment is active.",
        "error",
        "`journalctl -u pinka-inference.service`; reactivate the deployment.",
        check_vllm,
    ),
    Check(
        "inference.pairing",
        "Lab and inference nodes: certificate valid, paired with the node the host_vars name.",
        "error",
        "Pair the nodes and set the peer in host_vars (pair-inference-node how-to).",
        check_pairing,
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
        "No .git in deployed paths; catalog content only in the catalog directory (B2).",
        "error",
        "Remove the deployment and deploy again from the mirror.",
        check_deployed_paths,
    ),
    Check(
        "systemd.hardening",
        "Platform units carry their hardening (agent runner InaccessiblePaths).",
        "error",
        "Run bootstrap with --tags platform.",
        check_hardening,
    ),
    Check(
        "sockets.permissions",
        "Platform sockets have the contract's owners, modes and ACLs (C8).",
        "error",
        "Restart the owning service; run bootstrap with --tags platform.",
        check_sockets,
    ),
    Check(
        "postgres.roles",
        "Roles exist, reader and Grafana cannot read gt, durability is on.",
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
        "Lab services run on cpuset_services, and both CPU sets exist on this host.",
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
