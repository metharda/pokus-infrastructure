"""Checks of a staged release directory, and the pinka-platform CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from deploy_testlib import SPECIAL_CASES, fake_devices, make_special
from platform_testlib import CONTRACT, FIRST_PLATFORM, VALID, dump, manifest, write_release

from pinka_platform import cli, release, validate


def problem_codes(release: Path, *, built: bool = False) -> list[str]:
    result = validate.validate_release(release, CONTRACT, None, built=built)
    return [p.code for p in result.problems]


@pytest.mark.parametrize("fixture", VALID)
def test_complete_releases_pass(tmp_path: Path, fixture: str) -> None:
    release = write_release(tmp_path / "built", manifest(fixture), built=True)
    assert problem_codes(release, built=True) == []
    source = write_release(tmp_path / "source", manifest(fixture))
    assert problem_codes(source) == []


def test_built_commands_are_checked_after_the_build(tmp_path: Path) -> None:
    release = write_release(tmp_path, manifest("refplat"))
    assert problem_codes(release) == []
    missing = problem_codes(release, built=True)
    # The agent's start command lies in its own environment, which is missing too.
    assert missing and set(missing) == {"command-missing", "agent-env-missing"}


@pytest.mark.parametrize("entry", [".venv", ".agent-venvs"])
def test_a_source_does_not_ship_what_the_build_creates(tmp_path: Path, entry: str) -> None:
    # V6: the shared and the agents' environments come only from the build.
    release = write_release(tmp_path, manifest("refplat"))
    (release / entry / "bin").mkdir(parents=True)
    assert problem_codes(release) == ["built-entry"]
    static = write_release(tmp_path / "static", manifest("allprof"))
    (static / ".agent-venvs").mkdir()
    assert problem_codes(static) == ["built-entry"]


def test_static_commands_are_checked_before_the_build(tmp_path: Path) -> None:
    release = write_release(tmp_path, manifest("allprof"))
    (release / "bin" / "ctl").unlink()
    assert problem_codes(release) == ["command-missing"]


def test_python_uv_needs_uv_lock(tmp_path: Path) -> None:
    release = write_release(tmp_path, manifest("refplat"))
    (release / "uv.lock").unlink()
    assert problem_codes(release) == ["uv-lock"]


def test_command_must_be_a_regular_file(tmp_path: Path) -> None:
    release = write_release(tmp_path, manifest("allprof"))
    (release / "bin" / "obs").unlink()
    (release / "bin" / "obs").mkdir()
    assert problem_codes(release) == ["command-missing"]


@pytest.mark.parametrize("target", ["/etc/passwd", "../../outside", "../../../.."])
def test_symlinks_leaving_the_release_are_refused(tmp_path: Path, target: str) -> None:
    release = write_release(tmp_path, manifest("allprof"))
    (release / "docs" / "link").symlink_to(target)
    assert problem_codes(release) == ["symlink-escape"]


def test_a_command_symlinked_out_of_the_release_is_refused(tmp_path: Path) -> None:
    release = write_release(tmp_path, manifest("allprof"))
    outside = tmp_path / "evil"
    outside.write_text("#!/bin/sh\n")
    (release / "bin" / "ctl").unlink()
    (release / "bin" / "ctl").symlink_to(outside)
    assert sorted(problem_codes(release)) == ["command-missing", "symlink-escape"]


def test_symlinks_inside_the_release_are_fine(tmp_path: Path) -> None:
    release = write_release(tmp_path, manifest("allprof"))
    (release / "docs" / "index.md").write_text("# docs\n")
    (release / "docs" / "start.md").symlink_to("index.md")
    (release / "docs" / "up").symlink_to("../schemas")
    assert problem_codes(release) == []


@pytest.mark.parametrize(("kind", "code"), SPECIAL_CASES.items())
def test_special_files_and_setid_bits_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, code: str
) -> None:
    # Owner decision on 3d.2: none in the staged sources.
    built = write_release(tmp_path, manifest("allprof"))
    make_special(built / "docs" / "planted", kind)
    monkeypatch.setattr(release, "_lstat", fake_devices(kind))
    assert problem_codes(built) == [code]


@pytest.mark.parametrize(
    ("relative", "text", "code"),
    [
        ("authz.rego", "package system.authz\n", "policy-package"),
        ("system/data.json", "{}", "policy-data"),
        ("data.json", '{"pinka": {}}', "policy-data"),
    ],
)
def test_a_policy_bundle_in_infra_namespaces_is_refused(
    tmp_path: Path, relative: str, text: str, code: str
) -> None:
    # OPA's own authorization and infra's placeholder (§7.10).
    built = write_release(tmp_path, manifest("allprof"))
    path = built / "policy" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    assert problem_codes(built) == [code]


def test_python_uv_needs_pyproject_with_a_locked_build_backend(tmp_path: Path) -> None:
    built = write_release(tmp_path, manifest("refplat"))
    pyproject = built / "pyproject.toml"
    pyproject.write_text("[project]\nname = 'refplat'\n[build-system]\nrequires = ['hatchling']\n")
    assert problem_codes(built) == ["build-backend-unlocked"]
    pyproject.write_text("[project]\nname = 'refplat'\n")  # no build system: nothing to build
    # ...but the agent's package is a workspace member with a build system.
    assert problem_codes(built) == ["build-backend-unlocked"]
    member = built / "packages" / "refplat-agent" / "pyproject.toml"
    member.write_text("[project]\nname = 'refplat-agent'\n")
    assert problem_codes(built) == []
    pyproject.write_text("[project\n")
    assert problem_codes(built) == ["pyproject-invalid"]
    pyproject.unlink()
    assert problem_codes(built) == ["pyproject-missing"]


def test_config_schema_must_be_json(tmp_path: Path) -> None:
    release = write_release(tmp_path, manifest("allprof"))
    (release / "schemas" / "ctl.schema.json").write_text("not json")
    assert problem_codes(release) == ["config-invalid"]
    (release / "schemas" / "ctl.schema.json").unlink()
    assert problem_codes(release) == ["config-missing"]


def test_named_directories_must_exist(tmp_path: Path) -> None:
    release = write_release(tmp_path, manifest("allprof"))
    (release / "policy").rmdir()
    assert problem_codes(release) == ["directory-missing"]


def test_dashboards_hold_json_files_only(tmp_path: Path) -> None:
    release = write_release(tmp_path, manifest("allprof"))
    (release / "dashboards" / "runs.json").write_text("{}")
    (release / "dashboards" / "sub").mkdir()
    (release / "dashboards" / "sub" / "script.sh").write_text("#!/bin/sh\n")
    assert problem_codes(release) == ["dashboards-json"]


def test_manifest_size_limit(tmp_path: Path) -> None:
    release = write_release(tmp_path, manifest("refplat"))
    padding = "#" * int(CONTRACT.caps["manifest_bytes"])
    (release / "platform-manifest.yaml").write_text(dump(manifest("refplat")) + padding)
    assert problem_codes(release) == ["manifest-size"]


def test_manifest_must_be_a_regular_utf8_file(tmp_path: Path) -> None:
    release = write_release(tmp_path, manifest("refplat"))
    path = release / "platform-manifest.yaml"
    path.write_bytes(b"name: \xff\n")
    assert problem_codes(release) == ["manifest-encoding"]
    path.unlink()
    path.symlink_to(tmp_path / "elsewhere.yaml")
    assert problem_codes(release) == ["manifest-missing"]


def test_cli_prints_the_summary(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = manifest(FIRST_PLATFORM)
    release = write_release(tmp_path, data)
    assert cli.main(["validate", str(release), "--no-host-check"]) == cli.EXIT_VALID
    out = capsys.readouterr().out
    assert out.startswith(f"platform {data['name']} 0.1.0 (python-uv)\n")
    broker = next(s["name"] for s in data["services"] if s["profile"] == "inference-broker")
    assert f"socket {broker}/agent: untrusted-agent  [dedicated to the agent]" in out


def test_cli_refuses_with_every_problem(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = manifest("refplat")
    data["services"][1]["secrets"] = ["key"]
    data["services"][1]["relaxations"] = ["wx-memory"]
    release = write_release(tmp_path, data)
    assert cli.main(["validate", str(release), "--no-host-check"]) == cli.EXIT_REFUSED
    err = capsys.readouterr().err
    assert "[secrets-forbidden]" in err
    assert "[relaxation-forbidden]" in err
    assert err.endswith("refused: 2 problem(s)\n")


def test_cli_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    release = write_release(tmp_path, manifest("refplat"))
    assert cli.main(["validate", str(release), "--no-host-check", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["valid"] is True
    assert payload["summary"]["name"] == "refplat"
    (release / "uv.lock").unlink()
    assert cli.main(["validate", str(release), "--no-host-check", "--json"]) == cli.EXIT_REFUSED
    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"] == [
        {
            "code": "uv-lock",
            "where": "uv.lock",
            "message": "runtime python-uv needs uv.lock at the release root",
        }
    ]


def test_cli_checks_the_host(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = manifest("refplat")
    data["cli"]["name"] = "sh"  # on every PATH
    release = write_release(tmp_path, data)
    assert cli.main(["validate", str(release)]) == cli.EXIT_REFUSED
    assert "[name-exists-on-host]" in capsys.readouterr().err
