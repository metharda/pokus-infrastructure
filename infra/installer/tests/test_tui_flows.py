"""Complete installer flows driven with Textual's pilot (BUILD_PROMPT §18.9).

Each flow starts the real application on a copy of the repository files, a
fake host whose profile preflight produced from one of its fixtures, and a
fake bootstrap that replays a recorded event stream. Keys are pressed and
fields filled the way an operator would; the files written are then checked.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

from installer_testlib import copy_repo, private_key, public_key
from textual.pilot import Pilot
from textual.widgets import (
    Checkbox,
    Collapsible,
    DataTable,
    Input,
    RadioButton,
    Select,
    Static,
    TextArea,
)
from tui_fakes import (
    DISK_BY_ID,
    FakeHost,
    FakeRunner,
    app,
    context,
    preflight,
)

from pinka_installer import answers as answers_mod
from pinka_installer import apply, cli, hostvars, state, vault
from pinka_installer.paths import Paths
from pinka_installer.tui.app import InstallerApp
from pinka_installer.tui.base import Context, StepScreen
from pinka_installer.tui.host import CommandResult
from pinka_installer.tui.screens_install import SignalScreen
from pinka_installer.tui.widgets import Confirm, PasswordPrompt, ShownOnce, TypedConfirm

PASSWORD = "correct horse battery staple 42"  # noqa: S105 - test fixture
SIZE = (120, 40)
Flow = Callable[[Pilot[int]], Awaitable[None]]


def drive(installer: InstallerApp, flow: Flow) -> int | None:
    """Run the application with a pilot until the flow is done; returns its result."""

    async def main() -> None:
        async with installer.run_test(size=SIZE) as pilot:
            await settle(pilot)
            await flow(pilot)
            await settle(pilot)

    asyncio.run(main())
    return installer.return_value


async def settle(pilot: Pilot[Any]) -> None:
    """Let workers finish and their results reach the screen."""
    for _ in range(3):
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()


def at(pilot: Pilot[Any]) -> str:
    return cast(InstallerApp, pilot.app).current


def shown_errors(pilot: Pilot[Any]) -> list[str]:
    return [
        str(s.content) for s in pilot.app.screen.query(".error").results(Static) if str(s.content)
    ]


async def click(pilot: Pilot[Any], selector: str) -> None:
    """Click a widget; then wait out the button's 0.2 s press effect, which ignores clicks."""
    await pilot.click(selector)
    await pilot.pause(0.25)


async def forward(pilot: Pilot[Any], expect: str) -> None:
    """Press Ctrl+N and expect a screen."""
    await pilot.press("ctrl+n")
    await settle(pilot)
    assert at(pilot) == expect, (at(pilot), shown_errors(pilot))


def fill(pilot: Pilot[Any], **values: str) -> None:
    """Fill inputs by id (underscores become dashes unless the id has them)."""
    screen = pilot.app.screen
    for key, text in values.items():
        found = screen.query(f"#{key}") or screen.query(f"#{key.replace('_', '-')}")
        cast(Input, found.first()).value = text


def pick(pilot: Pilot[Any], button: str) -> None:
    pilot.app.screen.query_one(f"#{button}", RadioButton).value = True


def tick(pilot: Pilot[Any], box: str, on: bool = True) -> None:
    pilot.app.screen.query_one(f"#{box}", Checkbox).value = on


def notes(installer: InstallerApp) -> list[str]:
    """Record the notifications (run_test does not show them)."""
    found: list[str] = []

    def record(message: str, **_: Any) -> None:
        found.append(str(message))

    installer.notify = record  # type: ignore[method-assign]
    return found


async def add_researcher(pilot: Pilot[Any], name: str, key: str, admin: bool = False) -> None:
    await click(pilot, "#add")
    await pilot.pause()
    form = pilot.app.screen
    form.query_one("#r-name", Input).value = name
    form.query_one("#r-keys", TextArea).text = key
    form.query_one("#r-admin", Checkbox).value = admin
    await click(pilot, "#r-ok")
    await pilot.pause()


async def welcome_to_mode(pilot: Pilot[Any]) -> None:
    assert at(pilot) == "welcome"
    await forward(pilot, "preflight")
    assert "Outcome:" in str(pilot.app.screen.query_one("#outcome", Static).content)
    await forward(pilot, "mode")


async def secrets_to_progress(pilot: Pilot[Any]) -> None:
    """Secrets, Review, Install: until bootstrap has run."""
    assert at(pilot) == "secrets"
    fill(pilot, vault1=PASSWORD, vault2=PASSWORD)
    await forward(pilot, "review")
    screen = pilot.app.screen
    assert not screen.query(".banner.bad") or all(
        "Fix these first" not in str(s.content) for s in screen.query(".banner.bad").results(Static)
    )
    await forward(pilot, "progress")


async def progress_to_end(pilot: Pilot[Any]) -> None:
    """From a finished install through Doctor and Finish."""
    assert at(pilot) == "progress"
    await forward(pilot, "doctor")
    status = str(pilot.app.screen.query_one("#doctor-status", Static).content)
    assert status == "2 passed · 1 warnings · 0 failures"
    await forward(pilot, "finish")
    await pilot.press("ctrl+n")


