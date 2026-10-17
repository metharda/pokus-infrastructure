"""Screens 14-18: Secrets & vault, Review, Install progress, Doctor, Finish (BUILD_PROMPT §18.4)."""

from __future__ import annotations

import itertools
import json
import os
import re
from pathlib import Path
from typing import Any

import segno
from ruamel.yaml import YAML
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import (
    Button,
    Checkbox,
    Collapsible,
    DataTable,
    Input,
    RadioButton,
    RadioSet,
    RichLog,
    Static,
)

from pinka_installer import answers as answers_mod
from pinka_installer import apply, state, vault
from pinka_installer.answers import GENERATE, PROVIDED, Context
from pinka_installer.apply import Change, Plan
from pinka_installer.paths import write_file
from pinka_installer.tui.base import Problems, StepScreen
from pinka_installer.tui.events import Progress, site_roles
from pinka_installer.tui.model import password_strength, vault_password_problem
from pinka_installer.tui.runner import RunRequest
from pinka_installer.tui.widgets import Card, Field, NavBar, ShownOnce, TextView

MIN_GRAFANA_ADMIN = 8
PAIR_TAGS = "inference_control,observability,firewall"
# Two QR modules per character: (top, bottom) dark or light.
HALF_BLOCKS = {(1, 1): "█", (1, 0): "▀", (0, 1): "▄", (0, 0): " "}
EXIT_WSL_RESTART = 75
EXIT_REBOOT = 76
DOCTOR_CODES = (0, 1, 2)
EXIT_LINE_RE = re.compile(r"^readonly EXIT_[A-Z_]+=(\d+)\s+# ([^:]+): (.*)$")


def exit_meanings(exitcodes: Path) -> dict[int, str]:
    """Bootstrap's exit codes and their titles, from ``bootstrap/lib/exitcodes.sh``.

    Args:
        exitcodes: The file (the one definition of the codes).

    Returns:
        Code to title, such as ``60: play failed``.
    """
    try:
        lines = exitcodes.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    found = {}
    for line in lines:
        match = EXIT_LINE_RE.match(line)
        if match:
            found[int(match.group(1))] = match.group(2)
    return found


def _input(screen: StepScreen, selector: str) -> str:
    return screen.query_one(selector, Input).value


def qr_text(data: str) -> str:
    """A QR code drawn with half blocks (two modules per character).

    Args:
        data: The text to encode (the Tailscale login URL).

    Returns:
        Lines of block characters with a quiet zone.
    """
    code = segno.make(data, error="l", micro=False)
    matrix = [list(row) for row in code.matrix]
    size = len(matrix)
    border = 2
    rows = [[0] * (size + 2 * border) for _ in range(border)]
    rows += [[0] * border + list(r) + [0] * border for r in matrix]
    rows += [[0] * (size + 2 * border) for _ in range(border + 1)]  # an even row count
    lines = []
    for top, bottom in itertools.batched(rows, 2):
        lines.append("".join(HALF_BLOCKS[(t, b)] for t, b in zip(top, bottom, strict=True)))
    return "\n".join(lines)


