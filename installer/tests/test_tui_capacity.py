"""The capacity plan (step 5): it shows preflight's answer and decides nothing itself.

The flows run the real application with a fake host; where the host runs
preflight on one of its fixtures, the answer is the real preflight's for the
plan the screen sent.
"""

from __future__ import annotations

import asyncio
import copy
import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import pytest
from ruamel.yaml import YAML
from textual.pilot import Pilot
from textual.widgets import Checkbox, Collapsible, DataTable, Input, RadioButton, Select, Static
from tui_fakes import FIXTURES, FakeHost, app, context, preflight

from pinka_installer import answers as answers_mod
from pinka_installer import hostvars, state, validators
from pinka_installer import plan as plan_mod
from pinka_installer.answers import Answers
from pinka_installer.paths import Paths
from pinka_installer.tui import capacity
from pinka_installer.tui import host as host_mod
from pinka_installer.tui.app import InstallerApp
from pinka_installer.tui.host import Preflight
from pinka_installer.tui.model import DRIVER_MODES
from pinka_installer.tui.screens_capacity import DEBOUNCE, NVIDIA_DRIVER, CapacityScreen
from pinka_installer.tui.screens_install import ReviewScreen
from pinka_preflight import matrix, profile_tool, render, requirements

SIZE = (120, 40)
FITS = "ubuntu2404-wsl2-gpu-kvm"
DEGRADED = "ubuntu2404-memory-below-recommended"
SHORT = "ubuntu2404-disk-shortfall"
NEEDS_ACTION = "ubuntu2404-baremetal-docker-cdi-off"


def run(installer: InstallerApp, flow: Any) -> None:
    async def main() -> None:
        async with installer.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await flow(pilot)
            await settle(pilot)

    asyncio.run(main())


async def settle(pilot: Pilot[Any]) -> None:
    for _ in range(3):
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()


def capacity_debounce() -> float:
    """Long enough for the pause after a change to end and its run to start."""
    return DEBOUNCE + 0.2


def screen_of(pilot: Pilot[Any]) -> CapacityScreen:
    return cast(CapacityScreen, pilot.app.screen)


def status(pilot: Pilot[Any]) -> str:
    return str(pilot.app.screen.query_one("#cap-status", Static).content)


def on_capacity(repo: Paths, host: FakeHost) -> InstallerApp:
    installer = app(context(repo, host, machine="pinka-lab"), first="capacity")
    installer.ctx.wizard.profile = copy.deepcopy(host.result.profile)
    installer.ctx.wizard.answers.machine.name = "pinka-lab"
    return installer


@dataclass
class SlowFirstHost(FakeHost):
    """A host whose first preflight run answers only after the second one."""

    stale: dict[str, Any] = field(default_factory=dict)
    release_first: threading.Event = field(default_factory=threading.Event)

    def preflight(self, args: Any) -> Preflight:
        number = self.preflight_calls + 1
        result = super().preflight(args)
        if number == 1:
            assert self.release_first.wait(30)
            return Preflight(1, copy.deepcopy(self.stale))
        return result


def calls_of(monkeypatch: pytest.MonkeyPatch, screen: type[Any], name: str) -> list[bool]:
    """Record each call of a screen's method that shows an answer: whether it was left."""
    calls: list[bool] = []
    original = getattr(screen, name)

    def recorded(self: Any, *args: Any) -> None:
        calls.append(self.left)
        original(self, *args)

    monkeypatch.setattr(screen, name, recorded)
    return calls


async def after_the_first_run(pilot: Pilot[Any], host: SlowFirstHost, calls: list[bool]) -> None:
    """Let the slow first run end, and wait until its answer reached the screen that is gone.

    A cancelled worker is not waited for: its call comes after the worker counts as done.
    """
    host.release_first.set()
    for _ in range(500):
        await pilot.pause(0.02)
        if any(calls):
            return


def test_an_older_run_finishing_last_is_dropped(repo: Paths) -> None:
    # Run 1 (a host whose disks do not fit) answers after run 2 (one that fits):
    # the bars and Next follow run 2, the answer to the current choices.
    fits = preflight(FITS)
    host = SlowFirstHost(fits, stale=preflight(SHORT).profile)
    installer = on_capacity(repo, host)
    seen: dict[str, Any] = {}

    async def flow(pilot: Pilot[int]) -> None:
        screen = screen_of(pilot)
        screen.query_one("#targets").value = "3"  # type: ignore[attr-defined]
        screen.recompute(now=True)  # run 2 starts while run 1 still waits
        for _ in range(200):
            await pilot.pause(0.05)
            if screen.shown == screen.sequence:
                break
        seen["after run 2"] = (screen.shown, screen.sequence)
        host.release_first.set()  # run 1 ends last
        await settle(pilot)
        seen["after run 1"] = (screen.shown, screen.sequence)
        seen["capacity"] = installer.ctx.wizard.capacity
        seen["red"] = [r.label for r in screen.table if r.status == "does_not_fit"]
        await pilot.press("ctrl+n")
        await settle(pilot)
        seen["at"] = installer.current

    run(installer, flow)
    assert host.preflight_calls == 2
    assert seen["after run 2"] == (2, 2)
    assert seen["after run 1"] == (2, 2)  # run 1's answer was dropped
    assert seen["capacity"] == fits.profile
    assert seen["red"] == []
    assert seen["at"] == "capacity_device"


def test_red_blocks_next(repo: Paths) -> None:
    installer = on_capacity(repo, FakeHost(preflight(SHORT)))
    seen: dict[str, Any] = {}

    async def flow(pilot: Pilot[int]) -> None:
        await settle(pilot)
        await pilot.press("ctrl+n")
        await settle(pilot)
        seen["at"] = installer.current
        seen["status"] = status(pilot)
        seen["reasons"] = str(pilot.app.screen.query_one("#cap-reasons", Static).content)

    run(installer, flow)
    assert seen["at"] == "capacity"
    assert seen["status"] == "Next waits: does not fit: Disk /, Disk /srv (see below)"
    profile = preflight(SHORT).profile
    for reason in profile["unsupported_reasons"]:
        assert f"- {reason}" in seen["reasons"]  # preflight's words, unchanged


def test_yellow_needs_the_consent(repo: Paths) -> None:
    installer = on_capacity(repo, FakeHost(preflight(DEGRADED)))
    installer.ctx.wizard.consents.discard("accept_degraded")
    seen: dict[str, Any] = {}

    async def flow(pilot: Pilot[int]) -> None:
        await settle(pilot)
        consent = pilot.app.screen.query_one("#cap-consent", Checkbox)
        seen["shown"] = consent.display
        await pilot.press("ctrl+n")
        await settle(pilot)
        seen["without"] = installer.current
        consent.value = True
        await pilot.press("ctrl+n")
        await settle(pilot)
        seen["with"] = installer.current

    run(installer, flow)
    assert seen["shown"] is True
    assert seen["without"] == "capacity"
    assert seen["with"] == "capacity_device"
    assert "accept_degraded" in installer.ctx.wizard.consents


def test_a_refused_plan_blocks_next(repo: Paths) -> None:
    # The plan writer's own text, as bootstrap would print it.
    installer = on_capacity(repo, FakeHost(preflight(FITS)))
    installer.ctx.wizard.answers.machine.node_role = "inference"
    installer.ctx.wizard.answers.features.inference = "off"
    seen: dict[str, Any] = {}

    async def flow(pilot: Pilot[int]) -> None:
        await settle(pilot)
        await pilot.press("ctrl+n")
        await settle(pilot)
        seen["at"] = installer.current
        seen["status"] = status(pilot)

    run(installer, flow)
    assert seen["at"] == "capacity"
    assert seen["status"] == (
        "Next waits: the plan is refused: an inference node runs only the inference stack; "
        "it needs inference: auto or on"
    )


