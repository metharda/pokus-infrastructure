"""Tests for the backup role, its helper, restore.yml and rotate.yml (no restic, no database)."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import IO, Any

import pinka_backup as backup
import pytest

from ansible_testlib import (
    ANSIBLE,
    GROUP_VARS,
    ROLES,
    ansible_vars,
    evaluate,
    load_yaml,
    render_template,
)

ROLE = ROLES / "backup"


class FakeRunner:
    """Answers commands by the words after the program; writes dump bytes when asked."""

    def __init__(self, answers: Mapping[str, tuple[int, str]], dump: bytes = b"PGDMP-test") -> None:
        self.answers = dict(answers)
        self.dump = dump
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        stdin: IO[bytes] | None = None,
        stdout: IO[bytes] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(
            {"argv": list(argv), "env": dict(env or {}), "stdin": stdin.read() if stdin else None}
        )
        key = next((k for k in self.answers if k in " ".join(argv)), "")
        rc, out = self.answers.get(key, (0, ""))
        if stdout is not None and rc == 0:
            stdout.write(self.dump)
        return subprocess.CompletedProcess(list(argv), rc, out, "" if rc == 0 else "boom")


# Long and distinctive, so that no random name (the run's temporary
# directory) can contain it by chance; "pw" did in about 1 run in 200.
PASSWORD = "fixture-restic-password-q7z"  # noqa: S105 - a test fixture


@pytest.fixture
def config(tmp_path: Path) -> dict[str, Any]:
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "repository").write_text("sftp:backup@backup.example.com:/srv/restic/lab\n")
    (secrets / "password").write_text(PASSWORD)
    data = tmp_path / "srv" / "shared"
    data.mkdir(parents=True)
    return {
        "restic": "/usr/local/bin/restic",
        "host": "pinka-lab",
        "tag": "pinka",
        "secrets_dir": str(secrets),
        "known_hosts": "/etc/pinka/backup/known_hosts",
        "cache_dir": str(tmp_path / "cache"),
        "state": str(tmp_path / "state" / "state.json"),
        "textfile_dir": str(tmp_path / "textfile"),
        "paths": [str(data), str(tmp_path / "srv" / "missing")],
        "excludes": ["/etc/pinka/secrets"],
        "retention": {"daily": 7, "weekly": 4, "monthly": 12},
        "pgdump": {
            "container": "pinka-postgres",
            "database": "demo",
            "dir": str(tmp_path / "pg"),
            "keep": 2,
        },
    }


def metrics(config: Mapping[str, Any], job: str) -> dict[str, float]:
    text = (Path(config["textfile_dir"]) / f"pinka_backup_{job}.prom").read_text()
    return {
        line.split("{")[0]: float(line.rsplit(" ", 1)[1])
        for line in text.splitlines()
        if not line.startswith("#")
    }


SUMMARY = json.dumps(
    {
        "message_type": "summary",
        "snapshot_id": "a" * 64,
        "data_added": 1234,
        "total_files_processed": 5,
        "total_duration": 1.5,
    }
)


# --- restic ---------------------------------------------------------------------


def test_restic_reads_location_and_password_from_files(config: dict[str, Any]) -> None:
    env = backup.restic_env(config)
    assert env["RESTIC_REPOSITORY_FILE"].endswith("secrets/repository")
    assert env["RESTIC_PASSWORD_FILE"].endswith("secrets/password")
    argv = backup.restic_argv(config, backup.repository(config))
    assert argv[:2] == ["/usr/local/bin/restic", "-o"]
    assert argv[2].startswith("sftp.args=-i ")
    for option in (
        "StrictHostKeyChecking=yes",
        "BatchMode=yes",
        "UserKnownHostsFile=/etc/pinka/backup/known_hosts",
    ):
        assert option in argv[2]
    assert backup.restic_argv(config, "rest:https://backup.example.com/lab") == [
        "/usr/local/bin/restic"
    ]


def test_backup_initializes_a_new_repository(config: dict[str, Any]) -> None:
    runner = FakeRunner({"cat config": (10, ""), " backup ": (0, "{}\n" + SUMMARY + "\n")})
    assert backup.cmd_run(config, runner) == 0
    words = [" ".join(c["argv"][3:5]) for c in runner.calls]
    assert [w.strip() for w in words] == ["cat config", "init", "backup --json"]
    run = runner.calls[2]["argv"]
    assert run[run.index("--host") + 1] == "pinka-lab"
    assert run[run.index("--tag") + 1] == "pinka"
    assert PASSWORD not in " ".join(run)  # the password never appears on a command line
    gauges = metrics(config, "backup")
    assert gauges["pinka_backup_job_success"] == 1
    assert gauges["pinka_backup_data_added_bytes"] == 1234


def test_backup_lists_only_existing_paths(
    config: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, str] = {}

    class Recording(FakeRunner):
        def __call__(self, argv: Sequence[str], **kw: Any) -> subprocess.CompletedProcess[str]:
            if "--files-from" in argv:
                seen["paths"] = Path(argv[argv.index("--files-from") + 1]).read_text()
                seen["excludes"] = Path(argv[argv.index("--exclude-file") + 1]).read_text()
            return super().__call__(argv, **kw)

    assert backup.cmd_run(config, Recording({" backup ": (0, SUMMARY)})) == 0
    assert seen["paths"] == config["paths"][0] + "\n"
    assert seen["excludes"] == "/etc/pinka/secrets\n"


def test_failed_backup_keeps_the_last_success(config: dict[str, Any]) -> None:
    assert backup.cmd_run(config, FakeRunner({" backup ": (0, SUMMARY)})) == 0
    first = metrics(config, "backup")["pinka_backup_job_last_success_timestamp_seconds"]
    assert backup.cmd_run(config, FakeRunner({" backup ": (backup.INCOMPLETE, SUMMARY)})) == 1
    gauges = metrics(config, "backup")
    assert gauges["pinka_backup_job_success"] == 0
    assert gauges["pinka_backup_job_last_success_timestamp_seconds"] == first


def test_unconfigured_and_unreachable_repositories(config: dict[str, Any]) -> None:
    assert backup.cmd_run(config, FakeRunner({"cat config": (12, "")})) == 1
    assert metrics(config, "backup")["pinka_backup_job_success"] == 0
    (Path(config["secrets_dir"]) / "repository").unlink()
    for command in (backup.cmd_run, backup.cmd_prune, backup.cmd_snapshots):
        assert command(config, FakeRunner({})) == 2


def test_prune_keeps_this_hosts_snapshots_by_policy(config: dict[str, Any]) -> None:
    runner = FakeRunner({})
    assert backup.cmd_prune(config, runner) == 0
    argv = runner.calls[0]["argv"]
    assert argv[3] == "forget"
    for flag, value in (
        ("--keep-daily", "7"),
        ("--keep-weekly", "4"),
        ("--keep-monthly", "12"),
        ("--host", "pinka-lab"),
    ):
        assert argv[argv.index(flag) + 1] == value
    assert argv[-1] == "--prune"


def test_snapshots_restore_and_passwd(
    config: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    runner = FakeRunner({"snapshots": (0, '[{"id": "abc"}]')})
    assert backup.cmd_snapshots(config, runner) == 0
    assert json.loads(capsys.readouterr().out) == [{"id": "abc"}]
    runner = FakeRunner({})
    assert (
        backup.cmd_restore(config, "latest", ["/srv/pinka/platforms", "/etc/pinka/qa"], runner) == 0
    )
    argv = runner.calls[0]["argv"]
    assert argv[3:7] == ["restore", "latest", "--target", "/"]
    assert argv.count("--include") == 2
    assert argv[argv.index("--host") + 1] == "pinka-lab"  # "latest" of this host only
    assert backup.cmd_passwd(config, Path("/tmp/new"), runner) == 0  # noqa: S108 - a path argument only
    assert runner.calls[1]["argv"][3:] == ["key", "passwd", "--new-password-file", "/tmp/new"]  # noqa: S108


RECORD = {"name": "demo", "release": {"tag": "v1.2.0", "commit": "a" * 40}, "content": {}}


@pytest.mark.parametrize(
    ("answer", "printed", "code"),
    [
        ((0, json.dumps(RECORD)), RECORD, 0),
        ((1, ""), None, 0),  # the snapshot holds no record (stderr below)
        ((0, "[1]"), None, 0),
    ],
)
def test_the_platform_record_of_a_snapshot(
    config: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
    answer: tuple[int, str],
    printed: Any,
    code: int,
) -> None:
    class Missing(FakeRunner):
        def __call__(self, argv: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            done = super().__call__(argv, **kwargs)
            if done.returncode:
                stderr = 'path "/etc/pinka/platform/active.json" not found in snapshot'
                return subprocess.CompletedProcess(done.args, 1, "", stderr)
            return done

    runner = Missing({"dump": answer})
    assert backup.cmd_platform_record(config, "latest", runner) == code
    assert json.loads(capsys.readouterr().out) == printed
    argv = runner.calls[0]["argv"]
    assert argv[3:6] == ["dump", "latest", "/etc/pinka/platform/active.json"]
    assert argv[argv.index("--host") + 1] == "pinka-lab"


def test_a_failed_dump_is_reported(config: dict[str, Any]) -> None:
    assert backup.cmd_platform_record(config, "1" * 64, FakeRunner({"dump": (12, "")})) == 12
    assert backup.cmd_platform_record(config, "1" * 64, FakeRunner({"dump": (0, "{")})) == 1


# --- PostgreSQL -----------------------------------------------------------------


def test_pgdump_checks_and_keeps_the_newest(config: dict[str, Any]) -> None:
    directory = Path(config["pgdump"]["dir"])
    directory.mkdir()
    for stamp in ("20260101T000000Z", "20260102T000000Z"):
        (directory / f"demo-{stamp}.dump").write_bytes(b"old")
    runner = FakeRunner({})
    assert backup.cmd_pgdump(config, runner) == 0
    dumps = sorted(p.name for p in directory.glob("demo-*.dump"))
    assert len(dumps) == 2
    assert dumps[0] == "demo-20260102T000000Z.dump"
    newest = directory / dumps[1]
    assert newest.read_bytes() == b"PGDMP-test"
    assert newest.stat().st_mode & 0o777 == 0o600
    dump_call, list_call = runner.calls
    assert dump_call["argv"][:6] == ["docker", "exec", "--user", "postgres", "pinka-postgres", "sh"]
    assert "/run/secrets/postgres-superuser" in dump_call["argv"][7]  # read inside the container
    assert dump_call["argv"][8:] == ["pg_dump", "--format=custom", "--dbname=demo"]
    assert list_call["argv"][2] == "--interactive"
    assert list_call["argv"][-2:] == ["pg_restore", "--list"]
    assert list_call["stdin"] == b"PGDMP-test"
    assert metrics(config, "pgdump")["pinka_backup_dump_bytes"] == len(b"PGDMP-test")


@pytest.mark.parametrize("failing", ["pg_dump", "pg_restore"])
def test_failed_dump_leaves_nothing(config: dict[str, Any], failing: str) -> None:
    assert backup.cmd_pgdump(config, FakeRunner({failing: (1, "")})) == 1
    assert list(Path(config["pgdump"]["dir"]).iterdir()) == []
    assert metrics(config, "pgdump")["pinka_backup_job_success"] == 0


def test_pgrestore_replaces_the_dumped_objects(config: dict[str, Any], tmp_path: Path) -> None:
    dump = tmp_path / "demo-x.dump"
    dump.write_bytes(b"PGDMP-x")
    runner = FakeRunner({})
    assert backup.cmd_pgrestore(config, dump, runner) == 0
    argv = runner.calls[0]["argv"]
    assert argv[2] == "--interactive"
    assert argv[9:] == [
        "pg_restore",
        "--clean",
        "--if-exists",
        "--single-transaction",
        "--exit-on-error",
        "--dbname=demo",
    ]
    assert runner.calls[0]["stdin"] == b"PGDMP-x"


def test_main_dispatch_and_bad_configuration(config: dict[str, Any], tmp_path: Path) -> None:
    path = tmp_path / "backup.json"
    path.write_text(json.dumps(config))
    runner = FakeRunner({})
    assert backup.main(["--config", str(path), "prune"], runner) == 0
    assert runner.calls[0]["argv"][3] == "forget"
    assert backup.main(["--config", str(tmp_path / "missing.json"), "run"], runner) == 2


# --- role -----------------------------------------------------------------------


def role_variables(**extra: Any) -> dict[str, Any]:
    return ansible_vars(
        GROUP_VARS,
        GROUP_VARS.parent / "versions.yml",
        ROLE / "defaults" / "main.yml",
        ROLE / "vars" / "main.yml",
        **{"node_role": "full", **extra},
    )


def test_backed_up_paths_follow_the_specification() -> None:
    variables = role_variables(
        vault_restic_repository="sftp:backup@backup.example.com:/srv/restic/lab"
    )
    paths = evaluate("backup_paths", variables)
    assert paths == [
        "/srv/pinka/images",
        "/srv/pinka/datasets",
        "/srv/pinka/shared",
        "/srv/pinka/users",
        "/srv/pinka/backups/pg",
        "/srv/pinka/restricted",
        "/srv/pinka/training/datasets",
        "/srv/pinka/models/adapters",
        "/etc/pinka",
    ]
    assert "/etc/pinka/secrets" in evaluate("backup_excludes", variables)
    assert evaluate("backup_configured", variables) is True
    assert evaluate("backup_sftp", variables) is True


def test_the_platforms_backup_paths_are_backed_up() -> None:
    state = "/srv/pinka/platforms/demo/state/ctl/records"
    variables = role_variables(
        active_platform={"backup": {"database": False, "paths": [state]}},
        backup_include=["images"],
    )
    paths = evaluate("backup_paths", variables)
    assert state in paths
    for fixed in ("restricted", "training/datasets", "models/adapters"):
        assert f"/srv/pinka/{fixed}" in paths  # whatever backup_include says
    assert "/srv/pinka/shared" not in paths


def test_optional_paths_and_secrets() -> None:
    variables = role_variables(
        backup_include_models=True, backup_include_state=["prometheus"], backup_include_secrets=True
    )
    paths = evaluate("backup_paths", variables)
    assert "/srv/pinka/models" in paths
    assert "/srv/pinka/state/prometheus" in paths
    assert evaluate("backup_excludes", variables) == []


def enabled_jobs(variables: dict[str, Any]) -> dict[str, bool]:
    return {
        j["name"]: evaluate(j["enabled"].strip()[2:-2], variables)
        for j in load_yaml(ROLE / "vars" / "main.yml")["backup_jobs"]
    }


def test_unconfigured_repository_disables_backup_and_prune() -> None:
    variables = role_variables(active_platform=DUMPED)
    assert evaluate("backup_configured", variables) is False
    jobs = enabled_jobs(variables)
    assert jobs == {"pinka-pgdump": True, "pinka-backup": False, "pinka-backup-prune": False}


DUMPED = {"database": {"database": "demo"}, "backup": {"database": True, "paths": []}}


@pytest.mark.parametrize(
    ("platform", "node_role", "dumped"),
    [
        (DUMPED, "full", True),
        (DUMPED, "lab", True),
        (DUMPED, "inference", False),
        ({}, "full", False),
        ({"database": None, "backup": {"database": False, "paths": []}}, "full", False),
        ({"database": {"database": "demo"}, "backup": {"database": False}}, "full", False),
    ],
)
def test_the_dump_follows_the_active_platform(
    platform: dict[str, Any], node_role: str, dumped: bool
) -> None:
    variables = role_variables(active_platform=platform, node_role=node_role)
    assert enabled_jobs(variables)["pinka-pgdump"] is dumped
    assert evaluate("backup_database", variables) == (
        "demo" if (platform.get("database") or {}).get("database") else ""
    )


def test_units_render() -> None:
    variables = role_variables()
    for job in load_yaml(ROLE / "vars" / "main.yml")["backup_jobs"]:
        item = {
            k: evaluate(v.strip()[2:-2], variables) if "{{" in str(v) else v for k, v in job.items()
        }
        # The task loops over (job, kind) pairs and binds the job to backup_job.
        service = render_template(
            "backup", "backup-job.service.j2", {**variables, "backup_job": item}
        )
        assert f"--config /etc/pinka/backup/backup.json {item['command']}" in service
        assert "Type=oneshot" in service
        timer = render_template("backup", "backup-job.timer.j2", {**variables, "backup_job": item})
        assert f"OnCalendar={item['schedule']}" in timer
        assert "Persistent=true" in timer


def test_restic_download_is_pinned() -> None:
    variables = role_variables(ansible_facts={"architecture": "aarch64"})
    asset = evaluate("backup_restic_asset", variables)
    assert asset["url"].endswith("_linux_arm64.bz2")
    assert len(asset["sha256"]) == 64


# --- restore.yml and rotate.yml -------------------------------------------------


def play_task(path: Path, name: str) -> dict[str, Any]:
    def walk(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for task in tasks:
            out.append(task)
            out += walk(task.get("block") or []) + walk(task.get("rescue") or [])
        return out

    tasks = [
        t
        for play in load_yaml(path)
        for section in ("pre_tasks", "tasks")
        for t in walk(play.get(section) or [])
    ]
    (match,) = [t for t in tasks if t.get("name") == name]
    return match


SNAPSHOTS = [
    {
        "id": "1" * 64,
        "short_id": "11111111",
        "time": "2026-10-01T03:00:00Z",
        "paths": ["/etc/pinka", "/srv/pinka/platforms"],
    },
    {
        "id": "2" * 64,
        "short_id": "22222222",
        "time": "2026-10-02T03:00:00Z",
        "paths": ["/etc/pinka", "/srv/pinka/platforms"],
    },
]


@pytest.mark.parametrize(
    ("chosen", "expected"),
    [("latest", "2" * 64), ("11111111", "1" * 64), ("1" * 64, "1" * 64), ("abcdef12", None)],
)
def test_restore_finds_the_snapshot(chosen: str, expected: str | None) -> None:
    task = play_task(ANSIBLE / "restore.yml", "Find the chosen snapshot (an ID or a prefix of one)")
    entry = evaluate(
        task["ansible.builtin.set_fact"]["restore_entry"].strip()[2:-2],
        {"restore_snapshots": SNAPSHOTS, "restore_chosen": chosen},
    )
    assert entry.get("id") == expected


def test_restore_brings_back_data_but_not_rendered_configuration() -> None:
    task = play_task(ANSIBLE / "restore.yml", "Restore the files")
    play = load_yaml(ANSIBLE / "restore.yml")[0]
    argv = evaluate(
        task["ansible.builtin.command"]["argv"].strip()[2:-2],
        {
            "restore_backup": "/usr/local/libexec/pinka/backup",
            "restore_entry": SNAPSHOTS[0],
            "pinka_root": "/srv/pinka",
            "restore_etc_paths": play["vars"]["restore_etc_paths"],
        },
    )
    assert argv[:4] == ["/usr/local/libexec/pinka/backup", "restore", "--snapshot", "1" * 64]
    includes = [a.removeprefix("--include=") for a in argv[4:]]
    assert includes[0] == "/srv/pinka/platforms"
    assert "/etc/pinka" not in includes  # bootstrap renders the rest of /etc/pinka
    assert "/etc/pinka/vllm/current.yml" in includes


def restore_names() -> list[str]:
    block = play_task(ANSIBLE / "restore.yml", "Restore the snapshot")
    names = []
    for task in block["block"]:
        names.append(task["name"])
        names += [t["name"] for t in task.get("block", [])]
    return names


def test_restore_drains_first_and_starts_the_platform_last() -> None:
    names = restore_names()
    assert names.index("Drain and stop the platform") < names.index("Stop the lab")
    assert names.index("Load the newest PostgreSQL dump") < names.index(
        "Redeploy the platform the snapshot recorded"
    )
    assert names.index("Deploy the recorded release and content") < names.index("Start the lab")
    assert names[-1] == "Start the platform and wait for its health hook"
    drain = play_task(ANSIBLE / "restore.yml", "Drain and stop the platform")
    assert drain["ansible.builtin.include_role"] == {"name": "platform", "tasks_from": "stop"}


def test_restore_checks_the_recorded_platform_before_it_changes_anything() -> None:
    # Owner decision with the "commit" for 3d.4: a commit the mirror lost, a
    # moved tag or another tree refuses the restore before the drain.
    plays = load_yaml(ANSIBLE / "restore.yml")
    top = [t["name"] for play in plays for t in play.get("tasks", [])]
    assert top.index("Check the platform the snapshot records") < top.index("Restore the snapshot")
    check = play_task(ANSIBLE / "restore.yml", "Check the platform the snapshot records")
    steps = {t["name"]: t for t in check["block"]}
    include = steps["Check that the recorded commits and trees can be deployed"]
    assert include["ansible.builtin.include_role"] == {"name": "platform", "tasks_from": "check"}
    assert include["vars"]["platform"] == "{{ restore_platform }}"
    tasks = load_yaml(ROLES / "platform" / "tasks" / "check.yml")
    run = next(t for t in tasks if "ansible.builtin.command" in t)
    assert run["ansible.builtin.command"]["argv"][-1] == "--inspect"
    signal = next(t for t in tasks if t.get("ansible.builtin.include_role"))
    assert signal["vars"]["bootstrap_signal_code"] == 65


@pytest.mark.parametrize(
    ("release", "signed", "ref"),
    [
        # Exactly the recorded commit and tree (owner decision with the "commit"
        # for 3d.4); the recorded tag only where signed tags are required.
        ({"tag": "v1.2.0", "commit": "a" * 40, "tree": "c" * 40}, False, "a" * 40),
        ({"tag": None, "commit": "a" * 40, "tree": "c" * 40}, False, "a" * 40),
        ({"tag": "v1.2.0", "commit": "a" * 40, "tree": "c" * 40}, True, "v1.2.0"),
        ({"tag": None, "commit": "a" * 40, "tree": "c" * 40}, True, "a" * 40),
    ],
)
def test_restore_pins_the_recorded_commit_and_tree(
    release: dict[str, Any], signed: bool, ref: str
) -> None:
    task = play_task(
        ANSIBLE / "restore.yml", "Pin the platform source to the recorded commit and tree"
    )
    source = {"repo": "https://example.com/platform.git", "ref": "main", "path": "platform"}
    pinned = evaluate(
        task["ansible.builtin.set_fact"]["restore_platform"].strip()[2:-2],
        {"platform": source, "restore_record": {"release": release}, "require_signed_tags": signed},
    )
    assert pinned == {**source, "ref": ref, "commit": "a" * 40, "tree": "c" * 40}


def test_restore_pins_the_recorded_content() -> None:
    task = play_task(
        ANSIBLE / "restore.yml", "Pin the content sources to the recorded commits and trees"
    )
    expression = task["ansible.builtin.set_fact"]["restore_content"].strip()[2:-2]
    recorded_expr = task["vars"]["restore_recorded"].strip()[2:-2]
    record = {"content": {"cases": {"tag": "cases-v1", "commit": "b" * 40, "tree": "d" * 40}}}
    content: dict[str, Any] = {}
    for key, value in {
        "cases": {"repo": "https://example.com/c.git", "ref": "main"},
        "extra": {"repo": "https://example.com/e.git", "ref": "v2"},
    }.items():
        item = {"key": key, "value": value}
        recorded = evaluate(recorded_expr, {"restore_record": record, "item": item})
        content = evaluate(
            expression,
            {
                "restore_content": content,
                "item": item,
                "restore_recorded": recorded,
                "require_signed_tags": False,
            },
        )
    assert content["cases"] == {
        "repo": "https://example.com/c.git",
        "ref": "b" * 40,
        "commit": "b" * 40,
        "tree": "d" * 40,
    }
    assert content["extra"] == {"repo": "https://example.com/e.git", "ref": "v2"}  # not recorded


def test_rotation_revokes_the_replaced_certificates() -> None:
    tasks = load_yaml(ROLES / "incus" / "tasks" / "rotate_certificates.yml")
    (revoke,) = [
        t for t in tasks if t["name"] == "Remove the trust entries of the replaced certificates"
    ]
    item = {"fingerprints": {"sha256": "AB:CD:" + "EF:" * 29 + "01"}}
    argv = [
        evaluate(a.strip()[2:-2], {"item": item}) if "{{" in a else a
        for a in revoke["ansible.builtin.command"]["argv"]
    ]
    assert argv[:4] == ["incus", "config", "trust", "remove"]
    assert argv[4] == "abcd" + "ef" * 29 + "01"


def test_superuser_password_is_staged_before_it_changes() -> None:
    tasks = load_yaml(ROLES / "postgres" / "tasks" / "rotate.yml")
    (change,) = [t for t in tasks if t["name"] == "Change the superuser password"]
    names = [t["name"] for t in change["block"]]
    assert names == [
        "Stage the new superuser password",
        "Set the new superuser password with the current one",
        "Replace the superuser password file",
    ]
    assert all(t.get("no_log") for t in change["block"][:2])
    assert change["rescue"]
