"""The reference platform without a lab.

The distribution tests deploy refplat on real systems (``tools/test-distros``).
These tests prove what holds without one: the release passes validation, its
job image is the lock's and both jobs run ``jobs/tune.py``; the build's own uv
commands build the controller's and the agent's environments; the toy
training is deterministic and resumes from its checkpoint; the dataset holds
no test case and passes the arbiter's split guard; the controller follows its
runs through a fake arbiter that speaks the arbiter's protocol; the hooks
print one JSON line; the probe's targets are the paths infra renders and
every kind of action is measured; and the PostgreSQL client, the migration
and the controller's writes run against a throwaway server holding infra's
database of the platform.
"""

from __future__ import annotations

import http.server
import json
import os
import pwd
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import uuid
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pinka_gpu_arbiter as arbiter_mod
import pytest
import yaml
from agent_testlib import VERSIONS_LOCK, uv_environment
from platform_testlib import CONTRACT, REFERENCE_PLATFORM, manifest
from postgres_testlib import BINDIRS

from pinka_lockcheck import net
from pinka_platform import agentpkg, database, deploy, layout, names, render, validate
from refplat import admin, broker, cli, common, ctl, pg, training
from refplat_agent import main as agent_main
from refplat_agent import probe

INFRA = REFERENCE_PLATFORM.parents[1]
CASES = INFRA / "tests" / "reference-platform-cases"
TUNE = REFERENCE_PLATFORM / "jobs" / "tune.py"
MANIFEST = manifest("refplat")
DEFAULT_PORTS = yaml.safe_load(
    (INFRA / "ansible" / "group_vars" / "all" / "main.yml").read_text(encoding="utf-8")
)["pinka_default_ports"]
FACTS = render.LabFacts(
    machine_name="pinka-lab", lab_mode="production", node_role="full", ports=DEFAULT_PORTS
)
ENDPOINTS = render.endpoints(MANIFEST, FACTS)
PRINCIPALS = render.principals(
    MANIFEST,
    CONTRACT,
    {
        names.account(MANIFEST["name"], m["name"]): 2001 + i
        for i, m in enumerate([*MANIFEST["services"], *MANIFEST["jobs"]])
    },
)
# The rendered jobs as the arbiter checks a submission against them.
JOBS = {
    job["name"]: {
        "name": job["name"],
        "base_model": job["inputs"]["base_model"],
        "datasets": job["inputs"]["datasets"],
    }
    for job in MANIFEST["jobs"]
}
DATASET_SHA = "a" * 64
SPEC_SHA = "b" * 64
ADAPTER_SHA = "c" * 64
BASE_MODEL = "example-org/base-model"


