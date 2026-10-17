"""Tests of the distribution test harness, against a fake ``incus``."""

from __future__ import annotations

import datetime as dt
import json
import os
import shlex
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest
import yaml
from pinka_test_distros import checkmode, cli, harness, isolation, refplat, results, targets
from pinka_test_distros.incus import Incus, Result

from pinka_platform import contract as contract_mod
from pinka_platform import render
from pinka_preflight.detect.os_release import OsRelease
from pinka_preflight.matrix import load_matrix, match_os

INFRA = Path(__file__).resolve().parents[3]
AUTHKEY = "placeholder-auth-key-for-tests"
GOOD_LOG = "PLAY RECAP *****\nlocalhost : ok=500 changed=0 unreachable=0 failed=0 skipped=9\n"
CONTRACT = contract_mod.load()
ADAPTER = "c" * 64
RENAMED_COMMIT = "1" * 40
PORTS = yaml.safe_load((INFRA / "ansible" / "group_vars" / "all" / "main.yml").read_text())[
    "pinka_default_ports"
]


def endpoints_text(name: str) -> str:
    """The endpoints.toml infra renders for the reference platform under a name."""
    data = {**refplat.manifest(), "name": name}
    facts = render.LabFacts(machine_name="ptd", lab_mode="staging", node_role="full", ports=PORTS)
    return render.endpoints_toml(render.endpoints(data, facts))


def target_file() -> targets.TargetFile:
    return targets.load(INFRA / "tests" / "distros" / "targets.yml")


# --- the targets ----------------------------------------------------------------------------------


def test_every_supported_and_tested_release_has_a_target() -> None:
    matrix = load_matrix()
    covered = set()
    for t in target_file().targets:
        rule = match_os(matrix, OsRelease(id=t.os_id, version_id=t.version_id))
        assert rule["tier"] in ("supported", "tested"), (t.id, rule["tier"])
        covered.add(json.dumps(rule["match"], sort_keys=True))
    for rule in matrix["os"]:
        if rule["tier"] in ("supported", "tested"):
            assert json.dumps(rule["match"], sort_keys=True) in covered, rule["match"]


def test_variants() -> None:
    all_targets = target_file().targets
    assert {t.firewall for t in all_targets} == set(targets.FIREWALLS)
    enforcing = {t.os_id for t in all_targets if t.selinux == "enforcing"}
    assert enforcing == {"fedora", "almalinux"}
    for t in all_targets:
        name = f"{t.vm_name_stem}-abcdef"
        assert len(name) <= 63, t.id
        assert name.replace("-", "").isalnum(), t.id


def test_targets_file_is_checked(tmp_path: Path) -> None:
    path = tmp_path / "targets.yml"
    good = {"id": "x-1", "image": "a/b", "os": {"id": "ubuntu"}, "firewall": "ufw"}
    defaults = {"remote": "images", "cpus": 2, "memory": "4GiB", "disk": "20GiB"}
    path.write_text(yaml.safe_dump({"schema_version": 1, "defaults": defaults, "targets": [good]}))
    loaded = targets.load(path)
    assert loaded.unverified is None
    assert loaded.targets[0].image_ref == "images:a/b"
    with pytest.raises(targets.TargetError, match="unknown"):
        loaded.select(["nope"])
    for bad in ({**good, "firewall": "iptables"}, {**good, "os": {"id": "gentoo"}}):
        doc = {"schema_version": 1, "defaults": defaults, "targets": [bad]}
        path.write_text(yaml.safe_dump(doc))
        with pytest.raises(targets.TargetError, match="invalid"):
            targets.load(path)
    path.write_text(
        yaml.safe_dump({"schema_version": 1, "defaults": defaults, "targets": [good, good]})
    )
    with pytest.raises(targets.TargetError, match="duplicate"):
        targets.load(path)


def test_answers_template() -> None:
    template = INFRA / "tests" / "answers" / "ci.yml"
    data = yaml.safe_load(template.read_text())
    assert data["machine"]["lab_mode"] == "staging"  # test VMs never join a production lab
    assert data["gpu"]["default_model"] == "none"
    rendered = yaml.safe_load(harness.render_answers(template, "ptd-debian-13-abcdef"))
    assert rendered["machine"]["name"] == "ptd-debian-13-abcdef"
    assert rendered["backups"] == data["backups"]


def test_changed_tasks() -> None:
    assert harness.changed_tasks(GOOD_LOG) == 0
    two = GOOD_LOG + "PLAY RECAP\nlocalhost : ok=3 changed=2 unreachable=0 failed=0\n"
    assert harness.changed_tasks(two) == 2
    assert harness.changed_tasks("no recap here") is None


# --- the pipeline ---------------------------------------------------------------------------------


