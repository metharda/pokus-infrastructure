"""Tests for the doctor checks, one host situation at a time."""

from __future__ import annotations

import datetime as dt
import json
import unittest
from collections.abc import Sequence
from typing import Any

from labfakes import DIGEST, LAB, NOW, PROFILE, FakeSystem, context, info, lab_data

from pinka_lab import checks, fingerprint
from pinka_lab.checks import CHECKS
from pinka_lab.doctor import Check, Outcome

PREFLIGHT = (
    "/usr/local/lib/pinka/preflight/pinka-preflight --no-write --quiet --srv-path /srv/pinka"
)
PROFILE_PATH = "/etc/pinka/host-profile.json"
BOOT_ID = "00000000-0000-0000-0000-000000000000"
NOW_TS = NOW.timestamp()

RENDERED = "/etc/pinka/platform/rendered.json"
RECORD = "/var/lib/pinka/install-record.json"
CDI_INFO = "docker info --format {{json .CDISpecDirs}}"


def install_record(*, legacy: bool = False, joined: bool = False, **tailscale: Any) -> str:
    """An install record noting a Tailscale node that was logged in before pinka."""
    before = {"origin": "pre-existing", "hostname": "workstation", "tags": [], **tailscale}
    return json.dumps(
        {
            "schema_version": 1,
            "created_at": "2026-10-01T00:00:00Z",
            "legacy": legacy,
            "preexisting": {"components": {"tailscale": before}},
            "added": {"components": {"tailscale": True} if joined else {}},
        }
    )


def run(check: Any, system: FakeSystem, **changes: Any) -> Outcome:
    outcome: Outcome = check(context(system, **changes))
    return outcome


class Base(unittest.TestCase):
    def assertOutcome(  # noqa: N802 - unittest naming
        self, outcome: Outcome, result: str, detail: str = ""
    ) -> None:
        self.assertEqual(outcome.result, result, outcome.detail)
        self.assertIn(detail, outcome.detail)


class RegistryTest(Base):
    def test_ids_are_unique_and_complete(self) -> None:
        ids = [c.id for c in CHECKS]
        self.assertEqual(len(ids), len(set(ids)))
        for check in CHECKS:
            self.assertIsInstance(check, Check)
            self.assertRegex(check.id, r"^[a-z]+\.[a-z_]+$")
            self.assertIn(check.severity, ("error", "warning"))
            self.assertTrue(check.description.endswith("."), check.id)
            self.assertTrue(check.remediation, check.id)


class PreflightTest(Base):
    def report(self, outcome: str, rc: int = 0, **extra: Any) -> FakeSystem:
        report = dict(PROFILE, outcome=outcome, **extra)
        return FakeSystem({PREFLIGHT: (rc, report)}, files={PROFILE_PATH: json.dumps(PROFILE)})

    def test_outcome(self) -> None:
        self.assertOutcome(run(checks.check_preflight_outcome, self.report("supported")), "pass")
        self.assertOutcome(run(checks.check_preflight_outcome, self.report("degraded", 10)), "pass")
        self.assertOutcome(
            run(checks.check_preflight_outcome, self.report("needs_action", 30)),
            "fail",
            "needs_action",
        )
        forced = self.report("unsupported", 20, overrides={"force_unsupported": True})
        self.assertOutcome(run(checks.check_preflight_outcome, forced), "pass", "forced")
        self.assertOutcome(
            run(checks.check_preflight_outcome, self.report("unsupported", 20)), "fail"
        )

    def test_no_report(self) -> None:
        system = FakeSystem({PREFLIGHT: (40, "Traceback")})
        self.assertOutcome(
            run(checks.check_preflight_outcome, system), "fail", "did not produce a report"
        )
        self.assertOutcome(run(checks.check_preflight_drift, system), "skip")

    def test_runs_preflight_once(self) -> None:
        system = self.report("supported")
        ctx = context(system)
        checks.check_preflight_outcome(ctx)
        checks.check_preflight_drift(ctx)
        self.assertEqual(sum(1 for c in system.calls if c[0].endswith("pinka-preflight")), 1)

    def test_drift(self) -> None:
        self.assertOutcome(run(checks.check_preflight_drift, self.report("supported")), "pass")
        changed = dict(PROFILE, features={"gpu_inference": False, "vm_targets": False})
        system = FakeSystem({PREFLIGHT: (0, changed)}, files={PROFILE_PATH: json.dumps(PROFILE)})
        self.assertOutcome(
            run(checks.check_preflight_drift, system), "fail", "features.gpu_inference"
        )


class ServicesTest(Base):
    def test_units(self) -> None:
        system = FakeSystem(
            {
                "systemctl is-active pinka-health-gate.service": (0, "active\n"),
                "systemctl is-active pinka-core.service": (3, "failed\n"),
            }
        )
        self.assertOutcome(run(checks.check_units, system), "fail", "pinka-core.service (failed)")
        system.commands["systemctl is-active pinka-core.service"] = (0, "active\n")
        self.assertOutcome(run(checks.check_units, system), "pass", "2 unit(s)")
        # The platform's services and sockets come from its rendering.
        system.files[RENDERED] = json.dumps(
            {"service_units": ["demo-ctl.service"], "socket_units": ["demo-ctl-api.socket"]}
        )
        system.commands["systemctl is-active demo-ctl.service"] = (0, "active\n")
        outcome = run(checks.check_units, system)
        self.assertOutcome(outcome, "fail", "demo-ctl-api.socket")
        system.commands["systemctl is-active demo-ctl-api.socket"] = (0, "active\n")
        self.assertOutcome(run(checks.check_units, system), "pass", "4 unit(s)")

    def inspect(self, state: dict[str, Any], image: str) -> FakeSystem:
        return FakeSystem(
            {"docker inspect pinka-postgres": (0, [{"State": state, "Config": {"Image": image}}])}
        )

    def test_containers(self) -> None:
        pinned = "docker.io/library/postgres@" + DIGEST
        healthy = {"Running": True, "Health": {"Status": "healthy"}}
        self.assertOutcome(run(checks.check_containers, self.inspect(healthy, pinned)), "pass")
        self.assertOutcome(
            run(checks.check_containers, self.inspect({"Running": True}, pinned)), "pass"
        )
        self.assertOutcome(
            run(
                checks.check_containers,
                self.inspect({"Running": True, "Health": {"Status": "unhealthy"}}, pinned),
            ),
            "fail",
            "unhealthy",
        )
        self.assertOutcome(
            run(
                checks.check_containers,
                self.inspect({"Running": False, "Status": "exited"}, pinned),
            ),
            "fail",
            "exited",
        )
        self.assertOutcome(
            run(
                checks.check_containers,
                self.inspect(healthy, "docker.io/library/postgres:18"),
            ),
            "fail",
            "runs docker.io/library/postgres:18",
        )
        self.assertOutcome(run(checks.check_containers, FakeSystem()), "fail", "missing")


SS = """\
tcp   LISTEN 0      4096       127.0.0.1:8181       0.0.0.0:*    users:(("opa",pid=10,fd=3))
tcp   LISTEN 0      128          0.0.0.0:22         0.0.0.0:*    users:(("sshd",pid=11,fd=3))
tcp   LISTEN 0      128             [::]:22            [::]:*    users:(("sshd",pid=11,fd=4))
udp   UNCONN 0      0      127.0.0.53%lo:53         0.0.0.0:*    users:(("systemd-resolve",pid=12,fd=13))
tcp   LISTEN 0      4096           [::1]:8443          [::]:*    users:(("incusd",pid=13,fd=9))
tcp   LISTEN 0      4096      198.18.0.1:4317       0.0.0.0:*    users:(("otelcol-contrib",pid=14,fd=7))
udp   UNCONN 0      0       10.0.0.1%r-0:53         0.0.0.0:*    users:(("dnsmasq",pid=15,fd=6))
"""