def test_fresh_wsl2_with_gpu(repo: Paths, tmp_path: Path) -> None:
    host = FakeHost(preflight("ubuntu2404-wsl2-gpu-kvm"))
    runner = FakeRunner([("events-success.jsonl", 1)])
    installer = app(context(repo, host, runner, machine="pinka-lab"))
    key = public_key()

    async def flow(pilot: Pilot[int]) -> None:
        await welcome_to_mode(pilot)
        await forward(pilot, "machine")
        assert pilot.app.screen.query_one("#power_policy").disabled  # WSL: Windows decides
        await forward(pilot, "resources")
        assert "WSL" in str(
            pilot.app.screen.query(".banner.warn").results(Static).__next__().content
        )
        await forward(pilot, "storage")
        await forward(pilot, "researchers")  # loop file of 300 GiB fits 500 GiB free
        await add_researcher(pilot, "researcher2", key)
        await forward(pilot, "remote")
        assert pilot.app.screen.query_one("#ifaces").disabled  # WSL: Windows owns the network
        await forward(pilot, "gpu")
        assert pilot.app.screen.query_one("#profile", Select).value  # suggested from the GPU
        await forward(pilot, "backups")
        pick(pilot, "type-sftp")
        fill(pilot, sftp_host="backup.example.com", sftp_user="backup", path="/srv/restic/lab")
        await pilot.press("ctrl+n")
        await settle(pilot)
        assert isinstance(pilot.app.screen, ShownOnce)  # the generated restic password
        await click(pilot, "#stored")
        await click(pilot, "#continue")
        await settle(pilot)
        assert at(pilot) == "restore"
        assert pilot.app.screen.query(".greyed")  # not in restore mode
        await forward(pilot, "platform")
        await forward(pilot, "images")
        await forward(pilot, "secrets")
        await secrets_to_progress(pilot)
        progress = pilot.app.screen
        assert "ok" in str(progress.query_one("#status", Static).content)
        assert "login.tailscale.com" not in str(progress.query_one("#login", Static).content)
        assert "ssh-ed25519 KEYPLACEHOLDER pinka-deploy@pinka-lab" in str(
            progress.query_one("#deploy-key", Static).content
        )
        await progress_to_end(pilot)

    assert drive(installer, flow) == 1  # doctor warnings
    request = runner.requests[0]
    assert request.machine == "pinka-lab"
    assert request.vault_password == PASSWORD
    assert request.options == []
    values = hostvars.load(repo.host_vars("pinka-lab").read_text(encoding="utf-8"))
    assert values["machine_name"] == "pinka-lab"
    assert values["incus_pool_source"] == "loop"
    assert values["power_policy"] == "os_default"
    assert values["vllm_profile"]
    saved = answers_mod.load(repo.answers("pinka-lab"), apply.context(repo))
    assert [r.name for r in saved.researchers] == ["researcher1", "researcher2"]
    assert saved.backups.repository.host == "backup.example.com"
    assert (
        repo.public_keys("researcher2")
        .read_text(encoding="utf-8")
        .strip()
        .endswith(key.split()[1] + " researcher1@example.com")
    )
    secrets = vault.keys_of(vault.decrypt(repo.vault.read_text(encoding="utf-8"), PASSWORD))
    assert "vault_restic_password" in secrets
    assert not repo.state_file.exists()  # finished: no resume state

    # The answers replay (bootstrap.sh --answers) writes byte-identical files; only the
    # vault differs (its encryption is salted, and the replay generates its own secrets).
    replay = copy_repo(tmp_path / "replay")
    shutil.copytree(repo.users_dir, replay.users_dir, dirs_exist_ok=True)
    password_file = tmp_path / "vault-pass"
    password_file.write_text(PASSWORD + "\n", encoding="utf-8")
    password_file.chmod(0o600)
    argv = ["--infra", str(replay.infra), "apply", "--answers", str(repo.answers("pinka-lab"))]
    assert cli.main([*argv, "--vault-password-file", str(password_file)]) == 0
    for name in ("host_vars", "group_vars_main", "answers", "models_lock"):
        mine, theirs = getattr(repo, name), getattr(replay, name)
        if callable(mine):
            mine, theirs = mine("pinka-lab"), theirs("pinka-lab")
        assert theirs.read_bytes() == mine.read_bytes(), name
    keys = {p.name: p.read_bytes() for p in repo.users_dir.glob("*.pub")}
    assert keys == {p.name: p.read_bytes() for p in replay.users_dir.glob("*.pub")}
    assert "researcher2.pub" in keys


