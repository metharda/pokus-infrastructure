# Managed by pinka (ansible/roles/uninstall/library/pinka_uninstall.py). Local changes will be overwritten.
"""Ansible module: uninstall's plan, its removal and its report (BUILD_PROMPT §5.4).

* ``state: plan`` reads the install record (§7.21), the lab description, the
  active and retired platforms and the host, and writes the plan: every item
  pinka would remove, restore or keep, in removal order, with the reason. It
  changes nothing; ``--dry-run`` stops after it.
* ``state: apply`` carries out one phase of the confirmed plan. Every item
  ends with a result: ``removed``, ``restored``, ``kept``, ``already_gone``
  or ``failed`` (an item it should have removed and could not, which makes
  bootstrap exit with 81). A second run finds what is already gone.
* ``state: report`` writes the report (``/var/log/pinka-uninstall/<UTC>.json``)
  from the results. Its one ``pending`` item is ``/opt/pinka``, which
  bootstrap removes after the play that runs from it.

What is pinka's comes from the install record. A legacy record (the host was
provisioned before the record existed) or no record at all decides nothing:
then only what carries pinka's names goes, and Docker, Incus, Tailscale and
the NVIDIA packages stay ("kept: origin unknown") unless the owner names them
with ``--remove-component``. The lab's data stays unless ``purge_data``.

The module runs on the target's Python (3.9 in the Red Hat family's
check-mode containers) and uses the standard library only. Commands that
depend on the package manager come from the role's family variables.
"""

from __future__ import annotations

import datetime as dt
import fnmatch
import json
import os
import re
import shutil
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

DOCUMENTATION = r"""
module: pinka_uninstall
short_description: Plan, carry out and report uninstall
description:
  - Computes uninstall's plan from the install record and the host, removes
    one phase of a confirmed plan, or writes the report (BUILD_PROMPT §5.4).
options:
  state:
    description: plan computes and writes the plan; apply removes one phase; report writes the report.
    type: str
    choices: [plan, apply, report]
    required: true
  root:
    description: The host's file system root (tests use a fixture directory).
    type: path
    default: /
  machine:
    description: The machine name, for the plan and the report.
    type: str
    default: ""
  purge_data:
    description: Also remove the lab's data.
    type: bool
    default: false
  remove_components:
    description: On a host without a usable install record, also remove these (docker, incus, tailscale, nvidia).
    type: list
    elements: str
    default: []
  family:
    description: The package manager's commands (the role's family variables).
    type: dict
    default: {}
  component_packages:
    description: The packages of each component across the component source matrix, for --remove-component.
    type: dict
    default: {}
  component_repos:
    description: The repositories of each component across the component source matrix (pinka-<name> files).
    type: dict
    default: {}
  plan_file:
    description: plan writes, apply reads the plan (JSON).
    type: path
  plan_text:
    description: plan writes the plan for people.
    type: path
  phase:
    description: apply only; the phase to carry out.
    type: str
  results:
    description: report only; the results of every phase.
    type: list
    elements: dict
    default: []
  report_file:
    description: report only; where the report goes.
    type: path
  started_at:
    description: report only; when uninstall began (UTC).
    type: str
    default: ""
  repo_commit:
    description: report only; the commit of the repository uninstall ran from.
    type: str
    default: ""
  log_file:
    description: report only; bootstrap's log of this run.
    type: str
    default: ""
author: pinka
"""

SCHEMA_VERSION = 1
PHASES = (
    "platform",
    "keep",
    "units",
    "compose",
    "incus",
    "firewall",
    "tailscale",
    "packages",
    "accounts",
    "dropins",
    "paths",
)
PHASE_TITLES = {
    "platform": "the platform",
    "keep": "what stays: copied out of pinka's paths first",
    "units": "pinka's units",
    "compose": "containers, networks and images",
    "incus": "Incus objects",
    "firewall": "firewall",
    "tailscale": "Tailscale",
    "packages": "packages, holds, repositories and keys",
    "accounts": "accounts, groups, memberships and keys",
    "dropins": "drop-in files and settings",
    "paths": "pinka's paths",
}
ACTIONS = ("remove", "restore", "keep")
RESULTS = ("removed", "restored", "kept", "already_gone", "failed", "pending")
# The item bootstrap finishes after the play (bootstrap/lib/stages.sh).
PENDING_REASON = "bootstrap removes it after the play"
COMPONENTS = ("docker", "incus", "tailscale", "nvidia")
# Record component names for the --remove-component names.
RECORD_COMPONENT = {
    "docker": "docker",
    "incus": "incus",
    "tailscale": "tailscale",
    "nvidia": "nvidia_driver",
}

# What uninstall knows of pinka: the paths, names and objects it removes or
# keeps (§5.4). ansible/tests/test_uninstall.py keeps them in step with the
# roles that create each item.
NAMES: dict[str, Any] = {
    "record": "/var/lib/pinka/install-record.json",
    "lab": "/etc/pinka/lab.json",
    "active_platform": "/etc/pinka/platform/active.json",
    "rendered_platform": "/etc/pinka/platform/rendered.json",
    "retired_platforms": "/var/lib/pinka/platforms/*/retired.json",
    "pinka_root": "/srv/pinka",
    "pinka_cli": "/usr/local/bin/pinka",
    "opt_dir": "/opt/pinka",
    "state_dir": "/var/lib/pinka",
    "deploy_key": "/var/lib/pinka/git/deploy_key.pub",
    "bin_dir": "/usr/local/bin",
    "unit_dir": "/etc/systemd/system",
    "unit_prefix": "pinka-",
    "paths": [
        "/usr/local/bin/pinka",
        "/usr/local/lib/pinka",
        "/usr/local/libexec/pinka",
        "/usr/local/share/pinka",
        "/etc/pinka",
        "/run/pinka",
        "/var/cache/pinka",
    ],
    "links": ["/usr/local/bin/restic", "/usr/local/bin/quarto"],
    "link_targets": ["/opt/pinka/", "/usr/local/lib/pinka/"],
    "logs": ["/var/log/pinka-bootstrap", "/var/log/pinka-uninstall"],
    # What uninstall keeps outside pinka's paths, root only (owner, D2 review):
    # copied there before the paths go. "always": also with --purge-data,
    # because the backups, which are never deleted, need it. "secret": what
    # grants access to the backups; the first follow-up says to take it off
    # the host and delete it (owner, with the "commit" for D2).
    "keep_dir": "/var/lib/pinka-kept",
    "kept_copies": [
        {
            "source": "/etc/pinka/secrets/backup",
            "dest": "backup/secrets",
            "what": "the restic repository's location, its password and the SFTP key",
            "always": True,
            "secret": "the restic repository's password and location and the SFTP key of the "
            "backup server",
        },
        {
            "source": "/etc/pinka/backup",
            "dest": "backup/config",
            "what": "the backup helper's settings and the backup server's host keys",
            "always": True,
        },
        {
            "source": "/var/lib/pinka/backup",
            "dest": "backup/state",
            "what": "the state of the last backup",
            "always": True,
        },
        {
            "source": "/usr/local/bin/restic",
            "dest": "backup/restic",
            "what": "the restic binary the backups were written with",
            "always": True,
        },
        {
            "source": "/etc/pinka/secrets/vault-pass",
            "dest": "vault-pass",
            "what": "the vault password bootstrap stored: the vault in the checkout opens with it",
            "always": True,
            "secret": "the vault password, which opens the vault with the repository's password",
        },
        {
            "source": "/etc/pinka/qa",
            "dest": "qa",
            "what": "the QA trusted keys and the attestations",
            "always": False,
        },
        {
            "source": "/var/lib/pinka/gpu",
            "dest": "records/gpu",
            "what": "the GPU arbiter's run records and transition log",
            "always": False,
        },
        {
            "source": "/var/lib/pinka/sessions",
            "dest": "records/sessions",
            "what": "the lab's session records",
            "always": False,
        },
        {
            "source": "/var/lib/pinka/fingerprint",
            "dest": "records/fingerprint",
            "what": "the environment fingerprints",
            "always": False,
        },
        {
            "source": "/var/lib/pinka/platforms",
            "dest": "records/platforms",
            "what": "the platforms' deployment and retirement records",
            "always": False,
        },
        {
            "source": "/var/lib/pinka/verify",
            "dest": "records/verify",
            "what": "the verification results of the GPU containers and of each served model",
            "always": False,
        },
    ],
    # Read-only while packages are removed, so that no package script can
    # delete kept data (owner, D2 review): which bind mounts are in place.
    "guard_state": "/run/pinka-uninstall-guards",
    # The units of a component, stopped before its packages go and its data
    # directory is guarded.
    "component_units": {
        "docker": ["docker.socket", "docker.service", "containerd.service"],
        "incus": [
            "incus-user.socket",
            "incus-user.service",
            "incus.socket",
            "incus.service",
            "incus-startup.service",
            "incus-lxcfs.service",
        ],
        "tailscale": ["tailscaled.service"],
    },
    # Never removed: the SSH server (uninstall never stops SSH, and the
    # purge of openssh-server deletes the host keys).
    "never_remove_packages": ["openssh-server", "openssh"],
    # Docker's data root is docker info's (docker_data_root may move it).
    "component_data": {
        "docker": ["/var/lib/docker", "/var/lib/containerd"],
        "incus": ["/var/lib/incus"],
        "tailscale": ["/var/lib/tailscale"],
    },
    "container_prefix": "pinka-",
    "compose_projects": ["pinka-core", "pinka-observability", "pinka-inference"],
    "docker_networks": ["pinka-core_default"],
    "incus": {
        "pools": ["pinka-targets"],
        "projects": ["bench", "build"],
        "networks": ["pinka-build0"],
        "acls": ["pinka-run-default"],
        "run_network_patterns": ["r-*"],
        "personal_project_patterns": ["user-*"],
        "personal_network_patterns": ["incusbr-*"],
        "certificate_prefix": "pinka-",
        "image_property": "pinka.recipe_commit",
    },
    "nft_table_prefix": "pinka_",
    "interfaces": ["pinka-otlp0"],
    "bridge_patterns": ["r-+", "pinka-build0", "incusbr-+"],
    "firewalld_zones": ["pinka-tailnet", "pinka-run"],
    "firewalld_trusted_interfaces": ["pinka-build0", "incusbr-+"],
    "ufw_comment": "pinka",
    "infra_accounts": [
        "pinka-build",
        "pinka-inference",
        "pinka-query-proxy",
        "pinka-otelcol",
        "pinka-prometheus",
        "pinka-loki",
        "pinka-tempo",
        "pinka-grafana",
    ],
    "reserved_groups": [
        "pinka",
        "pinka-admin",
        "pinka-restricted",
        "pinka-datasets",
        "pinka-query",
    ],
    "account_home_prefix": "/var/lib/pinka/",
    "component_groups": {"docker": ["docker"], "incus": ["incus", "incus-admin"]},
    "restored_memberships": ["docker", "incus-admin"],
    "mok_keys": ["/var/lib/shim-signed/mok/MOK.der", "/var/lib/dkms/mok.pub"],
    "dropins": [
        {"path": "/etc/ssh/sshd_config.d/10-pinka.conf", "reload": "sshd"},
        {"path": "/etc/sudoers.d/90-pinka-admin", "reload": "sudoers"},
        {"path": "/etc/systemd/journald.conf.d/60-pinka.conf", "reload": "journald"},
        {"path": "/etc/sysctl.d/60-pinka.conf", "reload": "sysctl"},
        {"path": "/etc/modprobe.d/pinka-blacklist-nouveau.conf", "reload": "initramfs"},
        {"path": "/etc/apt/apt.conf.d/52pinka-unattended-upgrades", "reload": ""},
        {"path": "/etc/apt/apt.conf.d/20auto-upgrades", "reload": ""},
        {"path": "/etc/dnf/automatic.conf", "reload": ""},
        {"path": "/etc/docker/daemon.json", "reload": "", "component": "docker"},
        {"path": "/etc/wsl.conf", "reload": ""},
        {"path": "/etc/ld.so.conf.d/pinka-wsl.conf", "reload": "ldconfig"},
        {"path": "/etc/tmpfiles.d/pinka-postgres.conf", "reload": ""},
        {"path": "/etc/tmpfiles.d/pinka-query.conf", "reload": ""},
    ],
    "dropin_modes": {
        "/etc/sudoers.d/90-pinka-admin": {"mode": "0440", "owner": "root", "group": "root"}
    },
    "edited": {
        "/etc/ssh/sshd_config": {"line": "Include /etc/ssh/sshd_config.d/*.conf", "reload": "sshd"}
    },
    "dropin_globs": ["/etc/systemd/system/*.d/*pinka*.conf"],
    "distribution_units": [
        "docker.service",
        "incus.service",
        "incus-user.socket",
        "tailscaled.service",
        "nvidia-cdi-refresh.path",
        "nvidia-cdi-refresh.service",
        "systemd-timesyncd.service",
        "chrony.service",
        "chronyd.service",
        "dnf-automatic.timer",
        "qemu-guest-agent.service",
        "open-vm-tools.service",
        "vmtoolsd.service",
        "hv-kvp-daemon.service",
        "hypervkvpd.service",
        "hv_kvp_daemon.service",
        "ssh.service",
        "sshd.service",
    ],
    "never_stop": ["ssh.service", "sshd.service"],
    "sleep_targets": ["sleep.target", "suspend.target", "hibernate.target", "hybrid-sleep.target"],
    "sleep_marker": "/etc/pinka/power-policy-masked",
}

RunCommand = Callable[[Sequence[str]], "tuple[int, str, str]"]
Item = dict[str, Any]  # an item of the plan: phase, kind, name, action, reason and its details


def utc_now() -> str:
    """The current time, UTC ISO 8601 with Z (``datetime.UTC`` needs 3.11)."""
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: UP017


