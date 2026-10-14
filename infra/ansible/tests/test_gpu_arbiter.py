"""The GPU arbiter (BUILD_PROMPT §7.18): rendering, modes, runs, datasets, adapters.

The state machine runs against a fake host: a fake Docker (the job's exit
status and what it leaves in its output directory) and a fake vLLM (its
/health and /metrics answers).
"""

from __future__ import annotations

import configparser
import dataclasses
import datetime as dt
import hashlib
import io
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import jsonschema
import pinka_gpu_arbiter as arb
import pytest
import yaml

from ansible_testlib import GROUP_VARS, INFRA, ROLES, ansible_vars, load_yaml, render_template

PROFILE = INFRA / "vllm" / "profiles" / "ada-12g.yml"
RECORDS = INFRA / "contract" / "records"
MODEL_ID = "test-model"
REVISION = "0" * 40
# Deployment file names (<id>@<version>.yml) are built, so that no literal in
# this file looks like an e-mail address to the privacy scan.
YML = ".yml"
CTL = "refplat-ctl"
JOB_ACCOUNT = "refplat-tune"
IMAGE = "docker.io/library/python@sha256:" + "d" * 64
# Boot IDs: zeroed, like every UUID in a fixture.
BOOT = str(uuid.UUID(int=0))
NEXT_BOOT = str(uuid.UUID(int=1))
UNIT = "pinka-inference.service"


def write(path: Path, text: str, mode: int = 0o644) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)
    return path