class SecretsScreen(StepScreen):
    """Screen 14: generated service passwords, the Grafana admin, the vault password."""

    STEP = "secrets"
    HELP = (
        "Every service password is generated; their values are never shown. The vault "
        "(group_vars/all/vault.yml) is encrypted with the vault password, which you need "
        "for every later bootstrap. Storing it root-only in /etc/pinka/secrets/vault-pass "
        "makes unattended runs possible, but anyone with root on this host can then read "
        "every secret of the lab."
    )

    def body(self) -> ComposeResult:  # noqa: D102
        names = [
            f"database role {r}" for r in vault.postgres_roles(self.ctx.paths.postgres_defaults)
        ]
        names += [f"Grafana account {r.name}" for r in self.answers.researchers if r.grafana]
        yield Static(
            "Generated (values never shown):\n" + "\n".join(f"• {n}" for n in names), classes="info"
        )
        with RadioSet(id="admin"):
            yield RadioButton(
                "Generate the Grafana admin password",
                value=self.answers.secrets.grafana_admin == GENERATE,
                id="admin-generate",
            )
            yield RadioButton(
                "Enter the Grafana admin password",
                value=self.answers.secrets.grafana_admin == PROVIDED,
                id="admin-enter",
            )
        yield Field("Grafana admin password", Input(password=True, id="admin1"))
        yield Field("again", Input(password=True, id="admin2"))
        stored = self.ctx.paths.stored_vault_pass
        if self.ctx.paths.vault.exists():
            yield Static(
                "The vault exists: enter its password to update it.", classes="banner warn"
            )
            yield Field(
                "Vault password", Input(self.wizard.vault_password, password=True, id="vault1")
            )
        else:
            yield Field(
                "New vault password", Input(self.wizard.vault_password, password=True, id="vault1")
            )
            yield Field("again", Input(self.wizard.vault_password, password=True, id="vault2"))
            yield Static("", id="strength", classes="hint")
        yield Field(
            "…or read it from a file (0600)",
            Input(placeholder="/root/pinka-vault-pass", id="vault-file"),
            hint=f"a stored password in {stored} is used automatically" if stored.exists() else "",
        )
        yield Checkbox(
            f"Keep the vault password root-only in {stored}",
            self.answers.secrets.store_vault_password,
            id="store",
        )

    def on_input_changed(self, event: Input.Changed) -> None:  # noqa: D102
        if event.input.id == "vault1" and self.query("#strength"):
            _, word = password_strength(event.value)
            self.query_one("#strength", Static).update(f"strength: {word}")

    def _vault_password(self, problems: Problems) -> str:
        paths = self.ctx.paths
        path = _input(self, "#vault-file").strip()
        if path:
            try:
                return apply.read_password_file(Path(path))
            except (OSError, vault.VaultError) as exc:
                problems.append(("vault-file", str(exc)))
                return ""
        first = _input(self, "#vault1")
        if not first and paths.stored_vault_pass.exists():
            return apply.read_password_file(paths.stored_vault_pass)
        if self.query("#vault2") and (
            error := vault_password_problem(first, _input(self, "#vault2"))
        ):
            problems.append(("vault1", error))
        return first

    def save(self) -> Problems:  # noqa: D102
        problems: Problems = []
        secrets = self.answers.secrets
        if self.query_one("#admin-enter", RadioButton).value:
            secrets.grafana_admin = PROVIDED
            first, second = _input(self, "#admin1"), _input(self, "#admin2")
            if len(first) < MIN_GRAFANA_ADMIN or "$" in first or first != second:
                problems.append(("admin1", "8 or more characters without '$', entered twice"))
            self.wizard.provided.grafana_admin_password = first
        else:
            secrets.grafana_admin = GENERATE
            self.wizard.provided.grafana_admin_password = ""
        password = self._vault_password(problems)
        if password and self.ctx.paths.vault.exists():
            try:
                vault.decrypt(self.ctx.paths.vault.read_text(encoding="utf-8"), password)
            except vault.VaultError as exc:
                problems.append(("vault1", str(exc)))
        if not password and not problems:
            problems.append(("vault1", "enter the vault password"))
        self.wizard.vault_password = password
        secrets.store_vault_password = self.query_one("#store", Checkbox).value
        return problems


def platform_text(answers: answers_mod.Answers) -> str:
    """The Platform section of the Review screen.

    Args:
        answers: Normalized answers.

    Returns:
        Lines.
    """
    if not answers.has_platform:
        return "none: a standalone lab"
    p = answers.platform
    lines = [f"{p.repo} at {p.ref}" + (f", path {p.path}" if p.path else "")]
    lines += [f"content {n}: {s.repo} at {s.ref}" for n, s in sorted(answers.content.items())]
    signed = "required" if answers.source_trust.require_signed_tags else "not required"
    settings = ", ".join(sorted(answers.platform_settings)) or "no service"
    secrets = ", ".join(f"{k} {v}" for k, v in sorted(answers.secrets.platform.items()))
    lines.append(f"signed tags {signed} · settings for {settings} · secrets: {secrets or 'none'}")
    return "\n".join(lines)