class FakeIncus:
    """Answers incus commands for one VM; ``exec`` answers come from ``scripts``."""

    def __init__(self, tmp: Path, **behaviour: Any) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.pushed: dict[str, tuple[str, str]] = {}
        self.tmp = tmp
        self.behaviour = {
            "launch": 0,
            "getenforce": "Enforcing",
            "setenforce": 0,
            "bootstrap": 1,
            "backend": "nftables",
            "second_log": GOOD_LOG,
            "doctor": 0,
            "agent_reached": [],
            "leak": None,
            "models": [refplat.STANDIN_MODEL],
            "transitions": ["suspended", "resumed", "ok"],
            "replaced": True,
            "infra_source": "package",
            **behaviour,
        }
        self.infra_source = self.behaviour["infra_source"]
        self.runs = 0
        self.platform = "refplat"
        self.restarts = 0
        self.standins = 0

    def __call__(self, argv: Sequence[str], *, timeout: float | None = None) -> Result:
        args = tuple(argv)
        self.calls.append(args)
        rc, out = self.answer(args)
        return Result(args, rc, out, "" if rc == 0 else "simulated failure")

    def answer(self, args: tuple[str, ...]) -> tuple[int, str]:
        b = self.behaviour
        verb = args[1]
        if verb == "launch":
            return int(b["launch"]), ""
        if verb == "query":
            return 0, json.dumps({"config": {"volatile.base_image": "f" * 64}})
        if verb == "file" and args[2] == "push":
            local, dest = args[-2], args[-1]
            self.pushed[dest.split("/", 1)[1]] = (
                Path(local).read_text(),
                args[args.index("--mode") + 1],
            )
            return 0, ""
        if verb == "file" and args[2] == "pull":
            return self.pull(args[-2].split("/", 1)[1], Path(args[-1]))
        if verb == "exec":
            return self.exec(args[args.index("--") + 1 :])
        if verb == "restart":
            self.restarts += 1
        return 0, ""

    def pull(self, remote: str, local: Path) -> tuple[int, str]:
        if remote == "etc/pinka/bootstrap-state.json":
            local.write_text(
                json.dumps(
                    {
                        "log_file": f"/var/log/run-{self.runs}.log",
                        "result": "ok",
                        "infra_source": self.infra_source,
                    }
                )
            )
        elif remote.startswith("var/log/run-"):
            local.write_text(
                GOOD_LOG if remote.endswith("1.log") else str(self.behaviour["second_log"])
            )
        elif remote == "etc/pinka/host-profile.json":
            local.write_text(json.dumps({"firewall": {"backend": self.behaviour["backend"]}}))
        elif remote == refplat.ACTIVE[1:]:
            local.write_text(json.dumps({"name": self.platform}))
        elif remote == refplat.ENDPOINTS[1:]:
            local.write_text(endpoints_text(self.platform))
        elif remote == refplat.LAB[1:]:
            health = f"http://127.0.0.1:{PORTS['vllm']}/health"
            local.write_text(json.dumps({"vllm": {"health_url": health}}))
        elif remote == refplat.TRANSITIONS[1:]:
            lines = [
                json.dumps({"run": "run-resume", "outcome": o})
                for o in self.behaviour["transitions"]
            ]
            local.write_text("\n".join(['{"run": "other", "outcome": "ok"}', *lines]) + "\n")
        else:
            return 1, ""
        return 0, ""

    def exec(self, command: tuple[str, ...]) -> tuple[int, str]:  # noqa: PLR0911 - one answer per command
        if command[0] == refplat.CLI:
            return self.cli(command[1:])
        if command[0] == refplat.GUEST_PYTHON:
            return self.guest(command[3:])
        if command[0] == "systemd-run":
            return self.probe(command)
        script = command[-1]
        b = self.behaviour
        if "git rev-parse HEAD" in script:
            return 0, f"{RENAMED_COMMIT}\n"
        if refplat.STANDIN_UNIT in script and "enable --now" in script:
            self.standins += 1
            return 0, "started\n" if self.standins == 1 else "running\n"
        if "--replace-platform" in script and b["replaced"]:
            self.platform = refplat.RENAMED_NAME
        if script == "getenforce":
            return 0, f"{b['getenforce']}\n"
        if script.startswith("setenforce"):
            return int(b["setenforce"]), ""
        if "bootstrap.sh" in script:
            self.runs += 1
            return int(b["bootstrap"]), ""
        if "doctor --smoke" in script:
            return int(b["doctor"]), ""
        return 0, ""

    def cli(self, args: tuple[str, ...]) -> tuple[int, str]:
        answers: dict[str, dict[str, Any]] = {
            "status": {"ok": True, "database": "ok"},
            "dataset": {"ok": True, "dataset": "a" * 64},
            "lineage": {"ok": True, "lineage": {"adapter": {"sha256": ADAPTER}}},
        }
        if args[0] == "infer":
            return 0, json.dumps({"ok": True, "models": self.behaviour["models"]}) + "\n"
        if args[0] == "train":
            if "--wait" in args:
                answer = {"ok": True, "run": "run-train", "status": "succeeded", "adapter": ADAPTER}
            else:
                answer = {"ok": True, "run": "run-resume"}
            return 0, json.dumps(answer) + "\n"
        return 0, json.dumps(answers[args[0]]) + "\n"

    def guest(self, args: tuple[str, ...]) -> tuple[int, str]:
        if args[0] == "wait-report":
            outcomes = [
                {"name": n, "reached": n in self.behaviour["agent_reached"], "detail": "x"}
                for n in ("restricted area", "outside tcp")
            ]
            return 0, json.dumps({"ok": True, "outcomes": outcomes}) + "\n"
        return 0, json.dumps({"ok": True, "status": "succeeded", "step": 3}) + "\n"

    def probe(self, command: tuple[str, ...]) -> tuple[int, str]:
        profile = command[command.index("--profile") + 1]
        user = next(a for a in command if a.startswith("User="))
        expected = isolation.expectations(CONTRACT, profile, rendered=user != "User=refplat-probe")
        outcomes = [
            {
                "name": n,
                "reached": wanted != ((profile, n) == self.behaviour["leak"]),
                "detail": "x",
            }
            for n, wanted in expected.items()
        ]
        report = {"profile": profile, "reached": [], "outcomes": outcomes}
        return 0, "noise\n" + json.dumps(report) + "\n"

    def scripts(self) -> list[str]:
        return [c[-1] for c in self.calls if c[1] == "exec"]


