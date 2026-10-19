"""Screens 7-8: Researchers, Remote access (BUILD_PROMPT §18.4)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Input,
    Label,
    RadioButton,
    RadioSet,
    SelectionList,
    Static,
    TextArea,
)

from pinka_installer import groupvars, keys, validators
from pinka_installer.answers import Researcher
from pinka_installer.tui.base import Problems, StepScreen
from pinka_installer.tui.model import (
    acl_snippet,
    adopted_text,
    closable_listeners,
    install_path,
    published_ports,
)
from pinka_installer.tui.widgets import Field, NavBar


@dataclass
class Entry:
    """A researcher being edited, with keys."""

    researcher: Researcher
    check: keys.KeyCheck | None


class ResearcherForm(ModalScreen[Entry | None]):
    """Add or edit one researcher (screen 7)."""

    def __init__(
        self, entry: Entry | None, taken: set[str], service_accounts: frozenset[str]
    ) -> None:
        """Build the form.

        Args:
            entry: The researcher to edit, or None to add one.
            taken: Names already in the list.
            service_accounts: The lab's service accounts.
        """
        super().__init__()
        self.entry = entry
        self.taken = taken
        self.service_accounts = service_accounts

    def compose(self) -> ComposeResult:  # noqa: D102
        researcher = self.entry.researcher if self.entry else Researcher("", grafana=True)
        with Vertical(id="dialog"):
            yield Label("Researcher" if self.entry else "Add a researcher", classes="dialog-title")
            with VerticalScroll():
                yield Field(
                    "Linux user name",
                    Input(researcher.name, id="r-name", disabled=self.entry is not None),
                )
                yield Field(
                    "Display name (Grafana)", Input(researcher.display_name, id="r-display")
                )
                yield Checkbox(
                    "Admin (pinka-admin: sudo on this host)", researcher.admin, id="r-admin"
                )
                yield Checkbox("Grafana account", researcher.grafana, id="r-grafana")
                yield Field(
                    "SSH public keys (paste, one per line)",
                    TextArea("", id="r-keys"),
                    hint="or read them from a file:",
                )
                with Horizontal(classes="inline"):
                    yield Input(placeholder="/home/<user>/.ssh/id_ed25519.pub", id="r-keyfile")
                    yield Button("Read", id="r-read")
                yield Static(
                    self._fingerprints(self.entry.check if self.entry else None),
                    id="r-fingerprints",
                )
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="r-cancel")
                yield Button("OK", id="r-ok", variant="primary")

    @staticmethod
    def _fingerprints(check: keys.KeyCheck | None) -> str:
        if check is None or not check.keys:
            return "No keys yet: the researcher gets no SSH access until keys are added."
        return "Keys:\n" + "\n".join(check.fingerprints)

    def on_button_pressed(self, event: Button.Pressed) -> None:  # noqa: D102
        event.stop()
        if event.button.id == "r-cancel":
            self.dismiss(None)
        elif event.button.id == "r-read":
            path = Path(self.query_one("#r-keyfile", Input).value.strip())
            try:
                self.query_one("#r-keys", TextArea).text = path.read_text(encoding="utf-8")
            except OSError as exc:
                self.notify(f"cannot read {path}: {exc.strerror}", severity="error")
        elif event.button.id == "r-ok":
            self._submit()

    def _submit(self) -> None:
        name = self.query_one("#r-name", Input).value.strip()
        error = validators.username(name, self.service_accounts)
        if not error and self.entry is None and name in self.taken:
            error = "already in the list"
        self.query_one("#field-r-name", Field).set_error(error or "")
        text = self.query_one("#r-keys", TextArea).text
        check = self.entry.check if self.entry else None
        if text.strip():
            check = keys.check(text)
            message = "; ".join(check.errors)
            self.query_one("#field-r-keys", Field).set_error(message)
            self.query_one("#r-fingerprints", Static).update(self._fingerprints(check))
            if check.errors:
                return
        if error:
            return
        researcher = Researcher(
            name=name,
            admin=self.query_one("#r-admin", Checkbox).value,
            display_name=self.query_one("#r-display", Input).value.strip(),
            grafana=self.query_one("#r-grafana", Checkbox).value,
        )
        self.dismiss(Entry(researcher, check))


class ResearchersScreen(StepScreen):
    """Screen 7: the lab's researchers and their SSH keys."""

    STEP = "researchers"
    HELP = (
        "Every researcher gets a Linux account, a directory in the research space and "
        "optionally a Grafana account. Admins are members of pinka-admin (sudo). Keys are "
        "checked before anything is written; a private key is refused. Researchers you "
        "remove from an installed lab are locked, not deleted: their data stays."
    )

    def __init__(self) -> None:
        """Build the screen."""
        super().__init__()
        self.entries: list[Entry] = []
        self.known: set[str] = set()

    def nav(self) -> NavBar:  # noqa: D102
        return NavBar(
            extra=(
                Button("Add", id="add"),
                Button("Edit", id="edit"),
                Button("Remove", id="remove"),
            )
        )

    def body(self) -> ComposeResult:  # noqa: D102
        table: DataTable[str] = DataTable(id="people", cursor_type="row")
        yield table
        yield Static("", id="people-note", classes="hint")

    def on_mount(self) -> None:  # noqa: D102
        paths = self.ctx.paths
        existing, _ = groupvars.researchers(paths.group_vars_main.read_text(encoding="utf-8"))
        if self.answers.mode == "modify":
            self.known = {r.name for r in existing}
        researchers = self.answers.researchers
        if not researchers and self.answers.mode == "modify":
            researchers = existing
        user = self.ctx.host.invoking_user()
        if not researchers and user and not validators.username(user):
            researchers = [Researcher(user, admin=True, grafana=True)]
        for researcher in researchers:
            check = self.wizard.public_keys.get(researcher.name)
            if check is None:
                text = keys.read(paths.public_keys(researcher.name))
                check = keys.check(text, use_ssh_keygen=False) if text.strip() else None
            self.entries.append(Entry(researcher, check))
        table = self.query_one("#people", DataTable)
        table.add_columns("user", "display name", "admin", "Grafana", "SSH keys")
        self._refresh()

    def _refresh(self) -> None:
        table = self.query_one("#people", DataTable)
        table.clear()
        for entry in self.entries:
            r = entry.researcher
            count = len(entry.check.keys) if entry.check else 0
            table.add_row(
                r.name,
                r.display_name or "-",
                "yes" if r.admin else "no",
                "yes" if r.grafana else "no",
                f"{count} key(s)" if count else "none",
                key=r.name,
            )
        user = self.ctx.host.invoking_user()
        note = ""
        if user and user not in {e.researcher.name for e in self.entries}:
            note = f"Note: you ({user}) are not in the list."
        self.query_one("#people-note", Static).update(note)

    def _selected(self) -> Entry | None:
        table = self.query_one("#people", DataTable)
        if not self.entries or table.cursor_row < 0:
            return None
        return self.entries[min(table.cursor_row, len(self.entries) - 1)]

    def on_extra_button(self, event: Button.Pressed) -> None:  # noqa: D102
        accounts = self._service_accounts()
        if event.button.id == "add":
            taken = {e.researcher.name for e in self.entries}
            self.app.push_screen(ResearcherForm(None, taken, accounts), self._added)
        elif event.button.id == "edit" and (entry := self._selected()):
            self.app.push_screen(ResearcherForm(entry, set(), accounts), self._edited)
        elif event.button.id == "remove" and (entry := self._selected()):
            self.entries.remove(entry)
            self._refresh()

    def _service_accounts(self) -> frozenset[str]:
        from pinka_installer.apply import context  # noqa: PLC0415

        return context(self.ctx.paths).service_accounts

    def _added(self, entry: Entry | None) -> None:
        if entry is not None:
            self.entries.append(entry)
            self._refresh()

    def _edited(self, entry: Entry | None) -> None:
        if entry is None:
            return
        for index, current in enumerate(self.entries):
            if current.researcher.name == entry.researcher.name:
                self.entries[index] = entry
        self._refresh()

    def save(self) -> Problems:  # noqa: D102
        names = [e.researcher.name for e in self.entries]
        self.answers.researchers = [e.researcher for e in self.entries]
        self.wizard.public_keys = {e.researcher.name: e.check for e in self.entries if e.check}
        removed = sorted((self.known | set(self.answers.researchers_removed)) - set(names))
        self.answers.researchers_removed = removed
        if not self.entries:
            return [("people", "add at least one researcher")]
        if not any(e.researcher.admin for e in self.entries):
            return [("people", "at least one researcher must be an admin")]
        return []


