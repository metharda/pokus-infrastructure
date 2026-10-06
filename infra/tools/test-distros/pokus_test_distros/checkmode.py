"""The CI fast lane: provider-agnostic roles in check mode in a systemd container.

``make check-mode CI_IMAGE=<key>`` builds a container from the base image
``containers.ci_images.<key>`` of ``versions.lock.yml`` (pinned by digest)
with ``tests/distros/containers/``, starts it with systemd as PID 1, runs
preflight inside it for the host profile, and runs ``ansible/check-mode.yml``
with ``--check`` through Ansible's docker connection. Nothing runs on the
host itself; the container is removed afterwards.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from pokus_test_distros.incus import Result, Runner

INFRA = Path(__file__).resolve().parents[3]
CONTAINERS = INFRA / "tests" / "distros" / "containers"
PLAYBOOK = INFRA / "ansible" / "check-mode.yml"
GUEST_PREFLIGHT = "/tmp/pokus-preflight"  # noqa: S108 - inside the disposable container
SYSTEMD_STATES = ("running", "degraded")


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


def extra_vars(profile_json: str, interpreter: str) -> dict[str, Any]:
    """The variables bootstrap would pass, with the container's host profile.

    Preflight classifies the container as a container, without a provider;
    the fast lane treats it like a bare-metal host.

    Args:
        profile_json: Preflight's JSON report from inside the container.
        interpreter: Python of the container.

    Returns:
        Extra variables for ansible-playbook.

    Raises:
        CheckModeError: If preflight wrote no profile.
    """
    try:
        profile = json.loads(profile_json)
    except ValueError as exc:
        raise CheckModeError("preflight wrote no JSON profile in the container") from exc
    if not isinstance(profile, dict) or profile.get("schema_version") != 1:
        raise CheckModeError("preflight wrote no host profile in the container")
    profile["provider"] = profile.get("provider") or "baremetal"
    return {
        "host_profile": profile,
        "pokus_bootstrap": {"interactive": False, "check_mode_fast_lane": True},
        "machine": "example-machine",
        "ansible_python_interpreter": interpreter,
    }


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

    def profile(self) -> dict[str, Any]:
        """Run preflight in the container.

        Returns:
            The extra variables with its host profile.
        """
        self._check(
            self._docker("cp", str(INFRA / "preflight"), f"{self.name}:{GUEST_PREFLIGHT}"),
            "docker cp",
        )
        interpreter = self._check(
            self._exec("sh", "-c", "command -v python3.11 || command -v python3"), "finding Python"
        ).stdout.strip()
        report = self._exec(
            "env",
            f"PYTHONPATH={GUEST_PREFLIGHT}",
            interpreter,
            "-m",
            "pokus_preflight",
            "--no-write",
            "--offline",
            "--quiet",
        )
        return extra_vars(report.stdout, interpreter)

    def play(self, variables: dict[str, Any]) -> Result:
        """Run the check-mode play through the docker connection.

        Args:
            variables: Extra variables.

        Returns:
            The ansible-playbook result.
        """
        self.out_dir.mkdir(parents=True, exist_ok=True)
        extra = self.out_dir / f"{self.key}-vars.json"
        extra.write_text(json.dumps(variables, indent=2), encoding="utf-8")
        return self.run(
            [
                "ansible-playbook",
                "--check",
                "--inventory",
                f"{self.name},",
                "--connection",
                "community.docker.docker",
                "--extra-vars",
                f"@{extra}",
                str(PLAYBOOK),
            ],
            timeout=3600,
        )

    def stop(self) -> None:
        """Remove the container."""
        self._docker("rm", "--force", self.name)


def main(
    key: str,
    run: Runner,
    out_dir: Path,
    *,
    keep: bool = False,
    write: Callable[[str], Any] = print,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """One fast-lane run.

    Args:
        key: Key under ``containers.ci_images``.
        run: Command runner.
        out_dir: Directory for the extra variables.
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
        write(f"== {key}: {image}")
        check.start(image)
        result = check.play(check.profile())
        write(result.stdout)
        if result.stderr:
            write(result.stderr)
        return result.returncode
    except CheckModeError as exc:
        write(f"check-mode: {exc}")
        return 2
    finally:
        if not keep:
            check.stop()
