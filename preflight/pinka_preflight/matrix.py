"""Evaluation of the support matrix (BUILD_PROMPT §2.3, §2.4).

The decision is data-driven: ``support-matrix.yml`` (compiled to
``support-matrix.json``) holds every rule; this module only interprets it.
Detection results are first flattened into *facts* (``kvm_usable``,
``gpu_vram_gib``, ``wsl_conf_systemd`` ...); matrix conditions and feature
requirements refer to facts by name.

Outcome precedence: ``unsupported`` > ``needs_action`` > ``degraded`` >
``supported``. A failed core requirement normally means ``unsupported``; it
becomes ``needs_action`` when a matching provider rule lists it under
``resolves`` (for example, systemd is not PID 1 on WSL2 because ``wsl.conf``
does not enable it).

The operator's choices given on the command line (``choices``: the
``install_nvidia_driver`` mode, consent to MOK enrollment) are facts too. A
``needs_action`` rule with ``planned_when`` becomes a *planned action* (a
warning, recorded in the profile) when those conditions also hold: bootstrap
then does the action itself (approved deviation I1).
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pinka_preflight.detect.conflicts import ConflictReport
from pinka_preflight.detect.environment import Environment
from pinka_preflight.detect.gpu import GpuInfo
from pinka_preflight.detect.hardware import Hardware
from pinka_preflight.detect.kernel import KernelInfo
from pinka_preflight.detect.network import EndpointResult
from pinka_preflight.detect.os_release import OsRelease

MATRIX_JSON = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "support-matrix.json"
)
TIER_ORDER = ("supported", "tested", "experimental", "unsupported")
EXIT_CODES = {
    "supported": 0,
    "degraded": 10,
    "unsupported": 20,
    "needs_action": 30,
    "preflight_error": 40,
}
OUTCOME_MEANINGS = {
    "supported": (
        "All core requirements met; features computed.",
        "Bootstrap continues.",
    ),
    "degraded": (
        "Core requirements met, one or more optional features unavailable.",
        "Bootstrap continues only with --accept-degraded or interactive confirmation that lists "
        "every disabled feature and its research impact.",
    ),
    "unsupported": (
        "A core requirement failed, the OS, architecture or environment is unsupported, or a "
        "blocking conflict exists.",
        "Bootstrap aborts; --force-unsupported continues and records overrides.force_unsupported "
        "in the host profile and every environment fingerprint.",
    ),
    "needs_action": (
        "Fixable by operator action (reboot, wsl --terminate, enroll MOK, remove a conflict).",
        "Bootstrap prints the exact remediation steps and aborts; re-run after the action.",
    ),
    "preflight_error": (
        "Detection itself failed unexpectedly.",
        "Bootstrap aborts with the traceback in the log.",
    ),
}

Facts = dict[str, Any]

# Values of --nvidia-driver (the host's install_nvidia_driver setting).
NVIDIA_DRIVER_MODES = ("auto", "manual", "skip")
# The operator's choices when none are given: bootstrap's defaults.
DEFAULT_CHOICES: dict[str, Any] = {"nvidia_driver_mode": "auto", "mok_enrollment_accepted": False}
# The fact a requirement with a remediation compares ({actual} of its text).
REMEDIATION_FACTS = {
    "driver_cuda_min": "gpu_driver_cuda",
    "gpu_compute_capability_min": "gpu_compute_capability",
}


@dataclass
class Check:
    """Result of one core requirement or minimum.

    Attributes:
        id: Requirement identifier.
        ok: The requirement is met.
        detail: What was found.
    """

    id: str
    ok: bool
    detail: str


@dataclass
class Decision:
    """The preflight decision.

    Attributes:
        outcome: ``supported``, ``degraded``, ``unsupported`` or ``needs_action``.
        exit_code: Exit code for the outcome (§2.4).
        tier: Worst of the OS, architecture and environment tiers.
        os_tier: Tier of the matched OS rule.
        arch_tier: Tier of the matched architecture rule.
        family: Distribution family, if known.
        pkg_manager: Package manager of the family.
        release_model: ``fixed``, ``fast`` or ``rolling``.
        release: The distribution's own release identity.
        parent_release: Release identity used for third-party repositories.
        provider: Provider role (``baremetal``, ``wsl2``, ``vm``) or None.
        features: Feature flags.
        disabled_features: Features that make the host degraded, with impact text.
        storage_backend: ``btrfs``, ``lvm`` or None.
        core_requirements: Core requirement results.
        minimums: Hardware minimum results.
        unsupported_reasons: Why the host is unsupported.
        needs_action: Operator actions, as ``{"id", "action"}``.
        planned_actions: Actions bootstrap carries out with the operator's
            consent instead of stopping, as ``{"id", "action"}``.
        warnings: Warnings.
        notes: Informational notes.
    """

    outcome: str
    exit_code: int
    tier: str
    os_tier: str
    arch_tier: str
    family: str | None
    pkg_manager: str | None
    release_model: str | None
    release: str | None
    parent_release: str | None
    provider: str | None
    features: dict[str, bool] = field(default_factory=dict)
    disabled_features: list[dict[str, str]] = field(default_factory=list)
    storage_backend: str | None = None
    core_requirements: list[Check] = field(default_factory=list)
    minimums: list[Check] = field(default_factory=list)
    unsupported_reasons: list[str] = field(default_factory=list)
    needs_action: list[dict[str, str]] = field(default_factory=list)
    planned_actions: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def load_matrix(path: str = MATRIX_JSON) -> dict[str, Any]:
    """Load the compiled support matrix.

    Args:
        path: Path of ``support-matrix.json``.

    Returns:
        The matrix.
    """
    with open(path, encoding="utf-8") as handle:
        data: dict[str, Any] = json.load(handle)
    return data


def _version_tuple(text: str) -> tuple[int, ...]:
    parts: list[int] = []
    for p in text.split("."):
        digits = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def os_rule_matches(match: Mapping[str, Any], osr: OsRelease | None) -> bool:  # noqa: PLR0911, PLR0912
    """Evaluate one OS ``match`` block; every key must hold.

    Args:
        match: Conditions (``id``, ``id_in``, ``version_id``, ``version_id_in``,
            ``version_major_in``, ``id_like_contains``, ``id_like_contains_any``,
            ``id_or_like_in``, ``any``).
        osr: Parsed os-release, or None if missing.

    Returns:
        True if the rule matches.
    """
    if match.get("any"):
        return True
    if osr is None:
        return False
    for key, wanted in match.items():
        if key == "id":
            if osr.id != wanted:
                return False
        elif key == "id_in":
            if osr.id not in wanted:
                return False
        elif key == "version_id":
            if osr.version_id != wanted:
                return False
        elif key == "version_id_in":
            if osr.version_id not in wanted:
                return False
        elif key == "version_major_in":
            if osr.version_major not in wanted:
                return False
        elif key == "id_like_contains":
            if wanted not in osr.id_like:
                return False
        elif key == "id_like_contains_any":
            if not set(wanted) & set(osr.id_like):
                return False
        elif key == "id_or_like_in":
            if osr.id not in wanted and not set(wanted) & set(osr.id_like):
                return False
        else:
            raise ValueError(f"unknown os match key {key!r}")
    return True


def match_os(matrix: Mapping[str, Any], osr: OsRelease | None) -> dict[str, Any]:
    """Return the first matching OS rule.

    Args:
        matrix: Support matrix.
        osr: Parsed os-release.

    Returns:
        The rule (the catch-all rule if nothing else matches).
    """
    for rule in matrix["os"]:
        if os_rule_matches(rule["match"], osr):
            return dict(rule)
    raise ValueError("support matrix has no catch-all OS rule")


def match_arch(matrix: Mapping[str, Any], machine: str) -> dict[str, Any]:
    """Return the first matching architecture rule.

    Args:
        matrix: Support matrix.
        machine: ``platform.machine()``.

    Returns:
        The rule.
    """
    for rule in matrix["arch"]:
        m = rule["match"]
        if m.get("any") or m.get("machine") == machine:
            return dict(rule)
    raise ValueError("support matrix has no catch-all arch rule")


def release_identity(
    rule: Mapping[str, Any], osr: OsRelease | None
) -> tuple[str | None, str | None]:
    """Return the release identity and the parent release identity.

    Args:
        rule: Matched OS rule.
        osr: Parsed os-release.

    Returns:
        ``(release, parent_release)``. Derivatives take the parent identity from
        the key named in ``parent_from``; ``VERSION_ID`` yields its major number.
    """
    if osr is None:
        return None, None
    own = osr.version_codename or osr.version_id
    parent_from = rule.get("parent_from")
    if not parent_from:
        return own, own
    if parent_from == "VERSION_ID":
        return own, osr.version_major
    return own, osr.raw.get(str(parent_from)) or None


def build_facts(
    env: Environment,
    kernel: KernelInfo,
    hw: Hardware,
    gpu: GpuInfo,
    conflicts: ConflictReport,
) -> Facts:
    """Flatten detection results into named facts.

    Args:
        env: Environment classification.
        kernel: Kernel facts.
        hw: Hardware facts.
        gpu: GPU facts.
        conflicts: Conflict report.

    Returns:
        Facts referenced by the matrix.
    """
    wsl = env.wsl
    nvidia = gpu.vendor == "nvidia"
    return {
        "environment": env.env_class,
        "hypervisor": env.hypervisor,
        "systemd_pid1": kernel.systemd_pid1,
        "cgroup2_unified": kernel.cgroup2,
        "cgroup2_controllers": list(kernel.cgroup2_controllers),
        "dev_net_tun": kernel.tun,
        "kvm_usable": kernel.kvm_usable,
        "btrfs_available": kernel.feature_present("btrfs"),
        "dm_thin_pool_available": kernel.module_available("dm_thin_pool"),
        "secure_boot": kernel.secure_boot,
        "nvidia_gpu_present": nvidia and gpu.present,
        "nvidia_gpu_usable": nvidia and gpu.usable,
        "nvidia_gpu_open_module_supported": nvidia and bool(gpu.open_module_drivers),
        "nvidia_gpu_proprietary_only": (
            nvidia and not gpu.open_module_drivers and bool(gpu.proprietary_only_drivers)
        ),
        "nvidia_driver_installed": gpu.driver_installed,
        "nvidia_driver_loaded": gpu.driver_loaded,
        "gpu_vram_gib": gpu.vram_gib,
        "gpu_compute_capability": gpu.compute_capability,
        "gpu_driver_cuda": gpu.driver_cuda,
        "wsl_conf_systemd": wsl.systemd if wsl else None,
        "wsl_interop_enabled": wsl.interop_enabled if wsl else None,
        "wsl_automount_enabled": wsl.automount if wsl else None,
        "memory_gib": hw.memory_gib,
        "cpu_threads": hw.cpu_threads_effective,
        "free_disk_var_lib_gib": hw.disks["var_lib"].free_gib if "var_lib" in hw.disks else None,
        "free_disk_srv_pinka_gib": hw.disks["srv_pinka"].free_gib
        if "srv_pinka" in hw.disks
        else None,
        "blocking_conflicts": sum(1 for c in conflicts.conflicts if c.severity == "blocking"),
    }


def condition_holds(when: Mapping[str, Any], facts: Facts) -> bool:
    """Evaluate a ``when`` block: ``fact: value`` equality and ``fact_in: [...]``.

    Args:
        when: Conditions.
        facts: Host facts.

    Returns:
        True if all conditions hold.
    """
    for key, wanted in when.items():
        if key.endswith("_in") and isinstance(wanted, list):
            if facts.get(key[:-3]) not in wanted:
                return False
        elif facts.get(key) != wanted:
            return False
    return True


def requirement_met(req: Any, facts: Facts) -> tuple[bool, str]:  # noqa: ANN401 - YAML value
    """Evaluate one feature requirement.

    Args:
        req: A fact name (must be true) or a one-key mapping:
            ``gpu_vram_gib_min``, ``gpu_compute_capability_min``, ``environment_in``,
            ``driver_cuda_min`` (major.minor compared, as ``nvidia-smi`` reports it).
        facts: Host facts.

    Returns:
        ``(met, label)``.
    """
    if isinstance(req, str):
        return bool(facts.get(req)), req
    ((key, value),) = req.items()
    if key == "gpu_vram_gib_min":
        vram = facts.get("gpu_vram_gib")
        return vram is not None and float(vram) >= float(value), f"VRAM >= {value} GiB"
    if key == "gpu_compute_capability_min":
        cap = facts.get("gpu_compute_capability")
        ok = cap is not None and _version_tuple(str(cap)) >= _version_tuple(str(value))
        return ok, f"compute capability >= {value}"
    if key == "environment_in":
        return facts.get("environment") in value, f"environment in {value}"
    if key == "driver_cuda_min":
        cuda = facts.get("gpu_driver_cuda")
        ok = cuda is not None and _version_tuple(str(cuda))[:2] >= cuda_major_minor(value)
        return ok, f"driver CUDA >= {major_minor(value)}"
    raise ValueError(f"unknown requirement {key!r}")


def cuda_major_minor(version: Any) -> tuple[int, ...]:  # noqa: ANN401 - YAML value
    """Return the major and minor number of a CUDA version.

    Args:
        version: For example ``13.0.2``.

    Returns:
        For example ``(13, 0)``.
    """
    return _version_tuple(str(version))[:2]


def major_minor(version: Any) -> str:  # noqa: ANN401 - YAML value
    """Return a CUDA version as ``major.minor``.

    Args:
        version: For example ``13.0.2``.

    Returns:
        For example ``13.0``.
    """
    return ".".join(str(n) for n in cuda_major_minor(version))


def _remediation(spec: Mapping[str, Any], facts: Facts) -> str:
    """Remediation of a feature's unmet requirements that have one.

    Args:
        spec: The feature's matrix entry.
        facts: Host facts.

    Returns:
        The remediation texts, joined; empty when no unmet requirement has one
        or a fact the feature needs (for example a usable GPU) is missing.
    """
    texts: list[str] = []
    if not all(facts.get(req) for req in spec["requires"] if isinstance(req, str)):
        return ""
    for req in spec["requires"]:
        if isinstance(req, str) or requirement_met(req, facts)[0]:
            continue
        ((key, value),) = req.items()
        variants = spec.get("remediation", {}).get(key)
        if not variants:
            continue
        actual = facts.get(REMEDIATION_FACTS[key])
        if actual is None:
            text = variants["unknown"]
        else:
            text = variants.get(str(facts.get("environment")), variants["default"])
        texts.append(str(text).format(actual=actual, required=major_minor(value)))
    return " ".join(texts)


def _core_checks(matrix: Mapping[str, Any], facts: Facts, kernel: KernelInfo) -> list[Check]:
    checks: list[Check] = []
    for req in matrix["core_requirements"]:
        rid = req["id"]
        values = req.get("values", [])
        if rid == "cgroup2_controllers":
            missing = [v for v in values if v not in facts["cgroup2_controllers"]]
            checks.append(
                Check(rid, not missing, f"missing: {', '.join(missing)}" if missing else "present")
            )
        elif rid == "kernel_features":
            missing = [v for v in values if not kernel.feature_present(v)]
            checks.append(
                Check(rid, not missing, f"missing: {', '.join(missing)}" if missing else "present")
            )
        elif rid == "no_blocking_conflicts":
            n = int(facts["blocking_conflicts"])
            checks.append(Check(rid, n == 0, f"{n} blocking conflict(s)"))
        elif rid in facts:
            ok = bool(facts[rid])
            checks.append(Check(rid, ok, "yes" if ok else "no"))
        else:
            raise ValueError(f"unknown core requirement {rid!r}")
    return checks


def _minimum_checks(matrix: Mapping[str, Any], facts: Facts) -> list[Check]:
    mins = matrix["minimums"]
    items: list[tuple[str, Any, Mapping[str, Any]]] = [
        ("memory_gib", facts.get("memory_gib"), mins["memory_gib"]),
        ("cpu_threads", facts.get("cpu_threads"), mins["cpu_threads"]),
        (
            "free_disk_var_lib_gib",
            facts.get("free_disk_var_lib_gib"),
            mins["free_disk_gib"]["var_lib"],
        ),
        (
            "free_disk_srv_pinka_gib",
            facts.get("free_disk_srv_pinka_gib"),
            mins["free_disk_gib"]["srv_pinka"],
        ),
    ]
    checks: list[Check] = []
    for name, value, limits in items:
        if value is None:
            checks.append(Check(name, True, "unknown"))
            continue
        ok = float(value) >= float(limits["required"])
        checks.append(
            Check(
                name,
                ok,
                f"{value} (required {limits['required']}, recommended {limits['recommended']})",
            )
        )
    return checks


def _features(
    matrix: Mapping[str, Any], facts: Facts
) -> tuple[dict[str, bool], list[dict[str, str]], list[str]]:
    flags: dict[str, bool] = {}
    unmet: dict[str, list[str]] = {}
    for name, spec in matrix["features"].items():
        results = [requirement_met(r, facts) for r in spec["requires"]]
        flags[name] = all(ok for ok, _ in results)
        unmet[name] = [label for ok, label in results if not ok]
    disabled: list[dict[str, str]] = []
    notes: list[str] = []
    for name, spec in matrix["features"].items():
        if flags[name]:
            continue
        only_if = spec.get("only_if_missing")
        if only_if and flags.get(str(only_if)):
            continue
        detail = f"{name} disabled (unmet: {', '.join(unmet[name])})"
        if spec["on_missing"] == "degrade":
            entry = {
                "feature": name,
                "impact": str(spec.get("impact", "")),
                "unmet": ", ".join(unmet[name]),
            }
            remediation = _remediation(spec, facts)
            if remediation:
                entry["remediation"] = remediation
            disabled.append(entry)
        else:
            notes.append(f"{detail}: {spec.get('impact', 'fallback in use')}")
    return flags, disabled, notes


def evaluate(  # noqa: PLR0912, PLR0915 - one pass over the documented matrix sections
    matrix: Mapping[str, Any],
    *,
    osr: OsRelease | None,
    machine: str,
    env: Environment,
    kernel: KernelInfo,
    hw: Hardware,
    gpu: GpuInfo,
    conflicts: ConflictReport,
    endpoints: Sequence[EndpointResult],
    choices: Mapping[str, Any] | None = None,
) -> Decision:
    """Apply the support matrix to the detection results.

    Args:
        matrix: Compiled support matrix.
        osr: Parsed os-release.
        machine: CPU architecture.
        env: Environment classification.
        kernel: Kernel facts.
        hw: Hardware facts.
        gpu: GPU facts.
        conflicts: Conflict report.
        endpoints: Network probe results.
        choices: The operator's choices as facts: ``nvidia_driver_mode``
            (``auto``, ``manual`` or ``skip``, default ``auto``) and
            ``mok_enrollment_accepted`` (default false).

    Returns:
        The decision.
    """
    facts = build_facts(env, kernel, hw, gpu, conflicts)
    facts.update(DEFAULT_CHOICES)
    facts.update(choices or {})
    # Who would install a missing driver: bootstrap only for a GPU the pinned
    # release's open kernel module supports (§2.2), the operator with manual.
    mode = facts["nvidia_driver_mode"]
    supported = facts["nvidia_gpu_open_module_supported"]
    facts["nvidia_driver_planned"] = (
        "manual" if mode == "manual" else "auto" if mode == "auto" and supported else "none"
    )
    os_rule = match_os(matrix, osr)
    arch_rule = match_arch(matrix, machine)
    env_rule = matrix["environment"].get(env.env_class, matrix["environment"]["unknown"])
    family = os_rule.get("family")
    release, parent = release_identity(os_rule, osr)
    tiers = [str(os_rule["tier"]), str(arch_rule["tier"]), str(env_rule["tier"])]
    tier = max(tiers, key=TIER_ORDER.index)

    d = Decision(
        outcome="supported",
        exit_code=0,
        tier=tier,
        os_tier=str(os_rule["tier"]),
        arch_tier=str(arch_rule["tier"]),
        family=family,
        pkg_manager=matrix["families"][family]["pkg"] if family else None,
        release_model=os_rule.get("release_model"),
        release=release,
        parent_release=parent,
        provider=env_rule.get("provider"),
    )
    for rule in (os_rule, arch_rule, env_rule):
        if rule["tier"] == "unsupported":
            d.unsupported_reasons.append(str(rule.get("reason", "unsupported")))
        if rule.get("note"):
            d.notes.append(str(rule["note"]))
    if tier in matrix.get("tier_notes", {}) and tier != "unsupported":
        d.notes.append(str(matrix["tier_notes"][tier]))
    model_note = matrix.get("release_model_notes", {}).get(d.release_model or "")
    if model_note:
        d.warnings.append(str(model_note))
    if family and osr and os_rule.get("parent_from") and not parent:
        d.warnings.append(
            f"parent release identity {os_rule['parent_from']} is not set in os-release"
        )

    d.core_requirements = _core_checks(matrix, facts, kernel)
    d.minimums = _minimum_checks(matrix, facts)
    mins = matrix["minimums"]
    for check in d.minimums:
        value = facts.get(check.id)
        if check.detail == "unknown":
            d.warnings.append(f"{check.id}: could not be measured")
            continue
        limits = {
            "memory_gib": mins["memory_gib"],
            "cpu_threads": mins["cpu_threads"],
            "free_disk_var_lib_gib": mins["free_disk_gib"]["var_lib"],
            "free_disk_srv_pinka_gib": mins["free_disk_gib"]["srv_pinka"],
        }[check.id]
        if not check.ok:
            d.unsupported_reasons.append(
                f"{check.id} is {value}, below the required {limits['required']}"
            )
        elif value is not None and float(value) < float(limits["recommended"]):
            d.warnings.append(
                f"{check.id} is {value}, below the recommended {limits['recommended']}"
            )

    d.features, d.disabled_features, feature_notes = _features(matrix, facts)
    d.notes.extend(feature_notes)
    for backend in matrix["storage"]["backends"]:
        if d.features.get(backend["feature"]):
            d.storage_backend = str(backend["backend"])
            break
    if d.storage_backend is None:
        d.unsupported_reasons.append(str(matrix["storage"]["none_reason"]))

    resolved: set[str] = set()
    rules = matrix.get("provider_rules", {}).get(d.provider or "", {})
    for rule in rules.get("needs_action", []):
        if not condition_holds(rule["when"], facts):
            continue
        planned_when = rule.get("planned_when")
        if planned_when is not None and condition_holds(planned_when, facts):
            d.planned_actions.append({"id": str(rule["id"]), "action": str(rule["planned"])})
            d.warnings.append(f"planned action {rule['id']}: {rule['planned']}")
            continue
        d.needs_action.append({"id": str(rule["id"]), "action": str(rule["action"])})
        resolved.update(rule.get("resolves", []))
    for rule in rules.get("warnings", []):
        if condition_holds(rule["when"], facts):
            d.warnings.append(str(rule["message"]))
    for c in conflicts.conflicts:
        if c.severity == "needs_action":
            d.needs_action.append({"id": c.id, "action": f"{c.message}. {c.remediation}"})
        elif c.severity == "warning":
            d.warnings.append(f"{c.message}. {c.remediation}")
    for ep in endpoints:
        if ep.status in ("dns_failed", "unreachable"):
            text = f"{ep.host} ({ep.purpose}) is not reachable: {ep.detail}"
            if ep.required:
                d.needs_action.append(
                    {
                        "id": f"endpoint:{ep.id}",
                        "action": f"{text}. Check DNS, proxy and firewall settings.",
                    }
                )
            else:
                d.warnings.append(text)
    if env.confidence == "low":
        d.warnings.append(
            "environment detection confidence is low: "
            + ("; ".join(env.conflicts_between_signals) or "few signals")
        )

    for check in d.core_requirements:
        if check.ok or check.id in resolved:
            continue
        if check.id == "no_blocking_conflicts":
            for c in conflicts.conflicts:
                if c.severity == "blocking":
                    d.unsupported_reasons.append(f"blocking conflict: {c.message}. {c.remediation}")
        else:
            remediation = next(
                (
                    r.get("remediation", "")
                    for r in matrix["core_requirements"]
                    if r["id"] == check.id
                ),
                "",
            )
            d.unsupported_reasons.append(
                f"core requirement {check.id} failed ({check.detail}). {remediation}".strip()
            )

    if d.unsupported_reasons:
        d.outcome = "unsupported"
    elif d.needs_action:
        d.outcome = "needs_action"
    elif d.disabled_features:
        d.outcome = "degraded"
    d.exit_code = EXIT_CODES[d.outcome]
    return d
