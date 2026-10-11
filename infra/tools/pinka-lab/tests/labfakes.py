"""Shared helpers for the pinka tests (standard library only, Python 3.9)."""

from __future__ import annotations

import copy
import datetime as dt
import fnmatch
import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Union

from pinka_lab.config import LabConfig
from pinka_lab.doctor import Context
from pinka_lab.system import FileInfo, HttpResponse, Result, System

NOW = dt.datetime(2026, 1, 1, 12, 0, 0, tzinfo=dt.timezone.utc)
DIGEST = "sha256:" + "a" * 64

Answer = Union[tuple[Any, ...], list[Any], Callable[[Sequence[str]], Any]]

LAB: dict[str, Any] = {
    "schema_version": 1,
    "machine": "pinka-lab",
    "host_vars": "pinka-lab",
    "infra_dir": "/srv/checkout/pinka/infra",
    "pinka_root": "/srv/pinka",
    "node_role": "full",
    "provider": "baremetal",
    "features": {"gpu_inference": True, "vm_targets": False},
    "lab_mode": "production",
    "paths": {
        "preflight": "/usr/local/lib/pinka/preflight/pinka-preflight",
        "host_profile": "/etc/pinka/host-profile.json",
        "bootstrap_state": "/etc/pinka/bootstrap-state.json",
        "health_gate_status": "/run/pinka/health-gate.json",
        "sessions": "/var/lib/pinka/sessions",
        "python": "/opt/pinka/python/bin/python3",
        "ansible": "/opt/pinka/ansible-venv/bin/ansible",
    },
    "fingerprint": {
        "cache": "/var/lib/pinka/fingerprint/current.json",
        "versions_lock_sha256": "0" * 64,
        "host_vars_sha256": "1" * 64,
    },
    "lab": {"drain_timeout": 0},
    "units": ["pinka-health-gate.service", "pinka-core.service"],
    "containers": {"pinka-postgres": "docker.io/library/postgres@" + DIGEST},
    "listeners": {
        "processes": ["sshd", "dnsmasq"],
        "addresses": ["198.18.0.1"],
        "tailnet_ports": [],
    },
    "inference": {
        "dir": "/etc/pinka/inference",
        "control_port": 18710,
        "peer": "",
        "renew_days": 30,
    },
    "firewall": {"backend": "nftables", "incus_bridge_patterns": ["r-*"]},
    "tailscale": {"enabled": True, "hostname": "pinka-lab", "tag": "tag:pinka-lab"},
    "gpu": {
        "image": "docker.io/nvidia/cuda@" + DIGEST,
        "device_args": ["--device", "nvidia.com/gpu=all"],
        "nvidia_smi": "nvidia-smi",
        "vllm_cuda": "13.0.2",
    },
    "vllm": {
        "current": "/etc/pinka/vllm/current.yml",
        "health_url": "http://127.0.0.1:18704/health",
    },
    "incus": {
        "pool": "pinka-targets",
        "backend": "btrfs",
        "min_free_gib": 20,
        "run_acl": "pinka-run-default",
        "events_unit": "pinka-incus-events.service",
        "projects": ["bench", "build"],
        "profiles": [{"name": "target-container", "project": "bench"}],
        "cert_dir": "/etc/pinka/incus",
        "certificates": [{"account": "demo-ctl", "short": "demo-ctl", "name": "demo-ctl"}],
    },
    "postgres": {
        "container": "pinka-postgres",
        "roles": ["demo_ctl", "demo_reader"],
        "database": "demo",
        "restricted_roles": ["demo_reader"],
    },
    "opa": {"url": "http://127.0.0.1:8181"},
    "observability": {
        "otlp_http": "http://127.0.0.1:4318",
        "loki": "http://127.0.0.1:18702",
        "tempo": "http://127.0.0.1:18703",
        "prometheus": "http://127.0.0.1:18701",
        "wait_seconds": 0,
        "smoke_wait_seconds": 0,
        "poll_seconds": 0,
    },
    "layout": [
        {
            "path": "/srv/pinka/shared",
            "owner": "root",
            "group": "pinka",
            "mode": "2770",
            "default_acl": ["group:pinka:rwX"],
        }
    ],
    "accounts": {
        "researchers": ["researcher1", "researcher2"],
        "platform_accounts": ["demo-ctl", "demo-agent"],
        "privileged_groups": ["docker", "incus-admin"],
        "break_glass_docker": [],
    },
    "hardening": [],
    "sockets": [],
    "images": {
        "blueprints": [
            {
                "name": "_base",
                "type": "container",
                "nesting": False,
                "services": ["otelcol-contrib"],
                "base_blueprint": None,
            },
            {
                "name": "web-nginx-flask",
                "type": "container",
                "nesting": False,
                "services": ["nginx", "webapp"],
                "base_blueprint": "_base",
            },
        ],
        "bench_project": "bench",
        "export_root": "/srv/pinka/images",
        "files_helper": "/usr/local/libexec/pinka/image-files",
    },
    "backup": {
        "configured": True,
        "helper": "/usr/local/libexec/pinka/backup",
        "state": "/var/lib/pinka/backup/state.json",
        "max_age_hours": 26,
    },
    "time": {"max_offset_seconds": 1.0},
    "cpusets": {"services": "", "targets": "", "slice": "pinka-services.slice"},
    "docs": {"dir": "/usr/local/share/pinka/docs", "port": 18706},
    "wsl": {"memory_gib": None, "cpus": None, "kvm_expected": False},
}

