"""Validate a platform release: manifest, names, invariants and files (§7.14 step 4)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pinka_platform import checks, names, release, schema
from pinka_platform.contract import Contract, size_bytes
from pinka_platform.loader import MANIFEST_NAME, load_file, load_text
from pinka_platform.problems import ManifestError, Problem


@dataclass
class Result:
    """The outcome of a validation.

    Attributes:
        manifest: The parsed manifest, or None if it could not be read.
        problems: Every reason to refuse it; empty when it is valid.
    """

    manifest: dict[str, Any] | None
    problems: list[Problem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Whether the manifest is valid."""
        return self.manifest is not None and not self.problems


def check_manifest(
    manifest: dict[str, Any], contract: Contract, host: names.HostFacts | None = None
) -> list[Problem]:
    """Check a parsed manifest: schema, invariants, derived names.

    The invariant and name checks run only on a manifest that passed the
    schema, since they rely on its shape.

    Args:
        manifest: The parsed manifest.
        contract: The contract.
        host: Facts of the host, or None to skip the host checks.

    Returns:
        Problems.
    """
    found = [
        Problem("schema", where or "manifest", message)
        for where, message in schema.errors(manifest, contract.schema)
    ]
    if found:
        return found
    found += checks.check_all(manifest, contract)
    found += names.check(
        names.derive(manifest, contract), names.derived_paths(manifest), contract, host
    )
    return found


def validate_text(text: str, contract: Contract, host: names.HostFacts | None = None) -> Result:
    """Validate manifest text (no release directory).

    Args:
        text: The manifest.
        contract: The contract.
        host: Facts of the host, or None to skip the host checks.

    Returns:
        The result.
    """
    try:
        manifest = load_text(text)
    except ManifestError as exc:
        return Result(None, [exc.problem])
    return Result(manifest, check_manifest(manifest, contract, host))


def validate_release(
    directory: Path,
    contract: Contract,
    host: names.HostFacts | None = None,
    *,
    built: bool = False,
) -> Result:
    """Validate a staged release directory.

    Args:
        directory: The release (``platform-manifest.yaml`` at its root).
        contract: The contract.
        host: Facts of the host, or None to skip the host checks.
        built: Whether the build has run (then commands below ``.venv/`` must exist,
            and an agent's environment must hold only its package and dependencies).

    Returns:
        The result.
    """
    try:
        manifest = load_file(directory / MANIFEST_NAME, int(contract.caps["manifest_bytes"]))
    except ManifestError as exc:
        return Result(None, [exc.problem])
    skip = release.BUILT_ENTRIES if built and manifest.get("runtime") == "python-uv" else ()
    problems = release.check_symlinks(directory, skip)
    problems += release.check_special_files(directory)
    problems += check_manifest(manifest, contract, host)
    if not any(p.code == "schema" for p in problems):
        problems += release.check_files(directory, manifest, built=built)
    return Result(manifest, problems)


def summary(manifest: Mapping[str, Any], contract: Contract) -> dict[str, Any]:
    """Summarize a valid manifest for the operator (installer review screen, --inspect-platform).

    Args:
        manifest: A valid manifest.
        contract: The contract.

    Returns:
        A JSON-compatible summary.
    """
    p = manifest["name"]
    sockets = manifest.get("sockets", [])
    return {
        "name": p,
        "version": manifest["version"],
        "runtime": manifest["runtime"],
        "requires": list(manifest.get("requires", [])),
        "services": [
            {
                "name": s["name"],
                "profile": s["profile"],
                "account": names.account(p, s["name"]),
                "relaxations": list(s.get("relaxations", [])),
                "incus_projects": list(contract.profile(s["profile"]).data["incus_projects"]),
                "restricted_data": contract.profile(s["profile"]).data["restricted_data"] == "read",
            }
            for s in manifest["services"]
        ],
        "jobs": [
            {"name": j["name"], "image": j["image"], "gpus": j["resources"]["gpus"]}
            for j in manifest.get("jobs", [])
        ],
        "sockets": [
            {
                "service": s["service"],
                "name": s["name"],
                "clients": list(s["clients"]),
                "agent_dedicated": bool(s.get("untrusted_clients", False)),
            }
            for s in sockets
        ],
        "content": [
            {"name": c["name"], "restricted": c["restricted"], "required": c["required"]}
            for c in manifest.get("content", [])
        ],
        "tools": list(manifest.get("tools", [])),
        "cli": manifest.get("cli", {}).get("name"),
    }


def caps_warnings(manifest: Mapping[str, Any], caps: Mapping[str, Any]) -> list[str]:
    """The jobs whose resources exceed this host's ``training_caps`` (§7.18, §8.1).

    The contract's own caps refuse a manifest; the host's are lower and only
    warned about here, because the GPU arbiter refuses every run of such a
    job.

    Args:
        manifest: A valid manifest.
        caps: The host's ``training_caps`` (``memory_max``, ``cpus``,
            ``timeout_seconds``); a cap left out is not compared.

    Returns:
        One line per job and resource over the host's cap.
    """
    out = []
    for job in manifest.get("jobs", []):
        res, name = job["resources"], job["name"]
        if "memory_max" in caps and size_bytes(res["memory_max"]) > size_bytes(
            str(caps["memory_max"])
        ):
            out.append(f"job {name}: memory_max {res['memory_max']} > {caps['memory_max']}")
        if "cpus" in caps and float(res["cpus"]) > float(caps["cpus"]):
            out.append(f"job {name}: cpus {res['cpus']} > {caps['cpus']}")
        if "timeout_seconds" in caps and int(job["timeout_seconds"]) > int(caps["timeout_seconds"]):
            out.append(
                f"job {name}: timeout_seconds {job['timeout_seconds']} > {caps['timeout_seconds']}"
            )
    return [f"{line} (this host's training_caps; the GPU arbiter refuses its runs)" for line in out]


def summary_text(data: Mapping[str, Any]) -> str:
    """Render a summary as text.

    Args:
        data: The output of :func:`summary`.

    Returns:
        Lines of text.
    """
    lines = [
        f"platform {data['name']} {data['version']} ({data['runtime']})",
        f"requires: {', '.join(data['requires']) or 'nothing'}",
        "services:",
    ]
    for s in data["services"]:
        extras = []
        if s["incus_projects"]:
            extras.append("Incus " + "/".join(s["incus_projects"]))
        if s["restricted_data"]:
            extras.append("restricted data")
        if s["relaxations"]:
            extras.append("relaxation " + ", ".join(s["relaxations"]))
        tail = f" ({'; '.join(extras)})" if extras else ""
        lines.append(f"  {s['name']}: {s['profile']} as {s['account']}{tail}")
    for j in data["jobs"]:
        lines.append(f"job {j['name']}: {j['image']} ({j['gpus']} GPU)")
    for s in data["sockets"]:
        mark = "  [dedicated to the agent]" if s["agent_dedicated"] else ""
        lines.append(f"socket {s['service']}/{s['name']}: {', '.join(s['clients'])}{mark}")
    for c in data["content"]:
        lines.append(f"content {c['name']}{' (restricted)' if c['restricted'] else ''}")
    if data["cli"]:
        lines.append(f"cli: {data['cli']}")
    return "\n".join(lines) + "\n"