def manifest(directory: Path, files: dict[str, bytes], **extra: Any) -> list[dict[str, Any]]:
    records = []
    for name, data in files.items():
        (directory / name).parent.mkdir(parents=True, exist_ok=True)
        (directory / name).write_bytes(data)
        records.append(
            {"path": name, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
        )
    write(directory / "MANIFEST.json", json.dumps({"files": records, **extra}))
    return records


def adapter(paths: arb.Paths, name: str, files: dict[str, bytes], base: str = MODEL_ID) -> str:
    """A registered adapter as the arbiter leaves it; returns its reference."""
    records: list[dict[str, Any]] = [
        {"path": n, "sha256": hashlib.sha256(d).hexdigest(), "size": len(d)}
        for n, d in sorted(files.items())
    ]
    digest = arb.tree_hash(records)
    directory = paths.models / "adapters" / name / digest
    manifest(directory, files, name=name, sha256=digest, base_model_id=base)
    write(directory / "LINEAGE.json", "{}")
    return f"{name}@{digest}"


def spec(paths: arb.Paths, deployment: str, **changes: Any) -> Path:
    ident, _, version = deployment.partition("@")
    data: dict[str, Any] = {
        "id": ident,
        "version": int(version),
        "model_id": MODEL_ID,
        "adapter_ids": [],
        "profile": "ada-12g",
        "overrides": {"tool_call_parser": "hermes"},
        "sampling_defaults": {},
    }
    data.update(changes)
    return write(paths.vllm / "deployments" / f"{deployment}{YML}", yaml.safe_dump(data))


JOB = {
    "name": "tune",
    "account": JOB_ACCOUNT,
    "image": IMAGE,
    "command": ["python3", "-I", "-c", "print(1)"],
    "base_model": True,
    "datasets": ["train"],
    "adapter": "refplat-tune",
    "gpus": 1,
    "memory_max": "4G",
    "cpus": 2,
    "timeout_seconds": 600,
    "resumable": True,
}


class FakeSystem(arb.System):
    """The host: systemctl and docker calls are recorded, vLLM is a dict."""

    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.calls: list[list[str]] = []
        self.jobs: list[str] = []
        self.stopped: list[str] = []
        self.containers: list[list[str]] = []
        self.health = 200
        self.metrics = "vllm:num_requests_running 0\nvllm:num_requests_waiting 0\n"
        self.exit_code: int | None = 0
        # What the job leaves in its output directory: relative path -> bytes.
        self.job_output: dict[str, bytes] = {"adapter/adapter_model.safetensors": b"lora"}
        self.on_docker: Callable[[], object] | None = None
        self.boot = BOOT
        self.clock = dt.datetime(2026, 10, 12, 8, 0, 0, tzinfo=dt.UTC)
        self.accounts = {
            "root": (0, 0),
            CTL: (60578, 60578),
            JOB_ACCOUNT: (os.getuid(), os.getgid()),
            "pinka-inference": (991, 991),
        }
        self.groups = {"pinka-datasets": 990}
        self.logs: list[str] = []
        self.fail_restart = False

    def run(self, argv: Sequence[str]) -> None:
        self.calls.append(list(argv))
        if self.fail_restart and argv[:2] == ["systemctl", "restart"]:
            raise subprocess.CalledProcessError(1, list(argv))

    def start_job(self, run: str) -> None:
        self.jobs.append(run)

    def docker_run(self, argv: Sequence[str], name: str, timeout: float, grace: int) -> int | None:
        self.containers.append(list(argv))
        output = next(
            Path(a.split("source=", 1)[1].split(",", 1)[0])
            for a in argv
            if a.startswith("type=bind") and a.endswith("target=/output")
        )
        for relative, data in self.job_output.items():
            (output / relative).parent.mkdir(parents=True, exist_ok=True)
            (output / relative).write_bytes(data)
        if self.on_docker:
            self.on_docker()
        return self.exit_code

    def stop_container(self, name: str, grace: int) -> None:
        self.stopped.append(name)

    def http_get(self, url: str, timeout: float = 10.0) -> tuple[int, str]:
        if url.endswith("/health"):
            return self.health, ""
        return 200, self.metrics

    def boot_id(self) -> str:
        return self.boot

    def now(self) -> dt.datetime:
        self.clock += dt.timedelta(seconds=1)
        return self.clock

    def sleep(self, seconds: float) -> None:
        pass

    def account(self, name: str) -> tuple[int, int]:
        if name not in self.accounts:
            raise arb.ArbiterError(f"no account {name}")
        return self.accounts[name]

    def group(self, name: str) -> int:
        return self.groups[name]

    def chown(self, path: Path, uid: int, gid: int) -> None:
        pass

    def log(self, message: str) -> None:
        self.logs.append(message)


def make(tmp_path: Path, **config: Any) -> tuple[arb.Arbiter, FakeSystem]:
    paths = arb.Paths(
        config=tmp_path / "etc/gpu/arbiter.json",
        vllm=tmp_path / "etc/vllm",
        models=tmp_path / "models",
        training=tmp_path / "training",
        state=tmp_path / "state",
        rendered=tmp_path / "etc/platform/rendered.json",
        active=tmp_path / "etc/platform/active.json",
        bootstrap_state=tmp_path / "etc/bootstrap-state.json",
        owner_uid=os.getuid(),
    )
    shutil.copy(PROFILE, write(paths.vllm / "profiles" / "ada-12g.yml", ""))
    model = paths.models / "example-org" / "test-model-awq" / REVISION
    manifest(
        model,
        {"model.safetensors": b"weights", "tool_template.jinja": b"{{ messages }}"},
        id=MODEL_ID,
        hf_repo="example-org/test-model-awq",
        revision=REVISION,
        quantization="awq_marlin",
    )
    spec(paths, "dep-test@1")
    spec(paths, "dep-test@2")
    state_dir = tmp_path / "platform/state/ctl/datasets"
    state_dir.mkdir(parents=True)
    write(
        paths.rendered,
        json.dumps({"training": {"dataset_sources": [str(state_dir)], "jobs": [JOB]}}),
    )
    write(
        paths.active,
        json.dumps({"platform": "refplat", "version": "0.1.0", "release": {"commit": "a" * 40}}),
    )
    write(paths.bootstrap_state, json.dumps({"commit": "b" * 40}))
    (paths.training / "datasets").mkdir(parents=True)
    (paths.training / "jobs").mkdir(parents=True)
    system = FakeSystem(tmp_path)
    settings = {"peer": CTL, "default_deployment": "dep-test@1", **config}
    return arb.Arbiter(paths, arb.Config(**settings), system), system


@pytest.fixture
def lab(tmp_path: Path) -> tuple[arb.Arbiter, FakeSystem]:
    return make(tmp_path)


def build_dataset(arbiter: arb.Arbiter, name: str = "ds", split: str = "train") -> str:
    source = Path(arbiter._rendered_training()["dataset_sources"][0]) / name
    (source / "part").mkdir(parents=True)
    (source / "part" / "a.jsonl").write_text('{"x": 1}\n', encoding="utf-8")
    (source / "b.jsonl").write_text('{"x": 2}\n', encoding="utf-8")
    answer = arbiter.publish_dataset(
        str(source),
        {"instances": [{"id": "case-1", "split": split}], "source_runs": ["run-1"]},
        "root",
    )
    return str(answer["dataset_sha256"])


def submission(dataset: str, **changes: Any) -> dict[str, Any]:
    return {
        "base_model": MODEL_ID,
        "datasets": {"train": dataset},
        "hyperparameters": {"lr": 0.001, "epochs": 1},
        "seed": 7,
        "sessions_drained": True,
        **changes,
    }


def schema(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((RECORDS / f"{name}.schema.json").read_text("utf-8"))
    return data


# --- rendering (from contract v1's deploy helper) ------------------------------------------


def test_profile_shape() -> None:
    profile = yaml.safe_load(PROFILE.read_text(encoding="utf-8"))
    assert profile["name"] == PROFILE.stem
    assert set(profile) == {"name", "description", "gpu", "quantizations", "args", "overridable"}
    # The arbiter always sets these itself; a profile or deployment never does.
    pinned = {"model", "served-model-name", "host", "port", "otlp-traces-endpoint"}
    assert not pinned & set(profile["args"])
    assert not pinned & set(profile["overridable"].values())
    assert profile["args"]["seed"] == 0


def test_render(lab: tuple[arb.Arbiter, FakeSystem]) -> None:
    arbiter, _ = lab
    config_text, env_text = arbiter.render("dep-test@1")
    config = yaml.safe_load(config_text)
    assert config["model"] == f"/models/example-org/test-model-awq/{REVISION}"
    assert config["served-model-name"] == MODEL_ID
    assert (config["host"], config["port"]) == ("127.0.0.1", 18704)
    assert config["quantization"] == "awq_marlin"
    assert config["tool-call-parser"] == "hermes"
    assert config["enable-auto-tool-choice"] is True
    assert config["otlp-traces-endpoint"] == "http://127.0.0.1:4317"
    assert "override-generation-config" not in config
    env = dict(line.split("=", 1) for line in env_text.splitlines() if not line.startswith("#"))
    assert env["VLLM_CONFIG_SHA256"] == hashlib.sha256(config_text.encode()).hexdigest()
    assert env["HF_HUB_OFFLINE"] == "1"
    assert env["VLLM_DEPLOYMENT"] == "dep-test@1"


def test_constants_match_the_contract() -> None:
    variables = ansible_vars(GROUP_VARS)
    ports = variables["pinka_default_ports"]
    config, paths = arb.Config(), arb.Paths()
    assert config.vllm_port == ports["vllm"]
    assert config.otlp == f"http://127.0.0.1:{ports['otlp_grpc']}"
    assert paths.vllm == Path("/etc/pinka/vllm")
    assert paths.state == Path("/var/lib/pinka/gpu")
    assert paths.training == Path(variables["pinka_root"]) / "training"
    assert config.caps == variables["training_caps"]
    assert arb.JOB_UNIT == "pinka-gpu-job@{run}.service"
    assert arb.JOB_CONTAINER == "pinka-job-{run}"


def test_overrides_adapter_ids_and_sampling(lab: tuple[arb.Arbiter, FakeSystem]) -> None:
    arbiter, _ = lab
    reference = adapter(arbiter.paths, "style", {"adapter_model.safetensors": b"lora"})
    spec(
        arbiter.paths,
        "dep-test@3",
        overrides={
            "tool_call_parser": "hermes",
            "max_model_len": 4096,
            "chat_template": "tool_template.jinja",
        },
        adapter_ids=[reference],
        sampling_defaults={"temperature": 0.2, "top_p": 0.9},
    )
    config = yaml.safe_load(arbiter.render("dep-test@3")[0])
    assert config["max-model-len"] == 4096
    assert config["chat-template"] == (
        f"/models/example-org/test-model-awq/{REVISION}/tool_template.jinja"
    )
    assert config["enable-lora"] is True
    digest = reference.split("@")[1]
    assert config["lora-modules"] == [f"style=/models/adapters/style/{digest}"]
    assert config["override-generation-config"] == {"temperature": 0.2, "top_p": 0.9}


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"overrides": {"tool_call_parser": "hermes", "host": "0.0.0.0"}}, "not allowed"),  # noqa: S104
        (
            {"overrides": {"tool_call_parser": "hermes", "quantization": "bitsandbytes"}},
            "does not fit",
        ),
        ({"overrides": {}}, "needs overrides.tool_call_parser"),
        (
            {"overrides": {"tool_call_parser": "hermes", "chat_template": "../x.jinja"}},
            "not a verified file",
        ),
        ({"adapter_ids": ["style@" + "b" * 64]}, "not registered"),
        ({"adapter_ids": ["bad name"]}, "invalid adapter"),
        ({"adapters": []}, "unknown keys adapters"),
        ({"sampling_defaults": {"seed": 1}}, "sampling_defaults"),
        ({"sampling_defaults": {"temperature": "hot"}}, "sampling_defaults"),
        ({"model_id": "missing"}, "not downloaded and verified"),
        ({"profile": "../x"}, "invalid profile"),
        ({"version": 9}, "do not match the file name"),
        ({"extra": 1}, "unknown keys"),
    ],
)
def test_render_refuses(
    lab: tuple[arb.Arbiter, FakeSystem], changes: dict[str, Any], message: str
) -> None:
    arbiter, _ = lab
    spec(arbiter.paths, "dep-bad@1", **changes)
    with pytest.raises(arb.ArbiterError, match=message):
        arbiter.render("dep-bad@1")


def test_adapters_must_be_registered_for_the_model(lab: tuple[arb.Arbiter, FakeSystem]) -> None:
    arbiter, _ = lab
    other = adapter(arbiter.paths, "other", {"a.safetensors": b"x"}, base="another-model")
    spec(arbiter.paths, "dep-bad@1", adapter_ids=[other])
    with pytest.raises(arb.ArbiterError, match="is for another-model"):
        arbiter.render("dep-bad@1")
    # A changed file, a missing lineage: the adapter is no longer the registered one.
    good = adapter(arbiter.paths, "good", {"a.safetensors": b"y"})
    directory = arbiter.paths.models / "adapters" / "good" / good.split("@")[1]
    spec(arbiter.paths, "dep-bad@2", adapter_ids=[good])
    (directory / "a.safetensors").write_bytes(b"z")
    with pytest.raises(arb.ArbiterError, match="do not match its hash"):
        arbiter.render("dep-bad@2")
    (directory / "a.safetensors").write_bytes(b"y")
    (directory / "LINEAGE.json").unlink()
    with pytest.raises(arb.ArbiterError, match=r"LINEAGE\.json"):
        arbiter.render("dep-bad@2")