def test_bare_metal_with_a_dedicated_pool_disk(repo: Paths) -> None:
    host = FakeHost(preflight("ubuntu2404-baremetal-nvidia-driver-loaded"))
    runner = FakeRunner([("events-success.jsonl", 0)])
    installer = app(context(repo, host, runner))

    async def flow(pilot: Pilot[int]) -> None:
        await welcome_to_mode(pilot)
        await forward(pilot, "machine")
        fill(pilot, name="pinka-lab")
        await forward(pilot, "resources")
        await forward(pilot, "storage")
        table = pilot.app.screen.query_one("#disks", DataTable)
        table.focus()
        table.move_cursor(row=1)  # the mounted system disk cannot be chosen
        await pilot.press("enter")
        await pilot.pause()
        assert not isinstance(pilot.app.screen, TypedConfirm)
        table.move_cursor(row=0)
        await pilot.press("enter")
        await pilot.pause()
        dialog = pilot.app.screen
        assert isinstance(dialog, TypedConfirm)
        dialog.query_one("#typed", Input).value = DISK_BY_ID[:-1]
        await pilot.pause()
        assert dialog.query_one("#confirm").disabled  # only the exact path
        dialog.query_one("#typed", Input).value = DISK_BY_ID
        await pilot.pause()
        await click(pilot, "#confirm")
        await pilot.pause()
        assert "will be destroyed" in str(pilot.app.screen.query_one("#device", Static).content)
        await forward(pilot, "researchers")
        await forward(pilot, "remote")
        fill(pilot, authkey="authkey-placeholder")
        pick(pilot, "login-key")
        await forward(pilot, "gpu")
        pick(pilot, "driver-auto")
        await forward(pilot, "backups")
        await forward(pilot, "restore")
        await forward(pilot, "platform")
        await forward(pilot, "images")
        await forward(pilot, "secrets")
        fill(pilot, vault1=PASSWORD, vault2=PASSWORD)
        await forward(pilot, "review")
        banners = [str(s.content) for s in pilot.app.screen.query(".banner.bad").results(Static)]
        assert banners == [f"⚠ {DISK_BY_ID} is formatted as the Incus pool"]
        await forward(pilot, "progress")
        await progress_to_end(pilot)

    assert drive(installer, flow) == 0
    values = hostvars.load(repo.host_vars("pinka-lab").read_text(encoding="utf-8"))
    assert values["incus_pool_source"] == "device"
    assert values["incus_pool_device"] == values["incus_pool_device_confirm"] == DISK_BY_ID
    assert values["incus_pool_device_force_wipe"] is False
    assert values["install_nvidia_driver"] == "auto"
    secrets = vault.keys_of(vault.decrypt(repo.vault.read_text(encoding="utf-8"), PASSWORD))
    assert "vault_tailscale_authkey" in secrets


def test_a_disk_with_signatures_needs_wipe(repo: Paths) -> None:
    host = FakeHost(preflight("ubuntu2404-baremetal-no-gpu"))
    installer = app(context(repo, host), first="storage")

    async def flow(pilot: Pilot[int]) -> None:
        table = pilot.app.screen.query_one("#disks", DataTable)
        table.focus()
        table.move_cursor(row=2)
        await pilot.press("enter")
        await pilot.pause()
        pilot.app.screen.query_one("#typed", Input).value = "/dev/disk/by-id/ata-EXAMPLE_HDD_2222"
        await pilot.pause()
        await click(pilot, "#confirm")
        await pilot.pause()
        wipe = pilot.app.screen
        assert isinstance(wipe, TypedConfirm)
        wipe.query_one("#typed", Input).value = "WIPE"
        await pilot.pause()
        await click(pilot, "#confirm")
        await pilot.pause()

    drive(installer, flow)
    storage = installer.ctx.wizard.answers.storage
    assert storage.force_wipe is True
    assert storage.device == "/dev/disk/by-id/ata-EXAMPLE_HDD_2222"


def test_degraded_host_without_gpu(repo: Paths) -> None:
    host = FakeHost(preflight("ubuntu2404-baremetal-no-gpu"))
    runner = FakeRunner([("events-success.jsonl", 0)])
    installer = app(context(repo, host, runner, machine="pinka-lab"))
    found = notes(installer)

    async def flow(pilot: Pilot[int]) -> None:
        await forward(pilot, "preflight")
        await forward(pilot, "preflight")  # every disabled feature needs consent
        assert found[-1] == "tick every disabled feature to continue, or Abort"
        for box in pilot.app.screen.query(".consent-feature").results(Checkbox):
            box.value = True
        await forward(pilot, "mode")
        for step in ("machine", "resources", "storage", "researchers", "remote", "gpu"):
            await forward(pilot, step)
        assert "no GPU detected" in str(pilot.app.screen.query_one(".greyed", Static).content)
        for step in ("backups", "restore", "platform", "images", "secrets"):
            await forward(pilot, step)
        await secrets_to_progress(pilot)

    drive(installer, flow)
    assert runner.requests[0].options == ["--accept-degraded"]
    assert "accept_degraded" in installer.ctx.wizard.consents


SECURE_BOOT = "ubuntu2404-baremetal-nvidia-no-driver-secureboot"


