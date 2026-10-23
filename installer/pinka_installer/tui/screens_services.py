"""Screens 9-11 and 14: GPU & inference, Data & backups, Restore, Golden images."""

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
    Select,
    SelectionList,
    Static,
)

from pinka_installer import models, validators, vault
from pinka_installer.answers import GENERATE, PROVIDED, Repository, Retention
from pinka_installer.tui.base import Problems, StepScreen
from pinka_installer.tui.model import (
    ModelChoice,
    load_profiles,
    model_choices,
    suggest_profile,
)
from pinka_installer.tui.widgets import Field, NavBar, ShownOnce

SAFE = YAML(typ="safe")


def _input(screen: StepScreen, selector: str) -> str:
    return screen.query_one(selector, Input).value.strip()


def driver_pin(versions_lock: Path, release_key: str) -> str:
    """The pinned NVIDIA driver packages for a release, or why there are none.

    Args:
        versions_lock: ``versions.lock.yml``.
        release_key: Such as ``ubuntu-24.04``.

    Returns:
        A line for the GPU screen.
    """
    lock = SAFE.load(versions_lock.read_text(encoding="utf-8"))
    pins = ((lock.get("packages") or {}).get("nvidia_driver") or {}).get(release_key)
    if isinstance(pins, dict) and pins:
        return "pinned: " + ", ".join(f"{k} {v}" for k, v in pins.items())
    return "no verified driver pin for this release: choose manual or skip"


