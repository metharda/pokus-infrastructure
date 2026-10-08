"""The CI fast lane: provisioning plays in check mode in a systemd container.

``make check-mode CI_IMAGE=<key>`` builds a container from the base image
``containers.ci_images.<key>`` of ``versions.lock.yml`` (pinned by digest)
with ``tests/distros/containers/``, starts it with systemd as PID 1, runs
preflight inside it for the host profile, and runs a play with ``--check``
through Ansible's docker connection, with the variables bootstrap would pass
and the example vault. Nothing runs on the host itself; the container is
removed afterwards.

Two modes (approved deviation I2):

- required: ``ansible/check-mode.yml``, the provider-agnostic roles of
  BUILD_PROMPT §11 (base, users_groups, ssh, layout), with nothing skipped;
  the blocking CI job.
- extended (``make check-mode-extended``): the whole ``ansible/site.yml``.
  Roles whose component source or pins are not verified for the container's
  release are skipped by tag, as the play would refuse them on a real host;
  the summary lists every skipped component as not tested, never as passed.
  A skip disappears by itself once the component's sources are verified.
  The non-blocking CI job.
"""

from __future__ import annotations

import importlib.util
import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml

from pokus_test_distros.incus import Result, Runner

INFRA = Path(__file__).resolve().parents[3]
ANSIBLE = INFRA / "ansible"
CONTAINERS = INFRA / "tests" / "distros" / "containers"
# The required job's play (provider-agnostic roles only) and the extended job's play.
REQUIRED_PLAYBOOK = ANSIBLE / "check-mode.yml"
EXTENDED_PLAYBOOK = ANSIBLE / "site.yml"
# The roles of the required job, in play order (BUILD_PROMPT §11).
REQUIRED_ROLES = ("base", "users_groups", "ssh", "layout")
MACHINE = "example-machine"
# Read on the target by preflight_gate and doctor: copied into the container at
# the same absolute paths, because the play resolves them on the controller.
GUEST_TREES = ("preflight", "tools/pokus-lab")
# Not below /tmp: that is a tmpfs in the container, and docker cp cannot write into tmpfs mounts.
GUEST_DIR = "/opt/pokus-ci"
GUEST_PROFILE = f"{GUEST_DIR}/host-profile.json"
# Where bootstrap installs the pinned CPython before Ansible runs (pokus_python).
GUEST_POKUS_PYTHON = "/opt/pokus/python/bin/python3"
SYSTEMD_STATES = ("running", "degraded")
# Roles that install a component of ansible/component-sources.yml.
SOURCED_ROLES = {
    "docker": "docker",
    "tailscale": "tailscale",
    "incus": "incus",
    "nvidia_container_toolkit": "nvidia_container_toolkit",
}
FINGERPRINT_RE = re.compile(r"^([0-9A-F]{40}|[0-9A-F]{64})$")
# The example vault, with a local backup repository (an SFTP one needs the server's host keys).
VAULT_EXAMPLE = ANSIBLE / "group_vars" / "all" / "vault.yml.example"
CI_RESTIC_REPOSITORY = "/var/backups/pokus-restic"


class CheckModeError(RuntimeError):
    """The container could not be prepared."""


def image_ref(lock: dict[str, Any], key: str) -> str:
    """The pinned reference of a CI base image.

    Args:
        lock: ``versions.lock.yml``.
        key: Key under ``containers.ci_images``.

    Returns:
        ``<image>@<digest>``.

    Raises:
        CheckModeError: For an unknown key.
    """
    images = (lock.get("containers") or {}).get("ci_images") or {}
    if key not in images:
        raise CheckModeError(
            f"no containers.ci_images.{key} in versions.lock.yml ({', '.join(images)})"
        )
    return f"{images[key]['image']}@{images[key]['digest']}"


def parse_profile(profile_json: str) -> dict[str, Any]:
    """Preflight's host profile from inside the container.

    Args:
        profile_json: Preflight's JSON report.

    Returns:
        The profile.

    Raises:
        CheckModeError: If preflight wrote no profile.
    """
    try:
        profile = json.loads(profile_json)
    except ValueError as exc:
        raise CheckModeError("preflight wrote no JSON profile in the container") from exc
    if not isinstance(profile, dict) or profile.get("schema_version") != 1:
        raise CheckModeError("preflight wrote no host profile in the container")
    return profile


