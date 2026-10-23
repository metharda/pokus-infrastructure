"""The uninstall lane's variant: Docker and Tailscale before pinka (BUILD_PROMPT §19.9).

On a target with ``preexisting`` (``tests/distros/targets.yml``), the
harness sets up what a machine's owner would have had before pinka: Docker
Engine from the distribution (``docker.io`` with its Compose and buildx
plugins) running a workload container,
and Tailscale from Tailscale's repository at the lock's version, its key
checked against the lock's fingerprint, logged in with the test tailnet's
auth key under a host name and a tag of its own (and the lab's tag, which
doctor expects of an adopted node). Install and uninstall must leave both
as they were: the node's identity (the snapshot's ``tailscale`` section),
the components' package versions, and the workload's connectivity (its
published port from the VM and from the harness host, and its way out).
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

INFRA = Path(__file__).resolve().parents[3]
LOCK = INFRA / "versions.lock.yml"
SOURCES = INFRA / "ansible" / "component-sources.yml"
GROUP_VARS = INFRA / "ansible" / "group_vars" / "all" / "main.yml"

# The owner's packages of each component: their versions must not change.
# Docker comes with its Compose and buildx plugins, which preflight requires
# of a Docker that existed before pinka (docker_plugins_missing); the three
# names are in Ubuntu 24.04's archive (noble-updates universe, read on
# 2026-10-22).
DOCKER_PACKAGES = ("docker.io", "docker-compose-v2", "docker-buildx")
PACKAGES = {
    "docker": (*DOCKER_PACKAGES, "containerd", "runc"),
    "tailscale": ("tailscale",),
}
WORKLOAD = "example-workload"
WORKLOAD_PORT = 8090
# The node's own tag; the test tailnet's policy lets tag:pinka-qa own it.
OWNER_TAG = "tag:pinka-qa-owner"
# Where the workload's way out leads: Tailscale's package server, which the
# VM reaches anyway.
EGRESS = ("pkgs.tailscale.com", 443)
# Tailscale's own names for its key and repository (its install instructions).
KEYRING = "/usr/share/keyrings/tailscale-archive-keyring.asc"
SOURCES_LIST = "/etc/apt/sources.list.d/tailscale.list"
CODENAME = '. /etc/os-release && echo "$VERSION_CODENAME"'


class PinError(ValueError):
    """The lock or the source matrix lacks what the variant needs."""


@dataclass(frozen=True)
class TailscalePins:
    """Tailscale's repository, key and version for one release.

    Attributes:
        key_url: Where the key is downloaded from.
        fingerprint: The key's primary fingerprint.
        repository: The apt source without its options (URI, suite, components).
        version: The package version.
    """

    key_url: str
    fingerprint: str
    repository: str
    version: str


def _load(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def tailscale_pins(
    os_id: str, version_id: str, codename: str, *, lock: Path = LOCK, sources: Path = SOURCES
) -> TailscalePins:
    """Tailscale's pins for a release, from the lock and the component source matrix.

    Args:
        os_id: os-release ``ID`` (``ubuntu``, ``debian``).
        version_id: os-release ``VERSION_ID``.
        codename: os-release ``VERSION_CODENAME``.
        lock: ``versions.lock.yml``.
        sources: ``ansible/component-sources.yml``.

    Returns:
        The pins.

    Raises:
        PinError: When the key URL, a verified repository or the version is missing.
    """
    locked = _load(lock)
    key = locked["repository_keys"]["keys"]["tailscale"]
    urls = [str(u) for u in key["urls"] if str(u).endswith(f"/{os_id}/{codename}.asc")]
    if not urls:
        raise PinError(f"the lock has no Tailscale key URL for {os_id} {codename}")
    repo = _load(sources)["repositories"]["tailscale-deb"]
    if repo.get("verify"):
        raise PinError(f"Tailscale's apt repository is not verified: {repo['verify']}")
    pinned = (locked["packages"]["tailscale"] or {}).get(f"{os_id}-{version_id}")
    if not isinstance(pinned, dict) or not pinned.get("tailscale"):
        raise PinError(f"the lock pins no Tailscale version for {os_id}-{version_id}")
    uri = str(repo["uri"]).format(distro=os_id)
    suite = str(repo["suite"]).format(codename=codename)
    components = " ".join(str(c) for c in repo["components"])
    return TailscalePins(
        key_url=urls[0],
        fingerprint=str(key["fingerprint"]),
        repository=f"{uri} {suite} {components}",
        version=str(pinned["tailscale"]),
    )


def lab_tag(group_vars: Path = GROUP_VARS) -> str:
    """The tag pinka gives a staging lab's node (``pinka_tailscale_tags.staging``)."""
    return str(_load(group_vars)["pinka_tailscale_tags"]["staging"])


