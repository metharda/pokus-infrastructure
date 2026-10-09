"""Tests for the doctor checks, one host situation at a time."""

from __future__ import annotations

import datetime as dt
import json
import unittest
from collections.abc import Sequence
from typing import Any

from labfakes import DIGEST, NOW, PROFILE, FakeSystem, context, info

from pinka_lab import checks, fingerprint
from pinka_lab.checks import CHECKS
from pinka_lab.doctor import Check, Outcome

PREFLIGHT = (
    "/usr/local/lib/pinka/preflight/pinka-preflight --no-write --quiet --srv-path /srv/pinka"
)
PROFILE_PATH = "/etc/pinka/host-profile.json"
BOOT_ID = "00000000-0000-0000-0000-000000000000"
NOW_TS = NOW.timestamp()


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
        system = FakeSystem({"firewall-cmd --info-zone pinka": (0, "pinka")})
        self.assertOutcome(
            run(checks.check_firewall, system, firewall__backend="firewalld"), "pass"
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
            "/etc/pinka/incus/orch/client.crt": info("potka-orch", "root", 0o600),
            "/etc/pinka/incus/orch/client.key": info("potka-orch", "root", 0o600),
        }
        trusted = {"incus query /1.0/certificates?recursion=1": (0, [{"name": "potka-orch"}])}
        self.assertOutcome(
            run(checks.check_incus_certificates, FakeSystem(trusted, infos=good)), "pass"
        )
        loose = dict(good, **{"/etc/pinka/incus/orch/client.key": info("root", "root", 0o644)})
        self.assertOutcome(
            run(checks.check_incus_certificates, FakeSystem(trusted, infos=loose)),
            "fail",
            "client.key is root 0644",
        )
        untrusted: dict[str, Any] = {"incus query /1.0/certificates?recursion=1": (0, [])}
        self.assertOutcome(
            run(checks.check_incus_certificates, FakeSystem(untrusted, infos={})),
            "fail",
            "potka-orch is not trusted",
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
            "potka-orch": ["potka-orch", "potka-svc"],
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
        svc = dict(clean, **{"potka-agent": ["potka-agent", "docker"]})
        self.assertOutcome(
            run(checks.check_accounts, FakeSystem(groups=svc)),
            "fail",
            "service account potka-agent is in docker",
        )

    def test_deployed_paths(self) -> None:
        clean = FakeSystem(dirs={"/srv/pinka/deploy/platform": [], "/srv/pinka/catalog/x": []})
        self.assertOutcome(run(checks.check_deployed_paths, clean), "pass")
        git = FakeSystem(dirs={"/srv/pinka/deploy/platform/v1/.git": []})
        self.assertOutcome(run(checks.check_deployed_paths, git), "fail", ".git exists")
        catalog = FakeSystem(
            dirs={"/srv/pinka/deploy/catalog-v1": []},
            files={
                "/srv/pinka/deploy/catalog-v1/DEPLOYED_FROM.json": json.dumps(
                    {"component": "catalog"}
                )
            },
        )
        self.assertOutcome(run(checks.check_deployed_paths, catalog), "fail", "catalog deployed in")

    def test_hardening(self) -> None:
        self.assertOutcome(run(checks.check_hardening, FakeSystem()), "skip")
        unit = {
            "unit": "potka-agent-runner.service",
            "properties": {"NoNewPrivileges": "yes"},
            "inaccessible_paths": ["/srv/pinka/catalog", "/run/pinka/vllm-deploy.sock"],
        }
        show = (
            "systemctl show potka-agent-runner.service --property=NoNewPrivileges,InaccessiblePaths"
        )
        good = FakeSystem(
            {
                show: (
                    0,
                    "NoNewPrivileges=yes\n"
                    "InaccessiblePaths=-/srv/pinka/catalog /etc/pinka -/run/pinka/vllm-deploy.sock\n",
                )
            }
        )
        self.assertOutcome(run(checks.check_hardening, good, hardening=[unit]), "pass")
        bad = FakeSystem({show: (0, "NoNewPrivileges=no\nInaccessiblePaths=\n")})
        outcome = run(checks.check_hardening, bad, hardening=[unit])
        self.assertOutcome(outcome, "fail", "NoNewPrivileges='no'")
        self.assertIn("InaccessiblePaths lacks /srv/pinka/catalog", outcome.detail)

    def test_sockets(self) -> None:
        self.assertOutcome(run(checks.check_sockets, FakeSystem()), "skip")
        entry = {
            "path": "/run/pinka/orch/api.sock",
            "owner": "potka-orch",
            "group": "pinka",
            "mode": "0660",
            "acl": ["group:potka-svc:rw-"],
        }
        getfacl = "getfacl --omit-header --absolute-names /run/pinka/orch/api.sock"
        system = FakeSystem(
            {getfacl: (0, "user::rw-\ngroup:potka-svc:rw-\n")},
            infos={"/run/pinka/orch/api.sock": info("potka-orch", "pinka", 0o660)},
        )
        self.assertOutcome(run(checks.check_sockets, system, sockets=[entry]), "pass")
        system.commands[getfacl] = (0, "user::rw-\n")
        self.assertOutcome(run(checks.check_sockets, system, sockets=[entry]), "fail", "lacks ACL")

    def test_sockets_with_a_mask(self) -> None:
        path = "/run/pinka/gw/svc.sock"
        entry = {
            "path": path,
            "owner": "potka-gw",
            "group": "potka-gw",
            "mode": "0600",
            "acl": ["user:potka-orch:rw-"],
            "expect_mode": "0660",
            "expect_acl": ["user:potka-orch:rw-", "group::---", "mask::rw-"],
        }
        getfacl = f"getfacl --omit-header --absolute-names {path}"
        acl = "user::rw-\nuser:potka-orch:rw-\ngroup::---\nmask::rw-\nother::---\n"
        system = FakeSystem({getfacl: (0, acl)}, infos={path: info("potka-gw", "potka-gw", 0o660)})
        self.assertOutcome(run(checks.check_sockets, system, sockets=[entry]), "pass")
        # A narrower mask: getfacl marks the entry's effective rights.
        system.commands[getfacl] = (
            0,
            "user::rw-\nuser:potka-orch:rw-\t#effective:r--\ngroup::---\nmask::r--\nother::---\n",
        )
        self.assertOutcome(
            run(checks.check_sockets, system, sockets=[entry]), "fail", "lacks ACL mask::rw-"
        )
        system.infos[path] = info("potka-gw", "potka-gw", 0o600)
        self.assertOutcome(
            run(checks.check_sockets, system, sockets=[entry]), "fail", "potka-gw:potka-gw 0600"
        )


def psql_answers(roles: Sequence[str], gt: dict[str, bool], settings: dict[str, str]) -> Any:
    def answer(argv: Sequence[str]) -> tuple[int, str]:
        query = argv[-1]
        if "json_agg(rolname)" in query:
            return 0, json.dumps(list(roles))
        if "has_schema_privilege" in query:
            return 0, json.dumps(gt)
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
            {
                "docker exec *": psql_answers(
                    ["potka_orch", "potka_reader"], {"potka_reader": False}, ON
                )
            }
        )
        self.assertOutcome(run(checks.check_postgres, good), "pass", "2 roles")
        argv = good.calls[0]
        self.assertEqual(argv[:5], ["docker", "exec", "--user", "postgres", "pinka-postgres"])
        bad = FakeSystem(
            {
                "docker exec *": psql_answers(
                    ["potka_orch"], {"potka_reader": True}, dict(ON, fsync="off")
                )
            }
        )
        outcome = run(checks.check_postgres, bad)
        self.assertOutcome(outcome, "fail", "role potka_reader missing")
        self.assertIn("potka_reader can use schema gt", outcome.detail)
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
        run_id = next(a["value"]["stringValue"] for a in attributes if a["key"] == "potka.run_id")
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
                f"{self.DIR}/{n}": info("root", "potka-orch", 0o640)
                for n in ("self.crt", "self.key", "peer.crt")
            }
            if files
            else {}
        )
        records = {f"{self.DIR}/peer.json": json.dumps({"node": peer})} if peer else {}
        return FakeSystem({"openssl *": (0, f"notAfter={end}\n")}, files=records, infos=infos)

    def check(self, system: FakeSystem, peer: str = "", role: str = "inference") -> Outcome:
        return run(checks.check_pairing, system, node_role=role, inference__peer=peer)

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