class NetworkTest(Base):
    def test_parse_listeners(self) -> None:
        parsed = checks.listeners(SS)
        self.assertEqual(parsed[0], ("tcp", "127.0.0.1", 8181, "opa"))
        self.assertEqual(parsed[2], ("tcp", "::", 22, "sshd"))
        self.assertEqual(parsed[3], ("udp", "127.0.0.53", 53, "systemd-resolve"))
        self.assertTrue(checks.is_loopback("::1"))
        self.assertFalse(checks.is_loopback("::"))

    def test_listeners(self) -> None:
        system = FakeSystem({"ss -H -lntup": (0, SS)})
        self.assertOutcome(run(checks.check_listeners, system), "pass")
        system.commands["ss -H -lntup"] = (
            0,
            SS + 'tcp LISTEN 0 4096 0.0.0.0:18700 0.0.0.0:* users:(("grafana",pid=16,fd=8))\n',
        )
        self.assertOutcome(
            run(checks.check_listeners, system), "fail", "tcp 0.0.0.0:18700 (grafana)"
        )
        self.assertOutcome(run(checks.check_listeners, FakeSystem()), "fail", "ss failed")

    def test_split_inference_listeners(self) -> None:
        endpoint = (
            'tcp LISTEN 0 100 100.64.0.7:18710 0.0.0.0:* users:(("inference-contr",pid=20,fd=6))\n'
        )
        system = FakeSystem(
            {"ss -H -lntup": (0, SS + endpoint), "tailscale ip": (0, "100.64.0.7\nfd7a::7\n")}
        )
        ports = [18704, 18710]
        self.assertOutcome(
            run(checks.check_listeners, system, listeners__tailnet_ports=ports), "pass"
        )
        for wrong in ([18704], []):
            self.assertOutcome(
                run(checks.check_listeners, system, listeners__tailnet_ports=wrong),
                "fail",
                "tcp 100.64.0.7:18710 (inference-contr)",
            )
        # Another address than the tailnet's is never allowed by port.
        system.commands["tailscale ip"] = (0, "100.64.0.8\n")
        self.assertOutcome(
            run(checks.check_listeners, system, listeners__tailnet_ports=ports), "fail", "18710"
        )

    def firewall(self, **commands: Any) -> FakeSystem:
        table = {
            "nft list table inet pinka_filter": (0, "table inet pinka_filter {}"),
            "iptables -n -L DOCKER-USER": (0, ""),
            "iptables -C DOCKER-USER -i r-+ -j ACCEPT": (0, ""),
            "iptables -C DOCKER-USER -o r-+ -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT": (
                0,
                "",
            ),
        }
        table.update({k.replace("_", " "): v for k, v in commands.items()})
        return FakeSystem(table)

    def test_firewall(self) -> None:
        self.assertOutcome(run(checks.check_firewall, self.firewall()), "pass", "nftables")
        system = self.firewall()
        system.commands["nft list table inet pinka_filter"] = (1, "")
        self.assertOutcome(run(checks.check_firewall, system), "fail", "pinka_filter is missing")
        system = self.firewall()
        del system.commands[
            "iptables -C DOCKER-USER -o r-+ -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT"
        ]
        self.assertOutcome(
            run(checks.check_firewall, system), "fail", "iptables DOCKER-USER lacks -o r-+"
        )
        system = self.firewall()
        system.commands["ip6tables -n -L DOCKER-USER"] = (0, "")
        self.assertOutcome(run(checks.check_firewall, system), "fail", "ip6tables DOCKER-USER")

    def test_other_firewall_backends(self) -> None:
        zones = {
            "firewall-cmd --info-zone pinka-tailnet": (0, "pinka-tailnet"),
            "firewall-cmd --info-zone pinka-run": (0, "pinka-run"),
        }
        self.assertOutcome(
            run(checks.check_firewall, FakeSystem(zones), firewall__backend="firewalld"), "pass"
        )
        for missing in zones:
            system = FakeSystem({k: v for k, v in zones.items() if k != missing})
            self.assertOutcome(
                run(checks.check_firewall, system, firewall__backend="firewalld"),
                "fail",
                f"zone {missing.split()[-1]} is missing",
            )
        # A zone named only "pinka" is not what the firewall role creates.
        system = FakeSystem({"firewall-cmd --info-zone pinka": (0, "pinka")})
        self.assertOutcome(
            run(checks.check_firewall, system, firewall__backend="firewalld"), "fail"
        )
        system = FakeSystem({"ufw status": (0, "Status: inactive\n")})
        self.assertOutcome(
            run(checks.check_firewall, system, firewall__backend="ufw"), "fail", "not active"
        )

    def tailscale(self, status: dict[str, Any], serve: dict[str, Any]) -> FakeSystem:
        return FakeSystem(
            {"tailscale status --json": (0, status), "tailscale serve status --json": (0, serve)}
        )

    def test_tailscale(self) -> None:
        good = {
            "BackendState": "Running",
            "Self": {"HostName": "pinka-lab", "Tags": ["tag:pinka-lab"]},
        }
        self.assertOutcome(run(checks.check_tailscale, self.tailscale(good, {})), "pass")
        self.assertOutcome(
            run(checks.check_tailscale, self.tailscale(good, {"AllowFunnel": {"x:443": True}})),
            "fail",
            "Funnel",
        )
        bad = {"BackendState": "NeedsLogin", "Self": {"HostName": "other", "Tags": []}}
        outcome = run(checks.check_tailscale, self.tailscale(bad, {}))
        self.assertOutcome(outcome, "fail", "NeedsLogin")
        self.assertIn("'other'", outcome.detail)
        self.assertIn("tag:pinka-lab missing", outcome.detail)
        self.assertOutcome(
            run(checks.check_tailscale, FakeSystem(), tailscale__enabled=False), "skip"
        )
        system = self.tailscale(good, {})
        system.commands["tailscale debug prefs"] = (0, {"RunSSH": True})
        self.assertOutcome(run(checks.check_tailscale, system), "fail", "Tailscale SSH is on")

    def test_an_adopted_node_is_compared_with_the_install_record(self) -> None:
        # BUILD_PROMPT §7.4: a node logged in before pinka keeps its own name and tags.
        mine = {
            "BackendState": "Running",
            "Self": {"HostName": "workstation", "Tags": [], "KeyExpiry": "2027-04-01T00:00:00Z"},
        }
        system = self.tailscale(mine, {})
        system.files[RECORD] = install_record()
        self.assertOutcome(run(checks.check_tailscale, system), "pass", "as recorded")
        identity = run(checks.check_tailscale_identity, system)
        self.assertOutcome(identity, "fail", "no tag:pinka-lab")
        self.assertIn("expires at 2027-04-01T00:00:00Z", identity.detail)
        check = next(c for c in CHECKS if c.id == "network.tailscale_identity")
        self.assertEqual(check.severity, "warning")
        renamed = dict(mine, Self={"HostName": "other", "Tags": ["tag:x"]})
        system = self.tailscale(renamed, {})
        system.files[RECORD] = install_record()
        outcome = run(checks.check_tailscale, system)
        self.assertOutcome(outcome, "fail", "tags ['tag:x'], recorded []")
        self.assertIn("expected 'workstation'", outcome.detail)
        tagged = {"BackendState": "Running", "Self": {"HostName": "w", "Tags": ["tag:pinka-lab"]}}
        system = self.tailscale(tagged, {})
        system.files[RECORD] = install_record(hostname="w", tags=["tag:pinka-lab"])
        self.assertOutcome(run(checks.check_tailscale_identity, system), "pass")

    def test_a_node_pinka_joined_uses_the_configured_name(self) -> None:
        good = {
            "BackendState": "Running",
            "Self": {"HostName": "pinka-lab", "Tags": ["tag:pinka-lab"]},
        }
        for record in (install_record(joined=True), install_record(legacy=True), None):
            system = self.tailscale(good, {})
            if record:
                system.files[RECORD] = record
            self.assertOutcome(run(checks.check_tailscale, system), "pass", "pinka-lab with")
            self.assertOutcome(run(checks.check_tailscale_identity, system), "skip")


class InstallRecordTest(Base):
    def test_install_record(self) -> None:
        # BUILD_PROMPT §7.21: present, readable, and only root can change it.
        good = FakeSystem(files={RECORD: install_record()})
        self.assertOutcome(run(checks.check_install_record, good), "pass", "first install")
        legacy = FakeSystem(files={RECORD: install_record(legacy=True)})
        self.assertOutcome(run(checks.check_install_record, legacy), "pass", "legacy")
        self.assertOutcome(run(checks.check_install_record, FakeSystem()), "fail", "missing")
        broken = FakeSystem(files={RECORD: "[]"})
        self.assertOutcome(run(checks.check_install_record, broken), "fail", "not a JSON object")
        partial = FakeSystem(files={RECORD: '{"legacy": false}'})
        self.assertOutcome(
            run(checks.check_install_record, partial), "fail", "lacks created_at, preexisting"
        )
        writable = FakeSystem(
            files={RECORD: install_record()},
            infos={RECORD: info("root", "pinka", 0o664)},
        )
        self.assertOutcome(
            run(checks.check_install_record, writable), "fail", "root 0664; only root"
        )


class GpuTest(Base):
    def test_gpu(self) -> None:
        system = FakeSystem({"docker run *": (0, "GPU 0: Example GPU (UUID: GPU-0)\n")})
        self.assertOutcome(run(checks.check_gpu, system), "pass", "1 GPU")
        argv = system.calls[0]
        self.assertEqual(argv[:5], ["docker", "run", "--rm", "--pull", "never"])
        self.assertIn("nvidia.com/gpu=all", argv)
        self.assertOutcome(
            run(checks.check_gpu, FakeSystem({"docker run *": (0, "")})), "fail", "no GPU"
        )
        self.assertOutcome(run(checks.check_gpu, FakeSystem(), node_role="lab"), "skip")

    def test_docker_cdi(self) -> None:
        # BUILD_PROMPT §7.6: CDI, Docker's default from 28.2.
        on = FakeSystem({CDI_INFO: (0, '["/etc/cdi","/var/run/cdi"]\n')})
        self.assertOutcome(run(checks.check_docker_cdi, on), "pass", "/etc/cdi, /var/run/cdi")
        for answer in ("null\n", "[]\n"):
            off = FakeSystem({CDI_INFO: (0, answer)})
            self.assertOutcome(run(checks.check_docker_cdi, off), "fail", "CDI is off")
            self.assertOutcome(
                run(checks.check_docker_cdi, off, gpu__method="runtime"), "pass", "NVIDIA runtime"
            )
        self.assertOutcome(run(checks.check_docker_cdi, FakeSystem()), "fail", "docker info")
        self.assertOutcome(run(checks.check_docker_cdi, on, node_role="lab"), "skip")
        self.assertOutcome(
            run(checks.check_docker_cdi, on, features={"gpu_training": True}), "pass"
        )
        self.assertOutcome(run(checks.check_docker_cdi, on, features={}), "skip")

    def test_driver_cuda(self) -> None:
        version = "DRIVER version      : 580.82.07\nCUDA Version        : 13.1\n"
        system = FakeSystem({"nvidia-smi --version": (0, version)})
        self.assertOutcome(run(checks.check_driver_cuda, system), "pass", "supports CUDA 13.1")
        old = FakeSystem({"nvidia-smi --version": (0, "CUDA Version : 12.8\n")})
        self.assertOutcome(
            run(checks.check_driver_cuda, old),
            "fail",
            "supports CUDA 12.8, the pinned vLLM image needs 13.0: install a driver branch",
        )
        self.assertOutcome(
            run(checks.check_driver_cuda, old, provider="wsl2"),
            "fail",
            "update the NVIDIA driver on Windows",
        )
        banner = "| NVIDIA-SMI 580.82.07   Driver Version: 580.82.07   CUDA Version: 13.0 |\n"
        fallback = FakeSystem({"nvidia-smi --version": (2, ""), "nvidia-smi": (0, banner)})
        self.assertOutcome(run(checks.check_driver_cuda, fallback), "pass", "CUDA 13.0")
        self.assertOutcome(run(checks.check_driver_cuda, FakeSystem()), "fail", "does not report")
        self.assertOutcome(run(checks.check_driver_cuda, FakeSystem(), node_role="lab"), "skip")

    def test_docs_service(self) -> None:
        # §7.19: the service is checked only with a configured release.
        self.assertOutcome(run(checks.check_docs, FakeSystem()), "skip", "docs_release")
        docs = {
            "dir": "/usr/local/share/pinka/docs",
            "port": 18706,
            "unit": "pinka-docs.service",
            "configured": True,
        }
        system = FakeSystem({"systemctl is-active pinka-docs.service": (0, "active\n")})
        self.assertOutcome(run(checks.check_docs, system, docs=docs), "fail", "no documentation")
        system.docs_versions.append("pinka-docs-v1")
        self.assertOutcome(
            run(checks.check_docs, system, docs=docs), "pass", "serving pinka-docs-v1"
        )
        system.commands["systemctl is-active pinka-docs.service"] = (3, "inactive\n")
        self.assertOutcome(run(checks.check_docs, system, docs=docs), "fail", "is inactive")

    def test_vllm(self) -> None:
        self.assertOutcome(run(checks.check_vllm, FakeSystem()), "skip", "no deployment")
        current = {"/etc/pinka/vllm/current.yml": "model: x\n"}
        system = FakeSystem(files=current, http={"GET http://127.0.0.1:18704/health": (200, "")})
        self.assertOutcome(run(checks.check_vllm, system), "pass")
        system = FakeSystem(files=current, http={"GET http://127.0.0.1:18704/health": (503, "")})
        self.assertOutcome(run(checks.check_vllm, system), "fail", "503")