def test_secure_boot_mok_consent(repo: Paths) -> None:
    host = FakeHost(preflight(SECURE_BOOT), fixture=SECURE_BOOT)
    runner = FakeRunner([("events-signal76.jsonl", 76)])
    installer = app(context(repo, host, runner, machine="pinka-lab"))
    found = notes(installer)
    mok = "one-time mok"

    async def flow(pilot: Pilot[int]) -> None:
        await forward(pilot, "preflight")
        screen = pilot.app.screen
        assert "Outcome: needs_action" in str(screen.query_one("#outcome", Static).content)
        assert "Machine Owner Key" in "".join(
            str(s.content) for s in screen.query(".banner").results(Static)
        )
        await forward(pilot, "preflight")  # needs_action blocks without the consent
        assert found[-1] == "follow the remediation, then Re-check"
        tick(pilot, "consent-mok")
        await settle(pilot)
        screen = pilot.app.screen
        assert "Outcome: degraded" in str(screen.query_one("#outcome", Static).content)
        assert host.preflight_args[-1] == ["--nvidia-driver", "auto", "--accept-mok-enrollment"]
        assert screen.query_one("#consent-mok", Checkbox).value
        for box in screen.query(".consent-feature").results(Checkbox):
            box.value = True
        await forward(pilot, "mode")
        for step in ("machine", "resources", "storage", "researchers", "remote", "gpu"):
            await forward(pilot, step)
        assert pilot.app.screen.query_one("#driver-auto", RadioButton).value
        pick(pilot, "driver-manual")
        await pilot.pause()
        await forward(pilot, "gpu")
        assert "install the driver yourself first" in found[-1]
        pick(pilot, "driver-auto")
        await pilot.pause()  # the radio set clears driver-manual
        fill(pilot, mok=mok, mok2="typo")
        await forward(pilot, "gpu")
        assert "✖ enter the same one-time password twice" in shown_errors(pilot)
        fill(pilot, mok=mok, mok2=mok)
        for step in ("backups", "restore", "platform", "images", "secrets"):
            await forward(pilot, step)
        fill(pilot, vault1=PASSWORD, vault2=PASSWORD)
        await forward(pilot, "review")
        await forward(pilot, "progress")
        signal = pilot.app.screen
        assert isinstance(signal, SignalScreen)
        assert "MOK Manager" in str(signal.query_one(".banner", Static).content)
        assert "is not enrolled" in str(signal.query_one(".hint", Static).content)
        await click(pilot, "#quit")

    assert drive(installer, flow) == 76
    request = runner.requests[0]
    assert "--accept-mok-enrollment" in request.options
    assert "--mok-password-stdin" in request.options
    assert request.mok_password == mok
    assert installer.ctx.wizard.mok_password == ""  # gone once bootstrap had it
    assert installer.ctx.wizard.answers.gpu.accept_mok_enrollment
    assert mok not in repo.state_file.read_text(encoding="utf-8")
    assert not [c for c in host.calls if c[0][0] == "mokutil"]  # bootstrap imports the key
    exported = repo.answers("pinka-lab").read_text(encoding="utf-8")
    assert "accept_mok_enrollment: true" in exported
    assert mok not in exported


def test_secure_boot_without_the_driver(repo: Paths) -> None:
    host = FakeHost(preflight(SECURE_BOOT), fixture=SECURE_BOOT)
    installer = app(context(repo, host, machine="pinka-lab"))

    async def flow(pilot: Pilot[int]) -> None:
        await forward(pilot, "preflight")
        await click(pilot, "#skip-driver")
        await settle(pilot)
        screen = pilot.app.screen
        assert "Outcome: degraded" in str(screen.query_one("#outcome", Static).content)
        assert not screen.query("#consent-mok")
        assert host.preflight_args[-1] == ["--nvidia-driver", "skip"]
        for box in screen.query(".consent-feature").results(Checkbox):
            box.value = True
        await forward(pilot, "mode")
        for step in ("machine", "resources", "storage", "researchers", "remote", "gpu"):
            await forward(pilot, step)
        assert pilot.app.screen.query_one("#driver-skip", RadioButton).value
        await forward(pilot, "backups")

    drive(installer, flow)
    assert installer.ctx.wizard.answers.gpu.install_driver == "skip"
    assert "accept_mok_enrollment" not in installer.ctx.wizard.consents


def test_unsupported_host_can_only_be_forced_by_typing(repo: Paths) -> None:
    host = FakeHost(preflight("ubuntu2404-no-btrfs-no-dmthin"))
    installer = app(context(repo, host), first="preflight")

    async def flow(pilot: Pilot[int]) -> None:
        await forward(pilot, "preflight")
        await click(pilot, "#force")
        await pilot.pause()
        dialog = pilot.app.screen
        assert isinstance(dialog, TypedConfirm)
        dialog.query_one("#typed", Input).value = "FORCE UNSUPPORTED"
        await pilot.pause()
        await click(pilot, "#confirm")
        await pilot.pause()
        await forward(pilot, "mode")

    drive(installer, flow)
    assert installer.ctx.wizard.consents == {"force_unsupported"}
    assert installer.ctx.wizard.bootstrap_options() == ["--force-unsupported"]