@pytest.mark.parametrize("existing", [False, True], ids=["fresh", "modify"])
def test_the_plan_sent_is_bootstraps(repo: Paths, existing: bool) -> None:
    # The plan the screen sends equals the plan bootstrap writes from the host_vars
    # the same answers produce; preflight gets it with --plan and --offline. In modify
    # mode the file keeps the settings no screen asks for (here a hand-set exporter).
    host = FakeHost(preflight(FITS), fixture=FITS)
    installer = on_capacity(repo, host)
    example = repo.example_host_vars.read_text(encoding="utf-8")
    current: str | None = None
    if existing:
        current = hostvars.render(installer.ctx.wizard.answers, None, example)
        current += "observability_gpu_exporter: dcgm\n"
        repo.host_vars("pinka-lab").write_text(current, encoding="utf-8")

    async def flow(pilot: Pilot[int]) -> None:
        await settle(pilot)
        screen = screen_of(pilot)
        screen.query_one("#targets").value = "4"  # type: ignore[attr-defined]
        screen.query_one("#ret-loki").value = "720h"  # type: ignore[attr-defined]
        await pilot.press("ctrl+n")
        await settle(pilot)

    run(installer, flow)
    assert installer.current == "capacity_device"
    sent = host.plans[-1]
    assert (sent["concurrent_targets"], sent["retention"]["loki_days"]) == (4, 30)
    assert sent["gpu_exporter"] == ("dcgm" if existing else "nvidia_smi")
    args = host.preflight_args[-1]
    assert args[0] == "--plan" and "--offline" in args
    # The host_vars the installer writes for these answers, and bootstrap's plan of them.
    text = hostvars.render(installer.ctx.wizard.answers, current, example)
    repo.host_vars("pinka-lab").write_text(text, encoding="utf-8")
    assert plan_mod.machine_plan(repo, "pinka-lab") == sent


SUMMARY_SECTIONS = ("Unsupported", "Degraded", "Action needed", "Disabled features")


def summary_reasons(profile: dict[str, Any]) -> list[str]:
    """The lines of the summary's reason sections, as bootstrap's preflight stage logs them."""
    found: list[str] = []
    section = ""
    for line in render.render(profile).splitlines():
        if not line.startswith(" "):
            section = line.strip()
        elif section in SUMMARY_SECTIONS and line.startswith("  - "):
            found.append(line[4:])
    return found


@pytest.mark.parametrize(
    "fixture",
    [
        FITS,
        DEGRADED,
        SHORT,
        "ubuntu2404-inference-auto-small-gpu",
        NEEDS_ACTION,
    ],
)
def test_every_reason_and_item_of_preflights_summary_is_shown(
    repo: Paths, tmp_path: Path, fixture: str
) -> None:
    # Preflight runs on its fixture with the plan the screen sent: every line of the
    # summary's reason sections, from a run of its own on that plan, is on the screen
    # word for word, and every item behind the bars.
    host = FakeHost(preflight(fixture), fixture=fixture)
    installer = on_capacity(repo, host)
    texts: list[str] = []

    async def flow(pilot: Pilot[int]) -> None:
        await settle(pilot)
        screen = pilot.app.screen
        texts.extend(str(c.title) for c in screen.query(Collapsible))
        texts.extend(str(s.content) for s in screen.query("Collapsible Static").results(Static))
        texts.append(str(screen.query_one("#cap-reasons", Static).content))

    run(installer, flow)
    shown = "\n".join(texts)
    assert host.plans, "the screen sent no plan"
    sent = tmp_path / "plan.json"
    sent.write_text(json.dumps(host.plans[-1]), encoding="utf-8")
    args = host.preflight_args[-1][3:]  # after --plan <file> --offline: the driver options
    profile = preflight(fixture, ["--plan", str(sent), *args]).profile
    reasons = summary_reasons(profile)
    for reason in reasons:
        assert f"- {reason}" in shown, (fixture, reason)
    if fixture != FITS:
        assert reasons, fixture
    req = profile["requirements"]
    titles = [i["title"] for i in req["memory_gib"]["items"]]
    titles += [i["title"] for m in req["disk"] for i in m["items"]]
    for use in ("serving", "training"):
        titles += [i["title"] for i in (req["budgets"]["vram"].get(use) or {}).get("items", [])]
    for title in titles:
        assert title in shown, (fixture, title)


@pytest.mark.parametrize("old", ["resources", "storage", "gpu"])
def test_a_resume_at_a_replaced_screen_goes_on_at_the_capacity_plan(repo: Paths, old: str) -> None:
    # The Welcome screen offers to resume at the page that replaced the saved one.
    answers = Answers()
    answers.machine.name = "pinka-lab"
    saved = state.State(screen=old, answers={"answers": answers.to_dict(), "consents": []})
    state.save(saved, repo.state_file)
    installer = app(context(repo, FakeHost(preflight(FITS)), machine="pinka-lab"))
    seen: dict[str, Any] = {}

    async def flow(pilot: Pilot[int]) -> None:
        await settle(pilot)
        seen["resume"] = str(pilot.app.screen.query_one("#resume-yes", RadioButton).label)
        await pilot.press("ctrl+n")
        await settle(pilot)

    run(installer, flow)
    assert seen["resume"].startswith("Resume (step 5 · Capacity plan, started at ")
    assert installer.current == "capacity", old


@pytest.mark.parametrize(
    ("fixture", "rows"),
    [
        (
            FITS,
            [
                ("RAM", "fits", "fits · needs 8.0-37.2 GiB (compared at 0.94), has 64.0 GiB"),
                ("Disk /", "fits", "fits · needs 148.3 GiB, has 400.0 GiB free"),
                ("Disk /srv", "fits", "fits · needs 3.7 GiB, has 500.0 GiB free"),
                ("VRAM serving", "fits", "fits · needs 11500 MiB, has 12282 MiB"),
                ("VRAM training", "fits", "fits · needs 8000 MiB, has 12282 MiB"),
            ],
        ),
        (
            SHORT,
            [
                ("RAM", "fits", "fits · needs 15.8-37.2 GiB (compared at 0.94), has 64.0 GiB"),
                (
                    "Disk /",
                    "does_not_fit",
                    "does not fit, 282.3 GiB short · needs 382.3 GiB, has 100.0 GiB free",
                ),
                (
                    "Disk /srv",
                    "does_not_fit",
                    "does not fit, 6.4 GiB short · needs 9.4 GiB, has 3.0 GiB free",
                ),
                ("VRAM serving", "fits", "fits · needs 11500 MiB, has 12282 MiB"),
                ("VRAM training", "fits", "fits · needs 8000 MiB, has 12282 MiB"),
            ],
        ),
        (
            DEGRADED,
            [
                (
                    "RAM",
                    "degraded",
                    "degraded, 0.1 GiB short · needs 8.0-17.2 GiB (compared at 0.94), has 16.1 GiB",
                ),
                ("Disk /", "fits", "fits · needs 148.3 GiB, has 400.0 GiB free"),
                ("Disk /srv", "fits", "fits · needs 3.7 GiB, has 500.0 GiB free"),
                ("VRAM serving", "fits", "fits · needs 11500 MiB, has 12282 MiB"),
                ("VRAM training", "fits", "fits · needs 8000 MiB, has 12282 MiB"),
            ],
        ),
    ],
)
def test_the_rows_read_preflights_numbers(fixture: str, rows: list[tuple[str, ...]]) -> None:
    profile = preflight(fixture, ["--plan", plan_file(fixture)] if fixture == SHORT else []).profile
    table = capacity.rows(profile)
    assert [(r.label, r.status, r.line) for r in table] == rows
    assert table[0].items[0] == "each need is compared at 0.94 times its nominal value (configured)"
    answer = capacity.Answer(profile)
    assert (
        capacity.waits(answer, set(), set())
        == {
            FITS: "",
            SHORT: "does not fit: Disk /, Disk /srv",
            DEGRADED: "what is degraded needs your consent",
        }[fixture]
    )
    lines = set(capacity.degraded(profile))
    assert capacity.waits(answer, {"accept_degraded"}, lines) == (
        "does not fit: Disk /, Disk /srv" if fixture == SHORT else ""
    )
    if fixture == DEGRADED:
        # The flag alone, without its lines (a resumed wizard), is no consent.
        assert capacity.waits(answer, {"accept_degraded"}, set()) == (
            "what is degraded needs your consent"
        )


def plan_file(fixture: str) -> str:
    return str(Path(__file__).parents[2] / "preflight/tests/fixtures" / fixture / "plan.json")


