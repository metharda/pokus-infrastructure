"""The helpers of the reference platform's stages: probes, guest helper, renamed copy."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from ansible.parsing.vault import VaultLib, VaultSecret
from pinka_test_distros import guest, isolation, refplat

from pinka_platform import contract as contract_mod
from pinka_platform import validate

CONTRACT = contract_mod.load()
INFRA = Path(__file__).resolve().parents[3]
PORTS = yaml.safe_load((INFRA / "ansible" / "group_vars" / "all" / "main.yml").read_text())[
    "pinka_default_ports"
]
PASSWORD = "vault-test-password"  # noqa: S105 - a test vault


# --- the per-profile probe ------------------------------------------------------------------


def test_the_loopback_profiles_and_what_each_may_reach() -> None:
    profiles = isolation.loopback_profiles(CONTRACT)
    assert profiles == [
        "controller",
        "inference-broker",
        "sandbox-operator",
        "restricted-operator",
        "restricted-reader",
        "observer",
    ]
    reach = {
        p: sorted(t for t, v in isolation.expectations(CONTRACT, p, rendered=True).items() if v)
        for p in profiles
    }
    assert reach == {
        "controller": [
            "gpu arbiter socket",
            "inference api",
            "query proxy",
            "run-network helper socket",
        ],
        "inference-broker": ["inference api"],
        "sandbox-operator": ["query proxy"],
        "restricted-operator": [],
        "restricted-reader": ["query proxy"],
        "observer": ["query proxy"],
    }
    # An account the rendering did not make never passes the account-based guards.
    unrendered = isolation.expectations(CONTRACT, "controller", rendered=False)
    assert {t for t, v in unrendered.items() if v} == {"query proxy"}


def test_the_plan_uses_rendered_hardening_and_groups() -> None:
    probes = {p.profile: p for p in isolation.plan(refplat.manifest(), CONTRACT, PORTS)}
    controller = dict(probes["controller"].properties)
    assert (probes["controller"].account, probes["controller"].temporary) == ("refplat-ctl", False)
    assert controller["User"] == "refplat-ctl"
    assert controller["IPAddressDeny"] == "any"
    assert controller["IPAddressAllow"] == "localhost"
    assert set(controller["SupplementaryGroups"].split()) == {
        "pinka-query",
        "pinka-restricted",
        "refplat-db",
    }
    assert not set(controller) & isolation.UNIT_ONLY
    observer = probes["observer"]
    assert (observer.account, observer.temporary) == ("refplat-probe", True)
    groups = dict(observer.properties)["SupplementaryGroups"].split()
    assert "pinka-query" in groups and "pinka-restricted" not in groups
    reader = dict(probes["restricted-reader"].properties)["SupplementaryGroups"].split()
    assert "pinka-restricted" in reader


def test_the_command_carries_the_probe_untouched() -> None:
    probe = isolation.plan(refplat.manifest(), CONTRACT, PORTS)[5]
    argv = isolation.command(probe, "refplat", "pinka-test-probe-observer")
    assert argv[:7] == [
        "systemd-run",
        "--quiet",
        "--wait",
        "--pipe",
        "--collect",
        "--service-type=exec",
        "--unit=pinka-test-probe-observer",
    ]
    assert "--property" in argv
    tail = argv[argv.index("--") + 1 :]
    assert tail[:3] == ["/opt/pinka/python/bin/python3", "-I", "-c"]
    assert tail[4:] == ["--profile", "observer", "--platform", "refplat"]
    encoded = tail[3].split("'")[1]
    assert base64.b64decode(encoded).decode() == isolation.PROBE_SOURCE.read_text()
    assert "$" not in tail[3] and "%" not in tail[3]


def test_the_command_runs_the_probe(tmp_path: Path) -> None:
    probe = isolation.plan(refplat.manifest(), CONTRACT, PORTS)[5]
    code = isolation.command(probe, "refplat", "unit")[-5]
    absent = ["--endpoints", str(tmp_path / "a.toml"), "--principals", str(tmp_path / "a.json")]
    done = subprocess.run(
        [sys.executable, "-I", "-c", code, "--profile", "observer", *absent],
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
    )
    report = refplat.last_json(done.stdout)
    assert report["profile"] == "observer"
    assert set(isolation.COMPARED) <= {o["name"] for o in report["outcomes"]}


def test_compare_names_every_difference() -> None:
    probe = isolation.plan(refplat.manifest(), CONTRACT, PORTS)[0]
    outcomes = [
        {"name": n, "reached": v, "detail": "d"}
        for n, v in probe.expected.items()
        if n != "run-network helper socket"
    ]
    report = {"profile": "controller", "outcomes": outcomes}
    assert isolation.compare(probe, report) == [
        "controller: run-network helper socket was not tried"
    ]
    outcomes[0]["reached"] = True
    outcomes[3]["reached"] = False
    assert isolation.compare(probe, {"profile": "observer", "outcomes": outcomes}) == [
        "controller: the report is for 'observer'",
        "controller reached prometheus direct (d)",
        "controller must reach query proxy (d)",
        "controller: run-network helper socket was not tried",
    ]
    assert isolation.agent_problems({}) == ["the agent has not reported its probe"]


def test_the_temporary_account_comes_from_the_platform_range() -> None:
    probe = isolation.plan(refplat.manifest(), CONTRACT, PORTS)[1]
    create, remove = isolation.account_script(probe)
    assert "seq 60578 61183" in create
    assert "useradd --uid" in create and create.endswith("refplat-probe")
    assert remove.startswith("userdel refplat-probe")


# --- the stage helpers ----------------------------------------------------------------------


def test_the_renamed_copy_is_a_valid_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "pinka"
    for path in (refplat.RELEASE_PATH, refplat.CASES_PATH):
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["cp", "-a", str(INFRA.parent / path), str(repo / path)], check=True)
    monkeypatch.setattr(refplat, "GUEST_REPO", str(repo))
    monkeypatch.setattr(refplat, "RENAMED_REPO", str(tmp_path / "renamed"))
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    done = subprocess.run(
        ["bash", "-c", refplat.renamed_copy_script()],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    commit = done.stdout.strip().splitlines()[-1]
    assert len(commit) == 40
    release = tmp_path / "renamed" / "release"
    result = validate.validate_release(release, CONTRACT)
    assert [p.message for p in result.problems] == []
    assert result.manifest is not None and result.manifest["name"] == refplat.RENAMED_NAME
    assert refplat.renamed(refplat.manifest()) == result.manifest
    assert (tmp_path / "renamed" / "cases" / "cases.jsonl").is_file()


def test_enable_passes_names_never_values() -> None:
    argv = refplat.enable(
        "/hv.yml",
        "/vault.yml",
        "/pass",
        platform=refplat.source("file:///root/pinka", "0" * 40, refplat.RELEASE_PATH),
        content={},
        data=refplat.manifest(),
        contract=CONTRACT,
    )
    assert argv[:4] == [refplat.GUEST_PYTHON, "-I", refplat.GUEST_HELPER, "enable-platform"]
    assert argv.count("--postgres-role") == 4
    assert argv[argv.index("--secret") + 1] == "ctl/api-token"


def test_standin_and_transitions() -> None:
    script = refplat.standin_script(18704)
    assert "sport = :18704" in script and f"--unit={refplat.STANDIN_UNIT}" in script
    log = "\n".join(
        json.dumps(x)
        for x in (
            {"run": "a", "outcome": "ok"},
            {"run": "b", "outcome": "suspended"},
            {"run": "b", "outcome": "resumed"},
        )
    )
    assert refplat.run_outcomes(log + "\nnot json\n", "b") == ["suspended", "resumed"]
    assert refplat.last_json('x\n{"a": 1}\n{"b": 2}\nend') == {"b": 2}
    with pytest.raises(ValueError, match="no JSON"):
        refplat.last_json("nothing")


# --- the guest helper -----------------------------------------------------------------------


def write_vault(path: Path, data: dict[str, Any]) -> VaultLib:
    lib = VaultLib([("default", VaultSecret(PASSWORD.encode()))])
    path.write_bytes(lib.encrypt(yaml.safe_dump(data).encode()))
    return lib


def test_enable_platform_keeps_existing_secrets(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    host_vars = tmp_path / "hv.yml"
    host_vars.write_text("machine_name: lab\nplatform: {}\n")
    vault = tmp_path / "vault.yml"
    lib = write_vault(vault, {"vault_postgres_passwords": {"refplat_ctl": "kept"}, "other": 1})
    (tmp_path / "pass").write_text(PASSWORD + "\n")
    platform = {"repo": "file:///root/pinka", "ref": "0" * 40, "path": "x"}
    argv = [
        "enable-platform",
        "--host-vars",
        str(host_vars),
        "--vault",
        str(vault),
        "--vault-password-file",
        str(tmp_path / "pass"),
        "--platform",
        json.dumps(platform),
        "--settings",
        json.dumps({"ctl": {"job": "tunecpu"}}),
        "--postgres-role",
        "refplat_ctl",
        "--postgres-role",
        "refplat_migrator",
        "--secret",
        "ctl/api-token",
    ]
    assert guest.main(argv) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {
        "added": [
            "vault_postgres_passwords.refplat_migrator",
            "vault_platform_secrets.ctl.api-token",
        ],
        "ok": True,
    }
    hv = yaml.safe_load(host_vars.read_text())
    assert hv == {
        "machine_name": "lab",
        "platform": platform,
        "content": {},
        "platform_settings": {"ctl": {"job": "tunecpu"}},
    }
    stored = yaml.safe_load(lib.decrypt(vault.read_bytes()))
    assert stored["vault_postgres_passwords"]["refplat_ctl"] == "kept"
    new = stored["vault_postgres_passwords"]["refplat_migrator"]
    assert len(new) >= 32 and new not in json.dumps(out)
    assert stored["vault_platform_secrets"]["ctl"]["api-token"]
    assert stored["other"] == 1
    assert vault.stat().st_mode & 0o777 == 0o600
    # A second run adds nothing and leaves the vault as it is.
    before = vault.read_bytes()
    assert guest.main(argv) == 0
    assert json.loads(capsys.readouterr().out)["added"] == []
    assert vault.read_bytes() == before


def test_set_host_vars_and_the_waits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    host_vars = tmp_path / "hv.yml"
    host_vars.write_text("a: 1\n")
    values = json.dumps({"restore_snapshot": "latest"})
    assert guest.main(["set-host-vars", "--host-vars", str(host_vars), "--values", values]) == 0
    assert yaml.safe_load(host_vars.read_text()) == {"a": 1, "restore_snapshot": "latest"}
    capsys.readouterr()
    jobs = tmp_path / "jobs"
    assert guest.checkpoint("r1", 3, jobs) is None
    (jobs / "r1" / "checkpoints").mkdir(parents=True)
    (jobs / "r1" / "checkpoints" / "state.json").write_text('{"step": 2, "state": "x"}')
    assert guest.checkpoint("r1", 3, jobs) is None
    (jobs / "r1" / "checkpoints" / "state.json").write_text('{"step": 3, "state": "x"}')
    assert guest.checkpoint("r1", 3, jobs) == {"step": 3, "state": "x"}
    answers = iter(
        [
            {"ok": True, "runs": {"r1": {"status": "running"}}},
            {"ok": True, "runs": {"r1": {"status": "failed"}}},
        ]
    )
    monkeypatch.setattr(guest, "cli", lambda *_a: next(answers))
    monkeypatch.setattr(guest, "INTERVAL", 0.0)
    assert guest.main(["wait-run", "r1", "--timeout", "60"]) == 1
    assert json.loads(capsys.readouterr().out) == {"ok": True, "run": "r1", "status": "failed"}
    monkeypatch.setattr(guest, "cli", lambda *_a: {"ok": True, "report": {}})
    assert guest.main(["wait-report", "--timeout", "0"]) == 1
    assert "no probe report" in capsys.readouterr().out