def test_restore_mode(repo: Paths) -> None:
    snapshots = [
        {"time": "2026-01-01T03:00:00Z", "hostname": "pinka-lab", "short_id": "0a1b2c3d",
         "id": "0a1b2c3d" * 8, "paths": ["/srv/pinka", "/var/lib/pinka/backup"]},
    ]  # fmt: skip
    host = FakeHost(
        preflight("ubuntu2404-baremetal-no-gpu"),
        replies={"restic -r": CommandResult(0, json.dumps(snapshots))},
    )
    runner = FakeRunner([("events-success.jsonl", 0)])
    installer = app(context(repo, host, runner, machine="pinka-lab"), first="mode")
    installer.ctx.wizard.consents.add("accept_degraded")

    async def flow(pilot: Pilot[int]) -> None:
        pick(pilot, "mode-restore")
        for step in ("machine", "resources", "storage", "researchers", "remote", "gpu"):
            await forward(pilot, step)
        await forward(pilot, "backups")
        pick(pilot, "type-sftp")
        pick(pilot, "pw-enter")
        fill(
            pilot,
            sftp_host="backup.example.com",
            sftp_user="backup",
            path="/srv/restic/lab",
            pw1="restic-password-for-tests",
            pw2="restic-password-for-tests",
        )
        await forward(pilot, "restore")
        table = pilot.app.screen.query_one("#snapshots", DataTable)
        assert table.row_count == 1
        table.focus()
        await pilot.press("enter")
        await pilot.pause()
        assert pilot.app.screen.query_one("#snapshot", Input).value == "0a1b2c3d"
        for step in ("platform", "images", "secrets"):
            await forward(pilot, step)
        fill(pilot, vault1=PASSWORD, vault2=PASSWORD)
        await forward(pilot, "review")
        banners = [str(s.content) for s in pilot.app.screen.query(".banner.bad").results(Static)]
        assert "⚠ the lab's data is replaced by snapshot 0a1b2c3d" in banners
        await forward(pilot, "progress")

    drive(installer, flow)
    restic = next(c for c in host.calls if c[0][0] == "restic")
    assert restic[0] == ["restic", "-r", "sftp:backup@backup.example.com:/srv/restic/lab",
                         "snapshots", "--json"]  # fmt: skip
    assert restic[1] == {"RESTIC_PASSWORD": "restic-password-for-tests"}
    assert runner.requests[0].options == ["--accept-degraded", "--restore"]
    saved = answers_mod.load(repo.answers("pinka-lab"), apply.context(repo))
    assert (saved.mode, saved.restore_snapshot) == ("restore", "0a1b2c3d")


def installed(repo: Paths) -> None:
    """A lab installed with the CI answers (headless), to modify."""
    loaded = answers_mod.load(repo.infra / "tests" / "answers" / "ci.yml", apply.context(repo))
    loaded.machine.name = "pinka-lab"
    loaded.researchers[0].name = "researcher1"
    plan = apply.plan(
        loaded, repo, vault.Provided(tailscale_authkey="authkey-placeholder"), PASSWORD
    )
    plan.write()


def test_modify_mode(repo: Paths) -> None:
    installed(repo)
    host = FakeHost(preflight("ubuntu2404-baremetal-no-gpu"), installed="pinka-lab")
    runner = FakeRunner([("events-success.jsonl", 0)])
    ctx = context(repo, host, runner)
    assert ctx.wizard.answers.mode == "modify"  # an installed lab opens in modify mode
    assert ctx.wizard.answers.machine.name == "pinka-lab"
    installer = app(ctx, first="mode")
    before = repo.host_vars("pinka-lab").read_text(encoding="utf-8")
    restic = vault.decrypt(repo.vault.read_text(encoding="utf-8"), PASSWORD)

    async def flow(pilot: Pilot[int]) -> None:
        assert pilot.app.screen.query_one("#mode-modify", RadioButton).value
        for step in ("machine", "resources", "storage", "researchers", "remote", "gpu"):
            await forward(pilot, step)
        await forward(pilot, "backups")
        fill(pilot, schedule="04:30")
        for step in ("restore", "platform", "images", "secrets"):
            await forward(pilot, step)
        fill(pilot, vault1=PASSWORD)  # the existing vault's password
        await forward(pilot, "review")
        titles = [c.title for c in pilot.app.screen.query(Collapsible)]
        assert "ansible/inventory/host_vars/pinka-lab.yml (changed)" in titles
        assert "ansible/group_vars/all/main.yml (unchanged)" in titles
        assert repo.host_vars("pinka-lab").read_text(encoding="utf-8") == before  # not yet
        await forward(pilot, "progress")

    drive(installer, flow)
    after = vault.decrypt(repo.vault.read_text(encoding="utf-8"), PASSWORD)
    # the existing repository's password is kept: a new one would lock out every backup
    assert after["vault_restic_password"] == restic["vault_restic_password"]
    assert "backup_schedule: '*-*-* 04:30:00'" in repo.host_vars("pinka-lab").read_text(
        encoding="utf-8"
    )
    assert runner.requests[0].options == []