def summary(
    answers: answers_mod.Answers, *, approved: bool = False, replacing: str = ""
) -> list[tuple[str, str, str]]:
    """The settings by section: (screen, title, text) for the Review screen.

    Args:
        answers: The answers.
        approved: The operator approved the reviewed platform.
        replacing: The active platform the operator confirmed replacing.

    Returns:
        Sections.
    """
    a = answers.normalized()
    m, r, s, g, b = a.machine, a.resources, a.storage, a.gpu, a.backups
    caps = a.training_caps
    pool = f"loop file of {s.loop_size_gib} GiB" if s.source == "loop" else f"disk {s.device}"
    return [
        ("mode", "Install mode", a.mode),
        (
            "machine",
            "Machine",
            f"{m.name} · {m.lab_mode} · {m.node_role}"
            + (f" (peer {a.split_inference_peer})" if a.split_inference_peer else "")
            + f"\nprovider {m.provider} · power {m.power_policy}"
            + f"\n{m.srv_path} · Docker {m.docker_data_root}",
        ),
        (
            "resources",
            "Resources",
            f"expected {r.expected_memory_gib or '-'} GiB, "
            f"{r.expected_cpu_threads or '-'} threads\n"
            f"services {r.cpuset_services or '-'} · targets {r.cpuset_targets or '-'} · "
            f"Postgres {r.postgres_shared_buffers or 'derived'}",
        ),
        ("storage", "Storage", pool + (" · WIPE signatures" if s.force_wipe else "")),
        (
            "researchers",
            "Researchers",
            ", ".join(f"{p.name}{' (admin)' if p.admin else ''}" for p in a.researchers)
            + (f"\nlocked: {', '.join(a.researchers_removed)}" if a.researchers_removed else ""),
        ),
        (
            "remote",
            "Remote access",
            f"{a.tailscale_tag} · login {a.remote_access.tailscale_login} · Grafana serve "
            f"{'on' if a.remote_access.grafana_tailscale_serve else 'off'}",
        ),
        (
            "gpu",
            "GPU & inference",
            f"driver {g.install_driver} · profile {g.vllm_profile or '-'} · "
            f"model {g.default_model}\n"
            f"arbiter after boot {a.gpu_arbiter.after_boot}, serving again after training "
            f"{'on' if a.gpu_arbiter.restore_after_training else 'off'} · training caps "
            f"{caps.timeout_seconds} s, {caps.cpus} CPUs, memory {caps.memory_max}, "
            f"datasets {caps.dataset_max}, run storage {caps.run_storage_max}, "
            f"tmpfs {caps.tmp_max}",
        ),
        (
            "backups",
            "Backups",
            f"{b.repository.type} {b.repository.url_for_restic()} · {b.schedule} UTC · "
            f"keep {b.retention.daily}/{b.retention.weekly}/{b.retention.monthly}",
        ),
        ("restore", "Restore", a.restore_snapshot if a.mode == "restore" else "-"),
        ("platform", "Platform", platform_text(a)),
        (
            "platform_review",
            "Platform review",
            (
                ("approved" if approved else "not approved yet")
                + (f" · replaces the active platform {replacing}" if replacing else "")
            )
            if a.has_platform
            else "-",
        ),
        ("images", "Golden images", ", ".join(a.images_build) or "none"),
        (
            "secrets",
            "Secrets",
            f"Grafana admin {a.secrets.grafana_admin} · "
            f"vault password stored: {a.secrets.store_vault_password}",
        ),
    ]


def destructive(answers: answers_mod.Answers) -> list[str]:
    """What cannot be undone (shown in red on the Review screen).

    Args:
        answers: The answers.

    Returns:
        Lines.
    """
    found = []
    if answers.storage.source == "device":
        wipe = ", wiping its signatures" if answers.storage.force_wipe else ""
        found.append(f"{answers.storage.device} is formatted as the Incus pool{wipe}")
    if answers.mode == "restore":
        found.append(f"the lab's data is replaced by snapshot {answers.restore_snapshot}")
    if answers.researchers_removed:
        found.append(f"accounts are locked: {', '.join(answers.researchers_removed)}")
    return found


