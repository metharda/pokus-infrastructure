#!/opt/pinka/ansible-venv/bin/python3 -I
# Managed by pinka (ansible/roles/gpu_arbiter/files/pinka_gpu_arbiter.py). Local changes will be overwritten.
"""The GPU arbiter: whether the GPU serves, trains or idles (BUILD_PROMPT §7.18).

Installed as ``/usr/local/libexec/pinka/gpu-arbiter`` by the gpu_arbiter
role and run as root, never through sudo. It replaces contract v1's vLLM
deploy helper. Exactly one mode holds at a time: ``idle``, ``serving
<deployment>`` or ``training <run>``.

* ``connection``: started by ``pinka-gpu-arbiter.socket`` (``Accept=yes``,
  ``/run/pinka/gpu.sock``, 0600, owned by the active platform's controller,
  on an inference node by the control endpoint's account) for one
  connection on stdin/stdout. The peer (``SO_PEERCRED``) must be that
  account or root. It sends one JSON line and gets one back: ``status``,
  ``serve``, ``idle``, ``train`` and ``publish-dataset``; on an inference
  node the control endpoint may only ask for ``status`` and ``serve``.
* ``boot``: run once per boot by ``pinka-gpu-arbiter.service``. A run left in
  training is interrupted; it resumes from its checkpoints if its job is
  ``resumable``, otherwise the previous deployment comes back or the GPU
  stays idle. Without one, the default deployment is served or the GPU stays
  idle (``gpu_arbiter.after_boot``).
* ``run-job <run>``: the job runner of ``pinka-gpu-job@<run>.service``: the
  container ``pinka-job-<run>`` with the options fixed below, then the
  adapter's registration and the way back to serving or idle.
* ``render <id>@<version>``: print what a deployment renders to.
* ``prepare <id>@<version>``: render a deployment's files and point
  ``current.yml`` at it without starting anything (Ansible, restore), only
  while the GPU does not train.

Every transition goes to ``/var/lib/pinka/gpu/transitions.jsonl`` and to the
journal; the current mode is in ``/var/lib/pinka/gpu/state.json``. Datasets
are published to ``/srv/pinka/training/datasets/<sha256>/`` with
``PROVENANCE.json`` and refused, at publication and at mount, if they list an
instance of the ``test`` split; adapters are registered read-only under
``/srv/pinka/models/adapters/<name>/<sha256>/`` with ``MANIFEST.json`` and
``LINEAGE.json`` (schemas in contract/records/).
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime as dt
import fcntl
import grp
import hashlib
import json
import os
import pwd
import re
import shutil
import signal
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, TextIO

import yaml

DEPLOYMENT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}@[0-9]{1,6}$")
PROFILE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
ADAPTER_RE = re.compile(r"^(?P<name>[a-z][a-z0-9-]{0,62})@(?P<sha256>[0-9a-f]{64})$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RUN_RE = re.compile(r"^[a-z][a-z0-9]{0,9}-[0-9]{8}t[0-9]{6}-[0-9a-f]{8}$")
SPLIT_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
SPEC_KEYS = {
    "id",
    "version",
    "model_id",
    "adapter_ids",
    "profile",
    "overrides",
    "sampling_defaults",
}
SAMPLING_KEYS = {
    "temperature",
    "top_p",
    "top_k",
    "min_p",
    "repetition_penalty",
    "presence_penalty",
    "frequency_penalty",
    "max_new_tokens",
}
SUBMISSION_KEYS = {
    "base_model",
    "datasets",
    "hyperparameters",
    "seed",
    "sessions_drained",
    "restore",
}
REQUESTS = ("status", "serve", "idle", "train", "publish-dataset")
# What the control endpoint of an inference node may ask (§19.10).
SERVING_ONLY = ("status", "serve")
MAX_REQUEST = 1 << 20
CONTAINER_MODELS = "/models"
FORBIDDEN_SPLIT = "test"
PROVENANCE = "PROVENANCE.json"
MANIFEST = "MANIFEST.json"
LINEAGE = "LINEAGE.json"
JOB_UNIT = "pinka-gpu-job@{run}.service"
JOB_CONTAINER = "pinka-job-{run}"
DATASETS_GROUP = "pinka-datasets"
TRANSITIONS_SHOWN = 10
FINAL = ("succeeded", "failed", "interrupted")
# Mount points inside a job's container (docs/reference/gpu-arbiter.md).
JOB_MODEL = "/model"
JOB_DATASETS = "/datasets"
JOB_OUTPUT = "/output"
JOB_CHECKPOINTS = "/checkpoints"
# The adapter a job leaves in its output directory.
JOB_ADAPTER = "adapter"


class ArbiterError(Exception):
    """A request, a deployment, a dataset or a run cannot be served."""


# --- configuration -----------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Paths:
    """Where the arbiter reads and writes (tests point them at a temporary tree).

    Attributes:
        config: The role's configuration (``/etc/pinka/gpu/arbiter.json``).
        vllm: ``/etc/pinka/vllm`` (deployments/, profiles/, current.*).
        models: ``/srv/pinka/models`` (adapters/ below it).
        training: ``/srv/pinka/training`` (datasets/, jobs/).
        state: ``/var/lib/pinka/gpu`` (state, transitions, runs, lock).
        rendered: The active platform's ``rendered.json``.
        active: The active platform's ``active.json``.
        bootstrap_state: Infra's provenance (its commit).
        owner_uid: Required owner of deployment files (root).
    """

    config: Path = Path("/etc/pinka/gpu/arbiter.json")
    vllm: Path = Path("/etc/pinka/vllm")
    models: Path = Path("/srv/pinka/models")
    training: Path = Path("/srv/pinka/training")
    state: Path = Path("/var/lib/pinka/gpu")
    rendered: Path = Path("/etc/pinka/platform/rendered.json")
    active: Path = Path("/etc/pinka/platform/active.json")
    bootstrap_state: Path = Path("/etc/pinka/bootstrap-state.json")
    owner_uid: int = 0


@dataclasses.dataclass(frozen=True)
class Config:
    """The role's settings (``arbiter.json``).

    Attributes:
        node_role: ``full``, ``lab`` or ``inference``; only ``full`` trains.
        peer: The account allowed besides root.
        serves: vLLM runs on this host (``gpu_inference``).
        after_boot: ``serve`` (the default deployment) or ``idle``.
        restore_after_training: Default of a submission's ``restore``.
        default_deployment: ``<id>@<version>`` served after boot, or None.
        caps: ``training_caps``: ``timeout_seconds``, ``memory_max``, ``cpus``.
        gpu_args: Docker arguments that give a container the GPU (as for vLLM).
        vllm_port: vLLM's port on 127.0.0.1.
        otlp: OTLP gRPC endpoint of the collector.
        vllm_unit: The unit of the inference Compose project.
        serve_timeout: Seconds a deployment may take to answer /health.
        stop_grace: Seconds a stopped job gets to checkpoint.
        pids_limit: Process limit of a job's container.
    """

    node_role: str = "full"
    peer: str = "root"
    serves: bool = True
    after_boot: str = "serve"
    restore_after_training: bool = True
    default_deployment: str | None = None
    caps: Mapping[str, Any] = dataclasses.field(
        default_factory=lambda: {"timeout_seconds": 86400, "memory_max": "24G", "cpus": 8}
    )
    gpu_args: tuple[str, ...] = ("--device", "nvidia.com/gpu=all")
    vllm_port: int = 18704
    otlp: str = "http://127.0.0.1:4317"
    vllm_unit: str = "pinka-inference.service"
    serve_timeout: int = 900
    stop_grace: int = 120
    pids_limit: int = 4096

    @property
    def trains(self) -> bool:
        """Training runs only on a full node (§19.10)."""
        return self.node_role == "full"


def load_config(path: Path) -> Config:
    """Read ``arbiter.json``; a missing file means the defaults.

    Args:
        path: The file.

    Returns:
        The configuration.

    Raises:
        ArbiterError: If the file is not a JSON object of known keys.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Config()
    except (OSError, ValueError) as exc:
        raise ArbiterError(f"{path}: {exc}") from None
    fields = {f.name for f in dataclasses.fields(Config)}
    if not isinstance(data, dict) or set(data) - fields:
        raise ArbiterError(f"{path}: unknown keys {sorted(set(data) - fields)}")
    if "gpu_args" in data:
        data["gpu_args"] = tuple(data["gpu_args"])
    return Config(**data)