def extra_vars(profile: dict[str, Any], interpreter: str) -> dict[str, Any]:
    """The variables bootstrap would pass, with the container's host profile.

    Preflight classifies the container as a container, without a provider and
    unsupported; the fast lane treats it like a bare-metal host whose operator
    forced the installation (``--force-unsupported``), as bootstrap records it.

    Args:
        profile: Preflight's host profile from inside the container.
        interpreter: Python of the container.

    Returns:
        Extra variables for ansible-playbook.
    """
    host_profile = json.loads(json.dumps(profile))
    host_profile["provider"] = host_profile.get("provider") or "baremetal"
    host_profile.setdefault("overrides", {})["force_unsupported"] = True
    return {
        "host_profile": host_profile,
        "pokus_bootstrap": {
            "machine": MACHINE,
            "repo_commit": "",
            "infra_tree": "",
            "infra_dirty": False,
            "ref": "HEAD",
            "check": True,
            "refresh_profile": False,
            "allow_reboot": False,
            "adopt_incus": False,
            "allow_community_repos": False,
            "offline": True,
            "interactive": False,
            "profile_path": GUEST_PROFILE,
            "signal_file": f"{GUEST_DIR}/bootstrap-signal.json",
            "check_mode_fast_lane": True,
        },
        "machine": MACHINE,
        "ansible_python_interpreter": interpreter,
    }


def vault_vars() -> dict[str, Any]:
    """The secrets of the run: the example vault with a local backup repository.

    Returns:
        Vault variables.
    """
    vault: dict[str, Any] = yaml.safe_load(VAULT_EXAMPLE.read_text(encoding="utf-8"))
    vault["vault_restic_repository"] = CI_RESTIC_REPOSITORY
    return vault