class GpuScreen(StepScreen):
    """Screen 9: NVIDIA driver, MOK password, serving profile, default model, token."""

    STEP = "gpu"
    HELP = (
        "auto installs the pinned NVIDIA driver branch on bare metal and VMs (never on WSL, "
        "where the Windows driver provides the GPU). With Secure Boot and no driver, auto "
        "needs your consent on the Preflight screen: the driver's module is signed by a "
        "Machine Owner Key, which the installation imports with a one-time password you enter "
        "now; you enroll the key in MOK Manager at the next boot. manual stops the "
        "installation until you installed the driver yourself. The serving profile is "
        "chosen from the GPU; the "
        "default model must be pinned in models.lock.yml (Pin resolves the commit and the "
        "sha256 of every file from the Hugging Face Hub). Gated models need a token."
    )

    def __init__(self) -> None:
        """Build the screen."""
        super().__init__()
        self.choices: list[ModelChoice] = []
        self.profiles: dict[str, dict[str, Any]] = {}

    def not_applicable(self) -> str | None:  # noqa: D102
        if self.answers.machine.node_role == "lab":
            return "a lab node serves no model; its inference node does"
        if not self.wizard.has_gpu:
            return "no GPU detected; vLLM is not installed on this host"
        return None

    def nav(self) -> NavBar:  # noqa: D102
        return NavBar(extra=(Button("Pin model", id="pin"), Button("Check token", id="whoami")))

    def body(self) -> ComposeResult:  # noqa: D102
        gpu = self.answers.gpu
        info = self.wizard.gpu
        yield Static(
            f"GPU: {info.get('name') or info.get('vendor')}, {info.get('vram_gib') or '?'} GiB, "
            f"driver {info.get('driver') or info.get('driver_state') or 'none'}",
            classes="info",
        )
        release = (self.wizard.profile.get("os") or {}).get("release") or ""
        if self.wizard.gpu_unsupported:
            pci = info.get("pci_device") or "?"
            reason = (
                f"the pinned driver supports this GPU (PCI {pci}) only with its proprietary "
                "kernel module (Maxwell, Pascal, Volta: below compute capability 7.5), and a "
                "driver is installed only for a GPU its open kernel module supports (Turing or "
                "later)"
                if info.get("proprietary_only_drivers")
                else f"the pinned driver does not support this GPU (PCI {pci}): only an older "
                "legacy branch does, or NVIDIA's list does not name it"
            )
            yield Static(
                f"Not usable: {reason}. No driver is installed for it automatically and the lab "
                "installs without the GPU features.",
                classes="banner warn",
                id="gpu-unsupported",
            )
        if self.wizard.is_wsl:
            yield Static(
                "WSL: the Windows driver provides the GPU; no Linux driver is installed.",
                classes="hint",
            )
        else:
            with RadioSet(id="driver"):
                for value, label in (
                    ("auto", "Install the pinned driver"),
                    ("manual", "I install it myself"),
                    ("skip", "No driver"),
                ):
                    yield RadioButton(
                        label, value=gpu.install_driver == value, id=f"driver-{value}"
                    )
            yield Static(driver_pin(self.ctx.paths.versions_lock, str(release)), classes="hint")
            if self.wizard.mok_enrollment_needed:
                yield Field("MOK password (one time)", Input(password=True, id="mok"))
                yield Field("MOK password again", Input(password=True, id="mok2"))
        self.profiles = load_profiles(self.ctx.paths.vllm_profiles)
        profile = (
            gpu.vllm_profile
            or suggest_profile(info, self.profiles)
            or next(iter(self.profiles), "")
        )
        yield Field(
            "Serving profile",
            Select(
                [(f"{n}: {p.get('description', '')}", n) for n, p in self.profiles.items()],
                value=profile or Select.BLANK,
                id="profile",
            ),
        )
        table: DataTable[str] = DataTable(id="models", cursor_type="row")
        yield table
        yield Static(f"Default model: {gpu.default_model}", id="chosen", classes="info")
        with Horizontal(classes="inline"):
            yield Input(placeholder="custom: <owner>/<name>", id="custom-repo")
            yield Input(placeholder="branch, tag or commit", id="custom-ref")
        yield Field(
            "Hugging Face token (gated models)",
            Input(self.wizard.provided.hf_token, password=True, id="hf"),
        )
        yield Checkbox("Start serving after the install", gpu.start_serving, id="serve")
        yield from self._arbiter()

    def _arbiter(self) -> ComposeResult:
        arbiter, caps = self.answers.gpu_arbiter, self.answers.training_caps
        yield Static("GPU arbiter: vLLM and training jobs share the GPU", classes="info")
        with RadioSet(id="after-boot"):
            yield RadioButton(
                "After boot: serve the default deployment",
                value=arbiter.after_boot == "serve",
                id="boot-serve",
            )
            yield RadioButton(
                "After boot: stay idle until asked",
                value=arbiter.after_boot == "idle",
                id="boot-idle",
            )
        yield Checkbox(
            "Serve the deployment again when a training run ends",
            arbiter.restore_after_training,
            id="restore-serving",
        )
        if self.answers.machine.node_role != "full":
            yield Static(
                f"Training is unavailable on a {self.answers.machine.node_role} node: "
                "only a full node runs training jobs.",
                classes="hint",
            )
            return
        yield Static(
            "Training caps: the GPU arbiter refuses every run of a job over them",
            classes="info",
        )
        with Horizontal(classes="inline"):
            yield Field("Run time (s)", Input(str(caps.timeout_seconds), id="cap-timeout"))
            yield Field("CPUs", Input(str(caps.cpus), id="cap-cpus"))
            yield Field("Memory", Input(caps.memory_max, id="cap-memory"))
        with Horizontal(classes="inline"):
            yield Field("Dataset", Input(caps.dataset_max, id="cap-dataset"))
            yield Field("Output and checkpoints", Input(caps.run_storage_max, id="cap-storage"))
            yield Field("Job tmpfs", Input(caps.tmp_max, id="cap-tmp"))

    def _save_arbiter(self) -> Problems:
        arbiter, caps = self.answers.gpu_arbiter, self.answers.training_caps
        arbiter.after_boot = "idle" if self.query_one("#boot-idle", RadioButton).value else "serve"
        arbiter.restore_after_training = self.query_one("#restore-serving", Checkbox).value
        if self.answers.machine.node_role != "full":
            return []
        problems: Problems = []
        for field_id, name, minimum in (
            ("cap-timeout", "timeout_seconds", 60),
            ("cap-cpus", "cpus", 1),
        ):
            text = _input(self, f"#{field_id}")
            if not text.isdigit() or int(text) < minimum:
                problems.append((field_id, f"a whole number of at least {minimum}"))
            else:
                setattr(caps, name, int(text))
        for field_id, name in (
            ("cap-memory", "memory_max"),
            ("cap-dataset", "dataset_max"),
            ("cap-storage", "run_storage_max"),
            ("cap-tmp", "tmp_max"),
        ):
            value = _input(self, f"#{field_id}")
            if error := validators.cap_size(value):
                problems.append((field_id, error))
            else:
                setattr(caps, name, value)
        return problems

    def on_mount(self) -> None:  # noqa: D102
        if self.not_applicable():
            return
        table = self.query_one("#models", DataTable)
        table.add_columns("model", "repository", "license", "gated", "fits", "pinned")
        self._fill_models()

    def _lock_ids(self) -> set[str]:
        text = self.wizard.extra_files.get(
            "models.lock.yml"
        ) or self.ctx.paths.models_lock.read_text(encoding="utf-8")
        return {str(m.get("id")) for m in (SAFE.load(text) or {}).get("models") or []}

    def _fill_models(self) -> None:
        known = SAFE.load(self.ctx.paths.models_catalog.read_text(encoding="utf-8")) or {}
        profile = self.query_one("#profile", Select).value
        self.choices = model_choices(
            known, self._lock_ids(), str(profile), self.wizard.gpu.get("vram_gib")
        )
        table = self.query_one("#models", DataTable)
        table.clear()
        table.add_row("none", "-", "-", "-", "-", "-", key="none")
        for choice in self.choices:
            table.add_row(
                choice.id,
                choice.hf_repo,
                choice.license,
                "yes" if choice.gated else "no",
                choice.fits or "yes",
                "yes" if choice.pinned else "no",
                key=choice.id,
            )

    def on_select_changed(self, event: Select.Changed) -> None:  # noqa: D102
        if event.select.id == "profile" and self.choices is not None and self.query("#models"):
            self._fill_models()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:  # noqa: D102
        self.answers.gpu.default_model = str(event.row_key.value)
        self.query_one("#chosen", Static).update(f"Default model: {self.answers.gpu.default_model}")

    def on_extra_button(self, event: Button.Pressed) -> None:  # noqa: D102
        if event.button.id == "pin":
            self.pin()
        elif event.button.id == "whoami":
            self.check_token(_input(self, "#hf"))

    @work(thread=True, exclusive=True)
    def check_token(self, token: str) -> None:
        """Validate the Hugging Face token with the Hub's whoami.

        Args:
            token: The token.
        """
        try:
            name = models.whoami(token)
        except models.HubError as exc:
            self.app.call_from_thread(self.notify, f"token not accepted: {exc}", severity="error")
            return
        self.app.call_from_thread(self.notify, f"token of {name} accepted")

    @work(thread=True, exclusive=True)
    def pin(self) -> None:
        """Resolve the chosen (or custom) model and add it to models.lock.yml."""
        token = _input(self, "#hf")
        repo, ref = _input(self, "#custom-repo"), _input(self, "#custom-ref") or "main"
        chosen = next((c for c in self.choices if c.id == self.answers.gpu.default_model), None)
        meta: dict[str, Any]
        if repo:
            if error := validators.hf_repo(repo):
                self.app.call_from_thread(self.notify, error, severity="error")
                return
            model_id = repo.rsplit("/", 1)[-1].lower()
            meta = {"license": "", "gated": False, "notes": "custom model chosen in the installer"}
        elif chosen is not None:
            repo, model_id, meta = chosen.hf_repo, chosen.id, dict(chosen.entry)
        else:
            self.app.call_from_thread(
                self.notify, "choose a listed model or enter a repository", severity="error"
            )
            return
        try:
            revision = models.resolve_revision(repo, ref, token)
            files = models.pin_files(repo, revision, token)
        except models.HubError as exc:
            self.app.call_from_thread(self.notify, f"cannot pin {repo}: {exc}", severity="error")
            return
        text = self.wizard.extra_files.get(
            "models.lock.yml"
        ) or self.ctx.paths.models_lock.read_text(encoding="utf-8")
        entry = models.lock_entry(model_id, repo, revision, files, meta)
        self.wizard.extra_files["models.lock.yml"] = models.add_to_lock(text, entry)
        self.answers.gpu.default_model = model_id
        self.app.call_from_thread(self._pinned, model_id, revision, len(files))

    def _pinned(self, model_id: str, revision: str, count: int) -> None:
        self.notify(f"pinned {model_id} at {revision[:12]} ({count} files)")
        self.query_one("#chosen", Static).update(f"Default model: {model_id} (pinned now)")
        self._fill_models()

    def save(self) -> Problems:  # noqa: D102
        gpu = self.answers.gpu
        problems: Problems = []
        if self.wizard.is_wsl:
            gpu.install_driver = "skip"
        else:
            for value in ("auto", "manual", "skip"):
                if self.query_one(f"#driver-{value}", RadioButton).value:
                    gpu.install_driver = value
            problems += self._secure_boot_problems()
        profile = self.query_one("#profile", Select).value
        gpu.vllm_profile = "" if profile is Select.BLANK else str(profile)
        if gpu.default_model != "none" and gpu.default_model not in self._lock_ids():
            problems.append(("models", f"{gpu.default_model} is not pinned yet: press Pin model"))
        chosen = next((c for c in self.choices if c.id == gpu.default_model), None)
        token = _input(self, "#hf")
        self.wizard.provided.hf_token = token
        self.answers.secrets.hf_token = "provided" if token else "none"
        if chosen is not None and chosen.gated and not token:
            problems.append(("hf", "this model is gated: enter a Hugging Face token"))
        gpu.start_serving = self.query_one("#serve", Checkbox).value
        return problems + self._save_arbiter()

    def _secure_boot_problems(self) -> Problems:
        # Secure Boot without a driver (approved deviation I1): auto needs the consent
        # given on the Preflight screen and the one-time MOK password; manual would stop
        # the installation at bootstrap's preflight (exit 30).
        gpu = self.answers.gpu
        wizard = self.wizard
        if not wizard.mok_enrollment_needed:
            return []
        if gpu.install_driver == "manual":
            return [
                (
                    "driver",
                    "Secure Boot without a driver: install the driver yourself first and "
                    "Re-check on the Preflight screen, or choose auto or skip",
                )
            ]
        wizard.nvidia_driver = gpu.install_driver
        if gpu.install_driver == "skip":
            wizard.consents.discard("accept_mok_enrollment")
            gpu.accept_mok_enrollment = False
            wizard.mok_password = ""
            return []
        problems: Problems = []
        if "accept_mok_enrollment" not in wizard.consents:
            problems.append(
                ("driver", "Secure Boot: consent to the MOK enrollment on the Preflight screen")
            )
        first, second = _input(self, "#mok"), _input(self, "#mok2")
        if not first or first != second:
            problems.append(("mok", "enter the same one-time password twice"))
        wizard.mok_password = first if first == second else ""
        gpu.accept_mok_enrollment = "accept_mok_enrollment" in wizard.consents
        return problems


class BackupsScreen(StepScreen):
    """Screen 10: restic repository, password, schedule, retention."""

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
    """Screen 11 (restore mode): pick a snapshot and preview what is restored."""

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
            return "only in restore mode (screen 3)"
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
    """Screen 14: golden images to build, now or later."""

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
            if spec.get("type") == "virtual-machine" and not vm_ok:
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