class IncusTest(Base):
    def pool(self, driver: str, free_gib: int) -> FakeSystem:
        return FakeSystem(
            {
                "incus query /1.0/storage-pools/pinka-targets": (0, {"driver": driver}),
                "incus query /1.0/storage-pools/pinka-targets/resources": (
                    0,
                    {"space": {"total": 100 * 2**30, "used": (100 - free_gib) * 2**30}},
                ),
            }
        )

    def test_pool(self) -> None:
        self.assertOutcome(run(checks.check_incus_pool, self.pool("btrfs", 50)), "pass", "50.0")
        self.assertOutcome(
            run(checks.check_incus_pool, self.pool("btrfs", 5)), "fail", "less than 20 GiB"
        )
        self.assertOutcome(
            run(checks.check_incus_pool, self.pool("dir", 50)), "fail", "expected btrfs"
        )
        self.assertOutcome(
            run(checks.check_incus_pool, self.pool("lvm", 50), incus__backend="lvm"), "pass"
        )
        self.assertOutcome(
            run(checks.check_incus_pool, FakeSystem(), node_role="inference"), "skip"
        )

    def test_query_failure_raises(self) -> None:
        with self.assertRaises(RuntimeError):
            run(checks.check_incus_pool, FakeSystem())

    def test_objects(self) -> None:
        system = FakeSystem(
            {
                "incus query /1.0/projects?recursion=1": (
                    0,
                    [{"name": "bench"}, {"name": "build"}],
                ),
                "incus query /1.0/profiles?project=bench": (
                    0,
                    [
                        "/1.0/profiles/default?project=bench",
                        "/1.0/profiles/target-container?project=bench",
                    ],
                ),
            }
        )
        self.assertOutcome(run(checks.check_incus_objects, system), "pass")
        system.commands["incus query /1.0/projects?recursion=1"] = (0, [{"name": "bench"}])
        system.commands["incus query /1.0/profiles?project=bench"] = (0, [])
        outcome = run(checks.check_incus_objects, system)
        self.assertOutcome(outcome, "fail", "build")
        self.assertIn("profile target-container (bench)", outcome.detail)

    def test_certificates(self) -> None:
        good = {
            "/etc/pinka/incus/demo-ctl/client.crt": info("demo-ctl", "root", 0o600),
            "/etc/pinka/incus/demo-ctl/client.key": info("demo-ctl", "root", 0o600),
        }
        trusted = {"incus query /1.0/certificates?recursion=1": (0, [{"name": "demo-ctl"}])}
        self.assertOutcome(
            run(checks.check_incus_certificates, FakeSystem(trusted, infos=good)), "pass"
        )
        loose = dict(good, **{"/etc/pinka/incus/demo-ctl/client.key": info("root", "root", 0o644)})
        self.assertOutcome(
            run(checks.check_incus_certificates, FakeSystem(trusted, infos=loose)),
            "fail",
            "client.key is root 0644",
        )
        untrusted: dict[str, Any] = {"incus query /1.0/certificates?recursion=1": (0, [])}
        self.assertOutcome(
            run(checks.check_incus_certificates, FakeSystem(untrusted, infos={})),
            "fail",
            "demo-ctl is not trusted",
        )

    def test_metrics_and_events(self) -> None:
        url = "GET http://127.0.0.1:18701/api/v1/query?query=up%7Bjob%3D%22incus%22%7D"
        up = {"data": {"result": [{"value": [0, "1"]}]}}
        self.assertOutcome(
            run(checks.check_incus_metrics, FakeSystem(http={url: (200, up)})), "pass"
        )
        down = {"data": {"result": [{"value": [0, "0"]}]}}
        self.assertOutcome(
            run(checks.check_incus_metrics, FakeSystem(http={url: (200, down)})), "fail", "down"
        )
        self.assertOutcome(run(checks.check_incus_metrics, FakeSystem()), "fail", "no incus")
        active = {"systemctl is-active pinka-incus-events.service": (0, "active\n")}
        self.assertOutcome(run(checks.check_incus_events, FakeSystem(active)), "pass")
        self.assertOutcome(run(checks.check_incus_events, FakeSystem()), "fail")