def make_test(
    tmp_path: Path, target_id: str, keep: str = "failed", **behaviour: Any
) -> tuple[harness.Test, FakeIncus]:
    fake = FakeIncus(tmp_path, **behaviour)
    incus = Incus(fake, "pinka-test-distros", sleep=lambda _s: None)
    tar = tmp_path / "pinka.tar"
    tar.write_text("tar")
    package = tmp_path / "package-0.0.0+000000000000.tar.gz"
    package.write_text("package")
    secrets_env = tmp_path / "secrets.env"
    secrets_env.write_text(f"PINKA_SECRET_TAILSCALE_AUTHKEY={AUTHKEY}\n")
    times = iter(
        [dt.datetime(2026, 1, 1, 10, tzinfo=dt.UTC), dt.datetime(2026, 1, 1, 11, 30, tzinfo=dt.UTC)]
    )
    settings = harness.Settings(
        repo_tar=tar,
        package=package,
        answers=INFRA / "tests" / "answers" / "ci.yml",
        commit="0" * 40,
        out_dir=tmp_path / "out",
        secrets_env=secrets_env,
        keep=keep,
        now=lambda: next(times),
        token=lambda: "abcdef",
    )
    target = next(t for t in target_file().targets if t.id == target_id)
    return harness.Test(target, incus, settings), fake


def test_the_vm_bootstraps_from_the_release_package(tmp_path: Path) -> None:
    # Owner decisions of 3d.7 and 3d.8: make test-distros checks the bundled
    # collections by bootstrapping from a release package of the commit.
    test, fake = make_test(tmp_path, "debian-13")
    test.run()
    package = harness.GUEST_PACKAGE.lstrip("/")
    assert fake.pushed[package][0] == "package"
    unpack = next(s for s in fake.scripts() if "--strip-components=1" in s)
    assert f"test -f {harness.GUEST_INFRA}/release/RELEASE.json" in unpack
    assert all(
        f"cd {harness.GUEST_INFRA} && ./bootstrap/bootstrap.sh" in s
        for s in fake.scripts()
        if "bootstrap.sh" in s
    )
    (tmp_path / "checkout").mkdir()
    test, _ = make_test(tmp_path / "checkout", "debian-13", infra_source="checkout")
    record = test.run()
    assert record["stages"]["bootstrap"] == "failed"
    assert "bootstrap ran from checkout, not the package" in record["error"]


