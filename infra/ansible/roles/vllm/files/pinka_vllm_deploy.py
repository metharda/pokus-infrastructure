#!/opt/pinka/ansible-venv/bin/python3 -I
# Managed by pinka (ansible/roles/vllm/files/pinka_vllm_deploy.py). Local changes will be overwritten.
"""Switch the vLLM deployment (contracts.md#inference, resolution C3).

Installed as ``/usr/local/libexec/pinka/vllm-deploy`` by the vllm role and run
as root, never through sudo:

* ``serve``: started by ``pinka-vllm-deploy.socket`` (``Accept=yes``) for one
  connection on stdin/stdout. The peer must be ``potka-orch``
  (``SO_PEERCRED``); it sends one JSON line ``{"deployment": "<id>@<version>"}``
  and gets ``{"ok": true}`` or ``{"ok": false, "error": "..."}``.
* ``activate <id>@<version> [--no-restart]``: the same switch for root
  (Ansible, operators); prints ``{"ok": true, "changed": bool}``.
* ``render <id>@<version>``: print the vLLM configuration and environment
  without changing anything.

A switch validates the deployment file (a regular, root-owned file in
``/etc/pinka/vllm/deployments/`` that only root may write), the serving
profile and the verified model and adapters (their ``MANIFEST.json``), renders
``current.yaml`` (the ``vllm serve --config`` file) and ``current.env``,
points ``current.yml`` at the deployment and restarts
``pinka-inference.service`` if anything changed. Deployment overrides are
limited to the profile's ``overridable`` keys: model, served name, host,
port and the tracing endpoint are always set here.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import pwd
import re
import socket
import stat
import struct
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, TextIO

import yaml

DEPLOYMENT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}@[0-9]{1,6}$")
PROFILE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
ADAPTER_RE = re.compile(r"^(?P<name>[a-z0-9][a-z0-9._-]{0,62})@(?P<sha256>[0-9a-f]{64})$")
SPEC_KEYS = {"id", "version", "model_id", "adapters", "profile", "overrides", "sampling_defaults"}
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
MAX_REQUEST = 4096
UNIT = "pinka-inference.service"
PEER = "potka-orch"
CONTAINER_MODELS = "/models"
Runner = Callable[[Sequence[str]], None]


class DeployError(Exception):
    """The request or the deployment cannot be served."""


@dataclasses.dataclass(frozen=True)
class Settings:
    """Paths and fixed values (tests point them at a temporary tree).

    Attributes:
        etc: ``/etc/pinka/vllm`` (deployments/, profiles/, current.*).
        models: ``/srv/pinka/models``.
        port: vLLM's port on 127.0.0.1 (contracts.md#ports).
        otlp: OTLP gRPC endpoint of the collector.
        owner_uid: Required owner of deployment files.
    """

    etc: Path = Path("/etc/pinka/vllm")
    models: Path = Path("/srv/pinka/models")
    port: int = 18704
    otlp: str = "http://127.0.0.1:4317"
    owner_uid: int = 0


@dataclasses.dataclass(frozen=True)
class Rendered:
    """What a deployment renders to.

    Attributes:
        config: Text of current.yaml.
        env: Text of current.env.
    """

    config: str
    env: str


def deployment_path(settings: Settings, deployment: str) -> Path:
    """Validate a deployment reference and return its file.

    Args:
        settings: Paths.
        deployment: ``<id>@<version>``.

    Returns:
        The deployment file.

    Raises:
        DeployError: If the reference or the file is not acceptable.
    """
    if not isinstance(deployment, str) or not DEPLOYMENT_RE.fullmatch(deployment):
        raise DeployError("deployment must match <id>@<version> ([a-z0-9-], version 0-999999)")
    path = settings.etc / "deployments" / f"{deployment}.yml"
    try:
        info = path.lstat()
    except FileNotFoundError:
        raise DeployError(f"no deployment {deployment}") from None
    if not stat.S_ISREG(info.st_mode):
        raise DeployError(f"{path} is not a regular file")
    if info.st_uid != settings.owner_uid or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise DeployError(f"{path} must be owned by root and writable only by root")
    return path


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise DeployError(f"{path}: {exc}") from None
    if not isinstance(data, dict):
        raise DeployError(f"{path}: not a mapping")
    return data


def load_deployment(settings: Settings, deployment: str) -> dict[str, Any]:
    """Load and check a deployment spec (contracts.md#inference, C9e).

    Args:
        settings: Paths.
        deployment: ``<id>@<version>``.

    Returns:
        The spec.

    Raises:
        DeployError: If the spec is malformed or does not match its file name.
    """
    spec = _load_yaml(deployment_path(settings, deployment))
    unknown = sorted(set(spec) - SPEC_KEYS)
    if unknown:
        raise DeployError(f"{deployment}: unknown keys {', '.join(unknown)}")
    if f"{spec.get('id')}@{spec.get('version')}" != deployment:
        raise DeployError(f"{deployment}: id and version do not match the file name")
    for key, kind in (("adapters", list), ("overrides", dict), ("sampling_defaults", dict)):
        if not isinstance(spec.setdefault(key, kind()), kind):
            raise DeployError(f"{deployment}: {key} must be a {kind.__name__}")
    if not isinstance(spec.get("model_id"), str) or not isinstance(spec.get("profile"), str):
        raise DeployError(f"{deployment}: model_id and profile are required")
    return spec


def load_profile(settings: Settings, name: str) -> dict[str, Any]:
    """Load a serving profile.

    Args:
        settings: Paths.
        name: Profile name.

    Returns:
        The profile.

    Raises:
        DeployError: If the name is invalid or the profile is missing.
    """
    if not PROFILE_RE.fullmatch(name):
        raise DeployError(f"invalid profile name {name!r}")
    path = settings.etc / "profiles" / f"{name}.yml"
    if not path.is_file():
        raise DeployError(f"no serving profile {name}")
    return _load_yaml(path)


def _manifests(settings: Settings) -> list[tuple[Path, dict[str, Any]]]:
    found = []
    for path in sorted(settings.models.glob("*/*/*/MANIFEST.json")):
        if path.relative_to(settings.models).parts[0] == "adapters":
            continue
        try:
            found.append((path.parent, json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            continue
    return found


def _check_files(directory: Path, manifest: Mapping[str, Any]) -> None:
    for record in manifest.get("files", []):
        file = directory / record["path"]
        if not file.is_file() or file.stat().st_size != record["size"]:
            raise DeployError(f"{directory}: {record['path']} is missing or has another size")


def find_model(settings: Settings, model_id: str) -> tuple[Path, dict[str, Any]]:
    """Find the verified model of an id.

    Args:
        settings: Paths.
        model_id: Model id from models.lock.yml.

    Returns:
        The model directory and its manifest.

    Raises:
        DeployError: If no verified model or more than one has this id.
    """
    matches = [(d, m) for d, m in _manifests(settings) if m.get("id") == model_id]
    if len(matches) != 1:
        state = "is not downloaded and verified" if not matches else "is ambiguous"
        raise DeployError(f"model {model_id} {state} (models role)")
    _check_files(*matches[0])
    return matches[0]


def find_adapter(settings: Settings, reference: str) -> tuple[str, Path, dict[str, Any]]:
    """Find a verified adapter.

    Args:
        settings: Paths.
        reference: ``<name>@<sha256>``.

    Returns:
        Name, directory and manifest.

    Raises:
        DeployError: If the reference is invalid or the adapter is not verified.
    """
    match = ADAPTER_RE.fullmatch(reference) if isinstance(reference, str) else None
    if match is None:
        raise DeployError(f"invalid adapter reference {reference!r} (<name>@<sha256>)")
    directory = settings.models / "adapters" / match["name"] / match["sha256"]
    try:
        manifest = json.loads((directory / "MANIFEST.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise DeployError(f"adapter {reference} is not downloaded and verified") from None
    _check_files(directory, manifest)
    return match["name"], directory, manifest


def _container_path(settings: Settings, directory: Path) -> str:
    return f"{CONTAINER_MODELS}/{directory.relative_to(settings.models).as_posix()}"


def render(settings: Settings, deployment: str) -> Rendered:
    """Render the vLLM configuration and environment of a deployment.

    Args:
        settings: Paths and fixed values.
        deployment: ``<id>@<version>``.

    Returns:
        current.yaml and current.env.

    Raises:
        DeployError: If anything does not validate.
    """
    spec = load_deployment(settings, deployment)
    profile = load_profile(settings, spec["profile"])
    model_dir, manifest = find_model(settings, spec["model_id"])
    overridable: dict[str, str] = profile.get("overridable", {})
    unknown = sorted(set(spec["overrides"]) - set(overridable))
    if unknown:
        raise DeployError(f"{deployment}: overrides not allowed by {spec['profile']}: {unknown}")
    args: dict[str, Any] = dict(profile.get("args", {}))
    args.update({overridable[k]: v for k, v in spec["overrides"].items()})
    quantization = args.pop("quantization", manifest.get("quantization"))
    if quantization not in profile.get("quantizations", [None]):
        raise DeployError(f"quantization {quantization} does not fit profile {spec['profile']}")
    template = args.pop("chat-template", None)
    if template is not None:
        listed = {f["path"] for f in manifest.get("files", [])}
        if template not in listed:
            raise DeployError(f"chat_template {template} is not a verified file of the model")
        args["chat-template"] = f"{_container_path(settings, model_dir)}/{template}"
    if args.get("enable-auto-tool-choice") and not args.get("tool-call-parser"):
        raise DeployError(f"{deployment}: tool calling needs overrides.tool_call_parser")
    modules = []
    for reference in spec["adapters"]:
        name, directory, adapter = find_adapter(settings, reference)
        if adapter.get("base_model_id") != spec["model_id"]:
            raise DeployError(f"adapter {reference} is for {adapter.get('base_model_id')}")
        modules.append(f"{name}={_container_path(settings, directory)}")
    if modules:
        args["enable-lora"] = True
        args["lora-modules"] = modules
    sampling = spec["sampling_defaults"]
    unknown = sorted(set(sampling) - SAMPLING_KEYS)
    if unknown or not all(isinstance(v, (int, float)) for v in sampling.values()):
        raise DeployError(
            f"{deployment}: sampling_defaults allow numbers for {sorted(SAMPLING_KEYS)}"
        )
    config: dict[str, Any] = {
        "model": _container_path(settings, model_dir),
        "served-model-name": spec["model_id"],
        "host": "127.0.0.1",
        "port": settings.port,
        **({"quantization": quantization} if quantization else {}),
        **args,
        **({"override-generation-config": sampling} if sampling else {}),
        "otlp-traces-endpoint": settings.otlp,
    }
    config_text = (
        f"# Rendered by vllm-deploy for {deployment}. Local changes will be overwritten.\n"
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
        "OTEL_RESOURCE_ATTRIBUTES": f"pinka.deployment={deployment},pinka.model_id={spec['model_id']}",
    }
    env_text = f"# Rendered by vllm-deploy for {deployment}. Local changes will be overwritten.\n"
    env_text += "".join(f"{key}={value}\n" for key, value in env.items())
    return Rendered(config_text, env_text)


def _write(path: Path, text: str) -> bool:
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


def _link(link: Path, target: str) -> bool:
    if link.is_symlink() and str(link.readlink()) == target:
        return False
    temp = link.with_name(f".{link.name}.tmp")
    temp.unlink(missing_ok=True)
    temp.symlink_to(target)
    temp.replace(link)
    return True


def activate(settings: Settings, deployment: str, run: Runner, restart: bool = True) -> bool:
    """Make a deployment current and restart vLLM if anything changed.

    Args:
        settings: Paths.
        deployment: ``<id>@<version>``.
        run: Runs a command and raises on failure.
        restart: Restart pinka-inference.service after a change.

    Returns:
        True if a file or the link changed.

    Raises:
        DeployError: If the deployment does not validate (nothing is changed then).
    """
    rendered = render(settings, deployment)
    changed = _write(settings.etc / "current.yaml", rendered.config)
    changed |= _write(settings.etc / "current.env", rendered.env)
    changed |= _link(settings.etc / "current.yml", f"deployments/{deployment}.yml")
    if changed and restart:
        run(["systemctl", "restart", UNIT])
    return changed


def peer_uid(fd: int = 0) -> int:
    """Return the uid of the process on the other end of a Unix socket.

    Args:
        fd: Socket file descriptor (stdin under ``Accept=yes``).

    Returns:
        The peer's uid.
    """
    with socket.socket(fileno=os.dup(fd)) as sock:
        creds = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    _, uid, _ = struct.unpack("3i", creds)
    return int(uid)


def serve(
    settings: Settings, stdin: TextIO, stdout: TextIO, *, uid: int, allowed_uid: int, run: Runner
) -> int:
    """Answer one request from the socket.

    Args:
        settings: Paths.
        stdin: The connection, readable.
        stdout: The connection, writable.
        uid: The peer's uid.
        allowed_uid: The uid of potka-orch.
        run: Runs a command and raises on failure.

    Returns:
        0 if the switch succeeded, 1 otherwise.
    """
    try:
        if uid != allowed_uid:
            raise DeployError("only potka-orch may switch deployments")
        line = stdin.readline(MAX_REQUEST + 1)
        if len(line) > MAX_REQUEST:
            raise DeployError("request too long")
        try:
            request = json.loads(line)
        except ValueError:
            raise DeployError("request is not one JSON line") from None
        if not isinstance(request, dict) or set(request) != {"deployment"}:
            raise DeployError('request must be {"deployment": "<id>@<version>"}')
        activate(settings, request["deployment"], run)
    except (DeployError, subprocess.CalledProcessError, OSError) as exc:
        stdout.write(json.dumps({"ok": False, "error": str(exc)}) + "\n")
        stdout.flush()
        return 1
    stdout.write(json.dumps({"ok": True}) + "\n")
    stdout.flush()
    return 0


def _run(argv: Sequence[str]) -> None:
    subprocess.run(list(argv), check=True)  # noqa: S603 - fixed argv


def main(argv: Sequence[str]) -> int:
    """Run a subcommand.

    Args:
        argv: Arguments without the program name.

    Returns:
        Exit status.
    """
    parser = argparse.ArgumentParser(prog="vllm-deploy", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve", help="answer one request on stdin/stdout (socket activation)")
    activate_cmd = sub.add_parser("activate", help="switch to a deployment as root")
    activate_cmd.add_argument("deployment")
    activate_cmd.add_argument("--no-restart", action="store_true")
    render_cmd = sub.add_parser("render", help="print what a deployment renders to")
    render_cmd.add_argument("deployment")
    args = parser.parse_args(list(argv))
    settings = Settings()
    if args.command == "serve":
        return serve(
            settings,
            sys.stdin,
            sys.stdout,
            uid=peer_uid(0),
            allowed_uid=pwd.getpwnam(PEER).pw_uid,
            run=_run,
        )
    try:
        if args.command == "render":
            rendered = render(settings, args.deployment)
            sys.stdout.write(rendered.config + "---\n" + rendered.env)
            return 0
        changed = activate(settings, args.deployment, _run, restart=not args.no_restart)
    except (DeployError, subprocess.CalledProcessError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    print(json.dumps({"ok": True, "changed": changed}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
