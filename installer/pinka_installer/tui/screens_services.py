"""Data & backups, Restore, Golden images."""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Input,
    RadioButton,
    RadioSet,
    SelectionList,
    Static,
)

from pinka_installer import validators, vault
from pinka_installer.answers import GENERATE, PROVIDED, Repository, Retention
from pinka_installer.tui.base import Problems, StepScreen
from pinka_installer.tui.widgets import Field, NavBar, ShownOnce

SAFE = YAML(typ="safe")


def _input(screen: StepScreen, selector: str) -> str:
    return screen.query_one(selector, Input).value.strip()


class BackupsScreen(StepScreen):
    """Step 8: restic repository, password, schedule, retention."""

    STEP = "backups"
    HELP = (
        "Backups go to a restic repository over SFTP (a host on your tailnet), a REST "
        "server, or a local path (not a real backup: it dies with the disk). With 'later' "
        "the lab works, and doctor warns until a repository is set. A generated restic "
        "password is shown once: without it no backup can be restored. Secrets are only "
        "backed up if you ask, and then only inside restic's encryption."
    )

    def __init__(self) -> None:
        """Build the screen."""
        super().__init__()
        self._acknowledged_now = False

    def nav(self) -> NavBar:  # noqa: D102
        return NavBar(extra=(Button("Test connection", id="test"),))

    def body(self) -> ComposeResult:  # noqa: D102
        backups = self.answers.backups
        repo = backups.repository
        with RadioSet(id="repo-type"):
            for value, label in (
                ("sftp", "SFTP (a host on the tailnet)"),
                ("rest", "REST server"),
                ("local", "Local path (not a real backup)"),
                ("later", "Configure later (doctor warns)"),
            ):
                yield RadioButton(label, value=repo.type == value, id=f"type-{value}")
        yield Field("SFTP host", Input(repo.host, id="sftp-host"))
        yield Field("SFTP user", Input(repo.user, id="sftp-user"))
        yield Field("Path (SFTP or local)", Input(repo.path, id="path"))
        yield Field(
            "REST server URL",
            Input(repo.url, id="url", placeholder="https://backup.example.com/lab"),
        )
        with RadioSet(id="password"):
            yield RadioButton(
                "Generate the restic password (shown once)",
                value=backups.password == GENERATE,
                id="pw-generate",
            )
            yield RadioButton(
                "Enter the restic password", value=backups.password == PROVIDED, id="pw-enter"
            )
        yield Field("restic password", Input(password=True, id="pw1"))
        yield Field("restic password again", Input(password=True, id="pw2"))
        yield Field("Daily backup at (UTC)", Input(backups.schedule, id="schedule"))
        r = backups.retention
        with Horizontal(classes="inline"):
            yield Field("daily", Input(str(r.daily), id="keep-daily", type="integer"))
            yield Field("weekly", Input(str(r.weekly), id="keep-weekly", type="integer"))
            yield Field("monthly", Input(str(r.monthly), id="keep-monthly", type="integer"))
        yield Checkbox(
            "Include model weights (large; they can be downloaded again)",
            backups.include_models,
            id="models",
        )
        yield Checkbox(
            "Include /etc/pinka/secrets (encrypted by restic; "
            "anyone with the restic password can read them)",
            backups.include_secrets,
            id="secrets",
        )

    def _repository(self) -> Repository:
        kind = next(
            (
                v
                for v in ("sftp", "rest", "local", "later")
                if self.query_one(f"#type-{v}", RadioButton).value
            ),
            "later",
        )
        return Repository(
            kind,
            host=_input(self, "#sftp-host"),
            user=_input(self, "#sftp-user"),
            path=_input(self, "#path"),
            url=_input(self, "#url"),
        )

    def on_extra_button(self, event: Button.Pressed) -> None:  # noqa: D102
        if event.button.id == "test":
            self.test(self._repository())

    @work(thread=True, exclusive=True)
    def test(self, repository: Repository) -> None:
        """Check that the repository's host answers (restic is installed by the play).

        Args:
            repository: The repository.
        """
        host = self.ctx.host
        if repository.type == "sftp":
            ok = host.reachable(repository.host, 22)
            text = f"{repository.host}:22 {'answers' if ok else 'does not answer'}"
        elif repository.type == "rest":
            from urllib.parse import urlsplit  # noqa: PLC0415

            parsed = urlsplit(repository.url)
            ok = bool(parsed.hostname) and host.reachable(str(parsed.hostname), parsed.port or 443)
            text = f"{parsed.hostname} {'answers' if ok else 'does not answer'}"
        elif repository.type == "local":
            ok = not validators.absolute_path(repository.path)
            text = "a local path: it is created by the play" if ok else "not an absolute path"
        else:
            ok, text = True, "configure later: nothing to test"
        self.app.call_from_thread(self.notify, text, severity="information" if ok else "error")

    def save(self) -> Problems:  # noqa: D102
        backups = self.answers.backups
        problems: Problems = []
        backups.repository = self._repository()
        repository = backups.repository
        if repository.type != "later" and (
            error := validators.restic_repository(repository.url_for_restic())
        ):
            field = {"sftp": "sftp-host", "rest": "url", "local": "path"}[backups.repository.type]
            problems.append((field, error))
        generate = self.query_one("#pw-generate", RadioButton).value
        backups.password = GENERATE if generate else PROVIDED
        if not generate:
            first, second = _input(self, "#pw1"), _input(self, "#pw2")
            if not first or first != second:
                problems.append(("pw1", "enter the same password twice"))
            self.wizard.provided.restic_password = first
        backups.schedule = _input(self, "#schedule")
        if error := validators.time_of_day(backups.schedule):
            problems.append(("schedule", error))
        try:
            backups.retention = Retention(
                int(_input(self, "#keep-daily")),
                int(_input(self, "#keep-weekly")),
                int(_input(self, "#keep-monthly")),
            )
        except ValueError:
            problems.append(("keep-daily", "whole numbers of snapshots"))
        backups.include_models = self.query_one("#models", Checkbox).value
        backups.include_secrets = self.query_one("#secrets", Checkbox).value
        return problems

    def proceed(self) -> None:  # noqa: D102
        backups = self.answers.backups
        # With an existing vault its restic password stays (a new one would lock out the
        # repository); one generated for a vault without it is shown on the Review screen.
        needs_password = (
            backups.repository.type != "later"
            and backups.password == GENERATE
            and not self.ctx.paths.vault.exists()
        )
        if needs_password and not self.wizard.restic_acknowledged:
            if not self.wizard.provided.restic_password:
                self.wizard.provided.restic_password = secrets.token_urlsafe(vault.TOKEN_BYTES)
            self.app.push_screen(
                ShownOnce("restic password", self.wizard.provided.restic_password), self._stored
            )
            return
        self.installer.forward()

    def _stored(self, stored: bool | None) -> None:
        self.wizard.restic_acknowledged = bool(stored)
        if stored:
            self.installer.forward()