def test_a_passing_test(tmp_path: Path) -> None:
    test, fake = make_test(tmp_path, "debian-13")
    record = test.run()
    assert record == {
        "target": "debian-13",
        "image": "images:debian/13/cloud",
        "image_fingerprint": "f" * 64,
        "tested_on": "2026-01-01",
        "commit": "0" * 40,
        "stages": dict.fromkeys(harness.STAGES, "passed"),
        "changed_on_second_run": 0,
        "error": None,
        "duration_minutes": 90,
    }
    launch = next(c for c in fake.calls if c[1] == "launch")
    assert launch[2:5] == ("images:debian/13/cloud", "ptd-debian-13-abcdef", "--vm")
    assert launch[-2:] == ("--device", "root,size=60GiB")
    scripts = fake.scripts()
    assert any(harness.DISABLE_MANAGERS in s for s in scripts)
    first, second, deploy, redeploy, replace, restore = [s for s in scripts if "bootstrap.sh" in s]
    answers_option = (
        "--answers /root/pokus-infrastructure/installer-answers/ptd-debian-13-abcdef.yml"
    )
    assert answers_option in first
    assert "--accept-degraded" in first and "--answers" not in second
    assert second.endswith("--machine ptd-debian-13-abcdef") and redeploy == second
    # The platform is enabled, replaced and restored through the answers.
    assert deploy.endswith(answers_option) and restore.endswith(answers_option)
    assert replace.endswith(f"{answers_option} --replace-platform refplat")
    assert any(s == "pinka backup now" for s in scripts)
    # Secrets travel in 0600 files, never on a command line.
    assert all(AUTHKEY not in " ".join(c) for c in fake.calls)
    assert fake.pushed["root/ci-secrets.env"][1] == "0600"
    assert fake.pushed["root/ci-vault-pass"][1] == "0600"
    answers = yaml.safe_load(
        fake.pushed["root/pokus-infrastructure/installer-answers/ptd-debian-13-abcdef.yml"][0]
    )
    assert answers["machine"]["name"] == "ptd-debian-13-abcdef"
    # Deleted after leaving the tailnet.
    names = [c[1:3] for c in fake.calls[-2:]]
    assert names == [("exec", "--project"), ("delete", "--project")]
    assert not test.kept
    assert (tmp_path / "out" / "debian-13" / "host-profile.json").exists()


def test_the_reference_platform_stages(tmp_path: Path) -> None:
    test, fake = make_test(tmp_path, "debian-13")
    assert test.run()["error"] is None
    out = tmp_path / "out" / "debian-13"
    first, second, restored = (
        yaml.safe_load((out / f"{stage}-answers.yml").read_text())
        for stage in ("platform", "replace", "restore")
    )
    assert first["mode"] == second["mode"] == "modify"
    assert first["machine"]["name"] == "ptd-debian-13-abcdef"
    assert first["platform"] == {
        "path": "tests/reference-platform",
        "ref": "0" * 40,
        "repo": "file:///root/pinka",
    }
    assert first["content"]["cases"]["path"] == "tests/reference-platform-cases"
    assert first["platform_settings"] == {"ctl": {"job": "tunecpu"}}
    # The installer generates the platform's secrets: none in the answers.
    assert "platform" not in first["secrets"]
    assert second["platform"] == {
        "path": "release",
        "ref": RENAMED_COMMIT,
        "repo": "file:///root/refplat-renamed",
    }
    # The restore keeps the replaced platform and restores the latest snapshot.
    assert restored["mode"] == "restore" and restored["restore"] == {"snapshot": "latest"}
    assert restored["platform"] == second["platform"]
    # One probe unit per loopback-capable profile; the controller and the
    # broker as their own accounts, the others as a temporary one.
    probes = [c for c in fake.calls if c[1] == "exec" and "systemd-run" in c]
    users = [next(a for a in c if a.startswith("User=")) for c in probes]
    own = {"controller": "User=refplat-ctl", "inference-broker": "User=refplat-broker"}
    assert users == [
        own.get(profile, "User=refplat-probe") for profile in isolation.loopback_profiles(CONTRACT)
    ]
    scripts = fake.scripts()
    assert sum("useradd" in x for x in scripts) == sum("userdel" in x for x in scripts) == 4
    # The stand-in serves the inference API before the platform is deployed,
    # as an enabled unit that outlives the reboot; it is never stopped.
    standin = next(i for i, x in enumerate(scripts) if refplat.STANDIN_UNIT in x)
    platform_bootstrap = [i for i, x in enumerate(scripts) if "bootstrap.sh" in x][2]
    assert standin < platform_bootstrap
    assert not any(f"systemctl stop {refplat.STANDIN_UNIT}" in x for x in scripts)
    assert fake.standins == 2  # the platform stage, then the isolation stage finds it running
    assert any(c[-2:] == (refplat.CLI, "infer") for c in fake.calls)
    reports = json.loads((tmp_path / "out" / "debian-13" / "isolation.json").read_text())
    assert [r["profile"] for r in reports] == isolation.loopback_profiles(CONTRACT)
    assert fake.restarts == 1
    assert any("--step-seconds" in c for c in fake.calls)
    record = "/srv/pinka/platforms/refplatb/state/ctl/records/datasets.json"
    assert scripts.index(f"rm -f {record}") < scripts.index(f"test -s {record}")
    assert not any("set-host-vars" in c or "enable-platform" in c for c in fake.calls)