def workload_image(lock: Path = LOCK) -> str:
    """The workload's image: the lock's ``refplat_job`` image, by digest."""
    image = _load(lock)["containers"]["images"]["refplat_job"]
    return f"{image['image']}@{image['digest']}"


def docker_script(install: str) -> str:
    """Bash: Docker Engine and its plugins from the distribution, started, and the workload running.

    Args:
        install: The family's install command with a ``{pkg}`` placeholder.

    Returns:
        The script.
    """
    image = shlex.quote(workload_image())
    return (
        f"{install.format(pkg=' '.join(DOCKER_PACKAGES))} && systemctl enable --now docker"
        f" && docker run -d --name {WORKLOAD} --restart unless-stopped"
        f" -p {WORKLOAD_PORT}:{WORKLOAD_PORT} {image} python3 -m http.server {WORKLOAD_PORT}"
    )


def tailscale_script(
    install: str, pins: TailscalePins, hostname: str, tags: list[str], secrets_env: str
) -> str:
    """Bash: Tailscale from its repository at the pinned version, logged in.

    Args:
        install: The family's install command with a ``{pkg}`` placeholder.
        pins: The release's pins.
        hostname: The node's own host name.
        tags: Its tags.
        secrets_env: The file with ``PINKA_SECRET_TAILSCALE_AUTHKEY``.

    Returns:
        The script; it fails when the key's fingerprint differs from the lock's.
    """
    new = shlex.quote(f"{KEYRING}.new")
    source = shlex.quote(f"deb [signed-by={KEYRING}] {pins.repository}")
    return (
        f"set -e; curl -fsSL --retry 3 -o {new} {shlex.quote(pins.key_url)};"
        f" found=$(gpg --batch --show-keys --with-colons {new}"
        " | awk -F: '$1 == \"fpr\" {print $10; exit}');"
        f' if [ "$found" != {pins.fingerprint} ]; then'
        f' echo "Tailscale key $found, expected {pins.fingerprint}" >&2; rm -f {new}; exit 1; fi;'
        f" mv {new} {KEYRING}; echo {source} > {SOURCES_LIST};"
        f" {install.format(pkg=f'tailscale={pins.version}')};"
        " systemctl enable --now tailscaled;"
        f" set -a; . {shlex.quote(secrets_env)}; set +a;"
        ' tailscale up --auth-key="$PINKA_SECRET_TAILSCALE_AUTHKEY"'
        f" --hostname={shlex.quote(hostname)} --advertise-tags={','.join(tags)}"
        " --accept-dns=false --timeout=120s"
    )


def workload_answers() -> str:
    """Bash: the workload answers on its published port in the VM (for up to 30 s after a boot)."""
    return (
        "for _ in $(seq 30); do curl -sS -o /dev/null --max-time 5"
        f" http://127.0.0.1:{WORKLOAD_PORT}/ && exit 0; sleep 1; done; exit 1"
    )


def workload_egress() -> str:
    """Bash: the workload opens a connection out of the VM."""
    host, port = EGRESS
    code = f"import socket; socket.create_connection(({host!r}, {port}), timeout=15).close()"
    return f"docker exec {WORKLOAD} python3 -c {shlex.quote(code)}"


def versions_script(packages: list[str]) -> str:
    """Bash: ``name version`` of the given installed packages."""
    names = " ".join(shlex.quote(p) for p in packages)
    return f"dpkg-query -W -f '${{Package}} ${{Version}}\\n' {names}"