class ReviewScreen(StepScreen):
    """Screen 15: every setting, every file with its diff, estimates, destructive actions."""

    STEP = "review"
    HELP = (
        "Nothing has been changed yet. Check every section (Edit jumps back), the files that "
        "will be written with their differences, and the actions in red, which cannot be "
        "undone. Install writes the files and starts the installation; Export answers only "
        "writes installer-answers/<machine>.yml for a later --answers run."
    )

    def __init__(self) -> None:
        """Build the screen."""
        super().__init__()
        self.plan: Plan | None = None
        self.problems: list[str] = []

    def nav(self) -> NavBar:  # noqa: D102
        return NavBar("Install ▸", extra=(Button("Export answers only", id="export"),))

    def body(self) -> ComposeResult:  # noqa: D102
        self.problems, self.plan = self.build_plan()
        if self.problems:
            yield Static(
                "Fix these first:\n" + "\n".join(f"• {p}" for p in self.problems),
                classes="banner bad",
            )
        for warning in destructive(self.answers):
            yield Static(f"⚠ {warning}", classes="banner bad")
        replacing = self.wizard.replacing()
        if replacing:
            yield Static(
                f"⚠ The active platform {replacing} is replaced (you typed its name); its data "
                f"stays until pinka platform purge {replacing}",
                classes="banner bad",
            )
        for step, title, text in summary(
            self.answers, approved=self.wizard.platform_approved, replacing=replacing
        ):
            with Horizontal(classes="review-row"):
                yield Card(title, text)
                yield Button("Edit", id=f"edit-{step}", classes="edit")
        yield Static(self.estimates(), classes="info", id="estimates")
        if self.plan is not None:
            for warning in self.plan.warnings:
                yield Static(f"Note: {warning}", classes="hint")
            for change in self.plan.changes:
                rel = (
                    change.path.relative_to(self.ctx.paths.infra)
                    if change.path.is_relative_to(self.ctx.paths.infra)
                    else change.path
                )
                state_word = (
                    "new" if change.old is None else "changed" if change.changed else "unchanged"
                )
                with Collapsible(title=f"{rel} ({state_word})", collapsed=True):
                    yield Static(
                        change.diff(self.ctx.paths.infra) or "(no difference)", markup=False
                    )

    def build_plan(self) -> tuple[list[str], Plan | None]:
        """Check the answers and plan the files.

        Returns:
            The problems and the plan (None while there are problems).
        """
        answers = self.answers
        context = apply.context(self.ctx.paths, self.wizard.cpu_threads)
        extra_lock = self.wizard.extra_files.get("models.lock.yml")
        if extra_lock:
            ids = {
                str(m.get("id"))
                for m in (YAML(typ="safe").load(extra_lock) or {}).get("models") or []
            }
            context = Context(**{**context.__dict__, "model_ids": context.model_ids | ids})
        found = answers_mod.schema_problems(answers.to_dict()) + answers_mod.problems(
            answers, context
        )
        if found:
            return found, None
        try:
            plan = apply.plan(
                answers,
                self.ctx.paths,
                self.wizard.provided,
                self.wizard.vault_password,
                self.wizard.public_keys,
                smt_cores=self.ctx.host.smt_cores(),
                needs=self.wizard.platform_needs,
            )
        except (OSError, vault.VaultError) as exc:
            return [str(exc)], None
        if extra_lock:
            lock = self.ctx.paths.models_lock
            plan.changes.append(Change(lock, lock.read_text(encoding="utf-8"), extra_lock))
        return [], plan

    def estimates(self) -> str:
        """Downloads and duration, as far as they are known before the install.

        Returns:
            Text.
        """
        lock = YAML(typ="safe").load(self.ctx.paths.versions_lock.read_text(encoding="utf-8"))
        images = len(((lock.get("containers") or {}).get("images")) or {})
        model = self.answers.gpu.default_model
        return (
            f"Downloads: up to {images} pinned container images"
            + (f", model {model}" if model != "none" else "")
            + f"; {len(self.answers.images_build)} golden image(s) to build. Sizes and the "
            "duration depend on the network and are shown while the install runs."
        )

    def on_extra_button(self, event: Button.Pressed) -> None:  # noqa: D102
        button = event.button.id or ""
        if button.startswith("edit-"):
            self.installer.goto(button.removeprefix("edit-"))
        elif button == "export":
            if self.problems:
                self.notify("fix the problems first", severity="error")
                return
            path = self.ctx.paths.answers(self.answers.machine.name)
            write_file(path, answers_mod.dumps(self.answers))
            self.notify(f"wrote {path}")

    def save(self) -> Problems:  # noqa: D102
        if self.problems or self.plan is None:
            return [("estimates", "fix the problems listed at the top first")]
        return []

    def proceed(self) -> None:  # noqa: D102
        assert self.plan is not None  # noqa: S101 - save() checked it
        self._install(list(self.plan.shown_once))

    def _install(self, pending: list[tuple[str, str]]) -> None:
        """Show each secret generated for the vault once, then write and install."""
        if pending:
            name, value = pending.pop(0)
            self.app.push_screen(
                ShownOnce(name, value), lambda stored: self._install(pending) if stored else None
            )
            return
        assert self.plan is not None  # noqa: S101 - proceed() checked it
        self.plan.write()
        self.installer.goto("progress")