@pytest.mark.parametrize(
    ("behaviour", "stage", "message"),
    [
        ({"agent_reached": ["outside tcp"]}, "isolation", "untrusted-agent reached outside tcp"),
        ({"leak": ("observer", "prometheus direct")}, "isolation", "observer reached prometheus"),
        ({"leak": ("controller", "inference api")}, "isolation", "controller must reach inference"),
        (
            {"leak": ("inference-broker", "inference api")},
            "isolation",
            "inference-broker must reach inference",
        ),
        ({"models": []}, "platform", "the broker did not get the inference API's models"),
        ({"transitions": ["ok"]}, "resume", "was not suspended and resumed"),
        ({"replaced": False}, "replace", "the active platform is refplat, expected refplatb"),
    ],
)
def test_reference_platform_failures(
    tmp_path: Path, behaviour: dict[str, Any], stage: str, message: str
) -> None:
    test, _ = make_test(tmp_path, "debian-13", keep="never", **behaviour)
    record = test.run()
    assert record["stages"][stage] == "failed"
    assert message in record["error"], record["error"]


def test_a_second_run_that_changes_something(tmp_path: Path) -> None:
    changed = "PLAY RECAP\nlocalhost : ok=500 changed=2 unreachable=0 failed=0\n"
    test, fake = make_test(tmp_path, "debian-13", second_log=changed)
    record = test.run()
    assert record["stages"]["idempotent"] == "failed"
    assert record["stages"]["smoke"] == "skipped"
    assert record["changed_on_second_run"] == 2
    assert "changed 2 task(s)" in record["error"]
    assert test.kept  # kept for inspection after a failure
    assert not any(c[1] == "delete" for c in fake.calls)
    assert results.status(record) == "failed: idempotent"


@pytest.mark.parametrize(
    ("target_id", "behaviour", "stage", "message"),
    [
        ("ubuntu-24.04-ufw", {"backend": "nftables"}, "bootstrap", "firewall backend nftables"),
        (
            "fedora-43",
            {"getenforce": "Permissive", "setenforce": 1, "backend": "firewalld"},
            "prepare",
            "SELinux",
        ),
        ("debian-13", {"bootstrap": 2}, "bootstrap", "bootstrap ended"),
        ("debian-13", {"doctor": 1}, "smoke", "doctor --smoke"),
    ],
)
def test_failures(
    tmp_path: Path, target_id: str, behaviour: dict[str, Any], stage: str, message: str
) -> None:
    test, _ = make_test(tmp_path, target_id, keep="never", **behaviour)
    record = test.run()
    assert record["stages"][stage] == "failed"
    assert message in record["error"]
    assert not test.kept


def test_variant_preparation(tmp_path: Path) -> None:
    test, fake = make_test(tmp_path, "fedora-43", backend="firewalld", getenforce="Permissive")
    assert test.run()["stages"]["prepare"] == "passed"
    scripts = fake.scripts()
    assert "dnf install -y firewalld" in scripts
    assert "systemctl enable --now firewalld" in scripts
    assert any(s.startswith("setenforce 1") for s in scripts)


def test_a_failed_launch_leaves_nothing_to_clean(tmp_path: Path) -> None:
    test, fake = make_test(tmp_path, "archlinux", launch=1)
    record = test.run()
    assert record["stages"]["launch"] == "failed"
    assert record["image_fingerprint"] is None
    assert [c[1] for c in fake.calls] == ["launch"]


# --- results and the command line ---------------------------------------------------------------


def record(target: str, stages: dict[str, str], day: str = "2026-01-01") -> dict[str, Any]:
    return {"target": target, "tested_on": day, "stages": stages}


def test_results_file(tmp_path: Path) -> None:
    path = tmp_path / "results.yml"
    passed = dict.fromkeys(harness.STAGES, "passed")
    results.update(path, [record("b", passed)], ["a", "b"])
    results.update(path, [record("a", {**passed, "smoke": "failed"})], ["a", "b"])
    loaded = results.load(path)
    assert [r["target"] for r in loaded] == ["a", "b"]
    assert path.read_text().startswith("# Managed by pinka (tests/distros/results.yml).")
    assert results.status(loaded[0]) == "failed: smoke"
    assert results.status(loaded[1]) == "passed"
    assert results.status(None) == "not tested"
    assert results.valid_until(loaded[1]) == "2026-04-01"
    committed = results.load(INFRA / "tests" / "distros" / "results.yml")
    ids = {t.id for t in target_file().targets}
    assert {r["target"] for r in committed} <= ids


def run_cli(argv: list[str], runner: Callable[..., Result] | None = None) -> int:
    return cli.main(argv, run=runner or (lambda argv, timeout=None: Result(tuple(argv), 1, "", "")))