class RemoteScreen(StepScreen):
    """Screen 8: Tailscale, Grafana serve, extra SSH interfaces, access from before pinka."""

    STEP = "remote"
    HELP = (
        "The lab is reachable only over your tailnet. Log in with an auth key (masked, "
        "stored in the vault) or interactively: the login URL and a QR code appear during "
        "the install. Grafana can be published on the tailnet with tailscale serve (never "
        "Funnel). Extra interfaces open SSH on a local network as well. On a shared host what "
        "was reachable before pinka stays reachable unless you tick it to close; the SSH "
        "session of this install closes only with your consent and after a researcher logged "
        "in over the tailnet during the run. Add the ACL lines shown here to your tailnet "
        "policy file."
    )

    def body(self) -> ComposeResult:  # noqa: D102
        remote = self.answers.remote_access
        tag = self.answers.tailscale_tag
        adopted = self.wizard.tailscale_adopted
        if adopted:
            yield Static(adopted_text(adopted, tag), id="adopted", classes="info", markup=False)
        else:
            yield Static(
                f"Tailscale host name: {self.answers.machine.name} (the machine name)",
                classes="info",
            )
        yield Field(
            "Tailscale tag",
            Input(tag, id="tag", disabled=bool(adopted)),
            hint="the tag the tailnet ACL and doctor expect" if adopted else "",
        )
        with RadioSet(id="login", disabled=bool(adopted)):
            yield RadioButton(
                "Auth key", value=remote.tailscale_login == "auth_key", id="login-key"
            )
            yield RadioButton(
                "Interactive login (URL and QR code during the install)",
                value=remote.tailscale_login == "interactive",
                id="login-interactive",
            )
        keep = " (empty: keep the key in the vault)" if self.ctx.paths.vault.exists() else ""
        yield Field(
            "Auth key",
            Input(
                self.wizard.provided.tailscale_authkey,
                password=True,
                id="authkey",
                disabled=bool(adopted),
            ),
            hint=f"pre-approved, tagged with the tag above; stored only in the vault{keep}",
        )
        dns = Checkbox("Accept the tailnet's DNS settings", remote.accept_dns, id="dns")
        dns.disabled = bool(adopted)
        yield dns
        yield Checkbox(
            "Publish Grafana with tailscale serve", remote.grafana_tailscale_serve, id="serve"
        )
        if adopted:
            yield Field(
                "Consent",
                Checkbox(
                    "pinka may add its tailscale serve entries to this node",
                    remote.tailscale_serve_consent,
                    id="consent",
                ),
                hint="uninstall removes only the entries pinka added",
            )
        interfaces = self.ctx.host.interfaces()
        chosen = set(remote.ssh_extra_allowed_interfaces)
        selection: SelectionList[str] = SelectionList(
            *[(n, n, n in chosen) for n in interfaces], id="ifaces"
        )
        selection.disabled = self.wizard.is_wsl
        yield Field(
            "Extra SSH interfaces",
            selection,
            hint="not on WSL: Windows owns the network"
            if self.wizard.is_wsl
            else "besides tailscale0",
        )
        yield from self.access_fields()
        yield Static(acl_snippet(tag), id="acl", classes="code", markup=False)

    def access_fields(self) -> ComposeResult:
        """What existed before pinka and may close with consent (BUILD_PROMPT §7.5)."""
        remote = self.answers.remote_access
        access = self.wizard.access
        closable = closable_listeners(access)
        if closable:
            chosen = set(remote.close_listeners)
            listeners: SelectionList[str] = SelectionList(
                *[(label, name, name in chosen) for name, label in closable], id="close"
            )
            published = published_ports(access)
            yield Field(
                "Close with consent",
                listeners,
                hint="this host is shared: what you do not tick stays reachable as it was; "
                "the services keep running"
                + (
                    f"; Docker publishes {', '.join(published)} for containers, which the "
                    "firewall cannot close"
                    if published
                    else ""
                ),
            )
        path = install_path(access)
        if path is not None:
            yield Field(
                "Install session",
                Checkbox(
                    f"Close SSH to {path.get('address')}:{path.get('port')} after a tailnet login",
                    remote.close_install_path,
                    id="close-path",
                ),
                hint="it closes at the end of a bootstrap run in which a researcher logged in "
                "with a key over the tailnet; without that login, or without this consent, it "
                "stays open and doctor warns",
            )

    def nav(self) -> NavBar:  # noqa: D102
        return NavBar(extra=(Button("Copy ACL", id="copy"),))

    def on_extra_button(self, event: Button.Pressed) -> None:  # noqa: D102
        if event.button.id == "copy":
            self.app.copy_to_clipboard(acl_snippet(self.query_one("#tag", Input).value.strip()))
            self.notify("ACL snippet copied to the clipboard")

    def save(self) -> Problems:  # noqa: D102
        remote = self.answers.remote_access
        problems: Problems = []
        tag = self.query_one("#tag", Input).value.strip()
        default = "tag:pinka-qa" if self.answers.machine.lab_mode == "staging" else "tag:pinka-lab"
        remote.tailscale_tag = "" if tag == default else tag
        if error := validators.tailscale_tag(tag):
            problems.append(("tag", error))
        adopted = bool(self.wizard.tailscale_adopted)
        remote.tailscale_login = (
            "auth_key"
            if self.query_one("#login-key", RadioButton).value and not adopted
            else "interactive"  # an adopted node is never logged in by pinka
        )
        key = self.query_one("#authkey", Input).value.strip()
        self.wizard.provided.tailscale_authkey = key if remote.tailscale_login == "auth_key" else ""
        if remote.tailscale_login == "auth_key" and not key and not self.ctx.paths.vault.exists():
            problems.append(("authkey", "paste the auth key, or choose the interactive login"))
        remote.accept_dns = self.query_one("#dns", Checkbox).value
        remote.grafana_tailscale_serve = self.query_one("#serve", Checkbox).value
        remote.tailscale_serve_consent = adopted and self.query_one("#consent", Checkbox).value
        if error := validators.serve_consent(
            adopted, remote.grafana_tailscale_serve, remote.tailscale_serve_consent
        ):
            problems.append(("consent", error))
        remote.ssh_extra_allowed_interfaces = (
            [] if self.wizard.is_wsl else list(self.query_one("#ifaces", SelectionList).selected)
        )
        # Shown only where they apply; otherwise the answers stand as they are.
        if self.query("#close"):
            remote.close_listeners = list(self.query_one("#close", SelectionList).selected)
        if self.query("#close-path"):
            remote.close_install_path = self.query_one("#close-path", Checkbox).value
        return problems
