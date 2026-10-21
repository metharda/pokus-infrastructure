"""One distribution test: a disposable VM from launch to deletion.

The lab is bootstrapped twice (the second run must change nothing) and
checked with ``pinka doctor --smoke``, from a release package of the tested
commit (its pinned collections bundled, owner decision of 3d.7 and 3d.8);
on the ufw and firewalld lanes, what was closed with consent stays closed
after the owner's firewall reloads, while SSH over the tailnet still works
(approved deviation AG1);
then the reference platform is deployed in it (BUILD_PROMPT §7.18, §19.9): its isolation probes, the
training flow with the CPU-only job, a run interrupted by a reboot and
resumed, a second deployment that changes nothing, the replacement by a
renamed copy, and a backup and restore.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import secrets
import socket
import tempfile
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from pinka_platform import contract as contract_mod
from pinka_test_distros import isolation, refplat
from pinka_test_distros.incus import Incus, IncusError, Result
from pinka_test_distros.targets import Target

STAGES = (
    "launch",
    "prepare",
    "bootstrap",
    "idempotent",
    "smoke",
    "access",
    "platform",
    "isolation",
    "training",
    "resume",
    "redeploy",
    "replace",
    "restore",
)
KEEP = ("never", "failed", "always")
GUEST_REPO = "/root/pinka"
# Bootstrap runs from the unpacked release package; the clone serves the
# reference platform's sources and the guest helper.
GUEST_INFRA = "/root/pokus-infrastructure"
GUEST_PACKAGE = f"{GUEST_INFRA}.tar.gz"
GUEST_VAULT_PASS = "/root/ci-vault-pass"  # noqa: S105 - a path, not a password
GUEST_SECRETS = "/root/ci-secrets.env"
# Arguments longer than this are shortened in the stage logs (the probe's source).
SHOWN_ARGUMENT = 200
# Problems named in a stage's error; the stage's files hold all of them.
SHOWN_PROBLEMS = 5
STATE_FILE = "/etc/pinka/bootstrap-state.json"
PROFILE_FILE = "/etc/pinka/host-profile.json"
BOOTSTRAP_TIMEOUT = 4 * 3600.0
RECAP_RE = re.compile(r"^\S+\s*:\s*ok=\d+\s+changed=(\d+)\s+unreachable=\d+\s+failed=\d+", re.M)
# The harness's own package commands, before bootstrap (variants only).
INSTALL = {
    "debian": "DEBIAN_FRONTEND=noninteractive apt-get update -q"
    " && DEBIAN_FRONTEND=noninteractive apt-get install -y -q {pkg}",
    "redhat": "dnf install -y {pkg}",
    "suse": "zypper --non-interactive install {pkg}",
    "archlinux": "pacman -Syu --noconfirm {pkg}",
}
ENABLE = {
    "firewalld": "systemctl enable --now firewalld",
    "ufw": "ufw --force enable && systemctl enable ufw",
}
DISABLE_MANAGERS = (
    "for unit in firewalld ufw; do"
    ' if systemctl is-active --quiet "$unit"; then systemctl disable --now "$unit"; fi; done'
)
# The ufw and firewalld lanes (approved deviation AG1): two services of the
# machine's owner listen before the install, the owner's firewall admits
# both, and the answers close the first with consent.
ACCESS_FIREWALLS = ("ufw", "firewalld")
CLOSED_PORT, KEPT_PORT = 8080, 8081
OWNER_SERVICES = (
    f"mkdir -p /srv/example-web && for port in {CLOSED_PORT} {KEPT_PORT}; do"
    " printf '[Unit]\\nDescription=A service of the owner (test-distros)\\n[Service]\\n"
    "ExecStart=/usr/bin/python3 -m http.server %s --bind 0.0.0.0 --directory /srv/example-web\\n"
    '[Install]\\nWantedBy=multi-user.target\\n\' "$port"'
    " > /etc/systemd/system/example-web-$port.service; done"
    " && systemctl daemon-reload"
    f" && systemctl enable --now example-web-{CLOSED_PORT}.service example-web-{KEPT_PORT}.service"
)
OWNER_RULES = {
    "ufw": f"ufw allow {CLOSED_PORT}/tcp && ufw allow {KEPT_PORT}/tcp",
    "firewalld": f"firewall-cmd --permanent --add-port={CLOSED_PORT}/tcp"
    f" --add-port={KEPT_PORT}/tcp && firewall-cmd --reload",
}
RELOAD = {"ufw": "ufw reload", "firewalld": "firewall-cmd --reload"}
# The VM's address on its network, as the harness host reaches it.
LAN_ADDRESS = "ip -4 -o route get 192.0.2.1 | sed -n 's/.* src \\([0-9.]*\\).*/\\1/p'"
# A second Tailscale node inside the VM, in userspace networking: SSH from it
# reaches the VM's own node over tailscale0, never over loopback.
TAILNET_CLIENT = "/run/pinka-test-tailnet-client"
TAILNET_CLIENT_UNIT = "pinka-test-tailnet-client"
# Its SOCKS5 proxy on the VM's loopback: HTTP over the tailnet to the
# owner's services, which must answer on tcp/8081 and not on tcp/8080.
TAILNET_SOCKS = "localhost:1055"
CONNECT_TIMEOUT = 5.0


def tailnet_http(port: int) -> str:
    """An HTTP request over the tailnet to one of the owner's services on the VM.

    Args:
        port: The service's port.

    Returns:
        A shell command that succeeds only when the service answers.
    """
    return (
        f"curl -sS -o /dev/null --max-time 15 --socks5-hostname {TAILNET_SOCKS}"
        f' "http://$(tailscale ip -4):{port}/"'
    )


def tcp_reachable(address: str, port: int) -> bool:
    """Whether a TCP connection from the harness host to ``address:port`` opens.

    Args:
        address: IPv4 address.
        port: Port.

    Returns:
        True when the connection opens within the timeout.
    """
    try:
        with socket.create_connection((address, port), timeout=CONNECT_TIMEOUT):
            return True
    except OSError:
        return False


class StageError(RuntimeError):
    """A stage failed; the message says why."""


@dataclass
class Settings:
    """What every test of one harness run shares.

    Attributes:
        repo_tar: Tar archive of a clone of the repository at ``commit``.
        package: The release package of ``commit`` (``make release-package``).
        answers: The CI answers template (``tests/answers/ci.yml``).
        commit: Tested commit.
        out_dir: Directory for this run's logs.
        secrets_env: Local 0600 file with ``PINKA_SECRET_*`` lines for the VM.
        profile: Incus profile with network and root disk.
        keep: Keep VMs ``never``, on ``failed`` tests, or ``always``.
        keep_running: Leave kept VMs running; by default they are stopped, so
            that only one test VM runs at a time.
        now: Clock (UTC).
        token: Short random suffix of VM names.
        reachable: Whether the harness host opens a TCP connection to an
            address and port of a VM.
    """

    repo_tar: Path
    package: Path
    answers: Path
    commit: str
    out_dir: Path
    secrets_env: Path
    profile: str = "default"
    keep: str = "failed"
    keep_running: bool = False
    now: Callable[[], dt.datetime] = field(default=lambda: dt.datetime.now(dt.UTC))
    token: Callable[[], str] = field(default=lambda: secrets.token_hex(3))
    reachable: Callable[[str, int], bool] = field(default=tcp_reachable)


def changed_tasks(log: str) -> int | None:
    """Sum of ``changed=`` over the PLAY RECAP lines of a bootstrap log.

    Args:
        log: The log.

    Returns:
        The count, or None if the log has no recap.
    """
    counts = [int(c) for c in RECAP_RE.findall(log)] if "PLAY RECAP" in log else []
    return sum(counts) if counts else None


def render_answers(template: Path, machine: str, changes: Mapping[str, Any] | None = None) -> str:
    """The CI answers with this VM's machine name and a stage's changes.

    Args:
        template: ``tests/answers/ci.yml``.
        machine: VM name.
        changes: Top-level answers that replace the template's (the platform
            sections, the mode, the snapshot to restore).

    Returns:
        YAML text.
    """
    data: dict[str, Any] = yaml.safe_load(template.read_text(encoding="utf-8"))
    data.setdefault("machine", {})["name"] = machine
    data.update(changes or {})
    header = f"# Generated by make test-distros from {template.name} for {machine}.\n"
    return header + yaml.safe_dump(data, sort_keys=False)


class Test:
    """One target's test."""

    def __init__(self, target: Target, incus: Incus, settings: Settings) -> None:
        """Prepare a test.

        Args:
            target: The target.
            incus: Incus client.
            settings: Shared settings.
        """
        self.target = target
        self.incus = incus
        self.settings = settings
        self.name = f"{target.vm_name_stem}-{settings.token()}"
        self.dir = settings.out_dir / target.id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.fingerprint = ""
        self.changed: int | None = None
        self.created = False
        self.kept = False
        self.contract = contract_mod.load()
        # The answers' platform sections of the platform deployed last.
        self.platform_answers: dict[str, Any] = {}

    def _sh(self, stage: str, script: str, *, timeout: float = 1800) -> Result:
        result = self.incus.exec(self.name, ["bash", "-c", script], timeout=timeout)
        with (self.dir / f"{stage}.out").open("a", encoding="utf-8") as log:
            log.write(f"$ {script}\n[exit {result.returncode}]\n{result.stdout}{result.stderr}\n")
        return result

    def _run(self, stage: str, argv: list[str], *, timeout: float = 1800) -> Result:
        """Run a command without a shell; long arguments are shortened in the log."""
        result = self.incus.exec(self.name, argv, timeout=timeout)
        shown = " ".join(a if len(a) <= SHOWN_ARGUMENT else a[:60] + "..." for a in argv)
        with (self.dir / f"{stage}.out").open("a", encoding="utf-8") as log:
            log.write(f"$ {shown}\n[exit {result.returncode}]\n{result.stdout}{result.stderr}\n")
        return result

    def _answer(
        self, stage: str, argv: list[str], what: str, *, timeout: float = 1800
    ) -> dict[str, Any]:
        """A command's JSON answer, which must say ``ok``."""
        result = self._run(stage, argv, timeout=timeout)
        try:
            answer = refplat.last_json(result.stdout)
        except ValueError:
            answer = {}
        if not result.ok or answer.get("ok") is not True:
            reason = answer.get("error") or (result.stderr or result.stdout).strip()[-300:]
            raise StageError(f"{what} failed (exit {result.returncode}): {reason}")
        return answer

    def _require(self, stage: str, script: str, what: str, *, timeout: float = 1800) -> Result:
        result = self._sh(stage, script, timeout=timeout)
        if not result.ok:
            tail = (result.stderr or result.stdout).strip().splitlines()[-1:] or [""]
            raise StageError(f"{what} failed (exit {result.returncode}): {tail[0]}")
        return result

    # --- stages ---------------------------------------------------------------------------

    def launch(self) -> None:
        """Launch the VM and wait for it."""
        t = self.target
        self.incus.launch(
            self.name,
            t.image_ref,
            profile=self.settings.profile,
            cpus=t.cpus,
            memory=t.memory,
            disk=t.disk,
        )
        self.created = True
        self.fingerprint = self.incus.base_image(self.name)
        self.incus.wait_ready(self.name)

    def prepare(self) -> None:
        """Establish the variant and copy the repository, answers and secrets in."""
        t = self.target
        if t.firewall == "nftables":
            self._require("prepare", DISABLE_MANAGERS, "disabling firewalld and ufw")
        else:
            install = INSTALL[t.family].format(pkg=t.firewall)
            self._require("prepare", install, f"installing {t.firewall}")
            self._require("prepare", ENABLE[t.firewall], f"enabling {t.firewall}")
            self._require("prepare", OWNER_SERVICES, "starting the owner's services")
            self._require(
                "prepare", OWNER_RULES[t.firewall], "admitting them in the owner's firewall"
            )
        if t.selinux == "enforcing":
            mode = self._sh("prepare", "getenforce").stdout.strip()
            if mode != "Enforcing":
                self._require(
                    "prepare",
                    'setenforce 1 && test "$(getenforce)" = Enforcing',
                    f"switching SELinux from {mode or 'unknown'} to enforcing",
                )
        self.incus.push(self.name, self.settings.repo_tar, "/root/pinka.tar")
        self._require(
            "prepare",
            f"tar -xf /root/pinka.tar -C /root && test -d {GUEST_REPO}/.git",
            "unpacking the repository",
        )
        self.incus.push(self.name, self.settings.package, GUEST_PACKAGE)
        self._require(
            "prepare",
            f"mkdir -p {GUEST_INFRA} && tar -xzf {GUEST_PACKAGE} -C {GUEST_INFRA}"
            f" --strip-components=1 && test -f {GUEST_INFRA}/release/RELEASE.json",
            "unpacking the release package",
        )
        with tempfile.TemporaryDirectory() as tmp:
            answers = Path(tmp, "answers.yml")
            answers.write_text(self._render(), encoding="utf-8")
            self.incus.push(self.name, answers, self._answers_path(), mode="0644")
            vault = Path(tmp, "vault-pass")
            vault.write_text(secrets.token_urlsafe(32) + "\n", encoding="utf-8")
            self.incus.push(self.name, vault, GUEST_VAULT_PASS)
        self.incus.push(self.name, self.settings.secrets_env, GUEST_SECRETS)
        self._require(
            "prepare",
            "ssh-keygen -q -t ed25519 -N '' -C researcher1 -f /root/ci-researcher"
            f" && install -D -m 0644 /root/ci-researcher.pub {GUEST_INFRA}/users/researcher1.pub",
            "creating the test researcher's key",
        )

    def _answers_path(self) -> str:
        return f"{GUEST_INFRA}/installer-answers/{self.name}.yml"

    @property
    def closes_listeners(self) -> bool:
        """Whether this lane closes a pre-existing listener with consent (ufw, firewalld)."""
        return self.target.firewall in ACCESS_FIREWALLS

    def _render(self, changes: Mapping[str, Any] | None = None) -> str:
        """The CI answers for this VM, with a stage's changes and the lane's consent."""
        changes = dict(changes or {})
        if self.closes_listeners:
            template = yaml.safe_load(self.settings.answers.read_text(encoding="utf-8"))
            changes["remote_access"] = {
                **(template.get("remote_access") or {}),
                "close_listeners": [f"tcp/{CLOSED_PORT}"],
            }
        return render_answers(self.settings.answers, self.name, changes)

    def _bootstrap(self, stage: str, options: str) -> int:
        script = (
            f"set -a; . {GUEST_SECRETS}; set +a; cd {GUEST_INFRA} && "
            f"./bootstrap/bootstrap.sh --non-interactive --vault-password-file {GUEST_VAULT_PASS} "
            + options
        )
        result = self._sh(stage, script, timeout=BOOTSTRAP_TIMEOUT)
        state_file = self.dir / f"{stage}-state.json"
        if not self.incus.pull(self.name, STATE_FILE, state_file):
            raise StageError(f"bootstrap wrote no state file (exit {result.returncode})")
        state = json.loads(state_file.read_text(encoding="utf-8"))
        log_file = str(state.get("log_file") or "")
        if log_file:
            self.incus.pull(self.name, log_file, self.dir / f"{stage}.log")
        # 1: finished, doctor warned (no backup has run yet on a fresh lab).
        if result.returncode not in (0, 1):
            raise StageError(
                f"bootstrap ended with {state.get('result')} (exit {result.returncode})"
                f" in stage {state.get('stage')}"
            )
        return result.returncode

    def bootstrap(self) -> None:
        """First bootstrap from the answers; the host profile must show the variant."""
        self._bootstrap(
            "bootstrap",
            f"--answers {self._answers_path()} --accept-degraded",
        )
        state = json.loads((self.dir / "bootstrap-state.json").read_text(encoding="utf-8"))
        if state.get("infra_source") != "package":
            raise StageError(f"bootstrap ran from {state.get('infra_source')}, not the package")
        profile = self.dir / "host-profile.json"
        if not self.incus.pull(self.name, PROFILE_FILE, profile):
            raise StageError("no host profile after bootstrap")
        backend = (json.loads(profile.read_text(encoding="utf-8")).get("firewall") or {}).get(
            "backend"
        )
        if backend != self.target.firewall:
            raise StageError(f"firewall backend {backend}, expected {self.target.firewall}")

    def _changed(self, stage: str) -> int:
        """Bootstrap again with the machine's host_vars; the count of changed tasks."""
        self._bootstrap(stage, f"--machine {self.name}")
        log = self.dir / f"{stage}.log"
        text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
        changed = changed_tasks(text)
        if changed is None:
            raise StageError(f"the {stage} run's log has no PLAY RECAP")
        return changed

    def idempotent(self) -> None:
        """A second bootstrap must change nothing."""
        self.changed = self._changed("idempotent")
        if self.changed:
            raise StageError(f"the second run changed {self.changed} task(s)")

    def smoke(self) -> None:
        """Back up once, then ``pinka doctor --smoke`` must exit 0."""
        self._require("smoke", "pinka backup now", "the first backup", timeout=3600)
        self._require("smoke", "pinka doctor --smoke", "pinka doctor --smoke", timeout=3600)

    def access(self) -> None:
        """What was closed with consent stays closed after the owner's firewall reloads (AG1).

        On the ufw and firewalld lanes, before and after ``ufw reload`` or
        ``firewall-cmd --reload``: the access guard drops new connections to
        the closed listener; the harness host reaches the kept one and not
        the closed one, and so does a second node over the tailnet; and SSH
        over the tailnet still works.
        """
        if not self.closes_listeners:
            return
        address = self._require("access", LAN_ADDRESS, "finding the VM's address").stdout.strip()
        if not address:
            raise StageError("the VM has no address on its network")
        self._access_holds(address, "after the install")
        self._require("access", RELOAD[self.target.firewall], f"reloading {self.target.firewall}")
        self._access_holds(address, f"after {RELOAD[self.target.firewall]}")

    def _access_holds(self, address: str, when: str) -> None:
        guard = self._sh("access", "nft list table inet pinka_access_guard")
        if not guard.ok or f"tcp dport {CLOSED_PORT} drop" not in guard.stdout:
            raise StageError(f"{when}: the access guard does not drop tcp/{CLOSED_PORT}")
        if not self.settings.reachable(address, KEPT_PORT):
            raise StageError(f"{when}: the kept listener tcp/{KEPT_PORT} is not reachable")
        if self.settings.reachable(address, CLOSED_PORT):
            raise StageError(f"{when}: the closed listener tcp/{CLOSED_PORT} is reachable")
        self._over_the_tailnet(when)

    def _over_the_tailnet(self, when: str) -> None:
        """From a second tailnet node inside the VM: SSH works, tcp/8080 is closed, tcp/8081 not."""
        sock = f"{TAILNET_CLIENT}/tailscaled.sock"
        start = (
            f"set -a; . {GUEST_SECRETS}; set +a; mkdir -p {TAILNET_CLIENT}"
            f' && systemd-run --unit={TAILNET_CLIENT_UNIT} --collect "$(command -v tailscaled)"'
            f" --tun=userspace-networking --socket={sock} --statedir={TAILNET_CLIENT} --port=0"
            f" --socks5-server={TAILNET_SOCKS}"
            f" && for i in $(seq 30); do test -S {sock} && break; sleep 1; done"
            f' && tailscale --socket={sock} up --auth-key="$PINKA_SECRET_TAILSCALE_AUTHKEY"'
            f" --hostname={self.name}-client --accept-dns=false --timeout=120s"
        )
        login = (
            "ssh -i /root/ci-researcher -o BatchMode=yes -o StrictHostKeyChecking=no"
            " -o UserKnownHostsFile=/dev/null -o ConnectTimeout=30"
            f' -o ProxyCommand="tailscale --socket={sock} nc %h %p"'
            ' researcher1@"$(tailscale ip -4)" true'
        )
        try:
            self._require("access", start, f"{when}: starting the tailnet client")
            self._require("access", login, f"{when}: SSH over the tailnet")
            if not self._sh("access", tailnet_http(KEPT_PORT)).ok:
                raise StageError(
                    f"{when}: the kept listener tcp/{KEPT_PORT} is not reachable over the tailnet"
                )
            if self._sh("access", tailnet_http(CLOSED_PORT)).ok:
                raise StageError(
                    f"{when}: the closed listener tcp/{CLOSED_PORT} is reachable over the tailnet"
                )
        finally:
            self._sh(
                "access",
                f"tailscale --socket={sock} logout; systemctl stop {TAILNET_CLIENT_UNIT}; true",
            )

    # --- the reference platform -------------------------------------------------------------

    def _apply(self, stage: str, changes: Mapping[str, Any], options: str = "") -> None:
        """Bootstrap from the CI answers with a stage's changes (the installer's replay).

        The answers are kept as ``<stage>-answers.yml`` in the results.
        """
        text = self._render(changes)
        (self.dir / f"{stage}-answers.yml").write_text(text, encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            answers = Path(tmp, "answers.yml")
            answers.write_text(text, encoding="utf-8")
            self.incus.push(self.name, answers, self._answers_path(), mode="0644")
        self._bootstrap(stage, " ".join([f"--answers {self._answers_path()}", options]).strip())

    def _deployed(self, stage: str, name: str) -> None:
        """The active platform is ``name`` and its controller answers with its database."""
        active = self.dir / f"{stage}-active.json"
        if not self.incus.pull(self.name, refplat.ACTIVE, active):
            raise StageError("no active platform after the deployment")
        found = json.loads(active.read_text(encoding="utf-8")).get("name")
        if found != name:
            raise StageError(f"the active platform is {found}, expected {name}")
        status = self._answer(stage, [refplat.CLI, "status"], "refplat status")
        if status.get("database") != "ok":
            raise StageError(
                f"the controller does not reach its database: {status.get('database')}"
            )

    def _agent_report(self, stage: str) -> None:
        """The agent's own probe (untrusted-agent) must reach nothing."""
        report = self._answer(
            stage, refplat.guest("wait-report", "--timeout", "600"), "the agent's probe report"
        )
        problems = isolation.agent_problems(report)
        if problems:
            raise StageError("; ".join(problems))

    def _standin(self, stage: str) -> None:
        """The inference API's stand-in on the vLLM port (the VMs have no GPU)."""
        lab_file = self.dir / "lab.json"
        if not self.incus.pull(self.name, refplat.LAB, lab_file):
            raise StageError("no /etc/pinka/lab.json after the bootstrap")
        port = refplat.vllm_port(json.loads(lab_file.read_text(encoding="utf-8")))
        self._require(stage, refplat.standin_script(port), "the inference stand-in")

    def platform(self) -> None:
        """Deploy the reference platform from the VM's clone; it must be healthy.

        The platform requires ``inference``, so the stand-in serves the
        inference API first; the broker must then get its model list.
        """
        self._standin("platform")
        repo = f"file://{refplat.GUEST_REPO}"
        commit = self.settings.commit
        self.platform_answers = refplat.platform_answers(
            refplat.source(repo, commit, refplat.RELEASE_PATH),
            {"cases": refplat.source(repo, commit, refplat.CASES_PATH)},
        )
        self._apply("platform", {"mode": "modify", **self.platform_answers})
        self._deployed("platform", refplat.manifest()["name"])
        self._require("platform", "pinka doctor --smoke", "pinka doctor --smoke", timeout=3600)
        models = self._answer("platform", [refplat.CLI, "infer"], "refplat infer").get("models")
        if models != [refplat.STANDIN_MODEL]:
            raise StageError(f"the broker did not get the inference API's models: {models}")

    def isolation(self) -> None:
        """The agent's probe reaches nothing; each loopback-capable profile reaches what it may."""
        self._agent_report("isolation")
        endpoints_file = self.dir / "endpoints.toml"
        if not self.incus.pull(self.name, refplat.ENDPOINTS, endpoints_file):
            raise StageError("no endpoints.toml after the deployment")
        endpoints = tomllib.loads(endpoints_file.read_text(encoding="utf-8"))
        ports = {k: int(v) for k, v in endpoints["ports"].items()}
        self._standin("isolation")
        problems: list[str] = []
        reports = []
        for probe in isolation.plan(refplat.manifest(), self.contract, ports):
            create, remove = isolation.account_script(probe)
            if probe.temporary:
                self._require("isolation", create, f"the account of the {probe.profile} probe")
            try:
                unit = f"pinka-test-probe-{probe.profile}"
                argv = isolation.command(probe, str(endpoints["platform"]), unit)
                result = self._run("isolation", argv, timeout=600)
            finally:
                if probe.temporary:
                    self._sh("isolation", remove)
            try:
                report = refplat.last_json(result.stdout)
            except ValueError:
                problems.append(f"{probe.profile}: no report (exit {result.returncode})")
                continue
            reports.append(report)
            problems += isolation.compare(probe, report)
        text = json.dumps(reports, indent=2, sort_keys=True)
        (self.dir / "isolation.json").write_text(text + "\n", encoding="utf-8")
        if problems:
            shown = "; ".join(problems[:SHOWN_PROBLEMS])
            extra = len(problems) - SHOWN_PROBLEMS
            more = f" and {extra} more" if extra > 0 else ""
            raise StageError(shown + more)

    def training(self) -> None:
        """Dataset, the CPU-only job, its adapter and lineage."""
        self._answer("training", [refplat.CLI, "dataset"], "refplat dataset")
        train = [refplat.CLI, "train", "--job", "tunecpu", "--seed", "1", "--wait", "1200"]
        run = self._answer("training", train, "the training run", timeout=1800)
        lineage = self._answer("training", [refplat.CLI, "lineage", str(run["run"])], "the lineage")
        adapter = (lineage.get("lineage") or {}).get("adapter") or {}
        if not run.get("adapter") or adapter.get("sha256") != run.get("adapter"):
            raise StageError(f"run {run['run']} registered no matching adapter")

    def resume(self) -> None:
        """A run interrupted by a reboot is suspended, resumed at boot and succeeds."""
        argv = [refplat.CLI, "train", "--job", "tunecpu", "--seed", "2"]
        argv += ["--steps", str(refplat.RESUME_STEPS)]
        argv += ["--step-seconds", str(refplat.RESUME_STEP_SECONDS)]
        run = str(self._answer("resume", argv, "the training run")["run"])
        wait = ["wait-checkpoint", run, "--step", str(refplat.RESUME_AFTER_STEP)]
        self._answer("resume", refplat.guest(*wait), "the run's checkpoint")
        self.incus.restart(self.name)
        self.incus.wait_ready(self.name)
        ended = refplat.guest("wait-run", run, "--timeout", "1800")
        self._answer("resume", ended, f"run {run} after the reboot", timeout=2400)
        log = self.dir / "transitions.jsonl"
        if not self.incus.pull(self.name, refplat.TRANSITIONS, log):
            raise StageError("no transition log")
        outcomes = refplat.run_outcomes(log.read_text(encoding="utf-8"), run)
        if "suspended" not in outcomes or "resumed" not in outcomes:
            raise StageError(f"run {run} was not suspended and resumed: {outcomes}")

    def redeploy(self) -> None:
        """A second deployment of the same release must change nothing."""
        changed = self._changed("redeploy")
        if changed:
            raise StageError(f"the second deployment changed {changed} task(s)")

    def replace(self) -> None:
        """A renamed copy replaces the platform; its agent's probe reaches nothing."""
        script = refplat.renamed_copy_script()
        commit = (
            self._require("replace", script, "the renamed copy").stdout.strip().splitlines()[-1]
        )
        repo = f"file://{refplat.RENAMED_REPO}"
        self.platform_answers = refplat.platform_answers(
            refplat.source(repo, commit, "release"),
            {"cases": refplat.source(repo, commit, "cases")},
        )
        old = refplat.manifest()["name"]
        changes = {"mode": "modify", **self.platform_answers}
        self._apply("replace", changes, f"--replace-platform {old}")
        self._deployed("replace", refplat.RENAMED_NAME)
        self._agent_report("replace")

    def restore(self) -> None:
        """Back up, lose a controller record, restore the latest snapshot."""
        self._answer("restore", [refplat.CLI, "dataset"], "refplat dataset")
        record = f"/srv/pinka/platforms/{refplat.RENAMED_NAME}/state/ctl/records/datasets.json"
        self._require("restore", "pinka backup now", "the backup", timeout=3600)
        self._require("restore", f"rm -f {record}", "removing a controller record")
        changes = {"mode": "restore", "restore": {"snapshot": "latest"}, **self.platform_answers}
        self._apply("restore", changes)
        self._require("restore", f"test -s {record}", "the restored controller record")
        self._deployed("restore", refplat.RENAMED_NAME)
        self._require("restore", "pinka doctor --smoke", "pinka doctor --smoke", timeout=3600)

    # --- the whole test -----------------------------------------------------------------------

    def collect(self) -> None:
        """Copy the journal's warnings out of the VM (best effort)."""
        self._sh("collect", "journalctl -b -p warning --no-pager | tail -n 2000")

    def cleanup(self, passed: bool) -> bool:
        """Delete the VM unless it is kept.

        Args:
            passed: Whether every stage passed.

        Returns:
            Whether the VM was kept.
        """
        keep = self.settings.keep == "always" or (self.settings.keep == "failed" and not passed)
        if keep:
            # One test VM at a time: the next target starts only after this one stopped.
            if not self.settings.keep_running:
                self.incus.stop(self.name)
            return True
        # Leave the test tailnet at once (the auth key is ephemeral anyway).
        self.incus.exec(self.name, ["tailscale", "logout"], timeout=60)
        self.incus.delete(self.name)
        return False

    def run(self) -> dict[str, Any]:
        """Run every stage; after a failure the rest are skipped.

        Returns:
            The result record of tests/distros/results.yml.
        """
        started = self.settings.now()
        stages = dict.fromkeys(STAGES, "skipped")
        error = None
        for stage in STAGES:
            try:
                getattr(self, stage)()
            except (StageError, IncusError, OSError, ValueError) as exc:
                stages[stage] = "failed"
                error = f"{stage}: {exc}"
                break
            stages[stage] = "passed"
        if self.created:
            self.collect()
            self.kept = self.cleanup(error is None)
        minutes = int((self.settings.now() - started).total_seconds() // 60)
        return {
            "target": self.target.id,
            "image": self.target.image_ref,
            "image_fingerprint": self.fingerprint or None,
            "tested_on": started.strftime("%Y-%m-%d"),
            "commit": self.settings.commit,
            "stages": stages,
            "changed_on_second_run": self.changed,
            "error": error,
            "duration_minutes": minutes,
        }