class RestoreScreen(StepScreen):
    """Step 9 (restore mode): pick a snapshot and preview what is restored."""

    STEP = "restore"
    HELP = (
        "Restore installs the lab, then restores a restic snapshot of this host from the "
        "repository on the backups screen: the research space, the databases and, if they "
        "were backed up, the secrets. The list needs restic and the repository password; "
        "without them, type a snapshot id or latest."
    )

    def __init__(self) -> None:
        """Build the screen."""
        super().__init__()
        self.snapshots: list[dict[str, Any]] = []

    def not_applicable(self) -> str | None:  # noqa: D102
        if self.answers.mode != "restore":
            return "only in restore mode (step 3)"
        return None

    def body(self) -> ComposeResult:  # noqa: D102
        table: DataTable[str] = DataTable(id="snapshots", cursor_type="row")
        yield table
        yield Field(
            "Snapshot",
            Input(self.answers.restore_snapshot, id="snapshot"),
            hint="latest or a snapshot id",
        )
        yield Static("", id="preview", classes="hint")

    def on_mount(self) -> None:  # noqa: D102
        if not self.not_applicable():
            self.query_one("#snapshots", DataTable).add_columns("time", "host", "id", "paths")
            self.list_snapshots()

    @work(thread=True, exclusive=True)
    def list_snapshots(self) -> None:
        """List the repository's snapshots with restic, if restic is installed."""
        repository = self.answers.backups.repository.url_for_restic()
        password = self.wizard.provided.restic_password
        if not repository or not password:
            self.app.call_from_thread(
                self._note, "no repository password yet: type a snapshot id or latest"
            )
            return
        done = self.ctx.host.run(
            ["restic", "-r", repository, "snapshots", "--json"], env={"RESTIC_PASSWORD": password}
        )
        if done.returncode != 0:
            self.app.call_from_thread(
                self._note, f"cannot list snapshots: {done.stderr.strip()[-200:]}"
            )
            return
        try:
            self.snapshots = list(json.loads(done.stdout) or [])
        except ValueError:
            self.snapshots = []
        self.app.call_from_thread(self._fill)

    def _note(self, text: str) -> None:
        self.query_one("#preview", Static).update(text)

    def _fill(self) -> None:
        table = self.query_one("#snapshots", DataTable)
        for snap in reversed(self.snapshots):
            short = str(snap.get("short_id") or str(snap.get("id", ""))[:8])
            table.add_row(
                str(snap.get("time", ""))[:19],
                str(snap.get("hostname", "")),
                short,
                ", ".join(snap.get("paths") or []),
                key=short,
            )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:  # noqa: D102
        short = str(event.row_key.value)
        self.query_one("#snapshot", Input).value = short
        snap = next((s for s in self.snapshots if str(s.get("short_id")) == short), {})
        self._note("Restores: " + ", ".join(snap.get("paths") or []))

    def save(self) -> Problems:  # noqa: D102
        self.answers.restore_snapshot = _input(self, "#snapshot") or "latest"
        if error := validators.snapshot(self.answers.restore_snapshot):
            return [("snapshot", error)]
        if self.answers.backups.repository.type == "later":
            return [("snapshot", "set the repository on the backups screen first")]
        return []


