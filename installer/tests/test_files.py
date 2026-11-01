"""The files the installer writes."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from installer_testlib import public_key
from ruamel.yaml import YAML
from ruamel.yaml.representer import RoundTripRepresenter
from test_answers import full_answers

from pinka_installer import answers as answers_mod
from pinka_installer import apply, cli, groupvars, hostvars, keys, state, validators, vault
from pinka_installer.answers import Answers, Researcher
from pinka_installer.paths import Paths

PASSWORD = "vault-password-for-tests"  # noqa: S105 - test fixture
SECRETS = vault.Provided(tailscale_authkey="authkey-placeholder")


def counter() -> vault.Callable[[], str]:  # type: ignore[name-defined]
    """A predictable secret generator."""
    numbers = iter(range(1000))
    return lambda: f"generated-{next(numbers)}"


def test_host_vars_from_the_example(repo: Paths) -> None:
    answers = full_answers()
    example = repo.example_host_vars.read_text(encoding="utf-8")
    text = hostvars.render(answers, None, example)
    assert text.startswith("# Machine configuration of pinka-lab")
    assert "Managed by pinka (ansible/inventory/host_vars/example-machine.yml)" not in text
    assert "# production | staging." in text  # the example's explanations stay
    values = hostvars.load(text)
    assert values["machine_name"] == "pinka-lab"
    assert values["incus_pool_device"] == values["incus_pool_device_confirm"]
    assert values["incus_pool_source"] == "device"
    assert "inference_remote_node" not in values  # a full node has no peer
    assert values["vllm_default_deployment"] is None
    assert values["backup_schedule"] == "*-*-* 03:00:00"
    url = "git@github.com:example-org/example-platform.git"
    assert values["platform"] == {
        "repo": url,
        "ref": "example-v0.1.0",
        "path": "release",
        "tag_prefix": "example-v",
    }
    assert values["content"] == {
        "cases": {"repo": url, "ref": "cases-v1", "path": "cases", "tag_prefix": "cases-v"}
    }
    assert values["require_signed_tags"] is True
    assert values["pinka_allowed_signers"] == ["researcher1@example.com ssh-ed25519 AAAAexample"]
    assert values["pinka_source_known_hosts"] == ["git.example.com ssh-ed25519 AAAAexample"]
    assert values["platform_settings"] == {"ctl": {"job": "tunecpu"}}
    assert values["gpu_arbiter"] == {"after_boot": "idle", "restore_after_training": False}
    assert values["training_caps"]["memory_max"] == "8G"
    assert values["images_blueprints"] == ["_base", "web-nginx-flask"]
    assert "# The platform release to deploy (contract v2)" in text  # the example's comment
    assert values["expected"] == {"memory_gib": 22, "cpu_threads": 20}
    assert "tailscale_tag" not in values  # the lab mode's default applies


def test_host_vars_round_trip(repo: Paths) -> None:
    answers = full_answers().normalized()
    text = hostvars.render(answers, None, repo.example_host_vars.read_text(encoding="utf-8"))
    back = hostvars.to_answers(hostvars.load(text), Answers(researchers=answers.researchers))
    for section in (
        "machine",
        "resources",
        "storage",
        "gpu",
        "remote_access",
        "platform",
        "content",
        "source_trust",
        "platform_settings",
        "gpu_arbiter",
        "training_caps",
        "images_build",
    ):
        assert getattr(back, section) == getattr(answers, section), section
    assert back.backups.schedule == answers.backups.schedule
    assert back.backups.retention == answers.backups.retention
    # Without a platform, host_vars say so ({}), and nothing of one is read back.
    answers.platform = answers_mod.Source()
    text = hostvars.render(answers, text, "")
    assert hostvars.load(text)["platform"] == {}
    assert not hostvars.to_answers(hostvars.load(text), Answers()).has_platform


def test_host_vars_modify_keeps_comments_and_drops_unanswered(repo: Paths) -> None:
    answers = full_answers()
    first = hostvars.render(answers, None, repo.example_host_vars.read_text(encoding="utf-8"))
    edited = first.replace("lab_mode: production", "lab_mode: production  # decided by the PI")
    answers.resources.cpuset_services = answers.resources.cpuset_targets = ""
    answers.machine.power_policy = "never_sleep"
    second = hostvars.render(answers, edited, "")
    assert "# decided by the PI" in second
    assert "cpuset_services" not in second
    assert "power_policy: never_sleep" in second
    assert hostvars.render(answers, second, "") == second  # stable


def test_host_vars_close_only_with_consent(repo: Paths) -> None:
    # Pre-existing access: the keys are written only when
    # the owner consented, so group_vars' defaults (nothing closed) apply.
    answers = full_answers()
    first = hostvars.render(answers, None, repo.example_host_vars.read_text(encoding="utf-8"))
    values = hostvars.load(first)
    assert values["firewall_close_listeners"] == ["tcp/8080", "udp/5353"]
    assert values["firewall_close_install_path"] is True
    answers.remote_access.close_listeners = []
    answers.remote_access.close_install_path = False
    second = hostvars.render(answers, first, "")
    assert "firewall_close" not in second
    back = hostvars.to_answers(hostvars.load(second), Answers())
    assert (back.remote_access.close_listeners, back.remote_access.close_install_path) == (
        [],
        False,
    )


def test_group_vars_round_trip_keeps_everything_else(repo: Paths) -> None:
    text = repo.group_vars_main.read_text(encoding="utf-8")
    assert groupvars.render(text, *groupvars.researchers(text)) == text
    people = [Researcher("researcher1", True, "Researcher One"), Researcher("alice2", False)]
    edited = groupvars.render(text, people, ["researcher3"])
    assert "# Researchers that left" in edited
    assert groupvars.researchers(edited) == (people, ["researcher3"])
    assert edited.count("\n") == text.count("\n") - 1  # researcher2 and researcher3 gone, one added
    before = YAML(typ="safe").load(text)
    after = YAML(typ="safe").load(edited)
    assert {k: v for k, v in before.items() if not k.startswith("researchers")} == {
        k: v for k, v in after.items() if not k.startswith("researchers")
    }


def test_postgres_roles_come_from_the_release(repo: Paths) -> None:
    # The platform's roles are known only from its release: the vault
    # example shows their shape with placeholders, and the installer generates
    # them only from the release's inspection (release.Needs).
    example = YAML(typ="safe").load(repo.vault_example.read_text(encoding="utf-8"))
    assert list(example["vault_postgres_passwords"]) == [
        "<platform>_<service>",
        "<platform>_migrator",
        "<platform>_reader",
        "<platform>_grafana",
    ]
    assert vault.postgres_roles(repo.postgres_defaults) == []


def test_vault_build_generates_once_and_keeps_existing(repo: Paths) -> None:
    roles = vault.postgres_roles(repo.postgres_defaults)
    answers = full_answers()
    first = vault.build(
        answers, None, vault.Provided(), roles, counter(), platform_secrets=("ctl.api-token",)
    )
    data = first.data
    assert list(data) == list(vault.ORDER)
    assert data["vault_platform_secrets"] == {"ctl": {"api-token": "generated-0"}}
    assert set(data["vault_postgres_passwords"]) == set(roles)
    assert set(data["vault_grafana_user_passwords"]) == {"researcher1"}  # researcher2: no Grafana
    assert data["vault_restic_repository"] == "sftp:backup@backup.example.com:/srv/restic/pinka-lab"
    assert data["vault_tailscale_authkey"] == ""  # interactive login
    assert first.shown_once == [("restic password", data["vault_restic_password"])]
    answers.researchers.append(Researcher("alice2", grafana=True))
    second = vault.build(answers, data, vault.Provided(), roles, counter())
    assert second.generated == ["vault_grafana_user_passwords.alice2"]
    assert second.shown_once == []
    for key in (
        "vault_postgres_passwords",
        "vault_grafana_admin_password",
        "vault_restic_password",
    ):
        assert second.data[key] == data[key]


def test_vault_platform_secrets_from_the_release(repo: Paths) -> None:
    # One secret per name the manifest gives: provided (TUI or environment),
    # kept, or generated unless the answers call it provided.
    answers = full_answers()  # ctl.api-token: generate, ctl.webhook: provided
    keys = ("ctl.api-token", "ctl.webhook")
    roles = ["example_ctl", "example_migrator"]
    with pytest.raises(vault.VaultError, match="PINKA_SECRET_PLATFORM_CTL_WEBHOOK"):
        vault.build(answers, None, vault.Provided(), roles, counter(), platform_secrets=keys)
    environ = {"PINKA_SECRET_PLATFORM_CTL_WEBHOOK": "hook-secret", "PINKA_SECRET_X": "y"}
    provided = vault.Provided.from_environment(environ, keys)
    assert provided.platform == {"ctl.webhook": "hook-secret"}
    built = vault.build(answers, None, provided, roles, counter(), platform_secrets=keys)
    assert built.data["vault_platform_secrets"] == {
        "ctl": {"api-token": "generated-2", "webhook": "hook-secret"}
    }
    assert set(built.data["vault_postgres_passwords"]) == set(roles)
    assert "vault_platform_secrets.ctl.api-token" in built.generated
    assert "hook-secret" not in repr(provided)  # never shown
    again = vault.build(
        answers, built.data, vault.Provided(), roles, counter(), platform_secrets=keys
    )
    assert again.data == built.data and again.generated == []


def test_vault_provided_secrets(repo: Paths) -> None:
    roles = vault.postgres_roles(repo.postgres_defaults)
    answers = full_answers()
    answers.remote_access.tailscale_login = "auth_key"
    with pytest.raises(vault.VaultError, match="PINKA_SECRET_TAILSCALE_AUTHKEY"):
        vault.build(answers, None, vault.Provided(), roles)
    answers.secrets.grafana_admin = "provided"
    provided = vault.Provided(tailscale_authkey="authkey-placeholder")
    provided.grafana_admin_password = "short"  # noqa: S105 - test fixture
    with pytest.raises(vault.VaultError, match="8 or more"):
        vault.build(answers, None, provided, roles)
    provided.grafana_admin_password = "long-enough-admin"  # noqa: S105 - test fixture
    built = vault.build(answers, None, provided, roles)
    assert built.data["vault_grafana_admin_password"] == provided.grafana_admin_password
    assert built.data["vault_tailscale_authkey"] == "authkey-placeholder"
    assert "authkey-placeholder" not in repr(provided)  # never in logs or tracebacks
    env = vault.Provided.from_environment({"PINKA_SECRET_HF_TOKEN": "hf-placeholder"})
    assert env.values() == ["hf-placeholder"]


def test_vault_encryption_round_trip() -> None:
    data = {"vault_grafana_admin_password": "x" * 12, "vault_postgres_passwords": {"a": "b"}}
    text = vault.encrypt(data, PASSWORD)
    assert text.startswith("$ANSIBLE_VAULT;1.1;AES256")
    assert "x" * 12 not in text
    assert vault.decrypt(text, PASSWORD) == data
    with pytest.raises(vault.VaultError, match="wrong vault password"):
        vault.decrypt(text, "not-the-password")


def test_plan_writes_everything_and_is_idempotent(repo: Paths) -> None:
    answers = full_answers()
    key = keys.check(public_key(), use_ssh_keygen=False)
    plan = apply.plan(answers, repo, SECRETS, PASSWORD, {"researcher1": key})
    names = [c.path.relative_to(repo.infra).as_posix() for c in plan.changes]
    assert names == [
        "ansible/inventory/host_vars/pinka-lab.yml",
        "ansible/group_vars/all/main.yml",
        "users/researcher1.pub",
        "ansible/group_vars/all/vault.yml",
        "installer-answers/pinka-lab.yml",
    ]
    assert plan.warnings == [
        "no SSH key file users/researcher2.pub: researcher2 gets no SSH access"
    ]
    vault_change = plan.changes[3]
    review = vault_change.diff(repo.infra)
    assert "(encrypted)" in review and "+ vault_restic_password (generated)" in review
    assert plan.shown_once[0][1] not in review
    assert plan.changes[0].diff(repo.infra).startswith("--- /dev/null")
    written = plan.write()
    assert len(written) == 5
    assert (repo.vault.stat().st_mode & 0o777) == 0o600
    again = apply.plan(answers, repo, SECRETS, PASSWORD, {"researcher1": key})
    assert [c.path for c in again.changes if c.changed] == []
    assert again.changes[3].summary == ["no secret changes"]


def test_written_files_do_not_depend_on_what_the_process_wrote_before(repo: Paths) -> None:
    # Every round-trip writer writes null out with its own representer. Before, the
    # group_vars writer registered it on ruamel's RoundTripRepresenter class, so host_vars
    # (and the answers file) came out differently once group vars had been written: a TUI
    # that planned the files twice and a replay in a fresh process disagreed.
    assert RoundTripRepresenter.yaml_representers[type(None)] is RoundTripRepresenter.represent_none
    answers = full_answers()
    example = repo.example_host_vars.read_text(encoding="utf-8")
    first = hostvars.render(answers, None, example), answers_mod.dumps(answers)
    assert "\nvllm_default_deployment: null " in first[0]
    groupvars.render(
        repo.group_vars_main.read_text(encoding="utf-8"),
        answers.researchers,
        answers.researchers_removed,
    )
    assert (hostvars.render(answers, None, example), answers_mod.dumps(answers)) == first
    assert RoundTripRepresenter.yaml_representers[type(None)] is RoundTripRepresenter.represent_none


def test_plan_needs_the_right_vault_password(repo: Paths) -> None:
    answers = full_answers()
    apply.plan(answers, repo, SECRETS, PASSWORD).write()
    with pytest.raises(vault.VaultError):
        apply.plan(answers, repo, SECRETS, "wrong-password")


def test_stored_vault_password_is_root_only(repo: Paths) -> None:
    answers = full_answers()
    answers.secrets.store_vault_password = True
    plan = apply.plan(answers, repo, SECRETS, PASSWORD)
    stored = plan.changes[-1]
    assert (stored.path, stored.mode, stored.chown, stored.secret) == (
        repo.stored_vault_pass,
        0o600,
        False,
        True,
    )


def test_split_smt_cores_are_a_warning(repo: Paths) -> None:
    answers = full_answers()
    services, targets = answers.resources.cpuset_services, answers.resources.cpuset_targets
    first = min(validators.parse_cpuset(services))
    last = max(validators.parse_cpuset(targets))
    split = [frozenset({first, last})]
    plan = apply.plan(answers, repo, SECRETS, PASSWORD, smt_cores=split)
    assert any(w.startswith("SMT siblings of one core are split") for w in plan.warnings)
    together = [frozenset({first, first + 1})]
    plan = apply.plan(answers, repo, SECRETS, PASSWORD, smt_cores=together)
    assert not any("SMT" in w for w in plan.warnings)


def test_files_go_to_the_invoking_user(repo: Paths, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, int, int]] = []
    monkeypatch.setenv("SUDO_UID", "1000")
    monkeypatch.setenv("SUDO_GID", "1000")
    monkeypatch.setattr(os, "chown", lambda p, u, g: calls.append((str(p), u, g)))
    apply.plan(full_answers(), repo, SECRETS, PASSWORD).write()
    owned = {
        Path(p).name.lstrip(".").removesuffix(".tmp") for p, u, g in calls if (u, g) == (1000, 1000)
    }
    assert {"installer-answers", "pinka-lab.yml", "vault.yml", "main.yml"} <= owned


def test_read_password_file(tmp_path: Path) -> None:
    path = tmp_path / "vault-pass"
    path.write_text("secret-line\nignored\n", encoding="utf-8")
    path.chmod(0o600)
    assert apply.read_password_file(path) == "secret-line"
    path.chmod(0o644)
    with pytest.raises(vault.VaultError, match="chmod 600"):
        apply.read_password_file(path)
    path.write_text("", encoding="utf-8")
    path.chmod(0o600)
    with pytest.raises(vault.VaultError, match="empty"):
        apply.read_password_file(path)


def test_cli_describe_reports_the_mok_consent(
    repo: Paths, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    loaded = answers_mod.load(repo.infra / "tests" / "answers" / "ci.yml")
    loaded.gpu.install_driver = "auto"
    loaded.gpu.accept_mok_enrollment = True
    path = tmp_path / "answers.yml"
    path.write_text(answers_mod.dumps(loaded), encoding="utf-8")
    assert "accept_mok_enrollment: true" in path.read_text(encoding="utf-8")
    base = ["--infra", str(repo.infra)]
    assert cli.main([*base, "describe", "--answers", str(path)]) == 0
    assert "accept_mok_enrollment=true" in capsys.readouterr().out.splitlines()
    text = path.read_text(encoding="utf-8")  # consent without auto is a contradiction
    path.write_text(text.replace("install_driver: auto", "install_driver: manual"), "utf-8")
    assert cli.main([*base, "describe", "--answers", str(path)]) == 1
    assert "gpu/accept_mok_enrollment" in capsys.readouterr().err


def test_cli_apply_replays_the_ci_answers(
    repo: Paths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    password = tmp_path / "vault-pass"
    password.write_text(PASSWORD + "\n", encoding="utf-8")
    password.chmod(0o600)
    answers = repo.infra / "tests" / "answers" / "ci.yml"
    base = ["--infra", str(repo.infra)]
    assert cli.main([*base, "describe", "--answers", str(answers)]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "machine=ci-placeholder",
        "mode=fresh",
        "snapshot=",
        "accept_mok_enrollment=false",
    ]
    replay = [*base, "apply", "--answers", str(answers), "--vault-password-file", str(password)]
    monkeypatch.delenv("PINKA_SECRET_TAILSCALE_AUTHKEY", raising=False)
    assert cli.main(replay) == 1
    assert "PINKA_SECRET_TAILSCALE_AUTHKEY" in capsys.readouterr().err
    monkeypatch.setenv("PINKA_SECRET_TAILSCALE_AUTHKEY", "authkey-placeholder")
    assert cli.main(replay) == 0
    out = capsys.readouterr().out
    assert "wrote ansible/inventory/host_vars/ci-placeholder.yml" in out
    assert "authkey-placeholder" not in out
    assert "keep a copy outside this host" in out  # the restic password was generated
    restic = vault.decrypt(repo.vault.read_text(encoding="utf-8"), PASSWORD)[
        "vault_restic_password"
    ]
    assert restic not in out
    assert cli.main(replay) == 0
    assert "nothing to change" in capsys.readouterr().out
    assert cli.main([*replay, "--machine", "other"]) == 1
    data = vault.decrypt(repo.vault.read_text(encoding="utf-8"), PASSWORD)
    assert data["vault_tailscale_authkey"] == "authkey-placeholder"


def test_cli_rejects_bad_answers(
    repo: Paths, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "bad.yml"
    bad.write_text("schema_version: 1\n", encoding="utf-8")
    assert cli.main(["--infra", str(repo.infra), "validate", "--answers", str(bad)]) == 1
    assert "cannot be used" in capsys.readouterr().err
    assert cli.main(["--infra", str(repo.infra), "validate", "--answers", str(tmp_path / "x")]) == 1


def test_resume_state(tmp_path: Path) -> None:
    path = tmp_path / "installer" / "state.json"
    assert state.load(path) is None
    saved = state.State(screen="storage", answers={"mode": "fresh"}, phases_done=["preflight"])
    state.save(saved, path)
    assert (path.stat().st_mode & 0o777) == 0o600
    assert state.load(path) == saved
    path.write_text('{"schema_version": 99}', encoding="utf-8")
    assert state.load(path) is None
    state.clear(path)
    assert not path.exists()
