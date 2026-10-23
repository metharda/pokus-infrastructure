"""Shared widgets of the installer screens (BUILD_PROMPT §18.4, §18.10)."""

from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, Checkbox, Input, Label, Static


class StepHeader(Horizontal):
    """``Pokus Infrastructure Installer ─── step 6/15 · Storage``."""

    def __init__(self, title: str, position: str) -> None:
        """Build the header.

        Args:
            title: The screen's title.
            position: Such as ``step 6/15``.
        """
        super().__init__(id="step-header")
        self._title = title
        self._position = position

    def compose(self) -> ComposeResult:  # noqa: D102
        yield Label("Pokus Infrastructure Installer", id="app-title")
        yield Label(
            f"{self._position} · {self._title}" if self._position else self._title, id="step"
        )


class NavBar(Horizontal):
    """Back, help and next buttons (plus a screen's own buttons)."""

    def __init__(
        self, next_label: str = "Next ▸", back: bool = True, extra: tuple[Button, ...] = ()
    ) -> None:
        """Build the bar.

        Args:
            next_label: Label of the forward button (empty: no forward button).
            back: Show the back button.
            extra: More buttons, between help and next.
        """
        super().__init__(id="nav")
        self._next_label = next_label
        self._back = back
        self._extra = extra

    def compose(self) -> ComposeResult:  # noqa: D102
        if self._back:
            yield Button("◂ Back", id="back")
        yield Button("F1 Help", id="help")
        yield Static(classes="spacer")
        yield from self._extra
        if self._next_label:
            yield Button(self._next_label, id="next", variant="primary")


class Field(Vertical):
    """A labelled input with its inline validation message."""

    def __init__(self, label: str, widget: Widget, hint: str = "", field_id: str = "") -> None:
        """Wrap a widget.

        Args:
            label: Label text.
            widget: The input widget.
            hint: Help text under it.
            field_id: Id of this field (for error messages); defaults to the widget's id.
        """
        super().__init__(classes="field", id=f"field-{field_id or widget.id}")
        self._label = label
        self._widget = widget
        self._hint = hint

    def compose(self) -> ComposeResult:  # noqa: D102
        yield Label(self._label, classes="field-label")
        yield self._widget
        if self._hint:
            yield Static(self._hint, classes="hint")
        yield Static("", classes="error")

    def set_error(self, message: str) -> None:
        """Show or clear the field's message.

        Args:
            message: The problem, or empty.
        """
        error = self.query_one(".error", Static)
        error.update(f"✖ {message}" if message else "")
        self.set_class(bool(message), "invalid")


class Card(Static):
    """A bordered panel with a title (preflight cards, review groups)."""

    def __init__(self, title: str, body: str, classes: str = "") -> None:
        """Build the card.

        Args:
            title: Border title.
            body: Rich text.
            classes: More CSS classes.
        """
        super().__init__(body, classes=f"card {classes}".strip())
        self.border_title = title


class TypedConfirm(ModalScreen[bool]):
    """A red danger dialog that needs an exact text typed (BUILD_PROMPT §18.4 screens 2, 6)."""

    def __init__(self, title: str, text: str, expected: str) -> None:
        """Build the dialog.

        Args:
            title: Title.
            text: What will happen.
            expected: What the operator must type.
        """
        super().__init__(classes="danger")
        self._title = title
        self._text = text
        self._expected = expected

    def compose(self) -> ComposeResult:  # noqa: D102
        with Vertical(id="dialog", classes="danger"):
            yield Label(self._title, classes="dialog-title")
            yield Static(self._text)
            yield Label(f"Type {self._expected!r} to confirm:")
            yield Input(id="typed")
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Confirm", id="confirm", variant="error", disabled=True)

    def on_input_changed(self, event: Input.Changed) -> None:  # noqa: D102
        self.query_one("#confirm", Button).disabled = event.value != self._expected

    def on_button_pressed(self, event: Button.Pressed) -> None:  # noqa: D102
        self.dismiss(event.button.id == "confirm")