class Host:
    """The host as the module sees it: its commands and its files below ``root``.

    Attributes:
        root: The file system root (``/`` on a host).
    """

    def __init__(self, run: RunCommand, root: str = "/") -> None:
        """Wrap a command runner.

        Args:
            run: Runs a command, returning ``(rc, stdout, stderr)``.
            root: The file system root.
        """
        self._run = run
        self.root = root

    def path(self, name: str) -> Path:
        """The real path of a host path."""
        return Path(name) if self.root == "/" else Path(self.root, name.lstrip("/"))

    def exists(self, name: str) -> bool:
        """Whether a path exists (a dangling link counts)."""
        return os.path.lexists(self.path(name))

    def is_dir(self, name: str) -> bool:
        """Whether a path is a directory (not a link to one)."""
        real = self.path(name)
        return real.is_dir() and not real.is_symlink()

    def read(self, name: str) -> str | None:
        """A text file's content, or None."""
        try:
            return self.path(name).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    def link_target(self, name: str) -> str | None:
        """Where a symbolic link points, or None."""
        try:
            return str(self.path(name).readlink())
        except OSError:
            return None

    def glob(self, pattern: str) -> list[str]:
        """Host paths matching a pattern, sorted."""
        base = Path(self.root)
        return sorted("/" + str(p.relative_to(base)) for p in base.glob(pattern.lstrip("/")))

    def has(self, command: str) -> bool:
        """Whether a command is on the PATH."""
        return shutil.which(command) is not None

    def run(self, argv: Sequence[str]) -> tuple[int, str, str]:
        """Run a command."""
        return self._run([str(a) for a in argv])

    def ok(self, argv: Sequence[str]) -> bool:
        """Whether a command succeeds."""
        return self.run(argv)[0] == 0

    def remove(self, name: str) -> None:
        """Remove a file, a link or a whole directory."""
        real = self.path(name)
        if real.is_dir() and not real.is_symlink():
            shutil.rmtree(real)
        else:
            real.unlink()

    def write(self, name: str, text: str, mode: int, owner: str = "", group: str = "") -> None:
        """Write a file atomically with its mode (and owner, on a host)."""
        real = self.path(name)
        real.parent.mkdir(parents=True, exist_ok=True)
        fd, name_tmp = tempfile.mkstemp(dir=real.parent, prefix=".pinka-uninstall.")
        tmp = Path(name_tmp)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
            tmp.chmod(mode)
            if self.root == "/" and owner and group:
                shutil.chown(tmp, owner, group)
            tmp.replace(real)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def mode(self, name: str) -> int:
        """A path's permission bits."""
        return self.path(name).stat().st_mode & 0o7777

    def is_empty_dir(self, name: str) -> bool:
        """Whether a path is an empty directory."""
        return self.is_dir(name) and not any(self.path(name).iterdir())


def item(phase: str, kind: str, name: str, action: str, reason: str, **details: Any) -> Item:  # noqa: ANN401 - JSON details
    """One item of the plan.

    Args:
        phase: One of :data:`PHASES`.
        kind: What it is (``unit``, ``package``, ``path``, ...).
        name: What the report calls it.
        action: ``remove``, ``restore`` or ``keep``.
        reason: Why, in a few words.
        **details: What the removal needs (a project, a value to set back, ...).

    Returns:
        The item.
    """
    if phase not in PHASES or action not in ACTIONS:
        raise ValueError(f"no such phase or action: {phase} {action}")
    return {
        "phase": phase,
        "kind": kind,
        "name": name,
        "action": action,
        "reason": reason,
        **details,
    }


def read_json(host: Host, name: str) -> Any:  # noqa: ANN401 - JSON value
    """A JSON file's content, or None when it is missing or unreadable."""
    text = host.read(name)
    if text is None:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def parse_db(text: str | None, fields: int) -> dict[str, list[str]]:
    """``/etc/passwd`` or ``/etc/group``: the first field to the others."""
    found = {}
    for line in (text or "").splitlines():
        parts = line.split(":")
        if len(parts) >= fields and parts[0] and not line.startswith("#"):
            found[parts[0]] = parts[1:]
    return found


def members(groups: Mapping[str, list[str]], name: str) -> list[str]:
    """The members of a group in a parsed ``/etc/group``."""
    entry = groups.get(name) or ["", "", ""]
    return [m for m in entry[2].split(",") if m] if len(entry) > 2 else []  # noqa: PLR2004 - x, gid, members


def query_json(host: Host, argv: Sequence[str]) -> Any:  # noqa: ANN401 - JSON value
    """A command's JSON output, or None when it fails."""
    rc, out, _ = host.run(argv)
    if rc != 0:
        return None
    try:
        return json.loads(out)
    except ValueError:
        return None


def json_lines(text: str) -> list[dict[str, Any]]:
    """Objects of a one-object-per-line output (``docker ... --format json``)."""
    found = []
    for line in text.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            found.append(value)
    return found


def matches(name: str, patterns: Iterable[str]) -> bool:
    """Whether a name matches one of the shell patterns."""
    return any(fnmatch.fnmatchcase(name, p) for p in patterns)


class Context:
    """What the plan is computed from: the record, the lab, the platforms, the host.

    Attributes:
        host: The host.
        names: Pinka's names, paths and objects.
        family: The package manager's commands.
        record: The install record, or None.
        usable: The record decides (present and not legacy).
        pre: Its ``preexisting`` part ({} when not usable).
        added: Its ``added`` part ({} when not usable).
        changed: Settings pinka changed, with their value before.
        lab: The lab description ({} when missing).
        active: The active platform record, or None.
        rendered: What was rendered for the active platform, or None.
        retired: The retired platforms' records.
        passwd: ``/etc/passwd`` by account.
        groups: ``/etc/group`` by group.
        pinka_root: The research space.
        purge: Remove the lab's data too.
        remove_components: The components named with --remove-component.
        component_packages: Each component's packages (every source group).
        component_repos: Each component's repositories (every source group).
        keep_for_data: Components that stay because they hold kept data, with why.
        docker_root: Docker's data root (``docker info``), /var/lib/docker by default.
    """

    def __init__(self, host: Host, params: Mapping[str, Any]) -> None:
        """Read everything the plan needs."""
        self.host = host
        self.names: dict[str, Any] = NAMES
        self.family: dict[str, Any] = dict(params.get("family") or {})
        self.component_packages: dict[str, list[str]] = dict(params.get("component_packages") or {})
        self.component_repos: dict[str, list[str]] = dict(params.get("component_repos") or {})
        # Components pinka installed that stay because they hold kept data
        # (Incus with the researchers' instances), with the reason.
        self.keep_for_data: dict[str, str] = {}
        # The items planned so far (build_plan fills it phase by phase).
        self.planned: list[Item] = []
        self.purge = bool(params.get("purge_data"))
        self.remove_components = [str(c) for c in params.get("remove_components") or []]
        self.machine = str(params.get("machine") or "")
        self.session: dict[str, Any] = dict(params.get("session") or {})
        self.opt_existed = bool(params.get("opt_existed", True))
        record = read_json(host, self.names["record"])
        self.record = record if isinstance(record, dict) else None
        self.legacy = bool(self.record and self.record.get("legacy"))
        self.usable = self.record is not None and not self.legacy
        self.pre: dict[str, Any] = (self.record or {}).get("preexisting", {}) if self.usable else {}
        self.added: dict[str, Any] = (self.record or {}).get("added", {}) if self.usable else {}
        self.changed: dict[str, Any] = dict(self.added.get("changed") or {})
        lab = read_json(host, self.names["lab"])
        self.lab: dict[str, Any] = lab if isinstance(lab, dict) else {}
        active = read_json(host, self.names["active_platform"])
        self.active = active if isinstance(active, dict) and active.get("name") else None
        rendered = read_json(host, self.names["rendered_platform"])
        self.rendered = (
            rendered
            if isinstance(rendered, dict)
            and self.active
            and rendered.get("platform") == self.active["name"]
            else None
        )
        self.retired = [
            r
            for r in (read_json(host, p) for p in host.glob(self.names["retired_platforms"]))
            if isinstance(r, dict) and isinstance(r.get("active"), dict)
        ]
        self.passwd = parse_db(host.read("/etc/passwd"), 6)
        self.groups = parse_db(host.read("/etc/group"), 3)
        self.pinka_root = str(self.lab.get("pinka_root") or self.names["pinka_root"])
        rc, out, _ = (
            host.run(["docker", "info", "--format", "{{.DockerRootDir}}"])
            if host.has("docker")
            else (1, "", "")
        )
        root = out.strip()
        self.docker_root = root if rc == 0 and root.startswith("/") else "/var/lib/docker"

    def component_data(self, component: str) -> list[str]:
        """Where a component keeps its data on this host."""
        data = list(self.names["component_data"].get(component, []))
        if component == "docker":
            data[0] = self.docker_root
        return data

    @property
    def record_state(self) -> str:
        """``present``, ``legacy`` or ``missing``."""
        if self.record is None:
            return "missing"
        return "legacy" if self.legacy else "present"

    def origin(self, component: str) -> str:
        """Who installed a component: ``pinka``, ``pre-existing`` or ``unknown`` (no usable record)."""
        if not self.usable:
            return "unknown"
        added = self.added.get("components") or {}
        return "pinka" if added.get(RECORD_COMPONENT.get(component, component)) else "pre-existing"

    def removes(self, component: str) -> bool:
        """Whether uninstall removes a component: pinka's, or named with --remove-component.

        Never one that stays because it holds kept data.
        """
        if component in self.keep_for_data:
            return False
        origin = self.origin(component)
        return origin == "pinka" or (origin == "unknown" and component in self.remove_components)

    def platforms(self) -> list[dict[str, Any]]:
        """The active platform's record and every retired one's."""
        found = [self.active] if self.active else []
        return found + [r["active"] for r in self.retired]

    def unit_file(self, name: str) -> str:
        """The path of a unit file pinka writes."""
        return f"{self.names['unit_dir']}/{name}"


# The command that tells whether a shared component is on the host.
COMPONENT_COMMANDS = {
    "docker": "docker",
    "incus": "incus",
    "tailscale": "tailscale",
    "nvidia": "nvidia-smi",
}


def kept_component(ctx: Context, phase: str, component: str, title: str) -> list[Item]:
    """The keep item of a shared component uninstall leaves (§5.4), when it is there."""
    if not ctx.host.has(COMPONENT_COMMANDS[component]):
        return []
    origin = ctx.origin(component)
    if origin == "pre-existing":
        return [item(phase, "component", title, "keep", "it existed before pinka; kept as it is")]
    if origin == "unknown" and component not in ctx.remove_components:
        return [
            item(
                phase,
                "component",
                title,
                "keep",
                f"origin unknown (no install record); --remove-component {component} removes it",
            )
        ]
    return []


# --- the plan, phase by phase ------------------------------------------------------------------


def kept_copies(ctx: Context) -> list[dict[str, Any]]:
    """The copies uninstall keeps of files below pinka's paths that exist here.

    A link counts when its target does. The backup's access (its key and settings) is kept also with
    --purge-data, because uninstall never deletes the backups; the records
    and attestations are the lab's data and go with --purge-data.
    """
    return [
        c
        for c in ctx.names["kept_copies"]
        if (c["always"] or not ctx.purge) and ctx.host.path(c["source"]).exists()
    ]


def plan_keep(ctx: Context) -> list[Item]:
    """What stays though it lies below pinka's paths: copied to the keep directory first."""
    keep_dir = ctx.names["keep_dir"]
    return [
        item(
            "keep",
            "keep_copy",
            c["source"],
            "keep",
            f"{c['what']}: copied to {keep_dir}/{c['dest']} (root only) before the path goes",
            dest=f"{keep_dir}/{c['dest']}",
        )
        for c in kept_copies(ctx)
    ]


def plan_platform(ctx: Context) -> list[Item]:
    """Drain the platform and stop the lab: ``pinka stop`` (§5.4, §7.14)."""
    if not (ctx.active or ctx.host.exists(ctx.unit_file("pinka-lab.target"))):
        return []
    what = f"drain the platform {ctx.active['name']}, " if ctx.active else ""
    return [
        item(
            "platform",
            "stop",
            "pinka stop",
            "remove",
            f"{what}idle the GPU arbiter, stop pinka-platform.target and pinka-lab.target",
            platform=ctx.active["name"] if ctx.active else "",
        )
    ]


def platform_units(ctx: Context) -> list[str]:
    """The unit names of the active and the retired platforms, with their suffixes."""
    found = set()
    for platform in ctx.platforms():
        for unit in (platform.get("names") or {}).get("units", []):
            for suffix in (".service", ".socket", ".timer", ".path", ".target"):
                if ctx.host.exists(ctx.unit_file(f"{unit}{suffix}")):
                    found.add(f"{unit}{suffix}")
    for unit in (ctx.rendered or {}).get("units", []):
        name = str(unit.get("name", ""))
        if name and ctx.host.exists(ctx.unit_file(name)):
            found.add(name)
    return sorted(found)


def plan_units(ctx: Context) -> list[Item]:
    """Pinka's units, timers, sockets, slices and targets, and the platforms' (§5.4)."""
    prefix = ctx.names["unit_prefix"]
    pinka = [
        Path(p).name
        for p in ctx.host.glob(f"{ctx.names['unit_dir']}/{prefix}*")
        if not p.endswith(".d") and not ctx.host.is_dir(p)
    ]
    platform = set(platform_units(ctx))
    units = sorted(set(pinka) | platform)
    order = {"target": 0, "timer": 1, "socket": 2, "path": 2, "service": 3, "slice": 4}
    units.sort(key=lambda u: (order.get(u.rsplit(".", 1)[-1], 3), u))
    return [
        item(
            "units",
            "unit",
            u,
            "remove",
            "the platform's unit" if u in platform else "pinka's unit",
            template=u.split(".", 1)[0].endswith("@"),
        )
        for u in units
    ]


