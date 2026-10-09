"""Tests for bootstrap.sh and its libraries (BUILD_PROMPT §5).

Nothing here provisions anything. ``bootstrap.sh`` itself only runs as an
unprivileged user (it stops at the root check), and ``main`` only runs with
every ``stage_*`` function replaced by a stub.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest
from bashlib import BOOTSTRAP, INFRA, bash, q

FIXTURES = INFRA / "preflight" / "tests" / "fixtures"
UNPRIVILEGED = (
    ["setpriv", "--reuid=65534", "--regid=65534", "--clear-groups"] if os.geteuid() == 0 else []
)


def run_unprivileged(
    script: Sequence[str], cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Run a command as an unprivileged user (nobody when the tests run as root)."""
    return subprocess.run(
        [*UNPRIVILEGED, *script],
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
    )


def profile(tmp_path: Path, fixture: str, name: str = "profile.json") -> Path:
    """Write the host profile preflight produces for a fixture (with its ``args.json``)."""
    path = tmp_path / name
    args = FIXTURES / fixture / "args.json"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pinka_preflight",
            "--root",
            str(FIXTURES / fixture),
            "--offline",
            "--quiet",
            "--output",
            str(path),
            *(json.loads(args.read_text()) if args.exists() else []),
        ],
        cwd=INFRA / "preflight",
        check=False,
        capture_output=True,
    )
    assert path.exists()
    return path


# --- command line --------------------------------------------------------------


def test_help_lists_every_option() -> None:
    res = run_unprivileged(["bash", str(BOOTSTRAP / "bootstrap.sh"), "--help"])
    assert res.returncode == 0
    assert res.stdout.startswith(
        "Usage: sudo ./bootstrap/bootstrap.sh [--machine <name>] [options]"
    )
    for option in (
        "--machine",
        "--ref",
        "--restore",
        "--tags",
        "--skip-tags",
        "--check",
        "--accept-degraded",
        "--accept-experimental",
        "--force-unsupported",
        "--refresh-profile",
        "--allow-reboot",
        "--allow-dirty",
        "--adopt-incus",
        "--accept-mok-enrollment",
        "--mok-password-stdin",
        "--allow-community-repos",
        "--offline",
        "--non-interactive",
        "--vault-password-file",
        "--answers",
        "--tui",
        "--no-tui",
        "--help",
    ):
        assert option in res.stdout


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ([], "--machine is required"),
        (["--bogus"], "unknown option: --bogus"),
        (["--machine"], "option --machine needs a value"),
        (["--machine", "Bad_Name"], "invalid machine name"),
        (["--machine", "-starts-with-dash"], "invalid machine name"),
        (["--machine", "lab1", "--tags", "base images"], "invalid tag list"),
        (["--machine", "lab1", "--skip-tags", "images,"], "invalid tag list"),
        (["--machine", "lab1", "--tui", "--non-interactive"], "exclude each other"),
        (["--machine", "lab1", "--tui", "--mok-password-stdin"], "exclude each other"),
        (["--machine", "lab1", "--answers", "/nonexistent/answers.yml"], "is not readable"),
        (["--machine", "lab1", "--vault-password-file", "/nonexistent/pass"], "is not readable"),
    ],
)
def test_usage_errors_exit_64(args: list[str], message: str) -> None:
    res = run_unprivileged(["bash", str(BOOTSTRAP / "bootstrap.sh"), *args])
    assert res.returncode == 64
    assert message in res.stderr


def test_requires_root() -> None:
    res = run_unprivileged(["bash", str(BOOTSTRAP / "bootstrap.sh"), "--machine", "lab1"])
    assert res.returncode == 77
    assert "must run as root" in res.stderr
    assert "sudo" in res.stderr


def test_interactive_and_tui_decisions() -> None:
    script = """
    OPT_NON_INTERACTIVE=true; is_interactive && echo interactive || echo plain
    OPT_NON_INTERACTIVE=false; OPT_TUI=on; use_tui && echo tui-on
    OPT_TUI=off; use_tui || echo tui-off
    OPT_TUI=auto; use_tui || echo tui-auto-no-tty
    """
    res = bash(script)
    assert res.stdout.split() == ["plain", "tui-on", "tui-off", "tui-auto-no-tty"]


# --- logging and events ------------------------------------------------------------


