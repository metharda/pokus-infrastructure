"""Requirements computed from the operator's choices.

``requirements.json`` (compiled from ``requirements.yml`` by ``make generate``)
says what each part of the lab needs; the plan (``--plan``, else the configured
defaults) says what the operator chose. This module adds them up:

- memory against the floor and the computed required and recommended values,
  each compared with ``MemTotal`` at the support matrix's tolerance;
- the disk known at install, per mount point, item by item (a shortfall is
  listed with what is missing where), with the free space a loop pool's file
  leaves beside it as an item of its own;
- the Incus pool: the sum of everything that may be written to it
  (``pool_parts``), which ``incus_pool_size: auto`` is and an explicit size
  must hold;
- the expected growth of what grows with use, which is never a reason to
  refuse.

An item already on the host (a pulled image, a downloaded model, a built
golden image, the pool's loop file) takes no more space: it is listed with its
real size where that is known and counts nothing; so does the free space
beside a loop file that exists already. Every host access goes
through the ``fs``/``runner`` abstractions.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Collection, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from pinka_preflight import schema_check, vram
from pinka_preflight.detect.hardware import DiskInfo, disk_info
from pinka_preflight.fs import FileSystem
from pinka_preflight.runner import EXIT_NOT_FOUND, Runner

PREFLIGHT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUIREMENTS_JSON = os.path.join(PREFLIGHT_DIR, "requirements.json")
PLAN_SCHEMA = os.path.join(PREFLIGHT_DIR, "schema", "plan.schema.json")
# A lab node serves nothing itself (the installer's plan writer says the same).
LAB_ON = (
    "a lab node serves nothing itself and refuses inference: on; set inference: on on the "
    "paired inference node instead, whose preflight judges its GPU and model"
)
# An inference node exists only to serve (the installer's plan writer and its
# answers check say the same).
INFERENCE_NODE_OFF = (
    "an inference node runs only the inference stack; it needs inference: auto or on"
)
GIB = 1024**3
# Container image platforms by CPU architecture (platform.machine()).
PLATFORMS = {"x86_64": "linux/amd64", "aarch64": "linux/arm64"}
# Where pinka itself lives (the pinned CPython and its environments).
PINKA_DIR = "/opt/pinka"
# How far the growth looks ahead for the day the disk fills.
HORIZON_DAYS = 3650
LAB_ROLES = ("full", "lab")
# How many of the largest parts a degraded message names.
LARGEST = 3
# Below this a size is whole GiB (float sums of tenths).
EPSILON = 1e-9
# The disk item of the free space a loop pool's file leaves beside it: it
# stays free, so the growth does not count it as used.
RESERVE_ID = "pool-reserve"


class PlanError(ValueError):
    """The plan cannot be used: unreadable, or not matching its schema."""


@dataclass
class MemoryItem:
    """One part of the memory requirement.

    Attributes:
        id: Component id of ``requirements.yml``.
        title: What it is.
        required_gib: Its share of the required memory.
        recommended_gib: Its share of the recommended memory.
        cpu_threads: Its estimated CPU use (information only).
        detail: How the numbers follow from the choices.
        basis: ``estimated`` or ``measured``.
        source: Where the numbers come from.
    """

    id: str
    title: str
    required_gib: float
    recommended_gib: float
    cpu_threads: float
    detail: str
    basis: str
    source: str


@dataclass
class DiskItem:
    """One item of the disk known at install.

    Attributes:
        id: Such as ``image:vllm`` or ``model:qwen3-8b-awq``.
        title: What it is.
        path: Where it goes.
        mount: The mount point that holds the path.
        size_gib: Its size (estimated, or the real size when present).
        present: Already on the host: it needs no more space.
        missing_gib: The part of it that does not fit on its mount point.
        basis: ``estimated``, ``measured``, or ``configured`` (the operator's own
            number, such as the pool's size).
        source: Where the size comes from.
    """

    id: str
    title: str
    path: str
    mount: str
    size_gib: float
    present: bool
    missing_gib: float
    basis: str
    source: str


@dataclass
class MountCheck:
    """The disk known at install on one mount point.

    Attributes:
        mount: The mount point.
        fstype: Its file system type.
        free_gib: Its free space, or None if it could not be measured.
        required_gib: The items not yet on the host.
        shortfall_gib: How much is missing (0 when it fits).
        items: The items.

    The comparison is exact (bytes); the stored sizes are rounded to 0.01
    GiB: to the nearest where the mount point fits, and where it falls short
    the need up, the free space down and the shortfall up, at least 0.01, so
    a stored or printed figure never shows a fit where preflight refuses.
    """

    mount: str
    fstype: str | None
    free_gib: float | None
    required_gib: float
    shortfall_gib: float
    items: list[DiskItem] = field(default_factory=list)


@dataclass
class GrowthItem:
    """Something that grows with use.

    Attributes:
        id: Growth id of ``requirements.yml``.
        title: What it is.
        gib_per_day: Its growth per day.
        cap_gib: Where its retention stops it, or None if nothing does.
        per_run_gib: The most one training run adds, for per-run items.
        basis: ``estimated`` or ``measured``.
        source: Where the rate comes from.
    """

    id: str
    title: str
    gib_per_day: float
    cap_gib: float | None
    per_run_gib: float | None
    basis: str
    source: str


@dataclass
class Growth:
    """The expected growth below the research space.

    Attributes:
        mount: The mount point of the research space.
        free_after_install_gib: Its free space once the install's items are there.
        gib_per_day: The growth per day while every store fills.
        gib_per_week: Seven days of it.
        days_until_full: The day the disk fills at that rate, or None if
            nothing grows, if the free space is not measured, or if the stores
            (the retention's limits counted) stay below it for ten years.
        items: What grows.
    """

    mount: str | None
    free_after_install_gib: float | None
    gib_per_day: float
    gib_per_week: float
    days_until_full: int | None
    items: list[GrowthItem] = field(default_factory=list)


@dataclass
class Switches:
    """The feature switches of the plan on this host.

    Attributes:
        inference: The inference switch: ``auto`` or ``off``.
        serves: vLLM, its images and the selected model are installed and counted.
        inference_reason: Why inference is on or off here.
        training: The training switch: ``auto``, ``cpu`` or ``off``.
        runs: Where training jobs run: ``gpu``, ``cpu`` (the CPU-only variant) or ``off``.
        training_reason: Why.
        memory_max_gib: The training cap (``training_caps.memory_max``).
        memory_max_basis: ``computed`` (``memory_max: auto``) or ``configured``.
        memory_max_detail: How the cap follows from the host or the setting.
    """

    inference: str
    serves: bool
    inference_reason: str
    training: str
    runs: str
    training_reason: str
    memory_max_gib: float = 0.0
    memory_max_basis: str = "configured"
    memory_max_detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        """The host profile's ``requirements.switches``.

        Returns:
            JSON-compatible data.
        """
        return {
            "inference": {
                "mode": self.inference,
                "serves": self.serves,
                "reason": self.inference_reason,
            },
            "training": {
                "mode": self.training,
                "runs": self.runs,
                "reason": self.training_reason,
                "memory_max": size_text(self.memory_max_gib),
                "memory_max_gib": gib(self.memory_max_gib),
                "memory_max_basis": self.memory_max_basis,
                "memory_max_detail": self.memory_max_detail,
            },
        }

    def unwanted(self) -> dict[str, str]:
        """The GPU features the switches turn off, with the switch.

        Returns:
            Feature name to the setting that turns it off: ``gpu_inference``
            with ``inference: off``, ``gpu_training`` with ``training: off``
            or ``cpu`` (the CPU-only variant was chosen).
        """
        found = {}
        if self.inference == "off":
            found["gpu_inference"] = "inference: off"
        if self.training in ("off", "cpu"):
            found["gpu_training"] = f"training: {self.training}"
        return found

    def effective(self, features: Mapping[str, bool]) -> dict[str, bool]:
        """The feature flags after the switches: what the lab uses on this node.

        ``gpu_inference`` only where vLLM serves, ``gpu_training`` only where
        jobs run on the GPU; the play applies the same flags (preflight gate).

        Args:
            features: The detected feature flags.

        Returns:
            The flags with the two GPU features switched.
        """
        return {
            **features,
            "gpu_inference": self.serves,
            "gpu_training": self.runs == "gpu",
        }


@dataclass
class Requirements:
    """The requirements of the plan on this host.

    Attributes:
        plan: ``defaults``, or the plan file's path.
        choices: The effective plan.
        memory_total_gib: ``MemTotal``.
        floor_gib: The memory floor (nominal).
        required_gib: The required memory (nominal; never below the floor).
        recommended_gib: The recommended memory (nominal).
        tolerance: The fraction of each nominal threshold ``MemTotal`` is compared with.
        switches: The feature switches on this host and the training cap.
        memory_items: The memory breakdown.
        cpu_threads: The CPU estimate of the running parts (information).
        disks: The disk known at install, per mount point.
        growth: What grows with use.
        pool: The Incus pool's size (``source``, ``size_gib``, ``basis``
            ``computed`` or ``configured``, ``detail``); empty on an inference node.
        unsupported: Why the plan does not fit (memory below the floor or the
            required value, a disk shortfall, ``inference: on`` that cannot serve).
        needs_action: Operator actions before anything changes (an Incus pool
            smaller than the sum of everything that may be written to it), as
            ``{"id", "action"}``.
        degraded: Why it fits only with consent (memory below the recommended
            value, ``inference: auto`` that cannot serve).
        vram_serving: The serving line (None on a lab node).
        vram_training: The training line (None where training is unavailable).
        serving: Which serving profiles and models of models.catalog.yml fit the GPU.
        warnings: What could not be checked.
        notes: Information about the plan.
    """

    plan: str
    choices: dict[str, Any]
    memory_total_gib: float | None
    floor_gib: float
    required_gib: float
    recommended_gib: float
    tolerance: float
    switches: Switches
    memory_items: list[MemoryItem] = field(default_factory=list)
    cpu_threads: float = 0.0
    disks: list[MountCheck] = field(default_factory=list)
    growth: Growth | None = None
    pool: dict[str, Any] = field(default_factory=dict)
    unsupported: list[str] = field(default_factory=list)
    needs_action: list[dict[str, str]] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    vram_serving: vram.VramLine | None = None
    vram_training: vram.VramLine | None = None
    serving: dict[str, Any] = field(default_factory=dict)

    def budgets(self) -> dict[str, Any]:
        """One status per resource: memory, disk per mount point, and VRAM.

        Returns:
            ``memory`` (``fits``, ``degraded``, ``does_not_fit`` or
            ``unknown``), ``disk`` per mount point (``fits``,
            ``does_not_fit`` or ``unknown``) and ``vram`` with its serving and
            training lines (``fits``, ``does_not_fit``, ``off`` or ``unknown``);
            each with what is needed, what there is and the shortfall.
        """
        total, tol = self.memory_total_gib, self.tolerance
        short = 0.0
        if total is None:
            status = vram.UNKNOWN
        elif any(r.startswith("memory:") for r in self.unsupported):
            status, short = vram.DOES_NOT_FIT, tol * self.required_gib - total
        elif any(r.startswith("memory:") for r in self.degraded):
            status, short = "degraded", tol * self.recommended_gib - total
        else:
            status = vram.FITS
        disks = [
            {
                "mount": m.mount,
                "status": (
                    vram.UNKNOWN
                    if m.free_gib is None
                    else vram.DOES_NOT_FIT
                    if m.shortfall_gib > 0
                    else vram.FITS
                ),
                "required_gib": m.required_gib,
                "free_gib": m.free_gib,
                "shortfall_gib": m.shortfall_gib,
            }
            for m in self.disks
        ]
        lines = [self.vram_serving, self.vram_training]
        gpu = next((line.available_gib for line in lines if line is not None), None)
        gpu_mib = next((line.available_mib for line in lines if line is not None), None)
        return {
            "memory": {
                "status": status,
                "total_gib": None if total is None else gib(total),
                "required_gib": self.required_gib,
                "recommended_gib": self.recommended_gib,
                "tolerance": tol,
                "shortfall_gib": gib(max(0.0, short)),
            },
            "disk": disks,
            "vram": {
                "gpu_gib": gpu,
                "gpu_mib": gpu_mib,
                "serving": None if self.vram_serving is None else self.vram_serving.as_dict(),
                "training": None if self.vram_training is None else self.vram_training.as_dict(),
            },
        }

    def as_dict(self) -> dict[str, Any]:
        """The host profile's view (``requirements`` without ``core`` and ``minimums``).

        Returns:
            JSON-compatible data.
        """
        return {
            "plan": self.plan,
            "choices": self.choices,
            "switches": self.switches.as_dict(),
            "memory_gib": {
                "total": None if self.memory_total_gib is None else gib(self.memory_total_gib),
                "floor": self.floor_gib,
                "required": self.required_gib,
                "recommended": self.recommended_gib,
                "tolerance": self.tolerance,
                "items": [asdict(i) for i in self.memory_items],
            },
            "cpu_threads": {"estimated": self.cpu_threads},
            "disk": [asdict(m) for m in self.disks],
            "growth": asdict(self.growth) if self.growth else None,
            "pool": dict(self.pool) or None,
            "budgets": self.budgets(),
            "serving": dict(self.serving) or None,
            "notes": list(self.notes),
        }


def gib(value: float) -> float:
    """Round a size in GiB for the report.

    Args:
        value: GiB.

    Returns:
        Rounded to two decimals.
    """
    return round(value, 2)


def text_gib(value: float) -> str:
    """A size for a message.

    Args:
        value: GiB.

    Returns:
        Such as ``5.7 GiB``.
    """
    return f"{value:.1f} GiB"


def round_up(value: float, digits: int) -> float:
    """Round up to ``digits`` decimals (a size that must not be understated).

    Args:
        value: The value.
        digits: Decimals.

    Returns:
        The value rounded up, ignoring float noise below a billionth of a step
        (far below a byte, so a shortfall of one byte still counts).
    """
    scale = 10.0**digits
    return math.ceil(round(value * scale, 9)) / scale


def round_down(value: float, digits: int) -> float:
    """Round down to ``digits`` decimals (free space that must not be overstated).

    Args:
        value: The value.
        digits: Decimals.

    Returns:
        The value rounded down, ignoring float noise below a billionth of a step
        (far below a byte).
    """
    scale = 10.0**digits
    return math.floor(round(value * scale, 9)) / scale


def disk_texts(needed: float, free: float | None, shortfall: float) -> tuple[str, str, str]:
    """A mount point's need, free space and shortfall as every disk message prints them.

    One rule for the refusal, the summary and the installer's capacity plan:
    where the mount point falls short, the need is rounded up, the free space
    down and the shortfall up, to a tenth of a GiB; where it fits, both to the
    nearest tenth.

    Args:
        needed: What the items not yet on the host need (GiB).
        free: The free space (GiB), or None if not measured.
        shortfall: How much is missing (GiB; 0 when it fits).

    Returns:
        ``(need, free, shortfall)``, such as ``("94.6 GiB", "94.5 GiB",
        "0.1 GiB")``; the shortfall is empty when it fits, and the free space
        ``-`` when it is not measured.
    """
    if shortfall <= 0:
        return text_gib(needed), "-" if free is None else text_gib(free), ""
    shown_free = "-" if free is None else text_gib(round_down(free, 1))
    return text_gib(round_up(needed, 1)), shown_free, text_gib(round_up(shortfall, 1))


def disk_size_text(size: float, short: bool) -> str:
    """An item's size (or a group's) on a mount point, as every disk message prints it.

    Args:
        size: GiB.
        short: Whether the mount point falls short: the size is a need then,
            rounded up to a tenth like the total; else to the nearest tenth.

    Returns:
        Such as ``0.1 GiB`` for an image of 0.03 GiB where the space falls short.
    """
    return text_gib(round_up(size, 1) if short else size)


def size_text(value: float) -> str:
    """A size as ``training_caps`` writes it.

    Args:
        value: GiB.

    Returns:
        Such as ``17G``, or ``512M`` for a size that is not whole GiB.
    """
    if abs(value - round(value)) < EPSILON:
        return f"{round(value)}G"
    return f"{round(value * 1024)}M"


def load_requirements(path: str = REQUIREMENTS_JSON) -> dict[str, Any]:
    """Load the compiled requirements.

    Args:
        path: Path of ``requirements.json``.

    Returns:
        The data.
    """
    with open(path, encoding="utf-8") as handle:
        data: dict[str, Any] = json.load(handle)
    return data


def load_plan(
    path: str | None,
    defaults: Mapping[str, Any],
    srv_path: str | None = None,
    *,
    profiles: Collection[str] = (),
    blueprints: Collection[str] = (),
) -> tuple[dict[str, Any], str]:
    """The effective plan: the defaults, the research space given, then the file's keys.

    Args:
        path: The plan file (``--plan``), or None for the defaults.
        defaults: The configured defaults (``requirements.json`` ``defaults``).
        srv_path: The research space from ``--srv-path``, if given.
        profiles: The serving profiles (``requirements.json`` ``serving``); the
            plan's ``vllm_profile`` must be one of them where given.
        blueprints: The golden image blueprints (``requirements.json``
            ``blueprints``); each of the plan's ``images`` must be one where given.

    Returns:
        ``(plan, label)``; the label is ``defaults`` or the path.

    Raises:
        PlanError: If the file is unreadable, the plan does not match its
            schema, or it names a serving profile or a blueprint that does not
            exist.
    """
    plan = json.loads(json.dumps(defaults))
    if srv_path:
        plan["srv_path"] = srv_path
    label = "defaults"
    if path:
        try:
            with open(path, encoding="utf-8") as handle:
                given = json.load(handle)
        except (OSError, ValueError) as exc:
            raise PlanError(f"--plan {path}: {exc}") from exc
        if not isinstance(given, dict):
            raise PlanError(f"--plan {path}: must contain a JSON object")
        plan.update(given)
        label = path
    with open(PLAN_SCHEMA, encoding="utf-8") as handle:
        schema = json.load(handle)
    errors = schema_check.validate(plan, schema)
    if errors:
        raise PlanError(f"plan ({label}): " + "; ".join(errors))
    if plan["node_role"] == "inference" and plan["inference"] == "off":
        raise PlanError(f"plan ({label}): {INFERENCE_NODE_OFF}")
    if plan["node_role"] == "lab" and plan["inference"] == "on":
        raise PlanError(f"plan ({label}): {LAB_ON}")
    if profiles and str(plan["vllm_profile"]) not in profiles:
        raise PlanError(f"plan ({label}): {unknown_profile(plan['vllm_profile'], profiles)}")
    for name in plan["images"] if blueprints else ():
        if str(name) not in blueprints:
            raise PlanError(f"plan ({label}): {unknown_blueprint(name, blueprints)}")
    return plan, label


def unknown_blueprint(name: object, blueprints: Collection[str]) -> str:
    """The refusal of a golden image blueprint that does not exist.

    The installer's ``validators.unknown_blueprint`` says the same.

    Args:
        name: The blueprint named.
        blueprints: The blueprints (``images/``).

    Returns:
        The message.
    """
    return (
        f"golden image blueprint {str(name)!r} does not exist "
        f"(images/: {', '.join(sorted(blueprints))})"
    )


def unknown_profile(profile: object, profiles: Collection[str]) -> str:
    """The refusal of a serving profile that does not exist.

    The installer's ``validators.unknown_profile`` says the same.

    Args:
        profile: The plan's ``vllm_profile``.
        profiles: The serving profiles.

    Returns:
        The message.
    """
    return (
        f"vllm_profile {str(profile)!r} is not a serving profile "
        f"(vllm/profiles/: {', '.join(sorted(profiles))})"
    )


def switches(plan: Mapping[str, Any], features: Mapping[str, bool], misfit: str = "") -> Switches:
    """What the switches leave on, on this node with these features.

    Args:
        plan: The effective plan.
        features: The detected feature flags.
        misfit: Why the serving profile or the default model does not fit the
            GPU's memory (empty when it fits). Nothing is served then, under
            any switch: ``on`` (and an inference node) also makes preflight
            refuse the host, ``auto`` on a full node leaves it degraded.

    Returns:
        The switches; the training cap is filled by :func:`compute`.
    """
    role = str(plan["node_role"])
    inference = str(plan["inference"])
    if role == "lab":
        serves, why = False, "a lab node serves no inference"
    elif inference == "off":
        serves, why = False, "switched off, inference: off"
    elif not features.get("gpu_inference"):
        serves, why = False, "gpu_inference is not available on this host"
    elif misfit:
        serves, why = False, f"{misfit}, inference: {inference}"
    else:
        serves, why = True, f"gpu_inference on a {role} node, inference: {inference}"
    training = str(plan["training"]["mode"])
    if role != "full":
        runs, reason = "off", f"only a full node trains, node_role {role}"
    elif training == "off":
        runs, reason = "off", "switched off, training: off"
    elif features.get("gpu_training"):
        runs, reason = "gpu", f"gpu_training on this host, training: {training}"
    elif training == "cpu":
        runs, reason = "cpu", "the CPU-only variant without a usable GPU, training: cpu"
    else:
        runs, reason = (
            "off",
            "no usable GPU for training; training: cpu would run the CPU-only variant",
        )
    return Switches(inference, serves, why, training, runs, reason)


def _training_cap(
    data: Mapping[str, Any],
    plan: Mapping[str, Any],
    others: Sequence[MemoryItem],
    memory_gib: float | None,
    sw: Switches,
) -> None:
    """Fill the training cap: the configured one, or computed from the host.

    ``others`` are the parts that run next to a job: the core stack and the
    targets, never vLLM, which the arbiter stops for a job.
    """
    configured = plan["training"]["memory_max_gib"]
    if configured is not None:
        sw.memory_max_gib = float(configured)
        sw.memory_max_basis = "configured"
        sw.memory_max_detail = f"training_caps.memory_max {size_text(float(configured))}"
        return
    spec = data["training_cap"]
    low, high = int(spec["floor_gib"]), int(spec["ceiling_gib"])
    sw.memory_max_basis = "computed"
    if memory_gib is None:
        sw.memory_max_gib = float(low)
        sw.memory_max_detail = f"MemTotal could not be read: the floor of {low} GiB"
        return
    # Rounded: the parts are tenths, and Python 3.12's sum() is compensated
    # where 3.9's is not.
    rest = gib(sum(i.recommended_gib for i in others))
    left = memory_gib - rest
    sw.memory_max_gib = float(min(high, max(low, math.floor(left + EPSILON))))
    sw.memory_max_detail = (
        f"MemTotal {memory_gib:.2f} GiB - {text_gib(rest)} recommended for the core stack and "
        f"the targets = "
        f"{text_gib(left)}, rounded down to whole GiB within {low} and {high} GiB"
    )


def _memory_items(
    data: Mapping[str, Any],
    plan: Mapping[str, Any],
    features: Mapping[str, bool],
    notes: list[str],
    *,
    sw: Switches,
    memory_gib: float | None,
) -> list[MemoryItem]:
    """The memory breakdown; ``features`` are the flags after the switches."""
    role = str(plan["node_role"])
    targets = data["target_memory_gib"]
    items: list[MemoryItem] = []
    vllm: MemoryItem | None = None
    training: Mapping[str, Any] | None = None
    for comp in data["components"]:
        if role not in comp["roles"]:
            continue
        kind = comp["kind"]
        common = {
            "id": comp["id"],
            "title": comp["title"],
            "cpu_threads": float(comp["cpu_threads"]),
            "basis": comp["basis"],
            "source": comp["source"],
        }
        if kind == "fixed":
            mem = comp["memory_gib"]
            items.append(
                MemoryItem(
                    required_gib=float(mem["required"]),
                    recommended_gib=float(mem["recommended"]),
                    detail="",
                    **common,
                )
            )
        elif kind == "targets":
            count = int(plan["concurrent_targets"])
            probe = float(targets["probe_client"])
            big = "vm" if features.get("vm_targets") else "container"
            required = count * (float(targets["container"]) + probe)
            recommended = count * (float(targets[big]) + probe)
            detail = (
                f"{count} x (a {text_gib(targets['container'])} container"
                + (f" or a {text_gib(targets['vm'])} VM" if big == "vm" else "")
                + f" + a {text_gib(probe)} probe client)"
            )
            if plan["images"]:
                build = float(targets["build"])
                required, recommended = max(required, build), max(recommended, build)
                detail += f"; at least the {text_gib(build)} build instance"
            common["cpu_threads"] = float(comp["cpu_threads"]) * count
            items.append(
                MemoryItem(
                    required_gib=required, recommended_gib=recommended, detail=detail, **common
                )
            )
        elif kind == "vllm":
            if not features.get("gpu_inference"):
                continue
            model = plan.get("model")
            if not model:
                notes.append(
                    "no model selected: vLLM is installed and uses no memory until a "
                    "deployment is served"
                )
                continue
            size = int(model["size_bytes"]) / GIB
            over = comp["overhead_gib"]
            common["title"] = f"vLLM serving {model['id']}"
            vllm = MemoryItem(
                required_gib=size + float(over["required"]),
                recommended_gib=size + float(over["recommended"]),
                detail=f"the model's files ({text_gib(size)}) + "
                f"{text_gib(over['required'])} to {text_gib(over['recommended'])}",
                **common,
            )
            items.append(vllm)
        elif kind == "training":
            training = comp
    # The cap follows the core stack and the targets. vLLM does not count:
    # the arbiter never runs it and a job at once, so the recommended value
    # takes the larger of the two (_add_training), never their sum.
    _training_cap(data, plan, [i for i in items if i is not vllm], memory_gib, sw)
    if training is not None and sw.runs != "off":
        _add_training(items, vllm, training, sw)
    return items


def _add_training(
    items: list[MemoryItem], vllm: MemoryItem | None, training: Mapping[str, Any], sw: Switches
) -> None:
    """A training job at the cap; with vLLM, the larger of the two (one at a time)."""
    cap = sw.memory_max_gib
    how = f"training_caps.memory_max {size_text(cap)}" + (
        " (auto, computed from this host)" if sw.memory_max_basis == "computed" else ""
    )
    variant = " (CPU-only variant)" if sw.runs == "cpu" else ""
    if vllm is not None:
        vllm.recommended_gib = max(vllm.recommended_gib, cap)
        vllm.title += " or a training job"
        vllm.detail += f"; or a training job{variant} at {how}: the GPU arbiter runs one at a time"
        vllm.source += f"; {training['source']}"
        return
    items.append(
        MemoryItem(
            id=str(training["id"]),
            title=str(training["title"]) + variant,
            required_gib=0.0,
            recommended_gib=cap,
            cpu_threads=float(training["cpu_threads"]),
            detail=how,
            basis=str(training["basis"]),
            source=str(training["source"]),
        )
    )


def _fits(
    items: Sequence[MemoryItem],
    available: float,
    plan: Mapping[str, Any],
    targets: Mapping[str, Any],
    features: Mapping[str, bool],
    *,
    recommended: bool,
    cap: float | None,
    serving_gib: float = 0.0,
) -> list[str]:
    """The choices that would fit ``available`` nominal GiB.

    ``cap`` is the training cap of a job that counts, None when training is
    off here; ``serving_gib`` is what vLLM itself recommends: below it a lower
    training cap does not help.
    """

    def total(skip: str) -> float:
        return sum(
            (i.recommended_gib if recommended else i.required_gib) for i in items if i.id != skip
        )

    hints: list[str] = []
    by_id = {i.id: i for i in items}
    if "targets" in by_id:
        size = float(targets["vm" if recommended and features.get("vm_targets") else "container"])
        per = size + float(targets["probe_client"])
        count = math.floor((available - total("targets")) / per)
        if 1 <= count < int(plan["concurrent_targets"]):
            hints.append(f"incus_concurrent_targets {count}")
    if "vllm" in by_id and not recommended:
        room = available - total("vllm") - (by_id["vllm"].required_gib - _model_gib(plan))
        hints.append(
            f"a model of at most {text_gib(room)}" if room > 0 else "no model (default_model none)"
        )
    if recommended and cap is not None:
        other = by_id.get("vllm") or by_id.get("training")
        # Only where the training cap is what sets the recommended value.
        if other is not None and abs(other.recommended_gib - cap) < 0.01:  # noqa: PLR2004
            room = available - total(other.id)
            # Both hints only where the rest fits without the job.
            if room >= serving_gib:
                if max(1.0, serving_gib) <= room < cap:
                    hints.append(f"training_caps.memory_max {math.floor(room)}G")
                hints.append("training: off")
    return hints


def _model_gib(plan: Mapping[str, Any]) -> float:
    model = plan.get("model")
    return int(model["size_bytes"]) / GIB if model else 0.0


def _memory_checks(
    req: Requirements, data: Mapping[str, Any], features: Mapping[str, bool]
) -> None:
    total = req.memory_total_gib
    if total is None:
        req.warnings.append("memory: MemTotal could not be read; the requirements were not checked")
        return
    tol = req.tolerance
    available = total / tol
    targets = data["target_memory_gib"]
    have = f"memory: MemTotal {total:.2f} GiB"
    breakdown = "; ".join(
        f"{i.title} {text_gib(i.required_gib)}" for i in req.memory_items if i.required_gib
    )
    if total < tol * req.floor_gib:
        req.unsupported.append(
            f"{have} is below the floor of {req.floor_gib:g} GiB ({tol * req.floor_gib:.2f} GiB "
            f"at the {tol:g} tolerance), the core stack with no target running"
        )
        return
    if total < tol * req.required_gib:
        hints = _fits(
            req.memory_items,
            available,
            req.choices,
            targets,
            features,
            recommended=False,
            cap=None,
        )
        req.unsupported.append(
            f"{have} is below the {req.required_gib:g} GiB these choices require "
            f"({tol * req.required_gib:.2f} GiB at the {tol:g} tolerance): {breakdown}"
            + (f". These would fit: {', '.join(hints)}" if hints else "")
        )
        return
    if total < tol * req.recommended_gib:
        serving = 0.0
        model = req.choices.get("model")
        if model and any(i.id == "vllm" for i in req.memory_items):
            over = next(c for c in data["components"] if c["kind"] == "vllm")["overhead_gib"]
            serving = _model_gib(req.choices) + float(over["recommended"])
        hints = _fits(
            req.memory_items,
            available,
            req.choices,
            targets,
            features,
            recommended=True,
            cap=None if req.switches.runs == "off" else req.switches.memory_max_gib,
            serving_gib=serving,
        )
        largest = sorted(
            (i for i in req.memory_items if i.recommended_gib > i.required_gib),
            key=lambda i: -i.recommended_gib,
        )[:LARGEST]
        req.degraded.append(
            f"{have} is below the {req.recommended_gib:g} GiB recommended for these choices "
            f"({tol * req.recommended_gib:.2f} GiB at the {tol:g} tolerance); the largest parts: "
            + "; ".join(f"{i.title} {text_gib(i.recommended_gib)}" for i in largest)
            + (f". These would fit: {', '.join(hints)}" if hints else "")
        )


def _image_items(
    data: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    features: Mapping[str, bool],
    runner: Runner,
    machine: str,
    warnings: list[str],
) -> list[tuple[str, str, float, bool, str, str]]:
    platform = PLATFORMS.get(machine)
    docker = runner.which("docker") is not None
    factor = float(data["image_unpack_factor"])
    found: list[tuple[str, str, float, bool, str, str]] = []
    for name, spec in data["images"].items():
        if plan["node_role"] not in spec["roles"]:
            continue
        if not all(features.get(f) for f in spec.get("features", [])):
            continue
        if spec.get("gpu") and not (features.get("gpu_inference") or features.get("gpu_training")):
            continue
        if spec.get("gpu_exporter") not in (None, plan["gpu_exporter"]):
            continue
        reference = f"{spec['image']}@{spec['digest']}"
        if docker:
            res = runner.run(["docker", "image", "inspect", "--format", "{{.Size}}", reference])
            if res.ok and res.stdout.strip().isdigit():
                found.append(
                    (
                        name,
                        reference,
                        int(res.stdout.strip()) / GIB,
                        True,
                        "measured",
                        "the pulled image (docker image inspect)",
                    )
                )
                continue
        size = (spec.get("sizes") or {}).get(platform or "")
        if size is None:
            warnings.append(f"image {name}: no size for {platform or machine} in versions.lock.yml")
            continue
        found.append(
            (
                name,
                reference,
                int(size) * factor / GIB,
                False,
                "estimated",
                f"its compressed size for {platform} in versions.lock.yml times {factor}",
            )
        )
    return found


def _disk_items(
    data: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    features: Mapping[str, bool],
    fs: FileSystem,
    runner: Runner,
    machine: str,
    warnings: list[str],
    notes: list[str],
) -> list[tuple[DiskItem, str]]:
    """The items with the path whose mount point holds them."""
    role = str(plan["node_role"])
    srv = str(plan["srv_path"]).rstrip("/") or "/"
    disk = data["disk"]
    out: list[tuple[DiskItem, str]] = []

    def add(
        item_id: str,
        title: str,
        path: str,
        *,
        size: float,
        present: bool,
        basis: str,
        source: str,
    ) -> None:
        out.append((DiskItem(item_id, title, path, "", size, present, 0.0, basis, source), path))

    add(
        "pinka",
        "pinka itself",
        PINKA_DIR,
        size=float(disk["pinka_gib"]),
        present=False,
        basis=disk["pinka_basis"],
        source=disk["pinka_source"],
    )
    root = str(plan["docker_data_root"])
    for name, _ref, size, present, basis, source in _image_items(
        data, plan, features=features, runner=runner, machine=machine, warnings=warnings
    ):
        add(
            f"image:{name}",
            f"image {name}",
            root,
            size=size,
            present=present,
            basis=basis,
            source=source,
        )
    model = plan.get("model")
    if model and features.get("gpu_inference") and role in ("full", "inference"):
        path = f"{srv}/models/{model['hf_repo']}/{model['revision']}"
        add(
            f"model:{model['id']}",
            f"model {model['id']}",
            f"{srv}/models",
            size=int(model["size_bytes"]) / GIB,
            present=fs.exists(f"{path}/MANIFEST.json"),
            basis="measured",
            source="the sum of its files' sizes in models.lock.yml",
        )
    if role in LAB_ROLES:
        pool = plan["pool"]
        # A loop pool of incus_pool_size: auto comes last (_pool): it follows
        # the free space left after every other item, the reserve included.
        if pool["source"] == "loop":
            pool_file = str(disk["pool_file"])
            if pool["size_gib"] is not None:
                add(
                    "pool",
                    "the Incus pool (loop file)",
                    pool_file,
                    size=float(pool["size_gib"]),
                    present=fs.exists(pool_file),
                    basis="configured",
                    source="incus_pool_size: a loop file counts fully",
                )
            add(
                RESERVE_ID,
                "the free space kept beside the Incus pool (loop file)",
                pool_file,
                size=float(data["pool"]["loop_reserve_gib"]),
                # A new pool needs it free; beside an existing file it needs no
                # space here, and _existing_loop_space applies doctor's two
                # levels to the free space instead.
                present=fs.exists(pool_file),
                basis="estimated",
                source="loop_reserve_gib of preflight/requirements.yml pool",
            )
        elif pool["source"] == "device":
            notes.append("the Incus pool is a dedicated device: on no mount point")
        for name in plan["images"]:
            blueprint = data["blueprints"].get(name)
            if blueprint is None:
                raise PlanError(f"plan: {unknown_blueprint(name, data['blueprints'])}")
            built = fs.exists(f"{srv}/images/{name}/{blueprint['version']}")
            size = float(blueprint["estimated_size_gib"])
            source = f"estimated_size_gib of images/{name}/image.yaml"
            title = f"golden image {name} {blueprint['version']}"
            for item_id, where, path in (
                (f"golden:{name}", "export", f"{srv}/images"),
                (f"golden-store:{name}", "Incus image store", f"{disk['incus_dir']}/images"),
            ):
                add(
                    item_id,
                    f"{title} ({where})",
                    path,
                    size=size,
                    present=built,
                    basis="estimated",
                    source=source,
                )
    return out


def volume_size_gib(data: Mapping[str, Any], plan: Mapping[str, Any]) -> float:
    """The pool's ``volume.size``: configured, or just above the largest unpacked image.

    Args:
        data: Compiled requirements.
        plan: The plan.

    Returns:
        ``incus_pool_volume_size`` when set; otherwise the largest blueprint of
        ``images/`` (built or not), its estimate times the image unpack factor
        rounded up to whole GiB, plus the volume's own filesystem.
    """
    configured = plan["pool"].get("volume_size_gib")
    if configured is not None:
        return float(configured)
    factor = float(data["image_unpack_factor"])
    largest = max(float(b["estimated_size_gib"]) for b in data["blueprints"].values())
    return float(math.ceil(largest * factor - EPSILON)) + float(data["pool"]["volume_fs_gib"])


def pool_parts(
    data: Mapping[str, Any], plan: Mapping[str, Any], features: Mapping[str, bool]
) -> tuple[float, list[dict[str, Any]], int]:
    """The parts of the Incus pool's required size (ADR-0017).

    Args:
        data: Compiled requirements.
        plan: The plan.
        features: Feature flags after the switches.

    Returns:
        ``(volume_size_gib, parts, required_gib)``: each part ``{id, title,
        basis, size_gib, detail}`` (basis ``configured`` for a limit or a set
        size, else the estimate's), and their sum rounded up to whole GiB.
    """
    spec, roots = data["pool"], data["target_root_gib"]
    # The profiles' sizes and the projects' limits as the role renders them:
    # host_vars merged key by key with the defaults (the plan's incus_disk).
    sizes = plan["incus_disk"]
    vm = bool(features.get("vm_targets"))
    count = int(plan["concurrent_targets"])
    target = float(sizes["target_vm_root_gib" if vm else "target_container_root_gib"])
    probe = float(sizes["probe_client_root_gib"])
    state = float(sizes["target_vm_state_gib"]) if vm else 0.0
    build = float(sizes["build_root_gib"]) + float(sizes["build_state_gib"])
    researchers = int(plan["researchers"])
    personal = float(plan["personal_disk_gib"])
    size = volume_size_gib(data, plan)
    images = list(plan["images"])
    volumes = len(images) + (1 if images else 0) + 1
    vm_images = sum(1 for n in images if data["blueprints"][n]["type"] == "virtual-machine")
    metadata = float(spec["metadata_gib"])
    bench_set = sizes.get("bench_limits_disk_gib")
    build_set = sizes.get("build_limits_disk_gib")
    bench_detail = f"{count} x ({text_gib(target)} target + {text_gib(probe)} probe client"
    bench_detail += f" + {text_gib(state)} VM state)" if vm else ")"
    image_detail = (
        f"({len(images)} golden image(s)"
        + (" + the upstream base image" if images else "")
        + f" + 1 new version) x {text_gib(size)} volume.size"
    )
    if vm_images:
        image_detail += f" + {vm_images} x {text_gib(float(roots['vm_state']))} VM image state"
    parts: list[dict[str, Any]] = [
        {
            "id": "bench",
            "title": "the bench project's limits.disk",
            "basis": "configured",
            "size_gib": bench_set if bench_set is not None else count * (target + probe + state),
            "detail": "incus_bench_limits limits.disk" if bench_set is not None else bench_detail,
        },
        {
            "id": "build",
            "title": "the build project's limits.disk",
            "basis": "configured",
            "size_gib": build_set if build_set is not None else 2 * build,
            "detail": (
                "incus_build_limits limits.disk"
                if build_set is not None
                else f"2 x {text_gib(build)} build instance (root disk and VM state)"
            ),
        },
        {
            "id": "personal",
            "title": "the personal projects' limits.disk",
            "basis": "configured",
            "size_gib": researchers * personal,
            "detail": f"{researchers} researcher(s) x {text_gib(personal)}",
        },
        {
            "id": "images",
            "title": "the image volumes",
            # a set volume.size is the operator's; auto rests on the unpack
            # factor's and the volume filesystem's estimates
            "basis": "configured"
            if plan["pool"].get("volume_size_gib") is not None
            else str(spec["volume_fs_basis"]),
            "size_gib": volumes * size + vm_images * float(roots["vm_state"]),
            "detail": image_detail,
        },
        {
            "id": "metadata",
            "title": "the thin pool's metadata and its spare",
            "basis": str(spec["metadata_basis"]),
            "size_gib": 2 * metadata,
            "detail": f"2 x {text_gib(metadata)}",
        },
        {
            "id": "lvm",
            "title": "LVM's own overhead",
            "basis": str(spec["lvm_overhead_basis"]),
            "size_gib": float(spec["lvm_overhead_gib"]),
            "detail": "metadata area and extent rounding",
        },
        {
            "id": "headroom",
            "title": "the headroom for the images no limit counts",
            "basis": str(spec["headroom_basis"]),
            "size_gib": float(spec["headroom_gib"]),
            "detail": "before doctor's incus.pool.space_low",
        },
    ]
    required = math.ceil(sum(float(p["size_gib"]) for p in parts) - EPSILON)
    return size, parts, required


def _pool_text(parts: list[dict[str, Any]], required: int) -> str:
    """The sum of the pool's parts, for a message."""
    terms = " + ".join(f"{p['title']} {text_gib(float(p['size_gib']))}" for p in parts)
    return f"{terms} = {required} GiB"


POOL_ACTION_ID = "incus_pool_size"
# File systems on which fallocate does not reserve a loop file's space.
NO_PREALLOCATION = ("zfs",)
# File systems that copy on write: the loop file needs No_COW (chattr +C),
# which the incus role sets on its directory before Incus creates the file
# there, so the file is No_COW from its creation.
COPY_ON_WRITE = ("btrfs",)
# The thin pool Incus's LVM driver creates in the pool's volume group.
THIN_POOL = "IncusThinPool"
# Doctor's incus.pool.loop_space_full (and space_low): three image volumes.
SPACE_LOW_IMAGE_VOLUMES = 3
# The parts of the pool's sum outside the thin pool's data.
OUTSIDE_DATA = ("metadata", "lvm")


def _down(value: float) -> str:
    """A measured size for a message, rounded down to a tenth (never overstated)."""
    return f"{math.floor(round(value * 10, 9)) / 10:.1f} GiB"


def _loop_file(runner: Runner, path: str) -> tuple[float, float] | None:
    """An existing loop file's size and allocated space in GiB, or None if unreadable."""
    done = runner.run(["stat", "--format=%s %b %B", path])
    fields = done.stdout.split() if done.ok else []
    if len(fields) != 3 or not all(f.isdigit() for f in fields):  # noqa: PLR2004
        return None
    size, blocks, unit = (int(f) for f in fields)
    return size / GIB, min(size, blocks * unit) / GIB


def _no_cow(runner: Runner, path: str) -> bool | None:
    """Whether a file carries No_COW (lsattr's ``C``), or None if unreadable."""
    done = runner.run(["lsattr", "-d", path])
    fields = done.stdout.split() if done.ok else []
    if not fields:
        return None
    return "C" in fields[0]


def _thin_pool(runner: Runner, vg: str) -> tuple[str, float | None]:
    """An existing pool's volume group and its thin pool's data size.

    Returns:
        ``("absent", None)`` when the volume group does not exist (or LVM is
        not installed), ``("present", GiB)`` with the thin pool's data size,
        ``("unreadable", None)`` otherwise.
    """
    done = runner.run(["vgs", "--noheadings", "-o", "vg_name", vg])
    if not done.ok:
        if done.returncode == EXIT_NOT_FOUND or "not found" in done.stderr.lower():
            return "absent", None
        return "unreadable", None
    lvs = runner.run(
        ["lvs", "--noheadings", "--units", "b", "--nosuffix", "-o", "lv_size", f"{vg}/{THIN_POOL}"]
    )
    text = lvs.stdout.strip() if lvs.ok else ""
    return ("present", int(text) / GIB) if text.isdigit() else ("unreadable", None)


def _device_size(runner: Runner, device: str) -> float | None:
    """The size of a dedicated pool device in GiB, or None if unreadable."""
    done = runner.run(["blockdev", "--getsize64", device])
    text = done.stdout.strip() if done.ok else ""
    return int(text) / GIB if text.isdigit() else None


NEVER_SMALLER = "larger than its current size (Incus refuses a smaller size)"
LOWER = (
    "or lower the limits (incus_concurrent_targets, the profiles' root disks, the researchers' "
    "incus_user_limits limits.disk, incus_pool_volume_size)"
)


def _action(text: str) -> dict[str, str]:
    """A needs_action entry of the pool, ending with the re-run."""
    return {"id": POOL_ACTION_ID, "action": f"{text}, then re-run bootstrap."}


def _check_thin_pool(
    state: str,
    thin: float | None,
    data_need: float,
    total: str,
    *,
    name: str,
    file_size: float | None,
    needs_action: list[dict[str, str]],
) -> None:
    """An existing pool's thin pool against the data part of the sum."""
    if state == "unreadable":
        needs_action.append(
            _action(
                f"The Incus pool's volume group {name} exists but its thin pool {THIN_POOL} "
                "cannot be measured, so preflight cannot verify that it holds the sum of "
                f"everything that may be written to it ({total}). Check 'sudo vgs {name}' and "
                f"'sudo lvs {name}'"
            )
        )
        return
    if state != "present" or thin is None or thin + EPSILON >= data_need:
        return
    missing = round_up(data_need - thin, 1)
    if file_size is not None:
        grow = (
            f"grow it with 'sudo incus storage set {name} size="
            f"{math.ceil(file_size + data_need - thin - EPSILON)}GiB', " + NEVER_SMALLER
        )
    else:
        grow = (
            f"add a disk to its volume group ('sudo vgextend {name} <device>') and extend "
            f"the thin pool into it ('sudo lvextend -l +100%FREE {name}/{THIN_POOL}') until "
            f"it holds {math.ceil(data_need - EPSILON)} GiB"
        )
    needs_action.append(
        _action(
            f"The existing Incus pool's thin pool ({_down(thin)} of data) is smaller than "
            f"the data part of the sum of everything that may be written to it "
            f"({text_gib(data_need)} of {total}): missing {text_gib(missing)}. Either {grow}, "
            + LOWER
        )
    )


def _check_loop_file(
    found: tuple[float, float] | None,
    state: str,
    required: int,
    total: str,
    *,
    path: str,
    name: str,
    fstype: str | None,
    runner: Runner,
    needs_action: list[dict[str, str]],
) -> None:
    """An existing loop file: readable, its volume group present, No_COW on btrfs."""
    if found is None:
        needs_action.append(
            _action(
                f"The size and allocation of the existing Incus pool's file {path} cannot be "
                "read, so preflight cannot verify the pool or the space it still needs. "
                f"Check 'sudo stat {path}'"
            )
        )
    elif state == "absent":
        # The file is there but its volume group is not: the thin pool cannot be
        # measured, and the file's size says nothing of a thin pool whose metadata
        # was extended since.
        needs_action.append(
            _action(
                f"The existing Incus pool's file {path} is there, but its volume group {name} "
                f"is not ('vgs {name}' finds none: the loop device is not attached, or LVM is "
                "missing), so preflight cannot measure the pool's thin pool against the sum "
                f"of everything that may be written to it ({total}). Start Incus (it attaches "
                f"the file) and check 'sudo vgs {name}' and 'sudo incus storage info {name}'"
            )
        )
    if fstype not in COPY_ON_WRITE:
        return
    no_cow = _no_cow(runner, path)
    if no_cow is True:
        return
    needs_action.append(
        _action(
            f"The existing Incus pool's file {path} is on {fstype} "
            + ("without" if no_cow is False else "with an unreadable")
            + " No_COW attribute ('lsattr' shows no C), so writes to it can take new "
            "space on the file system. No_COW cannot be set on a file that holds data. "
            "Export the instances and custom volumes you need (incus export, incus storage "
            "volume export) and delete them, remove the root disk on this pool from the "
            "profiles that use it (incus profile device remove <profile> root --project "
            f"<project>), then delete the pool (incus storage delete {name}: Incus deletes its "
            "file), and re-run bootstrap, which creates the pool with a No_COW file and the "
            "profiles' root disks again; or put the pool on a dedicated device "
            "(incus_pool_source: device)"
        )
    )


def _check_new_pool(
    pool: Mapping[str, Any],
    required: int,
    total: str,
    *,
    runner: Runner,
    needs_action: list[dict[str, str]],
) -> None:
    """The pool the plan will create: a dedicated device, or an explicit loop size."""
    if pool["source"] == "device":
        device = str(pool.get("device") or "")
        found = _device_size(runner, device) if device else None
        if found is None:
            named = device or "(incus_pool_device is not set)"
            needs_action.append(
                _action(
                    f"The size of the Incus pool's device {named} cannot be read, so preflight "
                    "cannot verify that it holds the sum of everything that may be written to "
                    f"it ({total}). Set incus_pool_device to the device's /dev/disk/by-id/ path "
                    "and check 'sudo blockdev --getsize64 <device>'"
                )
            )
        elif found + EPSILON < required:
            needs_action.append(
                _action(
                    f"The Incus pool's device ({_down(found)}) is smaller than the sum of "
                    f"everything that may be written to it ({total}): missing "
                    f"{text_gib(round_up(required - found, 1))}. Either use a device of at "
                    f"least {required} GiB, " + LOWER
                )
            )
    elif pool["size_gib"] is not None and float(pool["size_gib"]) + EPSILON < required:
        size = float(pool["size_gib"])
        needs_action.append(
            _action(
                f"The Incus pool's incus_pool_size ({text_gib(size)}) is smaller than the sum "
                f"of everything that may be written to it ({total}): missing "
                f"{text_gib(round_up(required - size, 1))}. Either set incus_pool_size to "
                f"auto or at least {required}GiB, " + LOWER
            )
        )


def _pool_checks(
    plan: Mapping[str, Any],
    parts: list[dict[str, Any]],
    required: int,
    *,
    path: str,
    name: str,
    fstype: str | None,
    fs: FileSystem,
    runner: Runner,
    needs_action: list[dict[str, str]],
) -> float:
    """Check an existing pool, or the pool the plan will create, against the sum.

    The existing pool is measured whatever the plan's source: its thin pool's
    data size against the sum less the parts outside the data (metadata and
    LVM's overhead), so growing the volume group and the thin pool (vgextend,
    lvextend) or the loop file (incus storage set) clears it. A pool that
    cannot be measured is an operator action too.

    Returns:
        The part of an existing loop file still to allocate, in GiB (it needs
        that much on the file's mount point).
    """
    total = _pool_text(parts, required)
    data_need = sum(float(p["size_gib"]) for p in parts if p["id"] not in OUTSIDE_DATA)
    state, thin = _thin_pool(runner, name)
    exists = fs.exists(path)
    found = _loop_file(runner, path) if exists else None
    _check_thin_pool(
        state,
        thin,
        data_need,
        total,
        name=name,
        file_size=found[0] if found else None,
        needs_action=needs_action,
    )
    if exists:
        _check_loop_file(
            found,
            state,
            required,
            total,
            path=path,
            name=name,
            fstype=fstype,
            runner=runner,
            needs_action=needs_action,
        )
    elif state == "absent":
        _check_new_pool(plan["pool"], required, total, runner=runner, needs_action=needs_action)
    _check_source(plan["pool"], state, found, path=path, name=name, needs_action=needs_action)
    return max(0.0, found[0] - found[1]) if found else 0.0


def _check_source(
    pool: Mapping[str, Any],
    state: str,
    found: tuple[float, float] | None,
    *,
    path: str,
    name: str,
    needs_action: list[dict[str, str]],
) -> None:
    """The plan against the existing pool: the same source, and no smaller explicit size."""
    if found is not None and pool["source"] == "device":
        needs_action.append(
            _action(
                f"The existing Incus pool {name} is a loop file ({path}), but "
                "incus_pool_source is device: pinka never re-creates a pool. Set "
                "incus_pool_source: loop, or move the instances off the pool and delete it "
                "before bootstrap creates it on the device"
            )
        )
    elif found is None and state == "present" and pool["source"] == "loop":
        needs_action.append(
            _action(
                f"The existing Incus pool {name} is on a dedicated device (its volume group "
                f"has no loop file at {path}), but incus_pool_source is loop: pinka never "
                "re-creates a pool. Set incus_pool_source: device and incus_pool_device"
            )
        )
    elif found is not None and pool["size_gib"] is not None:
        size = float(pool["size_gib"])
        if size + EPSILON < found[0]:
            needs_action.append(
                _action(
                    f"incus_pool_size ({text_gib(size)}) is below the existing Incus pool's "
                    f"size ({_down(found[0])}): pinka never lowers a pool's size, and Incus "
                    "refuses a smaller size. Set incus_pool_size to auto or to at least "
                    f"{math.ceil(found[0] - EPSILON)}GiB"
                )
            )


def _pool(
    data: Mapping[str, Any],
    plan: Mapping[str, Any],
    entries: list[tuple[DiskItem, str]],
    *,
    features: Mapping[str, bool],
    fs: FileSystem,
    runner: Runner,
    unsupported: list[str],
    needs_action: list[dict[str, str]],
) -> dict[str, Any]:
    """The Incus pool: its required size, its size, and its loop file's items.

    The required size is the sum of everything that may be written to the pool
    (``pool_parts``). ``incus_pool_size: auto`` is exactly that sum, never cut
    down: a mount point without room for it is a disk shortfall like any other.
    A pool smaller than the sum (an explicit size, a dedicated device, an
    existing pool of either kind, measured whatever the plan's source), or one
    that cannot be measured, is an operator action with the exact numbers and
    the remedies, before anything changes; pinka never resizes a pool. A loop
    file on a file system that cannot preallocate it is refused, one whose
    file system cannot be read and an existing one on btrfs without No_COW
    are operator actions. What an existing sparse loop file still needs on its
    mount point, and the auto pool's item, are appended to ``entries``.
    """
    pool = plan["pool"]
    size_vol, parts, required = pool_parts(data, plan, features)
    common = {
        "required_gib": required,
        "volume_size_gib": gib(size_vol),
        # rounded up, never down: doctor sums them against the thin pool
        "parts": [{**p, "size_gib": round_up(float(p["size_gib"]), 2)} for p in parts],
    }
    total = _pool_text(parts, required)
    path = str(data["disk"]["pool_file"])
    name = os.path.splitext(os.path.basename(path))[0]
    loop = pool["source"] == "loop"
    fstype: str | None = None
    if loop or fs.exists(path):
        fstype = disk_info(fs, runner, path).fstype
    if loop and fstype is None:
        needs_action.append(
            _action(
                f"The file system type of the Incus pool's loop file location {path} cannot be "
                "read ('df --output=fstype'), so preflight cannot tell whether the file can be "
                "preallocated or needs No_COW. Check that its directory's file system is "
                "mounted and readable, or put the pool on a dedicated device "
                "(incus_pool_source: device)"
            )
        )
    if loop and fstype in NO_PREALLOCATION:
        unsupported.append(
            f"pool: the loop file {path} would be on {fstype}, which cannot preallocate "
            "it: put the Incus pool on a dedicated device (incus_pool_source: device)"
        )
    to_allocate = _pool_checks(
        plan,
        parts,
        required,
        path=path,
        name=name,
        fstype=fstype,
        fs=fs,
        runner=runner,
        needs_action=needs_action,
    )
    if not loop:
        return {
            "source": "device",
            "size_gib": None,
            "basis": "configured",
            "detail": f"a dedicated device; it must hold {total}",
            **common,
        }
    if to_allocate > 0:
        entries.append(
            (
                DiskItem(
                    "pool-allocate",
                    "the part of the Incus pool's existing file still to allocate",
                    path,
                    "",
                    to_allocate,
                    False,
                    0.0,
                    "measured",
                    "the file's size less its allocated blocks (stat); bootstrap preallocates it",
                ),
                path,
            )
        )
    if pool["size_gib"] is not None:
        size = float(pool["size_gib"])
        return {
            "source": "loop",
            "size_gib": gib(size),
            "basis": "configured",
            "detail": f"incus_pool_size {text_gib(size)}; the sum: {total}",
            **common,
        }
    entries.append(
        (
            DiskItem(
                "pool",
                "the Incus pool (loop file)",
                path,
                "",
                float(required),
                fs.exists(path),
                0.0,
                "estimated",
                "incus_pool_size: auto, the sum of preflight/requirements.yml pool",
            ),
            path,
        )
    )
    return {
        "source": "loop",
        "size_gib": float(required),
        "basis": "computed",
        "detail": total,
        **common,
    }


LOOP_SPACE_ID = "incus_pool_loop_space"


def _existing_loop_space(
    data: Mapping[str, Any],
    plan: Mapping[str, Any],
    req: Requirements,
    fs: FileSystem,
    runner: Runner,
) -> None:
    """Doctor's two levels for the free space beside an existing loop pool's file.

    The same condition as doctor's incus.pool.loop_space_low and
    incus.pool.loop_space_full, with the same answer: the free space the
    file's file system keeps after the install's other items there, below
    the reserve, a warning; below three image volumes, an operator action. A
    new pool needs the reserve free like any other item.
    """
    path = str(data["disk"]["pool_file"])
    if plan["pool"]["source"] != "loop" or not fs.exists(path) or not req.pool:
        return
    mount = next((m for m in req.disks if any(i.id == RESERVE_ID for i in m.items)), None)
    if mount is None:
        return
    # Exact, as doctor compares: the measured bytes less the items still to come
    # (the mount's own figures are rounded for display).
    info = disk_info(fs, runner, path)
    if info.free_bytes is None:
        return  # the free space is not measured: _disks warns
    free = info.free_bytes / GIB - sum(i.size_gib for i in mount.items if not i.present)
    reserve = float(data["pool"]["loop_reserve_gib"])
    level = SPACE_LOW_IMAGE_VOLUMES * float(req.pool["volume_size_gib"])
    where = f"{mount.mount} ({mount.fstype or 'type unknown'})"
    if free < level:
        req.needs_action.append(
            _action_with_id(
                LOOP_SPACE_ID,
                f"The file system that holds the existing Incus pool's loop file, {where}, "
                f"keeps {_down(max(free, 0.0))} free after the install, below three image "
                f"volumes ({text_gib(level)}), doctor's incus.pool.loop_space_full: a write "
                "into the pool that needs new space there may fail. Free space on it (old "
                "snapshots of its subvolume, logs, files nobody needs), or move the pool to a "
                "dedicated device (incus_pool_source: device)",
            )
        )
    elif free < reserve:
        req.warnings.append(
            f"pool: the file system that holds the existing Incus pool's loop file, {where}, "
            f"keeps {_down(free)} free after the install, below the {text_gib(reserve)} kept "
            "beside the file (doctor's incus.pool.loop_space_low): free space on it, or move "
            "the pool to a dedicated device"
        )


def _action_with_id(action_id: str, text: str) -> dict[str, str]:
    """A needs_action entry with its own id, ending with the re-run."""
    return {"id": action_id, "action": f"{text}, then re-run bootstrap."}


def _up(value: float) -> float:
    """Round up to a tenth, so that a small shortfall never reads as 0.0."""
    return round_up(value, 1)


def _distribute(items: list[DiskItem], shortfall: float) -> None:
    """Mark the missing part of the items, the largest first (the one to shrink)."""
    remaining = shortfall
    named = False
    for item in sorted(items, key=lambda i: -i.size_gib):
        # The first item always carries the shortfall; less than half a byte
        # left after it is float noise, not a byte, and names no other.
        if remaining <= 0 or (named and remaining < 0.5 / GIB):
            break
        if item.present:
            continue
        # Rounded up and at least 0.01, so that the item carrying a shortfall of
        # any size is named.
        item.missing_gib = max(0.01, round_up(min(item.size_gib, remaining), 2))
        named = True
        remaining -= item.size_gib


def _disks(
    entries: list[tuple[DiskItem, str]],
    fs: FileSystem,
    runner: Runner,
    req: Requirements,
    measured: dict[str, DiskInfo],
) -> dict[str, DiskInfo]:
    mounts: dict[str, MountCheck] = {}
    exact: dict[str, float | None] = {}
    for item, path in entries:
        info = measured.get(path) or disk_info(fs, runner, path)
        measured[path] = info
        mount = info.mount or info.checked_path
        item.mount = mount
        if mount not in mounts:
            free = None if info.free_bytes is None else info.free_bytes / GIB
            exact[mount] = free
            mounts[mount] = MountCheck(
                mount, info.fstype, None if free is None else gib(free), 0, 0
            )
        mounts[mount].items.append(item)
    for check in mounts.values():
        needed = sum(i.size_gib for i in check.items if not i.present)
        check.required_gib = gib(needed)
        free = exact[check.mount]
        if free is None:
            req.warnings.append(f"disk: the free space of {check.mount} could not be measured")
        elif needed > free:
            # The exact comparison decides; what is stored and printed follows it.
            check.required_gib = round_up(needed, 2)
            check.free_gib = round_down(free, 2)
            check.shortfall_gib = max(0.01, round_up(needed - free, 2))
            _distribute(check.items, needed - free)
            missing = [
                f"{i.title}: {disk_size_text(i.size_gib, True)}, "
                f"missing {text_gib(_up(i.missing_gib))} on {check.mount}"
                for i in check.items
                if i.missing_gib
            ]
            # From the stored figures: the summary and the capacity plan print these.
            need_text, free_text, _ = disk_texts(
                check.required_gib, check.free_gib, check.shortfall_gib
            )
            req.unsupported.append(
                f"disk: {check.mount} has {free_text} free and these choices need "
                f"{need_text} there: " + "; ".join(missing)
            )
        for item in check.items:
            item.size_gib = gib(item.size_gib)
    req.disks = sorted(mounts.values(), key=lambda m: m.mount)
    return measured


def _filled(items: Sequence[GrowthItem], day: int) -> float:
    return sum(
        min(i.gib_per_day * day, i.cap_gib) if i.cap_gib is not None else i.gib_per_day * day
        for i in items
    )


def _growth(
    data: Mapping[str, Any], plan: Mapping[str, Any], req: Requirements, srv_info: DiskInfo
) -> Growth:
    retention = plan["retention"]
    items: list[GrowthItem] = []
    for spec in data["growth"]:
        if plan["node_role"] not in spec["roles"]:
            continue
        rate = float(spec["gib_per_day"])
        cap: float | None = None
        if spec.get("retention"):
            cap = rate * float(retention[spec["retention"]])
        size_cap = float(retention.get(spec.get("size_cap") or "", 0) or 0)
        if size_cap > 0:
            cap = size_cap if cap is None else min(cap, size_cap)
        per_run = float(plan["training"][spec["per_run"]]) if spec.get("per_run") else None
        items.append(
            GrowthItem(
                spec["id"],
                spec["title"],
                rate,
                gib(cap) if cap is not None else None,
                per_run,
                spec["basis"],
                spec["source"],
            )
        )
    mount = srv_info.mount or srv_info.checked_path
    free_after: float | None = None
    if srv_info.free_gib is not None:
        # The reserve beside a loop pool stays free: doctor watches it, so the
        # stores do not count it as room to grow into, beside a new or an
        # existing file alike.
        used = sum(
            m.required_gib + sum(i.size_gib for i in m.items if i.id == RESERVE_ID and i.present)
            for m in req.disks
            if m.mount == mount
        )
        free_after = gib(srv_info.free_gib - used)
    per_day = sum(i.gib_per_day for i in items)
    days: int | None = None
    if free_after is not None and per_day > 0:
        if free_after <= 0:
            days = 0
        elif _filled(items, HORIZON_DAYS) >= free_after:
            low, high = 0, HORIZON_DAYS
            while low < high:
                mid = (low + high) // 2
                if _filled(items, mid) >= free_after:
                    high = mid
                else:
                    low = mid + 1
            days = low
    return Growth(mount, free_after, gib(per_day), gib(per_day * 7), days, items)


def must_serve(plan: Mapping[str, Any]) -> bool:
    """Whether preflight refuses a host that cannot serve: ``inference: on``, or an inference node.

    Args:
        plan: The effective plan.

    Returns:
        True where serving that cannot be done makes the host unsupported.
    """
    return str(plan["inference"]) == "on" or str(plan["node_role"]) == "inference"


def serve_or_else(plan: Mapping[str, Any], *, after_remedy: bool = True) -> str:
    """What else the operator can do where serving cannot be done and must be.

    Args:
        plan: The effective plan.
        after_remedy: The sentence follows a remedy (it then starts with "Or").

    Returns:
        The alternative, as a sentence.
    """
    if str(plan["node_role"]) == "inference":
        alternative = "use this machine as a full or lab node"
    else:
        alternative = (
            "set inference: auto (this node then serves nothing, degraded with consent) or off"
        )
    return f"Or {alternative}." if after_remedy else f"{alternative[0].upper()}{alternative[1:]}."


def _serving_misfit(req: Requirements, plan: Mapping[str, Any], why_not: vram.Misfit) -> None:
    """A serving profile or default model that does not fit: refused, or degraded."""
    if must_serve(plan):
        line = req.vram_serving
        short = (
            f" ({vram.at_least(line.shortfall_gib)} short)" if line and line.shortfall_gib else ""
        )
        who = (
            "inference: on must serve on this node"
            if str(plan["inference"]) == "on"
            else "an inference node must serve"
        )
        req.unsupported.append(
            f"vram: {why_not.reason}{short}; {who}. Use {why_not.remedy}. {serve_or_else(plan)}"
        )
        return
    req.degraded.append(
        f"vram: {why_not.reason}, so this node serves nothing (inference: auto). To serve, "
        f"use {why_not.remedy}; set inference: off to go on without consent"
    )


def compute(
    data: Mapping[str, Any],
    plan: Mapping[str, Any],
    label: str,
    *,
    minimums: Mapping[str, Any],
    features: Mapping[str, bool],
    memory_gib: float | None,
    fs: FileSystem,
    runner: Runner,
    machine: str,
    gpu_vram_gib: float | None = None,
    gpu_compute_capability: str | None = None,
    vram_minimums: Mapping[str, float] | None = None,
    gpu_unmet: Mapping[str, str] | None = None,
    driver_pending: bool = False,
) -> Requirements:
    """Compute the requirements of a plan on this host.

    Args:
        data: Compiled requirements (``requirements.json``).
        plan: The effective plan (:func:`load_plan`).
        label: ``defaults`` or the plan file's path.
        minimums: The support matrix's ``minimums`` (memory floor and tolerance).
        features: The host's detected feature flags (the plan's switches apply).
        memory_gib: ``MemTotal``.
        fs: Filesystem view.
        runner: Command runner.
        machine: CPU architecture.
        gpu_vram_gib: The GPU's memory (None without a usable NVIDIA GPU).
        gpu_compute_capability: The GPU's compute capability.
        vram_minimums: The support matrix's VRAM minimum of ``gpu_inference``
            and ``gpu_training`` (:func:`pinka_preflight.matrix.vram_minimums`).
        gpu_unmet: The support matrix's unmet conditions of each GPU feature
            it turned off (the decision's ``disabled_features``).
        driver_pending: Bootstrap installs the NVIDIA driver in this run (the
            decision's ``driver_pending``).

    Returns:
        The requirements, with what does not fit.

    Raises:
        PlanError: If the plan names an unknown golden image blueprint or
            serving profile.
    """
    notes: list[str] = []
    serving = data["serving"]
    profile = str(plan["vllm_profile"])
    if profile not in serving["profiles"]:
        raise PlanError(f"plan: {unknown_profile(profile, serving['profiles'])}")
    minimum = {"gpu_inference": 0.0, "gpu_training": 0.0, **(vram_minimums or {})}
    unmet = dict(gpu_unmet or {})
    needs, unusable, unknown_model = vram.serving_needs(serving, plan)
    could_serve = str(plan["node_role"]) != "lab" and str(plan["inference"]) != "off"
    why_not = None
    if could_serve and features.get("gpu_inference"):
        why_not = vram.misfit(needs, unusable, gpu_vram_gib)
    if could_serve and unknown_model and features.get("gpu_inference"):
        notes.append(unknown_model)
    detected = features
    sw = switches(plan, features, why_not.reason if why_not else "")
    features = sw.effective(features)
    items = _memory_items(data, plan, features, notes, sw=sw, memory_gib=memory_gib)
    floor = float(minimums["memory_gib"]["floor"])
    required = max(floor, sum(i.required_gib for i in items))
    recommended = max(required, sum(i.recommended_gib for i in items))
    for item in items:
        item.required_gib, item.recommended_gib = gib(item.required_gib), gib(item.recommended_gib)
    req = Requirements(
        plan=label,
        choices=dict(plan),
        memory_total_gib=memory_gib,
        floor_gib=floor,
        required_gib=gib(required),
        recommended_gib=gib(recommended),
        tolerance=float(minimums["memory_gib"]["tolerance"]),
        switches=sw,
        memory_items=items,
        cpu_threads=gib(sum(i.cpu_threads for i in items)),
        notes=notes,
    )
    req.vram_serving = vram.serving_line(
        plan,
        detected,
        needs,
        why_not,
        vram_gib=gpu_vram_gib,
        minimum=minimum["gpu_inference"],
        unmet=unmet.get("gpu_inference", ""),
        driver_pending=driver_pending,
    )
    req.vram_training = vram.training_line(
        plan,
        sw.runs,
        sw.training_reason,
        vram_gib=gpu_vram_gib,
        minimum=minimum["gpu_training"],
        unmet=unmet.get("gpu_training", ""),
        driver_pending=driver_pending,
    )
    req.serving = vram.fits_for_installer(serving, plan, gpu_vram_gib, gpu_compute_capability)
    if why_not:
        _serving_misfit(req, plan, why_not)
    _memory_checks(req, data, features)
    entries = _disk_items(
        data,
        plan,
        features=features,
        fs=fs,
        runner=runner,
        machine=machine,
        warnings=req.warnings,
        notes=req.notes,
    )
    measured: dict[str, DiskInfo] = {}
    if str(plan["node_role"]) in LAB_ROLES:
        req.pool = _pool(
            data,
            plan,
            entries,
            features=features,
            fs=fs,
            runner=runner,
            unsupported=req.unsupported,
            needs_action=req.needs_action,
        )
    measured = _disks(entries, fs, runner, req, measured)
    if str(plan["node_role"]) in LAB_ROLES:
        _existing_loop_space(data, plan, req, fs, runner)
    srv = str(plan["srv_path"])
    srv_info = measured.get(f"{srv.rstrip('/')}/models") or disk_info(fs, runner, srv)
    req.growth = _growth(data, plan, req, srv_info)
    return req