def compose_images(ctx: Context, containers: Sequence[Mapping[str, Any]]) -> list[str]:
    """The image references pinka pulled: the lab's pins, the platforms' job images, its containers'."""
    refs = {str(v) for v in (ctx.lab.get("containers") or {}).values()}
    gpu = (ctx.lab.get("gpu") or {}).get("image")
    if gpu:
        refs.add(str(gpu))
    for job in ((ctx.rendered or {}).get("training") or {}).get("jobs", []):
        if job.get("image"):
            refs.add(str(job["image"]))
    refs.update(str(c.get("Image")) for c in containers if c.get("Image"))
    return sorted(r for r in refs if r)


def labels(container: Mapping[str, Any]) -> dict[str, str]:
    """A container's labels from ``docker ps --format json`` (``a=b,c=d``)."""
    found = {}
    for pair in str(container.get("Labels") or "").split(","):
        key, sep, value = pair.partition("=")
        if sep:
            found[key] = value
    return found


def is_pinka_container(ctx: Context, container: Mapping[str, Any]) -> bool:
    """A container of pinka's Compose projects, or one named like pinka's."""
    project = labels(container).get("com.docker.compose.project", "")
    return (
        str(container.get("Names", "")).startswith(ctx.names["container_prefix"])
        or project in ctx.names["compose_projects"]
    )


def plan_compose(ctx: Context) -> list[Item]:
    """Pinka's containers, Compose networks and images (§5.4); nobody else's."""
    host = ctx.host
    if not host.has("docker") or not host.ok(["docker", "info", "--format", "{{.ID}}"]):
        return []
    rc, out, _ = host.run(["docker", "ps", "--all", "--no-trunc", "--format", "{{json .}}"])
    containers = json_lines(out) if rc == 0 else []
    ours = [c for c in containers if is_pinka_container(ctx, c)]
    items = [
        item("compose", "container", str(c["Names"]), "remove", "pinka's container") for c in ours
    ]
    rc, out, _ = host.run(["docker", "network", "ls", "--no-trunc", "--format", "{{json .}}"])
    for network in json_lines(out) if rc == 0 else []:
        project = labels(network).get("com.docker.compose.project", "")
        if (
            network.get("Name") in ctx.names["docker_networks"]
            or project in ctx.names["compose_projects"]
        ):
            items.append(
                item("compose", "network", str(network["Name"]), "remove", "pinka's network")
            )
    others = {str(c.get("Names")) for c in containers if c not in ours}
    used: dict[str, str] = {}
    for name in sorted(others):
        rc, out, _ = host.run(["docker", "inspect", "--format", "{{.Image}}", name])
        if rc == 0:
            used.setdefault(out.strip(), name)
    for ref in compose_images(ctx, ours):
        rc, out, _ = host.run(["docker", "image", "inspect", "--format", "{{.Id}}", ref])
        if rc != 0:
            continue
        user = used.get(out.strip())
        if user:
            items.append(
                item(
                    "compose",
                    "image",
                    ref,
                    "keep",
                    f"the container {user}, which is not pinka's, uses it",
                )
            )
        else:
            items.append(item("compose", "image", ref, "remove", "pulled by pinka"))
    return items


def incus_query(ctx: Context, url: str) -> Any:  # noqa: ANN401 - JSON value
    """``incus query URL`` as JSON, or None."""
    return query_json(ctx.host, ["incus", "query", url])


def incus_scope(ctx: Context) -> tuple[str, set[str]]:
    """Which Incus objects are pinka's here.

    Returns:
        The mode (``all``: pinka installed Incus, every object of pinka's goes;
        ``recorded``: an Incus that existed before pinka, only what the record
        names; ``names``: no usable record, only pinka's reserved names) and
        the recorded objects (``kind:name``).
    """
    origin = ctx.origin("incus")
    if origin == "pinka":
        return "all", set()
    if origin == "pre-existing":
        return "recorded", {str(o) for o in ctx.added.get("incus_objects") or []}
    return "names", set()


def plan_incus(ctx: Context) -> list[Item]:  # noqa: PLR0912, PLR0915 - one pass over Incus's objects in removal order
    """The Incus objects pinka created, in removal order, the pool last (§5.4)."""
    host, names = ctx.host, ctx.names["incus"]
    if not host.has("incus") or incus_query(ctx, "/1.0") is None:
        return []
    mode, recorded = incus_scope(ctx)
    personal = names["personal_project_patterns"] + names["personal_network_patterns"]

    def ours(kind: str, name: str, patterns: Sequence[str] = ()) -> bool:
        if mode == "recorded":
            return f"{kind}:{name}" in recorded or matches(name, names["run_network_patterns"])
        if mode == "names" and matches(name, personal):
            return False
        return name in names[f"{kind}s"] or matches(name, patterns)

    projects = [p.get("name", "") for p in incus_query(ctx, "/1.0/projects?recursion=1") or []]
    our_projects = [p for p in projects if ours("project", p, names["personal_project_patterns"])]
    instances = incus_query(ctx, "/1.0/instances?all-projects=true&recursion=1") or []
    pools = [p.get("name", "") for p in incus_query(ctx, "/1.0/storage-pools?recursion=1") or []]
    volumes = {
        pool: incus_query(ctx, f"/1.0/storage-pools/{pool}/volumes?all-projects=true&recursion=1")
        or []
        for pool in pools
    }
    # A researcher's personal project with instances or volumes is their
    # work: kept without --purge-data, and Incus with it (owner, D2 review).
    held = researchers_work(ctx, our_projects, instances, volumes)
    our_projects = [p for p in our_projects if p not in held]
    items: list[Item] = [
        item("incus", "project", p, "keep", f"a researcher's work: {what}; --purge-data removes it")
        for p, what in sorted(held.items())
    ]
    if held and mode == "all":
        ctx.keep_for_data["incus"] = (
            f"it holds the researchers' work ({', '.join(sorted(held))}); "
            "--purge-data removes it with Incus"
        )
    if mode == "names":
        items += [
            item(
                "incus",
                "project",
                p,
                "keep",
                "a personal project's name is incus-user's too; origin unknown",
            )
            for p in projects
            if matches(p, names["personal_project_patterns"])
        ]
    for instance in instances:
        if instance.get("project") in our_projects:
            items.append(
                item(
                    "incus",
                    "instance",
                    f"{instance['project']}/{instance['name']}",
                    "remove",
                    "an instance in pinka's project",
                    project=instance["project"],
                    object=instance["name"],
                )
            )
    upstream = {Path(p).name for p in host.glob(f"{ctx.pinka_root}/images/_upstream/*/*")}
    for image in incus_query(ctx, "/1.0/images?all-projects=true&recursion=1") or []:
        fingerprint = str(image.get("fingerprint", ""))
        properties = image.get("properties") or {}
        project = str(image.get("project") or "default")
        pinka_built = names["image_property"] in properties
        if mode == "recorded":
            mine = pinka_built or f"image:{fingerprint}" in recorded or project in our_projects
        else:
            mine = (
                pinka_built
                or project in our_projects
                or (mode == "all" and fingerprint in upstream)
            )
        if mine:
            aliases = ", ".join(a.get("name", "") for a in image.get("aliases") or [])
            items.append(
                item(
                    "incus",
                    "image",
                    f"{project}/{aliases or fingerprint[:12]}",
                    "remove",
                    "an image pinka built or brought in",
                    project=project,
                    object=fingerprint,
                )
            )
    for pool in pools:
        for volume in volumes[pool]:
            if volume.get("type") == "custom" and volume.get("project") in our_projects:
                items.append(
                    item(
                        "incus",
                        "volume",
                        f"{volume['project']}/{pool}/{volume['name']}",
                        "remove",
                        "a volume in pinka's project",
                        project=volume["project"],
                        pool=pool,
                        object=volume["name"],
                    )
                )
    for project in our_projects:
        for profile in incus_query(ctx, f"/1.0/profiles?project={project}&recursion=1") or []:
            if profile.get("name") != "default":
                items.append(
                    item(
                        "incus",
                        "profile",
                        f"{project}/{profile['name']}",
                        "remove",
                        "a profile of pinka's project",
                        project=project,
                        object=profile["name"],
                    )
                )
    items += [
        item("incus", "project", p, "remove", "pinka's project", object=p)
        for p in our_projects
        if p != "default"
    ]
    networks = [
        n.get("name", "")
        for n in incus_query(ctx, "/1.0/networks?recursion=1") or []
        if n.get("managed")
    ]
    if mode == "names":
        items += [
            item(
                "incus",
                "network",
                n,
                "keep",
                "a personal network's name is incus-user's too; origin unknown",
            )
            for n in networks
            if matches(n, names["personal_network_patterns"])
        ]
    network_patterns = names["run_network_patterns"] + names["personal_network_patterns"]
    # A kept personal project keeps its network (incusbr-<uid> of user-<uid>).
    held_networks = {f"incusbr-{p.split('-', 1)[1]}" for p in held}
    items += [
        item("incus", "network", n, "keep", "the network of a researcher's kept project")
        for n in networks
        if n in held_networks
    ]
    our_networks = [
        n for n in networks if ours("network", n, network_patterns) and n not in held_networks
    ]
    our_networks.sort(key=lambda n: (not matches(n, names["run_network_patterns"]), n))
    items += [
        item("incus", "network", n, "remove", "pinka's network", object=n) for n in our_networks
    ]
    for acl in incus_query(ctx, "/1.0/network-acls") or []:
        name = str(acl).rsplit("/", 1)[-1]
        if ours("acl", name):
            items.append(item("incus", "acl", name, "remove", "pinka's network ACL", object=name))
    platform_certificates = {
        str(c.get("name"))
        for c in (ctx.rendered or {}).get("incus_certificates", [])
        if c.get("name")
    }
    for certificate in incus_query(ctx, "/1.0/certificates?recursion=1") or []:
        name = str(certificate.get("name", ""))
        if mode == "recorded":
            mine = f"certificate:{name}" in recorded or name in platform_certificates
        else:
            mine = name.startswith(names["certificate_prefix"]) or name in platform_certificates
        if mine:
            items.append(
                item(
                    "incus",
                    "certificate",
                    name,
                    "remove",
                    "a client certificate pinka trusted",
                    object=str(certificate.get("fingerprint", "")),
                )
            )
    for pool in pools:
        if ours("pool", pool) and held:
            items.append(
                item("incus", "pool", pool, "keep", "the researchers' kept projects use it")
            )
        elif ours("pool", pool):
            items.append(
                item(
                    "incus",
                    "pool",
                    pool,
                    "remove",
                    "pinka's storage pool, removed last",
                    object=pool,
                )
            )
    for key, before in sorted(ctx.changed.items()):
        if key.startswith("incus:config:") and mode == "recorded":
            setting = key[len("incus:config:") :]
            items.append(
                item(
                    "incus",
                    "incus_config",
                    setting,
                    "restore",
                    f"set back to {before!r}, its value before pinka"
                    if before
                    else "unset, as before pinka",
                    value=before or "",
                )
            )
    if "incus" in ctx.keep_for_data:
        items.append(item("incus", "component", "Incus", "keep", ctx.keep_for_data["incus"]))
    return items + kept_component(ctx, "incus", "incus", "Incus")


def researchers_work(
    ctx: Context,
    projects: Sequence[str],
    instances: Sequence[Mapping[str, Any]],
    volumes: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, str]:
    """The personal projects among pinka's that hold instances or volumes, unless --purge-data.

    Returns:
        Each such project with what it holds.
    """
    if ctx.purge:
        return {}
    held: dict[str, str] = {}
    for project in projects:
        if not matches(project, ctx.names["incus"]["personal_project_patterns"]):
            continue
        count = sum(1 for i in instances if i.get("project") == project)
        vols = sum(
            1
            for pool in volumes.values()
            for v in pool
            if v.get("type") == "custom" and v.get("project") == project
        )
        if count or vols:
            held[project] = f"{count} instance(s), {vols} volume(s)"
    return held


def firewall_cmd(ctx: Context) -> list[str] | None:
    """firewall-cmd when firewalld runs, firewall-offline-cmd when it does not, or None."""
    if ctx.host.has("firewall-cmd") and ctx.host.ok(["firewall-cmd", "--state"]):
        return ["firewall-cmd", "--permanent"]
    if ctx.host.has("firewall-offline-cmd"):
        return ["firewall-offline-cmd"]
    return None


def kept_bridges(ctx: Context) -> set[str]:
    """The bridge patterns of a kept Incus's personal projects, whose rules stay."""
    return {"incusbr-+"} if "incus" in ctx.keep_for_data else set()


def docker_user_rules(ctx: Context) -> list[Item]:
    """The DOCKER-USER rules pinka inserted for its Incus bridges (§7.5), nothing else."""
    items = []
    patterns = set(ctx.names["bridge_patterns"]) - kept_bridges(ctx)
    for tool in ("iptables", "ip6tables"):
        if not ctx.host.has(tool):
            continue
        rc, out, _ = ctx.host.run([tool, "-S", "DOCKER-USER"])
        for line in out.splitlines() if rc == 0 else []:
            words = line.split()
            if words[:2] != ["-A", "DOCKER-USER"]:
                continue
            interfaces = {words[i + 1] for i, w in enumerate(words[:-1]) if w in ("-i", "-o")}
            if interfaces & patterns and words[-2:] == ["-j", "ACCEPT"]:
                items.append(
                    item(
                        "firewall",
                        "docker_user_rule",
                        f"{tool} {' '.join(words[1:])}",
                        "remove",
                        "pinka's rule for its Incus bridges",
                        tool=tool,
                        rule=words[2:],
                    )
                )
    return items


