"""The answers file: schema, checks, round trips (BUILD_PROMPT §18.6, §18.9)."""

from __future__ import annotations

import copy
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from ruamel.yaml import YAML

from pokus_installer import answers as answers_mod
from pokus_installer import apply
from pokus_installer.answers import Answers, Context, Researcher
from pokus_installer.paths import INFRA, Paths

CI = INFRA / "tests" / "answers" / "ci.yml"


def full_answers() -> Answers:
    """Answers that use every section."""
    data: dict[str, Any] = YAML(typ="safe").load(CI.read_text(encoding="utf-8"))
    answers = Answers.from_dict(data)
    answers.machine.name = "pokus-lab"
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
    answers.backups.repository = answers_mod.Repository(
        "sftp", "backup.example.com", "backup", "/srv/restic/pokus-lab"
    )
    answers.source.repo = "git@github.com:example-org/pokus.git"
    answers.platform = answers_mod.Component(True, "platform-v0.1.0")
    answers.catalog = answers_mod.Component(True, "catalog-v0.1.0")
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
    assert path.read_text().startswith("# Installer answers for pokus-lab")


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
        (lambda d: d.update(extra=1), "(top)"),
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
    answers.source.repo = "not a url"
    answers.gpu.install_driver = "manual"
    answers.gpu.accept_mok_enrollment = True
    found = "\n".join(answers_mod.problems(answers, context))
    for expected in (
        "storage/device_confirm",
        "share CPUs 4-5",
        "at least one researcher must be an admin",
        "still active: researcher2",
        "not in models.lock.yml",
        "no profile 'no-such-profile'",
        "no blueprint 'no-such-blueprint'",
        "source/repo",
        "split_inference/peer",
        "gpu/accept_mok_enrollment",
    ):
        assert expected in found, expected


def test_split_inference_rules(repo: Paths) -> None:
    context = apply.context(repo)
    answers = full_answers()
    answers.machine.node_role = "inference"
    answers.split_inference_peer = "pokus-lab"
    found = "\n".join(answers_mod.problems(answers, context))
    assert "the other node, not this machine" in found
    assert "an inference node runs neither platform nor catalog" in found
    answers.platform.enabled = answers.catalog.enabled = False
    answers.split_inference_peer = "pokus-lab-main"
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
    assert answers_mod.secret_names(answers) == []
    answers.remote_access.tailscale_login = "auth_key"
    answers.backups.password = "provided"  # noqa: S105 - the choice
    answers.secrets.grafana_admin = "provided"
    answers.secrets.hf_token = "provided"  # noqa: S105 - the choice
    assert answers_mod.secret_names(answers) == [
        "TAILSCALE_AUTHKEY",
        "RESTIC_PASSWORD",
        "GRAFANA_ADMIN_PASSWORD",
        "HF_TOKEN",
    ]


def test_load_reports_every_problem(tmp_path: Path) -> None:
    path = tmp_path / "bad.yml"
    path.write_text("schema_version: 1\nmode: fresh\n", encoding="utf-8")
    with pytest.raises(answers_mod.AnswersError) as caught:
        answers_mod.load(path)
    assert len(caught.value.problems) > 3