def test_json_escape_and_events(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    text = 'quote " backslash \\ tab \t newline \n bell \a end'
    res = bash(
        f"""
        emit_event bootstrap.stage git started
        emit_event bootstrap.signal ansible signaled 75 {q(text)}
        """,
        env={"PINKA_EVENTS_FILE": str(events)},
    )
    assert res.returncode == 0, res.stderr
    lines = [json.loads(line) for line in events.read_text().splitlines()]
    assert lines[0]["type"] == "bootstrap.stage"
    assert lines[0]["status"] == "started"
    assert "exit_code" not in lines[0]
    assert lines[1]["exit_code"] == 75
    assert lines[1]["message"] == text
    assert lines[1]["ts"].endswith("Z")


def test_no_events_without_events_file() -> None:
    res = bash("emit_event bootstrap.stage git started; echo done", env={"PINKA_EVENTS_FILE": ""})
    assert res.stdout.strip() == "done"


# --- preflight gate ------------------------------------------------------------------


def gate(
    tmp_path: Path,
    fixture: str,
    *,
    flags: str = "",
    interactive: bool = False,
    answer: str = "",
    outcome: str | None = None,
    needs_action: str | None = None,
) -> tuple[int, str, str]:
    """Run gate_check on a fixture profile; returns (code, granted overrides, stderr)."""
    path = profile(tmp_path, fixture)
    data = json.loads(path.read_text())
    actions = needs_action if needs_action is not None else json.dumps(data["needs_action"])
    script = f"""
    {flags}
    INTERACTIVE={"true" if interactive else "false"}
    rc=0
    gate_check {q(outcome or data["outcome"])} {q(data["tier"])} {q(actions)} {q(path)} || rc=$?
    echo "$rc ${{GRANTED_OVERRIDES[*]}}"
    """
    res = bash(script, stdin=answer)
    code, _, granted = res.stdout.strip().partition(" ")
    return int(code), granted, res.stderr


def test_gate_supported(tmp_path: Path) -> None:
    assert gate(tmp_path, "ubuntu2404-baremetal-nvidia-driver-loaded")[:2] == (0, "")


def test_gate_degraded_needs_consent(tmp_path: Path) -> None:
    code, granted, err = gate(tmp_path, "ubuntu2404-wsl2-gpu-nokvm")
    assert (code, granted) == (10, "")
    assert "vm_targets" in err  # the disabled features are listed
    assert "--accept-degraded" in err


def test_gate_degraded_with_flag(tmp_path: Path) -> None:
    flags = "OPT_ACCEPT_DEGRADED=true"
    assert gate(tmp_path, "ubuntu2404-wsl2-gpu-nokvm", flags=flags)[:2] == (0, "")


def test_gate_degraded_interactive(tmp_path: Path) -> None:
    yes = gate(tmp_path, "ubuntu2404-wsl2-gpu-nokvm", interactive=True, answer="yes\n")
    assert yes[:2] == (0, "accept_degraded")
    assert "Continue with these features disabled? [y/N]" in yes[2]
    no = gate(tmp_path, "ubuntu2404-wsl2-gpu-nokvm", interactive=True, answer="\n")
    assert no[:2] == (10, "")


def test_gate_unsupported(tmp_path: Path) -> None:
    assert gate(tmp_path, "docker-container")[0] == 20
    forced = gate(tmp_path, "docker-container", flags="OPT_FORCE_UNSUPPORTED=true")
    assert forced[0] == 0
    assert "--force-unsupported" in forced[2]


def test_gate_force_does_not_skip_needed_actions(tmp_path: Path) -> None:
    code, _, err = gate(
        tmp_path,
        "docker-container",
        flags="OPT_FORCE_UNSUPPORTED=true",
        needs_action='[{"id": "x"}]',
    )
    assert code == 30
    assert "does not skip it" in err


def test_gate_needs_action(tmp_path: Path) -> None:
    assert gate(tmp_path, "ubuntu2404-wsl2-systemd-off")[0] == 30
    forced = gate(tmp_path, "ubuntu2404-wsl2-systemd-off", flags="OPT_FORCE_UNSUPPORTED=true")
    assert forced[0] == 30


def test_gate_experimental(tmp_path: Path) -> None:
    fixture = "linuxmint22-baremetal"  # experimental tier, degraded outcome
    assert gate(tmp_path, fixture, flags="OPT_ACCEPT_DEGRADED=true")[0] == 11
    both = "OPT_ACCEPT_DEGRADED=true; OPT_ACCEPT_EXPERIMENTAL=true"
    assert gate(tmp_path, fixture, flags=both)[:2] == (0, "")
    asked = gate(tmp_path, fixture, interactive=True, answer="y\ny\n")
    assert asked[:2] == (0, "accept_degraded accept_experimental")
    assert "experimental tier" in asked[2]


def test_gate_unknown_outcome(tmp_path: Path) -> None:
    assert gate(tmp_path, "ubuntu2404-baremetal-no-gpu", outcome="maybe")[0] == 40


# --- Ansible signals and the profile change rule -------------------------------------


def signal_result(tmp_path: Path, rc: int, content: str | None) -> tuple[int, str]:
    signal = tmp_path / "signal.json"
    if content is not None:
        signal.write_text(content)
    res = bash(
        f"""
        code=0
        playbook_result {rc} {q(signal)} || code=$?
        printf '%s|%s|%s' "$code" "$SIGNAL_CODE" "$SIGNAL_MESSAGE"
        """
    )
    code, signal_code, message = res.stdout.split("|", 2)
    return int(code), f"{signal_code}|{message}"


@pytest.mark.parametrize("code", [75, 76, 77])
def test_playbook_signal(tmp_path: Path, code: int) -> None:
    message = "Run on Windows: wsl --terminate <distro>\nthen re-run bootstrap"
    content = json.dumps({"code": code, "message": message})
    assert signal_result(tmp_path, 2, content) == (code, f"{code}|{message}")


@pytest.mark.parametrize(
    ("code", "allow", "check", "rebooted"),
    [
        (76, "true", "false", True),
        (76, "false", "false", False),
        (76, "true", "true", False),
        (75, "true", "false", False),
        (77, "true", "false", False),
    ],
)
def test_reboot_only_with_allow_reboot(
    tmp_path: Path, code: int, allow: str, check: str, rebooted: bool
) -> None:
    stubs = tmp_path / "bin"
    stubs.mkdir()
    calls = tmp_path / "shutdown.calls"
    shutdown = stubs / "shutdown"
    shutdown.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >>{q(calls)}\n')
    shutdown.chmod(0o755)
    signal = tmp_path / "bootstrap-signal.json"
    signal.write_text(json.dumps({"code": code, "message": "A reboot is required."}))
    res = bash(
        f"""
        OPT_ALLOW_REBOOT={allow} OPT_CHECK={check} PATH={q(stubs)}:$PATH
        code=0
        playbook_result 2 {q(signal)} || code=$?
        printf '\n%s' "$code"
        """
    )
    assert res.stdout.splitlines()[-1] == str(code), res.stderr
    assert calls.exists() is rebooted
    if rebooted:
        assert calls.read_text().startswith("-r +1 ")
        assert "reboots in one minute" in res.stderr


# --- Secure Boot: MOK password and import (approved deviation I1) ---------------------


def mok_run(
    tmp_path: Path, setup: str, *, mokutil_rc: int = 0, stdin: str = ""
) -> tuple[int, str, str, str]:
    """playbook_result on a reboot signal that names a MOK key.

    Returns (exit code, mokutil calls, mokutil's standard input, output).
    """
    stubs = tmp_path / "bin"
    stubs.mkdir(parents=True)
    calls, given = tmp_path / "mokutil.calls", tmp_path / "mokutil.stdin"
    mokutil = stubs / "mokutil"
    mokutil.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$*" >>{q(calls)}\ncat >>{q(given)}\nexit {mokutil_rc}\n'
    )
    mokutil.chmod(0o755)
    signal = tmp_path / "bootstrap-signal.json"
    key = "/var/lib/shim-signed/mok/MOK.der"
    signal.write_text(json.dumps({"code": 76, "message": "Reboot.", "mok_key": key}))
    res = bash(
        f"""
        PATH={q(stubs)}:$PATH
        {setup}
        code=0
        playbook_result 2 {q(signal)} || code=$?
        printf '\n%s|%s|%s' "$code" "$SIGNAL_CODE" "${{#MOK_PASSWORD}}"
        """,
        stdin=stdin,
    )
    last = res.stdout.splitlines()[-1]
    code, signal_code, left = last.split("|")
    assert code == signal_code  # the state file records what bootstrap returns
    assert left == "0"  # the password is gone once mokutil had it (or failed)
    return int(code), read_if_any(calls), read_if_any(given), res.stdout + res.stderr


