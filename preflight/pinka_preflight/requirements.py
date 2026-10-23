"""Requirements computed from the operator's choices (BUILD_PROMPT §2.3).

``requirements.json`` (compiled from ``requirements.yml`` by ``make generate``)
says what each part of the lab needs; the plan (``--plan``, else the configured
defaults) says what the operator chose. This module adds them up:

- memory against the floor and the computed required and recommended values,
  each compared with ``MemTotal`` at the support matrix's tolerance;
- the disk known at install, per mount point, item by item (a shortfall is
  listed with what is missing where);
- the expected growth of what grows with use, which is never a reason to
  refuse.

An item already on the host (a pulled image, a downloaded model, a built
golden image, the pool's loop file) takes no more space: it is listed with its
real size where that is known and counts nothing. Every host access goes
through the ``fs``/``runner`` abstractions.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from pinka_preflight import schema_check
from pinka_preflight.detect.hardware import DiskInfo, disk_info
from pinka_preflight.fs import FileSystem
from pinka_preflight.runner import Runner

PREFLIGHT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUIREMENTS_JSON = os.path.join(PREFLIGHT_DIR, "requirements.json")
PLAN_SCHEMA = os.path.join(PREFLIGHT_DIR, "schema", "plan.schema.json")
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
        days_until_full: The day the disk fills at that rate, or None if the
            retention keeps it below the free space for ten years.
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
    """The feature switches of the plan on this host (§2.3).

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
            required value, a disk shortfall).
        degraded: Why it fits only with consent (memory below the recommended value).
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
    degraded: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

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
    path: str | None, defaults: Mapping[str, Any], srv_path: str | None = None
) -> tuple[dict[str, Any], str]:
    """The effective plan: the defaults, the research space given, then the file's keys.

    Args:
        path: The plan file (``--plan``), or None for the defaults.
        defaults: The configured defaults (``requirements.json`` ``defaults``).
        srv_path: The research space from ``--srv-path``, if given.

    Returns:
        ``(plan, label)``; the label is ``defaults`` or the path.

    Raises:
        PlanError: If the file is unreadable or the plan does not match its schema.
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
        raise PlanError(
            f"plan ({label}): an inference node runs only the inference stack; "
            "it needs inference: auto"
        )
    return plan, label


def switches(plan: Mapping[str, Any], features: Mapping[str, bool]) -> Switches:
    """What the switches leave on, on this node with these features (§2.3).

    Args:
        plan: The effective plan.
        features: The detected feature flags.

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
    else:
        serves, why = True, f"gpu_inference on a {role} node, inference: auto"
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
    """Fill the training cap: the configured one, or computed from the host (§2.3).

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
        # A loop pool of incus_pool_size: auto comes last (_auto_pool): it
        # follows the free space left after every other item.
        if pool["source"] == "loop" and pool["size_gib"] is not None:
            add(
                "pool",
                "the Incus pool (loop file)",
                str(disk["pool_file"]),
                size=float(pool["size_gib"]),
                present=fs.exists(str(disk["pool_file"])),
                basis="configured",
                source="incus_pool_size: a loop file counts fully",
            )
        elif pool["source"] == "device":
            notes.append("the Incus pool is a dedicated device: on no mount point")
        for name in plan["images"]:
            blueprint = data["blueprints"].get(name)
            if blueprint is None:
                raise PlanError(f"plan: unknown golden image blueprint {name!r}")
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


def _measure(fs: FileSystem, runner: Runner, path: str, measured: dict[str, DiskInfo]) -> DiskInfo:
    """``disk_info`` once per path."""
    if path not in measured:
        measured[path] = disk_info(fs, runner, path)
    return measured[path]


def _growth_caps(data: Mapping[str, Any], plan: Mapping[str, Any]) -> float:
    """What the stores below the research space hold at their retention (GiB)."""
    total = 0.0
    retention = plan["retention"]
    for spec in data["growth"]:
        if plan["node_role"] not in spec["roles"] or not spec.get("retention"):
            continue
        cap = float(spec["gib_per_day"]) * float(retention[spec["retention"]])
        size_cap = float(retention.get(spec.get("size_cap") or "", 0) or 0)
        total += min(cap, size_cap) if size_cap > 0 else cap
    return total


def _pool(
    data: Mapping[str, Any],
    plan: Mapping[str, Any],
    entries: list[tuple[DiskItem, str]],
    *,
    features: Mapping[str, bool],
    fs: FileSystem,
    runner: Runner,
    measured: dict[str, DiskInfo],
    warnings: list[str],
) -> dict[str, Any]:
    """The pool's size, and for incus_pool_size: auto its loop file item (§2.3).

    The computed size: the golden images and the concurrent targets' root
    disks (or the build instance's, when larger) with the margin, rounded up
    to whole GiB, limited by the free space on the loop file's mount point
    minus pinka's other needs there, never below the floor. The item is
    appended to ``entries``.
    """
    pool = plan["pool"]
    if pool["source"] == "device":
        return {"source": "device", "size_gib": None, "basis": "configured", "detail": ""}
    if pool["size_gib"] is not None:
        size = float(pool["size_gib"])
        return {
            "source": "loop",
            "size_gib": gib(size),
            "basis": "configured",
            "detail": f"incus_pool_size {text_gib(size)}",
        }
    spec, roots = data["pool"], data["target_root_gib"]
    path = str(data["disk"]["pool_file"])
    images = sum(float(data["blueprints"][n]["estimated_size_gib"]) for n in plan["images"])
    target = float(roots["vm" if features.get("vm_targets") else "container"])
    count = int(plan["concurrent_targets"])
    running = count * (target + float(roots["probe_client"]))
    build = float(roots["build"]) if plan["images"] else 0.0
    base = images + max(running, build)
    wanted = math.ceil(base * (1 + float(spec["margin"])) - EPSILON)
    floor = int(spec["floor_gib"])
    disks = (
        f"{count} x ({text_gib(target)} target + "
        f"{text_gib(float(roots['probe_client']))} probe client) root disks"
    )
    if build > running:
        disks = f"the {text_gib(build)} build instance's root disk (larger than {disks})"
    elif build:
        disks += f" (the {text_gib(build)} build instance is not larger)"
    detail = (
        f"golden images {text_gib(images)} + {disks} + {float(spec['margin']):.0%} = {wanted} GiB"
    )
    info = _measure(fs, runner, path, measured)
    mount = info.mount or info.checked_path
    if fs.exists(path):
        size = float(wanted)
        detail = "the existing pool keeps its size (Incus pools are never rewritten)"
    elif info.free_bytes is None:
        size = float(max(floor, wanted))
        warnings.append(f"disk: the free space of {mount} could not be measured for the pool")
    else:
        others = sum(
            item.size_gib
            for item, item_path in entries
            if not item.present
            and (_measure(fs, runner, item_path, measured).mount or item_path) == mount
        )
        srv = _measure(fs, runner, str(plan["srv_path"]), measured)
        growth = _growth_caps(data, plan) if (srv.mount or srv.checked_path) == mount else 0.0
        available = info.free_bytes / GIB - others - growth
        size = float(max(floor, min(wanted, math.floor(available + EPSILON))))
        detail += (
            f"; {available:.1f} GiB free on {mount} after pinka's other needs there "
            f"({text_gib(others)} at install"
            + (f", {text_gib(growth)} the stores hold at their retention" if growth else "")
            + ")"
        )
        if size < wanted:
            floored = f" (the floor of {floor} GiB)" if size == floor else ""
            detail += f", so {size:g} GiB{floored}"
    entries.append(
        (
            DiskItem(
                "pool",
                "the Incus pool (loop file)",
                path,
                "",
                size,
                fs.exists(path),
                0.0,
                "estimated",
                "incus_pool_size: auto, computed (preflight/requirements.yml pool)",
            ),
            path,
        )
    )
    return {"source": "loop", "size_gib": gib(size), "basis": "computed", "detail": detail}


def _up(value: float) -> float:
    """Round up to a tenth, so that a small shortfall never reads as 0.0."""
    return math.ceil(value * 10 - 1e-9) / 10


def _distribute(items: list[DiskItem], shortfall: float) -> None:
    """Mark the missing part of the items, the largest first (the one to shrink)."""
    remaining = shortfall
    for item in sorted(items, key=lambda i: -i.size_gib):
        if remaining <= 0:
            break
        if item.present:
            continue
        item.missing_gib = round(min(item.size_gib, remaining), 4)
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
            check.shortfall_gib = gib(needed - free)
            _distribute(check.items, needed - free)
            missing = [
                f"{i.title}: {text_gib(i.size_gib)}, missing {text_gib(_up(i.missing_gib))} on "
                f"{check.mount}"
                for i in check.items
                if i.missing_gib
            ]
            req.unsupported.append(
                f"disk: {check.mount} has {free:.2f} GiB free and these choices need "
                f"{needed:.2f} GiB there: " + "; ".join(missing)
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
        used = sum(m.required_gib for m in req.disks if m.mount == mount)
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

    Returns:
        The requirements, with what does not fit.

    Raises:
        PlanError: If the plan names an unknown golden image blueprint.
    """
    notes: list[str] = []
    sw = switches(plan, features)
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
            measured=measured,
            warnings=req.warnings,
        )
    measured = _disks(entries, fs, runner, req, measured)
    srv = str(plan["srv_path"])
    srv_info = measured.get(f"{srv.rstrip('/')}/models") or disk_info(fs, runner, srv)
    req.growth = _growth(data, plan, req, srv_info)
    return req