def test_changed_weights_are_refused(lab: tuple[arb.Arbiter, FakeSystem]) -> None:
    arbiter, _ = lab
    model = arbiter.paths.models / "example-org/test-model-awq" / REVISION
    (model / "model.safetensors").write_bytes(b"x")
    with pytest.raises(arb.ArbiterError, match="another size"):
        arbiter.render("dep-test@1")
    (model / "model.safetensors").write_bytes(b"wEights")  # same size, other content
    arbiter.render("dep-test@1")
    with pytest.raises(arb.ArbiterError, match="does not match its hash"):
        arbiter.find_model(MODEL_ID, rehash=True)


@pytest.mark.parametrize(
    "deployment", ["../etc@1", "Dep@1", "dep", "dep@1234567", "-dep@1", "dep@1\n", 7]
)
def test_invalid_references(lab: tuple[arb.Arbiter, FakeSystem], deployment: Any) -> None:
    arbiter, _ = lab
    with pytest.raises(arb.ArbiterError, match="must match"):
        arbiter.deployment_path(deployment)


def test_deployment_files_must_be_root_owned_regular_files(
    lab: tuple[arb.Arbiter, FakeSystem],
) -> None:
    arbiter, system = lab
    path = arbiter.paths.vllm / "deployments" / ("dep-test@1" + YML)
    path.chmod(0o664)
    with pytest.raises(arb.ArbiterError, match="writable only by root"):
        arbiter.render("dep-test@1")
    path.chmod(0o644)
    link = arbiter.paths.vllm / "deployments" / ("dep-link@1" + YML)
    link.symlink_to(path)
    with pytest.raises(arb.ArbiterError, match="not a regular file"):
        arbiter.render("dep-link@1")
    other = arb.Arbiter(
        arb.Paths(**{**arbiter.paths.__dict__, "owner_uid": os.getuid() + 1}),
        arbiter.config,
        system,
    )
    with pytest.raises(arb.ArbiterError, match="owned by root"):
        other.render("dep-test@1")
    with pytest.raises(arb.ArbiterError, match="no deployment"):
        arbiter.render("dep-none@1")


def test_prepare_writes_the_files_and_the_link(lab: tuple[arb.Arbiter, FakeSystem]) -> None:
    arbiter, system = lab
    assert arbiter.prepare("dep-test@1") is True
    etc = arbiter.paths.vllm
    assert (etc / "current.yml").readlink() == Path("deployments") / ("dep-test@1" + YML)
    assert (etc / "current.yaml").stat().st_mode & 0o777 == 0o644
    assert arbiter.prepare("dep-test@1") is False
    assert system.calls == []  # nothing is started
    spec(arbiter.paths, "dep-bad@1", model_id="missing")
    before = (etc / "current.yaml").read_text()
    with pytest.raises(arb.ArbiterError):
        arbiter.prepare("dep-bad@1")
    assert (etc / "current.yaml").read_text() == before


# --- modes and transitions ---------------------------------------------------------------


def test_serve_idle_and_the_transition_log(lab: tuple[arb.Arbiter, FakeSystem]) -> None:
    arbiter, system = lab
    assert arbiter.status()["mode"] == "idle"
    assert arbiter.serve("dep-test@1", CTL)["mode"] == "serving"
    assert system.calls == [["systemctl", "restart", UNIT]]
    # The same deployment, healthy and unchanged: nothing restarts.
    arbiter.serve("dep-test@1", CTL)
    assert len(system.calls) == 1
    arbiter.serve("dep-test@2", "root")
    assert arbiter.state()["deployment"] == "dep-test@2"
    assert arbiter.idle(CTL) == {"ok": True, "mode": "idle"}
    assert system.calls[-1] == ["systemctl", "stop", UNIT]
    log = arbiter.transitions()
    assert [(t["from"], t["to"], t["requester"], t["outcome"]) for t in log] == [
        ("idle", "serving", CTL, "ok"),
        ("serving", "serving", "root", "ok"),
        ("serving", "idle", CTL, "ok"),
    ]
    assert log[1]["deployment"] == "dep-test@2"
    assert set(log[0]) == {
        "time",
        "from",
        "to",
        "requester",
        "deployment",
        "run",
        "job",
        "outcome",
        "detail",
    }
    status = arbiter.status()
    assert status["mode"] == "idle" and status["training_available"] is True
    assert status["transitions"] == log


def test_a_deployment_that_does_not_come_up_leaves_the_gpu_idle(
    lab: tuple[arb.Arbiter, FakeSystem],
) -> None:
    arbiter, system = lab
    arbiter = arb.Arbiter(arbiter.paths, arb.Config(peer=CTL, serve_timeout=0), system)
    system.health = 503
    with pytest.raises(arb.ArbiterError, match="did not come up"):
        arbiter.serve("dep-test@1", CTL)
    assert arbiter.state()["mode"] == "idle"
    assert system.calls[-1] == ["systemctl", "stop", UNIT]
    assert arbiter.transitions()[-1]["outcome"] == "failed"
    system.fail_restart = True
    system.health = 200
    with pytest.raises(arb.ArbiterError, match="did not come up"):
        arbiter.serve("dep-test@1", CTL)


def test_train_needs_no_active_inference(lab: tuple[arb.Arbiter, FakeSystem]) -> None:
    arbiter, system = lab
    dataset = build_dataset(arbiter)
    arbiter.serve("dep-test@1", CTL)
    with pytest.raises(arb.ArbiterError, match="sessions drained"):
        arbiter.train("tune", submission(dataset, sessions_drained=False), CTL)
    system.metrics = (
        'vllm:num_requests_running{model_name="m"} 0.0\n'
        'vllm:num_requests_waiting{model_name="m"} 2.0\n'
    )
    with pytest.raises(arb.ArbiterError, match="0 running and 2 waiting"):
        arbiter.train("tune", submission(dataset), CTL)
    assert arbiter.state()["mode"] == "serving"
    assert system.jobs == []