def read_if_any(path: Path) -> str:
    """A file's text, or an empty string if it was never written."""
    return path.read_text() if path.exists() else ""


def test_mok_import_with_the_password_on_stdin(tmp_path: Path) -> None:
    code, calls, given, err = mok_run(tmp_path, "MOK_PASSWORD='one time'")
    assert code == 76
    assert calls == "--import /var/lib/shim-signed/mok/MOK.der\n"
    assert given == "one time\none time\n"
    assert "Enroll MOK" in err
    assert "one time" not in err


def test_mok_import_asks_on_a_terminal(tmp_path: Path) -> None:
    code, _, given, err = mok_run(tmp_path, "INTERACTIVE=true", stdin="pw1\npw1\n")
    assert (code, given) == (76, "pw1\npw1\n")
    assert "One-time MOK password" in err
    code, calls, _, err = mok_run(tmp_path / "x", "INTERACTIVE=true", stdin="pw1\npw2\n")
    assert (code, calls) == (77, "")
    assert "differ" in err


def test_mok_import_without_a_password(tmp_path: Path) -> None:
    code, calls, _, err = mok_run(tmp_path, "")
    assert (code, calls) == (77, "")
    assert "sudo mokutil --import /var/lib/shim-signed/mok/MOK.der" in err


def test_mok_import_failure_is_an_operator_action(tmp_path: Path) -> None:
    code, calls, _, err = mok_run(tmp_path, "MOK_PASSWORD=pw", mokutil_rc=1)
    assert code == 77
    assert calls
    assert "mokutil --import /var/lib/shim-signed/mok/MOK.der failed" in err


