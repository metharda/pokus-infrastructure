"""Screens 1-3: Welcome, Preflight, Install mode (BUILD_PROMPT §18.4)."""

from __future__ import annotations

import json
from typing import Any, ClassVar

from textual import work
from textual.app import ComposeResult
from textual.containers import Grid, Vertical
from textual.widgets import (
    Button,
    Checkbox,
    Collapsible,
    LoadingIndicator,
    RadioButton,
    RadioSet,
    Static,
)

from pinka_installer import state
from pinka_installer.tui.base import Problems, StepScreen
from pinka_installer.tui.host import Preflight
from pinka_installer.tui.model import existing_answers
from pinka_installer.tui.widgets import Card, NavBar, TextView, TypedConfirm

FORCE_TEXT = "FORCE UNSUPPORTED"
EXIT_OPERATOR = 77  # bootstrap/lib/exitcodes.sh
OUTCOME_STYLE = {
    "supported": "ok",
    "degraded": "warn",
    "needs_action": "warn",
    "unsupported": "bad",
}


class WelcomeScreen(StepScreen):
    """Screen 1: the checkout, the log, the keys, and resuming a previous install."""

    STEP = "welcome"
    HELP = (
        "The installer collects every setting of this lab host, shows them for review, "
        "writes the configuration files and then runs bootstrap non-interactively. "
        "Nothing is changed before the Review screen. Progress is saved after every "
        "screen, so a dropped SSH session, a WSL restart or a reboot continues here."
    )

    def __init__(self) -> None:
        """Build the screen."""
        super().__init__()
        self.saved: state.State | None = None

    def body(self) -> ComposeResult:  # noqa: D102
        git = self.ctx.git
        self.saved = state.load(self.ctx.paths.state_file)
        dirty = " [b]with uncommitted changes in infra/[/b]" if git.dirty else ""
        yield Static(
            f"Repository commit [b]{git.commit[:12]}[/b] on {git.ref}{dirty}\n"
            f"Log: {self.ctx.log_file or '/var/log/pinka-bootstrap/'}\n\n"
            "Keys: Tab moves between fields, Enter presses a button, F1 shows help, "
            "Ctrl+N goes on, Ctrl+B back, F2 switches the theme, Ctrl+Q quits.",
            id="welcome",
        )
        if self.saved is not None:
            yield Static("A previous installation was interrupted.", classes="banner warn")
            with RadioSet(id="resume"):
                yield RadioButton(
                    f"Resume (step {self.saved.screen}, started at {self.saved.started_at})",
                    value=True,
                    id="resume-yes",
                )
                yield RadioButton("Start over", id="resume-no")

    def proceed(self) -> None:  # noqa: D102
        if self.saved is not None and resume_choice(self):
            self.wizard.restore(self.saved.answers)
            self.installer.goto(
                self.saved.screen if self.saved.screen != "welcome" else "preflight"
            )
            return
        if self.saved is not None:
            state.clear(self.ctx.paths.state_file)
        self.installer.forward()


def _yes(value: object) -> str:
    return "yes" if value else "no"


