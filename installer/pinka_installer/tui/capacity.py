"""What the capacity plan screen shows of preflight's answer.

The screen decides nothing about capacity: it sends preflight the plan of the
choices so far (the same plan bootstrap writes) and shows the host profile's
``requirements``. These functions only turn preflight's numbers into lines:
a bar and a status per resource, the items behind each, the growth and the
reasons preflight gave, word for word. Whether Next waits follows preflight's
outcome, as bootstrap's preflight stage would.
"""

from __future__ import annotations

import datetime
import json
import math
import tempfile
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from pinka_installer import hostvars
from pinka_installer import plan as plan_mod
from pinka_installer.answers import Answers
from pinka_installer.paths import Paths
from pinka_installer.tui.host import Host

# Width of a bar in cells.
BAR_CELLS = 10
# What every number of a row is: what the host has, and what the choices need.
LEGEND = (
    "has: measured on this host · needs: estimated or configured, each number marked "
    "(Enter on a line shows its items)"
)
STATUS_WORDS = {
    "fits": "fits",
    "degraded": "degraded",
    "does_not_fit": "does not fit",
    "unknown": "not measured",
    "off": "off",
}


@dataclass(frozen=True)
class Row:
    """One resource of the budget.

    Attributes:
        key: ``memory``, ``disk:<mount>``, ``vram:serving`` or ``vram:training``.
        label: What it is (``RAM``, ``Disk /srv``, ``VRAM serving``).
        status: Preflight's status: fits, degraded, does_not_fit, unknown or off.
        fill: How full the bar is, 0 to 1 (need over what there is).
        line: The numbers and the status, after the bar.
        items: One line per item behind the need.
    """

    key: str
    label: str
    status: str
    fill: float
    line: str
    items: tuple[str, ...]

    def bar(self) -> str:
        """The bar: filled cells for the need, empty cells for what is left."""
        cells = min(BAR_CELLS, max(0, math.ceil(self.fill * BAR_CELLS - 1e-9)))
        return "█" * cells + "░" * (BAR_CELLS - cells)


def up(value: float) -> float:
    """Round up to a tenth, as preflight's refusals round a shortfall."""
    return math.ceil(round(value * 10, 6)) / 10


def down(value: float) -> float:
    """Round down to a tenth: what a host has, where a resource falls short."""
    return math.floor(round(value * 10, 6)) / 10


def gib(value: float | None) -> str:
    """A size in GiB with one decimal, or ``?``."""
    return "?" if value is None else f"{value:.1f} GiB"


def plan_for(paths: Paths, answers: Answers, lock_text: str) -> dict[str, Any]:
    """The plan of the choices so far, as bootstrap writes it from host_vars.

    The host_vars are the file the installer would write for these answers:
    over the machine's current file when there is one (modify mode keeps its
    other settings), else over the example.

    Args:
        paths: The installer's paths.
        answers: The answers being collected.
        lock_text: ``models.lock.yml`` (with the models pinned on the screen).

    Returns:
        The plan.

    Raises:
        PlanError: As the plan writer refuses the same host_vars.
    """
    safe = YAML(typ="safe")
    current_file = paths.host_vars(answers.machine.name) if answers.machine.name else None
    current = (
        current_file.read_text(encoding="utf-8")
        if current_file is not None and current_file.is_file()
        else None
    )
    example = paths.example_host_vars.read_text(encoding="utf-8")
    variables = safe.load(hostvars.render(answers, current, example)) or {}
    return plan_mod.plan_of(paths, variables, safe.load(lock_text) or {})


@dataclass(frozen=True)
class Answer:
    """Preflight's answer to the plan of the choices.

    Attributes:
        profile: Preflight's host profile of the plan (empty without one).
        refused: The plan writer's refusal, or empty.
        failed: Why preflight gave no profile (its exit code and last error
            line), or empty.
    """

    profile: dict[str, Any] = field(default_factory=dict)
    refused: str = ""
    failed: str = ""