def test_validation_errors_stay_on_the_screen(repo: Paths) -> None:
    host = FakeHost(preflight("ubuntu2404-wsl2-gpu-kvm"))
    installer = app(context(repo, host), first="machine")

    async def flow(pilot: Pilot[int]) -> None:
        fill(pilot, name="Bad_Name", srv_path="srv")
        await forward(pilot, "machine")
        errors = shown_errors(pilot)
        assert any("lower-case letters" in e for e in errors), errors
        assert any("absolute" in e for e in errors), errors
        fill(pilot, name="pinka-lab", srv_path="/srv/pinka")
        await forward(pilot, "resources")
        fill(pilot, cpuset_services="0-11", cpuset_targets="8-23", postgres="lots")
        await forward(pilot, "resources")
        assert "✖ services and targets share CPUs 8-11" in shown_errors(pilot)
        fill(pilot, cpuset_services="0-7", cpuset_targets="8-23", postgres="2048MB")
        await forward(pilot, "storage")
        fill(pilot, loop_size="10")
        await forward(pilot, "storage")
        assert shown_errors(pilot) == ["✖ at least 20 GiB"]
        fill(pilot, loop_size="460")
        await forward(pilot, "storage")
        assert shown_errors(pilot) == ["✖ at most 450 GiB fits here"]
        fill(pilot, loop_size="200")
        await forward(pilot, "researchers")
        table = pilot.app.screen.query_one("#people", DataTable)
        table.focus()
        await click(pilot, "#remove")
        await pilot.pause()
        await forward(pilot, "researchers")
        await add_researcher(pilot, "pinka-svc", public_key())
        assert any("pinka-" in e for e in shown_errors(pilot)), shown_errors(pilot)
        pilot.app.screen.query_one("#r-name", Input).value = "researcher2"
        pilot.app.screen.query_one("#r-keys", TextArea).text = private_key()
        await click(pilot, "#r-ok")
        await pilot.pause()
        assert any("PRIVATE key" in e for e in shown_errors(pilot)), shown_errors(pilot)
        await click(pilot, "#r-cancel")
        await pilot.pause()
        await add_researcher(pilot, "researcher2", public_key())
        await forward(pilot, "researchers")  # nobody is an admin
        await add_researcher(pilot, "researcher3", public_key(), admin=True)
        await forward(pilot, "remote")
        cast(StepScreen, pilot.app.screen).installer.goto("secrets")
        await settle(pilot)
        fill(pilot, vault1="short", vault2="short")
        await forward(pilot, "secrets")
        fill(pilot, vault1=PASSWORD, vault2=PASSWORD + "x")
        await forward(pilot, "secrets")
        assert shown_errors(pilot)

    drive(installer, flow)
    assert installer.ctx.wizard.answers.researchers_removed == []  # fresh: nobody to lock


def test_the_resources_screen_warns_about_split_smt_cores(repo: Paths) -> None:
    # cpu<n> and cpu<n+12> are the two threads of one core (24 threads).
    cores = [frozenset({n, n + 12}) for n in range(12)]
    host = FakeHost(preflight("ubuntu2404-baremetal-no-gpu"), cores=cores)
    installer = app(context(repo, host), first="resources")
    installer.ctx.wizard.profile = {**host.result.profile, "hardware": {"cpu_threads": 24}}

    def warning(pilot: Pilot[Any]) -> str:
        shown = pilot.app.screen.query_one("#smt-warning", Static)
        return str(shown.content) if shown.display else ""

    async def flow(pilot: Pilot[int]) -> None:
        # The suggestion keeps whole cores together: no warning.
        assert pilot.app.screen.query_one("#cpuset_services", Input).value == "0-3,12-15"
        assert warning(pilot) == ""
        fill(pilot, cpuset_services="0-7", cpuset_targets="8-23")
        await pilot.pause()
        assert warning(pilot).startswith("SMT siblings of one core are split")
        assert "0,12; 1,13; 2,14; 3,15 and 4 more" in warning(pilot)
        # A warning, not an error: the operator may go on.
        await forward(pilot, "storage")

    drive(installer, flow)
    resources = installer.ctx.wizard.answers.resources
    assert (resources.cpuset_services, resources.cpuset_targets) == ("0-7", "8-23")


def test_resume_after_a_wsl_restart(repo: Paths) -> None:
    host = FakeHost(preflight("ubuntu2404-wsl2-gpu-kvm"))
    first = FakeRunner([("events-signal75.jsonl", 75)])
    installer = app(context(repo, host, first, machine="pinka-lab"), first="secrets")
    installer.ctx.wizard.answers.machine.name = "pinka-lab"
    installer.ctx.wizard.answers.researchers = [answers_mod.Researcher("researcher1", True)]

    async def flow(pilot: Pilot[int]) -> None:
        fill(pilot, vault1=PASSWORD, vault2=PASSWORD)
        await forward(pilot, "review")
        await forward(pilot, "progress")
        signal = pilot.app.screen
        assert isinstance(signal, SignalScreen)
        text = str(signal.query_one(".banner", Static).content)
        assert "wsl --terminate" in text
        saved = state.load(repo.state_file)
        assert saved is not None and (saved.screen, saved.signal) == ("progress", 75)
        assert PASSWORD not in repo.state_file.read_text(encoding="utf-8")
        await click(pilot, "#quit")

    assert drive(installer, flow) == 75

    second = FakeRunner([("events-success.jsonl", 1)])
    again = app(context(repo, host, second, machine="pinka-lab"))

    async def resume(pilot: Pilot[int]) -> None:
        assert pilot.app.screen.query_one("#resume-yes", RadioButton).value
        await pilot.press("ctrl+n")
        await settle(pilot)
        prompt = pilot.app.screen
        assert isinstance(prompt, PasswordPrompt)  # the vault password is never stored
        prompt.query_one("#password", Input).value = "wrong password"
        await click(pilot, "#continue")
        await pilot.pause()
        assert isinstance(pilot.app.screen, PasswordPrompt)
        prompt.query_one("#password", Input).value = PASSWORD
        await click(pilot, "#continue")
        await settle(pilot)
        assert at(pilot) == "progress"
        await progress_to_end(pilot)

    assert drive(again, resume) == 1
    assert second.requests[0].vault_password == PASSWORD
    assert again.ctx.wizard.answers.machine.name == "pinka-lab"