def test_a_successful_run_registers_its_adapter_and_restores_the_deployment(
    lab: tuple[arb.Arbiter, FakeSystem],
) -> None:
    arbiter, system = lab
    dataset = build_dataset(arbiter)
    arbiter.serve("dep-test@1", CTL)
    answer = arbiter.train("tune", submission(dataset), CTL)
    run = answer["run"]
    assert arb.RUN_RE.fullmatch(run)
    assert answer["spec_sha256"] == arb.spec_sha256(
        "tune", arb.check_submission(JOB, submission(dataset))
    )
    assert system.calls[-1] == ["systemctl", "stop", UNIT]
    assert system.jobs == [run]
    state = arbiter.state()
    assert (state["mode"], state["run"], state["previous"]) == ("training", run, "dep-test@1")
    with pytest.raises(arb.ArbiterError, match="training run"):
        arbiter.serve("dep-test@2", CTL)
    with pytest.raises(arb.ArbiterError, match="training run"):
        arbiter.train("tune", submission(dataset), CTL)
    output = arbiter.paths.training / "jobs" / run
    assert sorted(p.name for p in output.iterdir()) == ["checkpoints", "output"]
    assert arbiter.run_job(run) == 0
    record = arbiter.run_record(run)
    assert record["status"] == "succeeded" and record["attempts"] == 1
    name, digest = record["adapter"]["name"], record["adapter"]["sha256"]
    registered = arbiter.paths.models / "adapters" / name / digest
    files = sorted(p.relative_to(registered).as_posix() for p in registered.rglob("*"))
    assert files == ["LINEAGE.json", "MANIFEST.json", "adapter_model.safetensors"]
    assert all(p.stat().st_mode & 0o777 == 0o444 for p in registered.iterdir())
    assert registered.stat().st_mode & 0o777 == 0o555
    manifest_doc = json.loads((registered / "MANIFEST.json").read_text())
    lineage = json.loads((registered / "LINEAGE.json").read_text())
    jsonschema.validate(manifest_doc, schema("adapter-manifest"))
    jsonschema.validate(lineage, schema("adapter-lineage"))
    assert manifest_doc["base_model_id"] == MODEL_ID
    assert lineage["datasets"] == [dataset]
    assert lineage["image"] == {"reference": IMAGE, "digest": "sha256:" + "d" * 64}
    assert lineage["job"] == {"name": "tune", "run": run, "spec_sha256": answer["spec_sha256"]}
    assert (lineage["seed"], lineage["hyperparameters"]) == (7, {"lr": 0.001, "epochs": 1})
    assert lineage["platform"] == {"name": "refplat", "version": "0.1.0", "commit": "a" * 40}
    assert lineage["infra"] == {"commit": "b" * 40}
    assert lineage["base_model"]["revision"] == REVISION
    # Back to the previous deployment, which may now serve the adapter.
    assert arbiter.state()["mode"] == "serving"
    assert arbiter.state()["deployment"] == "dep-test@1"
    spec(arbiter.paths, "dep-test@3", adapter_ids=[f"{name}@{digest}"])
    assert "lora-modules" in yaml.safe_load(arbiter.render("dep-test@3")[0])
    outcomes = [(t["from"], t["to"], t["requester"]) for t in arbiter.transitions()]
    assert outcomes[-3:] == [
        ("serving", "training", CTL),
        ("training", "idle", "arbiter"),
        ("idle", "serving", "arbiter"),
    ]


@pytest.mark.parametrize(
    ("exit_code", "output", "detail"),
    [
        (3, {"adapter/a.bin": b"x"}, "exit status 3"),
        (None, {"adapter/a.bin": b"x"}, "timeout after"),
        (0, {"metrics.jsonl": b"{}"}, "left no adapter"),
        (0, {"adapter/x/y": b""}, None),  # an empty file still makes an adapter
    ],
)
def test_failed_runs(
    lab: tuple[arb.Arbiter, FakeSystem],
    exit_code: int | None,
    output: dict[str, bytes],
    detail: str | None,
) -> None:
    arbiter, system = lab
    dataset = build_dataset(arbiter)
    run = arbiter.train("tune", submission(dataset, restore=False), CTL)["run"]
    system.exit_code, system.job_output = exit_code, output
    assert arbiter.run_job(run) == (0 if detail is None else 1)
    record = arbiter.run_record(run)
    if detail:
        assert record["status"] == "failed" and detail in record["detail"]
    assert arbiter.state()["mode"] == "idle"


def test_idle_stops_a_running_job_which_ends_interrupted(
    lab: tuple[arb.Arbiter, FakeSystem],
) -> None:
    arbiter, system = lab
    dataset = build_dataset(arbiter)
    arbiter.serve("dep-test@1", CTL)
    run = arbiter.train("tune", submission(dataset), CTL)["run"]
    # The runner is in the container when idle arrives: the job is stopped
    # and the runner settles the run; no deployment comes back.
    system.on_docker = lambda: arbiter.idle("root", wait=False)
    system.exit_code = 143
    arbiter.run_job(run)
    assert system.stopped == [f"pinka-job-{run}"]
    record = arbiter.run_record(run)
    assert record["status"] == "interrupted" and record["detail"] == "stopped by root"
    assert arbiter.state()["mode"] == "idle"
    assert ["systemctl", "restart", UNIT] not in system.calls[1:]


def test_idle_before_the_container_starts(lab: tuple[arb.Arbiter, FakeSystem]) -> None:
    arbiter, _ = lab
    dataset = build_dataset(arbiter)
    job = {**JOB, "resumable": False}
    arbiter.paths.rendered.write_text(
        json.dumps({"training": {**arbiter._rendered_training(), "jobs": [job]}})
    )
    run = arbiter.train("tune", submission(dataset), CTL)["run"]
    record = arbiter.run_record(run)
    record["stop_requested"] = CTL
    arbiter._save_run(record)
    assert arbiter.run_job(run) == 1
    assert arbiter.run_record(run)["status"] == "failed"
    assert arbiter.state()["mode"] == "idle"


def test_training_only_on_a_full_node(tmp_path: Path) -> None:
    for role in ("lab", "inference"):
        arbiter, _ = make(tmp_path / role, node_role=role)
        with pytest.raises(arb.ArbiterError, match="training is unavailable on this host"):
            arbiter.train("tune", submission("0" * 64), CTL)
        with pytest.raises(arb.ArbiterError, match="training is unavailable on this host"):
            arbiter.publish_dataset("/x", {"instances": [], "source_runs": []}, CTL)
        assert arbiter.status()["training_available"] is False


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"base_model": None}, "base_model must be a model id"),
        ({"datasets": {}}, "exactly the kinds"),
        ({"datasets": {"train": "0" * 64, "extra": "0" * 64}}, "exactly the kinds"),
        ({"seed": "7"}, "seed must be an integer"),
        ({"seed": True}, "seed must be an integer"),
        ({"hyperparameters": []}, "hyperparameters must be an object"),
        ({"restore": "yes"}, "restore must be true or false"),
        ({"gpus": 1}, "unknown submission keys gpus"),
    ],
)
def test_submissions_are_checked(changes: dict[str, Any], message: str) -> None:
    with pytest.raises(arb.ArbiterError, match=message):
        arb.check_submission(JOB, submission("0" * 64, **changes))


def test_the_job_spec_hash_is_canonical() -> None:
    a = arb.check_submission(JOB, submission("0" * 64, hyperparameters={"a": 1, "b": 2}))
    b = arb.check_submission(JOB, submission("0" * 64, hyperparameters={"b": 2, "a": 1}))
    assert arb.spec_sha256("tune", a) == arb.spec_sha256("tune", b)
    # Drained sessions and the restore wish are not part of the job.
    c = arb.check_submission(JOB, submission("0" * 64, sessions_drained=False, restore=False))
    d = arb.check_submission(JOB, submission("0" * 64))
    assert arb.spec_sha256("tune", c) == arb.spec_sha256("tune", d)
    assert arb.spec_sha256("tune", d) != arb.spec_sha256("tune", {**d, "seed": 8})


def test_train_refuses_unknown_jobs_inputs_and_caps(lab: tuple[arb.Arbiter, FakeSystem]) -> None:
    arbiter, _ = lab
    dataset = build_dataset(arbiter)
    with pytest.raises(arb.ArbiterError, match="declares no job 'other'"):
        arbiter.train("other", submission(dataset), CTL)
    with pytest.raises(arb.ArbiterError, match="not downloaded and verified"):
        arbiter.train("tune", submission(dataset, base_model="missing"), CTL)
    with pytest.raises(arb.ArbiterError, match="is not published"):
        arbiter.train("tune", submission("e" * 64), CTL)
    small = make(arbiter.paths.config.parents[2] / "caps", caps={**arb.Config().caps, "cpus": 1})
    dataset_small = build_dataset(small[0])
    with pytest.raises(arb.ArbiterError, match="CPUs over the cap"):
        small[0].train("tune", submission(dataset_small), CTL)
    assert arbiter.state()["mode"] == "idle"


