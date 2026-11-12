"""Steps 1-3: Welcome, Preflight, Install mode."""

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
from pinka_installer.tui import capacity
from pinka_installer.tui.base import Problems, StepScreen
from pinka_installer.tui.host import Preflight
from pinka_installer.tui.model import existing_answers, page_label
from pinka_installer.tui.widgets import Card, NavBar, TextView, TypedConfirm

FORCE_TEXT = "FORCE UNSUPPORTED"
EXIT_OPERATOR = 77  # bootstrap/lib/exitcodes.sh
OUTCOME_STYLE = {
    "supported": "ok",
    "degraded": "warn",
    "needs_action": "warn",
    "unsupported": "bad",
}
# preflight's finding: the research space is a lab an uninstall kept, lies inside it or holds it
KEPT_LAB = "kept_lab"
KEPT_LAB_TEXT = (
    "This action alone does not hold Next here. If the uninstall that kept the lab did not "
    "finish, choose Uninstall on the next screen: it runs although the data is kept. To "
    "install, do the action above, or choose a research space on the Machine screen that "
    "is not the kept lab, does not lie inside it and does not hold it (symbolic links "
    "followed): the capacity plan and the Review screen check the research space you choose."
)

# Preflight's exit codes whose host profile bootstrap's uninstall goes on with.
UNINSTALL_EXITS = (0, 10, 20, 30)
INSTALLED_TEXT = (
    "A lab is installed on this host. Uninstall runs whatever preflight's outcome here, "
    "without Force or a consent, and no override is recorded for it: Next goes on to the "
    "Install mode screen, where you can choose it. An install, a restore or a modification "
    "waits there while this screen holds it."
)


def only_kept_lab(profile: dict[str, Any]) -> bool:
    """Whether the kept lab is the only action preflight asks for.

    Uninstall runs on such a host (bootstrap's uninstall takes any preflight
    outcome), and an install is checked again on the capacity plan, with the
    research space chosen on the Machine screen.

    Args:
        profile: Preflight's host profile.

    Returns:
        True when every action is ``kept_lab``.
    """
    actions = profile.get("needs_action") or []
    return bool(actions) and all(a.get("id") == KEPT_LAB for a in actions)


