"""Tests for the pokus-infrastructure generators."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
import yaml
from pinka_lab.cli import build_parser

from pinka_gen import bootstrap, cli, lab, pages, sources

ROOT = Path(__file__).resolve().parents[3]


def test_resolve_lock_refs() -> None:
    lock = {"a": {"b": ["1", "2"]}}
    doc = {"x": "@versions.lock:a.b", "y": ["@versions.lock:a.b", "plain"], "z": {"k": 1}}
    assert sources.resolve_lock_refs(doc, lock) == {
        "x": ["1", "2"],
        "y": [["1", "2"], "plain"],
        "z": {"k": 1},
    }
    with pytest.raises(KeyError):
        sources.resolve_lock_refs("@versions.lock:missing", lock)


def test_committed_files_are_current() -> None:
    assert cli.check(ROOT) == 0


def test_no_generated_file_names_this_checkouts_path() -> None:
    # The drift check runs in other checkouts (CI): a default inside the
    # checkout is shown relative to it, for subcommands too.
    here = str(ROOT.resolve())
    for rel, produce in cli.targets(ROOT).items():
        assert here not in produce(), rel


def test_check_and_generate_detect_drift(tmp_path: Path) -> None:
    for rel in (
        "preflight",
        "docs",
        "bootstrap",
        "ansible",
        "versions.lock.yml",
        "README.md",
        "tests/distros",
        "tests/reference-platform/platform-manifest.yaml",
        "installer/requirements.in",
        "installer/requirements.lock",
        "installer/schema",
        "installer/tests/__snapshots__",
        "contract",
        "compose",
    ):
        src = ROOT / rel
        if src.is_dir():
            shutil.copytree(src, tmp_path / rel)
        else:
            (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, tmp_path / rel)
    stale = tmp_path / "docs/support-matrix.md"
    stale.write_text("edited by hand\n", encoding="utf-8")
    assert cli.check(tmp_path) == 1
    assert cli.generate(tmp_path) == 0
    assert cli.check(tmp_path) == 0


def test_coverage_is_complete_and_detects_gaps(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.coverage(ROOT) == 0
    for rel in (
        "preflight",
        "docs",
        "bootstrap",
        "ansible",
        "tests/distros",
        "installer/schema",
        "contract",
        "compose",
    ):
        shutil.copytree(ROOT / rel, tmp_path / rel)
    ref = tmp_path / "docs/reference/cli/pinka-preflight.md"
    ref.write_text(
        ref.read_text(encoding="utf-8").replace("`--offline`", "offline"), encoding="utf-8"
    )
    fix = tmp_path / "docs/how-to/fix-doctor-checks.md"
    fix.write_text(
        fix.read_text(encoding="utf-8").replace("{#check-time-sync}", ""), encoding="utf-8"
    )
    manifest_page = tmp_path / "docs/reference/platform-manifest.md"
    manifest_page.write_text(
        manifest_page.read_text(encoding="utf-8").replace("`cli.run`", "cli run"), encoding="utf-8"
    )
    assert cli.coverage(tmp_path) == 1
    out = capsys.readouterr().out
    assert "manifest field `cli.run`" in out
    assert "pinka-preflight option --offline" in out
    assert "troubleshooting section for doctor check time.sync" in out


def test_lab_pages_cover_every_command() -> None:
    page = lab.cli_page()
    for name, _, _ in lab.commands(build_parser()):
        assert f"### pinka {name}\n" in page
    assert "`pinka images build`" in page
    assert lab.anchor("lab.health_gate") == "check-lab-health_gate"
    assert "| 2 | `pinka doctor` |" in "".join(f"| {' | '.join(r)} |" for r in lab.exit_code_rows())


def test_table_escapes_pipes() -> None:
    assert pages.table(["a"], [["x|y"]]).splitlines()[2] == "| x\\|y |"


def test_table_escapes_angle_brackets_outside_code_spans() -> None:
    # Markdown shows an entity inside a code span literally.
    text = pages.table(["a"], [["`<sha256>  <path>` per <file>"], ["one ` <b>"]])
    assert "| `<sha256>  <path>` per &lt;file&gt; |" in text
    assert "| one ` &lt;b&gt; |" in text


def test_generated_json_carries_provenance() -> None:
    text = sources.support_matrix_json(ROOT)
    assert '"_generated_from": "preflight/support-matrix.yml' in text
    assert '"@versions.lock' not in text


def test_preflight_gets_the_gpu_lists_of_the_lock() -> None:
    lock = sources.load_yaml(ROOT / "versions.lock.yml")
    data = json.loads(sources.nvidia_gpus_json(ROOT))
    assert data["_generated_from"].startswith("versions.lock.yml")
    releases = lock["nvidia_driver_gpus"]["releases"]
    assert data["drivers"] == {
        str(v): {"open_module": e["open_module"], "proprietary_only": e["proprietary_only"]}
        for v, e in releases.items()
    }
    # The Turing control and the Ada card of the fixtures have the open module; the
    # Pascal card only the proprietary one; the Kepler card neither (legacy branch).
    (lists,) = data["drivers"].values()
    assert {"1E04", "2786"} <= set(lists["open_module"])
    assert "1D01" in lists["proprietary_only"]
    assert "1287" not in lists["open_module"] + lists["proprietary_only"]


def test_bootstrap_pins_come_from_the_lock() -> None:
    lock = sources.jsonable(sources.load_yaml(ROOT / "versions.lock.yml"))
    digest = hashlib.sha256((ROOT / "versions.lock.yml").read_bytes()).hexdigest()
    pins = bootstrap.pins_sh(ROOT)
    assert f"readonly PINKA_LOCK_SHA256={digest}\n" in pins
    assert f"readonly PINKA_UV_VERSION={lock['binaries']['uv']['version']}\n" in pins
    sha = lock["binaries"]["uv"]["assets"]["aarch64"]["sha256"]
    assert f"readonly PINKA_UV_SHA256_AARCH64={sha}\n" in pins
    assert f"readonly PINKA_PYTHON_VERSION={lock['python']['python_version']}\n" in pins
    requirements = [
        line for line in bootstrap.requirements_in(ROOT).splitlines() if not line.startswith("#")
    ]
    packages = lock["python"]["packages"]
    assert requirements == [f"{name}=={packages[name]}" for name in sorted(packages)]
    versions = yaml.safe_load(bootstrap.versions_yml(ROOT))
    assert versions == {"pinka_versions_lock_sha256": digest, "pinka_versions": lock}


def test_versions_yml_indents_sequences() -> None:
    text = bootstrap.versions_yml(ROOT)
    assert "    releases:\n      - '43'\n" in text


def test_bootstrap_usage_and_exit_codes_are_parsed() -> None:
    options = {o.names: o.value for o in bootstrap.options(ROOT)}
    assert options[("--machine",)] == "NAME"
    assert options[("--tags",)] == "T1,T2"
    assert options[("--tui", "--no-tui")] == ""
    assert options[("-h", "--help")] == ""
    codes = bootstrap.exit_codes(ROOT)
    assert len({c.code for c in codes}) == len(codes)
    by_name = {c.name: c for c in codes}
    assert by_name["EXIT_WSL_RESTART"].code == 75
    assert by_name["EXIT_REBOOT"].title == "reboot required"


def test_requirements_lock_must_match_requirements_in(tmp_path: Path) -> None:
    assert bootstrap.requirements_lock_problems(ROOT) == []
    shutil.copy2(ROOT / "versions.lock.yml", tmp_path / "versions.lock.yml")
    (tmp_path / "bootstrap").mkdir()
    lock = (ROOT / "bootstrap/requirements.lock").read_text(encoding="utf-8")
    (tmp_path / "bootstrap/requirements.lock").write_text(
        lock.replace("jmespath==", "jmespath==0.0.0-"), encoding="utf-8"
    )
    problems = bootstrap.requirements_lock_problems(tmp_path)
    assert len(problems) == 1
    assert "jmespath" in problems[0]
    assert "make bootstrap-lock" in problems[0]