def guard_reason(ctx: Context) -> str:
    """Why the access guard goes: what it closed with consent is reachable again (§7.5)."""
    closed = [
        "the install's access path" if str(c) == "install_path" else str(c)
        for c in ctx.added.get("closed_access") or []
    ]
    if not closed:
        return "the access guard: listeners closed with consent are reachable again"
    return f"the access guard: {', '.join(closed)}, closed with consent, reachable again"


def plan_firewall(ctx: Context) -> list[Item]:  # noqa: PLR0912 - one branch per firewall
    """The firewall objects pinka added, and what it changed in the owner's zones (§5.4, §7.5).

    After them the firewall is as it was before pinka: every listener closed
    with the owner's consent (the table ``inet pinka_access_guard``) and the
    install's access path are reachable again.
    """
    host, items = ctx.host, []
    if host.has("nft"):
        rc, out, _ = host.run(["nft", "list", "tables"])
        for line in out.splitlines() if rc == 0 else []:
            words = line.split()
            if (
                words[:1] == ["table"]
                and words[2:]
                and words[2].startswith(ctx.names["nft_table_prefix"])
            ):
                why = guard_reason(ctx) if words[2] == "pinka_access_guard" else "pinka's table"
                items.append(item("firewall", "nft_table", f"{words[1]} {words[2]}", "remove", why))
    items += docker_user_rules(ctx)
    for interface in ctx.names["interfaces"] if host.has("ip") else []:
        if host.ok(["ip", "-o", "link", "show", interface]):
            items.append(item("firewall", "interface", interface, "remove", "pinka's interface"))
    command = firewall_cmd(ctx)
    if command is not None:
        for key, before in sorted(ctx.changed.items()):
            if key.startswith("firewalld:zone-of:"):
                interface = key[len("firewalld:zone-of:") :]
                items.append(
                    item(
                        "firewall",
                        "firewalld_interface",
                        interface,
                        "restore",
                        f"back to the zone {before}" if before else "in no zone, as before pinka",
                        zone=before or "",
                    )
                )
            elif key.startswith("firewalld:service:") and before == "absent":
                zone, _, service = key[len("firewalld:service:") :].rpartition(":")
                items.append(
                    item(
                        "firewall",
                        "firewalld_service",
                        f"{service} in the zone {zone}",
                        "remove",
                        "pinka opened it there",
                        zone=zone,
                        service=service,
                    )
                )
        rc, out, _ = host.run([*command, "--zone=trusted", "--list-interfaces"])
        for interface in out.split() if rc == 0 else []:
            if interface in set(ctx.names["firewalld_trusted_interfaces"]) - kept_bridges(ctx):
                items.append(
                    item(
                        "firewall",
                        "firewalld_trusted",
                        interface,
                        "remove",
                        "pinka's bridge in the trusted zone",
                    )
                )
        rc, out, _ = host.run([*command, "--get-zones"])
        for zone in out.split() if rc == 0 else []:
            if zone in ctx.names["firewalld_zones"]:
                items.append(item("firewall", "firewalld_zone", zone, "remove", "pinka's zone"))
    if host.has("ufw"):
        rc, out, _ = host.run(["ufw", "show", "added"])
        marker = f"comment '{ctx.names['ufw_comment']}'"
        for raw in out.splitlines() if rc == 0 else []:
            rule = raw.strip()
            if rule.startswith("ufw ") and rule.endswith(marker):
                items.append(item("firewall", "ufw_rule", rule, "remove", "pinka's rule"))
    return items


def plan_tailscale(ctx: Context) -> list[Item]:
    """The serve entries pinka added; a logout only when pinka joined the node (§5.4, §7.4)."""
    host = ctx.host
    if not host.has("tailscale"):
        return []
    items = [
        item("tailscale", "serve", entry, "remove", "pinka added this serve entry", entry=entry)
        for entry in ctx.added.get("tailscale_serve") or []
    ]
    joined = bool((ctx.added.get("components") or {}).get("tailscale"))
    if joined or (ctx.origin("tailscale") == "unknown" and "tailscale" in ctx.remove_components):
        items.append(
            item("tailscale", "logout", "the tailnet node", "remove", "pinka joined it: log it out")
        )
    elif ctx.origin("tailscale") == "pre-existing":
        items.append(
            item(
                "tailscale",
                "component",
                "Tailscale",
                "keep",
                "logged in before pinka; kept as it is",
            )
        )
    else:
        items += kept_component(ctx, "tailscale", "tailscale", "Tailscale")
    return items


def installed_packages(ctx: Context) -> dict[str, str]:
    """The installed packages and their versions, from the package manager."""
    rc, out, _ = ctx.host.run(ctx.family["list"])
    found = {}
    for line in out.splitlines() if rc == 0 else []:
        parts = line.split(None, 1)
        if len(parts) == 2:  # noqa: PLR2004 - name and version
            found[parts[0]] = parts[1].strip()
    return found


def hold_blocks(text: str | None) -> list[str]:
    """The names of pinka's blocks (``# BEGIN pinka <name>``) in a file."""
    return re.findall(r"^# BEGIN pinka (.+?)\s*$", text or "", re.MULTILINE)


def rpm_key_packages(ctx: Context, fingerprints: Iterable[str]) -> dict[str, list[str]]:
    """The ``gpg-pubkey`` packages of rpm's keyring that hold the given keys."""
    rc, out, _ = ctx.host.run(
        ["rpm", "-q", "gpg-pubkey", "--qf", "%{NAME}-%{VERSION}-%{RELEASE}\\n"]
    )
    keys = out.split() if rc == 0 else []
    found = {}
    for fingerprint in fingerprints:
        ids = {fingerprint[-8:].lower(), fingerprint.lower()}
        held = [k for k in keys if k.split("-")[2:3] and k.split("-")[2] in ids]
        if held:
            found[fingerprint] = held
    return found


def package_item(ctx: Context, name: str) -> Item:
    """A package pinka installed; one whose purge deletes kept data is removed without purging."""
    deletes = (ctx.family.get("purge_deletes") or {}).get(name)
    if deletes and not ctx.purge:
        return item(
            "packages",
            "package",
            name,
            "remove",
            f"pinka installed it; removed, not purged: its purge deletes {', '.join(deletes)}, "
            "which stays (--purge-data purges it)",
            keep_config=True,
        )
    return item("packages", "package", name, "remove", "pinka installed it")


def guard_candidates(ctx: Context) -> list[str]:
    """The kept data directories guarded read-only while packages are removed."""
    paths = [ctx.pinka_root, ctx.names["keep_dir"], *ctx.names["logs"]]
    for component in sorted(ctx.names["component_data"]):
        if ctx.removes(component):
            paths += ctx.component_data(component)
    return paths


def plan_holds_and_repos(
    ctx: Context, removed_components: Sequence[str], kept_packages: set[str]
) -> list[Item]:
    """Pinka's holds, repositories and keys; a component that stays keeps its own (D2 review)."""
    host, family, items = ctx.host, ctx.family, []
    method = family["hold_method"]
    files_before = ctx.pre.get("files") or {}
    kept = sorted(ctx.keep_for_data)
    kept_repos = {f"pinka-{r}." for c in kept for r in ctx.component_repos.get(c, [])}
    if method in ("apt_preferences", "pacman_ignorepkg"):
        path = family["hold_file"]
        blocks = hold_blocks(host.read(path))
        mine = [b for b in blocks if b not in kept]
        items += [
            item("packages", "hold", b, "keep", f"{b} stays: {ctx.keep_for_data[b]}")
            for b in blocks
            if b in kept
        ]
        if mine:
            items.append(
                item(
                    "packages",
                    "hold_file",
                    path,
                    "remove",
                    f"pinka's holds: {', '.join(mine)}",
                    path=path,
                    blocks=mine,
                    delete_if_empty=method == "apt_preferences" and path not in files_before,
                )
            )
    else:
        held = list(ctx.added.get("holds") or [])
        for component in removed_components:
            held += ctx.component_packages.get(component, [])
        before = files_before.get(family["lock_file"]) or ""
        for name in sorted(set(held)):
            if re.search(rf"(^|[\s:/]){re.escape(name)}([-:\s.]|$)", before):
                items.append(item("packages", "hold", name, "keep", "it was held before pinka"))
            elif name in kept_packages:
                items.append(item("packages", "hold", name, "keep", "its component stays"))
            else:
                items.append(item("packages", "hold", name, "remove", "pinka held it"))
    for pattern in family["repo_globs"]:
        for path in host.glob(pattern):
            if any(Path(path).name.startswith(r) for r in kept_repos):
                items.append(item("packages", "repo_file", path, "keep", "its component stays"))
            else:
                items.append(
                    item("packages", "repo_file", path, "remove", "pinka's repository or key file")
                )
    return items


def plan_packages(ctx: Context) -> list[Item]:
    """Holds first, then repositories and keys, then pinka's packages, then their dependencies (§5.4)."""
    host, family, items = ctx.host, ctx.family, []
    installed = installed_packages(ctx)
    removed_components = [
        c for c in COMPONENTS if ctx.origin(c) == "unknown" and c in ctx.remove_components
    ]
    kept_packages = {n for c in ctx.keep_for_data for n in ctx.component_packages.get(c, [])}
    items += plan_holds_and_repos(ctx, removed_components, kept_packages)
    if family.get("rpm_keys"):
        recorded = [str(k) for k in ctx.added.get("repo_keys") or []]
        for fingerprint, held in sorted(rpm_key_packages(ctx, recorded).items()):
            items.append(
                item(
                    "packages",
                    "repo_key",
                    fingerprint,
                    "remove",
                    "pinka imported this key into rpm",
                    packages=held,
                )
            )
    names = list(ctx.added.get("packages") or [])
    for component in removed_components:
        names += ctx.component_packages.get(component, [])
    for name in sorted(set(names)):
        if name.startswith("/"):
            items.append(
                item("packages", "package", name, "keep", "a file requirement, not a package name")
            )
        elif name not in installed:
            continue
        elif name in ctx.names["never_remove_packages"]:
            items.append(
                item(
                    "packages",
                    "package",
                    name,
                    "keep",
                    "the SSH server stays: uninstall never stops SSH, and the purge of the SSH "
                    "server deletes the host keys",
                )
            )
        elif name in kept_packages:
            items.append(item("packages", "package", name, "keep", "its component stays"))
        else:
            items.append(package_item(ctx, name))
    removing = any(i["kind"] == "package" and i["action"] == "remove" for i in items)
    if ctx.usable and removing:
        items.append(
            item(
                "packages",
                "dependencies",
                "dependencies of pinka's packages",
                "remove",
                "only those absent at the first install that no package needs any more",
            )
        )
    if removing and not ctx.purge:
        guarded = guard_candidates(ctx)
        items.insert(
            0,
            item(
                "packages",
                "guard",
                "the kept data",
                "keep",
                "mounted read-only while packages are removed, so that no package script can "
                f"delete it: {', '.join(guarded)}",
                paths=guarded,
            ),
        )
    if ctx.removes("nvidia"):
        items += [
            item(
                "packages",
                "mok",
                key,
                "keep",
                f"the Machine Owner Key stays enrolled in the firmware: mokutil --delete {key}, "
                "then reboot and confirm it in the MOK Manager",
            )
            for key in ctx.names["mok_keys"]
            if host.exists(key)
        ]
    items += kept_component(ctx, "packages", "docker", "Docker Engine")
    items += kept_component(ctx, "packages", "nvidia", "the NVIDIA driver")
    return items


def plan_accounts(ctx: Context) -> list[Item]:  # noqa: PLR0912 - memberships, keys, platforms, accounts, groups
    """Memberships and keys pinka added, then its accounts and groups, then the platforms' (§5.4, §6.1)."""
    items: list[Item] = []
    passwd, groups = ctx.passwd, ctx.groups
    for entry in ctx.added.get("memberships") or []:
        user, group = entry["user"], entry["group"]
        if user in members(groups, group):
            items.append(
                item(
                    "accounts",
                    "membership",
                    f"{user} in {group}",
                    "remove",
                    "pinka added it",
                    user=user,
                    group=group,
                )
            )
    for entry in ctx.added.get("keys") or []:
        user = entry["user"]
        if user in passwd:
            items.append(
                item(
                    "accounts",
                    "key",
                    f"{user}: {entry['key'][:40]}...",
                    "remove",
                    "pinka added this key",
                    user=user,
                    key=entry["key"],
                    home=passwd[user][4],
                )
            )
    before = ctx.pre.get("groups") or {}
    for group in ctx.names["restored_memberships"]:
        for user in before.get(group) or []:
            if user in passwd and group in groups and user not in members(groups, group):
                items.append(
                    item(
                        "accounts",
                        "membership",
                        f"{user} in {group}",
                        "restore",
                        "pinka took it away",
                        user=user,
                        group=group,
                    )
                )
    users_before = set(ctx.pre.get("users") or [])
    accounts: dict[str, str] = {}
    for platform in ctx.platforms():
        for name in (platform.get("names") or {}).get("accounts_groups", []):
            accounts.setdefault(name, f"the platform {platform.get('name')}'s account")
    if ctx.usable:
        for name in ctx.added.get("accounts") or []:
            accounts.setdefault(name, "pinka created it")
    else:
        for name in ctx.names["infra_accounts"]:
            home = (passwd.get(name) or ["", "", "", "", ""])[4]
            if home.startswith(ctx.names["account_home_prefix"]):
                accounts.setdefault(name, "pinka's reserved account")
    researchers = set(lab_researchers(ctx))
    for name, why in sorted(accounts.items()):
        if name not in passwd:
            continue
        if name in users_before:
            items.append(item("accounts", "account", name, "keep", "it existed before pinka"))
            continue
        home = passwd[name][4]
        keep_home = (
            not ctx.purge
            and home
            and not home.startswith(ctx.names["account_home_prefix"])
            and ctx.host.exists(home)
        )
        items.append(
            item(
                "accounts",
                "account",
                name,
                "remove",
                why + (f"; its home {home} is kept (--purge-data removes it)" if keep_home else ""),
                home=home,
                remove_home=bool(home)
                and ctx.purge
                and not home.startswith(ctx.names["account_home_prefix"]),
            )
        )
        researchers.discard(name)
    if not ctx.usable:
        items += [
            item("accounts", "account", name, "keep", "origin unknown (no install record)")
            for name in sorted(researchers)
            if name in passwd
        ]
    group_names: dict[str, str] = {}
    for platform in ctx.platforms():
        for name in (platform.get("names") or {}).get("accounts_groups", []):
            group_names.setdefault(name, f"the platform {platform.get('name')}'s group")
    if ctx.usable:
        for name in ctx.added.get("groups") or []:
            group_names.setdefault(name, "pinka created it")
        for name in ctx.added.get("accounts") or []:
            if name not in before:
                group_names.setdefault(name, "the primary group of an account pinka created")
        for component, names in ctx.names["component_groups"].items():
            if ctx.removes(component):
                for name in names:
                    if name not in before:
                        group_names.setdefault(
                            name, "its package created it; pinka installed the package"
                        )
    else:
        for name in ctx.names["reserved_groups"] + ctx.names["infra_accounts"]:
            group_names.setdefault(name, "pinka's reserved group")
    for name, why in sorted(group_names.items()):
        if name in groups and name not in before:
            items.append(item("accounts", "group", name, "remove", why))
    return items