class WelcomeScreen(StepScreen):
    """Step 1: the checkout, the log, the keys, and resuming a previous install."""

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
        dirty = " [b]with uncommitted changes[/b]" if git.dirty else ""
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
                    f"Resume ({page_label(self.saved.screen)}, started at {self.saved.started_at})",
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
    """Step 2: preflight with a live spinner, its cards, the outcome and the consents."""

    STEP = "preflight"
    HELP = (
        "Preflight detects the environment, the OS, hardware, GPU, virtualization, "
        "storage, network and conflicts, and decides whether the lab is supported here. "
        "Degraded hosts need your consent per disabled feature; experimental tiers need "
        "consent; needs_action lists what to do first, and Next waits until it is done, on "
        "a forced host too, since forcing does not skip it; when the only action is the lab "
        "an earlier uninstall kept, you can go on to Uninstall, and an install is checked on "
        "the capacity plan; unsupported hosts can only be "
        "forced by typing FORCE UNSUPPORTED, which is recorded in every fingerprint. "
        "On a host with a lab installed, Next goes on to the Install mode screen unless "
        "preflight failed: Uninstall runs whatever preflight's outcome, and an install, a "
        "restore or a modification waits there until this screen no longer holds it. "
        "With Secure Boot on and no NVIDIA driver, the installation can install the "
        "driver and import a Machine Owner Key, with your consent: you choose a one-time "
        "password on the capacity plan's NVIDIA driver page and enroll the key at the blue "
        "MOK Manager screen during the next boot."
    )
    MOK_TEXT = (
        "Secure Boot is on and no NVIDIA driver is installed. The installation can install "
        "the pinned driver and import a Machine Owner Key (MOK) that signs it: you choose a "
        "one-time password on the capacity plan's NVIDIA driver page, the installation stops "
        "for a reboot, and during the boot you enroll the key at the blue MOK Manager screen "
        "(you need the console then). Or continue without the driver: no local inference on "
        "this host."
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
            # A box starts ticked only for a line the operator accepted already (here or
            # on the capacity plan), not for the bare flag a resume restores.
            for item in profile.get("disabled_features") or []:
                line = (
                    f"{item.get('feature')}: {item.get('impact')} "
                    f"{item.get('remediation') or ''}".rstrip()
                )
                report.mount(
                    Checkbox(
                        line,
                        value=line in self.wizard.degraded_accepted,
                        classes="consent-feature",
                    )
                )
            for reason in profile.get("degraded_reasons") or []:
                report.mount(
                    Checkbox(
                        str(reason),
                        value=str(reason) in self.wizard.degraded_accepted,
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
        actions = profile.get("needs_action") or []
        if actions:
            # Also on an unsupported host: forcing it does not skip an action, as in bootstrap.
            steps = "\n".join(f"• {a.get('action')}" for a in actions)
            kept = only_kept_lab(profile)
            first = "To install, do this first" if kept else "Do this first"
            report.mount(Static(f"{first}, then Re-check:\n{steps}", classes="banner warn"))
            if kept:
                report.mount(Static(KEPT_LAB_TEXT, classes="banner warn", id="kept-lab"))
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
        # Whatever could hold an install here (a consent or Force may still lift it); the
        # kept lab alone has its own banner above.
        experimental = profile.get("tier") == "experimental"
        kept_alone = only_kept_lab(profile) and outcome == "needs_action" and not experimental
        holds = (outcome != "supported" or experimental or bool(actions)) and not kept_alone
        if holds and self._uninstall_reachable():
            report.mount(Static(INSTALLED_TEXT, classes="banner warn", id="installed"))

    def save(self) -> Problems:  # noqa: D102
        profile = self.wizard.profile
        if not profile:
            return [("report", "preflight has not produced a host profile")]
        problems: Problems = []
        features = list(self.query(".consent-feature").results(Checkbox))
        if features:
            # What the boxes show, as the capacity plan words it: it asks only for more.
            shown = set(capacity.degraded(profile))
            if all(c.value for c in features):
                self.wizard.consents.add("accept_degraded")
                self.wizard.degraded_accepted |= shown
            else:
                self.wizard.consents.discard("accept_degraded")
                self.wizard.degraded_accepted -= shown
                problems.append(("report", "tick every disabled feature to continue, or Abort"))
        experimental = list(self.query(".consent-experimental").results(Checkbox))
        if experimental:
            if experimental[0].value:
                self.wizard.consents.add("accept_experimental")
            else:
                problems.append(("report", "consent to the experimental tier to continue"))
        outcome = profile.get("outcome")
        if outcome == "unsupported" and "force_unsupported" not in self.wizard.consents:
            problems.append(("report", "this host is unsupported: Abort, or Force"))
        # An action stops here as it stops bootstrap, on a forced host too; the kept lab
        # alone does not: Uninstall runs with it, and the capacity plan checks an install.
        if (profile.get("needs_action") or outcome == "needs_action") and not only_kept_lab(
            profile
        ):
            forced = outcome == "unsupported" and "force_unsupported" in self.wizard.consents
            problems.append(
                (
                    "report",
                    "follow the remediation, then Re-check"
                    + (": forcing the host does not skip it" if forced else ""),
                )
            )
        if problems and self._uninstall_reachable():
            # Bootstrap's uninstall takes any of these outcomes and excludes every
            # override: the Install mode screen offers it, and holds the rest.
            self.wizard.install_held = [message for _field, message in problems]
            return []
        self.wizard.install_held = []
        return problems

    def _uninstall_reachable(self) -> bool:
        """Whether a lab is installed and bootstrap's uninstall goes on with this outcome."""
        return self.wizard.installed and self.wizard.preflight_exit in UNINSTALL_EXITS

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
    """Step 3: fresh install, restore from backup, modify an existing install, or uninstall."""

    STEP = "mode"
    HELP = (
        "Fresh install sets up a new lab. Restore installs and then restores a restic "
        "snapshot (choose the repository on the backups screen). Modify changes an "
        "installed lab: every screen starts from its host_vars and answers, and the "
        "Review screen shows a diff of each file. Uninstall removes what pinka installed "
        "and keeps the lab's data: it shows the plan first, offers a backup and asks for "
        "the machine name. It runs whatever preflight finds: while the Preflight screen "
        "holds an install (an unsupported host not forced, an action, a consent not given), "
        "only Uninstall goes on from here."
    )

    def body(self) -> ComposeResult:  # noqa: D102
        mode = self.answers.mode
        uninstall = self.wizard.uninstall
        with RadioSet(id="mode"):
            yield RadioButton(
                "Fresh install", value=mode == "fresh" and not uninstall, id="mode-fresh"
            )
            yield RadioButton(
                "Restore from backup",
                value=mode == "restore" and not uninstall,
                id="mode-restore",
            )
            yield RadioButton(
                "Modify the existing installation",
                value=mode == "modify" and not uninstall,
                id="mode-modify",
                disabled=not self.wizard.installed,
            )
            yield RadioButton(
                "Uninstall pinka from this host",
                value=uninstall,
                id="mode-uninstall",
                disabled=not self.wizard.installed,
            )
        if not self.wizard.installed:
            yield Static(
                "Modify and Uninstall are offered once a lab is installed on this host.",
                classes="hint",
            )
        if self.wizard.install_held:
            # Without a lab (removed since a resumed state was saved), only Back is left.
            first = "Uninstall runs on this host. " if self.wizard.installed else ""
            yield Static(
                first + "An install, a restore or a modification waits until the Preflight "
                "screen no longer holds it: " + "; ".join(self.wizard.install_held),
                classes="banner warn",
                id="install-held",
                markup=False,
            )

    def save(self) -> Problems:  # noqa: D102
        uninstall = self.query_one("#mode-uninstall", RadioButton).value
        if not uninstall and self.wizard.install_held:
            way = "choose Uninstall, or go" if self.wizard.installed else "go"
            return [
                (
                    "mode",
                    f"{way} Back to the Preflight screen: " + "; ".join(self.wizard.install_held),
                )
            ]
        self.wizard.uninstall = uninstall
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