def test_no_mok_import_in_check_mode(tmp_path: Path) -> None:
    code, calls, _, _ = mok_run(tmp_path, "OPT_CHECK=true MOK_PASSWORD=pw")
    assert (code, calls) == (76, "")


def test_reboot_signal_without_a_key_imports_nothing(tmp_path: Path) -> None:
    stubs = tmp_path / "bin"
    stubs.mkdir()
    (stubs / "mokutil").write_text("#!/bin/sh\nexit 99\n")
    (stubs / "mokutil").chmod(0o755)
    signal = tmp_path / "signal.json"
    signal.write_text(json.dumps({"code": 76, "message": "A MOK is waiting for enrollment."}))
    res = bash(
        f"""
        PATH={q(stubs)}:$PATH MOK_PASSWORD=pw
        code=0
        playbook_result 2 {q(signal)} || code=$?
        printf '\n%s|%s' "$code" "$MOK_PASSWORD"
        """
    )
    assert res.stdout.splitlines()[-1] == "76|pw"


def test_planned_mok_enrollment_needs_the_password_first(tmp_path: Path) -> None:
    consent = profile(tmp_path, "ubuntu2404-baremetal-nvidia-no-driver-secureboot-consent")
    other = profile(tmp_path, "ubuntu2404-baremetal-no-gpu", "other.json")
    script = """
    profile_tool() {
      PYTHONPATH="$INFRA_DIR/preflight" pinka_python -m pinka_preflight.profile_tool "$@"
    }
    rc=0; require_mok_password "$1" || rc=$?; printf '\n%s|%s' "$rc" "${#MOK_PASSWORD}"
    """
    cases = [
        (other, "", "", "0|0"),
        (consent, "", "", "77|0"),
        (consent, "OPT_CHECK=true", "", "0|0"),
        (consent, "MOK_PASSWORD=pw", "", "0|2"),
        (consent, "INTERACTIVE=true", "abc\nabc\n", "0|3"),
    ]
    for path, setup, stdin, expected in cases:
        res = bash(f"set -- {q(path)}\n{setup}\n{script}", stdin=stdin)
        assert res.stdout.splitlines()[-1] == expected, (path.name, setup, res.stderr)
    res = bash(f"set -- {q(consent)}\n{script}")
    assert "--mok-password-stdin" in res.stderr


@pytest.mark.parametrize(
    ("stdin", "code", "length"),
    [("one time\nrest\n", 0, "8"), ("\n", 64, ""), ("", 64, "")],
)
def test_mok_password_from_stdin(stdin: str, code: int, length: str) -> None:
    res = bash(
        """
        OPT_MOK_PASSWORD_STDIN=true
        read_mok_password_stdin || exit "$?"
        printf '%s' "${#MOK_PASSWORD}"
        """,
        stdin=stdin,
    )
    assert (res.returncode, res.stdout) == (code, length)
    if code:
        assert "no password" in res.stderr


def test_nvidia_driver_mode_comes_from_the_machine(tmp_path: Path) -> None:
    host_vars = tmp_path / "ansible" / "inventory" / "host_vars"
    group_vars = tmp_path / "ansible" / "group_vars" / "all"
    host_vars.mkdir(parents=True)
    group_vars.mkdir(parents=True)
    (group_vars / "main.yml").write_text("# defaults\ninstall_nvidia_driver: skip  # comment\n")
    cases = {
        "manual": "install_nvidia_driver: manual # we install it\n",
        "auto": "x: 1\ninstall_nvidia_driver: 'auto'\n",
        "skip": "machine_note: none\n",  # not set: the group default
    }
    for expected, text in cases.items():
        (host_vars / "lab1.yml").write_text(text)
        res = bash(f"INFRA_DIR={q(tmp_path)} OPT_MACHINE=lab1; nvidia_driver_mode")
        assert res.stdout == expected, res.stderr
    (host_vars / "lab1.yml").write_text("install_nvidia_driver: sometimes\n")
    res = bash(f"INFRA_DIR={q(tmp_path)} OPT_MACHINE=lab1; nvidia_driver_mode")
    assert res.returncode == 77
    assert "must be auto, manual or skip" in res.stderr
    (group_vars / "main.yml").unlink()
    (host_vars / "lab1.yml").unlink()
    res = bash(f"INFRA_DIR={q(tmp_path)} OPT_MACHINE=lab1; nvidia_driver_mode")
    assert res.stdout == "auto"