def test_rows_bars_growth_and_consent() -> None:
    row = capacity.Row("memory", "RAM", "fits", 0.5, "", ())
    assert row.bar() == "█████░░░░░"
    assert capacity.Row("m", "RAM", "fits", 2.0, "", ()).bar() == "█" * capacity.BAR_CELLS
    assert capacity.Row("m", "RAM", "fits", 0.0, "", ()).bar() == "░" * capacity.BAR_CELLS
    assert capacity.up(0.01) == 0.1 and capacity.up(282.29) == 282.3 and capacity.up(1.0) == 1.0
    growth = {
        "mount": "/srv",
        "gib_per_day": 0.27,
        "gib_per_week": 1.89,
        "days_until_full": 28,
        "free_after_install_gib": 6.3,
        "items": [],
    }
    profile = {"generated_at": "2026-10-05T09:12:44Z", "requirements": {"growth": growth}}
    assert capacity.growth(profile) == (
        "Growth on /srv: 0.27 GiB a day, 1.89 GiB a week (estimated); full in about 28 days "
        "at this rate, around 2026-11-02"
    )
    assert capacity.growth(profile).endswith(f"; {render.growth_when(growth)}, around 2026-11-02")
    growth["days_until_full"] = None
    assert capacity.growth(profile).endswith(
        "; the disk does not fill within ten years, the retention's limits counted"
    )
    # Preflight's summary takes the same words for each case of a null date.
    assert capacity.growth(profile).endswith(f"; {render.growth_when(growth)}")
    growth["free_after_install_gib"] = None
    assert capacity.growth(profile).endswith(
        "; when the disk fills is not known: its free space is not measured"
    )
    assert capacity.growth(profile).endswith(f"; {render.growth_when(growth)}")
    growth["gib_per_day"] = 0
    assert capacity.growth(profile).endswith("; nothing grows there")
    assert capacity.growth(profile).endswith(f"; {render.growth_when(growth)}")
    growth["items"] = [{"id": "training", "per_run_gib": 50.0}]
    assert capacity.growth(profile).endswith("; nothing grows there (training runs come on top)")
    assert capacity.needs_consent({"outcome": "degraded"})
    assert not capacity.needs_consent({"outcome": "supported"})
    # A yellow resource on a host forced on the Preflight screen: bootstrap asks no
    # consent for it (the outcome is unsupported), so neither does the screen.
    forced = copy.deepcopy(preflight(DEGRADED).profile)
    forced["outcome"] = "unsupported"
    assert next(r.status for r in capacity.rows(forced)) == "degraded"
    assert not capacity.needs_consent(forced)
    assert capacity.waits(capacity.Answer(forced), {"force_unsupported"}, set()) == ""
    assert capacity.waits(capacity.Answer(refused="refused"), set(), set()) == (
        "the plan is refused: refused"
    )
    assert json.dumps(capacity.rows({})) == "[]"
    assert capacity.titles(
        [
            capacity.Row("m", "RAM", "fits", 0.0, "a", ()),
            capacity.Row("d", "Disk /srv/pinka", "fits", 0.0, "b", ()),
        ]
    ) == ["RAM             ░░░░░░░░░░ a", "Disk /srv/pinka ░░░░░░░░░░ b"]


@pytest.mark.parametrize(
    "fixture",
    [
        "ubuntu2404-disk-boundary-under",
        "ubuntu2404-disk-boundary-at",
        "ubuntu2404-loop-reserve-below",
        "ubuntu2404-disk-shortfall",
    ],
)
def test_the_disk_rows_print_preflight_s_figures(fixture: str) -> None:
    # One rounding: the capacity plan prints each disk budget's need, free space
    # and shortfall as preflight's refusal and summary print them.
    profile = preflight(fixture, ["--plan", str(FIXTURES / fixture / "plan.json")]).profile
    lines = {r.key: r.line for r in capacity.rows(profile)}
    budgets = profile["requirements"]["budgets"]["disk"]
    assert budgets
    for budget in budgets:
        need, free, short = requirements.disk_texts(
            budget["required_gib"], budget["free_gib"], budget["shortfall_gib"]
        )
        line = lines[f"disk:{budget['mount']}"]
        assert f"needs {need}, has {free} free" in line, line
        assert (f", {short} short" in line) == bool(short), line
        assert bool(short) == (budget["status"] == "does_not_fit")


def test_a_short_disk_row_rounds_the_need_up_and_the_free_space_down() -> None:
    # Figures whose tenths differ by the direction of the rounding.
    fixture = "ubuntu2404-disk-shortfall"
    profile = preflight(fixture, ["--plan", str(FIXTURES / fixture / "plan.json")]).profile
    budget = profile["requirements"]["budgets"]["disk"][-1]
    budget.update(status="does_not_fit", required_gib=9.41, free_gib=3.09, shortfall_gib=6.32)
    line = {r.key: r.line for r in capacity.rows(profile)}[f"disk:{budget['mount']}"]
    assert line == "does not fit, 6.4 GiB short · needs 9.5 GiB, has 3.0 GiB free"
    assert requirements.disk_texts(9.41, 3.09, 6.32) == ("9.5 GiB", "3.0 GiB", "6.4 GiB")
    # A need of exactly 10 GiB with one byte less free: never "has 10.0 GiB free".
    budget.update(required_gib=10.0, free_gib=9.99, shortfall_gib=0.01)
    line = {r.key: r.line for r in capacity.rows(profile)}[f"disk:{budget['mount']}"]
    assert line == "does not fit, 0.1 GiB short · needs 10.0 GiB, has 9.9 GiB free"
    # An item's size is a need there: rounded up, as preflight prints it.
    item = next(m for m in profile["requirements"]["disk"] if m["mount"] == budget["mount"])
    item["items"][0].update(size_gib=0.03, present=False, missing_gib=0.01)
    row = {r.key: r for r in capacity.rows(profile)}[f"disk:{budget['mount']}"]
    assert row.items[0].startswith(f"{item['items'][0]['title']}: 0.1 GiB (")
    assert row.items[0].endswith(", missing 0.1 GiB")
    assert requirements.disk_size_text(0.03, True) == "0.1 GiB"


def test_the_outcome_decides_whether_next_waits() -> None:
    # Where bootstrap's preflight stage would stop, Next waits: an unsupported outcome
    # not forced on the Preflight screen, an action preflight asks for (forced or not:
    # --force-unsupported does not skip it), a degraded outcome without the consent.
    profile = copy.deepcopy(preflight(FITS).profile)
    profile["outcome"] = "unsupported"
    unsupported = capacity.Answer(profile)
    assert capacity.waits(unsupported, set(), set()) == (
        "preflight finds the host unsupported with these choices"
    )
    assert capacity.waits(unsupported, {"force_unsupported"}, set()) == ""
    action = preflight(NEEDS_ACTION).profile
    assert capacity.waits(capacity.Answer(action), set(), set()) == (
        "preflight asks for an action first"
    )
    assert any(r.startswith("docker_cdi_off: ") for r in capacity.reasons(action))
    forced = copy.deepcopy(action)
    forced["outcome"] = "unsupported"  # an unsupported reason and an action together
    assert capacity.waits(capacity.Answer(forced), {"force_unsupported"}, set()) == (
        "preflight asks for an action first"
    )


def test_an_off_line_keeps_its_whole_reason_in_its_items() -> None:
    # A narrow terminal cuts a long title: the reason is the first item, in full.
    profile = preflight("ubuntu2404-inference-auto-small-gpu").profile
    serving = next(r for r in capacity.rows(profile) if r.key == "vram:serving")
    reason = profile["requirements"]["budgets"]["vram"]["serving"]["reason"]
    assert serving.status == "off"
    assert serving.line == f"off: {reason} · has 6144 MiB"
    assert serving.items[0] == f"off: {reason}"
    assert serving.fill == 0.0


@dataclass
class ChangingHost(FakeHost):
    """A host whose first answer fits and whose later answers do not."""

    later: dict[str, Any] = field(default_factory=dict)

    def preflight(self, args: Any) -> Preflight:
        first = self.preflight_calls == 0
        result = super().preflight(args)
        return result if first else Preflight(20, copy.deepcopy(self.later))