def test_a_failed_run_offers_retry(repo: Paths) -> None:
    host = FakeHost(preflight("ubuntu2404-baremetal-no-gpu"))
    runner = FakeRunner([("events-failure.jsonl", 60), ("events-success.jsonl", 0)])
    ctx = context(repo, host, runner, machine="pinka-lab")
    ctx.wizard.vault_password = PASSWORD
    installer = app(ctx, first="progress")

    async def flow(pilot: Pilot[int]) -> None:
        screen = pilot.app.screen
        failure = str(screen.query_one("#failure", Static).content)
        assert "exit code 60" in failure
        assert "Failed: docker > Start Docker" in failure
        assert "Unable to start service docker" in failure
        assert screen.query_one("#next").disabled
        await click(pilot, "#retry")
        await settle(pilot)
        assert not screen.query_one("#failure", Static).display
        assert not screen.query_one("#next").disabled

    drive(installer, flow)
    assert len(runner.requests) == 2


def test_quit_asks_and_keeps_the_state(repo: Paths) -> None:
    host = FakeHost(preflight("ubuntu2404-baremetal-no-gpu"))
    installer = app(context(repo, host, machine="pinka-lab"), first="machine")

    async def flow(pilot: Pilot[int]) -> None:
        fill(pilot, name="lab-two")
        await pilot.press("ctrl+q")
        await pilot.pause()
        assert isinstance(pilot.app.screen, Confirm)
        await click(pilot, "#cancel")
        await pilot.pause()
        assert at(pilot) == "machine"
        await pilot.press("ctrl+q")
        await pilot.pause()
        await click(pilot, "#yes")

    assert drive(installer, flow) == 77
    saved = state.load(repo.state_file)
    assert saved is not None and saved.screen == "machine"

    again = app(context(repo, FakeHost(preflight("ubuntu2404-baremetal-no-gpu"))))

    async def resume(pilot: Pilot[int]) -> None:
        await forward(pilot, "machine")  # resumes at the saved screen
        assert pilot.app.screen.query_one("#name", Input).value == "lab-two"

    drive(again, resume)


def test_theme_cycles_with_f2(repo: Paths) -> None:
    installer = app(context(repo, FakeHost(preflight("ubuntu2404-baremetal-no-gpu"))))
    seen: list[str] = []

    async def flow(pilot: Pilot[int]) -> None:
        for _ in range(3):
            await pilot.press("f2")
            seen.append(pilot.app.theme)

    drive(installer, flow)
    assert seen == ["textual-light", "pinka-high-contrast", "textual-dark"]


def test_back_keeps_what_was_entered(repo: Paths) -> None:
    installer = app(
        context(repo, FakeHost(preflight("ubuntu2404-baremetal-no-gpu"))), first="machine"
    )

    async def flow(pilot: Pilot[int]) -> None:
        fill(pilot, name="pinka-lab")
        await forward(pilot, "resources")
        await pilot.press("ctrl+b")
        await settle(pilot)
        assert at(pilot) == "machine"
        assert pilot.app.screen.query_one("#name", Input).value == "pinka-lab"

    drive(installer, flow)


def test_help_and_greyed_screens(repo: Paths) -> None:
    installer = app(context(repo, FakeHost(preflight("ubuntu2404-baremetal-no-gpu"))), first="gpu")

    async def flow(pilot: Pilot[int]) -> None:
        assert pilot.app.screen.query(".greyed")
        await pilot.press("f1")
        await pilot.pause()
        assert "Help · GPU & inference" in str(pilot.app.screen.query_one(".dialog-title").render())
        await pilot.press("escape")
        await pilot.pause()
        await forward(pilot, "backups")  # a greyed screen asks nothing

    drive(installer, flow)


def ctx_for(repo: Paths, fixture: str) -> Context:
    return context(repo, FakeHost(preflight(fixture)))