def blueprints(images_dir: Path) -> list[dict[str, Any]]:
    """The golden image blueprints.

    Args:
        images_dir: ``images/``.

    Returns:
        The parsed ``image.yaml`` files, by name.
    """
    return [
        SAFE.load(p.read_text(encoding="utf-8")) for p in sorted(images_dir.glob("*/image.yaml"))
    ]


class ImagesScreen(StepScreen):
    """Step 12: golden images to build, now or later."""

    STEP = "images"
    HELP = (
        "Golden images are the sandbox targets' base images, built once from the recipes in "
        "images/. Building them takes a while and needs disk space; you can build them "
        "later with bootstrap --tags images. VM variants are offered only where VM targets "
        "work (KVM)."
    )

    def body(self) -> ComposeResult:  # noqa: D102
        features = self.wizard.profile.get("features") or {}
        vm_ok = bool(features.get("vm_targets"))
        chosen = set(self.answers.images_build)
        options = []
        for spec in blueprints(self.ctx.paths.images_dir):
            name = str(spec.get("name"))
            if spec.get("type") == "vm" and not vm_ok:  # the blueprint schema's VM type
                continue
            packages = len(spec.get("packages") or [])
            label = (
                f"{name} {spec.get('version')}: {spec.get('description', '')} ({packages} packages)"
            )
            options.append((label, name, name in chosen))
        yield Static(
            "Build time and disk use are measured by the first build; the base image and the "
            "package list are shown.",
            classes="hint",
        )
        yield SelectionList(*options, id="blueprints")
        with RadioSet(id="when"):
            yield RadioButton(
                "Build them during the install", value=self.wizard.build_images_now, id="now"
            )
            yield RadioButton(
                "Build them later (bootstrap --tags images)",
                value=not self.wizard.build_images_now,
                id="later",
            )

    def save(self) -> Problems:  # noqa: D102
        self.answers.images_build = list(self.query_one("#blueprints", SelectionList).selected)
        self.wizard.build_images_now = self.query_one("#now", RadioButton).value
        return []