def cards(profile: dict[str, Any]) -> list[tuple[str, str]]:
    """The preflight cards: (title, text).

    Args:
        profile: The host profile.

    Returns:
        The cards in display order.
    """
    env = profile.get("environment") or {}
    os_ = profile.get("os") or {}
    kernel = profile.get("kernel") or {}
    hw = profile.get("hardware") or {}
    gpu = profile.get("gpu") or {}
    caps = profile.get("capabilities") or {}
    disks = hw.get("disks") or {}
    srv = disks.get("srv_pinka") or {}
    endpoints = (profile.get("network") or {}).get("endpoints") or []
    conflicts = profile.get("conflicts") or []
    reachable = [e for e in endpoints if e.get("reachable")]
    return [
        (
            "Environment",
            f"{env.get('class', 'unknown')} ({env.get('hypervisor') or 'no hypervisor'}), "
            f"confidence {env.get('confidence', 'unknown')}",
        ),
        (
            "OS & kernel",
            f"{os_.get('pretty_name') or 'unknown'} ({os_.get('arch', '?')})\n"
            f"kernel {kernel.get('release') or 'unknown'}",
        ),
        (
            "CPU, RAM, disk",
            f"{hw.get('cpu_threads_effective') or hw.get('cpu_threads') or '?'} threads, "
            f"{hw.get('memory_gib') or '?'} GiB\n"
            f"{srv.get('path', '/srv/pinka')}: {srv.get('free_gib') or '?'} GiB free "
            f"({srv.get('fstype') or '?'})",
        ),
        (
            "GPU",
            f"{gpu.get('name') or gpu.get('vendor') or 'none'}: "
            f"{'usable' if gpu.get('usable') else gpu.get('driver_state') or 'not usable'}"
            + (f", {gpu.get('vram_gib')} GiB" if gpu.get("vram_gib") else ""),
        ),
        (
            "Virtualization",
            f"KVM {'usable' if caps.get('kvm_usable') else 'not usable'}, "
            "nested "
            + (_yes(caps.get("kvm_nested")) if caps.get("kvm_nested") is not None else "unknown"),
        ),
        (
            "Storage backends",
            f"btrfs {_yes(caps.get('btrfs'))}, LVM thin {_yes(caps.get('dm_thin_pool'))}; "
            f"pool backend {profile.get('storage_backend') or 'none'}",
        ),
        (
            "Network endpoints",
            "not checked (offline)"
            if (profile.get("network") or {}).get("offline")
            else f"{len(reachable)} of {len(endpoints)} reachable",
        ),
        (
            "Conflicts",
            "\n".join(f"{c.get('severity')}: {c.get('message')}" for c in conflicts) or "none",
        ),
    ]