def lab_researchers(ctx: Context) -> list[str]:
    """The researchers the lab description names."""
    accounts = ctx.lab.get("accounts") or {}
    return [
        str(r.get("name", r)) if isinstance(r, Mapping) else str(r)
        for r in accounts.get("researchers") or []
    ]


def unit_state(ctx: Context, unit: str) -> str:
    """``systemctl is-enabled`` of a unit (``enabled``, ``masked``, ...), or empty."""
    _, out, _ = ctx.host.run(["systemctl", "is-enabled", unit])
    return out.strip().splitlines()[0] if out.strip() else ""


def file_mode_of(ctx: Context, path: str) -> dict[str, str]:
    """A shared file's mode and owner before pinka (the record), or the defaults."""
    recorded = (ctx.pre.get("file_modes") or {}).get(path) or {}
    default = ctx.names["dropin_modes"].get(path) or {
        "mode": "0644",
        "owner": "root",
        "group": "root",
    }
    return {**default, **recorded}


def plan_dropin(ctx: Context, dropin: Mapping[str, Any]) -> list[Item]:  # noqa: PLR0911 - one case each
    """Restore or remove one file pinka writes (§5.4, §7.21)."""
    host, path = ctx.host, str(dropin["path"])
    reload = str(dropin.get("reload") or "")
    if dropin.get("component") and not ctx.removes(str(dropin["component"])):
        return []
    before = (ctx.pre.get("files") or {}).get(path)
    current = host.read(path) if host.exists(path) else None
    if ctx.usable and path in (ctx.pre.get("files") or {}):
        if before is not None and before.startswith("<") and before.endswith(">"):
            return [
                item(
                    "dropins",
                    "file",
                    path,
                    "keep",
                    f"its content before pinka was not kept: {before}",
                )
            ]
        if current == before:
            return []
        return [
            item(
                "dropins",
                "file",
                path,
                "restore",
                "as it was before pinka",
                content=before,
                reload=reload,
                **file_mode_of(ctx, path),
            )
        ]
    if current is None and not host.exists(path):
        return []
    if ctx.usable or "pinka" in Path(path).name:
        return [item("dropins", "file", path, "remove", "pinka wrote it", reload=reload)]
    return [item("dropins", "file", path, "keep", "origin unknown (no install record)")]


def plan_dropins(ctx: Context) -> list[Item]:  # noqa: PLR0912 - one kind of file or setting each
    """Drop-in files and edits, and the settings pinka changed, set back (§5.4)."""
    host, names, items = ctx.host, ctx.names, []
    for dropin in names["dropins"]:
        items += plan_dropin(ctx, dropin)
    for path, edit in sorted(names["edited"].items()):
        text = host.read(path)
        line = str(edit["line"])
        if text is None or line not in text.splitlines():
            continue
        before = (ctx.pre.get("files") or {}).get(path)
        if ctx.usable and before is not None and line not in before.splitlines():
            items.append(
                item(
                    "dropins",
                    "edited_line",
                    path,
                    "restore",
                    f"the line pinka added goes: {line}",
                    line=line,
                    reload=str(edit.get("reload") or ""),
                    **file_mode_of(ctx, path),
                )
            )
        elif not ctx.usable:
            items.append(
                item(
                    "dropins",
                    "edited_line",
                    path,
                    "keep",
                    f"origin unknown (no install record): {line}",
                )
            )
    for pattern in names["dropin_globs"]:
        for path in host.glob(pattern):
            if not Path(path).parent.name.startswith(names["unit_prefix"]):
                items.append(
                    item(
                        "dropins", "file", path, "remove", "pinka wrote it", reload="daemon-reload"
                    )
                )
    for key, before in sorted(ctx.changed.items()):
        if key == "timezone":
            items.append(
                item(
                    "dropins",
                    "timezone",
                    "the time zone",
                    "restore",
                    f"back to {before}",
                    value=before,
                )
            )
        elif key.startswith("sysctl:"):
            setting = key[len("sysctl:") :]
            items.append(
                item(
                    "dropins",
                    "sysctl",
                    setting,
                    "restore",
                    f"back to {before} now",
                    key=setting,
                    value=before,
                )
            )
    if host.has("semanage"):
        rc, out, _ = host.run(["semanage", "fcontext", "--list", "--locallist"])
        for line in out.splitlines() if rc == 0 else []:
            spec = line.split()[0] if line.split() else ""
            if spec.startswith(f"{ctx.pinka_root}/"):
                items.append(
                    item(
                        "dropins",
                        "selinux_fcontext",
                        spec,
                        "remove",
                        "pinka's SELinux file context",
                        spec=spec,
                    )
                )
    if ctx.usable:
        enabled_before = set(ctx.pre.get("units_enabled") or [])
        # A component that stays because it holds kept data keeps its units running.
        staying = {u for c in ctx.keep_for_data for u in names["component_units"].get(c, [])}
        for unit in names["distribution_units"]:
            if unit in staying:
                continue
            if unit not in enabled_before and unit_state(ctx, unit) == "enabled":
                if unit in names["never_stop"]:
                    items.append(
                        item(
                            "dropins",
                            "unit_state",
                            unit,
                            "keep",
                            "pinka enabled it; uninstall never stops SSH: systemctl disable it yourself",
                        )
                    )
                else:
                    items.append(
                        item(
                            "dropins",
                            "unit_state",
                            unit,
                            "restore",
                            "disabled and stopped, as before pinka",
                        )
                    )
    masked_before = set(ctx.pre.get("units_masked") or [])
    marker = host.read(names["sleep_marker"]) or ""
    for target in names["sleep_targets"]:
        mine = target not in masked_before if ctx.usable else target in marker.split()
        if mine and unit_state(ctx, target) == "masked":
            items.append(item("dropins", "unmask", target, "restore", "unmasked, as before pinka"))
    if any(i.get("reload") == "initramfs" for i in items) or ctx.removes("nvidia"):
        items.append(
            item(
                "dropins",
                "initramfs",
                "the initramfs",
                "restore",
                "rebuilt without pinka's nouveau blacklist; reboot to load it",
            )
        )
    return items


def platform_links(ctx: Context) -> list[str]:
    """The platforms' CLI links in /usr/local/bin."""
    found = set()
    cli = (ctx.rendered or {}).get("cli") or {}
    if cli.get("link"):
        found.add(str(cli["link"]))
    for platform in ctx.platforms():
        for command in (platform.get("names") or {}).get("commands", []):
            found.add(f"{ctx.names['bin_dir']}/{command}")
    return sorted(found)


def plan_paths(ctx: Context) -> list[Item]:  # noqa: PLR0912 - one kind of path each
    """Pinka's paths, the record last; the data, the logs and the remote side kept (§5.4)."""
    host, names, items = ctx.host, ctx.names, []
    for link in sorted(set(names["links"]) | set(platform_links(ctx))):
        target = host.link_target(link)
        if target and target.startswith(tuple(names["link_targets"])):
            items.append(item("paths", "path", link, "remove", f"pinka's link to {target}"))
    for path in names["paths"]:
        if host.exists(path):
            items.append(item("paths", "path", path, "remove", "pinka's path"))
    if host.exists(names["state_dir"]):
        items.append(
            item(
                "paths",
                "state_dir",
                names["state_dir"],
                "remove",
                "pinka's state; the install record goes last",
                keep=names["record"],
            )
        )
    data = [
        (ctx.pinka_root, "the lab's data: the database files, models, datasets, images, backups")
    ]
    for component in sorted(names["component_data"]):
        if ctx.removes(component):
            data += [(p, f"{component}'s data") for p in ctx.component_data(component)]
    for path, what in data:
        if host.exists(path):
            if ctx.purge:
                items.append(item("paths", "data", path, "remove", f"{what} (--purge-data)"))
            else:
                items.append(
                    item("paths", "data", path, "keep", f"{what}; --purge-data removes it")
                )
    items += [
        item("paths", "log", path, "keep", "the logs of the install and of this uninstall")
        for path in names["logs"]
        if host.exists(path)
    ]
    if host.exists(names["keep_dir"]) or any(i["kind"] == "keep_copy" for i in ctx.planned):
        items.append(
            item(
                "paths",
                "kept",
                names["keep_dir"],
                "keep",
                "what uninstall kept outside pinka's paths (root only); --purge-data keeps it "
                "too: the backups need its key",
            )
        )
    if (ctx.lab.get("backup") or {}).get("configured"):
        items.append(
            item(
                "paths",
                "remote",
                "the restic repository",
                "keep",
                "uninstall never deletes the backups, wherever the repository is",
            )
        )
    if host.exists(names["deploy_key"]):
        items.append(
            item(
                "paths",
                "remote",
                "the platform's deploy key",
                "keep",
                "its public half stays registered on the Git host: remove it there",
            )
        )
    if host.exists(names["record"]):
        items.append(
            item("paths", "record", names["record"], "remove", "the install record, last of all")
        )
    # Bootstrap installed it for this run when it was missing: then it is no
    # reason to uninstall, though bootstrap removes it all the same.
    items.append(
        item(
            "paths", "tooling", names["opt_dir"], "remove", PENDING_REASON, counted=ctx.opt_existed
        )
    )
    return items


PLANNERS = {
    "platform": plan_platform,
    "keep": plan_keep,
    "units": plan_units,
    "compose": plan_compose,
    "incus": plan_incus,
    "firewall": plan_firewall,
    "tailscale": plan_tailscale,
    "packages": plan_packages,
    "accounts": plan_accounts,
    "dropins": plan_dropins,
    "paths": plan_paths,
}


def warnings_for(ctx: Context, items: Sequence[Item]) -> list[str]:
    """What the operator should know before confirming."""
    found = []
    session = ctx.session
    via_tailnet = session.get("via") == "ssh" and session.get("tailnet")
    if via_tailnet and any(i["kind"] == "logout" for i in items):
        found.append(
            "This SSH session comes over the tailnet, and uninstall logs the node out: the session "
            "ends then. Uninstall goes on; read its report afterwards in /var/log/pinka-uninstall."
        )
    if any(i["kind"] == "firewalld_service" for i in items) and session.get("via") == "ssh":
        found.append(
            "SSH closes again in the zones where pinka opened it; make sure this session does not "
            "depend on one of them."
        )
    if any(i["kind"] in ("initramfs", "mok") for i in items):
        found.append("A reboot is needed afterwards; uninstall never reboots.")
    if any(i["kind"] == "file" and i["name"] == "/etc/wsl.conf" for i in items):
        found.append(
            "WSL: restart the distribution afterwards (wsl --terminate <distro> on Windows)."
        )
    return found


def build_plan(ctx: Context) -> dict[str, Any]:
    """The whole plan, in removal order (§5.4)."""
    items: list[Item] = ctx.planned
    for phase in PHASES:
        items += PLANNERS[phase](ctx)
    refusal = ""
    if ctx.usable and ctx.remove_components:
        refusal = (
            "--remove-component applies only on a host without a usable install record; "
            "this host's record says what pinka installed"
        )
    backup = bool((ctx.lab.get("backup") or {}).get("configured")) and ctx.host.exists(
        ctx.names["pinka_cli"]
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": utc_now(),
        "machine": ctx.machine,
        "record": ctx.record_state,
        "purge_data": ctx.purge,
        "remove_components": ctx.remove_components,
        "refusal": refusal,
        "warnings": warnings_for(ctx, items),
        "backup": backup,
        "to_remove": sum(1 for i in items if i["action"] != "keep" and i.get("counted", True)),
        "keep_for_data": ctx.keep_for_data,
        "kept_dir": ctx.names["keep_dir"],
        "backup_access": backup_access(ctx, items),
        "follow_ups": follow_ups(ctx, items),
        "items": items,
    }


def backup_access(ctx: Context, items: Sequence[Item]) -> str:
    """Where the kept key and settings of the backups are, or empty when there are none."""
    copies = {i["name"]: i["dest"] for i in items if i["kind"] == "keep_copy"}
    secrets = ctx.names["kept_copies"][0]["source"]
    return str(copies.get(secrets, ""))


def deployed_repo(ctx: Context) -> str:
    """The repository the active platform was deployed from (its DEPLOYED_FROM.json), or empty."""
    target = ((ctx.active or {}).get("release") or {}).get("target")
    record = read_json(ctx.host, f"{target}/DEPLOYED_FROM.json") if target else None
    return str(((record or {}).get("source") or {}).get("repo") or "")