class LayoutAccountsTest(Base):
    def test_layout(self) -> None:
        acl = "user::rwx\ngroup::rwx\ngroup:pinka:rwx\t#effective:rwx\nmask::rwx\nother::---\n"
        getfacl = "getfacl --omit-header --absolute-names --default /srv/pinka/shared"
        right = {"/srv/pinka/shared": info("root", "pinka", 0o2770, is_dir=True)}
        self.assertOutcome(
            run(checks.check_layout, FakeSystem({getfacl: (0, acl)}, infos=right)), "pass"
        )
        self.assertOutcome(
            run(checks.check_layout, FakeSystem({getfacl: (0, "user::rwx\n")}, infos=right)),
            "fail",
            "lacks default ACL group:pinka:rwx",
        )
        wrong = {"/srv/pinka/shared": info("root", "root", 0o755, is_dir=True)}
        self.assertOutcome(
            run(checks.check_layout, FakeSystem({getfacl: (0, acl)}, infos=wrong)),
            "fail",
            "is root:root 0755, expected root:pinka 2770",
        )
        self.assertOutcome(run(checks.check_layout, FakeSystem()), "fail", "missing")

    def test_accounts(self) -> None:
        clean = {
            "researcher1": ["researcher1", "pinka", "pinka-admin"],
            "researcher2": ["researcher2", "pinka"],
            "demo-ctl": ["demo-ctl", "demo-db"],
        }
        self.assertOutcome(run(checks.check_accounts, FakeSystem(groups=clean)), "pass")
        docker = dict(clean, researcher1=["researcher1", "docker", "incus-admin"])
        outcome = run(checks.check_accounts, FakeSystem(groups=docker))
        self.assertOutcome(outcome, "fail", "researcher1 is in docker")
        self.assertIn("researcher1 is in incus-admin", outcome.detail)
        outcome = run(
            checks.check_accounts,
            FakeSystem(groups=docker),
            accounts__break_glass_docker=["researcher1"],
        )
        self.assertNotIn("is in docker", outcome.detail)
        self.assertOutcome(outcome, "fail", "incus-admin")
        svc = dict(clean, **{"demo-agent": ["demo-agent", "docker"]})
        self.assertOutcome(
            run(checks.check_accounts, FakeSystem(groups=svc)),
            "fail",
            "platform account demo-agent is in docker",
        )

    def test_deployed_paths(self) -> None:
        clean = FakeSystem(
            dirs={
                "/srv/pinka/platforms/demo/releases/1.0.0": [],
                "/srv/pinka/restricted/demo/cases-1.0.0": [],
            },
            files={
                "/srv/pinka/restricted/demo/cases-1.0.0/DEPLOYED_FROM.json": json.dumps(
                    {"package": "cases", "restricted": True}
                )
            },
        )
        self.assertOutcome(run(checks.check_deployed_paths, clean), "pass")
        for top in ("platforms/demo/releases/1.0.0", "restricted/demo/cases-1.0.0"):
            git = FakeSystem(dirs={f"/srv/pinka/{top}/.git": []})
            self.assertOutcome(run(checks.check_deployed_paths, git), "fail", ".git exists")
        outside = FakeSystem(
            dirs={"/srv/pinka/platforms/demo/content/cases-1.0.0": []},
            files={
                "/srv/pinka/platforms/demo/content/cases-1.0.0/DEPLOYED_FROM.json": json.dumps(
                    {"package": "cases", "restricted": True}
                )
            },
        )
        self.assertOutcome(
            run(checks.check_deployed_paths, outside), "fail", "restricted package cases in"
        )

    def test_hardening(self) -> None:
        self.assertOutcome(run(checks.check_hardening, FakeSystem()), "skip")
        unit = {
            "unit": "pinka-inference-control.service",
            "properties": {"NoNewPrivileges": "yes"},
            "inaccessible_paths": ["/srv/pinka/restricted", "/run/pinka/vllm-deploy.sock"],
        }
        show = (
            "systemctl show pinka-inference-control.service "
            "--property=NoNewPrivileges,InaccessiblePaths"
        )
        good = FakeSystem(
            {
                show: (
                    0,
                    "NoNewPrivileges=yes\n"
                    "InaccessiblePaths=-/srv/pinka/restricted /etc/pinka -/run/pinka/vllm-deploy.sock\n",
                )
            }
        )
        self.assertOutcome(run(checks.check_hardening, good, hardening=[unit]), "pass")
        bad = FakeSystem({show: (0, "NoNewPrivileges=no\nInaccessiblePaths=\n")})
        outcome = run(checks.check_hardening, bad, hardening=[unit])
        self.assertOutcome(outcome, "fail", "NoNewPrivileges='no'")
        self.assertIn("InaccessiblePaths lacks /srv/pinka/restricted", outcome.detail)

    def test_platform_hardening_comes_from_its_rendering(self) -> None:
        # §7.17: per profile, from rendered.json, never from platform names.
        agent = {
            "unit": "demo-agt.service",
            "profile": "untrusted-agent",
            "properties": {"PrivateNetwork": "yes", "User": "demo-agt"},
            "address_families": ["AF_UNIX"],
            "inaccessible_paths": ["/run/pinka/postgres"],
        }
        rendered = json.dumps({"platform": "demo", "hardening": [agent]})
        show = (
            "systemctl show demo-agt.service "
            "--property=PrivateNetwork,User,InaccessiblePaths,RestrictAddressFamilies"
        )
        values = (
            "PrivateNetwork=yes\nUser=demo-agt\nInaccessiblePaths=-/run/pinka/postgres\n"
            "RestrictAddressFamilies=AF_UNIX\n"
        )
        system = FakeSystem({show: (0, values)}, files={RENDERED: rendered})
        self.assertOutcome(run(checks.check_hardening, system), "pass", "1 unit(s)")
        system.commands[show] = (0, values.replace("AF_UNIX\n", "AF_UNIX AF_INET\n"))
        self.assertOutcome(
            run(checks.check_hardening, system), "fail", "RestrictAddressFamilies='AF_UNIX AF_INET'"
        )
        system.commands[show] = (0, values.replace("AF_UNIX\n", "~AF_INET\n"))
        self.assertOutcome(run(checks.check_hardening, system), "fail", "~AF_INET")
        system.commands[show] = (0, values.replace("PrivateNetwork=yes", "PrivateNetwork=no"))
        self.assertOutcome(run(checks.check_hardening, system), "fail", "PrivateNetwork='no'")

    def test_platform_manifest(self) -> None:
        self.assertOutcome(run(checks.check_platform_manifest, FakeSystem()), "skip")
        target = "/srv/pinka/platforms/demo/releases/v1.0.0"
        digest = "a" * 64
        active = {
            "name": "demo",
            "version": "1.0.0",
            "manifest_sha256": digest,
            "release": {"target": target},
        }
        provenance = {"platform": {"manifest_sha256": digest}}
        validate = (
            f"/usr/local/libexec/pinka/pinka-platform validate {target} --built "
            "--no-host-check --json"
        )
        system = FakeSystem(
            {
                f"sha256sum {target}/platform-manifest.yaml": (0, f"{digest}  manifest\n"),
                validate: (0, {"valid": True, "problems": []}),
            },
            files={
                "/etc/pinka/platform/active.json": json.dumps(active),
                f"{target}/DEPLOYED_FROM.json": json.dumps(provenance),
            },
        )
        self.assertOutcome(run(checks.check_platform_manifest, system), "pass", "demo 1.0.0")
        system.commands[f"sha256sum {target}/platform-manifest.yaml"] = (0, f"{'b' * 64}  m\n")
        outcome = run(checks.check_platform_manifest, system)
        self.assertOutcome(outcome, "fail", f"active.json records {digest}")
        self.assertIn("DEPLOYED_FROM.json records", outcome.detail)
        system.commands[f"sha256sum {target}/platform-manifest.yaml"] = (0, f"{digest}  m\n")
        refused = {"valid": False, "problems": [{"message": "service x: unknown profile"}]}
        system.commands[validate] = (65, refused)
        self.assertOutcome(
            run(checks.check_platform_manifest, system), "fail", "no longer validates: service x"
        )

    SOCKET = "/run/pinka/platform/ctl/api.sock"
    AGENT_SOCKET = "/run/pinka/platform/ctl/agent.sock"

    def socket_system(self, **extra: Any) -> FakeSystem:
        sockets = [
            {
                "path": self.SOCKET,
                "owner": "demo-ctl",
                "group": "demo-ctl-api",
                "mode": "0660",
                "acl": ["group:pinka:rw-"],
                "untrusted_clients": False,
            },
            {
                "path": self.AGENT_SOCKET,
                "owner": "demo-ctl",
                "group": "demo-ctl-agent",
                "mode": "0660",
                "acl": [],
                "untrusted_clients": True,
            },
        ]
        getfacl = f"getfacl --omit-header --absolute-names {self.SOCKET}"
        return FakeSystem(
            {getfacl: (0, "user::rw-\ngroup::rw-\ngroup:pinka:rw-\nmask::rw-\nother::---\n")},
            files={RENDERED: json.dumps({"platform": "demo", "sockets": sockets, **extra})},
            infos={
                self.SOCKET: info("demo-ctl", "demo-ctl-api", 0o660),
                self.AGENT_SOCKET: info("demo-ctl", "demo-ctl-agent", 0o660),
            },
        )

    def test_sockets(self) -> None:
        self.assertOutcome(run(checks.check_sockets, FakeSystem()), "skip")
        system = self.socket_system()
        self.assertOutcome(
            run(checks.check_sockets, system),
            "pass",
            f"dedicated agent sockets: {self.AGENT_SOCKET}",
        )
        getfacl = f"getfacl --omit-header --absolute-names {self.SOCKET}"
        system.commands[getfacl] = (0, "user::rw-\ngroup::rw-\nother::---\n")
        self.assertOutcome(run(checks.check_sockets, system), "fail", "lacks ACL group:pinka:rw-")
        # A narrower mask shows in the group bits.
        system = self.socket_system()
        system.infos[self.SOCKET] = info("demo-ctl", "demo-ctl-api", 0o640)
        self.assertOutcome(
            run(checks.check_sockets, system), "fail", "demo-ctl:demo-ctl-api 0640, expected"
        )
        system.infos.pop(self.AGENT_SOCKET)
        self.assertOutcome(
            run(checks.check_sockets, system), "fail", f"{self.AGENT_SOCKET} missing"
        )

    def test_sockets_that_fell_back(self) -> None:
        # The helper's fallback: 0666 without ACL entries (sockets.acl_fallback warns).
        system = self.socket_system(socket_acl_fallback=[self.SOCKET])
        system.infos[self.SOCKET] = info("demo-ctl", "demo-ctl-api", 0o666)
        self.assertOutcome(run(checks.check_sockets, system), "pass")

    def test_agent_view(self) -> None:
        self.assertOutcome(run(checks.check_agent_view, FakeSystem()), "skip")
        view: dict[str, Any] = {
            "sockets": ["/run/pinka/otlp/agent.sock", self.AGENT_SOCKET],
            "own_dirs": ["/run/pinka/platform-runtime/agt"],
        }
        rendered = json.dumps(
            {
                "hardening": [
                    {"unit": "demo-agt.service", "profile": "untrusted-agent", "agent_view": view}
                ]
            }
        )
        pid = "systemctl show demo-agt.service --property=MainPID --value"
        root = "/proc/4242/root"
        find = f"find {root} -path {root}/proc -prune -o -path {root}/sys -prune -o -type s -print"
        seen = [f"{root}{self.AGENT_SOCKET}", f"{root}/run/pinka/platform-runtime/agt/own.sock"]
        system = FakeSystem(
            {pid: (0, "4242\n"), find: (0, "\n".join(seen) + "\n")}, files={RENDERED: rendered}
        )
        self.assertOutcome(run(checks.check_agent_view, system), "pass", "2 socket(s)")
        # V6: of the release only its package and its environment.
        release = "/srv/pinka/platforms/demo/releases/demo-v1"
        view.update(release=release, release_paths=["packages/demo-agent", ".agent-venvs/agt"])
        system.files[RENDERED] = json.dumps(
            {"hardening": [{"unit": "demo-agt.service", "agent_view": view}]}
        )
        listing = f"find {root}{release} -mindepth 1 -maxdepth 2 -print"
        own = ["packages", "packages/demo-agent", ".agent-venvs", ".agent-venvs/agt"]
        system.commands[listing] = (0, "".join(f"{root}{release}/{p}\n" for p in own))
        self.assertOutcome(run(checks.check_agent_view, system), "pass", "2 socket(s)")
        system.commands[listing] = (
            0,
            "".join(
                f"{root}{release}/{p}\n"
                for p in [*own, "packages/demo-injector", "platform-manifest.yaml"]
            ),
        )
        self.assertOutcome(
            run(checks.check_agent_view, system),
            "fail",
            "beyond its package and environment: packages/demo-injector, platform-manifest.yaml",
        )
        system.commands[listing] = (0, "".join(f"{root}{release}/{p}\n" for p in own))
        system.commands[find] = (0, "\n".join([*seen, f"{root}/run/pinka/postgres/.s.PGSQL.5432"]))
        self.assertOutcome(
            run(checks.check_agent_view, system),
            "fail",
            "beyond its allowlist: /run/pinka/postgres",
        )
        system.commands[pid] = (0, "0\n")
        self.assertOutcome(
            run(checks.check_agent_view, system), "skip", "no untrusted-agent service is running"
        )


class SocketAclFallbackTest(Base):
    API = "/run/pinka/platform/ctl/api.sock"
    SVC = "/run/pinka/platform/ctl/svc.sock"
    RENDERED = "/etc/pinka/platform/rendered.json"

    def rendered(self, **extra: Any) -> str:
        sockets = [
            {"path": self.API, "acl": ["group:pinka:rw-"]},
            {"path": self.SVC, "acl": []},
        ]
        return json.dumps({"platform": "demo", "sockets": sockets, **extra})

    def test_skipped_without_a_platform_or_human_sockets(self) -> None:
        self.assertOutcome(run(checks.check_socket_acl_fallback, FakeSystem()), "skip")
        only_services = json.dumps({"sockets": [{"path": self.SVC, "acl": []}]})
        system = FakeSystem(files={self.RENDERED: only_services})
        self.assertOutcome(run(checks.check_socket_acl_fallback, system), "skip")

    def test_sockets_with_acl_entries_pass(self) -> None:
        system = FakeSystem(
            files={self.RENDERED: self.rendered()},
            infos={self.API: info("demo-ctl", "demo-ctl-api", 0o660)},
        )
        self.assertOutcome(run(checks.check_socket_acl_fallback, system), "pass", "1 socket")

    def test_each_recorded_fallback_is_reported(self) -> None:
        system = FakeSystem(
            files={self.RENDERED: self.rendered(socket_acl_fallback=[self.API])},
            infos={self.API: info("demo-ctl", "demo-ctl-api", 0o660)},
        )
        outcome = run(checks.check_socket_acl_fallback, system)
        self.assertOutcome(outcome, "fail", f"{self.API} is 0666 without ACL entries")
        check = next(c for c in checks.CHECKS if c.id == "sockets.acl_fallback")
        self.assertEqual(check.severity, "warning")

    def test_a_0666_socket_is_reported_without_a_record(self) -> None:
        system = FakeSystem(
            files={self.RENDERED: self.rendered()},
            infos={self.API: info("demo-ctl", "demo-ctl-api", 0o666)},
        )
        self.assertOutcome(run(checks.check_socket_acl_fallback, system), "fail", self.API)

    def test_the_path_comes_from_the_lab_description(self) -> None:
        system = FakeSystem(
            files={"/x/rendered.json": self.rendered(socket_acl_fallback=[self.API])}
        )
        outcome = run(
            checks.check_socket_acl_fallback,
            system,
            paths={**LAB["paths"], "rendered_platform": "/x/rendered.json"},
        )
        self.assertOutcome(outcome, "fail", self.API)
        broken = FakeSystem(files={self.RENDERED: "{"})
        self.assertOutcome(run(checks.check_socket_acl_fallback, broken), "fail", "not JSON")


def guard_rule(name: str, ports: list[int], negated: bool = True) -> dict[str, Any]:
    """One rule of a telemetry guard in ``nft -j`` output."""
    return {
        "expr": [
            {"match": {"op": "==", "left": {"meta": {"key": "oifname"}}, "right": "lo"}},
            {
                "match": {
                    "op": "==",
                    "left": {"payload": {"protocol": "tcp", "field": "dport"}},
                    "right": {"set": ports} if len(ports) > 1 else ports[0],
                }
            },
            {
                "match": {
                    "op": "!=" if negated else "==",
                    "left": {"meta": {"key": "skuid"}},
                    "right": f"@{name}",
                }
            },
            {"reject": {"type": "tcp reset"}},
        ]
    }