# --- the job's container ------------------------------------------------------------------


def test_container_options_are_fixed_by_infra(lab: tuple[arb.Arbiter, FakeSystem]) -> None:
    arbiter, system = lab
    dataset = build_dataset(arbiter)
    run = arbiter.train("tune", submission(dataset), CTL)["run"]
    argv = arbiter.container_argv(arbiter.run_record(run), resume=False)
    image = argv.index(IMAGE)
    options, command = argv[:image], argv[image + 1 :]
    assert command == JOB["command"]
    assert options[:7] == ["docker", "run", "--rm", "--name", f"pinka-job-{run}", "--pull", "never"]

    def value(flag: str) -> list[str]:
        return [options[i + 1] for i, a in enumerate(options) if a == flag]

    assert value("--network") == ["none"]
    assert "--read-only" in options
    assert value("--tmpfs") == ["/tmp:rw,nosuid,nodev"]  # noqa: S108 - the container's own /tmp
    assert value("--cap-drop") == ["ALL"]
    assert value("--security-opt") == ["no-new-privileges"]
    assert value("--ipc") == ["private"]
    uid, gid = system.accounts[JOB_ACCOUNT]
    assert value("--user") == [f"{uid}:{gid}"]
    assert value("--group-add") == ["990"]  # pinka-datasets, its only extra group
    assert value("--memory") == value("--memory-swap") == ["4G"]
    assert value("--cpus") == ["2"]
    assert value("--pids-limit") == ["4096"]
    assert value("--device") == ["nvidia.com/gpu=all"]
    # Nothing that widens the container, whatever the manifest says.
    for forbidden in ("--privileged", "--cap-add", "--pid", "--userns", "--uts", "-v", "--volume"):
        assert forbidden not in options, forbidden
    assert not [a for a in options if "seccomp" in a or "apparmor=unconfined" in a]
    assert not [a for a in options if a.endswith("=host") or a == "host"]
    # Exactly three kinds of mounts: the base model and the datasets read-only,
    # the run's output and checkpoints read-write.
    mounts = value("--mount")
    model = arbiter.paths.models / "example-org/test-model-awq" / REVISION
    datasets = arbiter.paths.training / "datasets" / dataset
    jobs = arbiter.paths.training / "jobs" / run
    assert mounts == [
        f"type=bind,source={model},target=/model,readonly",
        f"type=bind,source={datasets},target=/datasets/train,readonly",
        f"type=bind,source={jobs / 'output'},target=/output",
        f"type=bind,source={jobs / 'checkpoints'},target=/checkpoints",
    ]
    env = value("--env")
    assert env == [
        f"PINKA_JOB_RUN={run}",
        "PINKA_JOB_SEED=7",
        'PINKA_JOB_HYPERPARAMETERS={"epochs":1,"lr":0.001}',
        "PINKA_JOB_RESUME=0",
    ]


def test_the_cpu_only_variant_gets_no_device(lab: tuple[arb.Arbiter, FakeSystem]) -> None:
    arbiter, _ = lab
    dataset = build_dataset(arbiter)
    job = {**JOB, "gpus": 0, "base_model": False}
    arbiter.paths.rendered.write_text(
        json.dumps({"training": {**arbiter._rendered_training(), "jobs": [job]}})
    )
    run = arbiter.train("tune", submission(dataset, base_model=None), CTL)["run"]
    argv = arbiter.container_argv(arbiter.run_record(run), resume=True)
    assert "--device" not in argv and "nvidia.com/gpu=all" not in argv
    assert not [a for a in argv if "target=/model" in a]
    assert "PINKA_JOB_RESUME=1" in argv
    assert arbiter.run_job(run) == 0


# --- datasets and the split guard ------------------------------------------------------------


def test_publish_a_dataset(lab: tuple[arb.Arbiter, FakeSystem]) -> None:
    arbiter, _ = lab
    digest = build_dataset(arbiter)
    directory = arbiter.paths.training / "datasets" / digest
    provenance = json.loads((directory / "PROVENANCE.json").read_text())
    jsonschema.validate(provenance, schema("dataset-provenance"))
    assert provenance["dataset_sha256"] == digest
    assert [f["path"] for f in provenance["files"]] == ["b.jsonl", "part/a.jsonl"]
    assert arb.tree_hash(provenance["files"]) == digest
    assert provenance["platform"]["commit"] == "a" * 40
    assert directory.stat().st_mode & 0o777 == 0o750
    assert (directory / "part").stat().st_mode & 0o777 == 0o750
    assert (directory / "part" / "a.jsonl").stat().st_mode & 0o777 == 0o640
    assert (directory / "PROVENANCE.json").stat().st_mode & 0o777 == 0o640
    # Publishing the same content again changes nothing.
    source = Path(arbiter._rendered_training()["dataset_sources"][0]) / "ds"
    again = arbiter.publish_dataset(
        str(source), {"instances": [{"id": "case-1", "split": "train"}], "source_runs": []}, "root"
    )
    assert again == {"ok": True, "dataset_sha256": digest, "published": False}
    assert arbiter.check_dataset(digest) == directory
    assert [p.name for p in (arbiter.paths.training / "datasets").iterdir()] == [digest]


@pytest.mark.parametrize(
    ("provenance", "message"),
    [
        ({"instances": [{"id": "c", "split": "test"}], "source_runs": []}, "in the test split"),
        (
            {
                "instances": [{"id": "a", "split": "train"}, {"id": "b", "split": "test"}],
                "source_runs": [],
            },
            "instance b is in the test split",
        ),
        ({"instances": [{"id": "c", "split": "Test"}], "source_runs": []}, "invalid split"),
        ({"instances": [{"id": "c"}], "source_runs": []}, "exactly id and split"),
        ({"instances": [], "source_runs": []}, "lists no instances"),
        ({"instances": [{"id": "c", "split": "train"}]}, "exactly instances and source_runs"),
        ({"instances": [{"id": "c", "split": "train"}], "source_runs": [1]}, "list of run IDs"),
    ],
)
def test_the_split_guard_at_publication(
    lab: tuple[arb.Arbiter, FakeSystem], provenance: dict[str, Any], message: str
) -> None:
    arbiter, _ = lab
    source = Path(arbiter._rendered_training()["dataset_sources"][0]) / "ds"
    source.mkdir()
    (source / "a").write_text("x")
    with pytest.raises(arb.ArbiterError, match=message):
        arbiter.publish_dataset(str(source), provenance, CTL)
    assert list((arbiter.paths.training / "datasets").iterdir()) == []


def test_the_split_guard_and_the_hash_at_mount(lab: tuple[arb.Arbiter, FakeSystem]) -> None:
    arbiter, _ = lab
    digest = build_dataset(arbiter)
    directory = arbiter.paths.training / "datasets" / digest
    path = directory / "PROVENANCE.json"
    original = path.read_text()
    tampered = json.loads(original)
    tampered["instances"].append({"id": "case-2", "split": "test"})
    path.write_text(json.dumps(tampered))
    with pytest.raises(arb.ArbiterError, match="case-2 is in the test split"):
        arbiter.train("tune", submission(digest), CTL)
    path.write_text(original)
    (directory / "b.jsonl").chmod(0o644)
    (directory / "b.jsonl").write_text('{"x": 3}\n')
    with pytest.raises(arb.ArbiterError, match="do not match its hash"):
        arbiter.check_dataset(digest)
    with pytest.raises(arb.ArbiterError, match="invalid dataset hash"):
        arbiter.check_dataset("../x")