PROFILE: dict[str, Any] = {
    "schema_version": 1,
    "outcome": "supported",
    "provider": "baremetal",
    "family": "debian",
    "release_model": "fixed",
    "features": {"gpu_inference": True, "vm_targets": False},
    "environment": {"class": "baremetal", "hypervisor": None},
    "os": {"id": "ubuntu", "version_id": "24.04", "arch": "x86_64"},
    "kernel": {"release": "6.8.0-00-generic"},
    "tier": "tier1",
    "hardware": {"cpu_model": "Example CPU", "cpu_threads_effective": 8, "memory_gib": 32},
    "gpu": {
        "name": "Example GPU",
        "count": 1,
        "vram_gib": 12,
        "driver": "000.00",
        "compute_capability": "8.9",
        "path": "native",
    },
}


def lab_data(**changes: Any) -> dict[str, Any]:
    """A copy of the sample lab description with changes.

    Args:
        **changes: Keys to replace; ``a__b`` replaces the nested key ``a.b``.

    Returns:
        The description.
    """
    data = copy.deepcopy(LAB)
    for key, value in changes.items():
        *parents, last = key.split("__")
        target = data
        for parent in parents:
            target = target[parent]
        target[last] = value
    return data


def lab(**changes: Any) -> LabConfig:
    """The sample lab description as a :class:`LabConfig`.

    Args:
        **changes: As for :func:`lab_data`.

    Returns:
        The configuration.
    """
    return LabConfig(lab_data(**changes))


def _to_result(argv: Sequence[str], answer: Any) -> Result:
    if isinstance(answer, Result):
        return answer
    rc, out = answer[0], answer[1]
    err = answer[2] if len(answer) > 2 else ("" if rc == 0 else "failed")
    if not isinstance(out, str):
        out = json.dumps(out)
    return Result(tuple(argv), rc, out, err)


def _to_response(answer: Any) -> HttpResponse:
    status, body = answer
    return HttpResponse(status, body if isinstance(body, str) else json.dumps(body))


