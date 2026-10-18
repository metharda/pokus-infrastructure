"""Unit tests for the components that may exist before pinka (BUILD_PROMPT §2.2, §7.21)."""

from __future__ import annotations

import ipaddress
import json
import unittest
from typing import Any

from helpers import TempRootCase, runner

from pinka_preflight.detect import components
from pinka_preflight.runner import FixtureRunner

TAGS = ("tag:pinka-lab", "tag:pinka-qa")
POOL = [ipaddress.IPv4Network("172.30.0.0/16")]
CLASH_ARGS: dict[str, Any] = {
    "bridge_prefixes": ["incusbr-", "r-"],
    "bridge_names": ["pinka-build0"],
    "networks": POOL,
    "pool_device": None,
}
DOCKER = {
    "dockerd --version": "Docker version 29.8.2, build 0000000\n",
    "systemctl is-active docker.service": "active\n",
    "docker info --format '{{json .CDISpecDirs}}'": '["/etc/cdi","/var/run/cdi"]\n',
    "docker compose version": "Docker Compose version v5.6.0\n",
    "docker buildx version": "github.com/docker/buildx v0.37.1\n",
}


def docker_runner(**changes: Any) -> FixtureRunner:
    commands: dict[str, Any] = {**DOCKER, **changes}
    return runner(
        {k: v for k, v in commands.items() if v is not None},
        which={"dockerd": "/usr/bin/dockerd", "docker": "/usr/bin/docker"},
    )


def tailscale_runner(
    *, state: str = "Running", tags: list[str] | None = None, serve: Any = None, ssh: bool = False
) -> FixtureRunner:
    status = {
        "BackendState": state,
        "Self": {
            "HostName": "owner-desktop",
            "Tags": tags or [],
            "KeyExpiry": "2027-04-01T00:00:00Z",
        },
    }
    return runner(
        {
            "tailscale version": "1.104.1\n  tailscale commit: 0000000\n",
            "systemctl is-active tailscaled.service": "active\n",
            "tailscale status --json": json.dumps(status),
            "tailscale serve status --json": json.dumps(serve or {}),
            "tailscale debug prefs": json.dumps({"RunSSH": ssh}),
        },
        which={"tailscale": "/usr/bin/tailscale"},
    )


def record(*, added: dict[str, Any] | None = None, legacy: bool = False) -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "legacy": legacy,
            "preexisting": {"components": {}},
            "added": {"components": added or {}},
        }
    )


class DockerTest(TempRootCase):
    def test_absent(self) -> None:
        comp = components.detect_docker(self.fs(), runner(), None)
        self.assertEqual(
            comp.as_dict(), {"installed": False, "version": None, "running": False, "origin": None}
        )

    def test_pre_existing_with_cdi_and_plugins(self) -> None:
        comp = components.detect_docker(self.fs(), docker_runner(), None)
        self.assertEqual((comp.installed, comp.version, comp.running), (True, "29.8.2", True))
        self.assertEqual(comp.details, {"cdi": True, "compose": True, "buildx": True})
        self.assertEqual(comp.origin, "pre-existing")

    def test_origin_from_the_mark_and_the_record(self) -> None:
        self.put(
            "/etc/docker/daemon.json", '{"labels": ["org.pinka.managed=Managed by pinka (x)"]}'
        )
        self.assertEqual(components.detect_docker(self.fs(), docker_runner(), None).origin, "pinka")
        # A record decides: only what a role recorded as added is pinka's.
        self.put(components.INSTALL_RECORD, record())
        rec = components.read_record(self.fs())
        self.assertEqual(
            components.detect_docker(self.fs(), docker_runner(), rec).origin, "pre-existing"
        )
        self.put(components.INSTALL_RECORD, record(added={"docker": True}))
        rec = components.read_record(self.fs())
        self.assertEqual(components.detect_docker(self.fs(), docker_runner(), rec).origin, "pinka")
        # A legacy record leaves it to the marks.
        self.put(components.INSTALL_RECORD, record(legacy=True))
        rec = components.read_record(self.fs())
        self.assertEqual(components.detect_docker(self.fs(), docker_runner(), rec).origin, "pinka")

    def test_cdi_off_is_read_from_docker_info(self) -> None:
        r = docker_runner(**{"docker info --format '{{json .CDISpecDirs}}'": "null\n"})
        self.assertFalse(components.detect_docker(self.fs(), r, None).details["cdi"])

    def test_cdi_of_a_stopped_daemon_follows_its_version_and_configuration(self) -> None:
        stopped = {"systemctl is-active docker.service": ("inactive\n", 3)}
        cases = (
            ("Docker version 29.8.2, build x\n", "{}", True),
            ("Docker version 29.8.2, build x\n", '{"features": {"cdi": false}}', False),
            ("Docker version 28.1.1, build x\n", "{}", False),
            ("Docker version 27.5.1, build x\n", '{"features": {"cdi": true}}', True),
        )
        for version, daemon, cdi in cases:
            self.put("/etc/docker/daemon.json", daemon)
            r = docker_runner(**stopped, **{"dockerd --version": version})
            comp = components.detect_docker(self.fs(), r, None)
            self.assertFalse(comp.running)
            self.assertEqual(comp.details["cdi"], cdi, (version, daemon))