def _filters() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "pokus_filters", ANSIBLE / "filter_plugins" / "pokus.py"
    )
    if spec is None or spec.loader is None:
        raise CheckModeError("cannot load ansible/filter_plugins/pokus.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data


def unverified_roles(profile: dict[str, Any]) -> dict[str, str]:
    """Roles the play refuses on this release because their source is not verified.

    Mirrors the refusals of the play: the component's source entry, its vendor
    repository and repository key, and its package pins (unverified markers).

    Args:
        profile: Preflight's host profile from inside the container.

    Returns:
        ``{role: reason}``.

    Raises:
        CheckModeError: If the profile names no known release.
    """
    filters = _filters()
    sources = _load(ANSIBLE / "component-sources.yml")
    versions = _load(ANSIBLE / "group_vars" / "all" / "versions.yml")["pokus_versions"]
    family = str(profile.get("family"))
    facts = ANSIBLE / "roles" / "platform_facts" / "vars" / f"{family}.yml"
    if not facts.is_file():
        raise CheckModeError(f"the host profile names no supported family ({family})")
    codenames = _load(facts)["platform_facts_family"]["codenames"]
    try:
        identity = filters.release_identity(profile, codenames)
    except filters.PokusFilterError as exc:
        raise CheckModeError(f"no release identity for the container: {exc}") from exc
    refused: dict[str, str] = {}
    for role, component in SOURCED_ROLES.items():
        source = filters.component_source(
            sources["components"], component, identity["source_group"]
        )
        reasons = [str(source["verify"])] if "verify" in source else []
        if source.get("source") == "vendor_repo":
            repo = sources["repositories"][source["repo"]]
            key = versions["repository_keys"]["keys"][repo["key"]]
            if "verify" in repo:
                reasons.append(f"repository {source['repo']}: {repo['verify']}")
            if not FINGERPRINT_RE.match(str(key["fingerprint"])):
                reasons.append(f"repository key {repo['key']} is not verified")
        try:
            filters.lock_packages(versions["packages"], component, identity["release_key"])
        except filters.PokusFilterError as exc:
            reasons.append(str(exc))
        if reasons:
            refused[role] = "; ".join(reasons)
    return refused


class CheckMode:
    """One fast-lane run."""

    def __init__(
        self,
        key: str,
        run: Runner,
        out_dir: Path,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Prepare a run.

        Args:
            key: Key under ``containers.ci_images``.
            run: Command runner.
            out_dir: Directory for the extra variables and logs.
            sleep: Sleep function (tests replace it).
        """
        self.key = key
        self.run = run
        self.out_dir = out_dir
        self.sleep = sleep
        self.name = "pokus-ci-" + key.replace("_", "-")

    def _docker(self, *args: str, timeout: float = 600) -> Result:
        return self.run(["docker", *args], timeout=timeout)

    def _check(self, result: Result, what: str) -> Result:
        if not result.ok:
            raise CheckModeError(f"{what}: {(result.stderr or result.stdout).strip()[-600:]}")
        return result

    def _exec(self, *argv: str, timeout: float = 600) -> Result:
        return self._docker("exec", self.name, *argv, timeout=timeout)

    def start(self, image: str) -> None:
        """Build and start the systemd container.

        Args:
            image: Pinned base image.
        """
        self._check(
            self._docker(
                "build",
                "--tag",
                self.name,
                "--build-arg",
                f"BASE={image}",
                "--file",
                str(CONTAINERS / "Containerfile"),
                str(CONTAINERS),
                timeout=1800,
            ),
            "docker build",
        )
        self._docker("rm", "--force", self.name)
        self._check(
            self._docker(
                "run",
                "--detach",
                "--name",
                self.name,
                "--privileged",
                "--cgroupns=host",
                "--volume",
                "/sys/fs/cgroup:/sys/fs/cgroup:rw",
                "--tmpfs",
                "/run",
                "--tmpfs",
                "/run/lock",
                "--tmpfs",
                "/tmp",  # noqa: S108 - a tmpfs inside the container
                self.name,
            ),
            "docker run",
        )
        for _ in range(60):
            state = self._exec("systemctl", "is-system-running").stdout.strip()
            if state in SYSTEMD_STATES:
                return
            self.sleep(2)
        raise CheckModeError(
            f"systemd did not finish booting in the container ({state or 'no answer'})"
        )

    def prepare(self) -> tuple[dict[str, Any], str]:
        """Copy what the play reads on the target into the container and run preflight there.

        Returns:
            Preflight's host profile and the container's Python.

        Raises:
            CheckModeError: If preflight printed no report.
        """
        parents = sorted({str((INFRA / tree).parent) for tree in GUEST_TREES})
        self._check(self._exec("mkdir", "-p", GUEST_DIR, *parents), "creating the directories")
        for tree in GUEST_TREES:
            self._check(
                self._docker("cp", str(INFRA / tree), f"{self.name}:{INFRA / tree}"), "docker cp"
            )
        interpreter = self._check(
            self._exec("sh", "-c", "command -v python3.11 || command -v python3"), "finding Python"
        ).stdout.strip()
        # The container's Python stands in for the CPython that bootstrap installs.
        self._check(
            self._exec("mkdir", "-p", str(Path(GUEST_POKUS_PYTHON).parent)), "creating /opt/pokus"
        )
        self._check(
            self._exec("ln", "-sf", interpreter, GUEST_POKUS_PYTHON), "linking pokus_python"
        )
        host_vars = _load(ANSIBLE / "inventory" / "host_vars" / f"{MACHINE}.yml")
        # The same options as preflight_gate's run, which compares its profile with this one.
        report = self._exec(
            "env",
            f"PYTHONPATH={INFRA / 'preflight'}",
            interpreter,
            "-m",
            "pokus_preflight",
            "--no-write",
            "--quiet",
            "--srv-path",
            str(host_vars["pokus_root"]),
            "--nvidia-driver",
            str(host_vars.get("install_nvidia_driver", "auto")),
            "--offline",
        )
        if not report.stdout.strip():
            raise CheckModeError(
                f"preflight printed no report (exit status {report.returncode}): "
                f"{report.stderr.strip()[-600:]}"
            )
        profile = parse_profile(report.stdout)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        local = self.out_dir / f"{self.key}-host-profile.json"
        local.write_text(json.dumps(profile, indent=2), encoding="utf-8")
        self._check(self._docker("cp", str(local), f"{self.name}:{GUEST_PROFILE}"), "docker cp")
        return profile, interpreter

    def play(self, variables: dict[str, Any], skip: list[str], playbook: Path) -> Result:
        """Run a play in check mode through the docker connection.

        The container stands in for ``localhost``, the host the play provisions.

        Args:
            variables: Extra variables.
            skip: Roles to skip (their tags).
            playbook: The play.

        Returns:
            The ansible-playbook result.
        """
        self.out_dir.mkdir(parents=True, exist_ok=True)
        extra = self.out_dir / f"{self.key}-vars.json"
        extra.write_text(json.dumps(variables, indent=2), encoding="utf-8")
        vault = self.out_dir / f"{self.key}-vault.json"
        vault.write_text(json.dumps(vault_vars(), indent=2), encoding="utf-8")
        inventory = self.out_dir / f"{self.key}-inventory.ini"
        inventory.write_text(
            f"localhost ansible_connection=community.docker.docker ansible_host={self.name}\n",
            encoding="utf-8",
        )
        argv = [
            "ansible-playbook",
            "--check",
            "--inventory",
            str(inventory),
            "--extra-vars",
            f"@{extra}",
            "--extra-vars",
            f"@{vault}",
        ]
        if skip:
            argv += ["--skip-tags", ",".join(skip)]
        return self.run([*argv, str(playbook)], timeout=3600)

    def stop(self) -> None:
        """Remove the container."""
        self._docker("rm", "--force", self.name)


def extended_summary(key: str, skipped: dict[str, str], returncode: int) -> str:
    """The summary of an extended run, in Markdown.

    Skipped components are listed as not tested; a skipped component is never
    reported as passed, whatever the play's result.

    Args:
        key: Key under ``containers.ci_images``.
        skipped: ``{role: reason}`` of the roles skipped on this release.
        returncode: ansible-playbook's exit status.

    Returns:
        The summary.
    """
    played = "passed" if returncode == 0 else f"failed (ansible-playbook exit status {returncode})"
    lines = [
        f"## Extended check mode (non-blocking): {key}",
        "",
        "The whole provisioning play (`ansible/site.yml`) in check mode. Components whose "
        "sources are not verified on this release are skipped and **not tested**; they are "
        "never counted as passing.",
        "",
        f"- Roles that ran: **{played}**",
        f"- Components skipped, not tested: **{len(skipped)}**",
        "",
    ]
    if skipped:
        lines += ["| Component | Result | Why |", "| --- | --- | --- |"]
        for role, reason in sorted(skipped.items()):
            why = reason.replace("|", "\\|")
            lines.append(f"| `{role}` | SKIPPED, not tested | {why} |")
        lines.append("")
    return "\n".join(lines)


def main(
    key: str,
    run: Runner,
    out_dir: Path,
    *,
    extended: bool = False,
    summary: Path | None = None,
    keep: bool = False,
    write: Callable[[str], Any] = print,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """One fast-lane run.

    Args:
        key: Key under ``containers.ci_images``.
        run: Command runner.
        out_dir: Directory for the variables, the inventory and the host profile.
        extended: Run the whole play with unverified components skipped, instead
            of the provider-agnostic roles with nothing skipped.
        summary: File the extended run appends its Markdown summary to (in CI,
            ``$GITHUB_STEP_SUMMARY``); the summary is always also written to
            ``out_dir``.
        keep: Keep the container afterwards.
        write: Output function.
        sleep: Sleep function (tests replace it).

    Returns:
        ansible-playbook's exit status, or 2 if the container could not be prepared.
    """
    lock = yaml.safe_load((INFRA / "versions.lock.yml").read_text(encoding="utf-8"))
    check = CheckMode(key, run, out_dir, sleep)
    try:
        image = image_ref(lock, key)
        mode = "extended" if extended else "required"
        write(f"== {key} ({mode}): {image}")
        check.start(image)
        profile, interpreter = check.prepare()
        skipped = unverified_roles(profile) if extended else {}
        for role, reason in sorted(skipped.items()):
            write(f"-- skipping {role}, not tested, refused on this release: {reason}")
        playbook = EXTENDED_PLAYBOOK if extended else REQUIRED_PLAYBOOK
        result = check.play(extra_vars(profile, interpreter), sorted(skipped), playbook)
        write(result.stdout)
        if result.stderr:
            write(result.stderr)
        if extended:
            text = extended_summary(key, skipped, result.returncode)
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / f"{key}-extended-summary.md").write_text(text, encoding="utf-8")
            if summary is not None:
                with summary.open("a", encoding="utf-8") as handle:
                    handle.write(text + "\n")
            write(text)
        return result.returncode
    except CheckModeError as exc:
        write(f"check-mode: {exc}")
        return 2
    finally:
        if not keep:
            check.stop()