def run(paths: Paths, host: Host, answers: Answers, lock_text: str, args: Sequence[str]) -> Answer:
    """Preflight's answer to the plan of the choices so far.

    Args:
        paths: The installer's paths.
        host: The host layer that runs preflight.
        answers: The answers (a copy: this runs in a worker).
        lock_text: ``models.lock.yml`` (with the models pinned on the screen).
        args: Bootstrap's driver options for these answers (``Wizard.plan_args``).

    Returns:
        Preflight's host profile; or the plan writer's refusal; or, when
        preflight wrote no profile, why.
    """
    try:
        found = plan_for(paths, answers, lock_text)
        text = json.dumps(found)
    except plan_mod.PlanError as exc:
        return Answer(refused=str(exc))
    except (ValueError, OverflowError) as exc:
        # A number too large to plan with or to write (a field holds hundreds or
        # thousands of digits): a refusal, never a crash of the worker, which would end
        # the installer.
        return Answer(refused=f"a value is too large to plan with ({exc})")
    with tempfile.TemporaryDirectory(prefix="pinka-capacity-") as tmp:
        plan_file = Path(tmp) / "plan.json"
        plan_file.write_text(text, encoding="utf-8")
        result = host.preflight(["--plan", str(plan_file), "--offline", *args])
    if result.profile:
        return Answer(result.profile)
    last = next((line for line in reversed(result.stderr.splitlines()) if line.strip()), "")
    return Answer(
        failed=f"preflight gave no answer (exit {result.exit_code})" + (f": {last}" if last else "")
    )


def titles(table: Sequence[Row]) -> list[str]:
    """Each row's title: its label, its bar and its line, the bars in one column.

    Args:
        table: :func:`rows`.

    Returns:
        One title per row.
    """
    width = max((len(r.label) for r in table), default=0)
    return [f"{r.label:<{width}} {r.bar()} {r.line}" for r in table]


def lines(answer: Answer, consents: Collection[str], accepted: Collection[str]) -> list[str]:
    """The budget as text lines: one per resource, the growth, and why it waits.

    Args:
        answer: Preflight's answer to the plan.
        consents: The operator's consents so far (``Wizard.consents``).
        accepted: The degraded lines the operator accepted (``Wizard.degraded_accepted``).

    Returns:
        Lines.
    """
    found = titles(rows(answer.profile))
    if growth(answer.profile):
        found.append(growth(answer.profile))
    if waiting := waits(answer, consents, accepted):
        found.append(f"Waits: {waiting}")
    return found


def _fill(need: float | None, have: float | None) -> float:
    if need is None or not have:
        return 0.0
    return need / have


def _status(status: str, shortfall: float | None) -> str:
    """The status, first in a line (a narrow terminal cuts a long line at its end)."""
    word = STATUS_WORDS[status]
    if status in ("degraded", "does_not_fit") and shortfall:
        word += f", {gib(up(shortfall))} short"
    return word


def _short(status: str) -> bool:
    return status in ("degraded", "does_not_fit")


def _need(status: str, value: float) -> float:
    """A need as printed: rounded up where the resource falls short, else to the nearest."""
    return up(value) if _short(status) else value


def _has(status: str, value: float | None) -> float | None:
    """What the host has, as printed: rounded down where it falls short, else to the nearest."""
    return down(value) if value is not None and _short(status) else value


def _memory(req: Mapping[str, Any]) -> Row:
    budget = req["budgets"]["memory"]
    total, required = budget["total_gib"], budget["required_gib"]
    recommended = budget["recommended_gib"]
    status, tolerance = budget["status"], budget["tolerance"]
    line = (
        f"{_status(status, budget['shortfall_gib'])} · needs {_need(status, required):.1f}-"
        f"{_need(status, recommended):.1f} GiB (compared at {tolerance:g}), has "
        f"{gib(_has(status, total))}"
    )
    items = (
        f"each need is compared at {tolerance:g} times its nominal value (configured)",
        *(
            f"{i['title']}: {i['required_gib']:g} GiB, {i['recommended_gib']:g} GiB recommended"
            f" ({i['basis']})"
            for i in req["memory_gib"]["items"]
        ),
    )
    return Row("memory", "RAM", status, _fill(recommended, total), line, items)