def test_the_repository_default_driver_mode_is_auto() -> None:
    res = bash("OPT_MACHINE=example-machine; nvidia_driver_mode")
    assert res.stdout == "auto", res.stderr


@pytest.mark.parametrize(
    ("rc", "content", "expected"),
    [
        (0, None, 0),
        (0, '{"code": 75, "message": "stale"}', 0),
        (2, None, 60),
        (4, "not json", 60),
        (2, '{"code": 99, "message": "unknown"}', 60),
        (2, '{"message": "no code"}', 60),
        (2, '{"code": 76, "message": "x", "mok_key": "a\\nb"}', 60),
    ],
)
def test_playbook_results_without_signal(
    tmp_path: Path, rc: int, content: str | None, expected: int
) -> None:
    assert signal_result(tmp_path, rc, content) == (expected, "|")


@pytest.mark.parametrize(
    ("state", "accepted"),
    [
        (None, False),
        ({"result": "success"}, False),
        ({"result": "signal"}, True),
        ("garbage", False),
    ],
)
def test_profile_change_accepted(tmp_path: Path, state: object, accepted: bool) -> None:
    path = tmp_path / "bootstrap-state.json"
    if isinstance(state, dict):
        path.write_text(json.dumps(state))
    elif state is not None:
        path.write_text(str(state))
    res = bash(f"profile_change_accepted {q(path)} && echo yes || echo no")
    assert res.stdout.strip() == ("yes" if accepted else "no")


# --- state file and Ansible variables --------------------------------------------------


STATE_SETUP = """
OPT_MACHINE=lab1 OPT_TAGS=base,ssh OPT_SKIP_TAGS='' OPT_ACCEPT_DEGRADED=false
OPT_FORCE_UNSUPPORTED=true OPT_ALLOW_DIRTY=true OPT_RESTORE=true
GIT_COMMIT=0123456789abcdef0123456789abcdef01234567
GIT_INFRA_TREE=fedcba9876543210fedcba9876543210fedcba98
GIT_INFRA_DIRTY=true STARTED_AT=2026-10-05T09:00:00Z LOG_FILE='/var/log/pinka-bootstrap/x "y".log'
LAST_STAGE=doctor GRANTED_OVERRIDES=(accept_degraded)
"""


def test_state_json(tmp_path: Path) -> None:
    res = bash(STATE_SETUP + "state_json 1 2026-10-05T10:00:00Z")
    state = json.loads(res.stdout)
    assert state["schema_version"] == 1
    assert state["machine"] == "lab1"
    assert state["repo_commit"].startswith("0123")
    assert state["infra_dirty"] is True
    assert state["ref"] == "HEAD"
    assert state["tags"] == ["base", "ssh"]
    assert state["skip_tags"] == []
    assert state["overrides"]["accept_degraded"] is True  # granted interactively
    assert state["overrides"]["force_unsupported"] is True
    assert state["overrides"]["accept_experimental"] is False
    assert state["overrides"]["accept_mok_enrollment"] is False
    assert state["result"] == "doctor_warnings"
    assert state["exit_code"] == 1
    assert state["signal"] is None
    assert state["log_file"] == '/var/log/pinka-bootstrap/x "y".log'
    assert (state["started_at"], state["finished_at"]) == (
        "2026-10-05T09:00:00Z",
        "2026-10-05T10:00:00Z",
    )


@pytest.mark.parametrize(
    ("rc", "signal", "result"),
    [(0, "", "success"), (2, "", "doctor_failures"), (60, "", "failed"), (76, "76", "signal")],
)
def test_state_result_names(rc: int, signal: str, result: str) -> None:
    script = (
        STATE_SETUP + f"SIGNAL_CODE={q(signal)} SIGNAL_MESSAGE='reboot now'\nstate_json {rc} now"
    )
    state = json.loads(bash(script).stdout)
    assert state["result"] == result
    if signal:
        assert state["signal"] == {"code": 76, "message": "reboot now"}