class TailscaleTest(TempRootCase):
    def test_absent_and_logged_out(self) -> None:
        self.assertFalse(components.detect_tailscale(runner(), None, TAGS).installed)
        comp = components.detect_tailscale(tailscale_runner(state="NeedsLogin"), None, TAGS)
        self.assertTrue(comp.installed)
        self.assertIsNone(comp.origin)
        self.assertFalse(comp.details["logged_in"])
        self.assertEqual(comp.details["tags"], [])

    def test_a_node_of_the_owner_is_pre_existing(self) -> None:
        comp = components.detect_tailscale(tailscale_runner(tags=["tag:home"]), None, TAGS)
        self.assertEqual(comp.origin, "pre-existing")
        self.assertEqual(comp.details["hostname"], "owner-desktop")
        self.assertEqual(comp.details["key_expiry"], "2027-04-01T00:00:00Z")
        self.assertFalse(comp.details["funnel"])
        self.assertFalse(comp.details["ssh"])

    def test_a_node_pinka_joined(self) -> None:
        # Without a record pinka's tag marks it; with one, the recorded join does.
        self.assertEqual(
            components.detect_tailscale(
                tailscale_runner(tags=["tag:pinka-lab"]), None, TAGS
            ).origin,
            "pinka",
        )
        joined = json.loads(record(added={"tailscale": True}))
        self.assertEqual(
            components.detect_tailscale(tailscale_runner(tags=["tag:home"]), joined, TAGS).origin,
            "pinka",
        )
        other = json.loads(record())
        self.assertEqual(
            components.detect_tailscale(
                tailscale_runner(tags=["tag:pinka-lab"]), other, TAGS
            ).origin,
            "pre-existing",
        )

    def test_funnel_and_ssh(self) -> None:
        comp = components.detect_tailscale(
            tailscale_runner(serve={"AllowFunnel": {"owner:443": True}}, ssh=True), None, TAGS
        )
        self.assertTrue(comp.details["funnel"])
        self.assertTrue(comp.details["ssh"])


class LxdPodmanIncusTest(TempRootCase):
    LXD = {
        "snap list lxd": "Name  Version  Rev  Tracking  Publisher  Notes\nlxd  5.21.3  33110  5.21/stable  canonical  -\n",
        "lxc network list --format json": json.dumps(
            [
                {"name": "lxdbr0", "managed": True, "config": {"ipv4.address": "10.150.12.1/24"}},
                {"name": "eth0", "managed": False, "config": {}},
            ]
        ),
        "lxc storage list --format json": json.dumps(
            [
                {
                    "name": "default",
                    "config": {"source": "/var/snap/lxd/common/lxd/disks/default.img"},
                }
            ]
        ),
    }

    def test_lxd(self) -> None:
        comp = components.detect_lxd(self.fs(), runner(self.LXD))
        self.assertEqual(
            (comp.installed, comp.version, comp.running, comp.origin),
            (True, "5.21.3", True, "pre-existing"),
        )
        self.assertEqual(comp.details["networks"], [{"name": "lxdbr0", "ipv4": "10.150.12.1/24"}])
        self.assertEqual(
            comp.details["storage_sources"], ["/var/snap/lxd/common/lxd/disks/default.img"]
        )
        self.assertFalse(components.detect_lxd(self.fs(), runner()).installed)

    def test_podman_and_its_docker_command(self) -> None:
        r = runner(
            {
                "podman --version": "podman version 5.4.2\n",
                "docker --version": "Emulate Docker CLI using podman.\n",
            },
            which={"podman": "/usr/bin/podman", "docker": "/usr/bin/docker"},
        )
        comp = components.detect_podman(self.fs(), r)
        self.assertEqual((comp.version, comp.origin), ("5.4.2", "pre-existing"))
        self.assertIn("podman-docker", comp.details["docker_compat"])
        self.meta(symlinks={"/var/run/docker.sock": "/run/podman/podman.sock"})
        plain = runner(
            {"podman --version": "podman version 5.4.2\n"}, which={"podman": "/usr/bin/podman"}
        )
        self.assertIn(
            "Podman's socket", components.detect_podman(self.fs(), plain).details["docker_compat"]
        )

    def test_incus_and_the_driver(self) -> None:
        r = runner({"incus --version": "6.0.6\n"}, which={"incus": "/usr/bin/incus"})
        self.assertEqual(components.detect_incus(self.fs(), r, None).origin, "pre-existing")
        self.put("/etc/pinka/managed-incus")
        self.assertEqual(components.detect_incus(self.fs(), r, None).origin, "pinka")
        driver = components.detect_nvidia_driver(None, installed=True, version="580.95.05")
        self.assertEqual((driver.origin, driver.version), ("pre-existing", "580.95.05"))
        self.assertIsNone(
            components.detect_nvidia_driver(None, installed=False, version=None).origin
        )


