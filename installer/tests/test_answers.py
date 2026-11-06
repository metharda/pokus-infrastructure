"""The answers file: schema, checks, round trips."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from ruamel.yaml import YAML

from pinka_installer import answers as answers_mod
from pinka_installer import apply, validators
from pinka_installer import plan as plan_mod
from pinka_installer.answers import Answers, Context, Researcher
from pinka_installer.paths import INFRA, Paths

CI = INFRA / "tests" / "answers" / "ci.yml"


def full_answers() -> Answers:
    """Answers that use every section."""
    data: dict[str, Any] = YAML(typ="safe").load(CI.read_text(encoding="utf-8"))
    answers = Answers.from_dict(data)
    answers.machine.name = "pinka-lab"
    answers.machine.lab_mode = "production"
    answers.resources.expected_memory_gib = 22
    answers.resources.expected_cpu_threads = 20
    answers.resources.cpuset_services = "0-5"
    answers.resources.cpuset_targets = "6-19"
    answers.storage.source = "device"
    answers.storage.device = "/dev/disk/by-id/nvme-EXAMPLE_0000"
    answers.storage.device_confirm = "/dev/disk/by-id/nvme-EXAMPLE_0000"
    answers.researchers.append(Researcher("researcher2", False, "Researcher Two", False))
    answers.researchers_removed = ["researcher3"]
    answers.remote_access.tailscale_login = "interactive"
    answers.remote_access.ssh_extra_allowed_interfaces = ["enp5s0"]
    answers.remote_access.close_listeners = ["tcp/8080", "udp/5353"]
    answers.remote_access.close_install_path = True
    answers.backups.repository = answers_mod.Repository(
        "sftp", "backup.example.com", "backup", "/srv/restic/pinka-lab"
    )
    repo = "git@github.com:example-org/example-platform.git"
    answers.platform = answers_mod.Source(repo, "example-v0.1.0", "release", "example-v")
    answers.content = {"cases": answers_mod.Source(repo, "cases-v1", "cases", "cases-v")}
    answers.source_trust = answers_mod.SourceTrust(
        known_hosts=["git.example.com ssh-ed25519 AAAAexample"],
        require_signed_tags=True,
        allowed_signers=["researcher1@example.com ssh-ed25519 AAAAexample"],
    )
    answers.platform_settings = {"ctl": {"job": "tunecpu"}}
    answers.secrets.platform = {"ctl.api-token": "generate", "ctl.webhook": "provided"}
    answers.gpu_arbiter = answers_mod.GpuArbiter("idle", restore_after_training=False)
    answers.training_caps = answers_mod.TrainingCaps(3600, "8G", 4, "2G", "4G", "512M")
    answers.images_build = ["_base", "web-nginx-flask"]
    return answers


def test_ci_answers_satisfy_the_schema_and_the_checks(repo: Paths) -> None:
    loaded = answers_mod.load(repo.infra / "tests" / "answers" / "ci.yml", apply.context(repo))
    assert loaded.machine.name == "ci-placeholder"
    assert answers_mod.schema_problems(loaded.to_dict()) == []


def test_round_trip_through_the_file(repo: Paths, tmp_path: Path) -> None:
    answers = full_answers()
    assert answers_mod.schema_problems(answers.to_dict()) == []
    assert answers_mod.problems(answers, apply.context(repo)) == []
    path = tmp_path / "answers.yml"
    path.write_text(answers_mod.dumps(answers), encoding="utf-8")
    assert answers_mod.load(path, apply.context(repo)) == answers.normalized()
    assert answers.normalized().storage.loop_size_gib == "auto"  # not used with a device
    assert path.read_text().startswith("# Installer answers for pinka-lab")


def test_schema_rejects_bad_structure() -> None:
    data = full_answers().to_dict()
    cases: list[tuple[Callable[[dict[str, Any]], object], str]] = [
        (lambda d: d.pop("machine"), "(top)"),
        (lambda d: d["storage"].pop("device_confirm"), "storage"),
        (
            lambda d: d["backups"].update(repository={"type": "sftp", "host": "h"}),
            "backups/repository",
        ),
        (lambda d: d["secrets"].update(generate="some"), "secrets/generate"),
        (lambda d: d.update(mode="restore"), "(top)"),
        (lambda d: d["platform"].pop("ref"), "platform"),
        (lambda d: d["content"]["cases"].update(branch="main"), "content/cases"),
        (lambda d: d["content"].update(Bad={"repo": "r", "ref": "x"}), "content"),
        (lambda d: d["secrets"]["platform"].update({"ctl.x": "typed"}), "secrets/platform"),
        (lambda d: d["gpu_arbiter"].update(after_boot="sleep"), "gpu_arbiter/after_boot"),
        (lambda d: d["training_caps"].update(memory_max="8GiB"), "training_caps/memory_max"),
        (lambda d: d.update(extra=1), "(top)"),
        (
            lambda d: d["remote_access"].update(close_listeners=["8080"]),
            "remote_access/close_listeners",
        ),
        (
            lambda d: d["remote_access"].update(close_install_path="yes"),
            "remote_access/close_install_path",
        ),
    ]
    for mutate, where in cases:
        broken = copy.deepcopy(data)
        mutate(broken)
        found = answers_mod.schema_problems(broken)
        assert found and any(p.startswith(where) for p in found), (where, found)


def test_cross_field_problems(repo: Paths) -> None:
    context = apply.context(repo)
    answers = full_answers()
    answers.storage.device_confirm = "/dev/disk/by-id/nvme-OTHER"
    answers.resources.cpuset_targets = "4-19"
    answers.researchers[0].admin = False
    answers.researchers_removed = ["researcher2"]
    answers.gpu.default_model = "example-org/unknown"
    answers.gpu.vllm_profile = "no-such-profile"
    answers.images_build = ["no-such-blueprint"]
    answers.split_inference_peer = "Not_A_Host"
    answers.machine.node_role = "lab"
    answers.platform.repo = "http://git.example.com/example-org/example-platform.git"
    answers.source_trust.allowed_signers = []
    answers.platform_settings["ctl"] = "fast"  # a value that is no mapping
    answers.secrets.platform["ctl"] = "generate"
    answers.training_caps.tmp_max = "1GiB"
    answers.gpu.install_driver = "manual"
    answers.gpu.accept_mok_enrollment = True
    answers.remote_access.close_listeners = ["tcp/70000"]
    found = "\n".join(answers_mod.problems(answers, context))
    for expected in (
        "storage/device_confirm",
        "share CPUs 4-5",
        "at least one researcher must be an admin",
        "still active: researcher2",
        "not in models.lock.yml",
        "gpu/vllm_profile: vllm_profile 'no-such-profile' is not a serving profile",
        "images/build: golden image blueprint 'no-such-blueprint' does not exist (images/: ",
        "platform/repo: use an SSH URL",
        "source_trust/allowed_signers",
        "platform_settings/ctl: must be a mapping",
        "secrets/platform/ctl",
        "training_caps/tmp_max",
        "split_inference/peer",
        "gpu/accept_mok_enrollment",
        "remote_access/close_listeners: tcp/PORT or udp/PORT",
    ):
        assert expected in found, expected


def test_sources_follow_the_platform_tool_s_rules(repo: Paths) -> None:
    # A package overlapping another in one repository, a bad ref.
    answers = full_answers()
    answers.content["guides"] = answers_mod.Source(answers.content["cases"].repo, "main", "cases")
    answers.content["more"] = answers_mod.Source(answers.platform.repo, "a..b", "more")
    found = "\n".join(answers_mod.problems(answers, apply.context(repo)))
    assert "overlaps the path 'cases' of content package 'guides'" in found
    assert "content/more/ref: not a valid ref" in found


def test_split_inference_rules(repo: Paths) -> None:
    context = apply.context(repo)
    answers = full_answers()
    answers.machine.node_role = "inference"
    answers.split_inference_peer = "pinka-lab"
    found = "\n".join(answers_mod.problems(answers, context))
    assert "the other node, not this machine" in found
    assert "platform: an inference node runs no platform" in found
    # The CI answers switch inference off; an inference node needs it.
    assert "features/inference: an inference node runs only the inference stack" in found
    answers.features.inference = "auto"
    answers.platform = answers_mod.Source()
    answers.content = {}
    answers.platform_settings = {}
    answers.secrets.platform = {}
    answers.split_inference_peer = "pinka-lab-main"
    assert answers_mod.problems(answers, context) == []


def test_inference_on_by_node_role(repo: Paths) -> None:
    context = apply.context(repo)
    answers = full_answers()
    answers.features.inference = "on"
    assert not any("features/inference" in p for p in answers_mod.problems(answers, context))
    answers.machine.node_role = "lab"
    found = answers_mod.problems(answers, context)
    assert f"features/inference: {validators.LAB_ON}" in found
    answers.machine.node_role = "inference"
    answers.features.inference = "off"
    text = "\n".join(answers_mod.problems(answers, context))
    assert f"features/inference: {validators.INFERENCE_NODE_OFF}" in text


@pytest.mark.parametrize(
    "rule", ["lab node on", "inference node off", "unknown profile", "unknown blueprint"]
)
def test_one_text_per_plan_refusal(repo: Paths, tmp_path: Path, rule: str) -> None:
    """Bootstrap (the plan writer), pinka-preflight and the answers check refuse with one text.

    Each path prints it after its own prefix: ``pinka-installer: the requirements
    plan:``, ``pinka-preflight: error:`` and the answers field.
    """
    from pinka_preflight import requirements  # noqa: PLC0415 - preflight only here

    context = apply.context(repo)
    profiles = sorted(context.vllm_profiles)
    answers = full_answers()
    if rule == "lab node on":
        settings: dict[str, Any] = {"node_role": "lab", "inference": "on"}
        text, field = validators.LAB_ON, "features/inference"
        assert text == requirements.LAB_ON
    elif rule == "inference node off":
        settings = {"node_role": "inference", "inference": "off"}
        text, field = validators.INFERENCE_NODE_OFF, "features/inference"
        assert text == requirements.INFERENCE_NODE_OFF
    elif rule == "unknown profile":
        settings = {"vllm_profile": "ada-12gb"}
        text, field = validators.unknown_profile("ada-12gb", profiles), "gpu/vllm_profile"
        assert text == requirements.unknown_profile("ada-12gb", profiles)
        assert text == "vllm_profile 'ada-12gb' is not a serving profile (vllm/profiles/: ada-12g)"
    else:
        settings = {"images": ["no-such-blueprint"]}
        blueprints = sorted(context.blueprints)
        text, field = validators.unknown_blueprint("no-such-blueprint", blueprints), "images/build"
        assert text == requirements.unknown_blueprint("no-such-blueprint", blueprints)
        assert text.startswith(
            "golden image blueprint 'no-such-blueprint' does not exist (images/: "
        )
    # Bootstrap: the plan writer refuses host_vars (pinka-installer: the requirements plan: ...).
    (repo.infra / "preflight").mkdir(exist_ok=True)
    (repo.infra / "preflight" / "requirements.json").write_bytes(
        (INFRA / "preflight" / "requirements.json").read_bytes()
    )
    host_vars = repo.host_vars("lab9")
    host_settings = {("images_blueprints" if k == "images" else k): v for k, v in settings.items()}
    host_vars.write_text(
        "".join(f"{k}: {v!r}\n" for k, v in host_settings.items())
        + "vllm_default_deployment: null\n",
        encoding="utf-8",
    )
    with pytest.raises(plan_mod.PlanError) as caught:
        plan_mod.machine_plan(repo, "lab9")
    assert str(caught.value) == text
    # pinka-preflight --plan (pinka-preflight: error: plan (<file>): ...).
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps({"schema_version": 1, **settings}), encoding="utf-8")
    defaults = json.loads((INFRA / "preflight" / "requirements.json").read_text())["defaults"]
    data = json.loads((INFRA / "preflight" / "requirements.json").read_text())
    with pytest.raises(requirements.PlanError) as refused:
        requirements.load_plan(
            str(plan_file), defaults, profiles=profiles, blueprints=data["blueprints"]
        )
    assert str(refused.value) == f"plan ({plan_file}): {text}"
    # The installer's answers.
    answers.machine.node_role = settings.get("node_role", answers.machine.node_role)
    answers.features.inference = settings.get("inference", answers.features.inference)
    answers.gpu.vllm_profile = settings.get("vllm_profile", answers.gpu.vllm_profile)
    answers.images_build = settings.get("images", answers.images_build)
    assert f"{field}: {text}" in answers_mod.problems(answers, context)


def test_one_text_for_a_model_not_pinned(repo: Paths) -> None:
    """The plan writer and the answers check refuse an unpinned default model alike."""
    text = validators.unpinned_model("example-unpinned")
    assert text == (
        "'example-unpinned' is not in models.lock.yml; pin it first (docs/how-to/add-a-model.md)"
    )
    with pytest.raises(plan_mod.PlanError) as caught:
        defaults = json.loads((INFRA / "preflight" / "requirements.json").read_text())["defaults"]
        plan_mod.plan(defaults, {"vllm_default_deployment": {"model_id": "example-unpinned"}}, {})
    assert str(caught.value) == f"vllm_default_deployment.model_id {text}"
    answers = full_answers()
    answers.gpu.default_model = "example-unpinned"
    assert f"gpu/default_model: {text}" in answers_mod.problems(answers, apply.context(repo))


def test_restore_needs_a_repository(repo: Paths) -> None:
    answers = full_answers()
    answers.mode = "restore"
    answers.backups.repository = answers_mod.Repository()
    found = answers_mod.problems(answers, apply.context(repo))
    assert any("restore mode needs the repository" in p for p in found)
    assert answers.to_dict()["restore"] == {"snapshot": "latest"}


def test_unknown_interfaces_and_cpus(repo: Paths) -> None:
    context = Context(cpu_count=8, interfaces=frozenset({"eth0"}))
    answers = full_answers()
    found = "\n".join(answers_mod.problems(answers, context))
    assert "this host has no interface 'enp5s0'" in found
    assert "CPU 19 does not exist" in found


def test_secret_names() -> None:
    answers = full_answers()
    answers.secrets.platform = {"ctl.api-token": "generate"}
    assert answers_mod.secret_names(answers) == []
    answers.secrets.platform["ctl.webhook"] = "provided"
    answers.remote_access.tailscale_login = "auth_key"
    answers.backups.password = "provided"  # noqa: S105 - the choice
    answers.secrets.grafana_admin = "provided"
    answers.secrets.hf_token = "provided"  # noqa: S105 - the choice
    assert answers_mod.secret_names(answers) == [
        "TAILSCALE_AUTHKEY",
        "RESTIC_PASSWORD",
        "GRAFANA_ADMIN_PASSWORD",
        "HF_TOKEN",
        "PLATFORM_CTL_WEBHOOK",
    ]
    assert answers_mod.platform_secret_variable("ctl.api-token") == (
        "PINKA_SECRET_PLATFORM_CTL_API_TOKEN"
    )


def test_without_a_platform_nothing_of_it_is_kept() -> None:
    answers = full_answers()
    answers.platform = answers_mod.Source()
    data = answers.to_dict()
    assert data["platform"] is None
    assert not {"content", "source_trust", "platform_settings"} & set(data)
    assert "platform" not in data["secrets"]
    assert data["gpu_arbiter"] == {"after_boot": "idle", "restore_after_training": False}
    assert answers_mod.schema_problems(data) == []


def test_load_reports_every_problem(tmp_path: Path) -> None:
    path = tmp_path / "bad.yml"
    path.write_text("schema_version: 1\nmode: fresh\n", encoding="utf-8")
    with pytest.raises(answers_mod.AnswersError) as caught:
        answers_mod.load(path)
    assert len(caught.value.problems) > 3