class ProgressScreen(StepScreen):
    """Screen 16: the phase tree, the current task, counts, the log, the Tailscale login."""

    STEP = "progress"
    HELP = (
        "The installation runs bootstrap non-interactively. Each line is a bootstrap stage "
        "or an Ansible role. A failure shows the task, its error and what to do; Retry runs "
        "bootstrap again (it is idempotent). If WSL or the host must restart, follow the "
        "instructions; run bootstrap again afterwards and the installer resumes here."
    )

    def __init__(self, options: list[str] | None = None) -> None:
        """Build the screen.

        Args:
            options: Extra bootstrap options (a reconfiguration after pairing).
        """
        super().__init__()
        self.extra_options = options or []
        self.progress = Progress()
        self.started = 0.0
        self.exit_code: int | None = None

    def meanings(self) -> dict[int, str]:
        """Bootstrap's exit codes."""
        return exit_meanings(self.ctx.paths.bootstrap.parent / "lib" / "exitcodes.sh")

    def nav(self) -> NavBar:  # noqa: D102
        return NavBar(
            "Continue ▸",
            back=False,
            extra=(
                Button("Retry", id="retry", disabled=True),
                Button("Open log", id="log"),
                Button("Abort", id="abort", variant="error"),
            ),
        )

    def body(self) -> ComposeResult:  # noqa: D102
        yield Static("", id="status", classes="info")
        with Horizontal(id="progress-main"):
            table: DataTable[str] = DataTable(id="phases", cursor_type="none")
            yield table
            with Vertical(id="side"):
                yield Static("", id="login", classes="code", markup=False)
                yield Static("", id="deploy-key", classes="code", markup=False)
                yield Static("", id="failure", classes="banner bad", markup=False)
        with Collapsible(title="Log", collapsed=True, id="log-pane"):
            yield RichLog(id="output", max_lines=2000, markup=False)

    def on_mount(self) -> None:  # noqa: D102
        table = self.query_one("#phases", DataTable)
        table.add_columns(" ", "phase", "ok", "changed", "failed")
        self.query_one("#next", Button).disabled = True
        self.start()

    def start(self) -> None:
        """Start (or restart) the installation."""
        roles = site_roles(self.ctx.paths.infra / "ansible" / "site.yml")
        self.progress = Progress.planned(
            roles, restore=self.answers.mode == "restore", provider=self.wizard.provider
        )
        self.started = self.ctx.clock()
        self.exit_code = None
        failure = self.query_one("#failure", Static)
        failure.update("")
        failure.display = False
        self.query_one("#retry", Button).disabled = True
        self._show_progress()
        self.installer.save_state("progress")
        self.run_bootstrap()

    @work(thread=True, exclusive=True)
    def run_bootstrap(self) -> None:
        """Run bootstrap in the background and feed its events to the screen."""
        options = self.wizard.bootstrap_options(self.ctx.passthrough) + self.extra_options
        request = RunRequest(
            self.answers.machine.name,
            options,
            self.wizard.vault_password,
            self.wizard.mok_password,
        )
        code = self.ctx.runner.run(
            request,
            lambda events: self.app.call_from_thread(self._events, events),
            lambda line: self.app.call_from_thread(self._output, line),
        )
        self.app.call_from_thread(self._finished, code)

    def _events(self, events: list[dict[str, Any]]) -> None:
        for event in events:
            self.progress.apply(event)
        self._show_progress()

    def _output(self, line: str) -> None:
        if line.strip() and not line.startswith(("ok: [", "skipping: [", "included: ")):
            self.query_one("#output", RichLog).write(line)

    def _show_progress(self) -> None:
        table = self.query_one("#phases", DataTable)
        table.clear()
        for phase in self.progress.phases.values():
            table.add_row(
                phase.icon, phase.name, str(phase.ok), str(phase.changed), str(phase.failed)
            )
        totals = self.progress.totals
        elapsed = int(self.ctx.clock() - self.started)
        if self.exit_code is not None:
            meaning = self.meanings().get(self.exit_code, "see below")
            now = f"finished with exit code {self.exit_code}: {meaning}"
            if self.exit_code in DOCTOR_CODES:
                now = "Installation finished; Continue runs doctor."
        else:
            now = self.progress.current_task or "starting…"
        self.query_one("#status", Static).update(
            f"{now}\n"
            f"elapsed {elapsed // 60}:{elapsed % 60:02d} · ok {totals['ok']} · changed "
            f"{totals['changed']} · failed {totals['failed']}"
        )
        login = self.progress.login_url
        self.query_one("#login", Static).update(
            f"Log in to Tailscale:\n{login}\n{qr_text(login)}" if login else ""
        )
        key = self.progress.deploy_key
        self.query_one("#deploy-key", Static).update(
            f"Add this read-only deploy key to the Git host:\n{key}" if key else ""
        )

    def _finished(self, code: int) -> None:
        self.exit_code = code
        self.progress.current_task = ""
        self._show_progress()
        signal = self.progress.signal[0] if self.progress.signal else None
        restarts = (EXIT_WSL_RESTART, EXIT_REBOOT)
        if signal in restarts or code in restarts:
            self._signal(signal or code)
            return
        if code in DOCTOR_CODES:
            self.installer.result_code = code
            saved = state.load(self.ctx.paths.state_file) or state.State()
            saved.phases_done = sorted({*saved.phases_done, "ansible"})
            state.save(saved, self.ctx.paths.state_file)
            self.query_one("#next", Button).disabled = False
            return
        failure = self.progress.failure
        text = f"Bootstrap ended with exit code {code}: {self.meanings().get(code, 'see the log')}."
        if self.progress.signal:
            text += f"\n{self.progress.signal[1]}"
        if failure is not None:
            text += (
                f"\nFailed: {failure.phase} > {failure.task}\n{failure.message}"
                f"\n{failure.stderr[-600:]}"
            )
        text += f"\nWhat to do: docs/how-to/troubleshoot.md (exit code {code}), then Retry."
        shown = self.query_one("#failure", Static)
        shown.update(text)
        shown.display = True
        self.query_one("#retry", Button).disabled = False

    def _signal(self, code: int) -> None:
        saved = state.load(self.ctx.paths.state_file) or state.State()
        saved.signal = code
        saved.screen = "progress"
        saved.answers = self.wizard.resume_data()
        state.save(saved, self.ctx.paths.state_file)
        if code == EXIT_REBOOT:
            # bootstrap imported the MOK (if one was waiting) with this password
            self.wizard.mok_password = ""
        self.installer.show(
            SignalScreen(code, self.progress.signal[1] if self.progress.signal else "")
        )

    def on_extra_button(self, event: Button.Pressed) -> None:  # noqa: D102
        if event.button.id == "retry":
            self.start()
        elif event.button.id == "log":
            path = getattr(self.ctx.runner, "log_file", "") or ""
            text = "\n".join(self.progress.log[-400:])
            if path and Path(path).exists():
                text = "\n".join(
                    Path(path).read_text(encoding="utf-8", errors="replace").splitlines()[-400:]
                )
            self.app.push_screen(TextView(f"Log {path}".strip(), text or "(empty)"))
        elif event.button.id == "abort":
            self.installer.action_quit_installer()

    def save(self) -> Problems:  # noqa: D102
        if self.exit_code not in DOCTOR_CODES:
            return [("status", "the installation has not finished successfully")]
        return []

    def proceed(self) -> None:  # noqa: D102
        self.installer.goto("doctor")