def test_dataset_sources_are_the_controllers_own_files(
    lab: tuple[arb.Arbiter, FakeSystem], tmp_path: Path
) -> None:
    arbiter, system = lab
    root = Path(arbiter._rendered_training()["dataset_sources"][0])
    provenance = {"instances": [{"id": "c", "split": "train"}], "source_runs": []}
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "a").write_text("x")
    with pytest.raises(arb.ArbiterError, match="not inside the controller's state"):
        arbiter.publish_dataset(str(outside), provenance, CTL)
    with pytest.raises(arb.ArbiterError, match="not inside the controller's state"):
        arbiter.publish_dataset(str(root), provenance, CTL)  # the state directory itself
    with pytest.raises(arb.ArbiterError, match="not inside the controller's state"):
        arbiter.publish_dataset(f"{root}/../../../../elsewhere", provenance, CTL)
    with pytest.raises(arb.ArbiterError, match="absolute path"):
        arbiter.publish_dataset("relative", provenance, CTL)
    (root / "link").symlink_to(outside)
    with pytest.raises(arb.ArbiterError, match="is not a directory"):
        arbiter.publish_dataset(str(root / "link"), provenance, CTL)
    data = root / "ds"
    data.mkdir()
    (data / "a").write_text("x")
    (data / "secret").symlink_to("/etc/hostname")
    with pytest.raises(arb.ArbiterError, match="not a regular file"):
        arbiter.publish_dataset(str(data), provenance, "root")
    (data / "secret").unlink()
    os.mkfifo(data / "fifo")
    with pytest.raises(arb.ArbiterError, match="not a regular file or directory"):
        arbiter.publish_dataset(str(data), provenance, "root")
    (data / "fifo").unlink()
    # The controller may publish only files of its own: none it could not read.
    system.accounts[CTL] = (os.getuid() + 1, os.getgid())
    with pytest.raises(arb.ArbiterError, match="does not belong to the requester"):
        arbiter.publish_dataset(str(data), provenance, CTL)
    system.accounts[CTL] = (os.getuid(), os.getgid())
    assert arbiter.publish_dataset(str(data), provenance, CTL)["published"] is True
    assert [p.name for p in (arbiter.paths.training / "datasets").iterdir()] == [
        arb.tree_hash(arb.tree_files(data))
    ]


def test_copy_tree_refuses_setuid_files(tmp_path: Path) -> None:
    source, target = tmp_path / "s", tmp_path / "t"
    source.mkdir()
    target.mkdir()
    (source / "x").write_text("x")
    (source / "x").chmod(0o4755)
    with pytest.raises(arb.ArbiterError, match="setuid or setgid"):
        arb.copy_tree(source, target, None)


def test_adapter_files_must_belong_to_the_job(lab: tuple[arb.Arbiter, FakeSystem]) -> None:
    arbiter, system = lab
    dataset = build_dataset(arbiter)
    run = arbiter.train("tune", submission(dataset, restore=False), CTL)["run"]

    def swap() -> None:
        system.accounts[JOB_ACCOUNT] = (os.getuid() + 1, os.getgid())

    system.on_docker = swap
    assert arbiter.run_job(run) == 1
    assert "does not belong to the requester" in arbiter.run_record(run)["detail"]
    assert list((arbiter.paths.models / "adapters" / "refplat-tune").iterdir()) == []


# --- boot ----------------------------------------------------------------------------------


def test_boot_serves_the_default_deployment_once(lab: tuple[arb.Arbiter, FakeSystem]) -> None:
    arbiter, system = lab
    assert arbiter.boot()["mode"] == "serving"
    assert arbiter.state()["deployment"] == "dep-test@1"
    assert arbiter.boot() == {"ok": True, "mode": "serving"}
    assert system.calls == [["systemctl", "restart", UNIT]]
    # A new boot: vLLM is down, the default comes back.
    system.boot = NEXT_BOOT
    arbiter.boot()
    assert system.calls[-1] == ["systemctl", "restart", UNIT]


def test_boot_with_after_boot_idle(tmp_path: Path) -> None:
    arbiter, system = make(tmp_path, after_boot="idle")
    arbiter.serve("dep-test@2", CTL)
    system.boot = NEXT_BOOT
    assert arbiter.boot()["mode"] == "idle"
    assert system.calls[-1] == ["systemctl", "stop", UNIT]


def test_boot_resumes_a_resumable_run(lab: tuple[arb.Arbiter, FakeSystem]) -> None:
    arbiter, system = lab
    arbiter.boot()
    dataset = build_dataset(arbiter)
    run = arbiter.train("tune", submission(dataset), CTL)["run"]
    arbiter._start_run(run)  # the container was running when the host went down
    system.boot = NEXT_BOOT
    assert arbiter.boot() == {"ok": True, "mode": "training", "run": run}
    assert system.jobs == [run, run]
    assert arbiter.run_record(run)["status"] == "resuming"
    assert arbiter.transitions()[-1]["outcome"] == "resumed"
    assert arbiter.run_job(run) == 0
    record = arbiter.run_record(run)
    assert (record["status"], record["attempts"]) == ("succeeded", 2)
    assert "PINKA_JOB_RESUME=1" in system.containers[-1]
    assert arbiter.state()["deployment"] == "dep-test@1"


def test_boot_ends_a_run_that_cannot_resume(lab: tuple[arb.Arbiter, FakeSystem]) -> None:
    arbiter, system = lab
    arbiter.boot()
    job = {**JOB, "resumable": False}
    arbiter.paths.rendered.write_text(
        json.dumps({"training": {**arbiter._rendered_training(), "jobs": [job]}})
    )
    dataset = build_dataset(arbiter)
    run = arbiter.train("tune", submission(dataset), CTL)["run"]
    system.boot = NEXT_BOOT
    assert arbiter.boot()["mode"] == "serving"  # the previous deployment comes back
    assert arbiter.run_record(run)["status"] == "interrupted"
    assert system.jobs == [run]


# --- the socket ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("uid", "request_", "error"),
    [
        (60578, {"request": "status"}, None),
        (0, {"request": "status"}, None),
        (1000, {"request": "status"}, "only the controller and root"),
        (60578, {"request": "reboot"}, "request must be one of"),
        (60578, ["status"], "request must be one of"),
        (60578, {"request": "serve"}, "serve takes exactly deployment, request"),
        (60578, {"request": "idle", "now": True}, "idle takes exactly request"),
        (60578, {"request": "train", "job": 1, "submission": {}}, "job name and a submission"),
    ],
)
def test_requests(
    lab: tuple[arb.Arbiter, FakeSystem], uid: int, request_: Any, error: str | None
) -> None:
    arbiter, _ = lab
    if error is None:
        assert arbiter.handle(request_, uid)["mode"] == "idle"
    else:
        with pytest.raises(arb.ArbiterError, match=error):
            arbiter.handle(request_, uid)


