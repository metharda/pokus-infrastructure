"""The TUI's pure parts: event replay, runner, suggestions and helpers."""

from __future__ import annotations

import json
import stat
import threading
import time
from pathlib import Path
from typing import Any

import pytest
import segno
from ruamel.yaml import YAML
from tui_fakes import DATA, DISKS, FakeHost, context, preflight

from pinka_installer import answers as answers_mod
from pinka_installer import apply, validators, vault
from pinka_installer.paths import INFRA, Paths
from pinka_installer.tui import model
from pinka_installer.tui.events import Progress, read_new, site_roles
from pinka_installer.tui.runner import BootstrapRunner, RunRequest
from pinka_installer.tui.screens_host import cpu_bar, disk_row
from pinka_installer.tui.screens_install import (
    checks_by_category,
    destructive,
    exit_meanings,
    qr_text,
    summary,
)
from pinka_installer.tui.screens_start import cards

SITE = INFRA / "ansible" / "site.yml"


def replay(name: str, provider: str = "wsl2") -> Progress:
    progress = Progress.planned(site_roles(SITE), provider=provider)
    events, _ = read_new(DATA / name, 0)
    for event in events:
        progress.apply(event)
    return progress


# --- event replay ---------------------------------------------------------------------


def test_site_roles_follow_site_yml() -> None:
    roles = site_roles(SITE)
    assert roles[:4] == [
        "platform_facts",
        "preflight_gate",
        "install_record",
        "provider_{{ pinka_provider }}",
    ]
    assert roles[-1] == "lab_session"


def test_planned_tree_resolves_the_provider_once() -> None:
    names = list(Progress.planned(site_roles(SITE), provider="wsl2").phases)
    assert names.count("provider_wsl2") == 1
    assert names.index("provider_wsl2") == names.index("install_record") + 1
    assert not any("{{" in n for n in names)
    assert "restore" not in names
    assert "restore" in Progress.planned(site_roles(SITE), restore=True).phases


def test_replay_of_a_successful_run() -> None:
    progress = replay("events-success.jsonl")
    assert {p.status for p in progress.phases.values()} == {"ok"}
    assert progress.result == ("doctor_warnings", 1)
    assert progress.failure is None and progress.signal is None
    assert progress.totals["failed"] == 0
    assert progress.phases["docker"].ok == 3  # with its pkg tasks (a helper role)
    assert progress.phases["platform"].ok == 3  # with deploy_source's
    assert "pkg" not in progress.phases and "deploy_source" not in progress.phases
    assert progress.login_url == ""  # the login completed
    assert progress.deploy_key == "ssh-ed25519 KEYPLACEHOLDER pinka-deploy@pinka-lab"


def test_the_login_url_shows_until_the_login_completes() -> None:
    progress = Progress.planned(site_roles(SITE), provider="wsl2")
    events, _ = read_new(DATA / "events-success.jsonl", 0)
    done = next(
        i
        for i, e in enumerate(events)
        if e["type"] == "ansible.task_end" and e["task"] == "Wait for the login to complete"
    )
    for event in events[:done]:
        progress.apply(event)
    assert progress.login_url == "https://login.tailscale.com/a/0123456789abcdef"
    assert progress.current_task == "Wait for the login to complete"
    progress.apply(events[done])
    assert progress.login_url == ""


def test_replay_of_a_failed_run() -> None:
    progress = replay("events-failure.jsonl")
    assert progress.result == ("failed", 60)
    assert progress.failure is not None
    assert (progress.failure.phase, progress.failure.task) == ("docker", "Start Docker")
    assert "Unable to start service docker" in progress.failure.message
    assert progress.phases["docker"].status == "failed"
    assert progress.phases["incus"].status == "pending"


def test_replay_of_a_wsl_restart() -> None:
    progress = replay("events-signal75.jsonl")
    assert progress.signal is not None and progress.signal[0] == 75
    assert "wsl --terminate" in progress.signal[1]
    assert progress.phases["provider_wsl2"].status == "signaled"
    assert progress.result == ("signal", 75)