def guard_json(
    elements: list[int],
    ports: list[int],
    negated: bool = True,
    query: list[int] | None = None,
    query_ports: list[int] | None = None,
) -> str:
    """``nft -j list table`` output of a telemetry guard: the stores' set and the query port's."""
    query = [0, 995] if query is None else query
    query_ports = [18733] if query_ports is None else query_ports
    items: list[dict[str, Any]] = [
        {"metainfo": {"version": "1.0.9"}},
        {"table": {"family": "inet", "name": "pinka_telemetry_guard"}},
        {"set": {"name": "telemetry_clients", "type": "uid", "elem": elements}},
        {"set": {"name": "query_port_clients", "type": "uid", "elem": query}},
        {"rule": guard_rule("telemetry_clients", ports, negated)},
    ]
    if query_ports:
        items.append({"rule": guard_rule("query_port_clients", query_ports)})
    return json.dumps({"nftables": items})


class TelemetryIsolationTest(Base):
    QUERY = "/run/pinka/query/query.sock"
    LINK = "/run/pinka/query.sock"
    DIR = "/run/pinka/query"
    AGENT = "/run/pinka/otlp/agent.sock"
    NFT = "nft -j list table inet pinka_telemetry_guard"
    CLIENTS = {"root": 0, "pinka-otelcol": 991, "pinka-query-proxy": 992, "pinka-grafana": 995}
    SPEC: dict[str, Any] = {
        "clients": ["root", "pinka-otelcol", "pinka-query-proxy"],
        "ports": [18701, 18702, 18703],
        "query_clients": ["root", "pinka-grafana"],
        "query_ports": [18733],
        "query_link": "/run/pinka/query.sock",
        "query_target": "query/query.sock",
        "query_dir": "/run/pinka/query",
        "query_owner": "pinka-query-proxy",
        "query_socket": "/run/pinka/query/query.sock",
        "agent_socket": "/run/pinka/otlp/agent.sock",
    }

    def system(
        self,
        guard: str | None = None,
        query: int = 0o660,
        group: str = "pinka-query",
        target: str = "query/query.sock",
        owner: str = "pinka-query-proxy",
    ) -> FakeSystem:
        loaded = guard if guard is not None else guard_json([0, 991, 992], [18701, 18702, 18703])
        return FakeSystem(
            {self.NFT: (0, loaded)},
            infos={
                self.QUERY: info("pinka-query-proxy", group, query),
                self.AGENT: info("root", "root", 0o666),
                self.DIR: info(owner, "pinka-query", 0o750, is_dir=True),
            },
            uids=dict(self.CLIENTS),
            links={self.LINK: target},
        )

    def check(self, system: FakeSystem) -> Any:
        return run(checks.check_telemetry_isolation, system, telemetry_isolation=self.SPEC)

    def test_skipped_without_a_specification(self) -> None:
        outcome = run(checks.check_telemetry_isolation, FakeSystem(), telemetry_isolation={})
        self.assertOutcome(outcome, "skip")

    def test_guard_and_sockets_in_place(self) -> None:
        self.assertOutcome(self.check(self.system()), "pass", "admits only infra's clients")

    def test_the_guard_admits_exactly_infras_clients(self) -> None:
        # ADR-0016 as decided with the "commit" for 3d.4: an allowlist, no more.
        wider = self.system(guard=guard_json([0, 991, 992, 1000], [18701, 18702, 18703]))
        self.assertOutcome(self.check(wider), "fail", "telemetry_clients also admits UIDs 1000")
        narrower = self.system(guard=guard_json([0, 991], [18701, 18702, 18703]))
        self.assertOutcome(
            self.check(narrower), "fail", "telemetry_clients does not admit pinka-query-proxy"
        )
        inverted = self.system(guard=guard_json([0, 991, 992], [18701, 18702, 18703], False))
        self.assertOutcome(
            self.check(inverted), "fail", "no rule for the accounts outside telemetry_clients"
        )
        system = self.system()
        del system.uids["pinka-otelcol"]
        self.assertOutcome(self.check(system), "fail", "client pinka-otelcol does not exist")

    def test_problems_are_named(self) -> None:
        missing = FakeSystem({self.NFT: (1, "")}, uids=dict(self.CLIENTS))
        outcome = self.check(missing)
        self.assertOutcome(outcome, "fail", "is not loaded")
        self.assertIn("/run/pinka/query/query.sock is missing", outcome.detail)
        narrow = self.system(guard=guard_json([0, 991, 992], [18701]))
        self.assertOutcome(
            self.check(narrow), "fail", "telemetry_clients leaves ports open: 18702, 18703"
        )
        loose = self.system(query=0o666, group="pinka")
        self.assertOutcome(self.check(loose), "fail", "query.sock is pinka 0666")

    def test_grafana_reaches_only_the_query_port(self) -> None:
        # ADR-0016 as decided with the "commit" for 3d.4b: Grafana's data
        # sources go through the query proxy, so Grafana is not a store client.
        on_the_stores = self.system(guard=guard_json([0, 991, 992, 995], [18701, 18702, 18703]))
        self.assertOutcome(
            self.check(on_the_stores), "fail", "telemetry_clients also admits UIDs 995"
        )
        wider = self.system(
            guard=guard_json([0, 991, 992], [18701, 18702, 18703], query=[0, 995, 1000])
        )
        self.assertOutcome(self.check(wider), "fail", "query_port_clients also admits UIDs 1000")
        without = self.system(guard=guard_json([0, 991, 992], [18701, 18702, 18703], query=[0]))
        self.assertOutcome(
            self.check(without), "fail", "query_port_clients does not admit pinka-grafana"
        )
        unguarded = self.system(
            guard=guard_json([0, 991, 992], [18701, 18702, 18703], query_ports=[])
        )
        self.assertOutcome(
            self.check(unguarded), "fail", "no rule for the accounts outside query_port_clients"
        )
        # The query port inside the stores' rule does not count for its own set.
        moved = self.system(
            guard=guard_json([0, 991, 992], [18701, 18702, 18703, 18733], query_ports=[18799])
        )
        self.assertOutcome(self.check(moved), "fail", "query_port_clients leaves ports open: 18733")

    def test_the_query_link_and_its_directory(self) -> None:
        # The owner's condition for the link (with the "commit" for 3d.4).
        elsewhere = self.system(target="/tmp/query.sock")
        self.assertOutcome(
            self.check(elsewhere),
            "fail",
            "/run/pinka/query.sock points to /tmp/query.sock, not query/query.sock",
        )
        not_a_link = self.system()
        not_a_link.links.clear()
        self.assertOutcome(self.check(not_a_link), "fail", "/run/pinka/query.sock is not a link")
        taken = self.system(owner="pinka-build")
        self.assertOutcome(
            self.check(taken),
            "fail",
            "/run/pinka/query is pinka-build:pinka-query 0750, not pinka-query-proxy:pinka-query",
        )


class BackupPlatformStateTest(Base):
    RENDERED = "/etc/pinka/platform/rendered.json"
    RECORDS = "/srv/pinka/platforms/demo/state/ctl/records"
    CACHE = "/srv/pinka/platforms/demo/state/ctl/cache"

    def system(self, state: list[str], paths: list[str]) -> FakeSystem:
        rendered = {"platform": "demo", "state_dirs": state, "backup": {"paths": paths}}
        return FakeSystem(files={self.RENDERED: json.dumps(rendered)})

    def test_skipped_without_a_platform_or_state(self) -> None:
        self.assertOutcome(run(checks.check_backup_platform_state, FakeSystem()), "skip")
        self.assertOutcome(run(checks.check_backup_platform_state, self.system([], [])), "skip")

    def test_every_state_directory_backed_up(self) -> None:
        system = self.system([self.RECORDS], [self.RECORDS])
        self.assertOutcome(run(checks.check_backup_platform_state, system), "pass", "1 state")

    def test_left_out_directories_are_listed(self) -> None:
        system = self.system([self.RECORDS, self.CACHE], [self.RECORDS])
        outcome = run(checks.check_backup_platform_state, system)
        self.assertOutcome(outcome, "fail", self.CACHE)
        self.assertNotIn(self.RECORDS, outcome.detail)
        check = next(c for c in checks.CHECKS if c.id == "backup.platform_state")
        self.assertEqual(check.severity, "warning")


def psql_answers(
    roles: Sequence[str], restricted: dict[str, bool], settings: dict[str, str]
) -> Any:
    def answer(argv: Sequence[str]) -> tuple[int, str]:
        query = argv[-1]
        if "json_agg(rolname)" in query:
            return 0, json.dumps(list(roles))
        if "has_schema_privilege" in query:
            return 0, json.dumps(restricted)
        return 0, json.dumps(settings)

    return answer


ON = {"fsync": "on", "synchronous_commit": "on", "full_page_writes": "on", "data_checksums": "on"}


class DatabasePolicyTest(Base):
    def test_sql_names(self) -> None:
        self.assertEqual(checks.sql_names(["a", "b_c"]), "'a','b_c'")
        self.assertEqual(checks.sql_names([]), "NULL")
        with self.assertRaises(ValueError):
            checks.sql_names(["x'; DROP ROLE y; --"])

    def test_postgres(self) -> None:
        good = FakeSystem(
            {"docker exec *": psql_answers(["demo_ctl", "demo_reader"], {"demo_reader": False}, ON)}
        )
        self.assertOutcome(run(checks.check_postgres, good), "pass", "2 roles")
        argv = good.calls[0]
        self.assertEqual(argv[:5], ["docker", "exec", "--user", "postgres", "pinka-postgres"])
        bad = FakeSystem(
            {
                "docker exec *": psql_answers(
                    ["demo_ctl"], {"demo_reader": True}, dict(ON, fsync="off")
                )
            }
        )
        outcome = run(checks.check_postgres, bad)
        self.assertOutcome(outcome, "fail", "role demo_reader missing")
        self.assertIn("demo_reader can use schema restricted", outcome.detail)
        self.assertIn("fsync is off", outcome.detail)
        with self.assertRaises(RuntimeError):
            run(checks.check_postgres, FakeSystem())
        self.assertOutcome(run(checks.check_postgres, FakeSystem(), node_role="inference"), "skip")

    def test_opa(self) -> None:
        base = "http://127.0.0.1:8181"
        answers = {
            f"GET {base}/health": (200, "{}"),
            f"PUT {base}/v1/policies/doctor": (403, ""),
            f"PUT {base}/v1/data/pinka/doctor": (403, ""),
            f"GET {base}/v1/policies": (403, ""),
        }
        self.assertOutcome(run(checks.check_opa, FakeSystem(http=answers)), "pass")
        open_policy = dict(answers, **{f"PUT {base}/v1/policies/doctor": (200, "{}")})
        self.assertOutcome(
            run(checks.check_opa, FakeSystem(http=open_policy)),
            "fail",
            "PUT /v1/policies/doctor answered 200",
        )
        self.assertOutcome(run(checks.check_opa, FakeSystem()), "fail", "/health")