def _disk_item(item: Mapping[str, Any], short: bool) -> str:
    """One item: its size rounded up where the mount point falls short (a need), as preflight."""
    size = up(item["size_gib"]) if short else item["size_gib"]
    text = f"{item['title']}: {gib(size)} ({item['basis']})"
    if item["present"]:
        text += ", on the host already"
    elif item["missing_gib"]:
        text += f", missing {gib(up(item['missing_gib']))}"
    return text


def _disks(req: Mapping[str, Any]) -> list[Row]:
    rows = []
    items_by_mount = {m["mount"]: m["items"] for m in req["disk"]}
    for budget in req["budgets"]["disk"]:
        mount, status = budget["mount"], budget["status"]
        need, free = budget["required_gib"], budget["free_gib"]
        line = (
            f"{_status(status, budget['shortfall_gib'])} · needs {gib(_need(status, need))}, "
            f"has {gib(_has(status, free))} free"
        )
        items = tuple(_disk_item(i, _short(status)) for i in items_by_mount.get(mount, []))
        rows.append(Row(f"disk:{mount}", f"Disk {mount}", status, _fill(need, free), line, items))
    return rows


def _vram_item(item: Mapping[str, Any]) -> str:
    if item.get("mib") is not None:
        return f"{item['title']}: {item['mib']:g} MiB ({item['basis']})"
    return f"{item['title']}: {gib(up(item['gib']))} ({item['basis']})"


def _vram(req: Mapping[str, Any]) -> list[Row]:
    vram = req["budgets"].get("vram") or {}
    rows = []
    for use in ("serving", "training"):
        line_data = vram.get(use)
        if not line_data:
            continue
        status = line_data["status"]
        have_mib, have_gib = line_data.get("available_mib"), line_data.get("available_gib")
        need = line_data["needed_gib"]
        parts = line_data["items"]
        if len(parts) == 1 and parts[0].get("mib") is not None and have_mib is not None:
            # A configured minimum, stated in MiB as the GPU reports its memory.
            needs, have = f"{parts[0]['mib']:g} MiB", f"{have_mib:g} MiB"
        else:
            needs, have = gib(up(need)), gib(_has(status, have_gib))
        word = _status(status, line_data["shortfall_gib"])
        if line_data["reason"]:
            word += f": {line_data['reason']}"
        if status == "off":
            line = f"{word} · " + (
                f"has {have_mib:g} MiB" if have_mib is not None else "no usable GPU memory"
            )
        elif have_mib is None:
            line = f"{word} · needs {needs}, no usable GPU memory"
        else:
            line = f"{word} · needs {needs}, has {have}"
        # The reason in full where a narrow title is cut.
        reason = (f"{STATUS_WORDS[status]}: {line_data['reason']}",) if line_data["reason"] else ()
        items = reason + tuple(_vram_item(i) for i in parts)
        fill = _fill(need, have_gib) if status != "off" else 0.0
        rows.append(Row(f"vram:{use}", f"VRAM {use}", status, fill, line, items))
    return rows


def rows(profile: Mapping[str, Any]) -> list[Row]:
    """The budget's rows: RAM, each disk mount point, VRAM serving and training.

    Args:
        profile: Preflight's host profile of the plan.

    Returns:
        The rows; none when the profile has no requirements.
    """
    req = profile.get("requirements") or {}
    if not req.get("budgets"):
        return []
    return [_memory(req), *_disks(req), *_vram(req)]