def test_cli_refusals(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv(cli.AUTHKEY_ENV, raising=False)
    assert run_cli(["run", "--target", "debian-13"]) == 2
    assert "VM image aliases" in capsys.readouterr().err
    assert run_cli(["run", "--target", "debian-13", "--accept-unverified-images"]) == 2
    assert cli.AUTHKEY_ENV in capsys.readouterr().err
    assert run_cli(["run", "--target", "nope"]) == 2
    assert "unknown target" in capsys.readouterr().err


def test_cli_list(capsys: pytest.CaptureFixture[str]) -> None:
    assert run_cli(["list"]) == 0
    out = capsys.readouterr().out
    assert "fedora-43" in out and "SELinux enforcing" in out


def test_lab_running(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    active = lambda argv, timeout=None: Result(tuple(argv), 0, "", "")  # noqa: E731
    assert cli.lab_running(active) is Path("/etc/pinka/lab.json").exists()


# --- the CI fast lane -----------------------------------------------------------------------------


NO_SLEEP = lambda _seconds: None  # noqa: E731
PROFILE = {
    "schema_version": 1,
    "provider": None,
    "family": "debian",
    "release_model": "fixed",
    "os": {"id": "debian", "version_id": "13", "codename": "trixie", "parent_release": ""},
    "outcome": "unsupported",
    "overrides": {"force_unsupported": False},
}


class FakeDocker:
    """Answers docker and ansible-playbook for one fast-lane run."""

    def __init__(
        self,
        profile: dict[str, Any] | None = None,
        ansible_rc: int = 0,
        preflight_crash: str = "",
    ) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.profile = PROFILE if profile is None else profile
        self.ansible_rc = ansible_rc
        self.preflight_crash = preflight_crash
        self.boot = iter(["starting", "", "degraded"])

    def __call__(self, argv: Sequence[str], *, timeout: float | None = None) -> Result:
        args = tuple(argv)
        self.calls.append(args)
        out, rc = "", 0
        if args[0] == "ansible-playbook":
            rc, out = self.ansible_rc, "localhost : ok=10 changed=4 unreachable=0 failed=0"
        elif args[1] == "exec" and "is-system-running" in args:
            out = next(self.boot)
        elif args[1] == "exec" and args[3] == "sh":
            out = "/usr/bin/python3\n"
        elif args[1] == "exec" and "pinka_preflight" in args:
            if self.preflight_crash:
                return Result(args, 1, "", self.preflight_crash)
            rc, out = 20, json.dumps(self.profile)
        return Result(args, rc, out, "")


def test_check_mode_run(tmp_path: Path) -> None:
    """The required run: the provider-agnostic roles, nothing skipped."""
    fake = FakeDocker()
    printed: list[str] = []
    rc = checkmode.main("debian_13", fake, tmp_path, write=printed.append, sleep=NO_SLEEP)
    assert rc == 0
    verbs = [c[1] if c[0] == "docker" else c[0] for c in fake.calls]
    assert verbs[:3] == ["build", "rm", "run"]
    assert verbs[-2:] == ["ansible-playbook", "rm"]
    build = fake.calls[0]
    lock = yaml.safe_load((INFRA / "versions.lock.yml").read_text())
    pinned = lock["containers"]["ci_images"]["debian_13"]
    assert f"BASE={pinned['image']}@{pinned['digest']}" in build
    play = next(c for c in fake.calls if c[0] == "ansible-playbook")
    assert "--check" in play and play[-1].endswith("ansible/check-mode.yml")
    # The trees the play reads on the target go to the same paths as on the controller,
    # outside /tmp, a tmpfs that docker cp cannot write into.
    mkdir = next(i for i, c in enumerate(fake.calls) if c[1:2] == ("exec",) and c[3] == "mkdir")
    assert {"/opt/pinka-ci", str(INFRA / "tools")} <= set(fake.calls[mkdir][4:])
    copies = [c[-1] for c in fake.calls if c[:2] == ("docker", "cp")]
    assert copies == [
        f"pinka-ci-debian-13:{INFRA / 'preflight'}",
        f"pinka-ci-debian-13:{INFRA / 'tools' / 'pinka-lab'}",
        "pinka-ci-debian-13:/opt/pinka-ci/host-profile.json",
    ]
    assert verbs.index("cp") > mkdir
    preflight = next(c for c in fake.calls if "pinka_preflight" in c)
    assert preflight[preflight.index("--srv-path") + 1] == "/srv/pinka" and "--offline" in preflight
    assert preflight[preflight.index("--nvidia-driver") + 1] == "auto"  # example-machine's
    stored = json.loads((tmp_path / "debian_13-host-profile.json").read_text())
    assert stored == PROFILE  # preflight_gate compares its own run with this file
    variables = json.loads((tmp_path / "debian_13-vars.json").read_text())
    profile = variables["host_profile"]
    assert profile["provider"] == "baremetal"  # treated like bare metal
    assert profile["overrides"]["force_unsupported"] is True  # a container is unsupported
    assert variables["ansible_python_interpreter"] == "/usr/bin/python3"
    assert "pinka_python" not in variables  # bootstrap's CPython path, linked to the container's
    link = next(c for c in fake.calls if c[1:2] == ("exec",) and c[3] == "ln")
    assert link[-2:] == ("/usr/bin/python3", "/opt/pinka/python/bin/python3")
    assert variables["machine"] == "example-machine"
    bootstrap = variables["pinka_bootstrap"]
    assert bootstrap["profile_path"] == "/opt/pinka-ci/host-profile.json"
    assert bootstrap["offline"] is True and bootstrap["refresh_profile"] is False
    vault = json.loads((tmp_path / "debian_13-vault.json").read_text())
    assert vault["vault_restic_repository"] == "/var/backups/pinka-restic"
    assert set(vault) == set(yaml.safe_load(checkmode.VAULT_EXAMPLE.read_text()))
    assert "platform" not in json.loads((tmp_path / "debian_13-vars.json").read_text())
    inventory = (tmp_path / "debian_13-inventory.ini").read_text()
    assert inventory.split() == [
        "localhost",
        "ansible_connection=community.docker.docker",
        "ansible_host=pinka-ci-debian-13",
    ]
    assert play[play.index("--inventory") + 1] == str(tmp_path / "debian_13-inventory.ini")
    assert "--skip-tags" not in play  # the required run skips nothing
    assert printed[0].startswith("== debian_13 (required): docker.io/library/debian@sha256:")
    assert not [line for line in printed if line.startswith("-- skipping")]
    assert not list(tmp_path.glob("*-extended-summary.md"))


def test_required_play_is_the_provider_agnostic_roles() -> None:
    """BUILD_PROMPT §11: base, users_groups, ssh and layout, in check mode only."""
    plays = yaml.safe_load(checkmode.REQUIRED_PLAYBOOK.read_text())
    assert len(plays) == 1
    roles = [task["ansible.builtin.import_role"]["name"] for task in plays[0]["tasks"]]
    assert roles == ["platform_facts", *checkmode.REQUIRED_ROLES]
    assert "ansible_check_mode" in plays[0]["pre_tasks"][0]["ansible.builtin.assert"]["that"]
    assert not set(checkmode.REQUIRED_ROLES) & set(checkmode.SOURCED_ROLES)


@pytest.mark.parametrize("ansible_rc", [0, 2])
def test_check_mode_extended(tmp_path: Path, ansible_rc: int) -> None:
    """The extended run: the whole play; skipped components are listed as not tested."""
    fake = FakeDocker(ansible_rc=ansible_rc)
    printed: list[str] = []
    step_summary = tmp_path / "step-summary.md"
    step_summary.write_text("earlier step\n")
    out = tmp_path / "out"
    rc = checkmode.main(
        "debian_13",
        fake,
        out,
        extended=True,
        commit="0" * 40,
        summary=step_summary,
        write=printed.append,
        sleep=NO_SLEEP,
    )
    assert rc == ansible_rc
    play = next(c for c in fake.calls if c[0] == "ansible-playbook")
    assert "--check" in play and play[-1].endswith("ansible/site.yml")
    # The reference platform is enabled as in the test VMs, with placeholder secrets.
    variables = json.loads((out / "debian_13-vars.json").read_text())
    assert variables["platform"] == {
        "path": "tests/reference-platform",
        "ref": "0" * 40,
        "repo": "file:///root/pinka",
    }
    assert variables["content"]["cases"]["path"] == "tests/reference-platform-cases"
    vault = json.loads((out / "debian_13-vault.json").read_text())
    assert {"refplat_ctl", "refplat_migrator"} <= set(vault["vault_postgres_passwords"])
    assert vault["vault_platform_secrets"] == {"ctl": {"api-token": checkmode.PLACEHOLDER}}
    refused = checkmode.unverified_roles(PROFILE)
    assert refused  # Tailscale and Incus are not verified on any release yet
    skipped = play[play.index("--skip-tags") + 1].split(",")
    assert skipped == sorted(refused)
    assert printed[0].startswith("== debian_13 (extended): ")
    assert [line.split()[2] for line in printed if line.startswith("-- skipping")] == [
        f"{role}," for role in skipped
    ]
    text = (out / "debian_13-extended-summary.md").read_text()
    assert step_summary.read_text() == "earlier step\n" + text + "\n"  # appended
    assert printed[-1] == text
    assert "non-blocking" in text
    for role in skipped:
        rows = [line for line in text.splitlines() if line.startswith(f"| `{role}` |")]
        assert len(rows) == 1 and "| SKIPPED, not tested |" in rows[0]
        assert "pass" not in rows[0].lower()  # never reported as passing
    assert f"Components skipped, not tested: **{len(skipped)}**" in text
    expected = (
        "passed" if ansible_rc == 0 else f"failed (ansible-playbook exit status {ansible_rc})"
    )
    assert f"Roles that ran: **{expected}**" in text


def test_extended_summary_escapes_table_cells() -> None:
    text = checkmode.extended_summary("x", {"incus": "a | b"}, 0)
    assert "| `incus` | SKIPPED, not tested | a \\| b |" in text
    assert "Components skipped, not tested: **0**" in checkmode.extended_summary("x", {}, 0)


def test_unverified_roles() -> None:
    """The fast lane skips exactly the roles the play would refuse on the release."""
    refused = checkmode.unverified_roles(PROFILE)
    assert set(refused) <= set(checkmode.SOURCED_ROLES)
    sources = yaml.safe_load((INFRA / "ansible" / "component-sources.yml").read_text())
    for role, component in checkmode.SOURCED_ROLES.items():
        entry = (
            sources["components"][component].get("debian")
            or sources["components"][component]["default"]
        )
        repo = sources["repositories"].get(entry.get("repo", ""), {})
        if "verify" in entry or "verify" in repo:
            assert role in refused, role
    rolling = {**PROFILE, "family": "archlinux", "release_model": "rolling", "os": {"id": "arch"}}
    assert all("pins" not in reason for reason in checkmode.unverified_roles(rolling).values())
    with pytest.raises(checkmode.CheckModeError, match="no release identity"):
        checkmode.unverified_roles({**PROFILE, "os": {"id": "debian", "codename": "nope"}})
    with pytest.raises(checkmode.CheckModeError, match="no supported family"):
        checkmode.unverified_roles({**PROFILE, "family": "gentoo"})


def test_check_mode_failures(tmp_path: Path) -> None:
    printed: list[str] = []
    assert (
        checkmode.main(
            "no_such_image", FakeDocker(), tmp_path, write=printed.append, sleep=NO_SLEEP
        )
        == 2
    )
    assert "no containers.ci_images.no_such_image" in printed[-1]
    broken = FakeDocker(profile={"error": "x"})
    assert checkmode.main("debian_13", broken, tmp_path, write=printed.append, sleep=NO_SLEEP) == 2
    assert "no host profile" in printed[-1]
    assert broken.calls[-1][:2] == ("docker", "rm")  # removed although it failed
    crash = FakeDocker(preflight_crash="No module named pinka_preflight")
    assert checkmode.main("debian_13", crash, tmp_path, write=printed.append, sleep=NO_SLEEP) == 2
    assert "exit status 1" in printed[-1] and "No module named pinka_preflight" in printed[-1]
    assert (
        checkmode.main(
            "debian_13", FakeDocker(ansible_rc=2), tmp_path, write=printed.append, sleep=NO_SLEEP
        )
        == 2
    )


def test_every_family_has_a_ci_image() -> None:
    lock = yaml.safe_load((INFRA / "versions.lock.yml").read_text())
    images = lock["containers"]["ci_images"]
    families = {
        "debian": {"ubuntu_2404", "debian_13"},
        "redhat": {"fedora_44", "almalinux_9"},
        "suse": {"opensuse_leap_156"},
        "archlinux": {"archlinux"},
    }
    assert set(images) == set().union(*families.values())
    workflow = (INFRA / ".github" / "workflows" / "ci.yml").read_text()
    for key in images:
        assert f"image: {key}" in workflow, key


def test_cli_refuses_without_a_release_package(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The VMs bootstrap from a release package of HEAD; no package, no test.
    from pinka_lockcheck import bundle  # noqa: PLC0415 - the package builder

    def refused(*_args: object, **_kwargs: object) -> Path:
        raise bundle.PackageError("ansible.posix 2.2.2: download failed: HTTP 503")

    monkeypatch.setenv(cli.AUTHKEY_ENV, AUTHKEY)
    monkeypatch.setattr(cli, "lab_running", lambda run: False)
    monkeypatch.setattr(bundle, "build", refused)
    argv = ["run", "--target", "debian-13", "--accept-unverified-images"]
    assert run_cli(argv) == 2
    assert "refused: the release package of HEAD: ansible.posix 2.2.2" in capsys.readouterr().err


@pytest.mark.parametrize("target", ["test-distros", "check-mode", "check-mode-extended"])
def test_every_make_target_finds_the_packages_the_cli_imports(target: str) -> None:
    # The CI of e525a1e: check-mode's PYTHONPATH lacked tools/lockcheck, which
    # the CLI imports. Each target's own command line must import it.
    dry = subprocess.run(
        ["make", "-n", target, "CI_IMAGE=debian_13"],
        cwd=INFRA,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    line = next(x for x in dry.splitlines() if "-m pinka_test_distros" in x)
    path = next(w for w in shlex.split(line) if w.startswith("PYTHONPATH="))
    env = {**os.environ, "PYTHONPATH": path.removeprefix("PYTHONPATH=")}
    done = subprocess.run(
        [sys.executable, "-c", "import pinka_test_distros.cli"],
        cwd=INFRA,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