class TelemetryTest(Base):
    def stores(self, loki: bool = True, tempo: bool = True, prom: bool = True) -> FakeSystem:
        found = {"data": {"result": [{"value": [0, "1"]}]}}
        empty: dict[str, Any] = {"data": {"result": []}}
        return FakeSystem(
            http={
                "POST http://127.0.0.1:4318/v1/*": (200, "{}"),
                "GET http://127.0.0.1:18702/*": (200, found if loki else empty),
                "GET http://127.0.0.1:18703/*": (200, {"batches": [1]}) if tempo else (404, ""),
                "GET http://127.0.0.1:18701/*": (200, found if prom else empty),
            }
        )

    def test_roundtrip(self) -> None:
        system = self.stores()
        self.assertOutcome(run(checks.check_telemetry, system), "pass", "arrived")
        posted = [(m, u) for m, u, _ in system.http_calls if m == "POST"]
        self.assertEqual([u.rsplit("/", 1)[-1] for _, u in posted], ["logs", "traces", "metrics"])
        body = json.loads(system.http_calls[0][2] or b"{}")
        attributes = body["resourceLogs"][0]["resource"]["attributes"]
        run_id = next(a["value"]["stringValue"] for a in attributes if a["key"] == "pinka.probe_id")
        self.assertTrue(run_id.startswith("doctor-"))
        queried = " ".join(u for m, u, _ in system.http_calls if m == "GET")
        self.assertIn(run_id, queried)

    def test_missing_store(self) -> None:
        self.assertOutcome(run(checks.check_telemetry, self.stores(tempo=False)), "fail", "Tempo")

    def test_collector_rejects(self) -> None:
        system = FakeSystem(http={"POST *": (400, "bad")})
        self.assertOutcome(run(checks.check_telemetry, system), "fail", "rejected the logs")

    def test_no_stack(self) -> None:
        self.assertOutcome(run(checks.check_telemetry, FakeSystem(), observability={}), "skip")


class TimeBackupSessionTest(Base):
    def test_time(self) -> None:
        synced = {
            "timedatectl show --property=NTPSynchronized --value": (0, "yes\n"),
            "timedatectl show": (0, "NTPSynchronized=yes\nNTP=yes\n"),
            "timedatectl timesync-status": (0, "       Offset: +12ms\n"),
        }
        self.assertOutcome(run(checks.check_time, FakeSystem(synced)), "pass", "+0.012")
        late = dict(synced, **{"timedatectl timesync-status": (0, "Offset: +2s\n")})
        self.assertOutcome(run(checks.check_time, FakeSystem(late)), "fail", "exceeds 1 s")
        unknown = dict(synced, **{"timedatectl timesync-status": (1, "")})
        self.assertOutcome(run(checks.check_time, FakeSystem(unknown)), "pass", "offset unknown")
        self.assertOutcome(run(checks.check_time, FakeSystem()), "fail", "not NTP")

    def test_backup(self) -> None:
        state = "/var/lib/pinka/backup/state.json"
        recent = json.dumps({"backup": {"last_success": NOW_TS - 3600}})
        snapshots = {"/usr/local/libexec/pinka/backup snapshots": (0, "[]")}
        self.assertOutcome(
            run(checks.check_backup, FakeSystem(snapshots, files={state: recent})), "pass", "1.0 h"
        )
        old = json.dumps({"backup": {"last_success": NOW_TS - 48 * 3600}})
        self.assertOutcome(
            run(checks.check_backup, FakeSystem(snapshots, files={state: old})), "fail", "48 h"
        )
        self.assertOutcome(
            run(checks.check_backup, FakeSystem(files={state: recent})), "fail", "does not answer"
        )
        self.assertOutcome(run(checks.check_backup, FakeSystem()), "fail", "no backup has")
        self.assertOutcome(
            run(checks.check_backup, FakeSystem(), backup__configured=False), "fail", "E4"
        )

    def test_health_gate(self) -> None:
        path = "/run/pinka/health-gate.json"
        self.assertOutcome(run(checks.check_health_gate, FakeSystem()), "fail", "not run")
        ok = json.dumps({"passed": True, "checked_at": "2026-01-01T12:00:00Z"})
        self.assertOutcome(run(checks.check_health_gate, FakeSystem(files={path: ok})), "pass")
        bad = json.dumps(
            {
                "passed": False,
                "checks": [{"name": "time", "ok": True}, {"name": "gpu", "ok": False}],
            }
        )
        self.assertOutcome(
            run(checks.check_health_gate, FakeSystem(files={path: bad})), "fail", "failing: gpu"
        )

    def session_files(self, record: dict[str, Any]) -> dict[str, str]:
        return {
            "/proc/sys/kernel/random/boot_id": BOOT_ID + "\n",
            f"/var/lib/pinka/sessions/{BOOT_ID}.json": json.dumps(record),
        }

    def test_session(self) -> None:
        self.assertOutcome(run(checks.check_session, FakeSystem()), "fail", "no session record")
        good = self.session_files({"started_at": "2026-01-01T12:00:00Z", "errors": {}})
        self.assertOutcome(run(checks.check_session, FakeSystem(files=good)), "pass")
        partial = self.session_files({"errors": {"fingerprint": "exit 1"}})
        self.assertOutcome(
            run(checks.check_session, FakeSystem(files=partial)), "fail", "without fingerprint"
        )

    def test_rolling_drift(self) -> None:
        self.assertOutcome(
            run(checks.check_rolling_drift, FakeSystem(files={PROFILE_PATH: json.dumps(PROFILE)})),
            "skip",
            "not a rolling",
        )
        rolling = json.dumps(dict(PROFILE, release_model="rolling"))
        system = FakeSystem(files={PROFILE_PATH: rolling})
        current = fingerprint.compute(system, context(system).config)["hash"]
        system.files.update(self.session_files({"fingerprint_hash": current}))
        self.assertOutcome(run(checks.check_rolling_drift, system), "pass")
        system.files.update(self.session_files({"fingerprint_hash": "sha256:" + "0" * 64}))
        self.assertOutcome(run(checks.check_rolling_drift, system), "fail", "differs")


class WslTest(Base):
    CONF = "[boot]\nsystemd=true\n\n[automount]\nenabled=false\n\n[interop]\nenabled=false\n"

    def system(self, conf: str, mem_kb: int, cpus: str, kvm: bool = True) -> FakeSystem:
        infos = {"/dev/kvm": info("root", "kvm", 0o660)} if kvm else {}
        return FakeSystem(
            {"nproc": (0, cpus + "\n")},
            files={"/etc/wsl.conf": conf, "/proc/meminfo": f"MemTotal:       {mem_kb} kB\n"},
            infos=infos,
        )

    def test_not_wsl(self) -> None:
        self.assertOutcome(run(checks.check_wsl, FakeSystem()), "skip")

    def test_wsl(self) -> None:
        wsl = {
            "provider": "wsl2",
            "wsl__memory_gib": 16,
            "wsl__cpus": 8,
            "wsl__kvm_expected": True,
        }
        good = self.system(self.CONF, 16 * 2**20, "8")
        self.assertOutcome(run(checks.check_wsl, good, **wsl), "pass")
        interop = self.CONF.replace("[interop]\nenabled=false", "[interop]\nenabled=true")
        outcome = run(checks.check_wsl, self.system(interop, 8 * 2**20, "4", kvm=False), **wsl)
        self.assertOutcome(outcome, "fail", "[interop] enabled is not false")
        self.assertIn("expected 16 GiB", outcome.detail)
        self.assertIn("4 CPUs, expected 8", outcome.detail)
        self.assertIn("/dev/kvm is missing", outcome.detail)


if __name__ == "__main__":
    unittest.main()


class PairingTest(Base):
    DIR = "/etc/pinka/inference"

    def system(self, peer: str = "pinka-lab", days: int = 3650, files: bool = True) -> FakeSystem:
        end = (NOW + dt.timedelta(days=days)).strftime("%b %d %H:%M:%S %Y GMT")
        infos = (
            {
                f"{self.DIR}/{n}": info("root", "pinka-inference", 0o640)
                for n in ("self.crt", "self.key", "peer.crt")
            }
            if files
            else {}
        )
        records = {f"{self.DIR}/peer.json": json.dumps({"node": peer})} if peer else {}
        return FakeSystem({"openssl *": (0, f"notAfter={end}\n")}, files=records, infos=infos)

    def check(self, system: FakeSystem, peer: str = "", role: str = "inference") -> Outcome:
        group, mode = ("pinka-inference", "0640") if role == "inference" else ("root", "0600")
        return run(
            checks.check_pairing,
            system,
            node_role=role,
            inference__peer=peer,
            inference__key_group=group,
            inference__key_mode=mode,
        )

    def test_the_private_key_belongs_to_root_on_a_lab_node(self) -> None:
        # The forwarder runs as root; only an inference node's endpoint account reads the key.
        lab_node = self.system(peer="pinka-gpu")
        lab_node.infos[f"{self.DIR}/self.key"] = info("root", "root", 0o600)
        self.assertOutcome(self.check(lab_node, "pinka-gpu", role="lab"), "pass")
        shared = self.system(peer="pinka-gpu")
        self.assertOutcome(
            self.check(shared, "pinka-gpu", role="lab"),
            "fail",
            "self.key is root:pinka-inference 0640, expected root:root 0600",
        )
        # Checked before the pairing too: bootstrap creates the key.
        self.assertOutcome(
            self.check(self.system(peer=""), role="lab"), "fail", "expected root:root 0600"
        )
        loose = self.system()
        loose.infos[f"{self.DIR}/self.key"] = info("root", "pinka-inference", 0o644)
        self.assertOutcome(
            self.check(loose, "pinka-lab"),
            "fail",
            "self.key is root:pinka-inference 0644, expected root:pinka-inference 0640",
        )

    def test_not_split(self) -> None:
        self.assertOutcome(self.check(self.system(), role="full"), "skip", "full node")

    def test_before_pairing(self) -> None:
        self.assertOutcome(self.check(self.system(peer="")), "skip", "not paired yet")
        self.assertOutcome(self.check(self.system(), role="lab"), "fail", "inference_remote_node")
        self.assertOutcome(self.check(self.system(files=False)), "fail", "self.crt missing")

    def test_paired(self) -> None:
        self.assertOutcome(self.check(self.system(), "pinka-lab"), "pass", "paired with pinka-lab")
        self.assertOutcome(
            self.check(self.system(peer="other"), "pinka-lab"),
            "fail",
            "inference_lab_node is pinka-lab, but the node is paired with other",
        )
        missing = self.system()
        del missing.infos[f"{self.DIR}/peer.crt"]
        self.assertOutcome(self.check(missing, "pinka-lab"), "fail", "peer.crt missing")
        self.assertOutcome(self.check(self.system(days=10), "pinka-lab"), "fail", "expires in 10")
        # Without openssl the expiry is not checked.
        unknown = self.system()
        unknown.commands = {}
        self.assertOutcome(self.check(unknown, "pinka-lab"), "pass")