def growth(profile: Mapping[str, Any]) -> str:
    """The expected growth below the research space and when the disk fills.

    Args:
        profile: Preflight's host profile of the plan.

    Returns:
        One line, or empty.
    """
    found = (profile.get("requirements") or {}).get("growth")
    if not found:
        return ""
    text = (
        f"Growth on {found['mount']}: {found['gib_per_day']:g} GiB a day, "
        f"{found['gib_per_week']:g} GiB a week (estimated); "
    )
    runs = any(i.get("per_run_gib") is not None for i in found.get("items", []))
    on_top = " (training runs come on top)" if runs else ""
    days = found["days_until_full"]
    if days is None and not found["gib_per_day"]:
        return text + "nothing grows there" + on_top
    if days is None and found["free_after_install_gib"] is None:
        return text + "when the disk fills is not known: its free space is not measured"
    if days is None:
        return (
            text
            + "the disk does not fill within ten years, the retention's limits counted"
            + on_top
        )
    when = ""
    stamp = str(profile.get("generated_at") or "")
    try:
        start = datetime.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").date()
    except ValueError:
        pass
    else:
        when = f", around {start + datetime.timedelta(days=int(days))}"
    return text + f"full in about {days} days at this rate{when}{on_top}"


def reasons(profile: Mapping[str, Any]) -> list[str]:
    """What preflight refused, asks for and degraded, as its summary words it.

    Args:
        profile: Preflight's host profile of the plan.

    Returns:
        The unsupported reasons, the actions needed, the degraded reasons and
        the disabled features: the lines of the summary's sections of the same
        names.
    """
    return [
        *profile.get("unsupported_reasons", []),
        *(f"{a['id']}: {a['action']}" for a in profile.get("needs_action", [])),
        *degraded(profile),
    ]


def degraded(profile: Mapping[str, Any]) -> list[str]:
    """What preflight finds degraded: the lines a consent to ``--accept-degraded`` covers.

    Args:
        profile: A host profile (the Preflight screen's, or the plan's).

    Returns:
        The degraded reasons, then the disabled features, as the summary words them.
    """
    return [
        *profile.get("degraded_reasons", []),
        *(
            f"{d['feature']}: {d['impact']} {d.get('remediation') or ''}".rstrip()
            for d in profile.get("disabled_features", [])
        ),
    ]


def needs_consent(profile: Mapping[str, Any]) -> bool:
    """Whether going on needs the operator's consent: a degraded outcome, as bootstrap asks."""
    return profile.get("outcome") == "degraded"


def waits(answer: Answer, consents: Collection[str], accepted: Collection[str]) -> str:
    """Why Next waits, or empty; it follows bootstrap's preflight stage on this answer.

    A refused plan; no answer; a resource that does not fit (also on a host
    forced on the Preflight screen: the approved design); an unsupported
    outcome the operator did not force there; an action preflight asks for
    (forced or not); a degraded outcome without the consent to each of its
    lines.

    Args:
        answer: Preflight's answer to the plan.
        consents: The operator's consents so far (``Wizard.consents``).
        accepted: The degraded lines the operator accepted (``Wizard.degraded_accepted``).

    Returns:
        The reason, or empty.
    """
    profile = answer.profile
    outcome = profile.get("outcome")
    short = [r.label for r in rows(profile) if r.status == "does_not_fit"]
    consented = "accept_degraded" in consents and set(degraded(profile)) <= set(accepted)
    stops = (
        (bool(answer.refused), f"the plan is refused: {answer.refused}"),
        (bool(answer.failed), answer.failed),
        (bool(short), "does not fit: " + ", ".join(short)),
        (
            outcome == "unsupported" and "force_unsupported" not in consents,
            "preflight finds the host unsupported with these choices",
        ),
        (bool(profile.get("needs_action")), "preflight asks for an action first"),
        (needs_consent(profile) and not consented, "what is degraded needs your consent"),
    )
    return next((text for stops_here, text in stops if stops_here), "")