def test_write_state_is_atomic_and_world_readable(tmp_path: Path) -> None:
    target = tmp_path / "etc" / "bootstrap-state.json"
    res = bash(STATE_SETUP + f"write_state 0 {q(target)}")
    assert res.returncode == 0, res.stderr
    assert json.loads(target.read_text())["result"] == "success"
    assert stat.S_IMODE(target.stat().st_mode) == 0o644
    assert [p.name for p in target.parent.iterdir()] == ["bootstrap-state.json"]


def test_extra_vars_json(tmp_path: Path) -> None:
    path = profile(tmp_path, "ubuntu2404-wsl2-gpu-nokvm")
    res = bash(STATE_SETUP + f"OPT_CHECK=true OPT_ALLOW_REBOOT=false\nextra_vars_json {q(path)}")
    data = json.loads(res.stdout)
    assert data["host_profile"] == json.loads(path.read_text())
    assert data["pinka_bootstrap"]["check"] is True
    assert data["pinka_bootstrap"]["allow_reboot"] is False
    assert data["pinka_bootstrap"]["machine"] == "lab1"
    assert data["pinka_bootstrap"]["signal_file"] == "/run/pinka/bootstrap-signal.json"


def test_vault_args(tmp_path: Path) -> None:
    pass_file = tmp_path / "pass"
    pass_file.write_text("x")
    res = bash(
        f"""
        OPT_VAULT_PASSWORD_FILE={q(pass_file)}; vault_args | paste -sd' '
        OPT_VAULT_PASSWORD_FILE=''; INTERACTIVE=true; vault_args
        INTERACTIVE=false; vault_args || echo no-password
        """
    )
    lines = res.stdout.splitlines()
    assert lines[0] == f"--vault-password-file {pass_file}"
    # /etc/pinka/secrets/vault-pass does not exist on a development machine
    assert lines[1:] == ["--ask-vault-pass", "no-password"]


# --- installer front-end (BUILD_PROMPT §5.3, §18) -----------------------------------


FAKE_INSTALLER = """
installer() {
  echo "installer $*" >>"$CALLS"
  if [[ " $* " == *" describe "* ]]; then
    [[ -n ${DESCRIBE_FAIL:-} ]] && return 1
    printf 'machine=%s\nmode=%s\nsnapshot=%s\naccept_mok_enrollment=%s\n' \\
      "${ANSWERS_MACHINE:-lab1}" "${ANSWERS_MODE:-fresh}" "${ANSWERS_SNAPSHOT:-}" \\
      "${ANSWERS_MOK:-false}"
  fi
  if [[ " $* " == *" apply "* && -n ${APPLY_FAIL:-} ]]; then return 1; fi
  return 0
}
"""


def frontend(tmp_path: Path, setup: str, env: dict[str, str] | None = None) -> tuple[int, str, str]:
    calls = tmp_path / "calls"
    answers = tmp_path / "answers.yml"
    answers.write_text("schema_version: 1\n")
    password = tmp_path / "vault-pass"
    password.write_text("x\n")
    res = bash(
        FAKE_INSTALLER
        + f"""
        setup_venv() {{ echo "venv $*" >>"$CALLS"; }}
        enter_stage() {{ :; }}
        leave_stage() {{ :; }}
        OPT_ANSWERS={q(answers)}
        OPT_VAULT_PASSWORD_FILE={q(password)}
        {setup}
        stage_frontend
        echo "machine=$OPT_MACHINE restore=$OPT_RESTORE mok=$OPT_ACCEPT_MOK_ENROLLMENT"
        """,
        env={"CALLS": str(calls), **(env or {})},
    )
    return res.returncode, res.stdout + res.stderr, calls.read_text() if calls.exists() else ""


def test_answers_are_applied_before_preflight(tmp_path: Path) -> None:
    code, out, calls = frontend(tmp_path, "")
    assert code == 0, out
    assert "machine=lab1 restore=false" in out
    assert "venv /opt/pinka/installer-venv" in calls
    assert "installer apply --answers" in calls and "--machine lab1" in calls


def test_answers_carry_the_mok_consent(tmp_path: Path) -> None:
    code, out, _ = frontend(tmp_path, "", {"ANSWERS_MOK": "true"})
    assert code == 0, out
    assert "mok=true" in out
    (tmp_path / "x").mkdir()
    code, out, _ = frontend(tmp_path / "x", "")
    assert "mok=false" in out


def test_restore_answers_turn_on_the_restore_stage(tmp_path: Path) -> None:
    code, out, _ = frontend(tmp_path, "", {"ANSWERS_MODE": "restore", "ANSWERS_SNAPSHOT": "latest"})
    assert code == 0, out
    assert "restore=true" in out