class SignalScreen(StepScreen):
    """Full-screen instructions for a WSL restart (75) or a reboot (76)."""

    STEP = "signal"

    def __init__(self, code: int, message: str) -> None:
        """Build the screen.

        Args:
            code: 75 or 76.
            message: The play's message.
        """
        super().__init__()
        self.code = code
        self.message = message

    def title_text(self) -> str:  # noqa: D102
        return "WSL restart needed" if self.code == EXIT_WSL_RESTART else "Reboot needed"

    def nav(self) -> NavBar:  # noqa: D102
        return NavBar(
            "", back=False, extra=(Button("Quit installer", id="quit", variant="primary"),)
        )

    def body(self) -> ComposeResult:  # noqa: D102
        if self.code == EXIT_WSL_RESTART:
            # sudo usually drops WSL_DISTRO_NAME; 'wsl -l -v' lists the names then.
            name = os.environ.get("WSL_DISTRO_NAME") or "<distro>"
            text = (
                f"In Windows PowerShell run:\n\n    wsl --terminate {name}\n\n"
                + ("(wsl -l -v lists the distributions.)\n\n" if name == "<distro>" else "")
                + "Then open WSL again and run:\n\n    sudo ./bootstrap/bootstrap.sh\n\n"
                "The installer resumes here."
            )
        else:
            text = (
                "Reboot the host, then run sudo ./bootstrap/bootstrap.sh again; the installer "
                "resumes here.\n\nIf Secure Boot is on, MOK Manager appears during the boot: "
                "choose Enroll MOK, Continue, Yes, enter the one-time MOK password you chose, "
                "then Reboot."
            )
        yield Static(text, classes="banner warn", markup=False)
        if self.message:
            yield Static(self.message, classes="hint", markup=False)

    def on_extra_button(self, event: Button.Pressed) -> None:  # noqa: D102
        if event.button.id == "quit":
            self.installer.quit_installer(self.code)