class PreflightScreen(StepScreen):
    """Screen 2: preflight with a live spinner, its cards, the outcome and the consents."""

    STEP = "preflight"
    HELP = (
        "Preflight detects the environment, the OS, hardware, GPU, virtualization, "
        "storage, network and conflicts, and decides whether the lab is supported here. "
        "Degraded hosts need your consent per disabled feature; experimental tiers need "
        "consent; needs_action lists what to do first; unsupported hosts can only be "
        "forced by typing FORCE UNSUPPORTED, which is recorded in every fingerprint. "
        "With Secure Boot on and no NVIDIA driver, the installation can install the "
        "driver and import a Machine Owner Key, with your consent: you choose a one-time "
        "password on the GPU screen and enroll the key at the blue MOK Manager screen "
        "during the next boot."
    )
    MOK_TEXT = (
        "Secure Boot is on and no NVIDIA driver is installed. The installation can install "
        "the pinned driver and import a Machine Owner Key (MOK) that signs it: you choose a "
        "one-time password on the GPU screen, the installation stops for a reboot, and "
        "during the boot you enroll the key at the blue MOK Manager screen (you need the "
        "console then). Or continue without the driver: no local inference on this host."
    )
    CONSENT_IDS: ClassVar[tuple[str, ...]] = ("consent-feature", "consent-experimental")

    def __init__(self) -> None:
        """Build the screen."""
        super().__init__()
        self.result: Preflight | None = None

    def nav(self) -> NavBar:  # noqa: D102
        return NavBar(
            extra=(
                Button("Re-check", id="recheck"),
                Button("Raw JSON", id="raw"),
                Button("Abort", id="abort", variant="error"),
            )
        )

    def body(self) -> ComposeResult:  # noqa: D102
        yield LoadingIndicator(id="spinner")
        yield Vertical(id="report")

    async def on_mount(self) -> None:  # noqa: D102
        if self.wizard.profile and self.wizard.preflight_exit is not None:
            await self.show(Preflight(self.wizard.preflight_exit, self.wizard.profile))
        else:
            self.check()

    @work(thread=True, exclusive=True)
    def check(self) -> None:
        """Run preflight in the background."""
        self.app.call_from_thread(self._loading, True)
        result = self.ctx.host.preflight(self.wizard.preflight_args())
        self.app.call_from_thread(self.call_later, self.show, result)

    def _loading(self, on: bool) -> None:
        self.query_one("#spinner").display = on

    async def show(self, result: Preflight) -> None:
        """Render a preflight result (replacing the previous one).

        Args:
            result: The run.
        """
        self.result = result
        self.wizard.profile = result.profile
        self.wizard.preflight_exit = result.exit_code
        self._loading(False)
        report = self.query_one("#report", Vertical)
        await report.remove_children()
        profile = result.profile
        if not profile:
            report.mount(
                Static(f"Preflight failed:\n{result.stderr}", classes="banner bad", markup=False)
            )
            return
        outcome = str(profile.get("outcome"))
        report.mount(
            Static(
                f"Outcome: {outcome} · tier {profile.get('tier')} · provider "
                f"{profile.get('provider') or 'unknown'}",
                classes=f"banner {OUTCOME_STYLE.get(outcome, 'warn')}",
                id="outcome",
            )
        )
        grid = Grid(id="cards")
        report.mount(grid)
        for title, text in cards(profile):
            grid.mount(Card(title, text))
        signals = json.dumps((profile.get("environment") or {}).get("signals") or {}, indent=1)
        report.mount(
            Collapsible(Static(signals, markup=False), title="Environment signals", collapsed=True)
        )
        self._outcome_panel(report, profile)

    def _outcome_panel(self, report: Vertical, profile: dict[str, Any]) -> None:
        outcome = profile.get("outcome")
        if self.wizard.mok_enrollment_needed and self.wizard.nvidia_driver != "skip":
            report.mount(Static(self.MOK_TEXT, classes="banner warn", markup=False))
            report.mount(
                Checkbox(
                    "Install the driver and enroll a MOK at the next boot (recorded)",
                    value="accept_mok_enrollment" in self.wizard.consents,
                    id="consent-mok",
                )
            )
            report.mount(Button("Continue without the driver", id="skip-driver"))
        if outcome == "degraded":
            report.mount(
                Static("Continue without these features? Tick each one.", classes="banner warn")
            )
            for item in profile.get("disabled_features") or []:
                report.mount(
                    Checkbox(
                        f"{item.get('feature')}: {item.get('impact')} "
                        f"{item.get('remediation') or ''}".rstrip(),
                        value="accept_degraded" in self.wizard.consents,
                        classes="consent-feature",
                    )
                )
        if profile.get("tier") == "experimental":
            report.mount(
                Checkbox(
                    "Continue on the experimental tier (recorded in the fingerprint)",
                    value="accept_experimental" in self.wizard.consents,
                    classes="consent-experimental",
                )
            )
        if outcome == "needs_action":
            steps = "\n".join(f"• {a.get('action')}" for a in profile.get("needs_action") or [])
            report.mount(Static(f"Do this first, then Re-check:\n{steps}", classes="banner warn"))
        if outcome == "unsupported":
            reasons = "\n".join(f"• {r}" for r in profile.get("unsupported_reasons") or [])
            report.mount(Static(f"This host is not supported:\n{reasons}", classes="banner bad"))
            forced = "force_unsupported" in self.wizard.consents
            report.mount(
                Button(
                    "Forced (recorded in every fingerprint)" if forced else "Force unsupported…",
                    id="force",
                    variant="error",
                    disabled=forced,
                )
            )

    def save(self) -> Problems:  # noqa: D102
        profile = self.wizard.profile
        if not profile:
            return [("report", "preflight has not produced a host profile")]
        problems: Problems = []
        features = list(self.query(".consent-feature").results(Checkbox))
        if features:
            if all(c.value for c in features):
                self.wizard.consents.add("accept_degraded")
            else:
                self.wizard.consents.discard("accept_degraded")
                problems.append(("report", "tick every disabled feature to continue, or Abort"))
        experimental = list(self.query(".consent-experimental").results(Checkbox))
        if experimental:
            if experimental[0].value:
                self.wizard.consents.add("accept_experimental")
            else:
                problems.append(("report", "consent to the experimental tier to continue"))
        outcome = profile.get("outcome")
        if outcome == "needs_action":
            problems.append(("report", "follow the remediation, then Re-check"))
        if outcome == "unsupported" and "force_unsupported" not in self.wizard.consents:
            problems.append(("report", "this host is unsupported: Abort, or Force"))
        return problems

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        """Consent to the MOK enrollment (or take it back) and judge the host again.

        Args:
            event: The change.
        """
        consented = "accept_mok_enrollment" in self.wizard.consents
        if event.checkbox.id != "consent-mok" or event.value == consented:
            return  # also the change of a box mounted with the recorded value
        gpu = self.answers.gpu
        if event.value:
            self.wizard.consents.add("accept_mok_enrollment")
            self.wizard.nvidia_driver = gpu.install_driver = "auto"
        else:
            self.wizard.consents.discard("accept_mok_enrollment")
        gpu.accept_mok_enrollment = event.value
        self._recheck()

    def _recheck(self) -> None:
        self.wizard.profile = {}
        self.wizard.preflight_exit = None
        self.check()

    def _skip_driver(self) -> None:
        # continue without the NVIDIA driver (install_nvidia_driver: skip)
        self.wizard.consents.discard("accept_mok_enrollment")
        self.wizard.nvidia_driver = self.answers.gpu.install_driver = "skip"
        self.answers.gpu.accept_mok_enrollment = False
        self._recheck()

    def on_extra_button(self, event: Button.Pressed) -> None:  # noqa: D102
        if event.button.id == "recheck":
            self._recheck()
        elif event.button.id == "skip-driver":
            self._skip_driver()
        elif event.button.id == "raw":
            text = json.dumps(self.wizard.profile, indent=2)
            self.app.push_screen(TextView("Host profile (JSON)", text))
        elif event.button.id == "abort":
            # preflight's 10, 20 and 30 are bootstrap's codes for the same outcomes
            self.installer.quit_installer(self.wizard.preflight_exit or EXIT_OPERATOR)
        elif event.button.id == "force":
            self.app.push_screen(
                TypedConfirm(
                    "Force an unsupported host",
                    "The lab will be installed although preflight says this host is not "
                    "supported. Every environment fingerprint records it.",
                    FORCE_TEXT,
                ),
                self._forced,
            )

    def _forced(self, confirmed: bool | None) -> None:
        if confirmed:
            self.wizard.consents.add("force_unsupported")
            button = self.query_one("#force", Button)
            button.label = "Forced (recorded in every fingerprint)"
            button.disabled = True