@pytest.mark.parametrize(
    ("setup", "env", "code", "message"),
    [
        ("OPT_MACHINE=lab2", {}, 64, "does not match the answers file"),
        ("OPT_VAULT_PASSWORD_FILE=''", {}, 77, "--answers needs --vault-password-file"),
        ("", {"DESCRIBE_FAIL": "1"}, 77, "cannot be used"),
        ("", {"APPLY_FAIL": "1"}, 77, "could not be applied"),
        ("OPT_ANSWERS=''", {}, 64, "--machine is required"),
    ],
)
def test_frontend_refusals(
    tmp_path: Path, setup: str, env: dict[str, str], code: int, message: str
) -> None:
    rc, out, _ = frontend(tmp_path, setup, env)
    assert rc == code, out
    assert message in out


def test_tui_gets_the_operator_overrides() -> None:
    res = bash(
        """
        OPT_ACCEPT_DEGRADED=true; OPT_ALLOW_DIRTY=true; OPT_SKIP_TAGS=images,models
        OPT_ACCEPT_MOK_ENROLLMENT=true
        tui_bootstrap_args
        """
    )
    assert res.stdout.split() == [
        "--accept-degraded",
        "--allow-dirty",
        "--accept-mok-enrollment",
        "--skip-tags=images,models",
    ]


def test_unsuitable_terminals_are_refused() -> None:
    res = bash("terminal_problem", env={"TERM": "dumb"})
    assert res.stdout == "TERM is dumb"
    res = bash("terminal_problem", env={"TERM": "xterm-256color"})
    # no terminal in the tests: stty cannot read a size
    assert res.stdout == "no terminal size"


# --- tooling helpers -------------------------------------------------------------------


def test_verify_sha256(tmp_path: Path) -> None:
    target = tmp_path / "file"
    target.write_text("pinka\n")
    wrong = "0" * 64
    digest = subprocess.run(
        ["sha256sum", str(target)], capture_output=True, text=True, check=True
    ).stdout.split()[0]
    assert digest != wrong
    res = bash(
        f"""
        verify_sha256 {q(target)} {digest} && echo ok
        verify_sha256 {q(target)} {wrong} || echo mismatch
        verify_sha256 {q(target)} 'TODO(verify)' || echo unverified
        """
    )
    assert res.stdout.split() == ["ok", "mismatch", "unverified"]
    assert "checksum mismatch" in res.stderr
    assert "no verified sha256" in res.stderr


def test_uv_assets_match_the_lock() -> None:
    res = bash("uv_asset x86_64; uv_asset aarch64; uv_asset riscv64 || echo none")
    lines = res.stdout.splitlines()
    assert lines[0].startswith("https://github.com/astral-sh/uv/releases/download/")
    assert "x86_64-unknown-linux-gnu.tar.gz" in lines[0]
    assert "aarch64-unknown-linux-gnu.tar.gz" in lines[1]
    assert lines[2] == "none"


def test_check_versions_sync(tmp_path: Path) -> None:
    lock = INFRA / "versions.lock.yml"
    versions = INFRA / "ansible" / "group_vars" / "all" / "versions.yml"
    assert bash(f"check_versions_sync {q(lock)} {q(versions)} && echo ok").stdout.strip() == "ok"
    changed = tmp_path / "versions.lock.yml"
    changed.write_text(lock.read_text() + "\n# changed\n")
    res = bash(f"check_versions_sync {q(changed)} {q(versions)} || echo drift")
    assert res.stdout.strip() == "drift"
    assert "make generate" in res.stderr


# --- git provenance ----------------------------------------------------------------------


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "infra" / "bootstrap").mkdir(parents=True)
    (root / "platform").mkdir()
    (root / "infra" / "bootstrap" / "x.sh").write_text("echo\n")
    (root / "platform" / "y.txt").write_text("y\n")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    git(root, "add", ".")
    git(root, "-c", "user.name=test", "-c", "user.email=test@example.com", "commit", "-qm", "init")
    return root


# Bootstrap runs under sudo: the checkout belongs to root or to the invoking
# user (SUDO_UID). Tests run as either, so they name the current user.
AS_SUDO_USER = {"SUDO_UID": str(os.getuid())}


def git_state_of(infra: Path, env: dict[str, str] | None = None) -> dict[str, str]:
    env = AS_SUDO_USER if env is None else env
    res = bash(
        f"""
        rc=0
        git_state {q(infra)} || rc=$?
        printf 'rc=%s\\ncommit=%s\\ntree=%s\\ndirty=%s\\nsubdir=%s\\n' \
          "$rc" "$GIT_COMMIT" "$GIT_INFRA_TREE" "$GIT_INFRA_DIRTY" "$GIT_INFRA_SUBDIR"
        """,
        env=env,
    )
    return dict(line.split("=", 1) for line in res.stdout.splitlines())