def test_next_waits_for_the_answer_to_the_current_choices(repo: Paths) -> None:
    # Next pressed right after a change goes by the answer to that change, not by
    # the earlier answer on the screen.
    host = ChangingHost(preflight(FITS), later=preflight(SHORT).profile)
    installer = on_capacity(repo, host)
    seen: dict[str, Any] = {}

    async def flow(pilot: Pilot[int]) -> None:
        await settle(pilot)
        screen_of(pilot).query_one("#targets").value = "3"  # type: ignore[attr-defined]
        await pilot.press("ctrl+n")
        await settle(pilot)
        seen["at"] = installer.current
        seen["status"] = status(pilot)

    run(installer, flow)
    assert host.preflight_calls == 2
    assert seen["at"] == "capacity"
    assert seen["status"].startswith("Next waits: does not fit")


def test_review_holds_install_while_the_plan_does_not_fit(repo: Paths) -> None:
    host = FakeHost(preflight(SHORT))
    seen = review_problems(on_review(repo, host))
    assert seen["totals"].startswith("Capacity plan (preflight, these choices):\nRAM")
    assert "\nWaits: does not fit: Disk /, Disk /srv\n" in seen["totals"]
    for reason in host.result.profile["unsupported_reasons"]:
        assert f"\n- {reason}" in seen["totals"]  # the reasons right below
    assert not seen["review"], seen["review"]
    assert seen["problems"] == [
        (
            "capacity-totals",
            "the capacity plan waits: does not fit: Disk /, Disk /srv. Edit the capacity plan: "
            "it shows why and runs preflight again",
        )
    ]


def test_a_model_pinned_now_counts_in_the_plan(repo: Paths) -> None:
    # The lock with the model pinned on the screen, before anything is written.
    host = FakeHost(preflight(FITS))
    installer = on_capacity(repo, host)
    revision = "0" * 40
    lock = (
        "models:\n  - id: example-model\n    hf_repo: example-org/example-model\n"
        f"    revision: '{revision}'\n    files:\n"
        "      - {path: model.safetensors, sha256: '" + "1" * 64 + "', size: 6442450944}\n"
    )
    installer.ctx.wizard.extra_files["models.lock.yml"] = lock
    installer.ctx.wizard.answers.gpu.default_model = "example-model"

    async def flow(pilot: Pilot[int]) -> None:
        await settle(pilot)

    run(installer, flow)
    model = host.plans[-1]["model"]
    assert (model["id"], model["revision"], model["size_bytes"]) == (
        "example-model",
        revision,
        6442450944,
    )


def test_a_shortfall_of_0_01_gib_reads_as_preflight_rounds_it() -> None:
    fixture = "ubuntu2404-loop-reserve-below"
    profile = preflight(fixture, ["--plan", plan_file(fixture)]).profile
    root = next(r for r in capacity.rows(profile) if r.key == "disk:/")
    # Where it falls short the need reads rounded up and the free space down.
    assert root.line == "does not fit, 0.1 GiB short · needs 94.6 GiB, has 94.5 GiB free"
    item = next(i for i in root.items if i.startswith("the free space kept beside"))
    assert item.endswith("(estimated), missing 0.1 GiB")


@pytest.mark.parametrize("fixture", [FITS, "ubuntu2404-inference-auto-small-gpu"])
def test_only_the_models_that_fit_are_listed(repo: Paths, fixture: str) -> None:
    # The 12 GB GPU: the models preflight says fit the profile; the 6 GB one: none.
    host = FakeHost(preflight(fixture))
    installer = on_capacity(repo, host)
    seen: dict[str, Any] = {}

    async def flow(pilot: Pilot[int]) -> None:
        await settle(pilot)
        screen = screen_of(pilot)
        table = screen.query_one("#models", DataTable)
        seen["keys"] = [str(key.value) for key in table.rows]
        seen["fit"] = [c.id for c in screen.choices if not c.fits]
        seen["profile"] = _profile_of(screen)

    run(installer, flow)
    serving = installer.ctx.wizard.capacity["requirements"]["serving"]["models"]
    offered = YAML(typ="safe").load(repo.models_catalog.read_text(encoding="utf-8"))["models"]
    by_model = {m["id"]: (serving.get(m["id"]) or {}).get(seen["profile"], {}) for m in offered}
    fit = [model for model, found in by_model.items() if found.get("fits")]
    assert seen["keys"] == ["none", *seen["fit"]]
    assert sorted(seen["fit"]) == sorted(fit)
    assert bool(fit) == (fixture == FITS)


def _profile_of(screen: CapacityScreen) -> str:
    return str(screen.query_one("#profile", Select).value)


def test_the_hugging_face_token_stays_in_memory(repo: Paths) -> None:
    # Typed next to Pin model: never in the resume state or the answers file.
    token = "hf_example_token_kept_in_memory"  # noqa: S105 - a test value
    installer = on_capacity(repo, FakeHost(preflight(FITS)))

    async def flow(pilot: Pilot[int]) -> None:
        await settle(pilot)
        pilot.app.screen.query_one("#hf").value = token  # type: ignore[attr-defined]
        await pilot.press("ctrl+n")
        await settle(pilot)

    run(installer, flow)
    wizard = installer.ctx.wizard
    assert installer.current == "capacity_device"
    assert wizard.provided.hf_token == token
    assert wizard.answers.secrets.hf_token == "provided"  # noqa: S105 - the word, no token
    assert token not in repo.state_file.read_text(encoding="utf-8")
    assert token not in answers_mod.dumps(wizard.answers)


FAILED_RUN = Preflight(40, {}, "Traceback (most recent call last):\nValueError: example\n")


def test_no_answer_from_preflight_holds_next(repo: Paths) -> None:
    # Preflight wrote no profile: the screen says why, with its exit code and last
    # error line, and Next does not go on.
    installer = on_capacity(repo, FakeHost(FAILED_RUN))
    seen: dict[str, Any] = {}

    async def flow(pilot: Pilot[int]) -> None:
        await settle(pilot)
        await pilot.press("ctrl+n")
        await settle(pilot)
        seen["at"] = installer.current
        seen["status"] = status(pilot)

    run(installer, flow)
    assert seen["at"] == "capacity"
    assert seen["status"] == "Next waits: preflight gave no answer (exit 40): ValueError: example"
    answer = capacity.Answer(failed="preflight gave no answer (exit 40)")
    everything = {"force_unsupported", "accept_degraded"}
    assert capacity.waits(answer, everything, set()) == answer.failed


def on_review(repo: Paths, host: FakeHost) -> InstallerApp:
    installer = app(context(repo, host, machine="pinka-lab"), first="review")
    wizard = installer.ctx.wizard
    wizard.profile = copy.deepcopy(host.result.profile)
    ci = repo.infra / "tests" / "answers" / "ci.yml"
    wizard.answers = Answers.from_dict(YAML(typ="safe").load(ci.read_text(encoding="utf-8")))
    wizard.answers.machine.name = "pinka-lab"
    wizard.vault_password = "correct horse battery staple 42"  # noqa: S105 - a test value
    wizard.provided.tailscale_authkey = "authkey-placeholder"
    return installer


def review_problems(installer: InstallerApp) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    async def flow(pilot: Pilot[int]) -> None:
        await settle(pilot)
        screen = pilot.app.screen
        seen["totals"] = str(screen.query_one("#capacity-totals", Static).content)
        seen["problems"] = screen.save()  # type: ignore[attr-defined]
        seen["review"] = screen.problems  # type: ignore[attr-defined]

    run(installer, flow)
    return seen


def test_no_answer_from_preflight_holds_install(repo: Paths) -> None:
    seen = review_problems(on_review(repo, FakeHost(FAILED_RUN)))
    assert not seen["review"], seen["review"]
    assert seen["problems"] == [
        (
            "capacity-totals",
            "the capacity plan waits: preflight gave no answer (exit 40): ValueError: example. "
            "Edit the capacity plan: it shows why and runs preflight again",
        )
    ]


def test_the_review_asks_again_for_degraded_lines_never_accepted(repo: Paths) -> None:
    # A resumed wizard keeps --accept-degraded but not the lines it was given for: the
    # Review lists them and Install waits for the consent on the capacity plan.
    installer = on_review(repo, FakeHost(preflight(DEGRADED)))
    installer.ctx.wizard.consents.add("accept_degraded")
    seen = review_problems(installer)
    lines = capacity.degraded(preflight(DEGRADED).profile)
    assert lines
    for line in lines:
        assert f"\n- {line}" in seen["totals"]
    assert seen["problems"][0][1].startswith(
        "the capacity plan waits: what is degraded needs your consent. "
    )