def kept_secrets(ctx: Context, items: Sequence[Item]) -> list[str]:
    """What in the keep directory grants access to the backups: planned copies or kept files."""
    planned = {i["name"] for i in items if i["kind"] == "keep_copy"}
    keep = ctx.names["keep_dir"]
    return [
        str(c["secret"])
        for c in ctx.names["kept_copies"]
        if c.get("secret") and (c["source"] in planned or ctx.host.exists(f"{keep}/{c['dest']}"))
    ]


def secrets_follow_up(keep: str, secrets: Sequence[str]) -> str:
    """The first follow-up: take the kept secrets off the host, then delete them securely."""
    parent, name = str(Path(keep).parent), Path(keep).name
    return (
        f"{keep} holds {' and '.join(secrets)}: they grant access to the backups. Copy it off "
        f"this host, then delete it securely, as root: tar -C {parent} -cf - {name} | ssh "
        "<you>@<safe-host> 'umask 077; cat > pinka-kept.tar' (or copy it to removable media), "
        f"then find {keep} -type f -exec shred -u {{}} + && rm -rf {keep}. On an SSD or a "
        "copy-on-write file system (btrfs) shred cannot reach every old copy: then also change "
        "the repository's password (restic key passwd, from the copy) and replace the SFTP key "
        "on the backup server."
    )


def follow_ups(ctx: Context, items: Sequence[Item]) -> list[str]:
    """What the owner does afterwards, the kept secrets first (owner, D2 review and "commit")."""
    found = []
    secrets = kept_secrets(ctx, items)
    if secrets:
        found.append(secrets_follow_up(ctx.names["keep_dir"], secrets))
    if any(i["kind"] == "logout" for i in items):
        me = (query_json(ctx.host, ["tailscale", "status", "--json"]) or {}).get("Self") or {}
        node = str(me.get("HostName") or ctx.machine or "this host")
        addresses = ", ".join(str(a) for a in me.get("TailscaleIPs") or [])
        found.append(
            f"Delete the node {node}{f' ({addresses})' if addresses else ''} in the Tailscale "
            "admin console (Machines): uninstall logs it out, which does not take it off the "
            "tailnet."
        )
    deploy_key = ctx.names["deploy_key"]
    if ctx.host.exists(deploy_key):
        rc, out, _ = ctx.host.run(["ssh-keygen", "-l", "-f", str(ctx.host.path(deploy_key))])
        words = out.split() if rc == 0 else []
        key = words[1] if len(words) > 1 else deploy_key
        repo = deployed_repo(ctx)
        where = f"the Git host of {repo}" if repo else "the Git host of the platform's repository"
        found.append(
            f"Revoke the platform's read-only deploy key {key} on {where}: uninstall removes "
            "its private half from this host."
        )
    return found


def plan_text(plan: Mapping[str, Any]) -> str:
    """The plan for people, in removal order, what stays at the end."""
    items = plan["items"]
    data = "removed (--purge-data)" if plan["purge_data"] else "kept"
    lines = [
        f"Uninstall plan for {plan['machine'] or 'this host'} "
        f"(install record: {plan['record']}; the lab's data: {data})",
        "",
    ]
    width = max([len(i["name"]) for i in items] + [10])
    width = min(width, 32)
    for phase in PHASES:
        acting = [i for i in items if i["phase"] == phase and i["action"] != "keep"]
        if acting:
            lines.append(f"{phase}: {PHASE_TITLES[phase]}")
            lines += [f"  {i['action']:<8} {i['name']:<{width}}  {i['reason']}" for i in acting]
    kept = [i for i in items if i["action"] == "keep"]
    if kept:
        lines += ["", "Kept:"]
        lines += [f"  keep     {i['name']:<{width}}  {i['reason']}" for i in kept]
    if plan.get("backup_access"):
        lines += [
            "",
            f"The kept backups open with the key and settings in {plan['backup_access']} "
            f"(root only; {plan['kept_dir']}/README.txt says how).",
        ]
    if plan.get("follow_ups"):
        lines += ["", "Afterwards:"]
        lines += [f"  - {f}" for f in plan["follow_ups"]]
    if plan["warnings"]:
        lines += ["", "Warnings:"]
        lines += [f"  - {w}" for w in plan["warnings"]]
    if plan["refusal"]:
        lines += ["", f"Refused: {plan['refusal']}"]
    restore = sum(1 for i in items if i["action"] == "restore")
    remove = sum(1 for i in items if i["action"] == "remove" and i.get("counted", True))
    lines += ["", f"{remove} to remove, {restore} to restore, {len(kept)} kept."]
    return "\n".join(lines) + "\n"


# --- the removal, kind by kind ------------------------------------------------------------------

GONE_RE = re.compile(r"not found|no such|does not exist|doesn't exist|unknown", re.IGNORECASE)


def outcome(
    host: Host, argv: Sequence[str], done: str = "removed", gone: re.Pattern[str] = GONE_RE
) -> tuple[str, str]:
    """Run a removal command: ``done``, ``already_gone`` when its error says so, else ``failed``."""
    rc, out, err = host.run(argv)
    message = (err.strip() or out.strip()).splitlines()
    detail = message[-1] if message else f"exit status {rc}"
    if rc == 0:
        return done, ""
    if gone.search(detail):
        return "already_gone", ""
    return "failed", f"{' '.join(argv[:3])}: {detail}"


def apply_stop(ctx: Context, it: Item) -> tuple[str, str]:
    """``pinka stop``: the platform's drain hook, the arbiter idle, the targets stopped."""
    host = ctx.host
    targets = ["systemctl", "stop", "pinka-platform.target", "pinka-lab.target"]
    if host.exists(ctx.names["pinka_cli"]):
        rc, out, err = host.run([ctx.names["pinka_cli"], "stop"])
        if rc == 0:
            return "removed", "drained and stopped"
        detail = (err.strip() or out.strip()).splitlines()[-1:] or [f"exit status {rc}"]
        result, why = outcome(host, targets)
        return (
            result,
            f"pinka stop failed ({detail[0]}); stopped with systemctl"
            if result == "removed"
            else why,
        )
    return outcome(host, targets)


def apply_unit(ctx: Context, it: Item) -> tuple[str, str]:
    """Stop and disable a unit, remove its file, drop-ins and links."""
    host, unit = ctx.host, it["name"]
    path = ctx.unit_file(unit)
    stem, _, suffix = unit.rpartition(".")
    if it.get("template"):
        host.run(["systemctl", "stop", f"{stem}*.{suffix}"])
    host.run(["systemctl", "disable", "--now", unit])
    links = [
        p
        for pattern in (f"*.wants/{unit}", f"*.requires/{unit}", f"*.wants/{stem}*.{suffix}")
        for p in host.glob(f"{ctx.names['unit_dir']}/{pattern}")
    ]
    found = host.exists(path) or bool(links) or host.exists(f"{path}.d")
    for leftover in [*sorted(set(links)), f"{path}.d", path]:
        if host.exists(leftover):
            host.remove(leftover)
    return ("removed", "") if found else ("already_gone", "")


def apply_incus(ctx: Context, it: Item) -> tuple[str, str]:
    """Remove one Incus object pinka created, or set a server setting back."""
    project = ["--project", str(it.get("project", ""))] if it.get("project") else []
    obj = str(it.get("object", it["name"]))
    commands = {
        "instance": ["incus", "delete", "--force", *project, obj],
        "image": ["incus", "image", "delete", *project, obj],
        "volume": ["incus", "storage", "volume", "delete", str(it.get("pool", "")), obj, *project],
        "profile": ["incus", "profile", "delete", obj, *project],
        "project": ["incus", "project", "delete", obj],
        "network": ["incus", "network", "delete", obj],
        "acl": ["incus", "network", "acl", "delete", obj],
        "certificate": ["incus", "config", "trust", "remove", obj],
        "pool": ["incus", "storage", "delete", obj],
    }
    if it["kind"] == "incus_config":
        value = str(it.get("value") or "")
        argv = (
            ["incus", "config", "set", it["name"], value]
            if value
            else ["incus", "config", "unset", it["name"]]
        )
        return outcome(ctx.host, argv, done="restored")
    return outcome(ctx.host, commands[it["kind"]])


def firewalld_restore(ctx: Context, it: Item) -> tuple[str, str]:
    """Put an interface back in its zone, or out of pinka's."""
    command = firewall_cmd(ctx)
    if command is None:
        return "failed", "firewalld cannot be reached"
    interface, zone = it["name"], str(it.get("zone") or "")
    rc, out, _ = ctx.host.run([*command, f"--get-zone-of-interface={interface}"])
    current = out.strip() if rc == 0 else ""
    if zone:
        if current == zone:
            return "already_gone", ""
        return outcome(
            ctx.host,
            [*command, f"--zone={zone}", f"--change-interface={interface}"],
            done="restored",
        )
    if current in ctx.names["firewalld_zones"]:
        return outcome(
            ctx.host,
            [*command, f"--zone={current}", f"--remove-interface={interface}"],
            done="restored",
        )
    return "already_gone", ""


def apply_firewall(ctx: Context, it: Item) -> tuple[str, str]:  # noqa: PLR0911 - one command per kind
    """Remove one firewall object pinka added."""
    host, kind = ctx.host, it["kind"]
    if kind == "nft_table":
        return outcome(host, ["nft", "delete", "table", *it["name"].split()])
    if kind == "docker_user_rule":
        rule = [str(w) for w in it["rule"]]
        if not host.ok([it["tool"], "-C", "DOCKER-USER", *rule]):
            return "already_gone", ""
        return outcome(host, [it["tool"], "-D", "DOCKER-USER", *rule])
    if kind == "interface":
        return outcome(host, ["ip", "link", "delete", it["name"]])
    if kind == "firewalld_interface":
        return firewalld_restore(ctx, it)
    command = firewall_cmd(ctx)
    if kind.startswith("firewalld_") and command is None:
        return "failed", "firewalld cannot be reached"
    if kind == "firewalld_service":
        return outcome(
            host, [*(command or []), f"--zone={it['zone']}", f"--remove-service={it['service']}"]
        )
    if kind == "firewalld_trusted":
        return outcome(
            host, [*(command or []), "--zone=trusted", f"--remove-interface={it['name']}"]
        )
    if kind == "firewalld_zone":
        return outcome(host, [*(command or []), f"--delete-zone={it['name']}"])
    words = it["name"].split()[1:]
    if words[-2:] == ["comment", f"'{ctx.names['ufw_comment']}'"]:
        words = words[:-2]
    argv = (
        ["ufw", "route", "delete", *words[1:]]
        if words[:1] == ["route"]
        else ["ufw", "delete", *words]
    )
    return outcome(host, argv)


def apply_tailscale(ctx: Context, it: Item) -> tuple[str, str]:
    """Turn off a serve entry pinka added, or log out the node pinka joined."""
    if it["kind"] == "serve":
        scheme, _, port = str(it["entry"]).partition(":")
        return outcome(ctx.host, ["tailscale", "serve", f"--{scheme}={port}", "off"])
    return outcome(ctx.host, ["tailscale", "logout"])


def strip_blocks(text: str, names: Sequence[str] | None = None) -> str:
    """A file without pinka's blocks (``# BEGIN pinka <name>`` to ``# END pinka <name>``).

    Args:
        text: The file's content.
        names: Only these blocks (all of pinka's when None).
    """
    kept, inside = [], False
    for line in text.splitlines(keepends=True):
        begin = re.match(r"^# BEGIN pinka (.+?)\s*$", line)
        if begin and (names is None or begin.group(1) in names):
            inside = True
        elif inside and re.match(r"^# END pinka ", line):
            inside = False
        elif not inside:
            kept.append(line)
    return "".join(kept)


def apply_hold(ctx: Context, it: Item) -> tuple[str, str]:
    """Take off a hold pinka added: its block in a file, or the package manager's lock."""
    host = ctx.host
    if it["kind"] == "hold_file":
        path = it["path"]
        text = host.read(path)
        names = it.get("blocks")
        if text is None or not set(hold_blocks(text)) & set(names or hold_blocks(text)):
            return "already_gone", ""
        rest = strip_blocks(text, names)
        if it.get("delete_if_empty") and not rest.strip():
            host.remove(path)
        else:
            host.write(path, rest, host.mode(path))
        return "removed", ""
    rc, out, _ = host.run(ctx.family["holds_list"])
    if rc == 0 and not re.search(
        rf"(^|[\s:/]){re.escape(it['name'])}([-:\s.]|$)", out, re.MULTILINE
    ):
        return "already_gone", ""
    return outcome(host, [*ctx.family["unhold"], it["name"]])


def apply_repo_key(ctx: Context, it: Item) -> tuple[str, str]:
    """Remove from rpm's keyring a key pinka imported."""
    results = [outcome(ctx.host, ["rpm", "-e", "--allmatches", key]) for key in it["packages"]]
    failed = [why for result, why in results if result == "failed"]
    if failed:
        return "failed", "; ".join(failed)
    return ("removed", "") if any(r == "removed" for r, _ in results) else ("already_gone", "")


def blocked_packages(ctx: Context, output: str, candidates: Sequence[str]) -> set[str]:
    """The candidates a test removal says other packages still need."""
    found = set()
    for match in re.finditer(ctx.family["blocked"], output, re.MULTILINE):
        groups = match.groupdict()
        if groups.get("package"):
            found.add(groups["package"].split(":")[0])
        elif groups.get("capability"):
            rc, out, _ = ctx.host.run([*ctx.family["provides"], groups["capability"]])
            found.update(n for n in out.split() if rc == 0 and n in candidates)
    return found & set(candidates)


