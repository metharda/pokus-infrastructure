"""Snapshots of every installer screen at 80x24 and 120x40, dark and light (BUILD_PROMPT §18.9).

The screens show a WSL2 host with a GPU (preflight's own fixture) and a
filled-in wizard. Update the snapshots after a deliberate change with
``pytest installer/tests/test_tui_snapshots.py --snapshot-update`` and review
every changed SVG.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from textual.pilot import Pilot
from tui_fakes import FakeHost, FakeRunner, app, context, preflight

from pokus_installer import answers as answers_mod
from pokus_installer.paths import Paths
from pokus_installer.tui.app import SCREENS, InstallerApp
from pokus_installer.tui.screens_install import SignalScreen

PASSWORD = "correct horse battery staple 42"  # noqa: S105 - test fixture
SIZES = {"80x24": (80, 24), "120x40": (120, 40)}
THEMES = {"dark": "textual-dark", "light": "textual-light"}
SCREEN_NAMES = [*SCREENS, "signal"]


def filled(repo: Paths, screen: str, theme: str) -> InstallerApp:
    """The application on one screen, with the wizard filled in."""
    host = FakeHost(preflight("ubuntu2404-wsl2-gpu-kvm"))
    ctx = context(repo, host, FakeRunner([("events-success.jsonl", 1)]), machine="pokus-lab")
    wizard = ctx.wizard
    result = host.preflight([])
    wizard.profile, wizard.preflight_exit = result.profile, result.exit_code
    wizard.vault_password = PASSWORD
    answers = wizard.answers
    answers.machine.name = "pokus-lab"
    answers.resources.expected_memory_gib = 32
    answers.resources.expected_cpu_threads = 24
    answers.resources.cpuset_services = "0-7"
    answers.resources.cpuset_targets = "8-23"
    answers.researchers = [
        answers_mod.Researcher("researcher1", True, "Researcher One"),
        answers_mod.Researcher("researcher2", False, "Researcher Two", False),
    ]
    answers.backups.repository = answers_mod.Repository(
        "sftp", "backup.example.com", "backup", "/srv/restic/pokus-lab"
    )
    if screen == "restore":
        answers.mode = "restore"
    first = "welcome" if screen == "signal" else screen
    return app(ctx, first=first, theme=theme)


async def settle(pilot: Pilot[Any]) -> None:
    for _ in range(3):
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()


def before(screen: str) -> Callable[[Pilot[Any]], Any]:
    async def run(pilot: Pilot[Any]) -> None:
        await settle(pilot)
        if screen == "signal":
            message = "WSL must restart to apply /etc/wsl.conf"
            installer = pilot.app
            assert isinstance(installer, InstallerApp)
            installer.show(SignalScreen(75, message))
            await settle(pilot)

    return run


CASES = [
    f"{screen}-{size}-{theme}" for screen in SCREEN_NAMES for size in SIZES for theme in THEMES
]


@pytest.mark.parametrize("case", CASES)
def test_screen(case: str, repo: Paths, snap_compare: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    screen, size, theme = case.split("-")
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    installer = filled(repo, screen, THEMES[theme])
    assert snap_compare(installer, terminal_size=SIZES[size], run_before=before(screen))