# --- the host ----------------------------------------------------------------------------


class System:
    """Everything the arbiter does on the host; the tests replace it."""

    def run(self, argv: Sequence[str]) -> None:
        """Run a command; raise on failure.

        Args:
            argv: The command.
        """
        subprocess.run(list(argv), check=True)  # noqa: S603 - fixed programs

    def start_job(self, run: str) -> None:
        """Start a run's unit without waiting for it.

        Args:
            run: Run ID.
        """
        self.run(["systemctl", "start", "--no-block", JOB_UNIT.format(run=run)])

    terminated = False

    def docker_run(self, argv: Sequence[str], name: str, timeout: float, grace: int) -> int | None:
        """Run a job's container in the foreground.

        A SIGTERM to the runner (its unit stopped) stops the container with
        the same grace and sets :attr:`terminated`, so the run is still
        settled.

        Args:
            argv: ``docker run ...``.
            name: The container's name.
            timeout: Seconds before the container is stopped.
            grace: Seconds ``docker stop`` waits before it kills.

        Returns:
            The container's exit status, or None when it ran out of time.
        """

        def stop(signum: int, frame: object) -> None:
            self.terminated = True
            self.stop_container(name, grace)

        previous = signal.signal(signal.SIGTERM, stop)
        process = subprocess.Popen(list(argv))  # noqa: S603 - argv built by container_argv
        try:
            return process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.stop_container(name, grace)
            process.wait()
            return None
        finally:
            signal.signal(signal.SIGTERM, previous)

    def stop_container(self, name: str, grace: int) -> None:
        """Ask a container to stop (SIGTERM, then SIGKILL after the grace).

        Args:
            name: The container's name.
            grace: Seconds before the kill.
        """
        subprocess.run(  # noqa: S603 - fixed program
            ["docker", "stop", "--time", str(grace), name],  # noqa: S607 - docker from PATH
            check=False,
            capture_output=True,
        )

    def http_get(self, url: str, timeout: float = 10.0) -> tuple[int, str]:
        """GET a loopback URL without any proxy.

        Args:
            url: The URL.
            timeout: Seconds.

        Returns:
            Status (0 when nothing answered) and body.
        """
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(url, timeout=timeout) as response:
                return int(response.status), response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return int(exc.code), ""
        except (urllib.error.URLError, OSError):
            return 0, ""

    def boot_id(self) -> str:
        """The kernel's boot ID."""
        return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()

    def now(self) -> dt.datetime:
        """The time (UTC)."""
        return dt.datetime.now(dt.UTC)

    def sleep(self, seconds: float) -> None:
        """Wait.

        Args:
            seconds: How long.
        """
        time.sleep(seconds)

    def account(self, name: str) -> tuple[int, int]:
        """An account's UID and primary GID.

        Args:
            name: Account name.

        Returns:
            ``(uid, gid)``.

        Raises:
            ArbiterError: If the account does not exist.
        """
        try:
            entry = pwd.getpwnam(name)
        except KeyError:
            raise ArbiterError(f"no account {name}") from None
        return entry.pw_uid, entry.pw_gid

    def group(self, name: str) -> int:
        """A group's GID.

        Args:
            name: Group name.

        Returns:
            The GID.

        Raises:
            ArbiterError: If the group does not exist.
        """
        try:
            return grp.getgrnam(name).gr_gid
        except KeyError:
            raise ArbiterError(f"no group {name}") from None

    def chown(self, path: Path, uid: int, gid: int) -> None:
        """Change a path's owner without following links.

        Args:
            path: The path.
            uid: Owner.
            gid: Group.
        """
        os.chown(path, uid, gid, follow_symlinks=False)

    def log(self, message: str) -> None:
        """Write a line to the journal (the unit's standard error).

        Args:
            message: The line.
        """
        print(f"gpu-arbiter: {message}", file=sys.stderr, flush=True)