def remove_packages(ctx: Context, names: Sequence[str]) -> dict[str, tuple[str, str]]:
    """Remove packages exactly, never one another package still needs (§5.4).

    A test removal names the candidates others depend on; they are kept and
    the rest is tested again, until the test passes or nothing is left.

    Returns:
        Each package's result.
    """
    installed = installed_packages(ctx)
    results: dict[str, tuple[str, str]] = {
        n: ("already_gone", "") for n in names if n not in installed
    }
    candidates = [n for n in names if n in installed]
    while candidates:
        rc, out, err = ctx.host.run([*ctx.family["test_remove"], *candidates])
        if rc == 0:
            break
        blocked = blocked_packages(ctx, f"{out}\n{err}", candidates)
        if not blocked:
            detail = (err.strip() or out.strip()).splitlines()[-1:] or [f"exit status {rc}"]
            results.update(
                {n: ("failed", f"the test removal failed: {detail[0]}") for n in candidates}
            )
            return results
        for name in blocked:
            results[name] = ("kept", "another package still needs it")
        candidates = [n for n in candidates if n not in blocked]
    if not candidates:
        return results
    # A package whose purge deletes kept data is removed without purging
    # (owner, D2 review): its data and configuration stay.
    deletes = {} if ctx.purge else dict(ctx.family.get("purge_deletes") or {})
    keeping = [n for n in candidates if n in deletes]
    purging = [n for n in candidates if n not in deletes]
    details = {}
    for command, group in (
        (ctx.family["remove"], purging),
        (ctx.family["remove_keeping_config"], keeping),
    ):
        if group:
            rc, out, err = ctx.host.run([*command, *group])
            detail = (err.strip() or out.strip()).splitlines()[-1:] or [f"exit status {rc}"]
            details.update(dict.fromkeys(group, detail[0]))
    left = installed_packages(ctx)
    for name in candidates:
        if name in left:
            results[name] = ("failed", f"still installed: {details[name]}")
        elif name in deletes:
            results[name] = (
                "removed",
                f"removed, not purged: its purge deletes {', '.join(deletes[name])}, which stays",
            )
        else:
            results[name] = ("removed", "")
    return results


def unneeded(ctx: Context) -> list[str]:
    """What the package manager says no package needs any more (installed as a dependency)."""
    rc, out, _ = ctx.host.run(ctx.family["unneeded"])
    if rc != 0:
        return []
    return sorted(
        {m.group("package") for m in re.finditer(ctx.family["unneeded_line"], out, re.MULTILINE)}
    )


def remove_dependencies(ctx: Context) -> tuple[str, str]:
    """Remove the dependencies pinka's packages pulled in, as long as nothing needs them.

    Only packages absent at the first install (the record's package list) and
    no longer needed count; one the owner installed later is not a
    dependency and stays.
    """
    before = set(ctx.pre.get("packages") or {})
    removed: list[str] = []
    for _ in range(10):
        candidates = [n for n in unneeded(ctx) if n not in before and n not in removed]
        if not candidates:
            break
        done = remove_packages(ctx, candidates)
        now = [n for n, (result, _) in done.items() if result == "removed"]
        if not now:
            break
        removed += now
    if not removed:
        return "already_gone", ""
    return "removed", f"{len(removed)}: {', '.join(sorted(removed))}"


def apply_account(ctx: Context, it: Item) -> tuple[str, str]:  # noqa: PLR0911, PLR0912 - one command per kind
    """Remove a membership, a key, an account or a group; or put a membership back."""
    host, kind = ctx.host, it["kind"]
    if kind == "membership":
        groups = parse_db(host.read("/etc/group"), 3)
        present = it["user"] in members(groups, it["group"])
        if it["action"] == "restore":
            return (
                ("already_gone", "")
                if present
                else outcome(host, ["gpasswd", "-a", it["user"], it["group"]], "restored")
            )
        return (
            outcome(host, ["gpasswd", "-d", it["user"], it["group"]])
            if present
            else ("already_gone", "")
        )
    if kind == "key":
        path = f"{it['home']}/.ssh/authorized_keys"
        text = host.read(path)
        lines = (text or "").splitlines(keepends=True)
        kept = [line for line in lines if line.strip() != it["key"].strip()]
        if text is None or len(kept) == len(lines):
            return "already_gone", ""
        info = host.path(path).stat()
        host.write(path, "".join(kept), info.st_mode & 0o7777)
        if host.root == "/":
            os.chown(path, info.st_uid, info.st_gid)
        return "removed", ""
    if kind == "account":
        rc, out, err = host.run(["userdel", it["name"]])
        if rc == 6:  # noqa: PLR2004 - userdel: the account does not exist
            result, why = "already_gone", ""
        elif rc == 8:  # noqa: PLR2004 - userdel: the account is logged in
            return (
                "failed",
                f"{it['name']} is logged in or runs processes; end them and run uninstall again",
            )
        elif rc != 0:
            return (
                "failed",
                f"userdel: {(err.strip() or out.strip() or f'exit status {rc}').splitlines()[-1]}",
            )
        else:
            result, why = "removed", ""
        if it.get("remove_home") and it.get("home") and host.exists(it["home"]):
            host.remove(it["home"])
        return result, why
    rc, out, err = host.run(["groupdel", it["name"]])
    if rc == 6:  # noqa: PLR2004 - groupdel: the group does not exist
        return "already_gone", ""
    if rc != 0:
        return (
            "failed",
            f"groupdel: {(err.strip() or out.strip() or f'exit status {rc}').splitlines()[-1]}",
        )
    return "removed", ""


def reload_after(ctx: Context, reload: str) -> tuple[bool, str]:
    """What a changed file needs: a check first where a mistake locks people out, then a reload."""
    host = ctx.host
    if reload == "sshd":
        rc, out, err = host.run([ctx.family["sshd"], "-t"])
        if rc != 0:
            return False, f"sshd -t: {(err.strip() or out.strip()).splitlines()[-1:]}"
        if host.ok(["systemctl", "is-active", "--quiet", ctx.family["ssh_service"]]):
            host.run(["systemctl", "reload", ctx.family["ssh_service"]])
    elif reload == "sudoers":
        rc, out, err = host.run(["visudo", "-c"])
        if rc != 0:
            return False, f"visudo -c: {(err.strip() or out.strip()).splitlines()[-1:]}"
    elif reload == "journald":
        host.run(["systemctl", "restart", "systemd-journald"])
    elif reload == "sysctl":
        host.run(["sysctl", "--system"])
    elif reload == "ldconfig":
        host.run(["ldconfig"])
    elif reload == "daemon-reload":
        host.run(["systemctl", "daemon-reload"])
    return True, ""


def apply_file(ctx: Context, it: Item) -> tuple[str, str]:
    """Restore a shared file as it was, or remove pinka's; put it back if the check fails."""
    host, path = ctx.host, it["name"]
    old = host.read(path) if host.exists(path) else None
    old_mode = host.mode(path) if host.exists(path) else 0o644
    if it["kind"] == "edited_line":
        if old is None or it["line"] not in old.splitlines():
            return "already_gone", ""
        new = "".join(
            line for line in old.splitlines(keepends=True) if line.rstrip("\n") != it["line"]
        )
        host.write(path, new, int(it["mode"], 8), it["owner"], it["group"])
        result = "restored"
    elif it["action"] == "restore":
        if old == it["content"]:
            return "already_gone", ""
        host.write(path, it["content"], int(it["mode"], 8), it["owner"], it["group"])
        result = "restored"
    else:
        if old is None and not host.exists(path):
            return "already_gone", ""
        host.remove(path)
        parent = str(Path(path).parent)
        if parent.endswith(".d") and host.is_empty_dir(parent):
            host.path(parent).rmdir()
        result = "removed"
    ok, why = reload_after(ctx, str(it.get("reload") or ""))
    if not ok:
        if old is None:
            host.remove(path)
        else:
            host.write(path, old, old_mode)
        reload_after(ctx, str(it.get("reload") or ""))
        return "failed", f"put back as it was: {why}"
    return result, ""


def apply_setting(ctx: Context, it: Item) -> tuple[str, str]:  # noqa: PLR0911 - one command per kind
    """Set a setting back, or take off what pinka set in the system."""
    host, kind = ctx.host, it["kind"]
    if kind == "timezone":
        result = outcome(host, ["timedatectl", "set-timezone", it["value"]], done="restored")
        if result[0] == "failed" and host.exists(f"/usr/share/zoneinfo/{it['value']}"):
            if host.exists("/etc/localtime"):
                host.remove("/etc/localtime")
            host.path("/etc/localtime").symlink_to(f"/usr/share/zoneinfo/{it['value']}")
            return "restored", ""
        return result
    if kind == "sysctl":
        return outcome(host, ["sysctl", "-w", f"{it['key']}={it['value']}"], done="restored")
    if kind == "selinux_fcontext":
        return outcome(host, ["semanage", "fcontext", "--delete", it["spec"]])
    if kind == "unit_state":
        if unit_state(ctx, it["name"]) != "enabled":
            return "already_gone", ""
        return outcome(host, ["systemctl", "disable", "--now", it["name"]], done="restored")
    if kind == "unmask":
        if unit_state(ctx, it["name"]) != "masked":
            return "already_gone", ""
        return outcome(host, ["systemctl", "unmask", it["name"]], done="restored")
    return outcome(host, ctx.family["initramfs"], done="restored")


def missing_copies(ctx: Context, path: str) -> list[str]:
    """What below a path should have been copied to the keep directory and was not."""
    return [
        c["source"]
        for c in kept_copies(ctx)
        if (c["source"] == path or c["source"].startswith(path.rstrip("/") + "/"))
        and not ctx.host.exists(f"{ctx.names['keep_dir']}/{c['dest']}")
    ]


def apply_path(ctx: Context, it: Item) -> tuple[str, str]:
    """Remove a path of pinka's: the state directory keeps the record until its own item."""
    host, kind, path = ctx.host, it["kind"], it["name"]
    if kind == "tooling":
        return "pending", PENDING_REASON
    if not host.exists(path):
        return "already_gone", ""
    missing = missing_copies(ctx, path)
    if missing:
        return "failed", f"not removed: the kept copy of {', '.join(missing)} is missing"
    if kind == "state_dir":
        keep = str(it.get("keep") or "")
        for entry in sorted(host.path(path).iterdir()):
            if f"{path}/{entry.name}" != keep:
                host.remove(f"{path}/{entry.name}")
        return "removed", "the install record goes last"
    host.remove(path)
    if kind == "record":
        parent = str(Path(path).parent)
        if host.is_empty_dir(parent):
            host.path(parent).rmdir()
    return "removed", ""


KEEP_README = """\
What pinka's uninstall kept outside pinka's paths ({when}). Root only.

{copies}
The kept backups (restic; uninstall never deletes them) open with the kept
key, as root:

  export RESTIC_REPOSITORY_FILE={keep}/backup/secrets/repository
  export RESTIC_PASSWORD_FILE={keep}/backup/secrets/password
  {keep}/backup/restic snapshots

For an SFTP repository, add the kept key and the backup server's host keys:

  {keep}/backup/restic -o sftp.args='-i {keep}/backup/secrets/ssh_key \\
    -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes \\
    -o UserKnownHostsFile={keep}/backup/config/known_hosts' snapshots

The signing keys' public halves stay in the research space
(shared/signing-keys); they verify what the platform signed.

This directory grants access to the backups (the repository's password,
the SFTP key, the vault password). Copy it off this host, then delete it
securely, as root:

  tar -C {parent} -cf - {name} | ssh <you>@<safe-host> 'umask 077; cat > pinka-kept.tar'
  find {keep} -type f -exec shred -u {{}} + && rm -rf {keep}

On an SSD or a copy-on-write file system (btrfs) shred cannot reach every
old copy: then also change the repository's password (restic key passwd,
from the copy) and replace the SFTP key on the backup server.
"""


def copy_tree(src: Path, dest: Path, owners: bool) -> None:
    """Copy a file or a directory tree with its modes (and owners, as root on a host).

    A link is followed only at the top (the restic link names the binary).
    """
    if src.is_dir():
        shutil.copytree(src, dest, symlinks=True, dirs_exist_ok=True)
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
    if not owners:
        return
    pairs = [(src, dest)]
    if src.is_dir():
        pairs += [(src / rel, dest / rel) for rel in (p.relative_to(src) for p in src.rglob("*"))]
    for source, target in pairs:
        info = source.lstat() if source != src else source.stat()
        os.lchown(target, info.st_uid, info.st_gid)


def apply_keep_copy(ctx: Context, it: Item) -> tuple[str, str]:
    """Copy what stays into the keep directory (root only) and describe it there."""
    host = ctx.host
    src = host.path(it["name"])
    if not src.exists():
        return "already_gone", ""
    keep = host.path(ctx.names["keep_dir"])
    keep.mkdir(mode=0o700, parents=True, exist_ok=True)
    keep.chmod(0o700)
    copy_tree(src, host.path(it["dest"]), owners=host.root == "/" and os.geteuid() == 0)
    copies = "".join(
        f"  {c['dest']}: {c['what']} (from {c['source']})\n"
        for c in ctx.names["kept_copies"]
        if host.exists(f"{ctx.names['keep_dir']}/{c['dest']}")
    )
    host.write(
        f"{ctx.names['keep_dir']}/README.txt",
        KEEP_README.format(
            when=utc_now(),
            copies=copies,
            keep=ctx.names["keep_dir"],
            parent=str(Path(ctx.names["keep_dir"]).parent),
            name=Path(ctx.names["keep_dir"]).name,
        ),
        0o600,
    )
    return "kept", f"copied to {it['dest']}"