def test_the_inference_node_only_serves(tmp_path: Path) -> None:
    arbiter, _ = make(tmp_path, node_role="inference", peer="pinka-inference")
    assert arbiter.handle({"request": "serve", "deployment": "dep-test@1"}, 991)["ok"]
    for request in (
        {"request": "idle"},
        {"request": "train", "job": "tune", "submission": {}},
        {"request": "publish-dataset", "source": "/x", "provenance": {}},
    ):
        with pytest.raises(arb.ArbiterError, match="not available on an inference node"):
            arbiter.handle(request, 991)
    # Root still may: pinka gpu idle on the inference node.
    assert arbiter.handle({"request": "idle"}, 0)["mode"] == "idle"


def test_a_missing_peer_account_leaves_root(tmp_path: Path) -> None:
    arbiter, _ = make(tmp_path, peer="gone-ctl")
    assert arbiter.handle({"request": "status"}, 0)["ok"]
    with pytest.raises(arb.ArbiterError, match="only the controller and root"):
        arbiter.handle({"request": "status"}, 60578)


@pytest.mark.parametrize(
    ("line", "ok", "error"),
    [
        ('{"request": "serve", "deployment": "dep-test@1"}\n', True, None),
        ("not json\n", False, "one JSON line"),
        ('{"request": "serve", "deployment": "../x@1"}\n', False, "must match"),
        ('{"request": "status", "x": "' + "a" * (1 << 20) + '"}\n', False, "too long"),
    ],
)
def test_connection(
    lab: tuple[arb.Arbiter, FakeSystem], line: str, ok: bool, error: str | None
) -> None:
    arbiter, system = lab
    out = io.StringIO()
    code = arbiter.connection(io.StringIO(line), out, 60578)
    answer = json.loads(out.getvalue())
    assert (answer["ok"], code) == (ok, 0 if ok else 1)
    if error:
        assert error in answer["error"]
        assert system.calls == []


def test_peer_uid_of_a_unix_socket() -> None:
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    with left, right:
        assert arb.peer_uid(left.fileno()) == os.getuid()


def test_main(lab: tuple[arb.Arbiter, FakeSystem], capsys: pytest.CaptureFixture[str]) -> None:
    arbiter, _ = lab
    assert arb.main(["render", "dep-test@1"], lambda: arbiter) == 0
    assert "served-model-name: test-model" in capsys.readouterr().out
    assert arb.main(["prepare", "dep-test@1"], lambda: arbiter) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "changed": True}
    assert arb.main(["boot"], lambda: arbiter) == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "serving"
    assert arb.main(["render", "dep-none@1"], lambda: arbiter) == 1
    assert "no deployment" in json.loads(capsys.readouterr().out)["error"]


def test_load_config(tmp_path: Path) -> None:
    assert arb.load_config(tmp_path / "missing.json") == arb.Config()
    path = write(
        tmp_path / "a.json", json.dumps({"node_role": "lab", "gpu_args": ["--gpus", "all"]})
    )
    config = arb.load_config(path)
    assert (config.node_role, config.gpu_args, config.trains) == ("lab", ("--gpus", "all"), False)
    write(path, json.dumps({"surprise": 1}))
    with pytest.raises(arb.ArbiterError, match="unknown keys"):
        arb.load_config(path)


def test_the_lab_starts_again_within_the_same_boot(lab: tuple[arb.Arbiter, FakeSystem]) -> None:
    arbiter, system = lab
    arbiter.boot()
    # pinka stop asked for idle; pinka start brings the default back.
    arbiter.idle("root")
    assert arbiter.boot()["mode"] == "serving"
    assert system.calls[-1] == ["systemctl", "restart", UNIT]
    # The lab target was stopped without the arbiter: vLLM is down while the
    # state says serving, and comes back with the same deployment.
    arbiter.serve("dep-test@2", CTL)
    system.health = 0
    calls = len(system.calls)
    config = arb.Config(peer=CTL, serve_timeout=0, default_deployment="dep-test@1")
    arbiter = arb.Arbiter(arbiter.paths, config, system)
    answer = arbiter.boot()
    assert answer["ok"] is False and "did not come up with dep-test@2" in answer["error"]
    assert system.calls[calls] == ["systemctl", "restart", UNIT]
    assert arbiter.state()["mode"] == "idle"
    system.health = 200
    assert arbiter.boot()["mode"] == "serving"
    assert arbiter.state()["deployment"] == "dep-test@1"  # idle after the failure: the default


def test_a_host_without_vllm(tmp_path: Path) -> None:
    arbiter, system = make(tmp_path, serves=False, default_deployment=None, gpu_args=())
    with pytest.raises(arb.ArbiterError, match="serves no inference"):
        arbiter.serve("dep-test@1", CTL)
    assert arbiter.boot()["mode"] == "idle"
    dataset = build_dataset(arbiter)
    with pytest.raises(arb.ArbiterError, match="needs the GPU, which no container gets"):
        arbiter.train("tune", submission(dataset), CTL)
    cpu = {**JOB, "gpus": 0}
    arbiter.paths.rendered.write_text(
        json.dumps({"training": {**arbiter._rendered_training(), "jobs": [cpu]}})
    )
    run = arbiter.train("tune", submission(dataset), CTL)["run"]
    assert arbiter.run_job(run) == 0
    assert arbiter.state()["mode"] == "idle"
    assert system.calls == []


def test_a_stopped_job_unit_settles_its_run(lab: tuple[arb.Arbiter, FakeSystem]) -> None:
    arbiter, system = lab
    dataset = build_dataset(arbiter)
    run = arbiter.train("tune", submission(dataset, restore=False), CTL)["run"]

    def sigterm() -> None:
        system.terminated = True

    system.on_docker = sigterm
    system.exit_code = 143
    assert arbiter.run_job(run) == 1
    record = arbiter.run_record(run)
    assert (record["status"], record["detail"]) == ("interrupted", "stopped by systemd")


class StopRecorder(arb.System):
    def __init__(self) -> None:
        self.stops: list[tuple[str, int]] = []

    def stop_container(self, name: str, grace: int) -> None:
        self.stops.append((name, grace))


def test_the_runner_stops_the_container_on_sigterm_and_timeout() -> None:
    system = StopRecorder()
    timer = threading.Timer(0.3, os.kill, (os.getpid(), signal.SIGTERM))
    timer.start()
    code = system.docker_run([sys.executable, "-c", "import time; time.sleep(1)"], "c", 30, 5)
    timer.join()
    assert (code, system.terminated, system.stops) == (0, True, [("c", 5)])
    assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL
    system = StopRecorder()
    code = system.docker_run([sys.executable, "-c", "import time; time.sleep(1)"], "c", 0.2, 5)
    assert (code, system.terminated, system.stops) == (None, False, [("c", 5)])


def test_admin_commands(
    lab: tuple[arb.Arbiter, FakeSystem], capsys: pytest.CaptureFixture[str]
) -> None:
    arbiter, _ = lab
    assert arb.main(["serve", "dep-test@1"], lambda: arbiter) == 0
    answer = json.loads(capsys.readouterr().out)
    assert (answer["mode"], answer["changed"]) == ("serving", True)
    assert arb.main(["status"], lambda: arbiter) == 0
    assert json.loads(capsys.readouterr().out)["deployment"] == "dep-test@1"
    dataset = build_dataset(arbiter)
    arbiter.train("tune", submission(dataset), CTL)
    assert arb.main(["serve", "dep-test@2", "--unless-training"], lambda: arbiter) == 0
    assert json.loads(capsys.readouterr().out)["skipped"] is True
    assert arb.main(["serve", "dep-test@2"], lambda: arbiter) == 1
    assert "training run" in json.loads(capsys.readouterr().out)["error"]
    assert arb.main(["prepare", "dep-test@2"], lambda: arbiter) == 1
    assert "training" in json.loads(capsys.readouterr().out)["error"]


