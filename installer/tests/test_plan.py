"""The requirements plan bootstrap passes to preflight (BUILD_PROMPT §2.3)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest
import yaml

from pinka_installer import hostvars, models, validators
from pinka_installer import plan as plan_mod
from pinka_installer.answers import Answers, Machine
from pinka_installer.paths import INFRA, Paths

DEFAULTS: dict[str, Any] = json.loads(
    (INFRA / "preflight" / "requirements.json").read_text(encoding="utf-8")
)["defaults"]
SCHEMA = json.loads((INFRA / "preflight" / "schema" / "plan.schema.json").read_text("utf-8"))
REVISION = "0" * 40
LOCK = {
    "models": [
        {
            "id": "example-model",
            "hf_repo": "example-org/example-model",
            "revision": REVISION,
            "files": [
                {"path": "config.json", "sha256": "0" * 64, "size": 1000},
                {"path": "model.safetensors", "sha256": "1" * 64, "size": 6 * 2**30},
            ],
        }
    ]
}


def make(**variables: Any) -> dict[str, Any]:
    result = plan_mod.plan(DEFAULTS, variables, LOCK)
    jsonschema.validate(result, SCHEMA)
    return result


def test_nothing_set_is_the_defaults() -> None:
    assert make() == DEFAULTS


def test_the_installers_capacity_defaults_are_ansibles() -> None:
    # host_vars written from default answers ask for the Ansible defaults
    # (golden images and the model are the operator's choice in the TUI).
    answers = Answers(machine=Machine(name="lab1"))
    written = make(**hostvars.host_vars(answers))
    for key in (
        "node_role",
        "inference",
        "srv_path",
        "docker_data_root",
        "pool",
        "concurrent_targets",
    ):
        assert written[key] == DEFAULTS[key], key
    assert written["training"] == DEFAULTS["training"]
    assert written["retention"] == DEFAULTS["retention"]
    assert written["model"] is None


def test_settings_become_the_plan() -> None:
    found = make(
        node_role="lab",
        pinka_root="/data/pinka",
        docker_data_root="/data/docker",
        incus_pool_source="device",
        incus_pool_size="500GiB",
        incus_concurrent_targets=4,
        images_blueprints=["_base"],
        observability_gpu_exporter="dcgm",
        training_caps={"memory_max": "12G", "run_storage_max": "512M"},
        observability_prometheus_retention="2w",
        observability_prometheus_retention_size="50GB",
        observability_loki_retention="1y",
        observability_tempo_retention="48h",
        backup_pgdump_keep=3,
    )
    assert found["node_role"] == "lab"
    assert (found["srv_path"], found["docker_data_root"]) == ("/data/pinka", "/data/docker")
    assert found["pool"] == {"source": "device", "size_gib": 500.0}
    assert (found["concurrent_targets"], found["images"]) == (4, ["_base"])
    assert found["gpu_exporter"] == "dcgm"
    assert found["training"] == {"mode": "auto", "memory_max_gib": 12.0, "run_storage_max_gib": 0.5}
    assert found["retention"] == {
        "prometheus_days": 14,
        "prometheus_size_gib": 50.0,  # Prometheus reads GB as a power of 2
        "loki_days": 365,
        "tempo_days": 2,
        "pg_dumps": 3,
    }


def test_the_switches_and_the_computed_cap() -> None:
    # BUILD_PROMPT §2.3: the switches, and memory_max auto (null: preflight computes it).
    assert (DEFAULTS["inference"], DEFAULTS["training"]["mode"]) == ("auto", "auto")
    assert DEFAULTS["training"]["memory_max_gib"] is None
    found = make(inference="off", training="cpu", training_caps={"memory_max": "auto"})
    assert (found["inference"], found["training"]["mode"]) == ("off", "cpu")
    assert found["training"]["memory_max_gib"] is None
    # A training_caps without memory_max replaces the whole value: auto.
    assert make(training_caps={"cpus": 4})["training"]["memory_max_gib"] is None
    answers = Answers(machine=Machine(name="lab1"))
    answers.features.inference, answers.features.training = "off", "off"
    written = make(**hostvars.host_vars(answers))
    assert (written["inference"], written["training"]["mode"]) == ("off", "off")


def test_off_stays_off_for_ansible() -> None:
    # Ansible reads host_vars as YAML 1.1, where a plain off is false: the
    # installer quotes it (yamlio), so every reader sees the string.
    answers = Answers(machine=Machine(name="lab1"))
    answers.features.inference = answers.features.training = "off"
    example = (INFRA / "ansible" / "inventory" / "host_vars" / "example-machine.yml").read_text()
    text = hostvars.render(answers, None, example)
    assert 'inference: "off"' in text
    loaded = yaml.safe_load(text)
    assert (loaded["inference"], loaded["training"]) == ("off", "off")
    assert hostvars.render(answers, text, example) == text


def test_the_selected_model_with_its_size() -> None:
    found = make(vllm_default_deployment={"model_id": "example-model"})
    assert found["model"] == {
        "id": "example-model",
        "hf_repo": "example-org/example-model",
        "revision": REVISION,
        "size_bytes": 6 * 2**30 + 1000,
    }
    assert make(vllm_default_deployment=None)["model"] is None


@pytest.mark.parametrize(
    ("variables", "message"),
    [
        ({"vllm_default_deployment": {"model_id": "<id from models.lock.yml>"}}, "not in"),
        ({"pinka_root": "{{ somewhere }}"}, "plain value"),
        ({"node_role": "storage"}, "full, lab or inference"),
        ({"incus_pool_size": "300"}, "incus_pool_size"),
        ({"incus_concurrent_targets": 0}, "incus_concurrent_targets"),
        ({"incus_concurrent_targets": "2"}, "incus_concurrent_targets"),
        ({"observability_tempo_retention": "30d"}, "observability_tempo_retention"),
        ({"observability_prometheus_retention_size": "50G"}, "retention_size"),
        ({"training_caps": {"memory_max": "24GiB"}}, "training_caps.memory_max"),
        ({"inference": "on"}, "auto or off"),
        ({"training": "gpu"}, "auto, cpu or off"),
        ({"node_role": "inference", "inference": "off"}, "needs inference: auto"),
        ({"backup_pgdump_keep": 0}, "backup_pgdump_keep"),
    ],
)
def test_unusable_settings(variables: dict[str, Any], message: str) -> None:
    with pytest.raises(plan_mod.PlanError, match=message):
        plan_mod.plan(DEFAULTS, variables, LOCK)


def test_a_model_pinned_without_sizes_is_refused() -> None:
    lock = json.loads(json.dumps(LOCK))
    del lock["models"][0]["files"][0]["size"]
    with pytest.raises(plan_mod.PlanError, match="without its size"):
        plan_mod.plan(DEFAULTS, {"vllm_default_deployment": {"model_id": "example-model"}}, lock)


def test_host_vars_win_over_group_vars(tmp_path: Path) -> None:
    host_vars = tmp_path / "ansible" / "inventory" / "host_vars"
    group_vars = tmp_path / "ansible" / "group_vars" / "all"
    host_vars.mkdir(parents=True)
    group_vars.mkdir(parents=True)
    (tmp_path / "preflight").mkdir()
    (tmp_path / "preflight" / "requirements.json").write_text(
        json.dumps({"defaults": DEFAULTS}), encoding="utf-8"
    )
    (group_vars / "main.yml").write_text("node_role: lab\npinka_root: /data\n", encoding="utf-8")
    (host_vars / "lab1.yml").write_text("node_role: full\n", encoding="utf-8")
    found = plan_mod.machine_plan(Paths(infra=tmp_path), "lab1")
    assert (found["node_role"], found["srv_path"]) == ("full", "/data")
    with pytest.raises(plan_mod.PlanError, match="does not exist"):
        plan_mod.machine_plan(Paths(infra=tmp_path), "lab2")


def test_the_checkouts_example_machine_needs_a_pinned_model() -> None:
    # example-machine.yml names a placeholder model: a copy must pin its own.
    with pytest.raises(plan_mod.PlanError, match=r"not in models\.lock\.yml"):
        plan_mod.machine_plan(Paths(), "example-machine")


def test_pinned_files_carry_their_sizes() -> None:
    tree = [
        {
            "type": "file",
            "path": "model.safetensors",
            "size": 135,
            "lfs": {"oid": "a" * 64, "size": 7},
        },
        {"type": "file", "path": "config.json", "size": 2},
        {"type": "file", "path": "README.md", "size": 9},
        {"type": "directory", "path": "extra"},
    ]

    def get(url: str, token: str) -> bytes:
        assert token == ""
        if "/tree/" in url:
            return json.dumps(tree).encode()
        assert url.endswith("/config.json")
        return b"{}"

    files = models.pin_files("example-org/example-model", REVISION, "", get)
    assert [(f.path, f.size) for f in files] == [("config.json", 2), ("model.safetensors", 7)]
    entry = models.lock_entry("example-model", "example-org/example-model", REVISION, files, {})
    assert entry["files"][0] == {"path": "config.json", "sha256": files[0].sha256, "size": 2}
    tree[0] = {"type": "file", "path": "model.safetensors", "lfs": {"oid": "a" * 64}}
    with pytest.raises(models.HubError, match="no size"):
        models.pin_files("example-org/example-model", REVISION, "", get)


def test_retention_validators() -> None:
    assert validators.retention_period("90d") is None
    assert validators.retention_period("720h", "h") is None
    assert validators.retention_period("30d", "h")
    assert validators.retention_period("0d")
    assert validators.retention_days("2160h") == 90
    assert validators.prometheus_size("0") is None
    assert validators.prometheus_size("1TiB") is None
    assert validators.prometheus_size("1T")
    assert validators.prometheus_size_gib("512MB") == 0.5
    assert validators.cap_size_gib("512M") == 0.5
    assert validators.memory_max("auto") is None
    assert validators.memory_max("12G") is None
    assert validators.memory_max("12GiB") == "auto, or a whole number of K, M or G, such as 24G"
    assert validators.concurrent_targets(40) is None
    assert validators.concurrent_targets(41)
    assert validators.concurrent_targets(True)