@dataclasses.dataclass
class Arbiter:
    """The arbiter's paths, settings and host.

    Attributes:
        paths: Paths.
        config: Settings.
        system: The host.
    """

    paths: Paths
    config: Config
    system: System

    # --- state ---------------------------------------------------------------

    @contextlib.contextmanager
    def lock(self) -> Iterator[None]:
        """Hold the arbiter's lock (every change of mode, run or files)."""
        self.paths.state.mkdir(parents=True, exist_ok=True)
        with (self.paths.state / "lock").open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def state(self) -> dict[str, Any]:
        """The current mode (``state.json``); idle when there is none."""
        try:
            data = json.loads((self.paths.state / "state.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            data = {}
        except (OSError, ValueError) as exc:
            raise ArbiterError(f"state.json: {exc}") from None
        base = {
            "state_version": 1,
            "mode": "idle",
            "deployment": None,
            "run": None,
            "previous": None,
            "boot_id": None,
            "since": None,
        }
        return {**base, **data}

    def _save_state(self, state: Mapping[str, Any]) -> None:
        _write_json(self.paths.state / "state.json", state, 0o644)

    def transitions(self, last: int = TRANSITIONS_SHOWN) -> list[dict[str, Any]]:
        """The last transitions of the log.

        Args:
            last: How many.

        Returns:
            Oldest first.
        """
        try:
            lines = (self.paths.state / "transitions.jsonl").read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        out = []
        for line in lines.splitlines()[-last:]:
            with contextlib.suppress(ValueError):
                out.append(json.loads(line))
        return out

    def _transition(
        self,
        state: dict[str, Any],
        to: Mapping[str, Any],
        *,
        requester: str,
        outcome: str,
        detail: str = "",
        job: str | None = None,
    ) -> None:
        """Change the mode, write the state and log the transition."""
        stamp = _stamp(self.system.now())
        before = _mode_text(state)
        record = {
            "time": stamp,
            "from": state["mode"],
            "to": to["mode"],
            "requester": requester,
            "deployment": to.get("deployment") or state.get("deployment"),
            "run": to.get("run") or state.get("run"),
            "job": job,
            "outcome": outcome,
            "detail": detail,
        }
        state.update(
            {
                "mode": to["mode"],
                "deployment": to.get("deployment"),
                "run": to.get("run"),
                "previous": to.get("previous"),
                "since": stamp,
            }
        )
        self._save_state(state)
        path = self.paths.state / "transitions.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
        path.chmod(0o644)
        self.system.log(
            f"{before} -> {_mode_text(state)} ({outcome}, {requester}) {detail}".strip()
        )

    def run_record(self, run: str) -> dict[str, Any]:
        """A run's record (``runs/<run>.json``).

        Args:
            run: Run ID.

        Returns:
            The record.

        Raises:
            ArbiterError: If there is no such run.
        """
        if not RUN_RE.fullmatch(run):
            raise ArbiterError(f"invalid run {run!r}")
        try:
            data = json.loads((self.paths.state / "runs" / f"{run}.json").read_text("utf-8"))
        except FileNotFoundError:
            raise ArbiterError(f"no run {run}") from None
        except (OSError, ValueError) as exc:
            raise ArbiterError(f"run {run}: {exc}") from None
        if not isinstance(data, dict):
            raise ArbiterError(f"run {run}: not an object")
        return data

    def _save_run(self, record: Mapping[str, Any]) -> None:
        _write_json(self.paths.state / "runs" / f"{record['run']}.json", record, 0o644)

    # --- deployments -----------------------------------------------------------

    def deployment_path(self, deployment: str) -> Path:
        """Validate a deployment reference and return its file.

        Args:
            deployment: ``<id>@<version>``.

        Returns:
            The deployment file.

        Raises:
            ArbiterError: If the reference or the file is not acceptable.
        """
        if not isinstance(deployment, str) or not DEPLOYMENT_RE.fullmatch(deployment):
            raise ArbiterError("deployment must match <id>@<version> ([a-z0-9-], version 0-999999)")
        path = self.paths.vllm / "deployments" / f"{deployment}.yml"
        try:
            info = path.lstat()
        except FileNotFoundError:
            raise ArbiterError(f"no deployment {deployment}") from None
        if not stat.S_ISREG(info.st_mode):
            raise ArbiterError(f"{path} is not a regular file")
        if info.st_uid != self.paths.owner_uid or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise ArbiterError(f"{path} must be owned by root and writable only by root")
        return path

    def load_deployment(self, deployment: str) -> dict[str, Any]:
        """Load and check a deployment spec (§7.11).

        Args:
            deployment: ``<id>@<version>``.

        Returns:
            The spec.

        Raises:
            ArbiterError: If the spec is malformed or does not match its file name.
        """
        spec = _load_yaml(self.deployment_path(deployment))
        unknown = sorted(set(spec) - SPEC_KEYS)
        if unknown:
            raise ArbiterError(f"{deployment}: unknown keys {', '.join(unknown)}")
        if f"{spec.get('id')}@{spec.get('version')}" != deployment:
            raise ArbiterError(f"{deployment}: id and version do not match the file name")
        for key, kind in (("adapter_ids", list), ("overrides", dict), ("sampling_defaults", dict)):
            if not isinstance(spec.setdefault(key, kind()), kind):
                raise ArbiterError(f"{deployment}: {key} must be a {kind.__name__}")
        if not isinstance(spec.get("model_id"), str) or not isinstance(spec.get("profile"), str):
            raise ArbiterError(f"{deployment}: model_id and profile are required")
        return spec

    def load_profile(self, name: str) -> dict[str, Any]:
        """Load a serving profile.

        Args:
            name: Profile name.

        Returns:
            The profile.

        Raises:
            ArbiterError: If the name is invalid or the profile is missing.
        """
        if not PROFILE_RE.fullmatch(name):
            raise ArbiterError(f"invalid profile name {name!r}")
        path = self.paths.vllm / "profiles" / f"{name}.yml"
        if not path.is_file():
            raise ArbiterError(f"no serving profile {name}")
        return _load_yaml(path)

    def find_model(self, model_id: str, *, rehash: bool = False) -> tuple[Path, dict[str, Any]]:
        """Find the verified model of an id (the models role's ``MANIFEST.json``).

        Args:
            model_id: Model id from models.lock.yml.
            rehash: Hash every file again (a training run's base model).

        Returns:
            The model directory and its manifest.

        Raises:
            ArbiterError: If no verified model or more than one has this id.
        """
        found = []
        for path in sorted(self.paths.models.glob(f"*/*/*/{MANIFEST}")):
            if path.relative_to(self.paths.models).parts[0] == "adapters":
                continue
            with contextlib.suppress(OSError, ValueError):
                manifest = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(manifest, dict) and manifest.get("id") == model_id:
                    found.append((path.parent, manifest))
        if len(found) != 1:
            state = "is not downloaded and verified" if not found else "is ambiguous"
            raise ArbiterError(f"model {model_id} {state} (models role)")
        directory, manifest = found[0]
        for record in manifest.get("files", []):
            file = directory / record["path"]
            if not file.is_file() or file.stat().st_size != record.get("size"):
                raise ArbiterError(f"{directory}: {record['path']} is missing or has another size")
            if rehash and _sha256_file(file) != record.get("sha256"):
                raise ArbiterError(f"model {model_id}: {record['path']} does not match its hash")
        return directory, manifest

    def find_adapter(self, reference: str) -> tuple[str, Path, dict[str, Any]]:
        """Find a registered adapter and check its files against its hash.

        Args:
            reference: ``<name>@<sha256>``.

        Returns:
            Name, directory and manifest.

        Raises:
            ArbiterError: If the reference is invalid or the adapter is not registered.
        """
        match = ADAPTER_RE.fullmatch(reference) if isinstance(reference, str) else None
        if match is None:
            raise ArbiterError(f"invalid adapter reference {reference!r} (<name>@<sha256>)")
        directory = self.paths.models / "adapters" / match["name"] / match["sha256"]
        try:
            manifest = json.loads((directory / MANIFEST).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ArbiterError(f"adapter {reference} is not registered") from None
        files = tree_files(directory, exclude=(MANIFEST, LINEAGE))
        if not isinstance(manifest, dict) or tree_hash(files) != match["sha256"]:
            raise ArbiterError(f"adapter {reference}: its files do not match its hash")
        if manifest.get("sha256") != match["sha256"] or not (directory / LINEAGE).is_file():
            raise ArbiterError(
                f"adapter {reference} has no matching MANIFEST.json and LINEAGE.json"
            )
        return match["name"], directory, manifest

    def _container_path(self, directory: Path) -> str:
        return f"{CONTAINER_MODELS}/{directory.relative_to(self.paths.models).as_posix()}"

    def render(self, deployment: str) -> tuple[str, str]:
        """Render the vLLM configuration and environment of a deployment.

        Args:
            deployment: ``<id>@<version>``.

        Returns:
            current.yaml and current.env.

        Raises:
            ArbiterError: If anything does not validate.
        """
        spec = self.load_deployment(deployment)
        profile = self.load_profile(spec["profile"])
        model_dir, manifest = self.find_model(spec["model_id"])
        overridable: dict[str, str] = profile.get("overridable", {})
        unknown = sorted(set(spec["overrides"]) - set(overridable))
        if unknown:
            raise ArbiterError(
                f"{deployment}: overrides not allowed by {spec['profile']}: {unknown}"
            )
        args: dict[str, Any] = dict(profile.get("args", {}))
        args.update({overridable[k]: v for k, v in spec["overrides"].items()})
        quantization = args.pop("quantization", manifest.get("quantization"))
        if quantization not in profile.get("quantizations", [None]):
            raise ArbiterError(
                f"quantization {quantization} does not fit profile {spec['profile']}"
            )
        template = args.pop("chat-template", None)
        if template is not None:
            listed = {f["path"] for f in manifest.get("files", [])}
            if template not in listed:
                raise ArbiterError(f"chat_template {template} is not a verified file of the model")
            args["chat-template"] = f"{self._container_path(model_dir)}/{template}"
        if args.get("enable-auto-tool-choice") and not args.get("tool-call-parser"):
            raise ArbiterError(f"{deployment}: tool calling needs overrides.tool_call_parser")
        modules = []
        for reference in spec["adapter_ids"]:
            name, directory, adapter = self.find_adapter(reference)
            if adapter.get("base_model_id") != spec["model_id"]:
                raise ArbiterError(f"adapter {reference} is for {adapter.get('base_model_id')}")
            modules.append(f"{name}={self._container_path(directory)}")
        if modules:
            args["enable-lora"] = True
            args["lora-modules"] = modules
        sampling = spec["sampling_defaults"]
        unknown = sorted(set(sampling) - SAMPLING_KEYS)
        if unknown or not all(isinstance(v, (int, float)) for v in sampling.values()):
            raise ArbiterError(
                f"{deployment}: sampling_defaults allow numbers for {sorted(SAMPLING_KEYS)}"
            )
        config: dict[str, Any] = {
            "model": self._container_path(model_dir),
            "served-model-name": spec["model_id"],
            "host": "127.0.0.1",
            "port": self.config.vllm_port,
            **({"quantization": quantization} if quantization else {}),
            **args,
            **({"override-generation-config": sampling} if sampling else {}),
            "otlp-traces-endpoint": self.config.otlp,
        }
        config_text = (
            f"# Rendered by the GPU arbiter for {deployment}. Local changes will be overwritten.\n"
            + yaml.safe_dump(config, sort_keys=False, default_flow_style=False)
        )
        env = {
            "VLLM_DEPLOYMENT": deployment,
            "VLLM_MODEL_ID": spec["model_id"],
            "VLLM_CONFIG_SHA256": hashlib.sha256(config_text.encode()).hexdigest(),
            "VLLM_HOST_IP": "127.0.0.1",
            "VLLM_NO_USAGE_STATS": "1",
            "DO_NOT_TRACK": "1",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            # The root file system is read-only: every cache goes to the mounted state directory.
            "XDG_CACHE_HOME": "/root/.cache",
            "TRITON_CACHE_DIR": "/root/.cache/triton",
            "TORCHINDUCTOR_CACHE_DIR": "/root/.cache/torchinductor",
            "OTEL_SERVICE_NAME": "vllm",
            "OTEL_RESOURCE_ATTRIBUTES": (
                f"pinka.deployment={deployment},pinka.model_id={spec['model_id']}"
            ),
        }
        env_text = (
            f"# Rendered by the GPU arbiter for {deployment}. Local changes will be overwritten.\n"
        )
        env_text += "".join(f"{key}={value}\n" for key, value in env.items())
        return config_text, env_text

    def prepare(self, deployment: str) -> bool:
        """Render a deployment's files and point ``current.yml`` at it.

        Args:
            deployment: ``<id>@<version>``.

        Returns:
            True if a file or the link changed.

        Raises:
            ArbiterError: If it does not validate (nothing is changed then).
        """
        config_text, env_text = self.render(deployment)
        changed = _write_text(self.paths.vllm / "current.yaml", config_text)
        changed |= _write_text(self.paths.vllm / "current.env", env_text)
        changed |= _link(self.paths.vllm / "current.yml", f"deployments/{deployment}.yml")
        return changed

    def _healthy(self) -> bool:
        deadline = self.system.now() + dt.timedelta(seconds=self.config.serve_timeout)
        url = f"http://127.0.0.1:{self.config.vllm_port}/health"
        while True:
            status, _ = self.system.http_get(url)
            if status == 200:  # noqa: PLR2004 - HTTP OK
                return True
            if self.system.now() >= deadline:
                return False
            self.system.sleep(5)

    def _serve_locked(self, state: dict[str, Any], deployment: str, requester: str) -> bool:
        """Serve a deployment; the lock is held and the GPU does not train.

        Returns:
            True if vLLM was (re)started.
        """
        before = (state["mode"], state["deployment"])
        try:
            changed = self.prepare(deployment)
        except ArbiterError as exc:
            self.system.log(f"serve {deployment} refused: {exc}")
            raise
        if before == ("serving", deployment) and not changed and self._healthy():
            return False
        try:
            self.system.run(["systemctl", "restart", self.config.vllm_unit])
            healthy = self._healthy()
        except (subprocess.CalledProcessError, OSError) as exc:
            healthy, detail = False, str(exc)
        else:
            detail = "" if healthy else f"no answer on /health within {self.config.serve_timeout}s"
        if not healthy:
            with contextlib.suppress(subprocess.CalledProcessError, OSError):
                self.system.run(["systemctl", "stop", self.config.vllm_unit])
            self._transition(
                state,
                {"mode": "idle"},
                requester=requester,
                outcome="failed",
                detail=f"serve {deployment}: {detail}",
            )
            raise ArbiterError(f"vLLM did not come up with {deployment}: {detail}")
        self._transition(
            state,
            {"mode": "serving", "deployment": deployment},
            requester=requester,
            outcome="ok",
        )
        return True

    def _stop_serving(self, state: dict[str, Any], requester: str, detail: str) -> None:
        self.system.run(["systemctl", "stop", self.config.vllm_unit])
        self._transition(state, {"mode": "idle"}, requester=requester, outcome="ok", detail=detail)

    # --- requests ---------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """The mode, the last transitions and whether this host trains.

        Returns:
            The answer.
        """
        state = self.state()
        answer: dict[str, Any] = {
            "ok": True,
            "mode": state["mode"],
            "deployment": state["deployment"],
            "run": state["run"],
            "since": state["since"],
            "node_role": self.config.node_role,
            "training_available": self.config.trains,
            "transitions": self.transitions(),
        }
        if state["run"]:
            with contextlib.suppress(ArbiterError):
                record = self.run_record(state["run"])
                answer["job"] = {k: record.get(k) for k in ("job", "status", "started_at")}
        return answer

    def serve(self, deployment: str, requester: str) -> dict[str, Any]:
        """Serve a deployment (``serve <id>@<version>``).

        Args:
            deployment: ``<id>@<version>``.
            requester: Who asked.

        Returns:
            The answer.

        Raises:
            ArbiterError: While a run trains, or if the deployment does not come up.
        """
        if not self.config.serves:
            raise ArbiterError("this host serves no inference (gpu_inference is off)")
        with self.lock():
            state = self.state()
            if state["mode"] == "training":
                raise ArbiterError(f"the GPU is training run {state['run']}; ask for idle first")
            changed = self._serve_locked(state, deployment, requester)
        return {"ok": True, "mode": "serving", "deployment": deployment, "changed": changed}

    def idle(self, requester: str, *, wait: bool = True) -> dict[str, Any]:
        """Stop serving, or stop the running job (``idle``).

        A running job gets ``stop_grace`` seconds to checkpoint; it ends
        interrupted if its job is resumable, failed otherwise, and the GPU
        stays idle.

        Args:
            requester: Who asked.
            wait: Wait until the job's runner has settled the run.

        Returns:
            The answer.

        Raises:
            ArbiterError: If the job does not end in time.
        """
        with self.lock():
            state = self.state()
            if state["mode"] == "serving":
                self._stop_serving(state, requester, "idle requested")
            if state["mode"] == "idle":
                return {"ok": True, "mode": "idle"}
            run = str(state["run"])
            record = self.run_record(run)
            record["stop_requested"] = requester
            self._save_run(record)
        self.system.stop_container(JOB_CONTAINER.format(run=run), self.config.stop_grace)
        if not wait:
            return {"ok": True, "mode": "training", "stopping": run}
        limit = self.config.stop_grace + 60
        deadline = self.system.now() + dt.timedelta(seconds=limit)
        while self.system.now() < deadline:
            if self.state()["mode"] != "training":
                return {"ok": True, "mode": "idle", "run": run}
            self.system.sleep(2)
        raise ArbiterError(f"run {run} did not end within {limit}s")

    def train(self, job_name: str, submission: Mapping[str, Any], requester: str) -> dict[str, Any]:
        """Start a training run (``train <job> <submission>``).

        Args:
            job_name: A job of the active platform's manifest.
            submission: ``base_model``, ``datasets`` (kind -> hash),
                ``hyperparameters``, ``seed``, ``sessions_drained`` and
                optionally ``restore``.
            requester: Who asked.

        Returns:
            The answer with the run ID and the job-spec hash.

        Raises:
            ArbiterError: If the host does not train, the submission or its
                inputs do not validate, or inference is still active.
        """
        if not self.config.trains:
            raise ArbiterError(
                f"training is unavailable on this host (node_role {self.config.node_role})"
            )
        job = self._job(job_name)
        clean = check_submission(job, submission)
        if job["base_model"]:
            model_dir, model = self.find_model(str(clean["base_model"]), rehash=True)
        else:
            model_dir, model = None, None
        for digest in clean["datasets"].values():
            self.check_dataset(digest)
        self._check_caps(job)
        if int(job["gpus"]) > 0 and not self.config.gpu_args:
            raise ArbiterError(
                f"job {job_name} needs the GPU, which no container gets on this host"
            )
        spec_hash = spec_sha256(job_name, clean)
        restore = bool(submission.get("restore", self.config.restore_after_training))
        with self.lock():
            state = self.state()
            if state["mode"] == "training":
                raise ArbiterError(f"the GPU is training run {state['run']}")
            if state["mode"] == "serving":
                if submission.get("sessions_drained") is not True:
                    raise ArbiterError(
                        "the controller has not declared its sessions drained (sessions_drained)"
                    )
                running, waiting = self.inference_queue()
                if running or waiting:
                    raise ArbiterError(
                        f"vLLM still has {running} running and {waiting} waiting requests"
                    )
            now = self.system.now()
            run = f"{job_name}-{now.strftime('%Y%m%dt%H%M%S')}-{spec_hash[:8]}"
            directory = self.paths.training / "jobs" / run
            if directory.exists():
                raise ArbiterError(f"run {run} exists; submit again")
            uid, gid = self.system.account(job["account"])
            directory.mkdir(parents=True, mode=0o755)
            for sub in ("output", "checkpoints"):
                (directory / sub).mkdir(mode=0o750)
                self.system.chown(directory / sub, uid, gid)
            record = {
                "record_version": 1,
                "run": run,
                "job": job_name,
                "spec": job,
                "submission": clean,
                "spec_sha256": spec_hash,
                "base_model_dir": str(model_dir) if model_dir else None,
                "base_model": _model_lineage(model) if model else None,
                "restore": restore,
                "requester": requester,
                "status": "starting",
                "attempts": 0,
                "submitted_at": _stamp(now),
                "started_at": None,
                "ended_at": None,
                "adapter": None,
                "detail": "",
            }
            self._save_run(record)
            previous = state["deployment"] if state["mode"] == "serving" else None
            if state["mode"] == "serving":
                self.system.run(["systemctl", "stop", self.config.vllm_unit])
            self._transition(
                state,
                {"mode": "training", "run": run, "previous": previous},
                requester=requester,
                outcome="ok",
                job=job_name,
            )
            self.system.start_job(run)
        return {"ok": True, "mode": "training", "run": run, "spec_sha256": spec_hash}

    def inference_queue(self) -> tuple[int, int]:
        """The running and waiting requests of vLLM, from its metrics.

        Returns:
            ``(running, waiting)``.

        Raises:
            ArbiterError: If vLLM's metrics cannot be read.
        """
        status, body = self.system.http_get(f"http://127.0.0.1:{self.config.vllm_port}/metrics")
        if status != 200:  # noqa: PLR2004 - HTTP OK
            raise ArbiterError("vLLM's metrics cannot be read; the queue is unknown")
        totals = {"running": 0.0, "waiting": 0.0}
        for line in body.splitlines():
            match = re.match(r"^vllm:num_requests_(running|waiting)(\{[^}]*\})?\s+(\S+)", line)
            if match:
                totals[match[1]] += float(match[3])
        return int(totals["running"]), int(totals["waiting"])

    def _rendered_training(self) -> dict[str, Any]:
        try:
            rendered = json.loads(self.paths.rendered.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ArbiterError("no platform is active") from None
        except (OSError, ValueError) as exc:
            raise ArbiterError(f"rendered.json: {exc}") from None
        training = rendered.get("training") if isinstance(rendered, dict) else None
        if not isinstance(training, dict):
            raise ArbiterError("the active platform does not require the GPU")
        return training

    def _job(self, name: str) -> dict[str, Any]:
        for job in self._rendered_training().get("jobs", []):
            if job.get("name") == name:
                return dict(job)
        raise ArbiterError(f"the active platform declares no job {name!r}")

    def _check_caps(self, job: Mapping[str, Any]) -> None:
        caps = self.config.caps
        if int(job["timeout_seconds"]) > int(caps["timeout_seconds"]):
            raise ArbiterError(f"job {job['name']}: timeout over the cap {caps['timeout_seconds']}")
        if _bytes(str(job["memory_max"])) > _bytes(str(caps["memory_max"])):
            raise ArbiterError(f"job {job['name']}: memory over the cap {caps['memory_max']}")
        if float(job["cpus"]) > float(caps["cpus"]):
            raise ArbiterError(f"job {job['name']}: CPUs over the cap {caps['cpus']}")

    # --- datasets ------------------------------------------------------------

    def check_dataset(self, digest: str) -> Path:
        """Check a published dataset before it is mounted: hash and split guard.

        Args:
            digest: The dataset hash.

        Returns:
            Its directory.

        Raises:
            ArbiterError: If it is missing, its files do not match the hash,
                or its provenance lists an instance of the ``test`` split.
        """
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise ArbiterError(f"invalid dataset hash {digest!r}")
        directory = self.paths.training / "datasets" / digest
        try:
            provenance = json.loads((directory / PROVENANCE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ArbiterError(f"dataset {digest} is not published") from None
        if not isinstance(provenance, dict) or provenance.get("dataset_sha256") != digest:
            raise ArbiterError(f"dataset {digest}: PROVENANCE.json names another dataset")
        check_instances(provenance.get("instances"))
        if tree_hash(tree_files(directory, exclude=(PROVENANCE,))) != digest:
            raise ArbiterError(f"dataset {digest}: its files do not match its hash")
        return directory

    def publish_dataset(
        self, source: str, provenance: Mapping[str, Any], requester: str
    ) -> dict[str, Any]:
        """Publish a dataset the controller built in its own state (``publish-dataset``).

        Args:
            source: The dataset's directory, below one of the controller's
                state directories.
            provenance: ``instances`` (``{id, split}`` each) and ``source_runs``.
            requester: Who asked.

        Returns:
            The answer with the dataset hash.

        Raises:
            ArbiterError: If the host does not train, the source is outside
                the controller's state, holds anything but regular files and
                directories, or lists an instance of the ``test`` split.
        """
        if not self.config.trains:
            raise ArbiterError(
                f"training is unavailable on this host (node_role {self.config.node_role})"
            )
        if not isinstance(provenance, Mapping) or set(provenance) != {"instances", "source_runs"}:
            raise ArbiterError("provenance must have exactly instances and source_runs")
        instances = check_instances(provenance["instances"])
        runs = provenance["source_runs"]
        if not isinstance(runs, list) or not all(isinstance(r, str) and r for r in runs):
            raise ArbiterError("source_runs must be a list of run IDs")
        src = self._dataset_source(source)
        datasets = self.paths.training / "datasets"
        gid = self.system.group(DATASETS_GROUP)
        temp = Path(tempfile.mkdtemp(prefix=".publish-", dir=datasets))
        try:
            owner = None if requester == "root" else self.system.account(self.config.peer)[0]
            files = copy_tree(src, temp, owner)
            if not files:
                raise ArbiterError(f"{source} holds no files")
            digest = tree_hash(files)
            target = datasets / digest
            if target.exists():
                self.check_dataset(digest)
                return {"ok": True, "dataset_sha256": digest, "published": False}
            platform = self._platform()
            record = {
                "record_version": 1,
                "dataset_sha256": digest,
                "published_at": _stamp(self.system.now()),
                "platform": platform,
                "instances": instances,
                "source_runs": list(runs),
                "files": files,
            }
            (temp / PROVENANCE).write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
            for path in sorted(temp.rglob("*"), reverse=True):
                path.chmod(0o750 if path.is_dir() else 0o640)
                self.system.chown(path, 0, gid)
            temp.chmod(0o750)
            self.system.chown(temp, 0, gid)
            temp.rename(target)
        finally:
            shutil.rmtree(temp, ignore_errors=True)
        self.system.log(f"dataset {digest} published by {requester} from {source}")
        return {"ok": True, "dataset_sha256": digest, "published": True}

    def _dataset_source(self, source: str) -> Path:
        if not isinstance(source, str) or not source.startswith("/"):
            raise ArbiterError("source must be an absolute path")
        path = Path(source)
        roots = [Path(p) for p in self._rendered_training().get("dataset_sources", [])]
        if path.is_symlink() or not path.is_dir():
            raise ArbiterError(f"{source} is not a directory")
        real = path.resolve(strict=True)
        if real != path or not any(
            r.is_dir() and real.is_relative_to(r.resolve()) and real != r.resolve() for r in roots
        ):
            raise ArbiterError(f"{source} is not inside the controller's state directories")
        return real

    def _platform(self) -> dict[str, Any]:
        try:
            active = json.loads(self.paths.active.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ArbiterError("no platform is active (active.json)") from None
        release = active.get("release", {}) if isinstance(active, dict) else {}
        return {
            "name": active.get("platform"),
            "version": active.get("version"),
            "commit": release.get("commit"),
        }

    # --- runs --------------------------------------------------------------------

    def container_argv(self, record: Mapping[str, Any], *, resume: bool) -> list[str]:
        """The ``docker run`` of a run: infra's fixed options, the manifest's limits.

        Args:
            record: The run's record.
            resume: The run resumes from its checkpoints.

        Returns:
            The command.

        Raises:
            ArbiterError: If an account or group is missing, or a dataset
                fails its check.
        """
        job, submission, run = record["spec"], record["submission"], record["run"]
        uid, gid = self.system.account(job["account"])
        datasets_gid = self.system.group(DATASETS_GROUP)
        directory = self.paths.training / "jobs" / run
        argv = [
            "docker",
            "run",
            "--rm",
            "--name",
            JOB_CONTAINER.format(run=run),
            "--pull",
            "never",
            *FIXED_OPTIONS,
            "--user",
            f"{uid}:{gid}",
            "--group-add",
            str(datasets_gid),
            "--memory",
            str(job["memory_max"]),
            "--memory-swap",
            str(job["memory_max"]),
            "--cpus",
            str(job["cpus"]),
            "--pids-limit",
            str(self.config.pids_limit),
            "--env",
            f"PINKA_JOB_RUN={run}",
            "--env",
            f"PINKA_JOB_SEED={submission['seed']}",
            "--env",
            "PINKA_JOB_HYPERPARAMETERS="
            + json.dumps(submission["hyperparameters"], sort_keys=True, separators=(",", ":")),
            "--env",
            f"PINKA_JOB_RESUME={1 if resume else 0}",
        ]
        if int(job["gpus"]) > 0:
            argv += list(self.config.gpu_args)
        if record.get("base_model_dir"):
            argv += [
                "--mount",
                f"type=bind,source={record['base_model_dir']},target={JOB_MODEL},readonly",
            ]
        for kind, digest in sorted(submission["datasets"].items()):
            source = self.check_dataset(digest)
            argv += ["--mount", f"type=bind,source={source},target={JOB_DATASETS}/{kind},readonly"]
        argv += [
            "--mount",
            f"type=bind,source={directory / 'output'},target={JOB_OUTPUT}",
            "--mount",
            f"type=bind,source={directory / 'checkpoints'},target={JOB_CHECKPOINTS}",
            str(job["image"]),
            *[str(c) for c in job["command"]],
        ]
        return argv

    def run_job(self, run: str) -> int:
        """Run a job's container and settle the run (``pinka-gpu-job@<run>``).

        Args:
            run: Run ID.

        Returns:
            0 if the run succeeded.
        """
        with self.lock():
            argv = self._start_run(run)
        if argv is None:
            return 1
        record = self.run_record(run)
        started = self.system.now()
        code = self.system.docker_run(
            argv,
            JOB_CONTAINER.format(run=run),
            float(record["spec"]["timeout_seconds"]),
            self.config.stop_grace,
        )
        seconds = int((self.system.now() - started).total_seconds())
        with self.lock():
            record = self.run_record(run)
            state = self.state()
            if self.system.terminated and not record.get("stop_requested"):
                record["stop_requested"] = "systemd"
            if record.get("stop_requested"):
                status = "interrupted" if record["spec"]["resumable"] else "failed"
                detail = f"stopped by {record['stop_requested']}"
            elif code is None:
                status, detail = "failed", f"timeout after {seconds}s"
            elif code != 0:
                status, detail = "failed", f"exit status {code}"
            else:
                try:
                    record["adapter"] = self.register_adapter(record)
                    status, detail = "succeeded", ""
                except ArbiterError as exc:
                    status, detail = "failed", str(exc)
            return self._settle(state, record, status, detail)

    def _start_run(self, run: str) -> list[str] | None:
        """Check that a run may start and mark it running; the lock is held.

        Returns:
            The container's command, or None if the run does not start.
        """
        record = self.run_record(run)
        state = self.state()
        if state["mode"] != "training" or state["run"] != run:
            self.system.log(f"run {run} is not the GPU's current run; not starting it")
            return None
        if record["status"] not in ("starting", "resuming"):
            self.system.log(f"run {run} is {record['status']}; not starting it")
            return None
        if record.get("stop_requested"):
            status = "interrupted" if record["spec"]["resumable"] else "failed"
            self._settle(
                state, record, status, f"stopped by {record['stop_requested']} before it ran"
            )
            return None
        try:
            argv = self.container_argv(record, resume=record["status"] == "resuming")
        except ArbiterError as exc:
            self._settle(state, record, "failed", str(exc))
            return None
        record.update(
            {
                "status": "running",
                "attempts": int(record["attempts"]) + 1,
                "started_at": record["started_at"] or _stamp(self.system.now()),
            }
        )
        self._save_run(record)
        return argv

    def _settle(
        self, state: dict[str, Any], record: dict[str, Any], status: str, detail: str
    ) -> int:
        """End a run and leave training: back to the previous deployment or idle."""
        record.update({"status": status, "ended_at": _stamp(self.system.now()), "detail": detail})
        self._save_run(record)
        job = str(record["job"])
        previous = state.get("previous")
        outcome = "ok" if status == "succeeded" else status
        if record["restore"] and previous and not record.get("stop_requested"):
            self._transition(
                state,
                {"mode": "idle"},
                requester="arbiter",
                outcome=outcome,
                detail=f"run {record['run']} {status}; restoring {previous}",
                job=job,
            )
            with contextlib.suppress(ArbiterError):
                self._serve_locked(state, str(previous), "arbiter")
        else:
            self._transition(
                state,
                {"mode": "idle"},
                requester="arbiter",
                outcome=outcome,
                detail=f"run {record['run']} {status}",
                job=job,
            )
        return 0 if status == "succeeded" else 1

    def register_adapter(self, record: Mapping[str, Any]) -> dict[str, str]:
        """Hash a run's adapter and register it read-only with its lineage.

        Args:
            record: The run's record.

        Returns:
            ``{"name", "sha256"}``.

        Raises:
            ArbiterError: If the run left no adapter or anything but regular
                files and directories.
        """
        job, run = record["spec"], str(record["run"])
        source = self.paths.training / "jobs" / run / "output" / JOB_ADAPTER
        if source.is_symlink() or not source.is_dir():
            raise ArbiterError(f"run {run} left no adapter in {JOB_OUTPUT}/{JOB_ADAPTER}/")
        name = str(job["adapter"])
        parent = self.paths.models / "adapters" / name
        parent.mkdir(parents=True, exist_ok=True)
        parent.chmod(0o755)
        uid, _ = self.system.account(str(job["account"]))
        temp = Path(tempfile.mkdtemp(prefix=f".{run}-", dir=parent))
        try:
            files = copy_tree(source, temp, uid)
            if not files:
                raise ArbiterError(f"run {run} left an empty adapter")
            digest = tree_hash(files)
            target = parent / digest
            if target.exists():
                self.find_adapter(f"{name}@{digest}")
                return {"name": name, "sha256": digest}
            now = _stamp(self.system.now())
            base = record.get("base_model") or {}
            manifest = {
                "record_version": 1,
                "name": name,
                "sha256": digest,
                "base_model_id": base.get("id"),
                "run": run,
                "registered_at": now,
                "files": files,
            }
            lineage = {
                "record_version": 1,
                "adapter": {"name": name, "sha256": digest},
                "base_model": base or None,
                "datasets": sorted(record["submission"]["datasets"].values()),
                "image": {"reference": job["image"], "digest": str(job["image"]).rsplit("@", 1)[1]},
                "job": {"name": record["job"], "run": run, "spec_sha256": record["spec_sha256"]},
                "hyperparameters": record["submission"]["hyperparameters"],
                "seed": record["submission"]["seed"],
                "platform": self._platform(),
                "infra": {"commit": self._infra_commit()},
                "started_at": record["started_at"],
                "ended_at": now,
            }
            (temp / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            (temp / LINEAGE).write_text(json.dumps(lineage, indent=2) + "\n", encoding="utf-8")
            for path in sorted(temp.rglob("*"), reverse=True):
                path.chmod(0o555 if path.is_dir() else 0o444)
                self.system.chown(path, 0, 0)
            temp.chmod(0o555)
            self.system.chown(temp, 0, 0)
            temp.rename(target)
        finally:
            if temp.exists():
                for path in temp.rglob("*"):
                    if path.is_dir() and not path.is_symlink():
                        path.chmod(0o755)
                temp.chmod(0o755)
                shutil.rmtree(temp, ignore_errors=True)
        self.system.log(f"adapter {name}@{digest} registered from run {run}")
        return {"name": name, "sha256": digest}

    def _infra_commit(self) -> str | None:
        with contextlib.suppress(OSError, ValueError):
            data = json.loads(self.paths.bootstrap_state.read_text(encoding="utf-8"))
            commit = data.get("commit") if isinstance(data, dict) else None
            return str(commit) if commit else None
        return None

    # --- boot ---------------------------------------------------------------------

    def boot(self) -> dict[str, Any]:
        """Settle the GPU when the lab starts (``pinka-gpu-arbiter.service``).

        After a reboot, a run left in training resumes (resumable jobs) or
        ends interrupted, and the default deployment is served or the GPU
        stays idle (``after_boot``). When the lab starts again within the same
        boot (``pinka start`` after ``pinka stop``), a running job keeps
        running, a serving deployment is brought back if it stopped, and an
        idle GPU follows ``after_boot`` as after a reboot.

        Returns:
            The mode afterwards.
        """
        with self.lock():
            state = self.state()
            current = self.system.boot_id()
            same_boot = state["boot_id"] == current
            if not same_boot:
                state["boot_id"] = current
                self._save_state(state)
            if state["mode"] == "training":
                if same_boot:
                    return {"ok": True, "mode": "training", "run": state["run"]}
                return self._interrupted_by_reboot(state)
            if same_boot and state["mode"] == "serving":
                target = str(state["deployment"])
            elif self.config.after_boot == "serve" and self.config.serves:
                target = self.config.default_deployment or ""
            else:
                target = ""
            try:
                if target:
                    self._serve_locked(state, target, "boot")
                elif state["mode"] == "serving":
                    self._stop_serving(state, "boot", "after_boot is idle")
            except (ArbiterError, subprocess.CalledProcessError, OSError) as exc:
                return {"ok": False, "error": str(exc)}
            return {"ok": True, "mode": self.state()["mode"]}

    def _interrupted_by_reboot(self, state: dict[str, Any]) -> dict[str, Any]:
        record = self.run_record(str(state["run"]))
        if record["spec"]["resumable"] and self.config.trains:
            record.update({"status": "resuming", "detail": "interrupted by a reboot"})
            self._save_run(record)
            self._transition(
                state,
                {"mode": "training", "run": record["run"], "previous": state["previous"]},
                requester="boot",
                outcome="resumed",
                detail="interrupted by a reboot; resuming from its checkpoints",
                job=str(record["job"]),
            )
            self.system.start_job(str(record["run"]))
            return {"ok": True, "mode": "training", "run": record["run"]}
        self._settle(state, record, "interrupted", "interrupted by a reboot")
        return {"ok": True, "mode": self.state()["mode"]}

    # --- the socket ------------------------------------------------------------------

    def handle(self, request: object, uid: int) -> dict[str, Any]:
        """Answer one request.

        Args:
            request: The parsed request line.
            uid: The peer's UID.

        Returns:
            The answer.

        Raises:
            ArbiterError: If the peer or the request is not allowed or fails.
        """
        try:
            allowed = {0, self.system.account(self.config.peer)[0]}
        except ArbiterError:
            allowed = {0}
        if uid not in allowed:
            raise ArbiterError("only the controller and root may use the GPU arbiter")
        if not isinstance(request, dict) or request.get("request") not in REQUESTS:
            raise ArbiterError(f"request must be one of {', '.join(REQUESTS)}")
        kind = request["request"]
        requester = "root" if uid == 0 else self.config.peer
        if uid != 0 and self.config.node_role == "inference" and kind not in SERVING_ONLY:
            raise ArbiterError(f"{kind} is not available on an inference node")
        expected = {
            "status": {"request"},
            "serve": {"request", "deployment"},
            "idle": {"request"},
            "train": {"request", "job", "submission"},
            "publish-dataset": {"request", "source", "provenance"},
        }[kind]
        if set(request) != expected:
            raise ArbiterError(f"{kind} takes exactly {', '.join(sorted(expected))}")
        if kind == "status":
            return self.status()
        if kind == "serve":
            return self.serve(request["deployment"], requester)
        if kind == "idle":
            return self.idle(requester)
        if kind == "train":
            if not isinstance(request["job"], str) or not isinstance(request["submission"], dict):
                raise ArbiterError("train takes a job name and a submission object")
            return self.train(request["job"], request["submission"], requester)
        return self.publish_dataset(request["source"], request["provenance"], requester)

    def connection(self, stdin: TextIO, stdout: TextIO, uid: int) -> int:
        """Answer the request of one connection.

        Args:
            stdin: The connection, readable.
            stdout: The connection, writable.
            uid: The peer's UID.

        Returns:
            0 if the request succeeded, 1 otherwise.
        """
        try:
            line = stdin.readline(MAX_REQUEST + 1)
            if len(line) > MAX_REQUEST:
                raise ArbiterError("request too long")
            try:
                request = json.loads(line)
            except ValueError:
                raise ArbiterError("request is not one JSON line") from None
            answer = self.handle(request, uid)
        except (ArbiterError, subprocess.CalledProcessError, OSError) as exc:
            stdout.write(json.dumps({"ok": False, "error": str(exc)}) + "\n")
            stdout.flush()
            return 1
        stdout.write(json.dumps(answer, sort_keys=True) + "\n")
        stdout.flush()
        return 0


# Infra's options of every job container (§7.18), never taken from the manifest:
# no network, a read-only root file system with a private /tmp, no
# capabilities, no new privileges, Docker's default seccomp profile (nothing
# overrides it), and no host PID, IPC, network or user namespace.
FIXED_OPTIONS = (
    "--network",
    "none",
    "--read-only",
    "--tmpfs",
    "/tmp:rw,nosuid,nodev",  # noqa: S108 - the container's own private /tmp
    "--cap-drop",
    "ALL",
    "--security-opt",
    "no-new-privileges",
    "--ipc",
    "private",
)


# --- submissions, datasets and trees ----------------------------------------------------


def check_submission(job: Mapping[str, Any], submission: Mapping[str, Any]) -> dict[str, Any]:
    """Check a submission against its job and return its canonical part.

    Args:
        job: The rendered job.
        submission: The controller's submission.

    Returns:
        ``base_model``, ``datasets``, ``hyperparameters`` and ``seed``.

    Raises:
        ArbiterError: If a key is unknown or missing, or does not fit the job.
    """
    unknown = sorted(set(submission) - SUBMISSION_KEYS)
    if unknown:
        raise ArbiterError(f"unknown submission keys {', '.join(unknown)}")
    base = submission.get("base_model")
    if job["base_model"] != (base is not None) or (base is not None and not isinstance(base, str)):
        wanted = "a model id of models.lock.yml" if job["base_model"] else "null"
        raise ArbiterError(f"job {job['name']}: base_model must be {wanted}")
    datasets = submission.get("datasets", {})
    if not isinstance(datasets, dict) or sorted(datasets) != sorted(job["datasets"]):
        raise ArbiterError(
            f"job {job['name']}: datasets must name exactly the kinds {sorted(job['datasets'])}"
        )
    hyperparameters = submission.get("hyperparameters", {})
    if not isinstance(hyperparameters, dict):
        raise ArbiterError("hyperparameters must be an object")
    seed = submission.get("seed")
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise ArbiterError("seed must be an integer")
    for key in ("sessions_drained", "restore"):
        if key in submission and not isinstance(submission[key], bool):
            raise ArbiterError(f"{key} must be true or false")
    return {
        "base_model": base,
        "datasets": dict(datasets),
        "hyperparameters": hyperparameters,
        "seed": seed,
    }


def spec_sha256(job: str, submission: Mapping[str, Any]) -> str:
    """The job-spec hash: SHA-256 of the canonical JSON of the submission.

    Args:
        job: Job name.
        submission: The canonical submission (:func:`check_submission`).

    Returns:
        Hex digest.
    """
    canonical = json.dumps(
        {"job": job, **submission}, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def check_instances(instances: object) -> list[dict[str, str]]:
    """The split guard: every instance with a split, none of them ``test``.

    Args:
        instances: The provenance's instances.

    Returns:
        The instances.

    Raises:
        ArbiterError: If an instance is malformed or in the ``test`` split.
    """
    if not isinstance(instances, list) or not instances:
        raise ArbiterError("the provenance lists no instances")
    out = []
    for item in instances:
        if not isinstance(item, dict) or set(item) != {"id", "split"}:
            raise ArbiterError("every instance needs exactly id and split")
        if not isinstance(item["id"], str) or not item["id"]:
            raise ArbiterError("an instance id must be a non-empty string")
        split = item["split"]
        if not isinstance(split, str) or not SPLIT_RE.fullmatch(split):
            raise ArbiterError(f"instance {item['id']}: invalid split {split!r}")
        if split == FORBIDDEN_SPLIT:
            raise ArbiterError(f"instance {item['id']} is in the {FORBIDDEN_SPLIT} split")
        out.append({"id": item["id"], "split": split})
    return out


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_files(directory: Path, exclude: Sequence[str] = ()) -> list[dict[str, Any]]:
    """Every regular file below a directory, with its hash and size, sorted by path.

    Args:
        directory: The directory.
        exclude: Top-level file names left out (the records themselves).

    Returns:
        ``{path, sha256, size}`` per file.

    Raises:
        ArbiterError: On a link, a special file or a setuid or setgid file.
    """
    out = []
    for root, dirs, names in os.walk(directory):
        for name in [*dirs, *names]:
            path = Path(root) / name
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or not (
                stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)
            ):
                raise ArbiterError(f"{path} is not a regular file or directory")
            if info.st_mode & (stat.S_ISUID | stat.S_ISGID):
                raise ArbiterError(f"{path} is setuid or setgid")
        for name in names:
            path = Path(root) / name
            relative = path.relative_to(directory).as_posix()
            if relative in exclude:
                continue
            out.append(
                {"path": relative, "sha256": _sha256_file(path), "size": path.stat().st_size}
            )
    return sorted(out, key=lambda f: f["path"])


def tree_hash(files: Sequence[Mapping[str, Any]]) -> str:
    """The hash of a dataset or adapter: SHA-256 of ``sha256sum``-style lines.

    Args:
        files: :func:`tree_files` output.

    Returns:
        Hex digest.
    """
    lines = "".join(f"{f['sha256']}  {f['path']}\n" for f in sorted(files, key=lambda f: f["path"]))
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()


def copy_tree(source: Path, target: Path, owner: int | None) -> list[dict[str, Any]]:
    """Copy regular files and directories, hashing what was copied.

    The walk goes through directory file descriptors and opens nothing by a
    path that could be swapped for a link while it runs; the hash is of the
    copy. With ``owner``, every file and directory must belong to that UID,
    so the requester cannot hand in a file it could not read itself (a hard
    link to a root file, for one).

    Args:
        source: The directory to copy.
        target: An existing, empty directory.
        owner: UID every entry must belong to, or None (root's own request).

    Returns:
        :func:`tree_files` of the copy.

    Raises:
        ArbiterError: On a link, a special file, a setuid or setgid file, or
            an entry of another owner.
    """
    out: list[dict[str, Any]] = []
    try:
        top = os.open(source, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        raise ArbiterError(f"{source} is not a directory") from None
    try:
        if owner is not None and os.fstat(top).st_uid != owner:
            raise ArbiterError(f"{source} does not belong to the requester")
        _copy_dir(top, source=source, target=target, relative="", owner=owner, out=out, depth=0)
    finally:
        os.close(top)
    return sorted(out, key=lambda f: f["path"])


MAX_DEPTH = 64


def _copy_dir(
    fd: int,
    *,
    source: Path,
    target: Path,
    relative: str,
    owner: int | None,
    out: list[dict[str, Any]],
    depth: int,
) -> None:
    if depth > MAX_DEPTH:
        raise ArbiterError(f"{source}: directories nested deeper than {MAX_DEPTH}")
    for name in sorted(os.listdir(fd)):
        rel = f"{relative}/{name}" if relative else name
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if info.st_mode & (stat.S_ISUID | stat.S_ISGID):
            raise ArbiterError(f"{source / rel} is setuid or setgid")
        if owner is not None and info.st_uid != owner:
            raise ArbiterError(f"{source / rel} does not belong to the requester")
        if stat.S_ISDIR(info.st_mode):
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            try:
                (target / rel).mkdir(mode=0o700)
                _copy_dir(
                    child,
                    source=source,
                    target=target,
                    relative=rel,
                    owner=owner,
                    out=out,
                    depth=depth + 1,
                )
            finally:
                os.close(child)
        elif stat.S_ISREG(info.st_mode):
            try:
                handle_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            except OSError:
                raise ArbiterError(f"{source / rel} is not a regular file") from None
            with os.fdopen(handle_fd, "rb") as handle:
                opened = os.fstat(handle.fileno())
                if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                    raise ArbiterError(f"{source / rel} changed while it was copied")
                digest = hashlib.sha256()
                size = 0
                with (target / rel).open("xb") as copy:
                    for chunk in iter(lambda: handle.read(1 << 20), b""):
                        digest.update(chunk)
                        size += len(chunk)
                        copy.write(chunk)
            out.append({"path": rel, "sha256": digest.hexdigest(), "size": size})
        else:
            raise ArbiterError(f"{source / rel} is not a regular file or directory")


def _model_lineage(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": manifest.get("id"),
        "hf_repo": manifest.get("hf_repo"),
        "revision": manifest.get("revision"),
        "files": [{"path": f["path"], "sha256": f["sha256"]} for f in manifest.get("files", [])],
    }


# --- small helpers -----------------------------------------------------------------------


def _bytes(size: str) -> int:
    match = re.fullmatch(r"([0-9]+)([KMGT]?)", size)
    if match is None:
        raise ArbiterError(f"invalid size {size!r}")
    power: int = "_KMGT".index(match[2] or "_")
    return int(match[1]) * (1 << (10 * power))


def _stamp(moment: dt.datetime) -> str:
    return moment.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _mode_text(state: Mapping[str, Any]) -> str:
    if state["mode"] == "serving":
        return f"serving {state['deployment']}"
    if state["mode"] == "training":
        return f"training {state['run']}"
    return "idle"


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ArbiterError(f"{path}: {exc}") from None
    if not isinstance(data, dict):
        raise ArbiterError(f"{path}: not a mapping")
    return data


def _write_text(path: Path, text: str) -> bool:
    try:
        if path.read_text(encoding="utf-8") == text:
            return False
    except FileNotFoundError:
        pass
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_text(text, encoding="utf-8")
    temp.chmod(0o644)
    temp.replace(path)
    return True


def _write_json(path: Path, data: Mapping[str, Any], mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.chmod(mode)
    temp.replace(path)


def _link(link: Path, target: str) -> bool:
    if link.is_symlink() and str(link.readlink()) == target:
        return False
    temp = link.with_name(f".{link.name}.tmp")
    temp.unlink(missing_ok=True)
    temp.symlink_to(target)
    temp.replace(link)
    return True


def peer_uid(fd: int = 0) -> int:
    """Return the UID of the process on the other end of a Unix socket.

    Args:
        fd: Socket file descriptor (stdin under ``Accept=yes``).

    Returns:
        The peer's UID.
    """
    with socket.socket(fileno=os.dup(fd)) as sock:
        creds = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    _, uid, _ = struct.unpack("3i", creds)
    return int(uid)


def main(argv: Sequence[str], make: Callable[[], Arbiter] | None = None) -> int:
    """Run a subcommand.

    Args:
        argv: Arguments without the program name.
        make: Builds the arbiter (tests).

    Returns:
        Exit status.
    """
    parser = argparse.ArgumentParser(prog="gpu-arbiter", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("connection", help="answer one request on stdin/stdout (socket activation)")
    sub.add_parser("boot", help="settle the GPU once per boot")
    run_cmd = sub.add_parser("run-job", help="run a training run (pinka-gpu-job@<run>)")
    run_cmd.add_argument("run")
    for name, text in (
        ("render", "print what a deployment renders to"),
        ("prepare", "render a deployment's files without starting it"),
        ("serve", "serve a deployment as root (Ansible; pinka gpu uses the socket)"),
    ):
        cmd = sub.add_parser(name, help=text)
        cmd.add_argument("deployment")
        if name == "serve":
            cmd.add_argument(
                "--unless-training",
                action="store_true",
                help="leave a training run alone and change nothing",
            )
    sub.add_parser("status", help="print the mode and the last transitions")
    args = parser.parse_args(list(argv))
    try:
        arbiter = make() if make else Arbiter(Paths(), load_config(Paths().config), System())
        if args.command == "connection":
            return arbiter.connection(sys.stdin, sys.stdout, peer_uid(0))
        if args.command == "run-job":
            return arbiter.run_job(args.run)
        if args.command == "render":
            config_text, env_text = arbiter.render(args.deployment)
            sys.stdout.write(config_text + "---\n" + env_text)
            return 0
        answer = _admin(arbiter, args)
    except (ArbiterError, subprocess.CalledProcessError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    print(json.dumps(answer, sort_keys=True))
    return 0 if answer.get("ok") else 1


def _admin(arbiter: Arbiter, args: argparse.Namespace) -> dict[str, Any]:
    """The commands for root that answer with one JSON object."""
    if args.command == "status":
        return arbiter.status()
    if args.command == "serve":
        if args.unless_training and arbiter.state()["mode"] == "training":
            return {"ok": True, "mode": "training", "changed": False, "skipped": True}
        return arbiter.serve(args.deployment, "root")
    if args.command == "prepare":
        with arbiter.lock():
            if arbiter.state()["mode"] == "training":
                raise ArbiterError("the GPU is training; nothing is rendered")
            return {"ok": True, "changed": arbiter.prepare(args.deployment)}
    return arbiter.boot()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