class FakeSystem(System):
    """A host made of tables.

    Commands and HTTP requests are answered from tables keyed by the command
    line (``" ".join(argv)``) or ``"METHOD url"``: an exact key first, then the
    first key that matches as a glob pattern. An answer is ``(rc, stdout[,
    stderr])`` (``(status, body)`` for HTTP; non-string output is JSON-encoded),
    a list of such answers consumed in order (the last one repeats), or a
    callable taking the argv. Unknown commands exit 127; unknown requests get
    status 0.
    """

    def __init__(
        self,
        commands: Mapping[str, Any] | None = None,
        *,
        files: Mapping[str, str] | None = None,
        infos: Mapping[str, FileInfo] | None = None,
        http: Mapping[str, Any] | None = None,
        unix: Mapping[str, Any] | None = None,
        euid: int = 0,
        groups: dict[str, list[str]] | None = None,
        dirs: dict[str, list[str]] | None = None,
        interactive_rc: int = 0,
        serve_error: OSError | None = None,
    ) -> None:
        self.commands = dict(commands or {})
        self.files = dict(files or {})
        self.infos = dict(infos or {})
        self.http_answers = dict(http or {})
        self.unix_answers = dict(unix or {})
        self._euid = euid
        self.groups = dict(groups or {})
        self.dirs = dict(dirs or {})
        self.interactive_rc = interactive_rc
        self.serve_error = serve_error
        self.served: list[tuple[str, str, int]] = []
        self.docs_versions: list[str] = []
        self.removed: list[str] = []
        self.checked_ports: list[dict[str, int]] = []
        self.busy_ports: list[str] = []
        self.docs_calls: list[tuple[Any, ...]] = []
        self.docs_error: Exception | None = None
        self.calls: list[list[str]] = []
        self.interactive_calls: list[tuple[list[str], dict[str, str]]] = []
        self.http_calls: list[tuple[str, str, bytes | None]] = []
        self.unix_calls: list[tuple[str, str, bytes | None]] = []
        self.written: dict[str, tuple[str, int]] = {}

    @staticmethod
    def _lookup(table: dict[str, Any], key: str) -> Any:
        answer = table.get(key)
        if answer is None:
            answer = next(
                (value for pattern, value in table.items() if fnmatch.fnmatchcase(key, pattern)),
                None,
            )
        if isinstance(answer, list):
            return answer.pop(0) if len(answer) > 1 else answer[0]
        return answer

    def run(
        self,
        argv: Sequence[str],
        timeout: float = 60.0,
        stdin: str | None = None,
        env: dict[str, str] | None = None,
    ) -> Result:
        self.calls.append(list(argv))
        answer = self._lookup(self.commands, " ".join(argv))
        if callable(answer):
            answer = answer(argv)
        if answer is None:
            return Result(tuple(argv), 127, "", "not found")
        return _to_result(argv, answer)

    def interactive(self, argv: Sequence[str], env: dict[str, str] | None = None) -> int:
        self.interactive_calls.append((list(argv), dict(env or {})))
        return self.interactive_rc

    def read_text(self, path: str) -> str | None:
        return self.files.get(path)

    def write_text(self, path: str, text: str, mode: int) -> None:
        self.files[path] = text
        self.written[path] = (text, mode)

    def info(self, path: str) -> FileInfo | None:
        if path in self.infos:
            return self.infos[path]
        if path in self.files:
            return FileInfo("root", "root", 0o644, False, False, len(self.files[path]))
        return None

    def listdir(self, path: str) -> list[str]:
        return sorted(self.dirs.get(path, []))

    def walk_dirs(self, path: str, max_depth: int) -> list[str]:
        prefix = path.rstrip("/") + "/"
        return sorted(d for d in self.dirs if d.startswith(prefix))

    def http(
        self, method: str, url: str, body: bytes | None = None, timeout: float = 10.0
    ) -> HttpResponse:
        self.http_calls.append((method, url, body))
        answer = self._lookup(self.http_answers, f"{method} {url}")
        if callable(answer):
            answer = answer((method, url, body))
        return HttpResponse(0, "connection refused") if answer is None else _to_response(answer)

    def unix_http(
        self,
        socket_path: str,
        method: str,
        path: str,
        body: bytes | None = None,
        timeout: float = 10.0,
    ) -> HttpResponse:
        self.unix_calls.append((method, path, body))
        answer = self._lookup(self.unix_answers, f"{method} {path}")
        return HttpResponse(0, "no socket") if answer is None else _to_response(answer)

    def serve_static(self, directory: str, address: str, port: int) -> None:
        if self.serve_error is not None:
            raise self.serve_error
        self.served.append((directory, address, port))

    def tcp_connect(self, host: str, port: int, timeout: float = 3.0) -> bool:
        return False

    def euid(self) -> int:
        return self._euid

    def now(self) -> dt.datetime:
        return NOW

    def group_members(self, group: str) -> list[str] | None:
        members = [user for user, groups in self.groups.items() if group in groups]
        return sorted(members) if members else None

    def user_groups(self, user: str) -> list[str] | None:
        return self.groups.get(user)

    def remove(self, path: str) -> None:
        self.files.pop(path, None)
        self.removed.append(path)

    def port_conflicts(self, ports: dict[str, int]) -> list[str]:
        self.checked_ports.append(dict(ports))
        return list(self.busy_ports)

    def docs_install(self, base: str, tarball: str, sha256: str, version: str | None) -> str:
        self.docs_calls.append(("install", base, tarball, sha256, version))
        if self.docs_error is not None:
            raise self.docs_error
        name = version or tarball.rsplit("/", 1)[-1].split(".tar", 1)[0]
        self.docs_versions.append(name)
        return f"installed {name}"

    def docs_rollback(self, base: str) -> str:
        self.docs_calls.append(("rollback", base))
        if self.docs_error is not None:
            raise self.docs_error
        now = self.docs_versions.pop()
        return f"switched from {now} back to {self.docs_versions[-1]}"

    def docs_current(self, base: str) -> str | None:
        return self.docs_versions[-1] if self.docs_versions else None


def context(system: FakeSystem, **changes: Any) -> Context:
    """A check context on the sample lab.

    Args:
        system: The fake host.
        **changes: Changes to the lab description (see :func:`lab_data`).

    Returns:
        The context.
    """
    return Context(system, lab(**changes))


def info(owner: str, group: str, mode: int, is_dir: bool = False) -> FileInfo:
    """A :class:`FileInfo`.

    Args:
        owner: Owner name.
        group: Group name.
        mode: Permission bits.
        is_dir: Whether it is a directory.

    Returns:
        The info.
    """
    return FileInfo(owner, group, mode, is_dir, False, 0)