class TextView(ModalScreen[None]):
    """A scrollable text (help, raw JSON, diffs, log excerpts)."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        ("escape", "close", "Close")
    ]

    def __init__(self, title: str, text: str) -> None:
        """Build the view.

        Args:
            title: Title.
            text: Plain text (no markup).
        """
        super().__init__()
        self._title = title
        self._text = text

    def compose(self) -> ComposeResult:  # noqa: D102
        with Vertical(id="dialog"):
            yield Label(self._title, classes="dialog-title")
            with VerticalScroll(classes="text-view"):
                yield Static(self._text, markup=False)
            with Horizontal(classes="buttons"):
                yield Button("Close", id="close", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:  # noqa: D102
        self.dismiss(None)

    def action_close(self) -> None:  # noqa: D102
        self.dismiss(None)


class ShownOnce(ModalScreen[bool]):
    """A generated secret, shown once until the operator confirms storing it (§18.7)."""

    def __init__(self, name: str, value: str) -> None:
        """Build the dialog.

        Args:
            name: What the secret is.
            value: The secret (never logged).
        """
        super().__init__()
        self._name = name
        self._value = value

    def compose(self) -> ComposeResult:  # noqa: D102
        with Vertical(id="dialog"):
            yield Label(f"Store the {self._name} now", classes="dialog-title")
            yield Static(
                "It is shown only here. Without it the backups cannot be restored. Beware of "
                "terminal scrollback and tmux logging; the screen is cleared afterwards."
            )
            yield Static(self._value, id="secret", markup=False)
            yield Checkbox("I stored this safely", id="stored")
            with Horizontal(classes="buttons"):
                yield Button("Continue", id="continue", variant="primary", disabled=True)

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:  # noqa: D102
        self.query_one("#continue", Button).disabled = not event.value

    def on_button_pressed(self, event: Button.Pressed) -> None:  # noqa: D102
        self.query_one("#secret", Static).update("")
        self.dismiss(True)


class Confirm(ModalScreen[bool]):
    """A yes/no question (quitting, stopping a running installation)."""

    def __init__(self, title: str, text: str, yes: str = "Yes") -> None:
        """Build the dialog.

        Args:
            title: Title.
            text: The question and its consequences.
            yes: Label of the confirming button.
        """
        super().__init__()
        self._title = title
        self._text = text
        self._yes = yes

    def compose(self) -> ComposeResult:  # noqa: D102
        with Vertical(id="dialog"):
            yield Label(self._title, classes="dialog-title")
            yield Static(self._text)
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button(self._yes, id="yes", variant="warning")

    def on_button_pressed(self, event: Button.Pressed) -> None:  # noqa: D102
        event.stop()
        self.dismiss(event.button.id == "yes")


class PasswordPrompt(ModalScreen[str | None]):
    """Ask for a password again (the vault password after a resume; never stored)."""

    def __init__(self, title: str, text: str, check: Callable[[str], str | None]) -> None:
        """Build the dialog.

        Args:
            title: Title.
            text: Why it is needed.
            check: Returns the problem with a password, or None if it is right.
        """
        super().__init__()
        self._title = title
        self._text = text
        self._check = check

    def compose(self) -> ComposeResult:  # noqa: D102
        with Vertical(id="dialog"):
            yield Label(self._title, classes="dialog-title")
            yield Static(self._text)
            yield Input(password=True, id="password")
            yield Static("", classes="error", id="password-error")
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Continue", id="continue", variant="primary")

    def on_input_submitted(self, event: Input.Submitted) -> None:  # noqa: D102
        event.stop()
        self._submit()

    def on_button_pressed(self, event: Button.Pressed) -> None:  # noqa: D102
        event.stop()
        if event.button.id == "continue":
            self._submit()
        else:
            self.dismiss(None)

    def _submit(self) -> None:
        value = self.query_one("#password", Input).value
        problem = self._check(value)
        if problem:
            self.query_one("#password-error", Static).update(f"✖ {problem}")
            return
        self.dismiss(value)
