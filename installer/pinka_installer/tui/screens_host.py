"""Step 4: Machine."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Input, Select, Static

from pinka_installer import validators
from pinka_installer.tui.base import Problems, StepScreen
from pinka_installer.tui.widgets import Field

LAB_MODES = [("production", "production"), ("staging (QA: tag:pinka-qa, own vault)", "staging")]
POWER_POLICIES = [
    ("keep awake while a lab session runs", "lab_session_inhibit"),
    ("never sleep", "never_sleep"),
    ("the OS default", "os_default"),
]
PROVIDERS = [
    ("auto (from preflight)", "auto"),
    ("bare metal", "baremetal"),
    ("VM", "vm"),
    ("WSL2", "wsl2"),
]


def _value(screen: StepScreen, selector: str) -> str:
    return screen.query_one(selector, Input).value.strip()


def _select(screen: StepScreen, selector: str) -> str:
    value = screen.query_one(selector, Select).value
    return "" if value is Select.BLANK else str(value)


class MachineScreen(StepScreen):
    """Step 4: name, lab mode, provider, power policy, paths."""

    STEP = "machine"
    HELP = (
        "The machine name names the host_vars file and the tailnet host; it must be unique "
        "in your configuration. Staging hosts are QA labs with their own vault, research "
        "space and Tailscale identity. The node role (full, lab or inference) is chosen in "
        "the capacity plan. The provider comes from preflight; change it only when preflight's "
        "confidence is low. /srv/pinka needs a file system with room for the research space."
    )

    def body(self) -> ComposeResult:  # noqa: D102
        machine = self.answers.machine
        yield Field("Machine name", Input(machine.name or self.ctx.machine, id="name"))
        yield Field(
            "Lab mode", Select(LAB_MODES, value=machine.lab_mode, allow_blank=False, id="lab_mode")
        )
        confidence = (self.wizard.profile.get("environment") or {}).get("confidence")
        detected = self.wizard.provider or "unknown"
        if confidence == "low":
            yield Field(
                f"Provider (detected: {detected}, low confidence)",
                Select(PROVIDERS, value=machine.provider, allow_blank=False, id="provider"),
            )
        else:
            yield Static(f"Provider: {detected} (from preflight)", classes="info")
        power = Select(
            POWER_POLICIES, value=machine.power_policy, allow_blank=False, id="power_policy"
        )
        power.disabled = self.wizard.is_wsl
        yield Field(
            "Power policy",
            power,
            hint="not on WSL: Windows decides when the host sleeps" if self.wizard.is_wsl else "",
        )
        yield Field("Research space (/srv/pinka)", Input(machine.srv_path, id="srv_path"))
        yield Static("", id="srv-check", classes="hint")
        yield Field("Docker data root", Input(machine.docker_data_root, id="docker_data_root"))

    def on_mount(self) -> None:  # noqa: D102
        self._check_srv()

    def on_input_changed(self, event: Input.Changed) -> None:  # noqa: D102
        if event.input.id == "srv_path":
            self._check_srv()

    def _check_srv(self) -> None:
        found = self.query("#srv-check")
        if not found:
            return
        path = _value(self, "#srv_path")
        if validators.absolute_path(path):
            return
        try:
            free, fstype = self.ctx.host.free_gib(path)
        except OSError:
            return
        self.query_one("#srv-check", Static).update(f"{free:.0f} GiB free on {fstype or 'unknown'}")

    def save(self) -> Problems:  # noqa: D102
        machine = self.answers.machine
        problems: Problems = []
        machine.name = _value(self, "#name")
        if error := validators.hostname(machine.name):
            problems.append(("name", error))
        elif self.answers.mode != "modify" and self.ctx.paths.host_vars(machine.name).exists():
            problems.append(
                ("name", "host_vars exist for this name: choose Modify, or another name")
            )
        machine.lab_mode = _select(self, "#lab_mode")
        if self.query("#provider"):
            machine.provider = _select(self, "#provider")
        machine.power_policy = (
            "os_default" if self.wizard.is_wsl else _select(self, "#power_policy")
        )
        for key in ("srv_path", "docker_data_root"):
            setattr(machine, key, _value(self, f"#{key}"))
            if error := validators.absolute_path(getattr(machine, key)):
                problems.append((key, error))
        return problems