class CpusetTest(Base):
    SLICE = "/sys/fs/cgroup/pinka-services.slice/cpuset.cpus.effective"

    def test_parse_and_format(self) -> None:
        self.assertEqual(checks.parse_cpuset("0-2,5,7-8"), {0, 1, 2, 5, 7, 8})
        self.assertEqual(checks.parse_cpuset(""), set())
        self.assertEqual(checks.format_cpuset({0, 1, 2, 5, 7, 8}), "0-2,5,7-8")
        with self.assertRaises(ValueError):
            checks.parse_cpuset("5-3")

    def test_not_configured(self) -> None:
        self.assertOutcome(run(checks.check_cpusets, FakeSystem()), "skip")

    def test_services_pinned(self) -> None:
        sets = {"cpusets__services": "0-5", "cpusets__targets": "6-23"}
        files = {"/sys/devices/system/cpu/online": "0-23\n", self.SLICE: "0-5\n"}
        outcome = run(checks.check_cpusets, FakeSystem(files=files), **sets)
        self.assertOutcome(outcome, "pass", "services on CPUs 0-5")

    def test_problems(self) -> None:
        sets = {"cpusets__services": "0-5", "cpusets__targets": "6-23"}
        files = {"/sys/devices/system/cpu/online": "0-15\n", self.SLICE: "0-15\n"}
        outcome = run(checks.check_cpusets, FakeSystem(files=files), **sets)
        self.assertOutcome(
            outcome, "fail", "cpuset_targets names CPUs this host does not have: 16-23"
        )
        self.assertIn("runs on CPUs 0-15, expected 0-5", outcome.detail)
        stopped = {"/sys/devices/system/cpu/online": "0-23\n"}
        outcome = run(checks.check_cpusets, FakeSystem(files=stopped), **sets)
        self.assertOutcome(outcome, "fail", "has no cgroup")

    def test_smt_siblings_in_different_sets(self) -> None:
        # P3a: cpu<n> and cpu<n+12> are one core's two threads.
        topology = "/sys/devices/system/cpu/cpu{}/topology/thread_siblings_list"
        siblings = {topology.format(n): f"{n % 12},{n % 12 + 12}\n" for n in range(24)}
        files = {"/sys/devices/system/cpu/online": "0-23\n", **siblings}
        split = {"cpusets__services": "0-5", "cpusets__targets": "6-23"}
        outcome = run(
            checks.check_cpusets, FakeSystem(files={**files, self.SLICE: "0-5\n"}), **split
        )
        self.assertOutcome(
            outcome,
            "fail",
            "SMT siblings of one core are split between cpuset_services and cpuset_targets "
            "(0,12; 1,13; 2,14; 3,15; 4,16; 5,17)",
        )
        whole = {"cpusets__services": "0-2,12-14", "cpusets__targets": "3-11,15-23"}
        slice_cpus = {self.SLICE: "0-2,12-14\n"}
        outcome = run(checks.check_cpusets, FakeSystem(files={**files, **slice_cpus}), **whole)
        self.assertOutcome(outcome, "pass")
        # Without SMT (one thread per core) nothing is split.
        single = {topology.format(n): f"{n}\n" for n in range(24)}
        no_smt = {"/sys/devices/system/cpu/online": "0-23\n", self.SLICE: "0-5\n", **single}
        outcome = run(checks.check_cpusets, FakeSystem(files=no_smt), **split)
        self.assertOutcome(outcome, "pass")


# --- the GPU arbiter, training and the inference guard (§7.11, §7.18) ---------------------

STATE = "/var/lib/pinka/gpu/state.json"
TRANSITIONS = "/var/lib/pinka/gpu/transitions.jsonl"
GPU_SOCKET = "/run/pinka/gpu.sock"
ADAPTERS = "/srv/pinka/models/adapters"
DATASETS = "/srv/pinka/training/datasets"
RENDERED = "/etc/pinka/platform/rendered.json"


def arbiter_section() -> dict[str, Any]:
    section = lab_data()["gpu_arbiter"]
    assert isinstance(section, dict)
    return section


class GpuArbiterTest(Base):
    def system(self, state: dict[str, Any] | None, **kwargs: Any) -> FakeSystem:
        files = {RENDERED: json.dumps({"controller": "demo-ctl"})}
        if state is not None:
            files[STATE] = json.dumps(state)
        files[TRANSITIONS] = kwargs.pop(
            "log",
            json.dumps({"from": "idle", "to": "serving", "outcome": "ok"}) + "\n",
        )
        return FakeSystem(
            kwargs.pop("commands", {}),
            files=files,
            infos={GPU_SOCKET: kwargs.pop("socket", info("demo-ctl", "root", 0o600))},
        )

    def test_skipped_without_an_arbiter(self) -> None:
        outcome = run(checks.check_gpu_arbiter, FakeSystem(), node_role="lab", gpu_arbiter={})
        self.assertOutcome(outcome, "skip", "a lab node has no GPU arbiter")

    def test_serving(self) -> None:
        system = self.system({"mode": "serving", "deployment": "dep@1"})
        outcome = run(checks.check_gpu_arbiter, system)
        self.assertOutcome(outcome, "pass", "serving dep@1; last transition idle -> serving (ok)")
        # A fresh arbiter has no state yet: idle.
        self.assertOutcome(run(checks.check_gpu_arbiter, self.system(None)), "pass", "idle")

    def test_the_socket_belongs_to_the_controller(self) -> None:
        wrong = self.system({"mode": "idle"}, socket=info("demo-agent", "root", 0o600))
        self.assertOutcome(
            run(checks.check_gpu_arbiter, wrong), "fail", "is demo-agent 0600, not demo-ctl 0600"
        )
        loose = self.system({"mode": "idle"}, socket=info("demo-ctl", "root", 0o666))
        self.assertOutcome(run(checks.check_gpu_arbiter, loose), "fail", "0666")
        inference = self.system({"mode": "idle"}, socket=info("pinka-inference", "root", 0o600))
        outcome = run(
            checks.check_gpu_arbiter,
            inference,
            gpu_arbiter={**arbiter_section(), "peer": "pinka-inference"},
        )
        self.assertOutcome(outcome, "pass")

    def test_a_training_run_needs_its_unit(self) -> None:
        run_id = "tune-20261012t080000-0123abcd"
        unit = f"systemctl is-active pinka-gpu-job@{run_id}.service"
        state = {"mode": "training", "run": run_id}
        running = self.system(state, commands={unit: (0, "active\n")})
        self.assertOutcome(run(checks.check_gpu_arbiter, running), "pass", f"training {run_id}")
        gone = self.system(state, commands={unit: (3, "inactive\n")})
        self.assertOutcome(run(checks.check_gpu_arbiter, gone), "fail", "is inactive")

    def test_broken_state_and_log(self) -> None:
        self.assertOutcome(
            run(checks.check_gpu_arbiter, self.system({"mode": "busy"})), "fail", "no valid mode"
        )
        broken = self.system({"mode": "idle"}, log="{}\nnot json\n")
        self.assertOutcome(run(checks.check_gpu_arbiter, broken), "fail", "line 2 is not JSON")


def job_container(**changes: Any) -> dict[str, Any]:
    run_id = "tune-20261012t080000-0123abcd"
    host = {
        "NetworkMode": "none",
        "ReadonlyRootfs": True,
        "CapDrop": ["ALL"],
        "CapAdd": None,
        "SecurityOpt": ["no-new-privileges"],
        "Privileged": False,
        "PidMode": "",
        "IpcMode": "private",
        "UsernsMode": "",
        "Tmpfs": {"/tmp": "rw,nosuid,nodev,size=1073741824"},
    }
    host.update(changes.pop("host", {}))
    mounts = [
        {"Source": "/srv/pinka/models/example-org/m/" + "0" * 40, "RW": False},
        {"Source": f"{DATASETS}/" + "a" * 64, "RW": False},
        {"Source": f"/srv/pinka/training/jobs/{run_id}/output", "RW": True},
        {"Source": f"/srv/pinka/training/jobs/{run_id}/checkpoints", "RW": True},
    ]
    return {
        "HostConfig": host,
        "Config": {"User": changes.pop("user", "60590:60590")},
        "Mounts": changes.pop("mounts", mounts),
    }


class TrainingContainersTest(Base):
    NAME = "pinka-job-tune-20261012t080000-0123abcd"

    def system(self, container: dict[str, Any]) -> FakeSystem:
        return FakeSystem(
            {
                "docker ps --filter name=^pinka-job- --format {{.Names}}": (0, self.NAME + "\n"),
                f"docker inspect {self.NAME}": (0, [container]),
            }
        )

    def test_fixed_options_pass(self) -> None:
        self.assertOutcome(
            run(checks.check_training_containers, self.system(job_container())), "pass", "1 running"
        )

    def test_widened_options_fail(self) -> None:
        cases: list[tuple[dict[str, Any], str]] = [
            ({"host": {"NetworkMode": "bridge"}}, "network bridge"),
            ({"host": {"ReadonlyRootfs": False}}, "writable root file system"),
            ({"host": {"CapAdd": ["SYS_ADMIN"]}}, "capabilities"),
            ({"host": {"SecurityOpt": ["seccomp=unconfined", "no-new-privileges"]}}, "unconfined"),
            ({"host": {"SecurityOpt": []}}, "no no-new-privileges"),
            ({"host": {"Privileged": True}}, "privileged"),
            ({"host": {"PidMode": "host"}}, "the host's pid namespace"),
            ({"host": {"Tmpfs": {"/tmp": "rw,nosuid,nodev"}}}, "no size-limited tmpfs"),
            ({"host": {"Tmpfs": None}}, "no size-limited tmpfs"),
            (
                {"host": {"Tmpfs": {"/tmp": "size=1g", "/run": "size=1g"}}},
                "no size-limited tmpfs as its only /tmp",
            ),
            ({"user": ""}, "runs as root"),
            ({"user": "0:0"}, "runs as root"),
            ({"mounts": [{"Source": "/etc/pinka/secrets", "RW": False}]}, "the mount /etc"),
            (
                {"mounts": [{"Source": f"{DATASETS}/" + "a" * 64, "RW": True}]},
                "(read-write)",
            ),
        ]
        for changes, detail in cases:
            outcome = run(checks.check_training_containers, self.system(job_container(**changes)))
            self.assertOutcome(outcome, "fail", detail)

    def test_skipped(self) -> None:
        idle = FakeSystem({"docker ps *": (0, "")})
        self.assertOutcome(run(checks.check_training_containers, idle), "skip", "no training run")
        outcome = run(
            checks.check_training_containers,
            idle,
            gpu_arbiter={**arbiter_section(), "training": False},
        )
        self.assertOutcome(outcome, "skip", "does not train")