class ModeScreen(StepScreen):
    """Screen 3: fresh install, restore from backup, or modify an existing install."""

    STEP = "mode"
    HELP = (
        "Fresh install sets up a new lab. Restore installs and then restores a restic "
        "snapshot (choose the repository on the backups screen). Modify changes an "
        "installed lab: every screen starts from its host_vars and answers, and the "
        "Review screen shows a diff of each file."
    )

    def body(self) -> ComposeResult:  # noqa: D102
        mode = self.answers.mode
        with RadioSet(id="mode"):
            yield RadioButton("Fresh install", value=mode == "fresh", id="mode-fresh")
            yield RadioButton("Restore from backup", value=mode == "restore", id="mode-restore")
            yield RadioButton(
                "Modify the existing installation",
                value=mode == "modify",
                id="mode-modify",
                disabled=not self.wizard.installed,
            )
        if not self.wizard.installed:
            yield Static("Modify is offered once a lab is installed on this host.", classes="hint")

    def save(self) -> Problems:  # noqa: D102
        for mode in ("fresh", "restore", "modify"):
            if self.query_one(f"#mode-{mode}", RadioButton).value:
                if mode == "modify" and self.answers.mode != "modify":
                    self._load_existing()
                self.answers.mode = mode
        return []

    def _load_existing(self) -> None:
        """Start from the installed machine's answers, host_vars and researchers."""
        machine = self.ctx.machine or self.ctx.host.installed_machine()
        if machine:
            self.wizard.answers = existing_answers(self.ctx.paths, machine)


def resume_choice(screen: WelcomeScreen) -> bool:
    """Whether the operator chose to resume (helper for tests).

    Args:
        screen: The welcome screen.

    Returns:
        True to resume.
    """
    found = screen.query("#resume-yes")
    return bool(found) and bool(screen.query_one("#resume-yes", RadioButton).value)
