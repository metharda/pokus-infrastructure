"""The GPU memory the plan's serving and training need, against the GPU's.

Two lines, each ``fits``, ``does_not_fit`` or ``off`` (``unknown`` when the
GPU's memory could not be read):

- serving, on a ``full`` or ``inference`` node: the serving profile's stated
  minimum and the default model's expected VRAM for that profile
  (``models.catalog.yml``), both compiled into ``requirements.json``. Serving
  that does not fit installs nothing. With ``inference: auto`` on a full node
  the line is ``off`` with the reason (the host is degraded, with consent);
  with ``inference: on``, and on an inference node, it is ``does_not_fit``
  and the host unsupported.
- training, on a ``full`` node: the support matrix's minimum for
  ``gpu_training``, the only VRAM figure infra has (a job declares ``gpus``
  only). Training is ``fits`` where it runs on the GPU, else ``off`` with the
  reason.

While bootstrap installs the NVIDIA driver in this run, both lines are
``off``: the next run judges the GPU, once the driver is loaded.

Python standard library only, source-compatible with Python 3.9.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

# The statuses of a line.
FITS, DOES_NOT_FIT, OFF, UNKNOWN = "fits", "does_not_fit", "off", "unknown"


@dataclass
class VramItem:
    """What needs part of the GPU's memory.

    Attributes:
        title: What it is.
        gib: How much (GiB).
        basis: ``configured`` (a profile's or the support matrix's minimum) or
            ``estimated`` (models.catalog.yml's estimate for a model).
    """

    title: str
    gib: float
    basis: str


@dataclass
class VramLine:
    """One use of the GPU's memory.

    Attributes:
        use: ``serving`` or ``training``.
        switch: The switch's setting.
        status: ``fits``, ``does_not_fit``, ``off`` or ``unknown``.
        reason: Why it is off, does not fit or is unknown; empty when it fits.
        needed_gib: The largest item (0 when off by its switch).
        available_gib: The GPU's memory (measured), or None without a GPU.
        shortfall_gib: How much is missing (0 when it fits, and without a GPU).
        items: What needs the memory.
    """

    use: str
    switch: str
    status: str
    reason: str = ""
    needed_gib: float = 0.0
    available_gib: float | None = None
    shortfall_gib: float = 0.0
    items: list[VramItem] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """The host profile's view.

        Returns:
            JSON-compatible data.
        """
        return asdict(self)


@dataclass
class Misfit:
    """Why serving does not fit the GPU's memory.

    Attributes:
        reason: Such as ``the model x needs 10.5 GiB of VRAM, the GPU has 10.0 GiB``.
        remedy: What would fit: the deciding item's own remedy.
    """

    reason: str
    remedy: str


def _round(value: float) -> float:
    """GiB to two decimals."""
    return round(value + 1e-9, 2)


def _up(gib: float, digits: int) -> float:
    """A needed size rounded up, so that a message never understates it."""
    scale = 10.0**digits
    return math.ceil(round(gib * scale, 6)) / scale


def _two(need: float, have: float) -> tuple[str, str]:
    """A needed and a measured size for a message.

    One decimal, or two where one would read the same; the need is rounded up.
    """
    digits = 2 if f"{_up(need, 1):.1f}" == f"{have:.1f}" else 1
    return f"{_up(need, digits):.{digits}f} GiB", f"{have:.{digits}f} GiB"


def at_least(gib: float) -> str:
    """A size a GPU needs at least, for a message: rounded up to one decimal.

    Args:
        gib: The size (GiB).

    Returns:
        Such as ``11.3 GiB`` for 11.23.
    """
    return f"{_up(gib, 1):.1f} GiB"


def serving_needs(
    serving: Mapping[str, Any], plan: Mapping[str, Any]
) -> tuple[list[VramItem], str, str]:
    """What the plan's serving profile and default model need of the GPU's memory.

    Args:
        serving: ``requirements.json`` ``serving``.
        plan: The effective plan.

    Returns:
        ``(items, unusable, note)``: the items; why the model cannot be served
        with the profile at all (a model of models.catalog.yml without
        settings for it), else empty; and a note when models.catalog.yml does
        not know the model (only the profile's minimum is checked then).
    """
    name = str(plan["vllm_profile"])
    profile = serving["profiles"][name]
    items = [VramItem(f"the serving profile {name}", float(profile["min_vram_gib"]), "configured")]
    model = plan.get("model")
    if not model:
        return items, "", ""
    model_id = str(model["id"])
    estimates = serving["models"].get(model_id)
    if estimates is None:
        note = (
            f"the default model {model_id} has no VRAM estimate in models.catalog.yml: only "
            f"the serving profile {name}'s minimum is checked against the GPU's memory"
        )
        return items, "", note
    if name not in estimates:
        return items, f"the model {model_id} has no settings for the serving profile {name}", ""
    items.append(VramItem(f"the model {model_id}", float(estimates[name]), "estimated"))
    return items, "", ""


def misfit(items: Sequence[VramItem], unusable: str, vram_gib: float | None) -> Misfit | None:
    """Why serving does not fit the GPU's memory, or None.

    Args:
        items: From :func:`serving_needs`.
        unusable: Its second value.
        vram_gib: The GPU's memory; None when it could not be read.

    Returns:
        The reason and the deciding item's remedy, or None when it fits (or
        the GPU's memory is not known).
    """
    if unusable:
        return Misfit(unusable, "a model with settings for the serving profile, or another profile")
    if vram_gib is None:
        return None
    largest = max(items, key=lambda i: i.gib)
    if largest.gib <= vram_gib:
        return None
    need, have = _two(largest.gib, vram_gib)
    gpu = f"a GPU with at least {at_least(largest.gib)} of memory"
    if any(i.basis == "configured" and i.gib > vram_gib for i in items):
        # The profile's own minimum does not fit: a smaller model alone cannot help.
        remedy = f"{gpu}, or another serving profile"
    else:
        remedy = f"a smaller model, or {gpu}"
    return Misfit(f"{largest.title} needs {need} of VRAM, the GPU has {have}", remedy)


def _line(
    use: str,
    switch: str,
    status: str,
    reason: str,
    *,
    items: Sequence[VramItem],
    vram_gib: float | None,
) -> VramLine:
    needed = max((i.gib for i in items), default=0.0)
    short = 0.0
    if status in (DOES_NOT_FIT, OFF) and items and vram_gib is not None:
        short = _round(max(0.0, needed - vram_gib))
    return VramLine(
        use=use,
        switch=switch,
        status=status,
        reason=reason,
        needed_gib=_round(needed),
        available_gib=None if vram_gib is None else _round(vram_gib),
        shortfall_gib=short,
        items=[VramItem(i.title, _round(i.gib), i.basis) for i in items],
    )


def _not_available(feature: str, unmet: str, vram_gib: float | None) -> str:
    """Why a GPU feature is off: the support matrix's unmet conditions."""
    if unmet:
        return f"{feature} is not available on this host (unmet: {unmet})"
    if vram_gib is None:
        return f"{feature} is not available on this host: no usable NVIDIA GPU"
    return f"{feature} is not available on this host"


def _pending(what: str) -> str:
    """Why a line is off while bootstrap installs the NVIDIA driver in this run."""
    return (
        f"the NVIDIA driver is installed in this run: {what} is judged once it is loaded, "
        "by the next bootstrap run"
    )


def _unavailable(
    switch: str,
    status: str,
    items: Sequence[VramItem],
    *,
    vram_gib: float | None,
    minimum: float,
    unmet: str,
    driver_pending: bool,
) -> VramLine:
    """The serving line where the support matrix turned ``gpu_inference`` off."""
    if driver_pending:
        return _line("serving", switch, OFF, _pending("serving"), items=items, vram_gib=vram_gib)
    reason = _not_available("gpu_inference", unmet, vram_gib)
    need = [VramItem("the support matrix's minimum for gpu_inference", minimum, "configured")]
    return _line("serving", switch, status, reason, items=[*need, *items], vram_gib=vram_gib)


def serving_line(
    plan: Mapping[str, Any],
    features: Mapping[str, bool],
    items: Sequence[VramItem],
    why_not: Misfit | None,
    *,
    vram_gib: float | None,
    minimum: float,
    unmet: str = "",
    driver_pending: bool = False,
) -> VramLine | None:
    """The serving line, or None on a lab node (it serves nothing itself).

    Args:
        plan: The effective plan.
        features: The detected feature flags (before the switches).
        items: From :func:`serving_needs`.
        why_not: From :func:`misfit`.
        vram_gib: The GPU's memory.
        minimum: The support matrix's VRAM minimum for ``gpu_inference``.
        unmet: The support matrix's unmet conditions of ``gpu_inference``.
        driver_pending: Bootstrap installs the NVIDIA driver in this run: a
            GPU without it is judged by the next run, so the line is ``off``.

    Returns:
        The line.
    """
    role, switch = str(plan["node_role"]), str(plan["inference"])
    if role == "lab":
        return None
    if switch == "off":
        return _line(
            "serving", switch, OFF, "switched off, inference: off", items=[], vram_gib=vram_gib
        )
    status = DOES_NOT_FIT if switch == "on" or role == "inference" else OFF
    if not features.get("gpu_inference"):
        return _unavailable(
            switch, status, items, vram_gib=vram_gib, minimum=minimum, unmet=unmet,
            driver_pending=driver_pending,
        )  # fmt: skip
    if why_not:
        return _line("serving", switch, status, why_not.reason, items=items, vram_gib=vram_gib)
    if vram_gib is None:
        return _line(
            "serving", switch, UNKNOWN, "the GPU's memory is not known", items=items, vram_gib=None
        )
    return _line("serving", switch, FITS, "", items=items, vram_gib=vram_gib)


def training_line(
    plan: Mapping[str, Any],
    runs: str,
    reason: str,
    *,
    vram_gib: float | None,
    minimum: float,
    unmet: str = "",
    driver_pending: bool = False,
) -> VramLine | None:
    """The training line, or None where training is unavailable (not a full node).

    Args:
        plan: The effective plan.
        runs: Where training jobs run (the switches' ``runs``).
        reason: The switches' ``training_reason``.
        vram_gib: The GPU's memory.
        minimum: The support matrix's VRAM minimum for ``gpu_training``.
        unmet: The support matrix's unmet conditions of ``gpu_training``.
        driver_pending: Bootstrap installs the NVIDIA driver in this run: the
            line is ``off`` until the next run judges the GPU.

    Returns:
        The line.
    """
    switch = str(plan["training"]["mode"])
    if str(plan["node_role"]) != "full":
        return None
    need = [VramItem("the support matrix's minimum for gpu_training", minimum, "configured")]
    if runs == "gpu":
        if vram_gib is None:
            unknown = "the GPU's memory is not known"
            return _line("training", switch, UNKNOWN, unknown, items=need, vram_gib=None)
        return _line("training", switch, FITS, "", items=need, vram_gib=vram_gib)
    if switch == "off":
        return _line("training", switch, OFF, reason, items=[], vram_gib=vram_gib)
    if switch == "cpu":
        cpu = "jobs run their CPU-only variant on the CPU (training: cpu); no GPU memory is used"
        return _line("training", switch, OFF, cpu, items=[], vram_gib=vram_gib)
    if driver_pending:
        full = _pending("training on the GPU")
    else:
        why = _not_available("gpu_training", unmet, vram_gib)
        full = f"{why}; training: cpu would run the CPU-only variant"
    return _line("training", switch, OFF, full, items=need, vram_gib=vram_gib)


def fits_for_installer(
    serving: Mapping[str, Any],
    plan: Mapping[str, Any],
    vram_gib: float | None,
    compute_capability: str | None,
) -> dict[str, Any]:
    """Which serving profiles and models of models.catalog.yml fit this GPU, for the installer.

    A model fits a profile when both the profile's minimum and the model's
    estimate fit the GPU's memory. ``fits`` is null when the GPU's memory is
    not known. The suggested profile is the one with the largest minimum that
    fits and, where the GPU's compute capability is known, names the same
    capability.

    Args:
        serving: ``requirements.json`` ``serving``.
        plan: The effective plan (its ``vllm_profile``).
        vram_gib: The GPU's memory.
        compute_capability: The GPU's compute capability.

    Returns:
        ``profile``, ``suggested_profile`` (None when none fits), ``profiles``
        (name to its minimum, capability and whether it fits) and ``models``
        (id to profile to its estimate and whether it fits).
    """

    def fits(need: float) -> bool | None:
        return None if vram_gib is None else need <= vram_gib

    minimums = {name: float(p["min_vram_gib"]) for name, p in serving["profiles"].items()}
    profiles = {
        name: {
            "min_vram_gib": _round(minimums[name]),
            "compute_capability": str(p["compute_capability"]),
            "fits": fits(minimums[name]),
        }
        for name, p in sorted(serving["profiles"].items())
    }
    candidates = [
        (minimums[name], name)
        for name, p in profiles.items()
        if p["fits"] and (not compute_capability or p["compute_capability"] == compute_capability)
    ]
    models = {
        model_id: {
            name: {
                "expected_vram_gib": _round(float(need)),
                "fits": fits(max(float(need), minimums.get(name, 0.0))),
            }
            for name, need in sorted(estimates.items())
        }
        for model_id, estimates in sorted(serving["models"].items())
    }
    return {
        "profile": str(plan["vllm_profile"]),
        "suggested_profile": max(candidates)[1] if candidates else None,
        "profiles": profiles,
        "models": models,
    }
