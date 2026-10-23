"""The base of every wizard screen (BUILD_PROMPT §18.4): header, body, navigation, help."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar, cast

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Static

from pinka_installer.answers import Answers
from pinka_installer.paths import Paths
from pinka_installer.tui.host import GitInfo, Host
from pinka_installer.tui.model import AFTER_STEPS, STEP_NAMES, STEPS, Wizard
from pinka_installer.tui.runner import Runner
from pinka_installer.tui.widgets import Field, NavBar, StepHeader, TextView

if TYPE_CHECKING:
    from pinka_installer.tui.app import InstallerApp


@dataclass
class Context:
    """What every screen works with.

    Attributes:
        paths: Installer paths.
        host: Host access.
        runner: Runs bootstrap.
        wizard: The collected choices.
        git: The checkout.
        passthrough: Options the operator gave the first bootstrap.
        machine: Machine name given to bootstrap (``--machine``), if any.
        log_file: The installer's own log (for the Welcome screen).
        clock: Seconds for elapsed times (the tests fix it).
    """

    paths: Paths
    host: Host
    runner: Runner
    wizard: Wizard
    git: GitInfo
    passthrough: list[str] = field(default_factory=list)
    machine: str = ""
    log_file: str = ""
    clock: Callable[[], float] = time.monotonic


Problems = list[tuple[str, str]]


class StepScreen(Screen[None]):
    """One screen of the wizard.

    Subclasses set ``STEP`` (a name of :data:`STEPS`) and ``HELP``, build their
    fields in :meth:`body`, and copy them into the wizard in :meth:`save`,
    which returns ``(field id, message)`` for every problem.
    """

    STEP: ClassVar[str] = ""
    HELP: ClassVar[str] = ""
    NEXT_LABEL: ClassVar[str] = "Next ▸"
    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("f1", "help", "Help"),
        Binding("ctrl+n", "next", "Next", show=False),
        Binding("ctrl+b", "back", "Back", show=False),
    ]

    @property
    def installer(self) -> InstallerApp:
        """The application."""
        return cast("InstallerApp", self.app)

    @property
    def ctx(self) -> Context:
        """The shared context."""
        return self.installer.ctx

    @property
    def wizard(self) -> Wizard:
        """The collected choices."""
        return self.ctx.wizard

    @property
    def answers(self) -> Answers:
        """The answers being collected."""
        return self.wizard.answers

    def title_text(self) -> str:
        """The title of this screen."""
        return dict(STEPS).get(self.STEP) or AFTER_STEPS.get(self.STEP, self.STEP)

    def position(self) -> str:
        """``step N/15`` for wizard screens, empty after them."""
        if self.STEP not in STEP_NAMES:
            return ""
        return f"step {STEP_NAMES.index(self.STEP) + 1}/{len(STEP_NAMES)}"

    def not_applicable(self) -> str | None:
        """Why this screen does not apply to the host or the choices (shown greyed out).

        Returns:
            The one-line explanation, or None.
        """
        return None

    def body(self) -> ComposeResult:
        """The screen's fields."""
        yield from ()

    def nav(self) -> NavBar:
        """The navigation bar.

        Returns:
            The bar.
        """
        return NavBar(self.NEXT_LABEL, back=self.STEP != "welcome")

    def compose(self) -> ComposeResult:  # noqa: D102
        yield StepHeader(self.title_text(), self.position())
        with VerticalScroll(id="body"):
            reason = self.not_applicable()
            if reason:
                yield Static(f"Not applicable here: {reason}", classes="greyed")
            else:
                yield from self.body()
        yield self.nav()
        yield Footer()

    def save(self) -> Problems:
        """Copy the fields into the wizard.

        Returns:
            One ``(field id, message)`` per problem.
        """
        return []

    def show_problems(self, problems: Problems) -> None:
        """Show each message next to its field (or as a notification).

        Args:
            problems: From :meth:`save`.
        """
        for widget in self.query(Field):
            widget.set_error("")
        for field_id, message in problems:
            found = self.query(f"#field-{field_id}")
            if found:
                cast(Field, found.first()).set_error(message)
            else:
                self.notify(message, severity="error")

    def action_next(self) -> None:
        """Validate and go on."""
        problems = [] if self.not_applicable() else self.save()
        self.show_problems(problems)
        if problems:
            return
        self.proceed()

    def proceed(self) -> None:
        """Go to the next screen (a screen may ask something first)."""
        self.installer.forward()

    def action_back(self) -> None:
        """Go back without validating (nothing is lost)."""
        if self.not_applicable() is None:
            self.save()
        self.installer.back()

    def action_help(self) -> None:
        """Show the screen's help (F1)."""
        text = self.HELP or "No help for this screen."
        keys = (
            "\n\nKeys: Tab/Shift+Tab move between fields, Enter presses a button, "
            "Ctrl+N next, Ctrl+B back, F1 help, F2 theme, Ctrl+Q quit."
        )
        self.app.push_screen(TextView(f"Help · {self.title_text()}", text + keys))

    def on_button_pressed(self, event: Button.Pressed) -> None:  # noqa: D102
        actions: dict[str | None, Callable[[], None]] = {
            "next": self.action_next,
            "back": self.action_back,
            "help": self.action_help,
        }
        action = actions.get(event.button.id)
        if action is not None:
            event.stop()
            action()
        else:
            self.on_extra_button(event)

    def on_extra_button(self, event: Button.Pressed) -> None:
        """A screen's own button.

        Args:
            event: The press.
        """