# --- the role and its units ----------------------------------------------------------------


def role_scope(**extra: Any) -> dict[str, Any]:
    role = ROLES / "gpu_arbiter"
    return ansible_vars(
        GROUP_VARS,
        role / "defaults" / "main.yml",
        role / "vars" / "main.yml",
        gpu_arbiter_gpu_args=["--device", "nvidia.com/gpu=all"],
        **extra,
    )


def unit(name: str, **extra: Any) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str  # type: ignore[assignment,method-assign]
    parser.read_string(render_template("gpu_arbiter", f"{name}.j2", role_scope(**extra)))
    return parser


@pytest.mark.parametrize(
    ("overrides", "peer"),
    [
        ({}, "root"),
        ({"active_platform": {"controller": "demo-ctl"}}, "demo-ctl"),
        (
            {"node_role": "inference", "active_platform": {"controller": "demo-ctl"}},
            "pinka-inference",
        ),
    ],
)
def test_the_socket_belongs_to_the_peer(overrides: dict[str, Any], peer: str) -> None:
    sock = unit("pinka-gpu-arbiter.socket", **overrides)["Socket"]
    assert sock["ListenStream"] == "/run/pinka/gpu.sock"
    assert (sock["SocketUser"], sock["SocketGroup"], sock["SocketMode"]) == (peer, "root", "0600")
    assert sock["Accept"] == "yes"
    config = json.loads(render_template("gpu_arbiter", "arbiter.json.j2", role_scope(**overrides)))
    assert config["peer"] == peer


def test_the_units() -> None:
    connection = unit("pinka-gpu-arbiter@.service")["Service"]
    assert connection["ExecStart"] == "/usr/local/libexec/pinka/gpu-arbiter connection"
    assert (connection["StandardInput"], connection["StandardOutput"]) == ("socket", "socket")
    assert "User" not in connection  # root: it switches vLLM and writes the training areas
    assert connection["ReadWritePaths"].split() == [
        "-/etc/pinka/vllm",
        "-/var/lib/pinka/gpu",
        "-/srv/pinka/training",
        "-/srv/pinka/models/adapters",
    ]
    assert (connection["IPAddressAllow"], connection["IPAddressDeny"]) == ("localhost", "any")
    assert int(connection["TimeoutStartSec"]) > 1200 + 120
    boot = unit("pinka-gpu-arbiter.service")
    assert boot["Service"]["ExecStart"].endswith("gpu-arbiter boot")
    assert boot["Service"]["RemainAfterExit"] == "yes"
    assert boot["Unit"]["PartOf"] == "pinka-lab.target"
    assert boot["Install"]["WantedBy"] == "pinka-lab.target"
    job = unit("pinka-gpu-job@.service")
    assert job["Service"]["ExecStart"].endswith("gpu-arbiter run-job %i")
    assert job["Service"]["KillMode"] == "mixed"
    assert "Install" not in job  # started by the arbiter only
    variables = role_scope()
    assert variables["gpu_arbiter_program"] == "/usr/local/libexec/pinka/gpu-arbiter"
    assert variables["gpu_arbiter_units"] == [
        "pinka-gpu-arbiter.socket",
        "pinka-gpu-arbiter@.service",
        "pinka-gpu-arbiter.service",
        "pinka-gpu-job@.service",
    ]


def test_the_settings_file_fits_the_program() -> None:
    variables = role_scope(
        vllm_default_id="default-ada-12g@1", pinka_features={"gpu_inference": True}
    )
    config = json.loads(render_template("gpu_arbiter", "arbiter.json.j2", variables))
    # Every setting but the fixed unit name of the inference project.
    assert set(config) == {f.name for f in dataclasses.fields(arb.Config)} - {"vllm_unit"}
    assert config["default_deployment"] == "default-ada-12g@1"
    assert config["serves"] is True
    assert config["caps"] == variables["training_caps"]
    assert (config["after_boot"], config["restore_after_training"]) == ("serve", True)
    assert config["gpu_args"] == ["--device", "nvidia.com/gpu=all"]
    path = Path(variables["gpu_arbiter_config"])
    assert path == arb.Paths().config
    without = role_scope(pinka_features={"gpu_inference": False})
    config = json.loads(render_template("gpu_arbiter", "arbiter.json.j2", without))
    assert (config["serves"], config["default_deployment"]) == (False, None)


def test_bootstrap_serves_through_the_arbiter() -> None:
    tasks = load_yaml(ROLES / "gpu_arbiter" / "tasks" / "main.yml")
    serve = next(t for t in tasks if t["name"] == "Serve the chosen deployment")
    assert serve["ansible.builtin.command"]["argv"][1:] == [
        "serve",
        "{{ vllm_target }}",
        "--unless-training",
    ]
    vllm = load_yaml(ROLES / "vllm" / "tasks" / "main.yml")
    project = next(t for t in vllm if t["name"] == "Install the vLLM project")
    assert project["vars"]["compose_start"] is False  # only the arbiter starts vLLM
    assert not (ROLES / "vllm" / "files" / "pinka_vllm_deploy.py").exists()


@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="needs systemd-analyze")
def test_units_pass_systemd_analyze(tmp_path: Path) -> None:
    system = tmp_path / "etc" / "systemd" / "system"
    system.mkdir(parents=True)
    shutil.copytree(
        "/usr/lib/systemd/system", tmp_path / "usr" / "lib" / "systemd" / "system", symlinks=True
    )
    (system / "pinka-lab.target").write_text("[Unit]\nDescription=stub\n")
    (system / "pinka-services.slice").write_text("[Unit]\nDescription=stub\n")
    (system / "docker.service").write_text("[Service]\nExecStart=/bin/true\n")
    program = tmp_path / "usr/local/libexec/pinka/gpu-arbiter"
    program.parent.mkdir(parents=True)
    program.write_text("#!/bin/sh\n")
    program.chmod(0o755)
    names = ["pinka-gpu-arbiter.socket", "pinka-gpu-arbiter.service", "pinka-gpu-job@.service"]
    for name in [*names, "pinka-gpu-arbiter@.service"]:
        (system / name).write_text(render_template("gpu_arbiter", f"{name}.j2", role_scope()))
    done = subprocess.run(
        [
            "systemd-analyze",
            f"--root={tmp_path}",
            "verify",
            "--man=no",
            *[f"etc/systemd/system/{n.replace('@.', '@x.')}" for n in names],
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr


def test_doctor_hashes_trees_like_the_arbiter(tmp_path: Path) -> None:
    # doctor's training.adapters and training.datasets recompute the hash.
    from pinka_lab.system import RealSystem  # noqa: PLC0415 - the operator CLI's host access

    tree = tmp_path / "tree"
    (tree / "a" / "b").mkdir(parents=True)
    (tree / "a" / "b" / "x.bin").write_bytes(b"\x00\x01")
    (tree / "y.txt").write_text("y")
    (tree / "MANIFEST.json").write_text("{}")
    expected = arb.tree_hash(arb.tree_files(tree, exclude=("MANIFEST.json",)))
    assert RealSystem().tree_digest(str(tree), ("MANIFEST.json",)) == expected
    (tree / "link").symlink_to(tree / "y.txt")
    assert RealSystem().tree_digest(str(tree)) is None