def one_line(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    """A hook's or the CLI's output: exactly one JSON line."""
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1, lines
    value: dict[str, Any] = json.loads(lines[0])
    return value


# --- the release ----------------------------------------------------------------------


def test_the_release_passes_validation() -> None:
    result = validate.validate_release(REFERENCE_PLATFORM, CONTRACT)
    assert [p.message for p in result.problems] == []


def test_the_job_image_is_the_one_the_lock_pins() -> None:
    lock = yaml.safe_load(VERSIONS_LOCK.read_text(encoding="utf-8"))
    image = lock["containers"]["images"]["refplat_job"]
    assert {job["image"] for job in MANIFEST["jobs"]} == {f"{image['image']}@{image['digest']}"}


def test_both_jobs_run_jobs_tune_py_and_differ_only_in_the_gpu() -> None:
    script = TUNE.read_text(encoding="utf-8")
    jobs = {job["name"]: job for job in MANIFEST["jobs"]}
    assert set(jobs) == {"tune", "tunecpu"}
    for job in jobs.values():
        assert job["command"] == ["python3", "-I", "-c", script]
    assert (jobs["tune"]["resources"]["gpus"], jobs["tune"]["inputs"]["base_model"]) == (1, True)
    assert (jobs["tunecpu"]["resources"]["gpus"], jobs["tunecpu"]["inputs"]["base_model"]) == (
        0,
        False,
    )
    rest = [
        {k: v for k, v in job.items() if k not in ("name", "resources", "inputs")}
        for job in jobs.values()
    ]
    assert rest[0] == rest[1]


def test_the_controller_publishes_from_a_directory_the_arbiter_accepts() -> None:
    sources = {
        path
        for service in PRINCIPALS["services"]
        for path in (service["state"] or {}).values()
        if service["name"] == "ctl"
    }
    state = common.state_dir(MANIFEST["name"])
    assert sources == {f"{state}/records", f"{state}/datasets"}


# --- the build ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def built(uv: str, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The release built with the build's uv commands (the fetch phase uses PyPI)."""
    if not net.reachable("https://pypi.org/simple/uv-build/"):
        pytest.skip(
            "the build's fetch phase needs PyPI (uv-build); allowed to skip only outside CI"
        )
    base = tmp_path_factory.mktemp("refplat-build")
    staged = base / "release"
    shutil.copytree(
        REFERENCE_PLATFORM,
        staged,
        ignore=shutil.ignore_patterns(".venv", ".agent-venvs", "__pycache__"),
    )
    cache = base / "cache"
    plan = {"run": "run-refplat", "run_dir": str(base), "release": {"target": str(staged)}}
    commands = deploy.sandbox_commands(
        plan,
        deploy.Paths(uv_cache=cache),
        deploy.Owners(),
        build_group=True,
        agents=deploy.agent_packages(staged, int(CONTRACT.caps["manifest_bytes"])),
    )
    assert [phase for phase, _ in commands] == [
        "fetch",
        "build",
        "agent-agent",
        "agent-agent-prune",
    ]
    for phase, argv in commands:
        properties = [argv[i + 1] for i, a in enumerate(argv) if a == "--property"]
        env = dict(
            p.removeprefix("Environment=").split("=", 1)
            for p in properties
            if p.startswith("Environment=")
        )
        env.pop("HOME", None)
        env["UV_CACHE_DIR"] = str(cache)
        uv_argv = argv[argv.index("--") + 1 :]
        environment = uv_environment(cache, env)
        if "PrivateNetwork=yes" not in properties:
            environment.pop("UV_OFFLINE")
        done = subprocess.run(
            [uv, *uv_argv[1:-1], sys.executable],
            cwd=staged,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        assert done.returncode == 0, (phase, done.stderr)
    return staged


def test_the_built_release_passes_validation(built: Path) -> None:
    result = validate.validate_release(built, CONTRACT, built=True)
    assert [p.message for p in result.problems] == []
    for name in ("refplat-ctl", "refplat-admin", "refplat-broker", "refplat"):
        assert (built / ".venv" / "bin" / name).is_file(), name


def test_the_agent_environment_holds_only_the_agent_package(built: Path, tmp_path: Path) -> None:
    env = built / agentpkg.env_dir("agent")
    assert agentpkg.installed(env) == ["refplat-agent"]
    assert agentpkg.installed(built / ".venv") == ["refplat", "refplat-agent", "uv-build"]
    python = str(env / "bin" / "python")
    denied = subprocess.run(
        [python, "-I", "-c", "import refplat"], capture_output=True, text=True, check=False
    )
    assert "No module named 'refplat'" in denied.stderr
    done = subprocess.run(
        [
            python,
            "-I",
            "-m",
            "refplat_agent.probe",
            "--endpoints",
            str(tmp_path / "absent.toml"),
            "--principals",
            str(tmp_path / "absent.json"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    report = json.loads(done.stdout)
    assert report["profile"] == "untrusted-agent"
    defaults = (probe.default_endpoints(), probe.default_principals())
    names = [t.name for t in probe.targets(*defaults)]
    assert [o["name"] for o in report["outcomes"]] == names
    assert done.returncode == (1 if report["reached"] else 0)


def test_the_built_hook_prints_one_json_line(built: Path) -> None:
    done = subprocess.run(
        [str(built / ".venv" / "bin" / "refplat-admin"), "reconcile"],
        capture_output=True,
        text=True,
        check=False,
    )
    lines = done.stdout.splitlines()
    assert len(lines) == 1, done
    assert done.returncode == (0 if json.loads(lines[0])["ok"] else 1)


# --- the toy training -----------------------------------------------------------------


def job_root(tmp_path: Path, name: str, cases: str | None = None) -> Path:
    """The mounts of a job (its dataset, checkpoints and output) below one directory."""
    root = tmp_path / name
    (root / "datasets" / "train").mkdir(parents=True)
    text = (CASES / "cases.jsonl").read_text(encoding="utf-8") if cases is None else cases
    (root / "datasets" / "train" / training.DATASET_FILE).write_text(text, encoding="utf-8")
    (root / "checkpoints").mkdir()
    (root / "output").mkdir()
    return root


def run_job(
    root: Path, seed: str, steps: int, *, resume: bool = False
) -> subprocess.CompletedProcess[str]:
    """Run ``jobs/tune.py`` like the arbiter, with its mount points below ``root``."""
    script = TUNE.read_text(encoding="utf-8")
    for path in (
        f"{arbiter_mod.JOB_DATASETS}/train",
        f"{arbiter_mod.JOB_CHECKPOINTS}/state.json",
        f"{arbiter_mod.JOB_OUTPUT}/{arbiter_mod.JOB_ADAPTER}",
    ):
        assert script.count(f'"{path}"') == 1, path
        script = script.replace(f'"{path}"', json.dumps(f"{root}{path}"))
    env = {
        "PINKA_JOB_SEED": seed,
        "PINKA_JOB_HYPERPARAMETERS": json.dumps({"steps": steps, "step_seconds": 0}),
        "PINKA_JOB_RESUME": "1" if resume else "0",
    }
    return subprocess.run(
        [sys.executable, "-I", "-c", script],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )


def adapter(root: Path) -> tuple[bytes, dict[str, Any]]:
    out = root / "output" / arbiter_mod.JOB_ADAPTER
    config = json.loads((out / "adapter_config.json").read_text(encoding="utf-8"))
    return (out / "adapter_model.bin").read_bytes(), config


def test_the_toy_training_is_deterministic(tmp_path: Path) -> None:
    first, second = job_root(tmp_path, "a"), job_root(tmp_path, "b")
    run_job(first, "1", 8)
    run_job(second, "1", 8)
    assert adapter(first) == adapter(second)
    weights, config = adapter(first)
    assert len(weights) == 32
    assert config == {"seed": "1", "steps": 8, "toy": True}
    other_seed = job_root(tmp_path, "c")
    run_job(other_seed, "2", 8)
    assert adapter(other_seed)[0] != weights
    other_data = job_root(tmp_path, "d", cases='{"id": "x", "split": "train"}\n')
    run_job(other_data, "1", 8)
    assert adapter(other_data)[0] != weights


def test_an_interrupted_run_resumes_from_its_checkpoint(tmp_path: Path) -> None:
    whole = job_root(tmp_path, "whole")
    run_job(whole, "7", 8)
    interrupted = job_root(tmp_path, "interrupted")
    # Three steps done, then the run stopped: its checkpoint says so.
    assert run_job(interrupted, "7", 3).stdout.splitlines()[-1] == "step 3/3"
    shutil.rmtree(interrupted / "output")
    (interrupted / "output").mkdir()
    checkpoint = (interrupted / "checkpoints" / "state.json").read_text(encoding="utf-8")
    assert json.loads(checkpoint)["step"] == 3
    resumed = run_job(interrupted, "7", 8, resume=True)
    assert resumed.stdout.splitlines() == [f"step {n}/8" for n in range(4, 9)]
    assert adapter(interrupted) == adapter(whole)
    # Without PINKA_JOB_RESUME a checkpoint is ignored: the run starts over.
    again = job_root(tmp_path, "again")
    (again / "checkpoints" / "state.json").write_text(checkpoint, encoding="utf-8")
    assert run_job(again, "7", 8).stdout.splitlines()[0] == "step 1/8"
    assert adapter(again) == adapter(whole)


# --- the dataset --------------------------------------------------------------------


def test_the_dataset_holds_only_training_cases(tmp_path: Path) -> None:
    cases = training.read_cases(CASES)
    assert {c["split"] for c in cases} == {"train", "test"}
    directory, provenance = training.build_dataset(cases, "first", tmp_path)
    assert directory == tmp_path / "datasets" / "first"
    written = [
        json.loads(line)
        for line in (directory / training.DATASET_FILE).read_text(encoding="utf-8").splitlines()
    ]
    tests = {c["id"] for c in cases if c["split"] == "test"}
    assert written
    assert {c["split"] for c in written} == {"train"}
    assert not tests & {c["id"] for c in written}
    assert sorted(i["id"] for i in provenance["instances"]) == sorted(c["id"] for c in written)
    # The arbiter's split guard accepts it, and refuses it with a test case.
    assert arbiter_mod.check_instances(provenance["instances"]) == provenance["instances"]
    leaked = [*provenance["instances"], {"id": sorted(tests)[0], "split": "test"}]
    with pytest.raises(arbiter_mod.ArbiterError, match="test split"):
        arbiter_mod.check_instances(leaked)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('{"id": "a"}\n', "cases.jsonl:1: a case needs an id and a split"),
        ('\n{"id": "a", "split": "test"}\n', "no train-split case"),
    ],
)
def test_a_malformed_or_test_only_package_is_refused(
    tmp_path: Path, text: str, message: str
) -> None:
    (tmp_path / "cases.jsonl").write_text(text, encoding="utf-8")
    with pytest.raises(common.RefplatError, match=message):
        training.build_dataset(training.read_cases(tmp_path), "x", tmp_path)


# --- the controller, through a fake arbiter ----------------------------------------------


@pytest.fixture
def sockets() -> Iterator[Path]:
    """A short directory for Unix sockets (their paths are limited to 107 bytes)."""
    directory = Path(tempfile.mkdtemp(prefix="refplat-"))
    try:
        yield directory
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def serve(path: Path, handle: Callable[[Mapping[str, Any]], dict[str, Any]]) -> None:
    """Answer on a socket with refplat's own server loop, in a thread."""
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    listener.listen()
    threading.Thread(target=ctl.serve_socket, args=(listener, handle), daemon=True).start()


class FakeArbiter:
    """The GPU arbiter's requests and answers as refplat uses them (one run at a time)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.requests: list[dict[str, Any]] = []
        self.mode = "idle"
        self.run: str | None = None
        self.job_status: str | None = None
        self.transitions: list[dict[str, Any]] = []
        serve(path, self.answer)

    def answer(self, request: Mapping[str, Any]) -> dict[str, Any]:
        self.requests.append(dict(request))
        try:
            return self._answer(request)
        except arbiter_mod.ArbiterError as exc:
            return {"ok": False, "error": str(exc)}

    def _answer(self, request: Mapping[str, Any]) -> dict[str, Any]:
        kind = request.get("request")
        if kind == "publish-dataset":
            arbiter_mod.check_instances(request["provenance"]["instances"])
            return {"ok": True, "dataset_sha256": DATASET_SHA}
        if kind == "train":
            arbiter_mod.check_submission(JOBS[request["job"]], request["submission"])
            if self.mode == "training":
                return {"ok": False, "error": "a run is training"}
            self.mode, self.run, self.job_status = "training", f"run-{uuid.uuid4().hex}", "running"
            return {"ok": True, "mode": "training", "run": self.run, "spec_sha256": SPEC_SHA}
        if kind == "status":
            answer: dict[str, Any] = {
                "ok": True,
                "mode": self.mode,
                "run": self.run,
                "transitions": list(self.transitions),
            }
            if self.run:
                answer["job"] = {"job": "tunecpu", "status": self.job_status}
            return answer
        return {"ok": False, "error": f"unknown request {kind}"}

    def suspend(self) -> None:
        self.job_status = "suspended"
        self.transitions.append(
            {"from": "training", "to": "training", "run": self.run, "outcome": "suspended"}
        )

    def resume(self) -> None:
        self.job_status = "resuming"
        self.transitions.append(
            {"from": "training", "to": "training", "run": self.run, "outcome": "resumed"}
        )

    def end(self, outcome: str) -> str:
        """The current run leaves training (``ok``, ``failed`` or ``interrupted``)."""
        run = str(self.run)
        self.transitions.append({"from": "training", "to": "idle", "run": run, "outcome": outcome})
        self.mode, self.run, self.job_status = "idle", None, None
        return run


def register(adapters: Path, run: str, sha: str = ADAPTER_SHA) -> None:
    """The adapter the arbiter registers for a run, with the lineage's keys refplat reads."""
    directory = adapters / training.ADAPTER / sha
    directory.mkdir(parents=True)
    lineage = {
        "adapter": {"name": training.ADAPTER, "sha256": sha},
        "job": {"name": "tunecpu", "run": run, "spec_sha256": SPEC_SHA},
    }
    (directory / arbiter_mod.LINEAGE).write_text(json.dumps(lineage), encoding="utf-8")


def controller_for(
    tmp_path: Path,
    arbiter: FakeArbiter,
    settings: Mapping[str, Any] | None = None,
    db: ctl.Database | None = None,
) -> ctl.Controller:
    return ctl.Controller(
        config={"gpu": {"arbiter_socket": str(arbiter.path)}},
        settings_=settings or {},
        database=db,
        state=tmp_path / "state",
        cases=CASES,
        adapters=tmp_path / "adapters",
    )


def records(tmp_path: Path, name: str) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(
        (tmp_path / "state" / "records" / name).read_text(encoding="utf-8")
    )
    return value


def test_the_lineage_keys_refplat_reads_are_in_the_record_schema() -> None:
    schema = json.loads(
        (INFRA / "contract" / "records" / "adapter-lineage.schema.json").read_text(encoding="utf-8")
    )
    assert {"adapter", "job"} <= set(schema["required"])
    assert "sha256" in schema["properties"]["adapter"]["required"]
    assert "run" in schema["properties"]["job"]["required"]


def test_dataset_publishes_the_training_cases(tmp_path: Path, sockets: Path) -> None:
    arbiter = FakeArbiter(sockets / "gpu.sock")
    controller = controller_for(tmp_path, arbiter)
    trains = [c for c in training.read_cases(CASES) if c["split"] == "train"]
    assert controller.dataset() == {"ok": True, "dataset": DATASET_SHA, "instances": len(trains)}
    publish = arbiter.requests[-1]
    assert publish["request"] == "publish-dataset"
    assert Path(publish["source"]).parent == tmp_path / "state" / "datasets"
    assert {i["split"] for i in publish["provenance"]["instances"]} == {"train"}
    assert records(tmp_path, "datasets.json")["latest"] == DATASET_SHA


def test_train_submits_tunecpu_by_default_and_tune_with_the_base_model(
    tmp_path: Path, sockets: Path
) -> None:
    arbiter = FakeArbiter(sockets / "gpu.sock")
    controller = controller_for(tmp_path, arbiter, {"base_model": BASE_MODEL, "steps": 4})
    with pytest.raises(common.RefplatError, match="no dataset yet"):
        controller.train({})
    controller.dataset()
    first = controller.train({"seed": 2})
    assert first == {"ok": True, "run": arbiter.run, "spec_sha256": SPEC_SHA}
    request = arbiter.requests[-1]
    assert request["job"] == "tunecpu"
    assert request["submission"] == {
        "base_model": None,
        "datasets": {"train": DATASET_SHA},
        "hyperparameters": {"steps": 4, "step_seconds": 0.0},
        "seed": 2,
        "sessions_drained": True,
    }
    assert records(tmp_path, "runs.json") == {
        first["run"]: {"job": "tunecpu", "seed": 2, "dataset": DATASET_SHA, "status": "submitted"}
    }
    arbiter.end("ok")
    without = controller_for(tmp_path, arbiter)
    with pytest.raises(common.RefplatError, match="base_model must be a model id"):
        without.train({"job": "tune"})
    controller.train({"job": "tune", "steps": 2, "step_seconds": 0.5})
    submission = arbiter.requests[-1]["submission"]
    assert submission["base_model"] == BASE_MODEL
    assert submission["hyperparameters"] == {"steps": 2, "step_seconds": 0.5}
    # A refusal of the arbiter is the controller's error.
    with pytest.raises(common.RefplatError, match="refused train: a run is training"):
        controller.train({})


def test_runs_follow_the_arbiter_and_import_the_lineage(tmp_path: Path, sockets: Path) -> None:
    arbiter = FakeArbiter(sockets / "gpu.sock")
    controller = controller_for(tmp_path, arbiter)
    controller.dataset()
    run = controller.train({})["run"]

    def status() -> str:
        return str(controller.runs()["runs"][run]["status"])

    assert status() == "running"
    arbiter.suspend()
    assert status() == "suspended"
    arbiter.resume()
    assert status() == "running"
    with pytest.raises(common.RefplatError, match="registered no adapter"):
        controller.lineage(run)
    register(tmp_path / "adapters", arbiter.end("ok"))
    assert status() == "succeeded"
    assert controller.runs()["runs"][run]["adapter"] == ADAPTER_SHA
    assert controller.lineage(run)["lineage"]["adapter"]["sha256"] == ADAPTER_SHA
    failed = controller.train({})["run"]
    arbiter.end("failed")
    assert controller.runs()["runs"][failed]["status"] == "failed"
    # A run whose end scrolled out of the transitions shown has ended.
    lost = controller.train({})["run"]
    arbiter.end("interrupted")
    arbiter.transitions.clear()
    assert controller.runs()["runs"][lost]["status"] == "ended"
    saved = records(tmp_path, "runs.json")
    assert [saved[r]["status"] for r in (run, failed, lost)] == ["succeeded", "failed", "ended"]


def test_drain_refuses_new_runs_and_waits_for_the_active_one(tmp_path: Path, sockets: Path) -> None:
    arbiter = FakeArbiter(sockets / "gpu.sock")
    controller = controller_for(tmp_path, arbiter)
    controller.dataset()
    run = controller.train({})["run"]
    assert controller.drain() == {"ok": True, "drained": False, "active": [run]}
    with pytest.raises(common.RefplatError, match="draining"):
        controller.train({})
    # A suspended run waits for the lab, not for the drain.
    arbiter.suspend()
    assert controller.drain() == {"ok": True, "drained": True, "active": []}
    arbiter.resume()
    assert controller.drain()["drained"] is False
    arbiter.end("failed")
    assert controller.drain() == {"ok": True, "drained": True, "active": []}


def test_the_session_takes_hello_and_report_only(tmp_path: Path, sockets: Path) -> None:
    arbiter = FakeArbiter(sockets / "gpu.sock")
    renamed = ctl.Controller(
        config={"platform": "refplatb"}, settings_={}, database=None, state=tmp_path, cases=CASES
    )
    assert renamed.handle_session({"command": "hello"}) == {"ok": True, "platform": "refplatb"}
    controller = controller_for(tmp_path, arbiter)
    assert controller.handle_session({"command": "hello"}) == {"ok": True, "platform": "refplat"}
    assert controller.status()["agent_report"] is False
    report = {"profile": "untrusted-agent", "reached": [], "outcomes": []}
    assert controller.handle_session({"command": "report", "report": report}) == {
        "ok": True,
        "reached": 0,
    }
    assert controller.status()["agent_report"] is True
    saved = controller.handle_api({"command": "probe-report"})["report"]
    assert {k: saved[k] for k in report} == report
    for request in ({"command": "status"}, {"command": "dataset"}, {"command": "report"}):
        with pytest.raises(common.RefplatError, match="hello and report only"):
            controller.handle_session(request)


def test_the_sockets_answer_one_json_line(tmp_path: Path, sockets: Path) -> None:
    controller = controller_for(tmp_path, FakeArbiter(sockets / "gpu.sock"))
    api = sockets / "api.sock"
    serve(api, controller.handle_api)
    assert common.request(api, {"command": "status"}) == {
        "ok": True,
        "database": "not configured",
        "draining": False,
        "runs": 0,
        "agent_report": False,
    }
    assert common.request(api, {"command": "nope"}) == {
        "ok": False,
        "error": "unknown command 'nope'",
    }
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.connect(str(api))
        conn.sendall(b"not json\n")
        conn.shutdown(socket.SHUT_WR)
        assert common.read_line(conn) == {"ok": False, "error": "not one JSON line"}
    with pytest.raises(common.RefplatError, match=r"absent\.sock"):
        common.request(sockets / "absent.sock", {"command": "status"})


ACTIVATED = """
import os, sys
from refplat import ctl
api, session = os.dup(int(sys.argv[1])), os.dup(int(sys.argv[2]))
os.dup2(api, 3)
os.dup2(session, 4)
os.environ.update(LISTEN_PID=str(os.getpid()), LISTEN_FDS="2", LISTEN_FDNAMES="api:session")
ctl.endpoints = lambda: {"gpu": {"arbiter_socket": sys.argv[3]}}
ctl.settings = dict
sys.exit(ctl.main())
"""


def test_the_controller_runs_on_the_sockets_systemd_hands_over(sockets: Path) -> None:
    listeners = {}
    for name in ("api", "session"):
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(sockets / f"{name}.sock"))
        listener.listen()
        listeners[name] = listener
    env = {"PYTHONPATH": str(REFERENCE_PLATFORM / "src"), "PATH": os.environ["PATH"]}
    argv = [
        sys.executable,
        "-c",
        ACTIVATED,
        str(listeners["api"].fileno()),
        str(listeners["session"].fileno()),
        str(sockets / "gpu.sock"),
    ]
    process = subprocess.Popen(
        argv,
        env=env,
        cwd=sockets,
        pass_fds=[s.fileno() for s in listeners.values()],
        stderr=subprocess.PIPE,
    )
    try:
        for listener in listeners.values():
            listener.close()
        assert common.request(sockets / "api.sock", {"command": "status"})["ok"] is True
        assert common.request(sockets / "session.sock", {"command": "hello"}) == {
            "ok": True,
            "platform": "refplat",
        }
        refused = common.request(sockets / "session.sock", {"command": "status"})
        assert refused["ok"] is False
    finally:
        process.terminate()
        _, stderr = process.communicate(timeout=10)
    assert stderr == b""


def test_without_activated_sockets_the_controller_stops() -> None:
    assert common.listen_sockets({"LISTEN_PID": "1", "LISTEN_FDS": "2"}) == {}
    assert common.listen_sockets({}) == {}


# --- the hooks and the CLI ----------------------------------------------------------


def test_reconcile_reports_a_missing_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(admin, "endpoints", lambda: {"platform": "renamed"})
    monkeypatch.setattr(admin, "state_dir", {"renamed": tmp_path}.__getitem__)
    assert admin.main(["reconcile"]) == 1
    assert one_line(capsys) == {"ok": False, "missing": ["records", "datasets"]}
    for name in ("records", "datasets"):
        (tmp_path / name).mkdir()
    assert admin.main(["reconcile"]) == 0
    assert one_line(capsys) == {"ok": True, "missing": []}


def test_health_and_drain_ask_the_controller(
    tmp_path: Path,
    sockets: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    arbiter = FakeArbiter(sockets / "gpu.sock")
    controller = controller_for(tmp_path, arbiter)
    serve(sockets / "api.sock", controller.handle_api)
    monkeypatch.setattr(admin, "API_SOCKET", sockets / "api.sock")
    assert admin.main(["health"]) == 0
    assert one_line(capsys) == {"ok": True, "database": "not configured"}
    controller.dataset()
    run = controller.train({})["run"]
    assert admin.main(["drain", "--timeout", "0"]) == 1
    assert one_line(capsys) == {"ok": False, "drained": False, "active": [run]}
    arbiter.end("failed")
    assert admin.main(["drain", "--timeout", "0"]) == 0
    assert one_line(capsys) == {"ok": True, "drained": True}
    monkeypatch.setattr(admin, "API_SOCKET", sockets / "absent.sock")
    assert admin.main(["health"]) == 1
    failure = one_line(capsys)
    assert failure["ok"] is False
    assert "absent.sock" in failure["error"]


def test_the_cli_prints_the_controller_s_answer(
    tmp_path: Path,
    sockets: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    arbiter = FakeArbiter(sockets / "gpu.sock")
    controller = controller_for(tmp_path, arbiter, {"base_model": BASE_MODEL})
    serve(sockets / "api.sock", controller.handle_api)
    monkeypatch.setattr(cli, "API_SOCKET", sockets / "api.sock")
    assert cli.main(["status"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert cli.main(["train"]) == 1
    assert one_line(capsys) == {
        "ok": False,
        "error": "no dataset yet: run refplat dataset first",
    }
    assert cli.main(["dataset"]) == 0
    capsys.readouterr()
    argv = ["train", "--job", "tune", "--seed", "3", "--steps", "2", "--step-seconds", "0.5"]
    assert cli.main(argv) == 0
    assert json.loads(capsys.readouterr().out)["run"] == arbiter.run
    request = arbiter.requests[-1]
    assert (request["job"], request["submission"]["seed"]) == ("tune", 3)
    assert request["submission"]["hyperparameters"] == {"steps": 2, "step_seconds": 0.5}


# --- the inference broker -------------------------------------------------------------


class InferenceAPI:
    """An HTTP server on loopback that answers ``/v1/models`` like vLLM's OpenAI API."""

    def __init__(self, body: bytes) -> None:
        self.body = body
        self.paths: list[str] = []
        api = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                api.paths.append(self.path)
                self.send_response(200)
                self.send_header("Content-Length", str(len(api.body)))
                self.end_headers()
                self.wfile.write(api.body)

            def log_message(self, *args: object) -> None:
                del args

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


MODELS = json.dumps(
    {"object": "list", "data": [{"id": "model-b", "object": "model"}, {"id": "model-a"}]}
).encode()


def test_the_broker_asks_the_inference_api_of_endpoints_toml(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A proxy in the environment is never used: the API is on loopback.
    monkeypatch.setenv("http_proxy", "http://192.0.2.1:9")
    api = InferenceAPI(MODELS)
    try:
        answer = broker.Broker({"inference": {"api_url": api.url + "/"}}).handle(
            {"command": "models"}
        )
    finally:
        api.close()
    assert answer == {"ok": True, "api_url": api.url, "models": ["model-a", "model-b"]}
    assert api.paths == ["/v1/models"]
    assert ENDPOINTS["inference"]["api_url"] == f"http://127.0.0.1:{DEFAULT_PORTS['vllm']}"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"not json", "not JSON"),
        (b'{"object": "list"}', "no model list"),
        (b"x" * (broker.MAX_ANSWER + 1), "too long"),
    ],
)
def test_the_broker_refuses_an_answer_that_is_no_model_list(body: bytes, message: str) -> None:
    api = InferenceAPI(body)
    try:
        with pytest.raises(common.RefplatError, match=message):
            broker.Broker({"inference": {"api_url": api.url}}).models()
    finally:
        api.close()


def test_the_broker_without_an_inference_api(sockets: Path) -> None:
    with pytest.raises(common.RefplatError, match="names no inference API"):
        broker.Broker({}).models()
    with pytest.raises(common.RefplatError, match="names no inference API"):
        broker.Broker({"inference": {"api_url": "file:///etc/passwd"}}).models()
    with socket.socket() as unused:
        unused.bind(("127.0.0.1", 0))
        port = unused.getsockname()[1]
    with pytest.raises(common.RefplatError, match="does not answer"):
        broker.Broker({"inference": {"api_url": f"http://127.0.0.1:{port}"}}).models()
    with pytest.raises(common.RefplatError, match="unknown command"):
        broker.Broker({}).handle({"command": "status"})
    assert common.listen_sockets({}) == {}
    assert broker.main() == 1  # no socket handed over


def test_refplat_infer_asks_the_broker(
    sockets: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    api = InferenceAPI(MODELS)
    try:
        serve(sockets / "infer.sock", broker.Broker({"inference": {"api_url": api.url}}).handle)
        monkeypatch.setattr(cli, "BROKER_SOCKET", sockets / "infer.sock")
        assert cli.main(["infer"]) == 0
        assert json.loads(capsys.readouterr().out)["models"] == ["model-a", "model-b"]
    finally:
        api.close()
    assert cli.main(["infer"]) == 1  # the API is gone: the broker says so
    assert "does not answer" in one_line(capsys)["error"]


# --- the isolation probe ----------------------------------------------------------------


def test_the_probe_knows_infra_s_paths_and_the_rendered_ones() -> None:
    assert probe.RESTRICTED == layout.RESTRICTED_DIR
    assert probe.INFRA_SECRETS == layout.SECRETS_DIR
    assert probe.RUN_NETWORK_SOCKET == layout.RUN_NETWORK_SOCKET
    assert probe.GIT_MIRRORS in deploy.BUILD_HIDDEN
    assert str(arbiter_mod.Paths().training / "datasets") == probe.DATASETS
    assert (probe.ENDPOINTS, probe.PRINCIPALS) == (common.ENDPOINTS, common.PRINCIPALS)
    # The defaults the agent uses without infra's files are what infra renders.
    defaults = probe.default_endpoints(MANIFEST["name"])
    assert defaults["platform"] == ENDPOINTS["platform"]
    for section in ("postgres", "telemetry", "gpu", "ports"):
        for key, value in defaults[section].items():
            assert ENDPOINTS[section][key] == value, (section, key)
    sockets = {s["name"]: s for s in PRINCIPALS["sockets"]}
    assert sockets["session"]["agent_dedicated"] is True
    principals = probe.default_principals(MANIFEST["name"])
    assert principals["sockets"] == [{k: sockets["api"][k] for k in ("service", "name", "path")}]
    assert [
        {"name": s["name"], "state": s["state"]} for s in PRINCIPALS["services"] if s["state"]
    ] == principals["services"]
    # A copy deployed under another name finds its own paths.
    for name in ("refplat", "refplatb"):
        assert probe.default_principals(name)["services"][0]["state"] == {
            d: f"{common.state_dir(name)}/{d}" for d in ("records", "datasets")
        }
        assert str(common.cases_dir(name)).startswith(f"{probe.RESTRICTED}/{name}/content/")
    assert str(common.API_SOCKET) == sockets["api"]["path"]
    assert agent_main.SESSION == sockets["session"]["path"] == str(common.SESSION_SOCKET)
    assert str(common.BROKER_SOCKET) == sockets["infer"]["path"]


def test_the_probe_tries_every_target_infra_renders() -> None:
    names = [t.name for t in probe.targets(ENDPOINTS, PRINCIPALS)]
    assert names == [
        "restricted area",
        "restricted content",
        "database socket",
        "database reader socket",
        "infra secrets",
        "git mirrors",
        "datasets area",
        "gpu arbiter socket",
        "run-network helper socket",
        "query proxy",
        "prometheus direct",
        "loki direct",
        "tempo direct",
        "inference api",
        "outside tcp",
        "socket ctl/api",
        "socket ctl/session",
        "socket broker/infer",
        "write state ctl/datasets",
        "write state ctl/records",
        "write own environment",
    ]
    state = common.state_dir(MANIFEST["name"])
    own = [f"{state}/records", f"{state}/datasets"]
    kept = {(t.name, t.kind) for t in probe.targets(ENDPOINTS, PRINCIPALS)}
    probed = {
        (t.name, t.kind)
        for t in probe.targets(ENDPOINTS, PRINCIPALS)
        if not (t.kind == "write" and t.address in own)
    }
    assert kept - probed == {
        ("write state ctl/datasets", "write"),
        ("write state ctl/records", "write"),
    }


def answer_once(path: Path, reply: bytes) -> None:
    """An HTTP server on a Unix socket that answers one request."""
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    listener.listen()

    def run() -> None:
        with listener:
            conn, _ = listener.accept()
            with conn:
                conn.recv(4096)
                conn.sendall(reply)

    threading.Thread(target=run, daemon=True).start()


def test_every_kind_of_action_is_measured(tmp_path: Path, sockets: Path) -> None:
    with (
        socket.create_server(("127.0.0.1", 0)) as server,
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as bound,
        socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as unix,
    ):
        bound.bind(("127.0.0.1", 0))  # bound, not listening: refused
        unix.bind(str(sockets / "u.sock"))
        unix.listen()
        answer_once(sockets / "forbidden.sock", b"HTTP/1.1 403 Forbidden\r\n\r\n")
        answer_once(sockets / "open.sock", b"HTTP/1.1 200 OK\r\n\r\n")
        readable = tmp_path / "file"
        readable.write_bytes(b"x")
        cases = [
            ("tcp", f"127.0.0.1:{server.getsockname()[1]}", True),
            ("tcp", f"127.0.0.1:{bound.getsockname()[1]}", False),
            ("unix", str(sockets / "u.sock"), True),
            ("unix", str(sockets / "absent.sock"), False),
            ("http-unix", str(sockets / "forbidden.sock"), False),
            ("http-unix", str(sockets / "open.sock"), True),
            ("list", str(tmp_path), True),
            ("list", str(tmp_path / "absent"), False),
            ("read", str(readable), True),
            ("read", str(tmp_path / "absent"), False),
            ("write", str(tmp_path), True),
            ("write", str(tmp_path / "absent"), False),
            ("unix", "", False),
            ("unix", "/.s.PGSQL.5432", False),
        ]
        outcomes = [
            probe.attempt(probe.Target(f"{kind} {address}", kind, address))
            for kind, address, _ in cases
        ]
    assert [o.reached for o in outcomes] == [reached for *_, reached in cases]
    assert outcomes[1].detail == "ConnectionRefusedError"
    assert outcomes[4].detail == "HTTP/1.1 403 Forbidden"
    assert outcomes[-1].detail == "not configured"
    assert sorted(tmp_path.iterdir()) == [readable]  # the write left nothing behind


def test_the_outside_address_is_the_default_gateway(tmp_path: Path) -> None:
    routes = tmp_path / "route"
    header = "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\n"
    local = "eth0\t000200C0\t00000000\t0001\t0\t0\t0\t00FFFFFF\n"
    routes.write_text(header + local, encoding="ascii")
    assert probe.default_gateway(routes) == ""
    routes.write_text(header + local + "eth0\t00000000\t010200C0\t0003\t0\t0\t0\t00000000\n")
    assert probe.default_gateway(routes) == "192.0.2.1"
    assert probe.default_gateway(tmp_path / "absent") == ""


@pytest.fixture
def host_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Infra's fixed paths, moved where nothing exists (the test is independent of the host)."""
    for name in (
        "RESTRICTED",
        "INFRA_SECRETS",
        "GIT_MIRRORS",
        "DATASETS",
        "RUN_NETWORK_SOCKET",
        "OWN_ENVIRONMENT",
    ):
        monkeypatch.setattr(probe, name, str(tmp_path / "absent" / name.lower()))
    monkeypatch.setattr(probe, "ROUTES", tmp_path / "absent" / "route")


@pytest.mark.usefixtures("host_paths")
def test_the_probe_fails_on_a_reach_it_does_not_allow(
    tmp_path: Path, sockets: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    api = sockets / "api.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(api))
        listener.listen()
        state = tmp_path / "records"
        state.mkdir()
        endpoints = tmp_path / "endpoints.toml"
        endpoints.write_text('platform = "refplat"\n', encoding="utf-8")
        principals = tmp_path / "principals.json"
        principals.write_text(
            json.dumps(
                {
                    "platform": "refplat",
                    "sockets": [{"service": "ctl", "name": "api", "path": str(api)}],
                    "services": [{"name": "ctl", "state": {"records": str(state)}}],
                }
            ),
            encoding="utf-8",
        )
        files = ["--endpoints", str(endpoints), "--principals", str(principals)]
        assert probe.main(files) == 1
        report = one_line(capsys)
        assert report["reached"] == ["socket ctl/api", "write state ctl/records"]
        assert report["profile"] == "untrusted-agent"
        assert report["uid"] == os.getuid()
        allowed = [
            "--allow",
            "socket ctl/api",
            "--own-state",
            str(state),
            "--profile",
            "controller",
        ]
        assert probe.main([*files, *allowed]) == 0
        report = one_line(capsys)
        assert report["reached"] == ["socket ctl/api"]
        assert "write state ctl/records" not in [o["name"] for o in report["outcomes"]]
    assert probe.load(tmp_path / "absent.toml", tmp_path / "absent.json", "refplatb") == (
        probe.default_endpoints("refplatb"),
        probe.default_principals("refplatb"),
    )


@pytest.mark.usefixtures("host_paths")
def test_the_agent_reports_its_probe_over_the_session(
    tmp_path: Path,
    sockets: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    controller = controller_for(tmp_path, FakeArbiter(sockets / "gpu.sock"))
    monkeypatch.setattr(agent_main, "SESSION", str(sockets / "session.sock"))
    assert agent_main.main() == 1  # no session yet
    assert "no session" in capsys.readouterr().err
    serve(sockets / "session.sock", controller.handle_session)
    loaded: list[str] = []

    def load(platform: str) -> tuple[dict[str, Any], dict[str, Any]]:
        loaded.append(platform)
        return {"platform": platform}, {"platform": platform}

    monkeypatch.setattr(probe, "load", load)
    assert agent_main.session({"command": "hello"}) == {"ok": True, "platform": "refplat"}
    document = agent_main.run_once("refplatb")
    assert loaded == ["refplatb"]
    assert one_line(capsys) == document
    assert (document["profile"], document["reached"]) == ("untrusted-agent", [])
    saved = controller.handle_api({"command": "probe-report"})["report"]
    assert saved["outcomes"] == document["outcomes"]


# --- PostgreSQL ---------------------------------------------------------------------

SUPERUSER_PASSWORD = "refplat-superuser-test-password"  # noqa: S105 - throwaway test cluster
PLAN = database.plan(MANIFEST, CONTRACT, [])
PASSWORDS = {role: f"{role}-test-password" for role in PLAN["vault_roles"]}


@dataclass
class Server:
    """A running throwaway cluster with infra's database of refplat."""

    bindir: Path
    sockets: Path
    passwords: Path

    def psql(self, sql: str, db: str = "postgres") -> subprocess.CompletedProcess[str]:
        env = {**os.environ, "PGPASSWORD": SUPERUSER_PASSWORD, "PGCONNECT_TIMEOUT": "10"}
        env.pop("PGPASSFILE", None)
        argv = [str(self.bindir / "psql"), "--no-psqlrc", "--set=ON_ERROR_STOP=1"]
        argv += ["--tuples-only", "--no-align", f"--host={self.sockets}", "--username=postgres"]
        return subprocess.run(
            [*argv, f"--dbname={db}"],
            input=sql,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )

    def ok(self, sql: str, db: str = "postgres") -> str:
        done = self.psql(sql, db)
        assert done.returncode == 0, done.stderr
        return done.stdout.strip()

    def endpoints(self) -> dict[str, Any]:
        """``endpoints.toml`` of refplat with this server's socket."""
        return {**ENDPOINTS, "postgres": {**ENDPOINTS["postgres"], "socket_dir": str(self.sockets)}}

    def connect(self, role: str) -> pg.Connection:
        return pg.connect(str(self.sockets), role, PLAN["database"], self.passwords)


@pytest.fixture(scope="module", params=sorted(BINDIRS), ids=lambda major: f"pg{major}")
def server(request: pytest.FixtureRequest) -> Iterator[Server]:
    """A throwaway cluster (SCRAM on its socket, no TCP) with refplat's database and roles."""
    bindir = BINDIRS[request.param]
    root = Path(tempfile.mkdtemp(prefix="refplat-pg-"))
    prefix: list[str] = []
    (root / "pwfile").write_text(SUPERUSER_PASSWORD + "\n", encoding="utf-8")
    if os.geteuid() == 0:  # initdb refuses to run as root
        nobody = pwd.getpwnam("nobody")
        root.chmod(0o755)
        for path in (root, root / "pwfile"):
            os.chown(path, nobody.pw_uid, nobody.pw_gid)
        prefix = ["runuser", "-u", "nobody", "--"]
    data = root / "data"
    initdb = [str(bindir / "initdb"), f"--pgdata={data}", "--username=postgres"]
    initdb += [f"--pwfile={root / 'pwfile'}", "--auth-local=scram-sha-256", "--no-locale"]
    subprocess.run([*prefix, *initdb, "--auth-host=scram-sha-256"], check=True, capture_output=True)
    options = f"-c unix_socket_directories={root} -c listen_addresses=''"
    start = [str(bindir / "pg_ctl"), f"--pgdata={data}", "--wait", f"--options={options}"]
    subprocess.run(
        [*prefix, *start, f"--log={root / 'server.log'}", "start"], check=True, capture_output=True
    )
    try:
        found = Server(bindir, root, root / "passwords")
        found.ok(database.bootstrap_sql(PLAN))
        found.passwords.mkdir()
        for role, password in PASSWORDS.items():
            found.ok(f"ALTER ROLE \"{role}\" PASSWORD '{password}';")
            (found.passwords / role).write_text(password + "\n", encoding="utf-8")
        yield found
    finally:
        stop = [str(bindir / "pg_ctl"), f"--pgdata={data}", "--mode=immediate", "stop"]
        subprocess.run([*prefix, *stop], check=False, capture_output=True)
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture(scope="module")
def migrated(server: Server) -> Server:
    """The database after the ``migrate`` hook ran as the migration account."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(admin, "endpoints", server.endpoints)
        patch.setattr(admin, "DB_SECRETS", server.passwords)
        assert admin.migrate() == 0
    return server


def test_the_client_logs_in_with_scram_and_quotes_literals(server: Server) -> None:
    conn = server.connect("refplat_ctl")
    try:
        assert conn.query("SELECT current_user") == [("refplat_ctl",)]
        statement = pg.sql("SELECT {}, {}, {}, {}", "it's", None, 3, True)
        assert statement == "SELECT 'it''s', NULL, 3, TRUE"
        assert conn.query(statement) == [("it's", None, "3", "t")]
        assert pg.identifier('a"b') == '"a""b"'
        assert conn.query('SELECT x FROM (SELECT 1 AS x) AS "a""b"') == [("1",)]
    finally:
        conn.close()
    with pytest.raises(pg.PgError, match="28P01"):
        pg.Connection(str(server.sockets), "refplat_ctl", PLAN["database"], "wrong-password")


def test_migrate_creates_the_tables_and_passes_the_restricted_check(
    migrated: Server, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(admin, "endpoints", migrated.endpoints)
    monkeypatch.setattr(admin, "DB_SECRETS", migrated.passwords)
    capsys.readouterr()
    assert admin.main(["migrate"]) == 0  # a second run changes nothing
    assert one_line(capsys) == {"ok": True, "tables": 3}
    owners = migrated.ok(
        "SELECT schemaname || '.' || tablename || ' ' || tableowner FROM pg_tables"
        " WHERE tablename LIKE 'refplat_%' ORDER BY 1",
        PLAN["database"],
    )
    assert owners.splitlines() == [
        "public.refplat_datasets refplat_migrator",
        "public.refplat_runs refplat_migrator",
        "restricted.refplat_cases refplat_migrator",
    ]
    check = migrated.psql(database.restricted_check_sql(PLAN), PLAN["database"])
    assert check.returncode == 0, check.stderr
    assert "pinka-violation" not in check.stderr


def test_the_controller_writes_its_tables(migrated: Server, tmp_path: Path, sockets: Path) -> None:
    arbiter = FakeArbiter(sockets / "gpu.sock")
    controller = controller_for(tmp_path, arbiter, db=lambda: migrated.connect("refplat_ctl"))
    assert controller.status()["database"] == "ok"
    controller.dataset()
    run = controller.train({})["run"]
    register(tmp_path / "adapters", arbiter.end("ok"))
    controller.runs()
    db = PLAN["database"]
    row = migrated.ok(
        pg.sql("SELECT job, status, adapter FROM public.refplat_runs WHERE run = {}", run), db
    )
    assert row == f"tunecpu|succeeded|{ADAPTER_SHA}"
    trains = sorted(c["id"] for c in training.read_cases(CASES) if c["split"] == "train")
    stored = migrated.ok("SELECT id FROM restricted.refplat_cases ORDER BY id", db)
    assert stored.splitlines() == trains


def test_the_reader_cannot_read_the_restricted_cases(migrated: Server) -> None:
    conn = migrated.connect("refplat_reader")
    try:
        with pytest.raises(pg.PgError, match="42501"):
            conn.query("SELECT id FROM restricted.refplat_cases")
        assert conn.query("SELECT 1") == [("1",)]
    finally:
        conn.close()


def test_a_failed_statement_rolls_the_transaction_back(migrated: Server) -> None:
    conn = migrated.connect("refplat_ctl")
    try:
        with pytest.raises(pg.PgError, match="22012"):
            pg.execute(
                conn,
                [
                    "INSERT INTO public.refplat_datasets (sha256, instances) "
                    "VALUES ('rollback', 1)",
                    "SELECT 1 / 0",
                ],
            )
        kept = "SELECT count(*) FROM public.refplat_datasets WHERE sha256 = 'rollback'"
        assert conn.query(kept) == [("0",)]
    finally:
        conn.close()