class ConflictsTest(TempRootCase):
    def judge(
        self, comps: components.Components, *, gpu: bool = False, **clash: Any
    ) -> tuple[list[tuple[str, str]], list[str]]:
        found, notes = components.component_conflicts(
            self.fs(), comps, gpu_usable=gpu, lxd_clash_args={**CLASH_ARGS, **clash}
        )
        return [(c.id, c.severity) for c in found], notes

    def test_nothing_installed(self) -> None:
        self.assertEqual(self.judge(components.Components()), ([], []))

    def test_a_pre_existing_docker_is_used_as_it_is(self) -> None:
        comps = components.Components(
            docker=components.detect_docker(self.fs(), docker_runner(), None)
        )
        ids, notes = self.judge(comps, gpu=True)
        self.assertEqual(ids, [])
        self.assertIn("Docker Engine 29.8.2 is installed already", notes[0])

    def test_a_docker_pinka_installed_is_not_judged(self) -> None:
        comps = components.Components(
            docker=components.Component(
                installed=True, version="29.8.2", origin="pinka", details={"cdi": False}
            )
        )
        self.assertEqual(self.judge(comps, gpu=True), ([], []))

    def test_what_a_pre_existing_docker_lacks(self) -> None:
        r = docker_runner(
            **{
                "systemctl is-active docker.service": ("inactive\n", 3),
                "docker compose version": ("", 1),
                "dockerd --version": "Docker version 27.5.1, build x\n",
            }
        )
        comps = components.Components(docker=components.detect_docker(self.fs(), r, None))
        ids, _ = self.judge(comps, gpu=True)
        self.assertEqual(
            ids,
            [
                ("docker_not_running", "needs_action"),
                ("docker_plugins_missing", "needs_action"),
                ("docker_cdi_off", "needs_action"),
            ],
        )
        found, _ = components.component_conflicts(
            self.fs(), comps, gpu_usable=True, lxd_clash_args=CLASH_ARGS
        )
        self.assertIn('add "features": {"cdi": true}', found[2].remediation)
        self.assertIn("compose plugin", found[1].message)
        # Without a GPU, CDI does not matter.
        self.assertNotIn(("docker_cdi_off", "needs_action"), self.judge(comps)[0])

    def test_cdi_switched_off_on_a_recent_docker(self) -> None:
        r = docker_runner(**{"docker info --format '{{json .CDISpecDirs}}'": "[]\n"})
        comps = components.Components(docker=components.detect_docker(self.fs(), r, None))
        found, _ = components.component_conflicts(
            self.fs(), comps, gpu_usable=True, lxd_clash_args=CLASH_ARGS
        )
        self.assertEqual([c.id for c in found], ["docker_cdi_off"])
        self.assertIn('remove "features": {"cdi": false}', found[0].remediation)

    def test_an_adopted_tailscale_node(self) -> None:
        ok = components.detect_tailscale(tailscale_runner(tags=["tag:home"]), None, TAGS)
        ids, notes = self.judge(components.Components(tailscale=ok))
        self.assertEqual(ids, [])
        self.assertIn("pinka uses the node as it is", notes[0])
        bad = components.detect_tailscale(
            tailscale_runner(serve={"AllowFunnel": {"x:443": True}}, ssh=True), None, TAGS
        )
        ids, _ = self.judge(components.Components(tailscale=bad))
        self.assertEqual(
            ids, [("tailscale_funnel_on", "needs_action"), ("tailscale_ssh_on", "needs_action")]
        )
        # A node pinka joined is checked by the tailscale role and doctor, not here.
        own = components.detect_tailscale(
            tailscale_runner(tags=["tag:pinka-lab"], ssh=True), None, TAGS
        )
        self.assertEqual(self.judge(components.Components(tailscale=own)), ([], []))

    def test_podman_providing_docker(self) -> None:
        podman = components.Component(
            installed=True, version="5.4.2", origin="pre-existing", details={"docker_compat": None}
        )
        ids, notes = self.judge(components.Components(podman=podman))
        self.assertEqual(ids, [])
        self.assertIn("Podman 5.4.2 is installed", notes[0])
        podman.details["docker_compat"] = "the docker command is Podman's (podman-docker)"
        self.assertEqual(
            self.judge(components.Components(podman=podman))[0], [("podman_docker", "needs_action")]
        )


