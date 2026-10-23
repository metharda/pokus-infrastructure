"""Uninstall from the installer (BUILD_PROMPT §5.4, §18.4 screen 3).

The Install mode screen offers Uninstall when a lab is installed. These two
screens show uninstall's own dry run, offer a backup when a restic
repository is configured, ask for the machine name (a second time to delete
the lab's data too), then run the same command as ``bootstrap.sh
--uninstall`` and show its report. The TUI decides nothing itself: the plan,
the backup and the removal are bootstrap's. ``/opt/pinka``, where the TUI
runs from, goes when the TUI has ended.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from textual import work
from textual.app import ComposeResult
from textual.widgets import Checkbox, Input, RichLog, Static

from pinka_installer import validators
from pinka_installer.tui.base import Problems, StepScreen
from pinka_installer.tui.runner import UninstallRun
from pinka_installer.tui.widgets import Field, NavBar

# Bootstrap's exit codes of uninstall (bootstrap/lib/exitcodes.sh).
UNINSTALL_EXITS = {
    0: "Uninstall finished.",
    80: "Uninstall was refused or not confirmed; nothing was removed.",
    81: "Uninstall finished, but kept items it could not remove; the report names each.",
}


class UninstallScreen(StepScreen):
    """The dry run, the backup offer and the typed confirmations."""

    STEP = "uninstall"
    HELP = (
        "Uninstall removes what pinka installed and keeps the lab's data (/srv/pinka, the "
        "database files, backups and models) and everything that was on the host before "
        "pinka. The plan comes from bootstrap's own dry run. Type the machine name to "
        "confirm; deleting the lab's data too needs it a second time. Nothing is removed "
        "before you press Uninstall."
    )
    NEXT_LABEL = "Uninstall"

    def __init__(self) -> None:
        """Build the screen; the plan comes from a dry run when it is shown."""
        super().__init__()
        self.plan: dict[str, Any] = {}

    def nav(self) -> NavBar:  # noqa: D102
        return NavBar(self.NEXT_LABEL, back=True)

    def body(self) -> ComposeResult:  # noqa: D102
        yield Static("Working out what uninstall removes (a dry run)…", id="uninstall-status")
        yield Checkbox(
            "Back up the lab first (pinka backup now)",
            value=True,
            id="backup",
        )
        yield Field(
            "Type the machine name to remove pinka from this host",
            Input(id="confirm"),
            field_id="confirm",
        )
        yield Checkbox("Also delete the lab's data (--purge-data)", id="purge")
        yield Field(
            "Type the machine name again to delete the lab's data",
            Input(id="confirm-data"),
            field_id="confirm-data",
        )
        # Below the fields, so they stay in view on a small terminal.
        yield Static("", id="plan", classes="code", markup=False)

    def on_mount(self) -> None:  # noqa: D102
        self.query_one("#next").disabled = True
        self.query_one("#backup", Checkbox).display = False
        self.query_one("#field-confirm-data", Field).display = False
        self.dry_run()

    @work(thread=True, exclusive=True)
    def dry_run(self) -> None:
        """Ask bootstrap for the plan (``--dry-run``), in the background."""
        run = self.ctx.runner.uninstall(["--dry-run"], lambda _line: None)
        self.app.call_from_thread(self._planned, run)

    def _planned(self, run: UninstallRun) -> None:
        self.plan = run.result
        status = self.query_one("#uninstall-status", Static)
        if not self.plan.get("items"):
            status.update(f"The dry run gave no plan (exit code {run.code}); see the log.")
            return
        self.query_one("#plan", Static).update(str(self.plan.get("text", "")))
        refusal = str(self.plan.get("refusal") or "")
        if refusal:
            status.update(f"Uninstall is refused here: {refusal}")
            return
        status.update(
            f"Machine {self.machine()}: {self.plan.get('to_remove', 0)} items to remove or "
            "restore. Nothing is removed before you press Uninstall."
        )
        self.query_one("#backup", Checkbox).display = bool(self.plan.get("backup"))
        self.query_one("#next").disabled = False

    def machine(self) -> str:
        """The machine name the plan is for."""
        return str(self.plan.get("machine") or self.ctx.machine)

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:  # noqa: D102
        if event.checkbox.id == "purge":
            self.query_one("#field-confirm-data", Field).display = event.value

    def save(self) -> Problems:  # noqa: D102
        problems: Problems = []
        machine = self.machine()
        typed = self.query_one("#confirm", Input).value
        problem = validators.uninstall_confirmation(typed, machine)
        if problem:
            problems.append(("confirm", problem))
        purge = self.query_one("#purge", Checkbox).value
        if purge:
            problem = validators.uninstall_confirmation(
                self.query_one("#confirm-data", Input).value, machine
            )
            if problem:
                problems.append(("confirm-data", problem))
        options = ["--confirm-machine", typed]
        if purge:
            options.append("--purge-data")
        backup = self.query_one("#backup", Checkbox)
        if not (backup.display and backup.value):
            options.append("--skip-backup")
        self.wizard.uninstall_options = options
        return problems

    def proceed(self) -> None:  # noqa: D102
        self.installer.goto("uninstall_run")


class UninstallRunScreen(StepScreen):
    """The removal and its report."""

    STEP = "uninstall_run"
    HELP = (
        "Bootstrap removes the plan you confirmed, phase by phase, and writes a report in "
        "/var/log/pinka-uninstall. Items it could not remove are named with the reason; "
        "running uninstall again continues where it stopped. /opt/pinka, where this "
        "installer runs from, goes when you close it."
    )

    def __init__(self) -> None:
        """Build the screen."""
        super().__init__()
        self.exit_code: int | None = None

    def nav(self) -> NavBar:  # noqa: D102
        return NavBar("Close", back=False)

    def body(self) -> ComposeResult:  # noqa: D102
        yield Static("Uninstalling…", id="uninstall-status")
        yield Static("", id="report", classes="code", markup=False)
        yield RichLog(id="output", max_lines=2000, markup=False)

    def on_mount(self) -> None:  # noqa: D102
        self.query_one("#next").disabled = True
        self.run_uninstall()

    @work(thread=True, exclusive=True)
    def run_uninstall(self) -> None:
        """Run ``bootstrap.sh --uninstall`` with the confirmed options, in the background."""
        run = self.ctx.runner.uninstall(
            self.wizard.uninstall_options,
            lambda line: self.app.call_from_thread(self._output, line),
        )
        self.app.call_from_thread(self._finished, run)

    def _output(self, line: str) -> None:
        if line.strip():
            self.query_one("#output", RichLog).write(line)

    def _finished(self, run: UninstallRun) -> None:
        self.exit_code = run.code
        self.installer.result_code = run.code
        self.query_one("#uninstall-status", Static).update(
            UNINSTALL_EXITS.get(
                run.code, f"Uninstall ended with exit code {run.code}; see the log."
            )
        )
        self.query_one("#report", Static).update(report_text(run.result))
        self.query_one("#next").disabled = False

    def proceed(self) -> None:  # noqa: D102
        if self.exit_code is not None:  # Close only once bootstrap has ended
            self.app.exit(self.exit_code)


def report_text(report: dict[str, Any]) -> str:
    """The report's summary: the counts, what failed, what is left to do, what was kept.

    Args:
        report: The report (or the plan, when nothing was removed).

    Returns:
        Text for people.
    """
    items = [i for i in report.get("items", []) if isinstance(i, dict)]
    if not items or "result" not in items[0]:
        return str(report.get("text", ""))
    counts = Counter(str(i.get("result")) for i in items)
    lines = [", ".join(f"{n} {result}" for result, n in sorted(counts.items()))]

    def listed(title: str, result: str) -> list[str]:
        named = [i for i in items if i.get("result") == result]
        return (
            ["", title, *(f"  {i.get('name')}: {i.get('reason')}" for i in named)] if named else []
        )

    lines += listed("Could not remove:", "failed")
    if report.get("reboot_needed"):
        lines += ["", "Reboot the host: uninstall never reboots."]
        lines += [f"  {reason}" for reason in report.get("reboot_reasons") or []]
    if report.get("follow_ups"):
        lines += ["", "Afterwards:"]
        lines += [f"  - {step}" for step in report["follow_ups"]]
    if report.get("backup_access"):
        lines += [
            "",
            f"The kept backups open with the key and settings in {report['backup_access']} "
            f"(root only; {report.get('kept_dir', '')}/README.txt says how).",
        ]
    if counts.get("pending"):
        lines += ["", "/opt/pinka goes when you close the installer, which runs from it."]
    lines += listed("Kept:", "kept")
    return "\n".join(lines)