def test_git_state_records_commit_tree_and_dirty_flag(repo: Path) -> None:
    state = git_state_of(repo / "infra")
    assert state["rc"] == "0"
    assert state["commit"] == git(repo, "rev-parse", "HEAD")
    assert state["tree"] == git(repo, "rev-parse", "HEAD:infra")
    assert state["subdir"] == "infra"
    assert state["dirty"] == "false"
    (repo / "platform" / "y.txt").write_text("changed outside infra\n")
    assert git_state_of(repo / "infra")["dirty"] == "false"
    (repo / "infra" / "new-file").write_text("untracked\n")
    assert git_state_of(repo / "infra")["dirty"] == "true"


def test_git_state_outside_a_checkout(tmp_path: Path) -> None:
    plain = tmp_path / "plain" / "infra"
    plain.mkdir(parents=True)
    assert git_state_of(plain)["rc"] == "1"


@pytest.mark.skipif(os.geteuid() != 0, reason="changing a file owner needs root")
def test_git_state_refuses_a_foreign_checkout(repo: Path) -> None:
    os.chown(repo, 4242, 4242)
    assert git_state_of(repo / "infra", env={"SUDO_UID": "1000"})["rc"] == "2"
    assert git_state_of(repo / "infra", env={"SUDO_UID": "4242"})["rc"] == "0"


def test_ref_matches_head(repo: Path) -> None:
    git(repo, "tag", "infra-v0.1.0")
    res = bash(
        f"""
        git_state {q(repo / "infra")}
        ref_matches_head infra-v0.1.0 && echo tag
        ref_matches_head HEAD && echo head
        ref_matches_head no-such-ref || echo missing
        """,
        env=AS_SUDO_USER,
    )
    assert res.stdout.split() == ["tag", "head", "missing"]


# --- orchestration with stubbed stages ----------------------------------------------------


def main_with_stubs(tmp_path: Path, args: str, overrides: str = "") -> tuple[int, list[str], str]:
    """Run main() of bootstrap.sh with every stage replaced by a recording stub."""
    calls = tmp_path / "calls"
    script = f"""
    source {q(BOOTSTRAP / "bootstrap.sh")}
    for stage in $(compgen -A function stage_); do
      eval "$stage() {{ echo $stage >>{q(calls)}; }}"
    done
    stage_doctor() {{ echo stage_doctor >>{q(calls)}; FINAL_RC=1; }}
    {overrides}
    main {args}
    """
    res = bash(script, load=False)
    lines = calls.read_text().split() if calls.exists() else []
    return res.returncode, lines, res.stderr


def test_main_runs_the_stages_in_order(tmp_path: Path) -> None:
    code, calls, err = main_with_stubs(tmp_path, "--machine lab1 --restore")
    assert code == 1  # doctor's exit code
    assert calls == [
        "stage_root",
        "stage_logging",
        "stage_git",
        "stage_prepreflight",
        "stage_tooling",
        "stage_frontend",
        "stage_preflight",
        "stage_ansible_env",
        "stage_ansible",
        "stage_restore",
        "stage_doctor",
    ]
    assert "doctor warnings" in err


def test_main_skips_restore_by_default(tmp_path: Path) -> None:
    _, calls, _ = main_with_stubs(tmp_path, "--machine lab1")
    assert "stage_restore" not in calls


def test_deliberate_exit_codes_pass_through(tmp_path: Path) -> None:
    code, calls, err = main_with_stubs(tmp_path, "--machine lab1", "stage_tooling() { return 50; }")
    assert code == 50
    assert calls[-1] == "stage_prepreflight"
    assert "tooling failed" in err


def test_unexpected_failures_are_internal_errors(tmp_path: Path) -> None:
    code, _, err = main_with_stubs(tmp_path, "--machine lab1", "stage_tooling() { false; }")
    assert code == 70
    assert "internal error: 'false' failed with exit status 1" in err


def test_fail_ends_with_its_code_and_message(tmp_path: Path) -> None:
    code, _, err = main_with_stubs(
        tmp_path, "--machine lab1", 'stage_git() { fail "$EXIT_OPERATOR" "commit infra/ first"; }'
    )
    assert code == 77
    assert "commit infra/ first" in err
    assert "operator action required" in err


def test_help_does_not_run_any_stage(tmp_path: Path) -> None:
    code, calls, _ = main_with_stubs(tmp_path, "--help")
    assert (code, calls) == (0, [])


def test_bootstrap_sh_is_executable() -> None:
    for script in ("bootstrap.sh", "lib/prepreflight.sh"):
        assert os.access(BOOTSTRAP / script, os.X_OK), script
    assert shutil.which("bash")