def test_the_review_drops_a_consent_nothing_needs(repo: Paths) -> None:
    # Bootstrap judges the final plan: when nothing is degraded, no override goes to it.
    installer = on_review(repo, FakeHost(preflight(FITS)))
    installer.ctx.wizard.consents.add("accept_degraded")
    seen = review_problems(installer)
    assert seen["problems"] == []
    assert "accept_degraded" not in installer.ctx.wizard.consents


def test_a_degraded_outcome_needs_the_consent(repo: Paths) -> None:
    # inference: auto on a GPU too small for the profile: preflight turns serving off and
    # calls the outcome degraded although no bar is yellow; going on needs the consent.
    fixture = "ubuntu2404-inference-auto-small-gpu"
    installer = on_capacity(repo, FakeHost(preflight(fixture)))
    seen: dict[str, Any] = {}

    async def flow(pilot: Pilot[int]) -> None:
        await settle(pilot)
        screen = screen_of(pilot)
        seen["rows"] = {r.status for r in screen.table}
        consent = screen.query_one("#cap-consent", Checkbox)
        seen["shown"] = consent.display
        await pilot.press("ctrl+n")
        await settle(pilot)
        seen["without"] = installer.current
        consent.value = True
        await pilot.press("ctrl+n")
        await settle(pilot)
        seen["with"] = installer.current

    run(installer, flow)
    assert "degraded" not in seen["rows"]
    assert seen["shown"] is True
    assert seen["without"] == "capacity"
    assert seen["with"] == "capacity_device"
    assert "accept_degraded" in installer.ctx.wizard.consents


def test_a_consent_without_its_lines_does_not_tick_the_box(repo: Paths) -> None:
    # The bare flag (as a resume restores it, without the lines it was given for) does
    # not tick the box; unticking it here takes --accept-degraded back, and so does an
    # answer that no longer degrades anything.
    host = ChangingHost(preflight(DEGRADED), later=preflight(FITS).profile)
    installer = on_capacity(repo, host)
    wizard = installer.ctx.wizard
    wizard.consents.add("accept_degraded")  # restored by a resume, without its lines
    seen: dict[str, Any] = {}

    async def flow(pilot: Pilot[int]) -> None:
        await settle(pilot)
        screen = screen_of(pilot)
        consent = screen.query_one("#cap-consent", Checkbox)
        seen["ticked at first"] = consent.value
        seen["status"] = status(pilot)
        consent.value = True
        await pilot.pause()
        seen["after tick"] = "accept_degraded" in wizard.consents
        consent.value = False
        await pilot.pause()
        seen["after untick"] = "accept_degraded" in wizard.consents
        seen["accepted after untick"] = set(wizard.degraded_accepted)
        consent.value = True
        screen.query_one("#targets").value = "1"  # type: ignore[attr-defined]
        await pilot.pause(capacity_debounce())
        await settle(pilot)  # the later answer fits
        seen["shown when fitting"] = consent.display
        seen["after fitting"] = "accept_degraded" in wizard.consents

    run(installer, flow)
    assert seen["ticked at first"] is False
    assert seen["status"] == "Next waits: what is degraded needs your consent (see below)"
    assert seen["after tick"] is True
    assert seen["after untick"] is False
    assert not seen["accepted after untick"] & set(capacity.degraded(preflight(DEGRADED).profile))
    assert seen["shown when fitting"] is False
    assert seen["after fitting"] is False