def checks_by_category(report: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Doctor's checks grouped by the part of the id before the first dot.

    Args:
        report: ``pinka doctor --json``.

    Returns:
        Category to checks.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for check in report.get("checks") or []:
        groups.setdefault(str(check.get("id", "")).split(".", 1)[0], []).append(check)
    return groups


class DoctorScreen(StepScreen):
    """Screen 17: doctor's results with remediation, and the optional smoke test."""

    STEP = "doctor"
    HELP = (
        "Doctor checks the installed lab: services, security, networking, storage, "
        "backups. Warnings are fine to start with (a fresh lab has no backup yet); "
        "failures come with what to do. The smoke test launches a throwaway target of every "
        "golden image."
    )

    def nav(self) -> NavBar:  # noqa: D102
        return NavBar(back=False, extra=(Button("Run smoke test", id="smoke"),))

    def body(self) -> ComposeResult:  # noqa: D102
        yield Static("Running doctor…", id="doctor-status", classes="info")
        table: DataTable[str] = DataTable(id="checks", cursor_type="row")
        yield table
        yield Static("", id="remediation", classes="hint", markup=False)

    def on_mount(self) -> None:  # noqa: D102
        self.query_one("#checks", DataTable).add_columns(" ", "category", "check", "detail")
        self.run_doctor(False)

    @work(thread=True, exclusive=True)
    def run_doctor(self, smoke: bool) -> None:
        """Run ``pinka doctor --json`` in the background.

        Args:
            smoke: Also run the smoke test.
        """
        argv = ["pinka", "doctor", "--json"] + (["--smoke"] if smoke else [])
        done = self.ctx.host.run(argv, timeout=3600)
        try:
            report = json.loads(done.stdout)
        except ValueError:
            report = {"checks": [], "error": done.stderr.strip()[-400:]}
        self.app.call_from_thread(self._show, report)

    def _show(self, report: dict[str, Any]) -> None:
        self.report = report
        table = self.query_one("#checks", DataTable)
        table.clear()
        icons = {"pass": "✔", "skip": "-", "fail": "✖"}
        counts = {"pass": 0, "warn": 0, "fail": 0}
        for category, checks in sorted(checks_by_category(report).items()):
            for check in checks:
                result = str(check.get("result"))
                kind = "warn" if result == "fail" and check.get("severity") == "warning" else result
                counts[kind] = counts.get(kind, 0) + 1
                icon = "⚠" if kind == "warn" else icons.get(result, "?")
                table.add_row(
                    icon,
                    category,
                    str(check.get("id")),
                    str(check.get("detail", ""))[:80],
                    key=str(check.get("id")),
                )
        error = report.get("error")
        self.query_one("#doctor-status", Static).update(
            f"doctor could not run: {error}"
            if error
            else f"{counts['pass']} passed · {counts['warn']} warnings · {counts['fail']} failures"
        )

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:  # noqa: D102
        check = next(
            (
                c
                for c in getattr(self, "report", {}).get("checks", [])
                if str(c.get("id")) == event.row_key.value
            ),
            None,
        )
        if check:
            self.query_one("#remediation", Static).update(
                f"{check.get('description')}\n{check.get('remediation') or ''}".strip()
            )

    def on_extra_button(self, event: Button.Pressed) -> None:  # noqa: D102
        if event.button.id == "smoke":
            self.query_one("#doctor-status", Static).update("Running doctor with the smoke test…")
            self.run_doctor(True)

    def proceed(self) -> None:  # noqa: D102
        self.installer.goto("finish")


class FinishScreen(StepScreen):
    """Screen 18: how to connect, what to commit, logs, start and stop, pairing."""

    STEP = "finish"
    HELP = (
        "Researchers connect over the tailnet with SSH. Commit the files the installer wrote "
        "to your lab's private configuration repository (never to the public project): the "
        "commit goes to branch machine/<name> with you as author and is never pushed. Lab "
        "and inference nodes are paired here once both are installed."
    )

    def nav(self) -> NavBar:  # noqa: D102
        buttons = [Button("Commit files", id="commit")]
        role = self.answers.machine.node_role
        if role == "inference":
            buttons.append(Button("Show pairing code", id="pair-show"))
        elif role == "lab":
            buttons.append(Button("Pair", id="pair-connect"))
        return NavBar("Finish", back=False, extra=tuple(buttons))

    def body(self) -> ComposeResult:  # noqa: D102
        a = self.answers
        machine = a.machine.name
        ssh = "\n".join(f"  ssh {r.name}@{machine}" for r in a.researchers)
        grafana = (
            f"https://{machine}.<tailnet>.ts.net/ (tailscale serve)"
            if a.remote_access.grafana_tailscale_serve
            else f"ssh -L 18700:127.0.0.1:18700 <user>@{machine}, then http://localhost:18700/"
        )
        lines = [f"Connect (over the tailnet):\n{ssh}", f"Grafana: {grafana}"]
        if a.machine.node_role in ("full", "inference") and a.gpu.default_model != "none":
            lines.append(
                f"vLLM: http://127.0.0.1:18704/v1 on {machine}, model {a.gpu.default_model}"
            )
        lines.append("Start and stop the lab: sudo pinka start | sudo pinka stop")
        lines.append("Logs: /var/log/pinka-bootstrap/ · state: /etc/pinka/bootstrap-state.json")
        yield Static("\n\n".join(lines), classes="info", markup=False)
        yield Static(
            "Files to commit:\n" + "\n".join(f"  {f}" for f in self.files()),
            classes="hint",
            markup=False,
        )
        if a.machine.node_role == "lab":
            with Horizontal(classes="inline"):
                yield Input(a.split_inference_peer, placeholder="inference node", id="peer")
                yield Input(placeholder="pairing code", id="code")
        log = RichLog(id="pair-log", max_lines=200, markup=False)
        log.display = a.machine.node_role in ("lab", "inference")
        yield log

    def files(self) -> list[str]:
        """The files of this installation in the repository.

        Returns:
            Paths relative to the repository root.
        """
        paths, machine = self.ctx.paths, self.answers.machine.name
        candidates = [
            paths.host_vars(machine),
            paths.group_vars_main,
            paths.vault,
            paths.answers(machine),
            paths.models_lock,
            *(paths.public_keys(r.name) for r in self.answers.researchers),
        ]
        root = paths.infra
        return [str(p.relative_to(root)) for p in candidates if p.exists()]

    def on_extra_button(self, event: Button.Pressed) -> None:  # noqa: D102
        if event.button.id == "commit":
            self.commit()
        elif event.button.id == "pair-show":
            self.pair(["pinka", "pair", "show"])
        elif event.button.id == "pair-connect":
            node, code = _input(self, "#peer").strip(), _input(self, "#code").strip()
            self.answers.split_inference_peer = node
            self.pair(["pinka", "pair", "connect", node, code])

    @work(thread=True, exclusive=True)
    def commit(self) -> None:
        """Commit the files on branch machine/<name> as the invoking user (never pushed)."""
        user = self.ctx.host.invoking_user()
        if not user:
            self.app.call_from_thread(
                self.notify, "run through sudo to commit as yourself", severity="error"
            )
            return
        root = str(self.ctx.paths.infra)
        git = ["sudo", "-u", user, "git", "-C", root]
        branch = f"machine/{self.answers.machine.name}"
        steps = [
            [*git, "switch", "-C", branch],
            [*git, "add", "--force", "--", *self.files()],
            [
                *git,
                "commit",
                "-m",
                f"chore({self.answers.machine.name}): lab configuration from the installer",
            ],
        ]
        for argv in steps:
            done = self.ctx.host.run(argv)
            if done.returncode != 0:
                self.app.call_from_thread(
                    self.notify, f"git: {done.stderr.strip()[-300:]}", severity="error"
                )
                return
        self.app.call_from_thread(
            self.notify, f"committed on {branch}; push it to your private repository"
        )

    @work(thread=True, exclusive=True)
    def pair(self, argv: list[str]) -> None:
        """Run a pinka pair command and show its output.

        Args:
            argv: The command.
        """
        done = self.ctx.host.run(argv, timeout=660)
        output = (done.stdout + done.stderr).strip()
        self.app.call_from_thread(self.query_one("#pair-log", RichLog).write, output)
        if done.returncode == 0:
            self.app.call_from_thread(self._paired, argv)

    def _paired(self, argv: list[str]) -> None:
        if argv[2] == "show":
            for line in self.query_one("#pair-log", RichLog).lines:
                text = line.text
                if text.startswith("Paired with lab node "):
                    self.answers.split_inference_peer = text.split()[4]
        try:
            plan = apply.plan(
                self.answers,
                self.ctx.paths,
                self.wizard.provided,
                self.wizard.vault_password,
                needs=self.wizard.platform_needs,
            )
        except (OSError, vault.VaultError) as exc:
            self.notify(str(exc), severity="error")
            return
        plan.write()
        self.installer.current = "progress"
        self.installer.show(ProgressScreen([f"--tags={PAIR_TAGS}"]))

    def proceed(self) -> None:  # noqa: D102
        state.clear(self.ctx.paths.state_file)
        self.app.exit(self.installer.result_code)
