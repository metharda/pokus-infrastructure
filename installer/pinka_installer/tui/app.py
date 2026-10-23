"""The installer application (BUILD_PROMPT §18): navigation, resume state, themes.

:class:`InstallerApp` shows one :class:`~pinka_installer.tui.base.StepScreen`
at a time. ``goto`` saves the resume state before every screen change, so an
interrupted installation continues at the same screen when bootstrap starts
the installer again. :func:`run` refuses unsuitable terminals (``TERM=dumb``,
no terminal, smaller than 80x24) and points to ``--answers`` instead.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import ClassVar

from textual.app import App
from textual.binding import Binding, BindingType
from textual.theme import Theme

from pinka_installer import answers as answers_mod
from pinka_installer import apply, state, vault
from pinka_installer.paths import Paths
from pinka_installer.tui.base import Context, StepScreen
from pinka_installer.tui.host import Host, RealHost
from pinka_installer.tui.model import STEP_NAMES, Wizard, existing_answers
from pinka_installer.tui.runner import BootstrapRunner, Runner
from pinka_installer.tui.screens_host import MachineScreen, ResourcesScreen, StorageScreen
from pinka_installer.tui.screens_install import (
    DoctorScreen,
    FinishScreen,
    ProgressScreen,
    ReviewScreen,
    SecretsScreen,
)
from pinka_installer.tui.screens_people import RemoteScreen, ResearchersScreen
from pinka_installer.tui.screens_platform import PlatformReviewScreen, PlatformScreen
from pinka_installer.tui.screens_services import (
    BackupsScreen,
    GpuScreen,
    ImagesScreen,
    RestoreScreen,
)
from pinka_installer.tui.screens_start import ModeScreen, PreflightScreen, WelcomeScreen
from pinka_installer.tui.screens_uninstall import UninstallRunScreen, UninstallScreen
from pinka_installer.tui.widgets import Confirm, PasswordPrompt

MIN_COLUMNS = 80
MIN_ROWS = 24
EXIT_OPERATOR = 77  # bootstrap/lib/exitcodes.sh: operator action required
RESUME_HINT = "Run sudo ./bootstrap/bootstrap.sh again to continue where you left off."
WITHOUT_TUI = "docs/how-to/install-without-the-tui.md"
AFTER_EXIT = {
    75: "WSL must restart: run 'wsl --terminate <distro>' in Windows PowerShell.",
    76: "The host must reboot (enroll the MOK in MOK Manager if asked).",
    EXIT_OPERATOR: "Stopped before the installation finished.",
}

SCREENS: dict[str, type[StepScreen]] = {
    "welcome": WelcomeScreen,
    "preflight": PreflightScreen,
    "mode": ModeScreen,
    "machine": MachineScreen,
    "resources": ResourcesScreen,
    "storage": StorageScreen,
    "researchers": ResearchersScreen,
    "remote": RemoteScreen,
    "gpu": GpuScreen,
    "backups": BackupsScreen,
    "restore": RestoreScreen,
    "platform": PlatformScreen,
    "platform_review": PlatformReviewScreen,
    "images": ImagesScreen,
    "secrets": SecretsScreen,
    "review": ReviewScreen,
    "progress": ProgressScreen,
    "doctor": DoctorScreen,
    "finish": FinishScreen,
    "uninstall": UninstallScreen,
    "uninstall_run": UninstallRunScreen,
}
# Uninstall removes the resume state with /var/lib/pinka: its screens never write it.
STATELESS = ("welcome", "uninstall", "uninstall_run")

# Black and white with saturated accents; for low-vision use and poor terminals.
HIGH_CONTRAST = Theme(
    name="pinka-high-contrast",
    primary="#FFFF00",
    secondary="#00FFFF",
    accent="#FF00FF",
    foreground="#FFFFFF",
    background="#000000",
    surface="#000000",
    panel="#1A1A1A",
    success="#00FF00",
    warning="#FFFF00",
    error="#FF4040",
    dark=True,
)
THEMES = ("textual-dark", "textual-light", HIGH_CONTRAST.name)


class InstallerApp(App[int]):
    """The Pokus Infrastructure Installer.

    The result of :meth:`run` is the exit status of the installer: the
    installation's own (0, 1 or 2 from doctor) after Finish, a bootstrap
    signal (75, 76) when the host must restart, or 77 when the operator quit
    before the installation finished.
    """

    CSS_PATH = "installer.tcss"
    TITLE = "Pokus Infrastructure Installer"
    ENABLE_COMMAND_PALETTE = False  # its Quit would skip the confirmation
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("f2", "cycle_theme", "Theme"),
        Binding("ctrl+q", "quit_installer", "Quit", priority=True),
    ]

    def __init__(self, ctx: Context, first: str = "welcome", theme: str = "") -> None:
        """Build the application.

        Args:
            ctx: What the screens work with.
            first: The first screen (tests start elsewhere).
            theme: A theme of :data:`THEMES` (default: Textual's dark theme).
        """
        vault.preload()  # before Textual replaces standard output
        super().__init__()
        self.ctx = ctx
        self.first = first
        self.initial_theme = theme
        self.current = ""
        self.result_code = 0
        self._state_warned = False

    def on_mount(self) -> None:  # noqa: D102
        self.register_theme(HIGH_CONTRAST)
        if self.initial_theme:
            self.theme = self.initial_theme
        self.goto(self.first)

    # --- navigation -----------------------------------------------------------------

    def show(self, screen: StepScreen) -> None:
        """Make a screen current (it replaces the current one).

        Args:
            screen: The screen.
        """
        if len(self.screen_stack) <= 1:
            self.push_screen(screen)
        else:
            self.switch_screen(screen)

    def goto(self, name: str) -> None:
        """Save the resume state and show a screen by name.

        Args:
            name: A key of :data:`SCREENS`.
        """
        if name == "progress" and not self.ctx.wizard.vault_password:
            self.ask_vault_password(lambda: self.goto("progress"))
            return
        self.current = name
        if name not in STATELESS:
            self.save_state(name)
        self.show(SCREENS[name]())

    def forward(self) -> None:
        """The next wizard screen; after Install mode, Uninstall when it was chosen."""
        if self.current == "mode" and self.ctx.wizard.uninstall:
            self.goto("uninstall")
            return
        if self.current in STEP_NAMES:
            index = STEP_NAMES.index(self.current)
            if index + 1 < len(STEP_NAMES):
                self.goto(STEP_NAMES[index + 1])

    def back(self) -> None:
        """The previous wizard screen (Install mode from Uninstall)."""
        if self.current == "uninstall":
            self.goto("mode")
            return
        if self.current in STEP_NAMES:
            index = STEP_NAMES.index(self.current)
            if index > 0:
                self.goto(STEP_NAMES[index - 1])

    # --- resume state and secrets -----------------------------------------------------

    def save_state(self, name: str) -> None:
        """Record the screen and the answers so far (never a secret).

        Args:
            name: The screen being shown.
        """
        path = self.ctx.paths.state_file
        saved = state.load(path) or state.State()
        saved.screen = name
        saved.answers = self.ctx.wizard.resume_data()
        try:
            state.save(saved, path)
        except OSError as exc:
            if not self._state_warned:
                self._state_warned = True
                self.notify(
                    f"cannot save the resume state in {path}: {exc.strerror}",
                    severity="warning",
                )

    def ask_vault_password(self, then: Callable[[], None]) -> None:
        """Get the vault password after a resume: the stored copy, or ask for it.

        Args:
            then: Called once the wizard has the password.
        """
        paths, wizard = self.ctx.paths, self.ctx.wizard
        if paths.stored_vault_pass.exists():
            try:
                wizard.vault_password = apply.read_password_file(paths.stored_vault_pass)
            except (OSError, vault.VaultError) as exc:
                self.notify(f"{paths.stored_vault_pass}: {exc}", severity="warning")
            else:
                then()
                return

        def check(password: str) -> str | None:
            if not password:
                return "enter the vault password"
            if not paths.vault.exists():
                return None
            try:
                vault.decrypt(paths.vault.read_text(encoding="utf-8"), password)
            except vault.VaultError as exc:
                return str(exc)
            return None

        def done(password: str | None) -> None:
            if password is not None:
                wizard.vault_password = password
                then()

        self.push_screen(
            PasswordPrompt(
                "Vault password",
                "The installation continues with the vault password, which is kept in "
                "memory only. Enter it again.",
                check,
            ),
            done,
        )

    # --- actions ----------------------------------------------------------------------

    def action_cycle_theme(self) -> None:
        """Switch between the dark, light and high-contrast themes (F2)."""
        current = THEMES.index(self.theme) if self.theme in THEMES else -1
        self.theme = THEMES[(current + 1) % len(THEMES)]
        self.notify(f"theme: {self.theme}", timeout=2)

    def running(self) -> bool:
        """Whether bootstrap is running (the progress screen's worker)."""
        return any(worker.name == "run_bootstrap" and worker.is_running for worker in self.workers)

    def action_quit_installer(self) -> None:
        """Quit after confirmation; stop a running bootstrap first."""
        running = self.running()
        text = (
            "The installation is running. Stopping interrupts bootstrap; running it "
            "again continues safely (every step is idempotent)."
            if running
            else "Your choices so far are kept (never the secrets)."
        )

        def done(yes: bool | None) -> None:
            if not yes:
                return
            if running:
                self.ctx.runner.cancel()
            self.quit_installer()

        self.push_screen(Confirm("Quit the installer?", f"{text}\n\n{RESUME_HINT}", "Quit"), done)

    def quit_installer(self, code: int = EXIT_OPERATOR) -> None:
        """Leave with the state saved.

        Args:
            code: Exit status.
        """
        screen = self.screen
        if (
            isinstance(screen, StepScreen)
            and screen.STEP in STEP_NAMES
            and screen.not_applicable() is None
        ):
            screen.save()  # keep what was typed, unchecked, as Back does
        if self.current and self.current not in ("welcome", "finish"):
            self.save_state(self.current)
        self.exit(code)


def terminal_problem(term: str | None, interactive: bool, size: os.terminal_size | None) -> str:
    """Why this terminal cannot show the installer, or empty.

    Args:
        term: ``$TERM``.
        interactive: Standard input and output are terminals.
        size: The terminal's size, if known.

    Returns:
        The reason, or empty.
    """
    if not term or term == "dumb":
        return f"TERM is {term or 'unset'}"
    if not interactive:
        return "standard input or output is not a terminal"
    if size is None or size.columns < MIN_COLUMNS or size.lines < MIN_ROWS:
        shown = f"{size.columns}x{size.lines}" if size else "of unknown size"
        return f"the terminal is {shown}, the installer needs {MIN_COLUMNS}x{MIN_ROWS}"
    return ""


def build_context(
    paths: Paths,
    host: Host,
    runner: Runner,
    *,
    machine: str = "",
    answers: Path | None = None,
    bootstrap_args: Sequence[str] = (),
) -> Context:
    """Collect what the screens need, and the answers to start from.

    An answers file is the starting point when given; otherwise an installed
    lab opens in modify mode, pre-filled from its files (BUILD_PROMPT §18.1).

    Args:
        paths: Installer paths.
        host: Host access.
        runner: Runs bootstrap.
        machine: ``--machine`` of the first bootstrap.
        answers: ``--answers`` of the first bootstrap.
        bootstrap_args: Options for the bootstrap the installer runs.

    Returns:
        The context.

    Raises:
        answers_mod.AnswersError: The answers file cannot be used.
        OSError: The answers file cannot be read.
    """
    wizard = Wizard()
    installed = host.installed_machine()
    wizard.installed = bool(installed)
    if answers is not None:
        wizard.answers = answers_mod.load(answers, apply.context(paths))
    elif installed and (not machine or machine == installed):
        wizard.answers = existing_answers(paths, installed)
    elif machine:
        wizard.answers.machine.name = machine
    return Context(
        paths=paths,
        host=host,
        runner=runner,
        wizard=wizard,
        git=host.git_info(),
        passthrough=list(bootstrap_args),
        machine=machine or installed,
        log_file=os.environ.get("PINKA_BOOTSTRAP_LOG", ""),
    )


def replaced_platform(bootstrap_args: Sequence[str]) -> str:
    """The platform bootstrap may replace (``--replace-platform=NAME``), or "".

    Args:
        bootstrap_args: Options for the bootstrap the installer runs.

    Returns:
        The name.
    """
    prefix = "--replace-platform="
    found = [a[len(prefix) :] for a in bootstrap_args if a.startswith(prefix)]
    return found[-1] if found else ""


def run(
    paths: Paths,
    *,
    machine: str = "",
    answers: Path | None = None,
    bootstrap_args: Sequence[str] = (),
) -> int:
    """Start the installer on this terminal.

    Args:
        paths: Installer paths.
        machine: ``--machine`` of the first bootstrap.
        answers: ``--answers`` of the first bootstrap.
        bootstrap_args: Options for the bootstrap the installer runs.

    Returns:
        The exit status (see :class:`InstallerApp`).
    """
    try:
        size: os.terminal_size | None = os.get_terminal_size(sys.stdout.fileno())
    except (OSError, ValueError):
        size = None
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    problem = terminal_problem(os.environ.get("TERM"), interactive, size or None)
    if problem:
        sys.stderr.write(
            f"pinka-installer: the installer cannot start: {problem}.\n"
            f"Use a larger terminal, or an answers file ({WITHOUT_TUI}).\n"
        )
        return EXIT_OPERATOR
    try:
        ctx = build_context(
            paths,
            RealHost(paths, replace=replaced_platform(bootstrap_args)),
            BootstrapRunner(paths),
            machine=machine,
            answers=answers,
            bootstrap_args=bootstrap_args,
        )
    except answers_mod.AnswersError as exc:
        sys.stderr.write(f"pinka-installer: {answers} cannot be used:\n")
        sys.stderr.writelines(f"  {problem}\n" for problem in exc.problems)
        return EXIT_OPERATOR
    except OSError as exc:
        sys.stderr.write(f"pinka-installer: cannot read {answers}: {exc.strerror}\n")
        return EXIT_OPERATOR
    result = InstallerApp(ctx).run()
    code = EXIT_OPERATOR if result is None else result
    if code in AFTER_EXIT:
        sys.stderr.write(f"pinka-installer: {AFTER_EXIT[code]} {RESUME_HINT}\n")
    return code