class TrainingCapsTest(Base):
    JOB = {"name": "tune", "memory_max": "4G", "cpus": 2, "timeout_seconds": 600}

    def system(self, *jobs: dict[str, Any]) -> FakeSystem:
        return FakeSystem({}, files={RENDERED: json.dumps({"training": {"jobs": list(jobs)}})})

    def test_jobs_within_the_caps_pass(self) -> None:
        outcome = run(checks.check_training_caps, self.system(self.JOB))
        self.assertOutcome(outcome, "pass", "1 job(s) within this host's training_caps")

    def test_jobs_over_the_caps_warn(self) -> None:
        big = {**self.JOB, "memory_max": "32G", "cpus": 16, "timeout_seconds": 90000}
        outcome = run(checks.check_training_caps, self.system(big))
        self.assertOutcome(outcome, "fail", "the GPU arbiter refuses every run of tune:")
        for detail in ("memory_max 32G > 24G", "cpus 16 > 8", "timeout_seconds 90000 > 86400"):
            self.assertIn(detail, outcome.detail)
        check = next(c for c in checks.CHECKS if c.id == "training.caps")
        self.assertEqual(check.severity, "warning")

    def test_skipped(self) -> None:
        self.assertOutcome(run(checks.check_training_caps, self.system()), "skip", "no training")
        outcome = run(
            checks.check_training_caps,
            self.system(self.JOB),
            gpu_arbiter={**arbiter_section(), "training": False},
        )
        self.assertOutcome(outcome, "skip", "does not train")


class TrainingRecordsTest(Base):
    DIGEST = "b" * 64
    ADAPTER = f"{ADAPTERS}/refplat-tune/{'b' * 64}"
    DATASET = f"{DATASETS}/{'c' * 64}"

    def adapters(self, **changes: Any) -> FakeSystem:
        manifest = {"sha256": self.DIGEST, **changes.pop("manifest", {})}
        return FakeSystem(
            files={
                f"{self.ADAPTER}/MANIFEST.json": json.dumps(manifest),
                f"{self.ADAPTER}/LINEAGE.json": "{}",
            },
            infos={self.ADAPTER: changes.pop("dir", info("root", "root", 0o555, is_dir=True))},
            dirs={ADAPTERS: ["refplat-tune", ".tmp"], f"{ADAPTERS}/refplat-tune": ["b" * 64]},
            digests={self.ADAPTER: changes.pop("digest", self.DIGEST)},
        )

    def test_adapters(self) -> None:
        self.assertOutcome(
            run(checks.check_training_adapters, self.adapters()), "pass", "1 adapter"
        )
        self.assertOutcome(
            run(checks.check_training_adapters, self.adapters(digest="d" * 64)),
            "fail",
            "files do not match its hash",
        )
        self.assertOutcome(
            run(checks.check_training_adapters, self.adapters(manifest={"sha256": "e" * 64})),
            "fail",
            "MANIFEST.json and LINEAGE.json do not match",
        )
        writable = self.adapters(dir=info("root", "root", 0o755, is_dir=True))
        self.assertOutcome(run(checks.check_training_adapters, writable), "fail", "read-only")
        self.assertOutcome(run(checks.check_training_adapters, FakeSystem()), "skip", "no adapter")

    def datasets(self, instances: list[dict[str, str]], **changes: Any) -> FakeSystem:
        digest = "c" * 64
        provenance = {"dataset_sha256": digest, "instances": instances}
        return FakeSystem(
            files={f"{self.DATASET}/PROVENANCE.json": json.dumps(provenance)},
            infos={
                self.DATASET: changes.pop("dir", info("root", "pinka-datasets", 0o750, is_dir=True))
            },
            dirs={DATASETS: ["c" * 64, ".publish-x"]},
            digests={self.DATASET: changes.pop("digest", digest)},
        )

    def test_datasets_and_the_split_guard(self) -> None:
        train = [{"id": "case-1", "split": "train"}]
        self.assertOutcome(run(checks.check_training_datasets, self.datasets(train)), "pass")
        tested = self.datasets([*train, {"id": "case-9", "split": "test"}])
        self.assertOutcome(
            run(checks.check_training_datasets, tested), "fail", "test split: case-9"
        )
        changed = self.datasets(train, digest="0" * 64)
        self.assertOutcome(run(checks.check_training_datasets, changed), "fail", "do not match")
        open_dir = self.datasets(train, dir=info("root", "root", 0o755, is_dir=True))
        self.assertOutcome(run(checks.check_training_datasets, open_dir), "fail", "0750")

    def test_training_availability(self) -> None:
        self.assertOutcome(
            run(checks.check_training_availability, FakeSystem()), "pass", "runs on this host"
        )
        for role in ("lab", "inference"):
            outcome = run(checks.check_training_availability, FakeSystem(), node_role=role)
            self.assertOutcome(outcome, "pass", f"unavailable on this host (node_role {role})")


class SplitGuardTest(Base):
    NFT = "nft -j list table inet pinka_split_guard"
    PEER = "/etc/pinka/inference/peer.json"
    SPEC = {"interface": "tailscale0", "ports": [18704, 18710]}

    def loaded(self, peer: list[str], pairing: list[Any] | None = None) -> str:
        drop = {
            "family": "inet",
            "expr": [
                {"match": {"left": {"meta": {"key": "iifname"}}, "right": "tailscale0"}},
                {
                    "match": {
                        "left": {"payload": {"field": "dport"}},
                        "right": {"set": [18704, 18710]},
                    }
                },
                {"drop": None},
            ],
        }
        return json.dumps(
            {
                "nftables": [
                    {"set": {"name": "peer", "type": "ipv4_addr", "elem": peer}},
                    {"set": {"name": "pairing", "type": "inet_service", "elem": pairing or []}},
                    {"rule": drop},
                ]
            }
        )

    def check(
        self, loaded: str | None, record: dict[str, Any] | None, role: str = "inference"
    ) -> Outcome:
        files = {self.PEER: json.dumps(record)} if record is not None else {}
        commands = {self.NFT: (0, loaded) if loaded is not None else (1, "")}
        return run(
            checks.check_split_guard,
            FakeSystem(commands, files=files),
            node_role=role,
            split_guard=self.SPEC,
        )

    def test_only_the_paired_peer(self) -> None:
        paired = {"node": "pinka-lab", "address": "100.64.0.7"}
        self.assertOutcome(
            self.check(self.loaded(["100.64.0.7"]), paired), "pass", "admit only 100.64.0.7"
        )
        self.assertOutcome(
            self.check(self.loaded([]), None), "pass", "admit only nobody until the nodes pair"
        )
        # An unpaired address on the allowlist, or the peer missing from it.
        self.assertOutcome(
            self.check(self.loaded(["100.64.0.7", "100.64.0.9"]), paired),
            "fail",
            "the guard admits 100.64.0.7, 100.64.0.9, expected 100.64.0.7",
        )
        self.assertOutcome(
            self.check(self.loaded([]), paired), "fail", "admits nobody, expected 100.64.0.7"
        )
        self.assertOutcome(
            self.check(self.loaded([]), {"node": "pinka-lab"}),
            "fail",
            "no tailnet address; pair the nodes again",
        )
        self.assertOutcome(self.check(None, paired), "fail", "not loaded")

    def test_the_pairing_window_and_open_ports(self) -> None:
        paired = {"node": "pinka-lab", "address": "100.64.0.7"}
        window = [{"elem": {"val": 18710, "timeout": 90, "expires": 80}}]
        self.assertOutcome(
            self.check(self.loaded(["100.64.0.7"], window), paired),
            "fail",
            "the pairing port is open to the tailnet",
        )
        no_rule = json.dumps(
            {"nftables": [{"set": {"name": "peer", "type": "ipv4_addr", "elem": ["100.64.0.7"]}}]}
        )
        self.assertOutcome(
            self.check(no_rule, paired), "fail", "leaves ports open to the tailnet: 18704, 18710"
        )

    def test_a_full_node(self) -> None:
        self.assertOutcome(self.check(None, None, role="full"), "skip", "full node")


class InferenceIsolationTest(Base):
    NFT = "nft -j list table inet pinka_inference_guard"
    UIDS = {
        "root": 0,
        "pinka-prometheus": 992,
        "pinka-otelcol": 991,
        "demo-ctl": 60578,
        "demo-brk": 60579,
    }

    def guard(self, api: list[int], control: list[int] | None = None) -> str:
        items: list[dict[str, Any]] = [
            {"set": {"name": "inference_clients", "type": "uid", "elem": api}},
            {"rule": guard_rule("inference_clients", [18704])},
        ]
        if control is not None:
            items += [
                {"set": {"name": "inference_control_clients", "type": "uid", "elem": control}},
                {"rule": guard_rule("inference_control_clients", [18710])},
            ]
        return json.dumps({"nftables": items})

    def system(self, loaded: str) -> FakeSystem:
        rendered = {"controller": "demo-ctl", "inference_clients": ["demo-brk", "demo-ctl"]}
        return FakeSystem(
            {self.NFT: (0, loaded)}, files={RENDERED: json.dumps(rendered)}, uids=dict(self.UIDS)
        )

    def test_the_platforms_clients_and_infras(self) -> None:
        ok = self.system(self.guard([0, 991, 992, 60578, 60579]))
        self.assertOutcome(run(checks.check_inference_isolation, ok), "pass", "demo-brk, demo-ctl")
        wider = self.system(self.guard([0, 991, 992, 60578, 60579, 60580]))
        self.assertOutcome(
            run(checks.check_inference_isolation, wider), "fail", "also admits UIDs 60580"
        )
        stale = self.system(self.guard([0, 991, 992]))
        self.assertOutcome(
            run(checks.check_inference_isolation, stale),
            "fail",
            "does not admit demo-ctl, demo-brk",
        )
        missing = FakeSystem({self.NFT: (1, "")})
        self.assertOutcome(run(checks.check_inference_isolation, missing), "fail", "not loaded")

    def test_the_control_port_of_a_lab_node(self) -> None:
        spec = {"clients": ["root"], "ports": [18704], "control_ports": [18710]}
        ok = self.system(self.guard([0, 60578, 60579], [0, 60578]))
        outcome = run(checks.check_inference_isolation, ok, inference_isolation=spec)
        self.assertOutcome(outcome, "pass")
        broker = self.system(self.guard([0, 60578, 60579], [0, 60578, 60579]))
        outcome = run(checks.check_inference_isolation, broker, inference_isolation=spec)
        self.assertOutcome(outcome, "fail", "inference_control_clients also admits UIDs 60579")
        unguarded = self.system(self.guard([0, 60578, 60579]))
        outcome = run(checks.check_inference_isolation, unguarded, inference_isolation=spec)
        self.assertOutcome(outcome, "fail", "no set inference_control_clients")
