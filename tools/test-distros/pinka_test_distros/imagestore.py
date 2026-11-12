"""The images stage: what a client restricted like a platform service may do.

The projects ``build`` and ``bench`` use the default project's image store
(``features.images: "false"``). The images role and doctor's
``images.golden`` rely on what a client certificate restricted to them can
do there: it can create images and aliases, but it can neither delete an
image or an alias of the store, rename an alias or point it elsewhere, nor
change an image through the image's own API. It can still change an image's
record (its properties among them) through a new image request that names
the image's fingerprint, and the stage checks that too. The guest helper's
``image-store`` command tries each operation with the incus CLI, as root's
stand-in of a golden image beside the client's own; this module says what
each must give. The refusal texts are those of the pinned Incus (6.0.7);
the report names the server's version.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pinka_platform.contract import Contract

# Incus's HTTPS address and server certificate (ansible/roles/incus:
# defaults incus_https_address, vars/<family>.yml incus_server_certificate).
ADDRESS = "127.0.0.1:8443"
SERVER_CERT = "/var/lib/incus/server.crt"
# The harness's limit for one run of the guest helper (above its incus calls' own).
STAGE_TIMEOUT = 1800.0
# The guest helper's alias prefix (guest.STORE_ALIAS).
ALIAS = "pinka-test-store"
ALLOWED = (
    "generate a client certificate",
    "trust it, restricted to the projects",
    "root imports a golden image",
    "the client imports an image with an alias",
    "the client creates an alias of the golden image",
)
# The property that request sets, and its value (guest.STORE_REWRITTEN).
REWRITTEN = ("user.pinka-test", "rewritten")
# What Incus 6.0.7 answers a trusted certificate restricted to other projects
# when the object is in the default project (internal/server/auth/driver_tls.go,
# CheckPermission); an untrusted client gets "not authorized" instead.
RESTRICTED = 'User does not have permission for project "default"'
# Each refused operation with the error Incus gives. Creating an alias that
# exists is a name check any client gets (cmd/incusd/images.go, imageAliasesPost).
REFUSED = {
    "the client creates the golden alias again": "already exists",
    # The record is rewritten first (REWRITTEN shows it); the request then
    # fails on the image's own aliases (cmd/incusd/images.go, imagesPost).
    "the client rewrites the golden image's record through a new image request": (
        "Alias already exists"
    ),
    "the client points the golden alias at its image (PUT)": RESTRICTED,
    "the client points the golden alias at its image (PATCH)": RESTRICTED,
    "the client deletes the golden alias": RESTRICTED,
    "the client renames the golden alias": RESTRICTED,
    "the client deletes its own alias": RESTRICTED,
    "the client changes a property of the golden image": RESTRICTED,
    "the client deletes the golden image": RESTRICTED,
    "the client deletes its own image": RESTRICTED,
}


def runs(contract: Contract) -> list[tuple[list[str], str]]:
    """Each set of projects a profile's certificate is restricted to, with each of its projects.

    Args:
        contract: The platform contract.

    Returns:
        ``(projects, project)`` pairs, sorted.
    """
    sets = sorted(
        {
            tuple(sorted(profile.data["incus_projects"]))
            for profile in contract.profiles.values()
            if profile.data.get("incus_projects")
        }
    )
    return [(list(projects), project) for projects in sets for project in projects]


def _steps(steps: Mapping[Any, Mapping[str, Any]]) -> list[str]:
    problems = []
    for name in ALLOWED:
        found = steps.get(name)
        if found is None:
            problems.append(f"{name}: not tried")
        elif found.get("rc") != 0:
            problems.append(f"{name} failed: {found.get('output')}")
    for name, refusal in REFUSED.items():
        found = steps.get(name)
        if found is None:
            problems.append(f"{name}: not tried")
        elif found.get("rc") == 0:
            problems.append(f"{name} succeeded")
        elif refusal not in str(found.get("output")):
            problems.append(f"{name} failed without {refusal!r}: {found.get('output')}")
    for name, found in steps.items():
        if str(name).startswith("cleanup: ") and found.get("rc") != 0:
            problems.append(f"{str(name)[9:]} failed: {found.get('output')}")
    return problems


def _store(report: Mapping[str, Any]) -> list[str]:
    problems = []
    aliases = report.get("aliases") or []
    for alias in (f"{ALIAS}/1.0.1", f"{ALIAS}/2.0.0"):
        if alias not in aliases:
            problems.append(f"the client's alias {alias} is not in the shared store")
    golden = report.get("golden") or {}
    if golden.get("aliases") != [f"{ALIAS}/1.0.0", f"{ALIAS}/2.0.0"]:
        problems.append(f"the golden image's aliases changed: {golden.get('aliases')}")
    properties = golden.get("properties") or {}
    if properties.get("description") != "pinka-test-images golden":
        problems.append(
            f"the golden image's description changed: {properties.get('description')!r}"
        )
    # A new image request naming the golden image rewrites its record (Incus
    # 6.0.7: cmd/incusd/images.go, imgPostRemoteInfo): the role must not rely
    # on properties a client cannot change.
    key, value = REWRITTEN
    if properties.get(key) != value:
        problems.append(
            f"the client's new image request did not set {key}: {properties.get(key)!r}"
        )
    if report.get("left"):
        problems.append(f"left behind: {', '.join(report['left'])}")
    return problems


def compare(report: Mapping[str, Any], project: str) -> list[str]:
    """Every difference between a report and what the image store gives.

    Args:
        report: The guest helper's ``image-store`` answer.
        project: The project the client worked in.

    Returns:
        The problems, each naming the project.
    """
    steps = {s.get("name"): s for s in report.get("steps") or []}
    return [f"{project}: {problem}" for problem in [*_steps(steps), *_store(report)]]