class LxdClashTest(TempRootCase):
    def lxd(
        self, *networks: tuple[str, str | None], sources: tuple[str, ...] = ()
    ) -> components.Component:
        return components.Component(
            installed=True,
            version="5.21.3",
            running=True,
            origin="pre-existing",
            details={
                "networks": [{"name": n, "ipv4": a} for n, a in networks],
                "storage_sources": list(sources),
            },
        )

    def clashes(self, lxd: components.Component, *, docker: bool = True, **args: Any) -> list[str]:
        return components.lxd_clashes(
            self.fs(), lxd, docker_preexisting=docker, **{**CLASH_ARGS, **args}
        )

    def test_lxd_next_to_incus_without_a_clash(self) -> None:
        self.assertEqual(self.clashes(self.lxd(("lxdbr0", "10.150.12.1/24"))), [])
        self.assertEqual(self.clashes(components.Component()), [])

    def test_bridge_names_and_subnets(self) -> None:
        found = self.clashes(
            self.lxd(("pinka-build0", None), ("r-abc", None), ("lxdbr1", "172.30.4.1/24"))
        )
        self.assertEqual(len(found), 3)
        self.assertIn("LXD's network pinka-build0 has a name pinka's Incus uses", found[0])
        self.assertIn("overlaps pinka's 172.30.0.0/16", found[2])

    def test_dns_or_dhcp_on_every_address(self) -> None:
        header = "  sl  local_address rem_address   st\n"
        self.put(
            "/proc/net/udp",
            header + "   0: 00000000:0035 00000000:0000 07\n   1: 3500007F:0035 00000000:0000 07\n",
        )
        found = self.clashes(self.lxd(("lxdbr0", "10.150.12.1/24")))
        self.assertEqual(len(found), 1)
        self.assertIn("UDP port 53 of every address", found[0])

    def test_storage_and_dockers_forward_policy(self) -> None:
        lxd = self.lxd(("lxdbr0", "10.150.12.1/24"), sources=("/dev/disk/by-id/nvme-example",))
        found = self.clashes(lxd, pool_device="/dev/disk/by-id/nvme-example")
        self.assertIn("is LXD's storage", found[0])
        found = self.clashes(self.lxd(("lxdbr0", "10.150.12.1/24")), docker=False)
        self.assertIn("would stop forwarding on LXD's bridges", found[0])

    def test_a_clash_is_needs_action(self) -> None:
        comps = components.Components(lxd=self.lxd(("r-abc", None)))
        found, notes = components.component_conflicts(
            self.fs(), comps, gpu_usable=False, lxd_clash_args=CLASH_ARGS
        )
        self.assertEqual([(c.id, c.severity) for c in found], [("lxd_incus_clash", "needs_action")])
        self.assertIn("pinka never changes LXD", found[0].remediation)
        self.assertIn("LXD 5.21.3 is installed", notes[0])


class RecordTest(TempRootCase):
    def test_missing_broken_and_valid(self) -> None:
        self.assertIsNone(components.read_record(self.fs()))
        self.put(components.INSTALL_RECORD, "{not json")
        self.assertIsNone(components.read_record(self.fs()))
        self.put(components.INSTALL_RECORD, "[]")
        self.assertIsNone(components.read_record(self.fs()))
        self.put(components.INSTALL_RECORD, record())
        self.assertEqual(components.read_record(self.fs())["schema_version"], 1)  # type: ignore[index]

    def test_profile_section(self) -> None:
        comps = components.detect_components(
            self.fs(), docker_runner(), pinka_tags=TAGS, nvidia_installed=False, nvidia_version=None
        )
        data = comps.as_dict()
        self.assertFalse(data["install_record"])
        self.assertEqual(data["docker"]["origin"], "pre-existing")
        self.assertEqual(
            sorted(data),
            ["docker", "incus", "install_record", "lxd", "nvidia_driver", "podman", "tailscale"],
        )


if __name__ == "__main__":
    unittest.main()