def test_read_new_leaves_a_partial_line(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    assert read_new(path, 0) == ([], 0)
    path.write_text('{"type": "a"}\n{"type": "b"', encoding="utf-8")
    found, offset = read_new(path, 0)
    assert found == [{"type": "a"}] and offset == len('{"type": "a"}\n')
    with path.open("a", encoding="utf-8") as handle:
        handle.write("}\nnot json\n")
    found, _ = read_new(path, offset)
    assert found == [{"type": "b"}]


# --- the runner ---------------------------------------------------------------------------

FAKE_BOOTSTRAP = """#!/usr/bin/env bash
# A stand-in for bootstrap.sh: it checks the handover and replays recorded events.
set -Eeuo pipefail
password_file=""
while (($#)); do
  case $1 in
    --vault-password-file) password_file=$2; shift 2 ;;
    --mok-password-stdin) IFS= read -r mok; printf 'mok %s\\n' "$mok"; shift ;;
    *) printf 'arg %s\\n' "$1"; shift ;;
  esac
done
printf 'mode %s\\n' "$(stat -c %a "$password_file")"
printf 'password %s\\n' "$(cat "$password_file")"
printf 'log: /var/log/pinka-bootstrap/2026-01-01T00-00-00Z.log\\n'
while IFS= read -r line; do
  printf '%s\\n' "$line" >>"$PINKA_EVENTS_FILE"
done <"$EVENTS"
[[ -z ${SLEEP:-} ]] || sleep "$SLEEP"
exit "${CODE:-0}"
"""


def fake_checkout(tmp_path: Path) -> Paths:
    infra = tmp_path / "checkout"
    script = infra / "bootstrap" / "bootstrap.sh"
    script.parent.mkdir(parents=True)
    script.write_text(FAKE_BOOTSTRAP, encoding="utf-8")
    script.chmod(0o755)
    return Paths(infra=infra, run_dir=tmp_path / "run", state_file=tmp_path / "state.json")


def test_runner_hands_over_the_password_and_replays_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = fake_checkout(tmp_path)
    monkeypatch.setenv("EVENTS", str(DATA / "events-failure.jsonl"))
    monkeypatch.setenv("CODE", "60")
    runner = BootstrapRunner(paths)
    events: list[dict[str, Any]] = []
    lines: list[str] = []
    request = RunRequest("pinka-lab", ["--accept-degraded"], "secret password")
    code = runner.run(request, events.extend, lines.append)
    assert code == 60
    assert "mode 600" in lines and "password secret password" in lines
    assert "arg --machine" in lines and "arg pinka-lab" in lines
    assert "arg --non-interactive" in lines and "arg --no-tui" in lines
    assert lines.index("arg --accept-degraded") > lines.index("arg --no-tui")
    assert runner.log_file == "/var/log/pinka-bootstrap/2026-01-01T00-00-00Z.log"
    recorded, _ = read_new(DATA / "events-failure.jsonl", 0)
    assert events == recorded
    assert not (paths.run_dir / "vault-pass").exists()  # removed after the run
    assert stat.S_IMODE(paths.run_dir.stat().st_mode) == 0o700


def test_runner_writes_the_mok_password_to_stdin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = fake_checkout(tmp_path)
    monkeypatch.setenv("EVENTS", str(DATA / "events-signal76.jsonl"))
    monkeypatch.setenv("CODE", "76")
    runner = BootstrapRunner(paths)
    lines: list[str] = []
    request = RunRequest("pinka-lab", ["--mok-password-stdin"], "vault secret", "one time")
    assert "one time" not in repr(request)
    assert runner.run(request, lambda e: None, lines.append) == 76
    assert "mok one time" in lines
    assert not any("one time" in line for line in request.argv(paths.bootstrap, tmp_path / "p"))


def test_runner_cancel_stops_bootstrap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = fake_checkout(tmp_path)
    monkeypatch.setenv("EVENTS", str(DATA / "events-success.jsonl"))
    monkeypatch.setenv("SLEEP", "60")
    runner = BootstrapRunner(paths)
    result: list[int] = []
    thread = threading.Thread(
        target=lambda: result.append(runner.run(RunRequest("m"), lambda e: None, lambda s: None))
    )
    started = time.monotonic()
    thread.start()
    while runner._process is None and time.monotonic() - started < 10:
        time.sleep(0.05)
    runner.cancel()
    thread.join(timeout=20)
    assert result and result[0] != 0
    assert time.monotonic() - started < 30
    runner.cancel()  # nothing running: no error


# --- suggestions and checks ---------------------------------------------------------------


def test_cpu_split_suggestion() -> None:
    assert model.suggest_cpusets(None) == ("", "")
    assert model.suggest_cpusets(2) == ("", "")
    assert model.suggest_cpusets(4) == ("0-1", "2-3")
    assert model.suggest_cpusets(24) == ("0-6", "7-23")
    # With SMT siblings (cpu<n> and cpu<n+8> share a core) whole cores go to
    # one set, so the suggestion never splits a core.
    cores = [frozenset({n, n + 8}) for n in range(8)]
    services, targets = model.suggest_cpusets(16, cores)
    assert (services, targets) == ("0-2,8-10", "3-7,11-15")
    assert validators.smt_split(services, targets, cores) is None
    assert validators.smt_split("0-4", "5-15", cores) is not None
    # Siblings numbered next to each other (0,1 one core) keep the plain ranges.
    assert model.suggest_cpusets(24, [frozenset({n, n + 1}) for n in range(0, 24, 2)]) == (
        "0-7",
        "8-23",
    )
    assert cpu_bar("0-1", "2-3", 6) == "SSTT··"
    assert cpu_bar("0-2", "2-3", 4) == "SSXT"
    assert cpu_bar("x", "2-3", 4) == ""


def test_reads_the_smt_siblings(tmp_path: Path) -> None:
    for cpu, siblings in {0: "0,2", 1: "1,3", 2: "0,2", 3: "1,3", 4: "4", 5: "x"}.items():
        topology = tmp_path / f"cpu{cpu}" / "topology"
        topology.mkdir(parents=True)
        (topology / "thread_siblings_list").write_text(siblings + "\n", encoding="utf-8")
    (tmp_path / "cpufreq").mkdir()
    assert apply.read_smt_cores(tmp_path) == [frozenset({0, 2}), frozenset({1, 3})]
    assert apply.read_smt_cores(tmp_path / "missing") == []


def test_postgres_and_loop_suggestions() -> None:
    assert model.suggest_postgres(None) == ""
    assert model.suggest_postgres(1) == "128MB"
    assert model.suggest_postgres(32) == "2048MB"
    assert model.suggest_postgres(256) == "4096MB"
    assert model.loop_size_limit(500) == 450
    assert model.loop_size_limit(30) == 0


def test_serving_profile_and_models_fit_the_gpu() -> None:
    profiles = model.load_profiles(INFRA / "vllm" / "profiles")
    gpu = preflight("ubuntu2404-wsl2-gpu-kvm").profile["gpu"]
    name = model.suggest_profile(gpu, profiles)
    assert name == "ada-12g"
    assert model.suggest_profile({"vram_gib": 2}, profiles) == ""
    known = YAML(typ="safe").load((INFRA / "models.catalog.yml").read_text(encoding="utf-8"))
    choices = model.model_choices(known, {"qwen3-8b-awq"}, name, gpu["vram_gib"])
    assert choices and all(not c.fits for c in choices)
    assert [c.id for c in choices if c.pinned] == ["qwen3-8b-awq"]
    small = model.model_choices(known, set(), name, 2.0)
    assert all(c.fits.startswith("needs ") for c in small)
    assert all(c.fits for c in model.model_choices(known, set(), "no-such-profile", 12.0))


def test_vault_password_rules() -> None:
    assert model.password_strength("short") == (0, "too short")
    assert model.password_strength("a" * 12)[1] == "weak"
    assert model.password_strength("Correct-Horse-Battery-42")[1] == "strong"
    assert model.vault_password_problem("short", "short") == "at least 12 characters"
    assert model.vault_password_problem("a" * 12, "b" * 12) == "the two entries differ"
    assert model.vault_password_problem("a" * 12, "a" * 12) is None


def test_acl_and_interfaces() -> None:
    snippet = model.acl_snippet("tag:pinka-lab")
    assert '"dst": ["tag:pinka-lab:22", "tag:pinka-lab:443"]' in snippet
    assert "funnel" not in snippet.lower()
    assert model.interface_problems(["enp5s0"]) == []
    assert model.interface_problems(["bad name"])


def test_what_screen_8_offers_to_close() -> None:
    # The shared host of preflight's fixture, installed over SSH from the LAN.
    access = preflight("ubuntu2404-access-shared", ["--researcher", "researcher1"]).profile[
        "access"
    ]
    assert model.closable_listeners(access) == [
        ("tcp/22", "tcp/22 on every address (sshd): only with the install session"),
        ("tcp/8080", "tcp/8080 on every address (example-web)"),
    ]
    assert model.install_path(access) == {
        "via": "ssh",
        "address": "192.0.2.10",
        "port": 22,
        "user": "owner1",
        "tailnet": False,
    }
    # What the strict policy admits and what Docker publishes are not offered.
    listeners = [
        {"proto": "udp", "address": "0.0.0.0", "port": 68, "admitted": True},  # noqa: S104
        {"proto": "tcp", "address": "0.0.0.0", "port": 9000, "process": "docker-proxy"},  # noqa: S104
        {"proto": "tcp", "address": "::", "port": 9000, "process": "docker-proxy"},
        {"proto": "tcp", "address": "192.0.2.10", "port": 631, "process": None},
    ]
    shared = {"host": "shared", "session": {"via": "console"}, "listeners": listeners}
    assert model.closable_listeners(shared) == [
        ("tcp/631", "tcp/631 on 192.0.2.10 (unknown process)")
    ]
    assert model.published_ports(shared) == ["tcp/9000"]
    assert model.install_path(shared) is None
    # A dedicated host offers nothing; its strict policy is the default.
    dedicated = {**shared, "host": "dedicated"}
    assert model.closable_listeners(dedicated) == []
    assert model.published_ports(dedicated) == []
    tailnet = {"session": {"via": "ssh", "address": "100.101.2.3", "port": 22, "tailnet": True}}
    assert model.install_path(tailnet) is None


def test_bootstrap_options_and_resume_data() -> None:
    wizard = model.Wizard()
    wizard.consents = {"force_unsupported", "accept_degraded"}
    wizard.answers.mode = "restore"
    wizard.answers.images_build = ["_base"]
    wizard.build_images_now = False
    wizard.vault_password = "never stored"  # noqa: S105 - test fixture
    assert wizard.bootstrap_options(["--allow-dirty", "--skip-tags=backup"]) == [
        "--allow-dirty",
        "--skip-tags=backup,images",
        "--accept-degraded",
        "--force-unsupported",
        "--restore",
    ]
    data = wizard.resume_data()
    assert "never stored" not in json.dumps(data)
    again = model.Wizard()
    again.restore(data)
    assert again.answers == wizard.answers
    assert again.consents == wizard.consents
    assert again.build_images_now is False
    again.restore({"answers": {"mode": "nonsense"}, "consents": ["not_a_consent"]})
    assert again.consents == set()


def test_mok_consent_reaches_preflight_and_bootstrap() -> None:
    wizard = model.Wizard()
    assert wizard.preflight_args() == []  # preflight judges with auto, like bootstrap
    wizard.nvidia_driver = "skip"
    assert wizard.preflight_args() == ["--nvidia-driver", "skip"]
    wizard.nvidia_driver = "auto"
    wizard.consents = {"accept_mok_enrollment"}
    wizard.mok_password = "one time"  # noqa: S105 - test fixture
    assert wizard.preflight_args() == ["--nvidia-driver", "auto", "--accept-mok-enrollment"]
    assert wizard.bootstrap_options() == ["--accept-mok-enrollment", "--mok-password-stdin"]
    data = wizard.resume_data()
    assert "one time" not in json.dumps(data)
    again = model.Wizard()
    again.restore(data)
    assert again.consents == {"accept_mok_enrollment"}
    assert again.mok_password == ""


def test_mok_enrollment_is_needed_only_with_secure_boot_and_no_driver() -> None:
    wizard = model.Wizard()
    wizard.profile = preflight("ubuntu2404-baremetal-nvidia-no-driver-secureboot").profile
    assert wizard.mok_enrollment_needed
    for fixture in (
        "ubuntu2404-baremetal-nvidia-driver-loaded",
        "ubuntu2404-baremetal-no-gpu",
        "ubuntu2404-wsl2-gpu-kvm",
    ):
        wizard.profile = preflight(fixture).profile
        assert not wizard.mok_enrollment_needed, fixture


def test_no_driver_is_installed_for_a_gpu_below_turing() -> None:
    wizard = model.Wizard()
    for fixture in (
        "ubuntu2404-baremetal-nvidia-kepler-secureboot",
        "ubuntu2404-baremetal-nvidia-kepler",
        "ubuntu2404-baremetal-nvidia-pascal",
    ):
        wizard.profile = preflight(fixture).profile
        assert wizard.gpu_unsupported and not wizard.mok_enrollment_needed, fixture
    for fixture in (
        "ubuntu2404-baremetal-nvidia-turing",
        "ubuntu2404-baremetal-nvidia-no-driver-secureboot",
        "ubuntu2404-baremetal-nvidia-driver-loaded",
        "ubuntu2404-baremetal-no-gpu",
        "ubuntu2404-wsl2-gpu-kvm",
    ):
        wizard.profile = preflight(fixture).profile
        assert not wizard.gpu_unsupported, fixture


def test_existing_answers_prefer_the_files(repo: Paths) -> None:
    loaded = answers_mod.load(repo.infra / "tests" / "answers" / "ci.yml", apply.context(repo))
    loaded.machine.name = "pinka-lab"
    apply.plan(
        loaded, repo, vault.Provided(tailscale_authkey="authkey-placeholder"), "password-1234"
    ).write()
    text = repo.host_vars("pinka-lab").read_text(encoding="utf-8")
    repo.host_vars("pinka-lab").write_text(
        text.replace("pinka_root: /srv/pinka", "pinka_root: /data/pinka"), encoding="utf-8"
    )
    found = model.existing_answers(repo, "pinka-lab")
    assert found.mode == "modify"
    assert found.machine.srv_path == "/data/pinka"  # host_vars edited by hand win
    assert found.backups.repository == loaded.backups.repository
    unknown = model.existing_answers(repo, "other-machine")
    assert (unknown.machine.name, unknown.mode) == ("other-machine", "modify")


# --- screen helpers -------------------------------------------------------------------------


def test_qr_code_has_two_modules_per_character() -> None:
    url = "https://login.tailscale.com/a/0123456789abcdef"
    lines = qr_text(url).splitlines()
    size = len(segno.make(url, error="l", micro=False).matrix) + 4
    assert {len(line) for line in lines} == {size}
    assert len(lines) == (size + 1) // 2
    assert set("".join(lines)) <= {"█", "▀", "▄", " "}


def test_review_summary_and_destructive_actions() -> None:
    answers = answers_mod.Answers()
    answers.machine.name = "pinka-lab"
    answers.researchers = [answers_mod.Researcher("researcher1", True)]
    sections = {step: text for step, _, text in summary(answers)}
    assert sections["storage"] == "loop file of auto size"
    answers.storage.loop_size_gib = 300
    assert {step: text for step, _, text in summary(answers)}["storage"] == "loop file of 300 GiB"
    assert sections["researchers"] == "researcher1 (admin)"
    assert destructive(answers) == []
    answers.storage.source = "device"
    answers.storage.device = answers.storage.device_confirm = "/dev/disk/by-id/nvme-EXAMPLE"
    answers.storage.force_wipe = True
    answers.mode = "restore"
    answers.researchers_removed = ["researcher9"]
    assert destructive(answers) == [
        "/dev/disk/by-id/nvme-EXAMPLE is formatted as the Incus pool, wiping its signatures",
        "the lab's data is replaced by snapshot latest",
        "accounts are locked: researcher9",
    ]


def test_exit_meanings_come_from_bootstrap() -> None:
    meanings = exit_meanings(INFRA / "bootstrap" / "lib" / "exitcodes.sh")
    assert meanings[60] == "play failed"
    assert meanings[75] == "WSL restart required"
    assert exit_meanings(INFRA / "missing.sh") == {}


def test_doctor_checks_by_category() -> None:
    report = {"checks": [{"id": "backups.recent"}, {"id": "security.ssh"}, {"id": "solo"}]}
    assert {k: [c["id"] for c in v] for k, v in checks_by_category(report).items()} == {
        "backups": ["backups.recent"],
        "security": ["security.ssh"],
        "solo": ["solo"],
    }


def test_preflight_cards_and_disk_rows() -> None:
    profile = preflight("ubuntu2404-wsl2-gpu-kvm").profile
    found = dict(cards(profile))
    assert found["Environment"] == "wsl2 (microsoft), confidence high"
    assert found["Virtualization"] == "KVM usable, nested yes"
    assert found["GPU"] == "NVIDIA GeForce RTX 4070: usable, 12.0 GiB"
    assert cards({})[0] == ("Environment", "unknown (no hypervisor), confidence unknown")
    assert disk_row(DISKS[0])[3] == "2.0 TiB"
    assert disk_row(DISKS[1])[-1] == "✖ mounted at /"


def test_build_context(repo: Paths) -> None:
    host = FakeHost(preflight("ubuntu2404-baremetal-no-gpu"))
    ctx = context(repo, host, machine="lab-two", bootstrap_args=["--allow-dirty"])
    assert ctx.wizard.answers.machine.name == "lab-two"
    assert ctx.wizard.answers.mode == "fresh"
    assert not ctx.wizard.installed
    assert ctx.passthrough == ["--allow-dirty"]
    assert ctx.git.commit.startswith("0123")
    installed = FakeHost(preflight("ubuntu2404-baremetal-no-gpu"), installed="pinka-lab")
    other = context(repo, installed, machine="lab-two")
    assert other.wizard.installed and other.wizard.answers.mode == "fresh"