def test_an_answer_after_leaving_the_page_is_dropped(
    repo: Paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The operator goes back while preflight still runs: its late answer reaches the
    # page that is gone, which drops it (Textual keeps is_mounted on such a screen).
    host = SlowFirstHost(preflight(FITS), stale=preflight(SHORT).profile)
    installer = on_capacity(repo, host)
    calls = calls_of(monkeypatch, CapacityScreen, "show_result")
    seen: dict[str, Any] = {}

    async def flow(pilot: Pilot[int]) -> None:
        await pilot.press("ctrl+b")
        await pilot.pause()
        seen["at"] = installer.current
        await after_the_first_run(pilot, host, calls)
        await settle(pilot)
        seen["capacity"] = installer.ctx.wizard.capacity

    run(installer, flow)
    assert seen["at"] == "machine"
    assert calls == [True]  # the late answer came, to a page that was gone
    assert seen["capacity"] == {}


def test_a_review_answer_after_edit_does_not_replace_the_newer_one(
    repo: Paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = SlowFirstHost(preflight(FITS), stale=preflight(SHORT).profile)
    installer = app(context(repo, host, machine="pinka-lab"), first="review")
    wizard = installer.ctx.wizard
    wizard.profile = copy.deepcopy(host.result.profile)
    wizard.answers.machine.name = "pinka-lab"
    calls = calls_of(monkeypatch, ReviewScreen, "show_capacity")
    seen: dict[str, Any] = {}

    async def flow(pilot: Pilot[int]) -> None:
        await pilot.pause()
        installer.goto("capacity")  # what the capacity row's Edit button does
        for _ in range(200):
            await pilot.pause(0.05)
            if wizard.capacity:
                break
        await after_the_first_run(pilot, host, calls)  # the Review's run ends last
        await settle(pilot)
        seen["capacity"] = wizard.capacity

    run(installer, flow)
    assert calls == [True]  # the Review's late answer came, after it was left
    assert seen["capacity"] == host.result.profile


def test_a_next_pressed_before_a_change_waits_for_a_new_press(repo: Paths) -> None:
    host = SlowFirstHost(preflight(FITS), stale=preflight(FITS).profile)
    installer = on_capacity(repo, host)
    seen: dict[str, Any] = {}

    async def flow(pilot: Pilot[int]) -> None:
        await pilot.pause()
        await pilot.press("ctrl+n")  # run 1 still runs: Next waits for its answer
        screen = screen_of(pilot)
        seen["wanted"] = screen.next_wanted
        screen.query_one("#targets").value = "3"  # type: ignore[attr-defined]
        await pilot.pause(capacity_debounce())
        host.release_first.set()
        await settle(pilot)
        seen["at"] = installer.current

    run(installer, flow)
    assert seen["wanted"] is True
    assert seen["at"] == "capacity"


def test_a_gated_model_needs_the_token_even_when_pinned(repo: Paths) -> None:
    # The installation downloads a gated model with the token.
    listed = repo.models_catalog.read_text(encoding="utf-8")
    repo.models_catalog.write_text(listed.replace("gated: false", "gated: true", 1))
    model = YAML(typ="safe").load(listed)["models"][0]
    host = FakeHost(preflight(FITS))
    installer = on_capacity(repo, host)
    installer.ctx.wizard.extra_files["models.lock.yml"] = (
        f"models:\n  - id: {model['id']}\n    hf_repo: {model['hf_repo']}\n"
        f"    revision: '{'0' * 40}'\n    files:\n"
        "      - {path: model.safetensors, sha256: '" + "1" * 64 + "', size: 6442450944}\n"
    )
    installer.ctx.wizard.answers.gpu.default_model = model["id"]
    seen: dict[str, Any] = {}

    async def flow(pilot: Pilot[int]) -> None:
        await settle(pilot)
        await pilot.press("ctrl+n")
        await settle(pilot)
        seen["without"] = installer.current
        seen["error"] = str(pilot.app.screen.query_one("#field-hf .error", Static).content)
        pilot.app.screen.query_one("#hf").value = "hf_example"  # type: ignore[attr-defined]
        await pilot.press("ctrl+n")
        await settle(pilot)
        seen["with"] = installer.current

    run(installer, flow)
    assert seen["without"] == "capacity"
    assert "this model is gated: enter a Hugging Face token" in seen["error"]
    assert seen["with"] == "capacity_device"


def test_the_node_role_rebuilds_the_serving_choices(repo: Paths) -> None:
    host = FakeHost(preflight(FITS))
    installer = on_capacity(repo, host)
    installer.ctx.wizard.answers.gpu.default_model = "qwen3-8b-awq"
    seen: dict[str, Any] = {}

    async def flow(pilot: Pilot[int]) -> None:
        await settle(pilot)
        screen = screen_of(pilot)
        screen.query_one("#node_role", Select).value = "lab"
        await settle(pilot)
        seen["lab"] = [str(s.content) for s in screen.query("#serving Static").results(Static)]
        seen["lab models"] = bool(screen.query("#models"))
        seen["lab model"] = installer.ctx.wizard.answers.gpu.default_model
        screen.query_one("#node_role", Select).value = "full"
        await settle(pilot)
        seen["full models"] = bool(screen.query("#models"))

    run(installer, flow)
    assert seen["lab"] == [
        "Serving: not applicable here: a lab node serves no model; its inference node does"
    ]
    assert seen["lab models"] is False
    assert seen["lab model"] == "none"
    assert seen["full models"] is True


def test_wsl_installs_no_driver_and_invalid_fields_are_not_counted(repo: Paths) -> None:
    # On WSL the Windows driver serves the GPU, whatever an earlier answers file said;
    # a field that holds a value it does not accept is not counted, and the status says so.
    installer = on_capacity(repo, FakeHost(preflight(FITS)))
    installer.ctx.wizard.answers.gpu.install_driver = "auto"
    seen: dict[str, Any] = {}

    async def flow(pilot: Pilot[int]) -> None:
        await settle(pilot)
        screen_of(pilot).query_one("#targets").value = "many"  # type: ignore[attr-defined]
        await pilot.pause(capacity_debounce())
        await settle(pilot)
        seen["status"] = status(pilot)

    run(installer, flow)
    assert installer.ctx.wizard.answers.gpu.install_driver == "skip"
    assert installer.ctx.host.plans[-1]["concurrent_targets"] == 2  # type: ignore[attr-defined]
    assert seen["status"] == (
        "preflight: supported without the fields that hold a value they do not accept "
        "(Next names them)"
    )


def test_a_pending_next_does_not_go_on_under_a_dialog(repo: Paths) -> None:
    # Next pressed while preflight still runs, then F1: the answer arrives under the
    # help dialog, and the page stays (the dialog is not replaced by the next page).
    host = SlowFirstHost(preflight(FITS), stale=preflight(FITS).profile)
    installer = on_capacity(repo, host)
    seen: dict[str, Any] = {}

    async def flow(pilot: Pilot[int]) -> None:
        await pilot.pause()
        await pilot.press("ctrl+n")
        await pilot.press("f1")
        await pilot.pause()
        host.release_first.set()
        await settle(pilot)
        seen["screen"] = type(pilot.app.screen).__name__
        seen["at"] = installer.current

    run(installer, flow)
    assert seen["screen"] == "TextView"
    assert seen["at"] == "capacity"


def test_preflight_judges_the_driver_mode_host_vars_will_hold(repo: Paths) -> None:
    # Bootstrap's own run passes host_vars' install_nvidia_driver: so do the plan's runs
    # (bare metal: WSL always skips the driver).
    host = FakeHost(preflight("ubuntu2404-baremetal-nvidia-driver-loaded"))
    installer = on_capacity(repo, host)
    installer.ctx.wizard.answers.gpu.install_driver = "manual"
    installer.ctx.wizard.consents.add("accept_mok_enrollment")

    async def flow(pilot: Pilot[int]) -> None:
        await settle(pilot)

    run(installer, flow)
    args = host.preflight_args[-1]
    assert args[:3] == ["--plan", args[1], "--offline"]
    example = repo.example_host_vars.read_text(encoding="utf-8")
    written = YAML(typ="safe").load(hostvars.render(installer.ctx.wizard.answers, None, example))
    assert written["install_nvidia_driver"] == "manual"
    assert args[3:] == ["--nvidia-driver", "manual", "--accept-mok-enrollment"]


def test_the_review_runs_preflight_with_bootstraps_driver_options(repo: Paths) -> None:
    host = FakeHost(preflight("ubuntu2404-baremetal-nvidia-driver-loaded"))
    installer = on_review(repo, host)
    installer.ctx.wizard.answers.gpu.install_driver = "auto"
    review_problems(installer)
    args = host.preflight_args[-1]
    assert args[:3] == ["--plan", args[1], "--offline"]
    assert args[3:] == ["--nvidia-driver", "auto"]


def test_a_pasted_superscript_digit_is_no_number(repo: Paths) -> None:
    # str.isdigit accepts "²" but int refuses it: the fields say so, nothing crashes.
    installer = on_capacity(repo, FakeHost(preflight(FITS)))
    seen: dict[str, Any] = {}

    async def flow(pilot: Pilot[int]) -> None:
        await settle(pilot)
        screen = screen_of(pilot)
        for field_id in ("targets", "loop_size", "memory", "threads"):
            screen.query_one(f"#{field_id}").value = "²"  # type: ignore[attr-defined]
        await pilot.pause(capacity_debounce())
        await settle(pilot)
        await pilot.press("ctrl+n")
        await settle(pilot)
        seen["at"] = installer.current
        seen["errors"] = {
            str(f.id): str(f.query_one(".error", Static).content)
            for f in screen.query(".field")
            if str(f.query_one(".error", Static).content)
        }

    run(installer, flow)
    assert seen["at"] == "capacity"
    assert seen["errors"]["field-targets"] == "✖ a whole number from 1 to 40"
    assert seen["errors"]["field-loop_size"] == "✖ auto, or at least 20 GiB (at most nine digits)"
    assert seen["errors"]["field-memory"] == "✖ a positive whole number (at most nine digits)"
    assert seen["errors"]["field-threads"] == "✖ a positive whole number (at most nine digits)"
    assert validators.whole("²") is None and validators.whole("12") == 12
    # Hundreds of digits overflow what the plan computes with: no number either.
    assert validators.whole("9" * 9) == 999999999 and validators.whole("9" * 10) is None


def test_a_consent_problem_of_an_older_answer_does_not_stop_a_pending_next(
    repo: Paths,
) -> None:
    # The shown answer is degraded and unticked; the operator changes a field and presses
    # Next at once: Next waits for the answer to the new choices, which fits, and goes on.
    host = ChangingHost(preflight(DEGRADED), later=preflight(FITS).profile)
    installer = on_capacity(repo, host)
    seen: dict[str, Any] = {}

    async def flow(pilot: Pilot[int]) -> None:
        await settle(pilot)
        screen = screen_of(pilot)
        seen["shown"] = screen.query_one("#cap-consent", Checkbox).display
        screen.query_one("#targets").value = "1"  # type: ignore[attr-defined]
        await pilot.press("ctrl+n")
        await settle(pilot)
        seen["at"] = installer.current

    run(installer, flow)
    assert seen["shown"] is True
    assert seen["at"] == "capacity_device"


def test_a_value_too_large_to_plan_with_is_refused_not_a_crash(repo: Paths) -> None:
    # Hundreds of digits in a retention period overflow the plan writer's arithmetic: the
    # page refuses the plan, and the installer goes on running.
    installer = on_capacity(repo, FakeHost(preflight(FITS)))
    seen: dict[str, Any] = {}

    async def flow(pilot: Pilot[int]) -> None:
        await settle(pilot)
        screen_of(pilot).query_one("#ret-tempo").value = "9" * 400 + "h"  # type: ignore[attr-defined]
        await pilot.pause(capacity_debounce())
        await settle(pilot)
        seen["status"] = status(pilot)

    run(installer, flow)
    assert seen["status"].startswith(
        "Next waits: the plan is refused: a value is too large to plan with ("
    )


def test_the_preflight_screen_ticks_only_lines_accepted_already(repo: Paths) -> None:
    # A resume restores --accept-degraded without its lines: the Preflight screen's boxes
    # start unticked, and ticked only for the lines the operator accepted.
    fixture = "ubuntu2404-baremetal-no-gpu"
    lines = set(capacity.degraded(preflight(fixture).profile))
    assert lines
    for accepted in (set(), lines):
        installer = app(context(repo, FakeHost(preflight(fixture))), first="preflight")
        installer.ctx.wizard.consents.add("accept_degraded")
        installer.ctx.wizard.degraded_accepted = set(accepted)
        seen: dict[str, Any] = {}

        async def flow(pilot: Pilot[int], seen: dict[str, Any] = seen) -> None:
            await settle(pilot)
            boxes = list(pilot.app.screen.query(".consent-feature").results(Checkbox))
            seen["boxes"] = {str(b.label): b.value for b in boxes}

        run(installer, flow)
        assert set(seen["boxes"]) == lines
        assert set(seen["boxes"].values()) == {bool(accepted)}


def test_new_degraded_lines_untick_the_box_and_keep_the_accepted_ones(repo: Paths) -> None:
    # The operator ticked the box; a later answer adds a line: the box asks again, and the
    # lines ticked before stay accepted (only the operator's untick takes them back).
    later = copy.deepcopy(preflight(DEGRADED).profile)
    later["degraded_reasons"].append("example: another degraded line")
    host = ChangingHost(preflight(DEGRADED), later=later)
    installer = on_capacity(repo, host)
    wizard = installer.ctx.wizard
    first = set(capacity.degraded(preflight(DEGRADED).profile))
    seen: dict[str, Any] = {}

    async def flow(pilot: Pilot[int]) -> None:
        await settle(pilot)
        screen = screen_of(pilot)
        consent = screen.query_one("#cap-consent", Checkbox)
        consent.value = True
        await pilot.pause()
        screen.query_one("#targets").value = "1"  # type: ignore[attr-defined]
        await pilot.pause(capacity_debounce())
        await settle(pilot)
        seen["ticked"] = consent.value
        seen["accepted"] = set(wizard.degraded_accepted)

    run(installer, flow)
    assert seen["ticked"] is False
    assert first <= seen["accepted"]
    assert "example: another degraded line" not in seen["accepted"]


def test_the_driver_is_chosen_on_the_main_page_and_preflight_judges_with_it(
    repo: Paths,
) -> None:
    # Bare metal, a GPU without a driver, no Secure Boot, inference: on. With the answers'
    # default "No driver" serving does not fit; choosing the pinned driver on this page,
    # before Next, leaves only the consent to wait for the driver the install installs.
    fixture = "ubuntu2404-baremetal-nvidia-turing"
    host = FakeHost(preflight(fixture), fixture=fixture)
    installer = on_capacity(repo, host)
    installer.ctx.wizard.answers.features.inference = "on"
    seen: dict[str, Any] = {}

    async def flow(pilot: Pilot[int]) -> None:
        await settle(pilot)
        screen = screen_of(pilot)
        seen["field"] = screen.query_one("#install_driver", Select).value
        seen["skip"] = status(pilot)
        screen.query_one("#install_driver", Select).value = "auto"
        await pilot.pause(capacity_debounce())
        await settle(pilot)
        seen["auto args"] = host.preflight_args[-1][3:]
        seen["auto"] = status(pilot)

    run(installer, flow)
    assert seen["field"] == "skip"
    assert seen["skip"] == "Next waits: does not fit: VRAM serving (see below)"
    assert seen["auto args"] == ["--nvidia-driver", "auto"]
    # The driver is installed during the install: only the consent to wait for it.
    assert seen["auto"] == "Next waits: what is degraded needs your consent (see below)"
    assert installer.ctx.wizard.answers.gpu.install_driver == "auto"


def test_the_driver_field_is_shown_off_wsl_on_every_node_role(repo: Paths) -> None:
    # WSL: the Windows driver provides the GPU. A lab node serves no model, but the play
    # installs the driver there too (and with Secure Boot it needs the MOK password page).
    loaded, no_gpu = "ubuntu2404-baremetal-nvidia-driver-loaded", "ubuntu2404-baremetal-no-gpu"
    for fixture, role, shown in (
        (FITS, "full", False),
        (no_gpu, "full", False),  # no GPU: no driver, and no serving either
        (loaded, "lab", True),
        (loaded, "full", True),
    ):
        installer = on_capacity(repo, FakeHost(preflight(fixture)))
        installer.ctx.wizard.answers.machine.node_role = role
        seen: dict[str, Any] = {}

        async def flow(pilot: Pilot[int], seen: dict[str, Any] = seen) -> None:
            await settle(pilot)
            screen = pilot.app.screen
            seen["field"] = bool(screen.query("#install_driver"))
            seen["serving"] = bool(screen.query("#profile"))

        run(installer, flow)
        assert seen["field"] is shown, (fixture, role)
        assert seen["serving"] is (role == "full" and fixture != no_gpu), (fixture, role)


def test_a_forced_host_keeps_its_yellow_bars_and_their_reasons(repo: Paths) -> None:
    # Forced on the Preflight screen, the outcome is unsupported: bootstrap asks no consent
    # for what is degraded, and neither does the page; the yellow bar and its reason stay.
    forced = copy.deepcopy(preflight(DEGRADED).profile)
    forced["outcome"] = "unsupported"
    forced["unsupported_reasons"] = preflight("ubuntu2404-no-btrfs-no-dmthin").profile[
        "unsupported_reasons"
    ]
    installer = on_capacity(repo, FakeHost(Preflight(20, forced)))
    installer.ctx.wizard.consents.add("force_unsupported")
    seen: dict[str, Any] = {}

    async def flow(pilot: Pilot[int]) -> None:
        await settle(pilot)
        screen = pilot.app.screen
        seen["yellow"] = [
            str(c.title) for c in screen.query("Collapsible.degraded").results(Collapsible)
        ]
        seen["reasons"] = str(screen.query_one("#cap-reasons", Static).content)
        seen["consent"] = screen.query_one("#cap-consent", Checkbox).display
        seen["status"] = status(pilot)
        await pilot.press("ctrl+n")
        await settle(pilot)
        seen["at"] = installer.current

    run(installer, flow)
    assert len(seen["yellow"]) == 1 and seen["yellow"][0].startswith("RAM ")
    assert "degraded, 0.1 GiB short" in seen["yellow"][0]
    for reason in [*forced["unsupported_reasons"], *forced["degraded_reasons"]]:
        assert f"- {reason}" in seen["reasons"]
    assert seen["consent"] is False
    assert seen["status"] == "preflight: unsupported with these choices"
    assert seen["at"] == "capacity_device"


@pytest.mark.parametrize("field_id", ["ret-prometheus", "ret-loki"])
def test_a_retention_too_long_to_write_is_refused_not_a_crash(repo: Paths, field_id: str) -> None:
    # 4300 digits and "y" or "w": the plan's whole number of days has more digits than
    # json.dumps writes; the page refuses the plan, and the installer goes on running.
    installer = on_capacity(repo, FakeHost(preflight(FITS)))
    unit = "y" if field_id == "ret-prometheus" else "w"
    seen: dict[str, Any] = {}

    async def flow(pilot: Pilot[int]) -> None:
        await settle(pilot)
        screen_of(pilot).query_one(f"#{field_id}").value = "9" * 4300 + unit  # type: ignore[attr-defined]
        await pilot.pause(capacity_debounce())
        await settle(pilot)
        seen["status"] = status(pilot)

    run(installer, flow)
    assert seen["status"].startswith(
        "Next waits: the plan is refused: a value is too large to plan with ("
    )


def test_the_review_lists_a_forced_hosts_degraded_reasons(repo: Paths) -> None:
    # Nothing waits on a forced host, and the Review still lists what is degraded.
    forced = copy.deepcopy(preflight(DEGRADED).profile)
    forced["outcome"] = "unsupported"
    installer = on_review(repo, FakeHost(Preflight(20, forced)))
    installer.ctx.wizard.consents.add("force_unsupported")
    seen = review_problems(installer)
    assert seen["problems"] == []
    for reason in forced["degraded_reasons"]:
        assert f"\n- {reason}" in seen["totals"]


def test_a_driver_mode_no_field_offers_is_refused_never_replaced(repo: Paths) -> None:
    # A host_vars file edited by hand: the field starts blank and Next names the value;
    # it is neither shown nor saved as another mode, and the installer goes on running.
    host = FakeHost(preflight("ubuntu2404-baremetal-nvidia-driver-loaded"))
    installer = on_capacity(repo, host)
    installer.ctx.wizard.answers.gpu.install_driver = "bogus"
    seen: dict[str, Any] = {}

    async def flow(pilot: Pilot[int]) -> None:
        await settle(pilot)
        screen = pilot.app.screen
        seen["field"] = screen.query_one("#install_driver", Select).value
        await pilot.press("ctrl+n")
        await settle(pilot)
        seen["errors"] = [
            str(s.content) for s in screen.query(".error").results(Static) if str(s.content)
        ]

    run(installer, flow)
    assert seen["field"] is Select.NULL
    assert "✖ 'bogus' is not one of the choices (auto, manual, skip): choose one" in seen["errors"]
    assert installer.ctx.wizard.answers.gpu.install_driver == "bogus"
    assert installer.current == "capacity"


def test_a_driver_mode_no_field_offers_never_reaches_preflight(repo: Paths) -> None:
    # Preflight refuses a --nvidia-driver it does not take (no profile, a dead end on the
    # Preflight screen): such a mode, or one of another type from a resumed state, is
    # left out of its runs while the field refuses it.
    secure_boot = "ubuntu2404-baremetal-nvidia-no-driver-secureboot"
    host = FakeHost(preflight(secure_boot), fixture=secure_boot)
    installer = on_capacity(repo, host)
    wizard = installer.ctx.wizard
    wizard.answers.gpu.install_driver = "magic"

    async def flow(pilot: Pilot[int]) -> None:
        await settle(pilot)
        await pilot.pause(capacity_debounce())
        await settle(pilot)

    run(installer, flow)
    assert wizard.mok_enrollment_needed
    assert host.preflight_args
    assert not any("magic" in args for args in host.preflight_args)
    assert wizard.nvidia_driver != "magic"
    assert "--nvidia-driver" not in wizard.plan_args()
    wizard.nvidia_driver = "magic"
    assert "--nvidia-driver" not in wizard.preflight_args()
    wizard.answers.gpu.install_driver = ["auto"]  # type: ignore[assignment]  # a hand-edited state
    assert "--nvidia-driver" not in wizard.plan_args()


def test_a_driver_mode_of_another_type_is_refused_never_a_crash(repo: Paths) -> None:
    # A hand-edited resumed state can hold a mode of any type; with Secure Boot (the MOK
    # consent given) the driver's save compares it, and the field refuses it.
    secure_boot = "ubuntu2404-baremetal-nvidia-no-driver-secureboot"
    installer = on_capacity(repo, FakeHost(preflight(secure_boot), fixture=secure_boot))
    wizard = installer.ctx.wizard
    wizard.consents.add("accept_mok_enrollment")
    wizard.answers.gpu.install_driver = ["auto"]  # type: ignore[assignment]  # a hand-edited state
    seen: dict[str, Any] = {}

    async def flow(pilot: Pilot[int]) -> None:
        await settle(pilot)
        screen = pilot.app.screen
        await pilot.press("ctrl+n")
        await settle(pilot)
        seen["errors"] = [
            str(s.content) for s in screen.query(".error").results(Static) if str(s.content)
        ]

    run(installer, flow)
    assert wizard.mok_enrollment_needed
    assert (
        "✖ \"['auto']\" is not one of the choices (auto, manual, skip): choose one"
        in seen["errors"]
    )
    assert installer.current == "capacity"


def test_the_driver_modes_are_preflight_s() -> None:
    assert DRIVER_MODES == matrix.NVIDIA_DRIVER_MODES
    assert [value for _label, value in NVIDIA_DRIVER] == list(DRIVER_MODES)


def test_a_stored_profile_bootstrap_cannot_compare_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Bootstrap stops on such a file until it is fixed or replaced: the Review screen
    # compares nothing, and passes no --refresh-profile that would replace it unseen.
    stored = tmp_path / "host-profile.json"
    monkeypatch.setattr(host_mod, "PROFILE_FILE", stored)
    real = host_mod.RealHost(cast(Paths, None))
    assert real.stored_profile() == {}  # none
    for text in ("{", "[]", '{"features": ["x"]}', '{"features": "x"}', '{"features": 5}'):
        stored.write_text(text, encoding="utf-8")
        assert real.stored_profile() == {}, text
    stored.write_text('{"outcome": "supported", "features": {"vm_targets": true}}')
    assert real.stored_profile() == {"outcome": "supported", "features": {"vm_targets": True}}


def test_a_message_without_a_field_is_plain_text(repo: Paths) -> None:
    # Shown as a notification: a refusal can name a value read from a file, with markup in
    # it (a closing tag would end the installer when the notification is drawn).
    installer = on_capacity(repo, FakeHost(preflight(FITS)))
    drawn: list[str] = []

    async def main() -> None:
        # run_test turns notifications off unless asked: the toast must be drawn here.
        async with installer.run_test(size=SIZE, notifications=True) as pilot:
            await settle(pilot)
            screen = cast(CapacityScreen, pilot.app.screen)
            screen.show_problems([("nowhere", "Next waits: the plan is refused: x ('[/]')")])
            for _ in range(3):
                await pilot.pause()
            drawn.extend(str(t.render()) for t in pilot.app.screen.query("Toast"))

    asyncio.run(main())
    assert drawn == ["Next waits: the plan is refused: x ('[/]')"]
    assert installer.current == "capacity"


def test_the_preflight_screen_ticks_each_accepted_line_alone(repo: Paths) -> None:
    # One line accepted, the other not: only its box starts ticked.
    fixture = "ubuntu2404-baremetal-no-gpu"
    lines = sorted(capacity.degraded(preflight(fixture).profile))
    assert len(lines) >= 2
    installer = app(context(repo, FakeHost(preflight(fixture))), first="preflight")
    installer.ctx.wizard.consents.add("accept_degraded")
    installer.ctx.wizard.degraded_accepted = {lines[0]}
    seen: dict[str, Any] = {}

    async def flow(pilot: Pilot[int]) -> None:
        await settle(pilot)
        boxes = list(pilot.app.screen.query(".consent-feature").results(Checkbox))
        seen["boxes"] = {str(b.label): b.value for b in boxes}

    run(installer, flow)
    assert seen["boxes"] == {line: line == lines[0] for line in lines}


def test_the_profile_changes_are_bootstrap_s(repo: Paths) -> None:
    # The Review screen's comparison is a copy of the one bootstrap and the play's
    # preflight gate stop on (preflight's profile_tool.differences): the same lines.
    base = preflight("ubuntu2404-baremetal-no-gpu").profile
    other = preflight("ubuntu2404-baremetal-nvidia-driver-loaded").profile
    unsupported = preflight("ubuntu2404-no-btrfs-no-dmthin").profile
    no_features = {k: v for k, v in base.items() if k != "features"}
    none_provider = {**base, "provider": None}
    extra = {**base, "features": {**base["features"], "new_feature": {"nested": [1, 2]}}}
    pairs = [
        (base, base),
        (base, other),
        (other, base),
        (base, unsupported),
        (base, no_features),
        (no_features, base),
        (base, none_provider),
        (base, extra),
        (extra, base),
    ]
    for stored, new in pairs:
        assert capacity.profile_changes(stored, new) == profile_tool.differences(
            copy.deepcopy(stored), copy.deepcopy(new)
        )
    assert capacity.profile_changes(base, base) == []
    assert capacity.profile_changes(base, unsupported)[0] == "outcome: degraded -> unsupported"
    assert capacity.profile_changes(base, extra) == ['features.new_feature:  -> {"nested": [1, 2]}']


def test_a_cpu_range_of_many_digits_is_refused_at_once(repo: Paths) -> None:
    # The CPU bar runs on every keystroke and the check on Next: both handle the range
    # as a range (test_validators.py runs them with bounded memory and time).
    installer = on_capacity(repo, FakeHost(preflight(FITS)))
    threads = installer.ctx.wizard.cpu_threads
    assert threads
    seen: dict[str, Any] = {}

    async def flow(pilot: Pilot[int]) -> None:
        await settle(pilot)
        screen = pilot.app.screen
        screen.query_one("#cpuset_services", Input).value = "0-99999999999"
        screen.query_one("#cpuset_targets", Input).value = "0"
        await pilot.pause()
        seen["bar"] = str(screen.query_one("#cpu-bar", Static).content)
        await pilot.press("ctrl+n")
        await settle(pilot)
        seen["errors"] = [
            str(s.content) for s in screen.query(".error").results(Static) if str(s.content)
        ]

    run(installer, flow)
    assert seen["bar"].startswith("[X" + "S" * (threads - 1) + "]")
    assert (
        f"✖ CPU 99999999999 does not exist (this host has CPUs 0-{threads - 1})" in seen["errors"]
    )
    assert installer.current == "capacity"