def test_doctor_and_finish(repo: Paths) -> None:
    host = FakeHost(preflight("ubuntu2404-baremetal-no-gpu"))
    ctx = context(repo, host, machine="pinka-lab")
    ctx.wizard.answers.machine.name = "pinka-lab"
    ctx.wizard.answers.researchers = [answers_mod.Researcher("researcher1", True)]
    installer = app(ctx, first="doctor")
    found = notes(installer)

    async def flow(pilot: Pilot[int]) -> None:
        table = pilot.app.screen.query_one("#checks", DataTable)
        assert table.row_count == 3
        await click(pilot, "#smoke")
        await settle(pilot)
        await forward(pilot, "finish")
        await click(pilot, "#commit")
        await settle(pilot)

    drive(installer, flow)
    assert ["pinka", "doctor", "--json", "--smoke"] in [c[0] for c in host.calls]
    git = [c[0] for c in host.calls if c[0][:1] == ["sudo"]]
    assert git[0][:4] == ["sudo", "-u", "researcher1", "git"]
    assert git[0][-3:] == ["switch", "-C", "machine/pinka-lab"]
    assert "committed on machine/pinka-lab; push it to your private repository" in found


def test_pairing_on_an_inference_node(repo: Paths) -> None:
    host = FakeHost(
        preflight("ubuntu2404-baremetal-nvidia-driver-loaded"),
        replies={
            "pinka pair show": CommandResult(
                0, "Code: 123-456\nPaired with lab node lab-node (abc).\n"
            )
        },
    )
    runner = FakeRunner([("events-success.jsonl", 0)])
    ctx = context(repo, host, runner, machine="gpu-node")
    ctx.wizard.vault_password = PASSWORD
    answers = ctx.wizard.answers
    answers.machine.name = "gpu-node"
    answers.machine.node_role = "inference"
    answers.researchers = [answers_mod.Researcher("researcher1", True)]
    installer = app(ctx, first="finish")

    async def flow(pilot: Pilot[int]) -> None:
        await click(pilot, "#pair-show")
        await settle(pilot)
        assert at(pilot) == "progress"

    drive(installer, flow)
    assert answers.split_inference_peer == "lab-node"
    assert runner.requests[0].options == ["--tags=inference_control,observability,firewall"]
    values = hostvars.load(repo.host_vars("gpu-node").read_text(encoding="utf-8"))
    assert values["inference_lab_node"] == "lab-node"


def test_the_terminal_must_fit() -> None:
    from os import terminal_size  # noqa: PLC0415

    from pinka_installer.tui.app import terminal_problem  # noqa: PLC0415

    assert terminal_problem("dumb", True, terminal_size((120, 40))) == "TERM is dumb"
    assert terminal_problem(None, True, terminal_size((120, 40))) == "TERM is unset"
    assert "not a terminal" in terminal_problem("xterm-256color", False, terminal_size((120, 40)))
    assert terminal_problem("xterm-256color", True, terminal_size((79, 24))) == (
        "the terminal is 79x24, the installer needs 80x24"
    )
    assert terminal_problem("xterm-256color", True, terminal_size((80, 24))) == ""


def test_run_refuses_an_unsuitable_terminal(repo: Paths, capsys: Any, monkeypatch: Any) -> None:
    from pinka_installer.tui import app as app_mod  # noqa: PLC0415

    monkeypatch.setenv("TERM", "dumb")
    assert app_mod.run(repo) == 77
    err = capsys.readouterr().err
    assert "TERM is dumb" in err and "docs/how-to/install-without-the-tui.md" in err


def test_answers_file_prefills_the_wizard(repo: Paths, tmp_path: Path) -> None:
    path = tmp_path / "answers.yml"
    loaded = answers_mod.load(repo.infra / "tests" / "answers" / "ci.yml", apply.context(repo))
    path.write_text(answers_mod.dumps(loaded), encoding="utf-8")
    ctx = context(repo, FakeHost(preflight("ubuntu2404-baremetal-no-gpu")), answers=path)
    assert ctx.wizard.answers == loaded


def test_review_shows_a_password_the_vault_generates(repo: Paths) -> None:
    installed(repo)  # its vault has a restic password of the local repository
    data = vault.decrypt(repo.vault.read_text(encoding="utf-8"), PASSWORD)
    data["vault_restic_password"] = ""  # as after an install with backups set up later
    repo.vault.write_text(vault.encrypt(data, PASSWORD), encoding="utf-8")
    host = FakeHost(preflight("ubuntu2404-baremetal-no-gpu"), installed="pinka-lab")
    ctx = context(repo, host)
    ctx.wizard.vault_password = PASSWORD
    installer = app(ctx, first="review")
    shown: list[str] = []

    async def flow(pilot: Pilot[int]) -> None:
        await pilot.press("ctrl+n")
        await settle(pilot)
        dialog = pilot.app.screen
        assert isinstance(dialog, ShownOnce)
        shown.append(str(dialog.query_one("#secret", Static).content))
        unwritten = vault.decrypt(repo.vault.read_text(encoding="utf-8"), PASSWORD)
        assert unwritten["vault_restic_password"] == ""  # nothing is written before that
        await click(pilot, "#stored")
        await click(pilot, "#continue")
        await settle(pilot)
        assert at(pilot) == "progress"

    drive(installer, flow)
    after = vault.decrypt(repo.vault.read_text(encoding="utf-8"), PASSWORD)
    assert after["vault_restic_password"] == shown[0] != ""
