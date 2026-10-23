"""The answers file: schema, checks, round trips (BUILD_PROMPT §18.6, §18.9)."""

from __future__ import annotations

import copy
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from ruamel.yaml import YAML

from pinka_installer import answers as answers_mod
from pinka_installer import apply
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
    assert answers.normalized().storage.loop_size_gib == 300  # not used with a device
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
        "no profile 'no-such-profile'",
        "no blueprint 'no-such-blueprint'",
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
    # A package overlapping another in one repository (§12.1), a bad ref.
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
    # The CI answers switch inference off (§2.3); an inference node needs it.
    assert "features/inference: an inference node runs only the inference stack" in found
    answers.features.inference = "auto"
    answers.platform = answers_mod.Source()
    answers.content = {}
    answers.platform_settings = {}
    answers.secrets.platform = {}
    answers.split_inference_peer = "pinka-lab-main"
    assert answers_mod.problems(answers, context) == []


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
