"""Tests of the distribution test harness, against a fake ``incus``."""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest
import yaml
from pokus_test_distros import checkmode, cli, harness, results, targets
from pokus_test_distros.incus import Incus, Result

from pokus_preflight.detect.os_release import OsRelease
from pokus_preflight.matrix import load_matrix, match_os

INFRA = Path(__file__).resolve().parents[3]
AUTHKEY = "placeholder-auth-key-for-tests"
GOOD_LOG = "PLAY RECAP *****\nlocalhost : ok=500 changed=0 unreachable=0 failed=0 skipped=9\n"


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
            **behaviour,
        }
        self.runs = 0

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
        return 0, ""

    def pull(self, remote: str, local: Path) -> tuple[int, str]:
        if remote == "etc/pokus/bootstrap-state.json":
            local.write_text(
                json.dumps({"log_file": f"/var/log/run-{self.runs}.log", "result": "ok"})
            )
        elif remote.startswith("var/log/run-"):
            local.write_text(
                GOOD_LOG if remote.endswith("1.log") else str(self.behaviour["second_log"])
            )
        elif remote == "etc/pokus/host-profile.json":
            local.write_text(json.dumps({"firewall": {"backend": self.behaviour["backend"]}}))
        else:
            return 1, ""
        return 0, ""

    def exec(self, command: tuple[str, ...]) -> tuple[int, str]:
        script = command[-1]
        b = self.behaviour
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

    def scripts(self) -> list[str]:
        return [c[-1] for c in self.calls if c[1] == "exec"]


def make_test(
    tmp_path: Path, target_id: str, keep: str = "failed", **behaviour: Any
) -> tuple[harness.Test, FakeIncus]:
    fake = FakeIncus(tmp_path, **behaviour)
    incus = Incus(fake, "pokus-test-distros", sleep=lambda _s: None)
    tar = tmp_path / "pokus.tar"
    tar.write_text("tar")
    secrets_env = tmp_path / "secrets.env"
    secrets_env.write_text(f"POKUS_SECRET_TAILSCALE_AUTHKEY={AUTHKEY}\n")
    times = iter(
        [dt.datetime(2026, 1, 1, 10, tzinfo=dt.UTC), dt.datetime(2026, 1, 1, 11, 30, tzinfo=dt.UTC)]
    )
    settings = harness.Settings(
        repo_tar=tar,
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
    first, second = [s for s in scripts if "bootstrap.sh" in s]
    assert "--answers /root/pokus/infra/installer-answers/ptd-debian-13-abcdef.yml" in first
    assert "--accept-degraded" in first and "--answers" not in second
    assert any(s == "pokus-lab backup now" for s in scripts)
    # Secrets travel in 0600 files, never on a command line.
    assert all(AUTHKEY not in " ".join(c) for c in fake.calls)
    assert fake.pushed["root/ci-secrets.env"][1] == "0600"
    assert fake.pushed["root/ci-vault-pass"][1] == "0600"
    answers = yaml.safe_load(
        fake.pushed["root/pokus/infra/installer-answers/ptd-debian-13-abcdef.yml"][0]
    )
    assert answers["machine"]["name"] == "ptd-debian-13-abcdef"
    # Deleted after leaving the tailnet.
    names = [c[1:3] for c in fake.calls[-2:]]
    assert names == [("exec", "--project"), ("delete", "--project")]
    assert not test.kept
    assert (tmp_path / "out" / "debian-13" / "host-profile.json").exists()


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
    assert path.read_text().startswith("# Managed by pokus-infra (tests/distros/results.yml).")
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
    assert cli.lab_running(active) is Path("/etc/pokus/lab.json").exists()


# --- the CI fast lane -----------------------------------------------------------------------------


NO_SLEEP = lambda _seconds: None  # noqa: E731
PROFILE = {"schema_version": 1, "provider": None, "family": "debian", "outcome": "unsupported"}


class FakeDocker:
    """Answers docker and ansible-playbook for one fast-lane run."""

    def __init__(self, profile: dict[str, Any] | None = None, ansible_rc: int = 0) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.profile = PROFILE if profile is None else profile
        self.ansible_rc = ansible_rc
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
        elif args[1] == "exec" and "pokus_preflight" in args:
            rc, out = 20, json.dumps(self.profile)
        return Result(args, rc, out, "")


def test_check_mode_run(tmp_path: Path) -> None:
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
    variables = json.loads((tmp_path / "debian_13-vars.json").read_text())
    assert variables["host_profile"]["provider"] == "baremetal"  # treated like bare metal
    assert variables["ansible_python_interpreter"] == "/usr/bin/python3"
    assert printed[0].startswith("== debian_13: docker.io/library/debian@sha256:")


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
    root_workflow = (INFRA.parent / ".github" / "workflows" / "infra.yml").read_text()
    for key in images:
        assert f"image: {key}" in workflow, key
        assert f"image: {key}" in root_workflow, key