APPLIERS: dict[str, Callable[[Context, Item], tuple[str, str]]] = {
    "stop": apply_stop,
    "unit": apply_unit,
    "container": lambda ctx, it: outcome(
        ctx.host, ["docker", "rm", "--force", "--volumes", it["name"]]
    ),
    "network": lambda ctx, it: (
        apply_incus(ctx, it)
        if it["phase"] == "incus"
        else outcome(ctx.host, ["docker", "network", "rm", it["name"]])
    ),
    "image": lambda ctx, it: (
        apply_incus(ctx, it)
        if it["phase"] == "incus"
        else outcome(ctx.host, ["docker", "image", "rm", it["name"]])
    ),
    **{
        k: apply_incus
        for k in (
            "instance",
            "volume",
            "profile",
            "project",
            "acl",
            "certificate",
            "pool",
            "incus_config",
        )
    },
    **{
        k: apply_firewall
        for k in (
            "nft_table",
            "docker_user_rule",
            "interface",
            "firewalld_interface",
            "firewalld_service",
            "firewalld_trusted",
            "firewalld_zone",
            "ufw_rule",
        )
    },
    "serve": apply_tailscale,
    "logout": apply_tailscale,
    "hold_file": apply_hold,
    "hold": apply_hold,
    "repo_file": apply_path,
    "repo_key": apply_repo_key,
    "dependencies": lambda ctx, it: remove_dependencies(ctx),
    **{k: apply_account for k in ("membership", "key", "account", "group")},
    "file": apply_file,
    "edited_line": apply_file,
    **{
        k: apply_setting
        for k in ("timezone", "sysctl", "selinux_fcontext", "unit_state", "unmask", "initramfs")
    },
    **{k: apply_path for k in ("path", "state_dir", "data", "record", "tooling")},
}
EPILOGUES = {
    "units": [["systemctl", "daemon-reload"], ["systemctl", "reset-failed"]],
    "dropins": [["systemctl", "daemon-reload"]],
}


def result_of(it: Item, result: str, why: str) -> dict[str, Any]:
    """An item's line in the report."""
    reason = why or it["reason"]
    return {
        "phase": it["phase"],
        "kind": it["kind"],
        "name": it["name"],
        "action": it["action"],
        "result": result,
        "reason": reason,
    }


def apply_phase(ctx: Context, plan: Mapping[str, Any], phase: str) -> list[dict[str, Any]]:
    """Carry out one phase of a confirmed plan; every item gets its result.

    Args:
        ctx: The host and pinka's names.
        plan: The confirmed plan.
        phase: The phase.

    Returns:
        The results, in the plan's order.
    """
    items = [i for i in plan["items"] if i["phase"] == phase]
    guard = next((i for i in items if i["kind"] == "guard"), None)
    guarding = guard is not None
    guarded: list[str] = []
    problems: list[str] = []
    if guard is not None:
        release_guards(ctx)
        stop_components(ctx)
        guarded, problems = guard_data(ctx, [str(p) for p in guard.get("paths") or []])
    try:
        return apply_items(ctx, items, phase, guarded, problems)
    finally:
        if guarding:
            release_guards(ctx)


def guarded_result(it: Item, guarded: Sequence[str], problems: Sequence[str]) -> tuple[str, str]:
    """The guard's own result, and the dependencies' when the guard failed."""
    if it["kind"] == "dependencies":
        return "failed", "not removed: the kept data is not guarded"
    if problems:
        return "failed", "; ".join(problems)
    return "kept", f"read-only while packages went: {', '.join(guarded)}"


def apply_item(
    ctx: Context, it: Item, removed_packages: Mapping[str, tuple[str, str]]
) -> tuple[str, str]:
    """Carry out one item; a failure is the item's result, the rest goes on."""
    if it["kind"] == "keep_copy":
        try:
            return apply_keep_copy(ctx, it)
        except OSError as exc:  # the copy failed: the item says why, nothing else stops
            return "failed", f"{type(exc).__name__}: {exc}"
    if it["action"] == "keep":
        return "kept", ""
    if it["kind"] == "package":
        return removed_packages[it["name"]]
    try:
        return APPLIERS[it["kind"]](ctx, it)
    except Exception as exc:  # one item's failure is reported, the rest goes on
        return "failed", f"{type(exc).__name__}: {exc}"


def apply_items(
    ctx: Context, items: Sequence[Item], phase: str, guarded: Sequence[str], problems: Sequence[str]
) -> list[dict[str, Any]]:
    """Carry out a phase's items (the packages phase inside its guard)."""
    packages = [i["name"] for i in items if i["kind"] == "package" and i["action"] == "remove"]
    if problems:
        # Unguarded, a package script could delete kept data: no package goes.
        why = f"not removed: the kept data could not be guarded ({'; '.join(problems)})"
        removed_packages = dict.fromkeys(packages, ("failed", why))
    else:
        removed_packages = remove_packages(ctx, packages) if packages else {}
    unmask_after_removal(ctx, removed_packages)
    results = [
        result_of(it, *guarded_result(it, guarded, problems))
        if it["kind"] == "guard" or (it["kind"] == "dependencies" and problems)
        else result_of(it, *apply_item(ctx, it, removed_packages))
        for it in items
    ]
    if any(r["result"] in ("removed", "restored") for r in results):
        for argv in EPILOGUES.get(phase, []):
            ctx.host.run(argv)
        if (
            phase == "firewall"
            and ctx.host.has("firewall-cmd")
            and ctx.host.ok(["firewall-cmd", "--state"])
        ):
            ctx.host.run(["firewall-cmd", "--reload"])
    return results


def stop_components(ctx: Context) -> None:
    """Stop the units of the components whose packages go, before their data is guarded."""
    if not ctx.host.has("systemctl"):
        return
    for component, units in sorted(ctx.names["component_units"].items()):
        if ctx.removes(component):
            ctx.host.run(["systemctl", "stop", *units])


def guard_data(ctx: Context, paths: Sequence[str]) -> tuple[list[str], list[str]]:
    """Mount every kept data directory read-only on itself, so no package script can delete it.

    Args:
        ctx: The host.
        paths: The plan's guard paths (computed while Docker still ran).

    Returns:
        The guarded paths, and why a path could not be guarded.
    """
    guarded: list[str] = []
    problems: list[str] = []
    for name in paths:
        if not ctx.host.is_dir(name):
            continue
        real = str(ctx.host.path(name))
        for argv in (["mount", "--bind", real, real], ["mount", "-o", "remount,bind,ro", real]):
            rc, out, err = ctx.host.run(argv)
            if rc != 0:
                problems.append(
                    f"{name} not guarded: {(err.strip() or out.strip() or f'exit {rc}')}"
                )
                if argv[1] != "--bind":
                    ctx.host.run(["umount", real])
                break
        else:
            guarded.append(name)
            write_json(ctx.host, ctx.names["guard_state"], {"guarded": guarded}, 0o600)
    return guarded, problems


def release_guards(ctx: Context) -> None:
    """Take off the read-only guards, also those an interrupted run left."""
    state = read_json(ctx.host, ctx.names["guard_state"])
    for name in reversed((state or {}).get("guarded") or []):
        ctx.host.run(["umount", str(ctx.host.path(name))])
    if ctx.host.exists(ctx.names["guard_state"]):
        ctx.host.remove(ctx.names["guard_state"])


def unmask_after_removal(ctx: Context, results: Mapping[str, tuple[str, str]]) -> None:
    """Unmask the units a removal without purging masked (Tailscale's package does)."""
    for name, (result, _) in results.items():
        if result != "removed":
            continue
        for unit in (ctx.family.get("masked_on_remove") or {}).get(name, []):
            if unit_state(ctx, unit) == "masked":
                ctx.host.run(["systemctl", "unmask", unit])


def write_json(host: Host, path: str, data: Mapping[str, Any], mode: int) -> None:
    """Write JSON atomically, one key per line (bootstrap finishes the pending item with sed)."""
    real = host.path(path)
    real.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    host.write(path, json.dumps(data, indent=2) + "\n", mode)


def reboot_reasons(items: Sequence[Mapping[str, Any]]) -> list[str]:
    """Why the host needs a reboot after the uninstall (it never reboots itself)."""
    found = []
    for r in items:
        if r["kind"] == "initramfs" and r["result"] == "restored":
            found.append(
                "the initramfs was rebuilt without pinka's nouveau blacklist or NVIDIA driver"
            )
        elif r["kind"] == "mok" and r["result"] == "kept":
            found.append(f"to delete the Machine Owner Key {r['name']} (mokutil --delete)")
        elif (
            r["kind"] == "file"
            and r["name"] == "/etc/wsl.conf"
            and r["result"] in ("removed", "restored")
        ):
            found.append("WSL: /etc/wsl.conf is back; restart the distribution (wsl --terminate)")
    return found


def build_report(params: Mapping[str, Any], plan: Mapping[str, Any]) -> dict[str, Any]:
    """The report: every item of the plan with its result (§5.4)."""
    results = [r for r in params.get("results") or [] if isinstance(r, Mapping)]
    done = {(r["phase"], r["kind"], r["name"]) for r in results}
    missing = [
        result_of(i, "failed", "not carried out: the play stopped before it")
        for i in plan["items"]
        if (i["phase"], i["kind"], i["name"]) not in done
    ]
    items = [dict(r) for r in results] + missing
    order = {p: n for n, p in enumerate(PHASES)}
    items.sort(key=lambda r: order.get(r["phase"], len(PHASES)))
    return {
        "schema_version": SCHEMA_VERSION,
        "machine": plan.get("machine", ""),
        "started_at": str(params.get("started_at") or ""),
        "finished_at": utc_now(),
        "record": plan.get("record", ""),
        "purge_data": bool(plan.get("purge_data")),
        "remove_components": list(plan.get("remove_components") or []),
        "repo_commit": str(params.get("repo_commit") or ""),
        "log_file": str(params.get("log_file") or ""),
        "reboot_needed": bool(reboot_reasons(items)),
        "reboot_reasons": reboot_reasons(items),
        "kept_dir": str(plan.get("kept_dir") or ""),
        "backup_access": str(plan.get("backup_access") or ""),
        "follow_ups": list(plan.get("follow_ups") or []),
        "items": items,
    }


def run_module(
    params: Mapping[str, Any], *, run: RunCommand, check_mode: bool = False
) -> dict[str, Any]:
    """Do what the module's parameters ask.

    Args:
        params: The module's parameters.
        run: Runs a command, returning ``(rc, stdout, stderr)``.
        check_mode: Ansible's check mode: the plan is computed, nothing is removed or written.

    Returns:
        The module's result.

    Raises:
        ValueError: For a phase that does not exist or a plan file that cannot be read.
    """
    host = Host(run, str(params.get("root") or "/"))
    state = str(params["state"])
    if state == "plan":
        ctx = Context(host, params)
        plan = build_plan(ctx)
        text = plan_text(plan)
        # The text too, for the installer and --json (bootstrap shows plan.txt).
        plan["text"] = text
        if not check_mode:
            write_json(host, str(params["plan_file"]), plan, 0o600)
            host.write(str(params["plan_text"]), text, 0o600)
        return {
            "changed": False,
            "plan": {k: v for k, v in plan.items() if k != "items"},
            "text": text,
        }
    plan = read_json(host, str(params["plan_file"]))
    if not isinstance(plan, dict) or not isinstance(plan.get("items"), list):
        raise ValueError(f"{params['plan_file']} holds no plan")
    if state == "apply":
        phase = str(params.get("phase") or "")
        if phase not in PHASES:
            raise ValueError(f"unknown phase {phase!r}")
        if check_mode:
            return {"changed": False, "results": []}
        ctx = Context(
            host,
            {
                **params,
                "purge_data": plan.get("purge_data", False),
                "remove_components": plan.get("remove_components") or [],
            },
        )
        ctx.keep_for_data = dict(plan.get("keep_for_data") or {})
        results = apply_phase(ctx, plan, phase)
        return {
            "changed": any(r["result"] in ("removed", "restored") for r in results),
            "results": results,
        }
    report = build_report(params, plan)
    if not check_mode:
        release_guards(Context(host, params))
        write_json(host, str(params["report_file"]), report, 0o600)
    counts: dict[str, int] = {}
    for r in report["items"]:
        counts[r["result"]] = counts.get(r["result"], 0) + 1
    return {"changed": not check_mode, "counts": counts, "failed": counts.get("failed", 0)}


def main() -> None:  # pragma: no cover - exercised on hosts
    """Run the module."""
    from ansible.module_utils.basic import AnsibleModule  # noqa: PLC0415 - only on the host

    module = AnsibleModule(
        argument_spec={
            "state": {"type": "str", "choices": ["plan", "apply", "report"], "required": True},
            "root": {"type": "path", "default": "/"},
            "machine": {"type": "str", "default": ""},
            "purge_data": {"type": "bool", "default": False},
            "remove_components": {"type": "list", "elements": "str", "default": []},
            "family": {"type": "dict", "default": {}},
            "component_packages": {"type": "dict", "default": {}},
            "component_repos": {"type": "dict", "default": {}},
            "session": {"type": "dict", "default": {}},
            "opt_existed": {"type": "bool", "default": True},
            "plan_file": {"type": "path"},
            "plan_text": {"type": "path"},
            "phase": {"type": "str"},
            "results": {"type": "list", "elements": "dict", "default": []},
            "report_file": {"type": "path"},
            "started_at": {"type": "str", "default": ""},
            "repo_commit": {"type": "str", "default": ""},
            "log_file": {"type": "str", "default": ""},
        },
        supports_check_mode=True,
    )
    # Every command's output is parsed in English; packages are removed without questions.
    module.run_command_environ_update = {
        "LC_ALL": "C",
        "LANG": "C",
        "DEBIAN_FRONTEND": "noninteractive",
    }

    def run(argv: Sequence[str]) -> tuple[int, str, str]:
        # A command the host lacks is an answer (127), not a module failure.
        try:
            rc, out, err = module.run_command(list(argv), handle_exceptions=False)
        except OSError as exc:
            return 127, "", f"{argv[0]}: {exc.strerror or exc}"
        return int(rc), str(out), str(err)

    try:
        result = run_module(module.params, run=run, check_mode=module.check_mode)
    except (OSError, ValueError, KeyError) as exc:
        module.fail_json(msg=f"{type(exc).__name__}: {exc}")
        return
    module.exit_json(**result)


if __name__ == "__main__":
    main()
