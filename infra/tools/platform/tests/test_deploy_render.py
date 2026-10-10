"""Deployment step 7 (§7.14): configuration, vault keys, render, principals, rendered.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from deploy_testlib import NOW, make_repo, prepare, settings
from platform_testlib import CONTRACT, manifest, write_release

from pinka_platform import cli, config, deploy, release, rendering

PORTS = {
    "otlp_grpc": 4317,
    "otlp_http": 4318,
    "opa": 8181,
    "incus_https": 8443,
    "grafana": 18700,
    "prometheus": 18701,
    "loki": 18702,
    "tempo": 18703,
    "vllm": 18704,
    "gateway_adhoc": 18705,
    "docs_serve": 18706,
    "inference_control": 18710,
}
LAB = {"machine_name": "pinka-lab", "lab_mode": "production", "node_role": "full", "ports": PORTS}
SCHEMA = {
    "$comment": "annotations are allowed",
    "type": "object",
    "properties": {"workers": {"type": "integer", "minimum": 1, "default": 1}},
    "additionalProperties": False,
}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return make_repo(tmp_path)


@pytest.fixture
def paths(tmp_path: Path) -> deploy.Paths:
    return deploy.Paths.under(tmp_path / "host")


# --- configuration (platform_settings) ----------------------------------------------


def staged(tmp_path: Path, schema: Any = SCHEMA) -> Path:
    directory = write_release(tmp_path, manifest("allprof"))
    path = directory / "schemas" / "ctl.schema.json"
    path.write_text(schema if isinstance(schema, str) else json.dumps(schema), encoding="utf-8")
    return directory


def config_codes(tmp_path: Path, value: Any, schema: Any = SCHEMA) -> list[tuple[str, str]]:
    problems = config.check(manifest("allprof"), staged(tmp_path, schema), value)
    return [(p.code, p.where) for p in problems]


def test_a_valid_configuration_passes(tmp_path: Path) -> None:
    assert config_codes(tmp_path, {"ctl": {"workers": 2}}) == []
    assert config_codes(tmp_path, {}) == []  # checked as {}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"ctl": {"workers": 0}}, [("config", "platform_settings.ctl.workers")]),
        ({"ctl": {"threads": 2}}, [("config", "platform_settings.ctl")]),
        ({"ctl": [1]}, [("config", "platform_settings.ctl")]),
        ({"nosuch": {}}, [("config-unknown", "platform_settings.nosuch")]),
        ({"brk": {}}, [("config-unexpected", "platform_settings.brk")]),
        ([], [("config", "platform_settings")]),
    ],
)
def test_configuration_problems(
    tmp_path: Path, value: Any, expected: list[tuple[str, str]]
) -> None:
    assert config_codes(tmp_path, value) == expected


def test_a_required_key_must_be_set(tmp_path: Path) -> None:
    schema = {**SCHEMA, "required": ["workers"]}
    assert config_codes(tmp_path, {}, schema) == [("config", "platform_settings.ctl")]


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object", "if": {"type": "object"}},  # a keyword infra does not check
        "not json",
        "[1, 2]",
        "x" * (config.MAX_SCHEMA_BYTES + 1),
    ],
)
def test_schemas_infra_cannot_check_are_refused(tmp_path: Path, schema: Any) -> None:
    assert config_codes(tmp_path, {}, schema) == [("config-schema", "schemas/ctl.schema.json")]


def test_prepare_refuses_a_bad_configuration(repo: Path, paths: deploy.Paths) -> None:
    s = settings(repo, platform_settings={"ctl": {"workers": 2}, "nosuch": {}})
    with pytest.raises(deploy.Refused) as exc:
        prepare(paths, s)
    assert [p.code for p in exc.value.problems] == ["config-unknown"]


# --- vault keys ------------------------------------------------------------------------


def test_vault_keys_name_every_password_and_secret() -> None:
    keys = deploy.vault_keys(manifest("allprof"), CONTRACT, [])
    assert "vault_platform_secrets.ctl.token" in keys["needed"]
    assert "vault_postgres_passwords.allprof_ctl" in keys["needed"]
    assert "vault_postgres_passwords.allprof_migrator" in keys["needed"]
    assert "vault_postgres_passwords.allprof_reader" in keys["needed"]
    assert "vault_postgres_passwords.allprof_grafana" in keys["needed"]
    assert keys["missing"] == keys["needed"]
    held = deploy.vault_keys(manifest("allprof"), CONTRACT, keys["needed"][1:])
    assert held["missing"] == keys["needed"][:1]
    without = manifest("allprof")
    without["requires"] = [r for r in without["requires"] if r != "postgres"]
    assert not any(
        k.startswith("vault_postgres_passwords.")
        for k in deploy.vault_keys(without, CONTRACT, [])["needed"]
    )


def test_the_summary_lists_the_vault_keys() -> None:
    text = cli.vault_text({"needed": ["a.b", "c.d"], "missing": ["c.d"]})
    assert text == "Vault keys:\n  a.b\n  c.d (missing)\n"
    assert cli.vault_text({"needed": [], "missing": []}) == ""


# --- render, principals, activate ------------------------------------------------------


def uid_of(name: str) -> int:
    return 3000 + sum(map(ord, name)) % 1000


def rendered_deployment(repo: Path, paths: deploy.Paths) -> dict[str, Any]:
    plan = prepare(paths, settings(repo, lab=LAB, platform_settings={"ctl": {"workers": 2}}))
    deploy.publish(plan, paths, now=lambda: NOW)
    result = deploy.render_step(plan, paths, CONTRACT)
    assert result["changed"] is False
    assert json.loads((Path(plan["run_dir"]) / deploy.RENDER).read_text()) == result["render"]
    assert deploy.principals_step(plan, paths, CONTRACT, uid=uid_of) == {"changed": True}
    assert deploy.principals_step(plan, paths, CONTRACT, uid=uid_of) == {"changed": False}
    deploy.activate(plan, paths, now=lambda: NOW)
    return plan


def test_render_principals_and_the_rendered_record(repo: Path, paths: deploy.Paths) -> None:
    plan = rendered_deployment(repo, paths)
    document = json.loads((Path(plan["run_dir"]) / deploy.RENDER).read_text())
    assert document["platform"] == "allprof"
    assert document["config_files"][0]["content"] == '{\n  "workers": 2\n}\n'
    principals = json.loads((paths.etc_platform / deploy.PRINCIPALS).read_text())
    assert principals["services"]
    assert (paths.etc_platform / deploy.ENDPOINTS).read_text().startswith("#")
    kept = json.loads((paths.etc_platform / deploy.RENDERED).read_text())
    assert kept == json.loads(json.dumps(rendering.public(document)))
    assert "content" not in kept["config_files"][0]  # configuration stays in its 0640 file
    assert (paths.etc_platform / deploy.RENDERED).stat().st_mode & 0o777 == 0o644
    assert deploy.read_rendered(paths) == kept


def test_principals_need_the_accounts(repo: Path, paths: deploy.Paths) -> None:
    plan = prepare(paths, settings(repo, lab=LAB))
    deploy.publish(plan, paths, now=lambda: NOW)

    def missing(name: str) -> int:
        raise KeyError(name)

    with pytest.raises(deploy.DeployError, match="account allprof-ctl does not exist"):
        deploy.principals_step(plan, paths, CONTRACT, uid=missing)


def test_retire_plan_follows_the_rendered_record(repo: Path, paths: deploy.Paths) -> None:
    rendered_deployment(repo, paths)
    kept = deploy.read_rendered(paths) or {}
    retire = deploy.retire_plan(deploy.read_active(paths) or {}, CONTRACT, paths)
    assert set(retire["units"]) == {u["name"] for u in kept["units"]} - {"pinka-platform.target"}
    assert any(u.endswith(".socket") for u in retire["units"])
    assert retire["config_files"] == [f["path"] for f in kept["config_files"]]
    assert retire["cli_wrapper"] == kept["cli"]["wrapper"]
    assert retire["cli_link"] == kept["cli"]["link"]
    assert set(kept["database"]["all_roles"]) <= set(retire["postgresql_roles"])
    assert retire["incus_certificates"] == [c["name"] for c in kept["incus_certificates"]]


def test_a_rendering_of_another_platform_is_not_used(repo: Path, paths: deploy.Paths) -> None:
    rendered_deployment(repo, paths)
    record = paths.etc_platform / deploy.RENDERED
    record.write_text(json.dumps({**json.loads(record.read_text()), "platform": "other"}))
    retire = deploy.retire_plan(deploy.read_active(paths) or {}, CONTRACT, paths)
    assert retire["config_files"] == []
    assert "allprof-ctl.service" in retire["units"]


def test_a_new_rendering_knows_the_previous_one(repo: Path, paths: deploy.Paths) -> None:
    plan = rendered_deployment(repo, paths)
    previous = deploy.read_rendered(paths) or {}
    record = paths.etc_platform / deploy.RENDERED
    stale = {"name": "allprof-old.service", "content": ""}
    record.write_text(json.dumps({**previous, "units": [*previous["units"], stale]}))
    document = deploy.render_step(plan, paths, CONTRACT)["render"]
    assert document["stale_units"] == ["allprof-old.service"]


def test_cli_render_and_principals(
    repo: Path, paths: deploy.Paths, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan = prepare(paths, settings(repo, lab=LAB))
    deploy.publish(plan, paths, now=lambda: NOW)
    common = ["--run", plan["run_dir"], "--root", str(tmp_path / "host"), "--no-host-check"]
    assert cli.main(["deploy", "render", *common]) == 0
    assert json.loads(capsys.readouterr().out)["render"]["platform"] == "allprof"
    # The accounts do not exist on this machine: a clear failure, no traceback.
    assert cli.main(["deploy", "principals", *common]) == cli.EXIT_FAILED
    assert "does not exist" in json.loads(capsys.readouterr().out)["error"]


# --- the policy bundle ------------------------------------------------------------------


def bundle(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "release"
    for relative, text in files.items():
        path = root / "policy" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


@pytest.mark.parametrize(
    ("files", "codes"),
    [
        ({"decide.rego": "package allprof.decide\n"}, []),
        ({"data.json": '{"allprof": {"limit": 1}}'}, []),
        ({"x/data.yaml": "limit: 1\n"}, []),
        ({"a.rego": "package system.authz\n"}, ["policy-package"]),
        ({"a.rego": "# x\n  package pinka.placeholder\n"}, ["policy-package"]),
        ({"a.rego": "package systematic\n"}, []),
        ({"system/data.json": "{}"}, ["policy-data"]),
        ({"pinka/x/data.yaml": "a: 1\n"}, ["policy-data"]),
        ({"data.json": '{"system": {"authz": {}}}'}, ["policy-data"]),
        ({"data.yaml": "pinka: {allow: true}\n"}, ["policy-data"]),
        ({"data.yaml": "a: &x 1\nb: *x\n"}, ["policy-data"]),
        ({"data.json": "{"}, ["policy-data"]),
    ],
)
def test_policy_bundles_stay_out_of_infra_namespaces(
    tmp_path: Path, files: dict[str, str], codes: list[str]
) -> None:
    root = bundle(tmp_path, files)
    assert [p.code for p in release.check_policy_bundle(root, "policy")] == codes


def test_a_module_that_is_not_utf8_is_refused(tmp_path: Path) -> None:
    root = bundle(tmp_path, {"a.rego": ""})
    (root / "policy" / "a.rego").write_bytes(b"package \xff\n")
    assert [p.code for p in release.check_policy_bundle(root, "policy")] == ["policy-module"]
