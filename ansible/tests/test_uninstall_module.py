"""Tests for the pinka_uninstall module (BUILD_PROMPT §5.4).

The module runs on the target's Python, which is 3.9 in the check-mode
containers of the Red Hat family, so these tests are unittest-style and also
run on CPython 3.9 (``make test-py39``). Each test builds a host: files below
a temporary root and the commands' answers.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Union

import pinka_uninstall as mod

# The Debian family's commands (roles/uninstall/vars/debian.yml; test_uninstall.py
# checks that this copy equals the file, which plain CPython 3.9 cannot read).
DEBIAN: dict[str, Any] = {
    "list": ["dpkg-query", "-W", "-f", "${Package}\t${Version}\n"],
    "test_remove": ["dpkg", "--dry-run", "--purge"],
    "remove": ["dpkg", "--purge"],
    "remove_keeping_config": ["dpkg", "--remove"],
    "purge_deletes": {
        "incus-base": [
            "/var/lib/incus",
            "/var/cache/incus",
            "/var/log/incus",
            "/var/lib/incus-lxcfs",
        ],
        "tailscale": ["/var/lib/tailscale"],
    },
    "masked_on_remove": {"tailscale": ["tailscaled.service"]},
    "blocked": r"dependency problems prevent removal of (?P<package>[^\s:]+)",
    "provides": [],
    "unneeded": ["apt-get", "--simulate", "autoremove"],
    "unneeded_line": r"^Remv (?P<package>\S+)",
    "hold_method": "apt_preferences",
    "hold_file": "/etc/apt/preferences.d/pinka-pins",
    "holds_list": [],
    "unhold": [],
    "lock_file": "",
    "repo_globs": ["/etc/apt/sources.list.d/pinka-*", "/etc/apt/keyrings/pinka-*"],
    "rpm_keys": False,
    "initramfs": ["update-initramfs", "-u", "-k", "all"],
    "sshd": "/usr/sbin/sshd",
    "ssh_service": "ssh",
}
Answer = Union["tuple[int, str, str]", Callable[[Sequence[str]], "tuple[int, str, str]"]]


class FakeHost:
    """Files below a root and the answers of the commands; records every call."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.answers: list[tuple[tuple[str, ...], Answer]] = []
        self.calls: list[list[str]] = []
        self.commands = {"systemctl", "docker", "incus", "nft", "iptables", "tailscale", "ip"}
        self.file("etc/passwd", "root:x:0:0:root:/root:/bin/bash\n")
        self.file("etc/group", "root:x:0:\n")

    def file(self, name: str, text: str = "", mode: int = 0o644) -> Path:
        path = self.root / name.lstrip("/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        path.chmod(mode)
        return path

    def link(self, name: str, target: str) -> None:
        path = self.root / name.lstrip("/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(target)

    def answer(self, *prefix: str, rc: int = 0, out: str = "", err: str = "") -> None:
        self.answers.insert(0, (prefix, (rc, out, err)))

    def answer_with(
        self, *prefix: str, call: Callable[[Sequence[str]], tuple[int, str, str]]
    ) -> None:
        self.answers.insert(0, (prefix, call))

    def run(self, argv: Sequence[str]) -> tuple[int, str, str]:
        self.calls.append(list(argv))
        for prefix, answer in self.answers:
            if tuple(argv[: len(prefix)]) == prefix:
                return answer(argv) if callable(answer) else answer
        return 1, "", f"{argv[0]}: not answered in this test"

    def ran(self, *prefix: str) -> list[list[str]]:
        return [c for c in self.calls if tuple(c[: len(prefix)]) == prefix]


def answer_the_guard(fake: FakeHost) -> set[str]:
    """Answer the guard's bind mounts and stops; returns the paths mounted read-only now."""
    readonly: set[str] = set()

    def remount(argv: Sequence[str]) -> tuple[int, str, str]:
        readonly.add(argv[-1])
        return 0, "", ""

    def umount(argv: Sequence[str]) -> tuple[int, str, str]:
        readonly.discard(argv[-1])
        return 0, "", ""

    fake.answer("mount", "--bind", rc=0)
    fake.answer_with("mount", "-o", "remount,bind,ro", call=remount)
    fake.answer_with("umount", call=umount)
    fake.answer("systemctl", "stop", rc=0)
    return readonly


class FakeDpkg:
    """dpkg with maintainer scripts that delete data paths, as some packages' scripts do.

    ``scripts`` names, per package, what its script deletes on ``--remove``
    (and so on ``--purge`` too) and on ``--purge`` only. A path below a
    read-only mount survives, as on a host.
    """

    def __init__(
        self,
        fake: FakeHost,
        installed: set[str],
        scripts: dict[str, dict[str, list[str]]],
        readonly: set[str],
    ) -> None:
        self.fake, self.installed, self.scripts, self.readonly = fake, installed, scripts, readonly
        fake.answer_with("dpkg-query", call=self.listing)
        fake.answer("dpkg", "--dry-run", rc=0)
        fake.answer_with("dpkg", "--purge", call=self.remove)
        fake.answer_with("dpkg", "--remove", call=self.remove)
        fake.answer("apt-get", "--simulate", "autoremove", rc=0)

    def listing(self, argv: Sequence[str]) -> tuple[int, str, str]:
        return 0, "".join(f"{n}\t1\n" for n in sorted(self.installed)), ""

    def remove(self, argv: Sequence[str]) -> tuple[int, str, str]:
        purge = argv[1] == "--purge"
        for name in argv[2:]:
            script = self.scripts.get(name, {})
            for path in script.get("remove", []) + (script.get("purge", []) if purge else []):
                real = str(self.fake.root / path.lstrip("/"))
                if not any(real == r or real.startswith(r + "/") for r in self.readonly):
                    shutil.rmtree(real, ignore_errors=True)
            self.installed.discard(name)
        return 0, "", ""


def make_host(fake: FakeHost) -> mod.Host:
    host = mod.Host(fake.run, str(fake.root))
    host.has = lambda command: command in fake.commands  # type: ignore[method-assign]
    return host


def record(**added: Any) -> dict[str, Any]:
    """An install record as the install_record module writes it."""
    base: dict[str, Any] = {
        "components": {},
        "changed": {},
        "packages": [],
        "holds": [],
        "accounts": [],
        "groups": [],
        "tailscale_serve": [],
        "repo_keys": [],
        "incus_objects": [],
        "closed_access": [],
        "memberships": [],
        "keys": [],
    }
    base.update(added)
    return {
        "schema_version": 1,
        "created_at": "2026-10-01T00:00:00Z",
        "legacy": False,
        "preexisting": {
            "components": {},
            "access": {},
            "packages": {"bash": "5.2", "openssh-server": "9.6"},
            "units_enabled": ["ssh.service"],
            "units_masked": [],
            "users": ["root", "owner1"],
            "groups": {"root": [], "owner1": [], "docker": []},
            "files": {"/etc/ssh/sshd_config": "Port 22\n"},
            "file_modes": {
                "/etc/ssh/sshd_config": {"mode": "0600", "owner": "root", "group": "root"}
            },
        },
        "added": base,
    }


def params(fake: FakeHost, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "state": "plan",
        "root": str(fake.root),
        "machine": "lab1",
        "purge_data": False,
        "remove_components": [],
        "family": DEBIAN,
        "component_packages": {
            "docker": ["docker-ce", "containerd.io"],
            "tailscale": ["tailscale"],
        },
        "session": {},
        "opt_existed": True,
        "plan_file": "/run/pinka-uninstall/plan.json",
        "plan_text": "/run/pinka-uninstall/plan.txt",
        "report_file": "/var/log/pinka-uninstall/r.json",
        "results": [],
        "started_at": "2026-10-22T10:00:00Z",
        "log_file": "/var/log/pinka-bootstrap/x.log",
    }
    base.update(overrides)
    return base


def plan_of(fake: FakeHost, **overrides: Any) -> dict[str, Any]:
    ctx = mod.Context(make_host(fake), params(fake, **overrides))
    return mod.build_plan(ctx)


def named(plan: dict[str, Any], kind: str) -> dict[str, dict[str, Any]]:
    return {i["name"]: i for i in plan["items"] if i["kind"] == kind}


def full_install(fake: FakeHost) -> None:
    """A full lab pinka installed whole: its Docker, Incus and tailnet node, a platform."""
    fake.file(
        "var/lib/pinka/install-record.json",
        json.dumps(
            record(
                components={"docker": True, "incus": True, "tailscale": True},
                packages=[
                    "docker-ce",
                    "containerd.io",
                    "incus",
                    "tailscale",
                    "jq",
                    "/usr/bin/curl",
                ],
                holds=["docker-ce"],
                accounts=["pinka-build", "researcher1"],
                groups=["pinka", "pinka-admin"],
                memberships=[{"user": "owner1", "group": "pinka"}],
                keys=[{"user": "owner1", "key": "ssh-ed25519 AAAAC3Nz example-key-1"}],
                tailscale_serve=["https:443"],
                closed_access=["tcp/8080"],
                changed={"timezone": "Europe/Example", "sysctl:net.ipv4.ip_forward": "0"},
            )
        ),
        0o600,
    )
    fake.file(
        "etc/pinka/lab.json",
        json.dumps(
            {
                "pinka_root": "/srv/pinka",
                "containers": {"pinka-postgres": "docker.io/library/postgres@sha256:" + "1" * 64},
                "gpu": {"image": "docker.io/nvidia/cuda@sha256:" + "2" * 64},
                "backup": {"configured": True},
                "accounts": {"researchers": ["researcher1"]},
            }
        ),
    )
    fake.file(
        "etc/pinka/platform/active.json",
        json.dumps(
            {
                "name": "refplat",
                "names": {
                    "accounts_groups": ["refplat-api", "refplat-migrate", "refplat-db"],
                    "units": ["refplat-api"],
                    "commands": ["refplat"],
                },
            }
        ),
    )
    fake.file(
        "etc/passwd",
        "root:x:0:0:root:/root:/bin/bash\n"
        "owner1:x:1000:1000::/home/owner1:/bin/bash\n"
        "researcher1:x:1001:1001::/home/researcher1:/bin/bash\n"
        "pinka-build:x:990:990::/var/lib/pinka/pinka-build:/usr/sbin/nologin\n"
        "refplat-api:x:60578:985:service api:/var/lib/pinka/refplat-api:/usr/sbin/nologin\n",
    )
    fake.file(
        "etc/group",
        "root:x:0:\nowner1:x:1000:\nresearcher1:x:1001:\npinka:x:1002:owner1,researcher1\n"
        "pinka-admin:x:1003:researcher1\npinka-build:x:990:\nrefplat-api:x:985:\nrefplat-db:x:984:\n"
        "docker:x:983:\n",
    )
    fake.file("home/researcher1/.profile", "")
    fake.file(
        "home/owner1/.ssh/authorized_keys",
        "ssh-ed25519 AAAAC3Nz owner-key\nssh-ed25519 AAAAC3Nz example-key-1\n",
        0o600,
    )
    for unit in (
        "pinka-lab.target",
        "pinka-platform.target",
        "pinka-core.service",
        "pinka-backup.timer",
        "pinka-services.slice",
        "pinka-gpu-job@.service",
        "refplat-api.service",
    ):
        fake.file(f"etc/systemd/system/{unit}", "[Unit]\n")
    fake.link(
        "etc/systemd/system/pinka-lab.target.wants/pinka-core.service",
        "/etc/systemd/system/pinka-core.service",
    )
    fake.file("etc/systemd/system/nvidia-cdi-refresh.service.d/50-pinka.conf", "[Service]\n")
    fake.file(
        "etc/ssh/sshd_config.d/10-pinka.conf", "Match Group pinka\n  PasswordAuthentication no\n"
    )
    fake.file("etc/ssh/sshd_config", "Include /etc/ssh/sshd_config.d/*.conf\nPort 22\n")
    fake.file("etc/sudoers.d/90-pinka-admin", "%pinka-admin ALL=(ALL:ALL) ALL\n", 0o440)
    fake.file(
        "etc/apt/preferences.d/pinka-pins",
        "# BEGIN pinka docker\nPackage: docker-ce\nPin: version 29\n"
        "Pin-Priority: 1001\n# END pinka docker\n",
    )
    fake.file("etc/apt/sources.list.d/pinka-docker-deb.sources", "Types: deb\n")
    fake.file("etc/apt/keyrings/pinka-docker-deb.asc", "key\n")
    fake.file("usr/local/bin/pinka", "#!/bin/sh\n")
    fake.link("usr/local/bin/restic", "/opt/pinka/restic/0.18.0/restic")
    fake.link("usr/local/bin/refplat", "/usr/local/lib/pinka/platform/cli/refplat")
    fake.file("usr/local/lib/pinka/x.py", "")
    fake.file("var/lib/pinka/git/deploy_key.pub", "ssh-ed25519 AAAA deploy\n")
    fake.file("var/lib/pinka/preflight/x.json", "{}")
    fake.file("srv/pinka/state/postgres/PG_VERSION", "18\n")
    fake.file("opt/pinka/bin/uv", "")
    fake.file("var/log/pinka-bootstrap/x.log", "")
    fake.answer(
        "dpkg-query",
        out="bash\t5.2\nopenssh-server\t9.6\ndocker-ce\t29\ncontainerd.io\t2\nincus\t6\ntailscale\t1.9\njq\t1.7\n",
    )
    fake.answer("docker", "info", out="ID\n")
    fake.answer(
        "docker",
        "ps",
        out=json.dumps(
            {
                "Names": "pinka-postgres",
                "Labels": "com.docker.compose.project=pinka-core",
                "Image": "docker.io/library/postgres@sha256:" + "1" * 64,
            }
        )
        + "\n"
        + json.dumps({"Names": "owner-web", "Labels": "", "Image": "nginx"})
        + "\n",
    )
    fake.answer(
        "docker",
        "network",
        "ls",
        out=json.dumps(
            {"Name": "pinka-core_default", "Labels": "com.docker.compose.project=pinka-core"}
        )
        + "\n"
        + json.dumps({"Name": "bridge", "Labels": ""})
        + "\n",
    )
    fake.answer("docker", "inspect", "--format", "{{.Image}}", "owner-web", out="sha256:cafe\n")
    fake.answer_with(
        "docker",
        "image",
        "inspect",
        call=lambda argv: (
            (0, "sha256:cafe\n", "") if "cuda" in argv[-1] else (0, "sha256:beef\n", "")
        ),
    )
    incus = {
        "/1.0": {"config": {}},
        "/1.0/projects?recursion=1": [
            {"name": "default"},
            {"name": "bench"},
            {"name": "user-1001"},
        ],
        "/1.0/instances?all-projects=true&recursion=1": [
            {"name": "t1", "project": "bench"},
            {"name": "mine", "project": "default"},
        ],
        "/1.0/images?all-projects=true&recursion=1": [
            {
                "fingerprint": "a" * 64,
                "project": "default",
                "properties": {"pinka.recipe_commit": "x"},
                "aliases": [{"name": "base/1"}],
            },
            {"fingerprint": "b" * 64, "project": "default", "properties": {}, "aliases": []},
        ],
        "/1.0/storage-pools?recursion=1": [{"name": "default"}, {"name": "pinka-targets"}],
        "/1.0/storage-pools/default/volumes?all-projects=true&recursion=1": [],
        "/1.0/storage-pools/pinka-targets/volumes?all-projects=true&recursion=1": [],
        "/1.0/profiles?project=bench&recursion=1": [
            {"name": "default"},
            {"name": "target-container"},
        ],
        "/1.0/profiles?project=user-1001&recursion=1": [{"name": "default"}],
        "/1.0/networks?recursion=1": [
            {"name": "pinka-build0", "managed": True},
            {"name": "incusbr-1001", "managed": True},
            {"name": "r-abc", "managed": True},
            {"name": "eth0", "managed": False},
        ],
        "/1.0/network-acls": ["/1.0/network-acls/pinka-run-default"],
        "/1.0/certificates?recursion=1": [
            {"name": "pinka-metrics", "fingerprint": "f" * 64},
            {"name": "owner-laptop", "fingerprint": "e" * 64},
        ],
    }
    for url, answer in incus.items():
        fake.answer("incus", "query", url, out=json.dumps(answer))
    fake.answer(
        "nft",
        "list",
        "tables",
        out="table inet pinka_filter\ntable inet pinka_access_guard\ntable inet owner_table\n",
    )
    fake.answer(
        "iptables",
        "-S",
        "DOCKER-USER",
        out="-N DOCKER-USER\n"
        "-A DOCKER-USER -i r-+ -j ACCEPT\n"
        "-A DOCKER-USER -o r-+ -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT\n"
        "-A DOCKER-USER -i owner0 -j ACCEPT\n"
        "-A DOCKER-USER -j RETURN\n",
    )
    fake.answer("ip", "-o", "link", "show", rc=1, err="Device does not exist")
    fake.answer("systemctl", "is-enabled", out="disabled\n")
    fake.answer("systemctl", "is-enabled", "docker.service", out="enabled\n")
    fake.answer("systemctl", "is-enabled", "ssh.service", out="enabled\n")


class PlanTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.fake = FakeHost(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def full_plan(self) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        full_install(self.fake)
        plan = plan_of(self.fake)
        return plan, plan["items"]

    def test_the_plan_of_a_full_install_follows_the_removal_order(self) -> None:
        plan, items = self.full_plan()
        phases = [mod.PHASES.index(i["phase"]) for i in items]
        self.assertEqual(phases, sorted(phases))
        self.assertEqual(items[0]["name"], "pinka stop")
        self.assertIn("drain the platform refplat", items[0]["reason"])
        units = [i["name"] for i in items if i["kind"] == "unit"]
        self.assertEqual(units[:2], ["pinka-lab.target", "pinka-platform.target"])
        self.assertEqual(units[-1], "pinka-services.slice")
        self.assertIn("refplat-api.service", units)
        self.assertTrue(named(plan, "unit")["pinka-gpu-job@.service"]["template"])

    def test_docker_keeps_what_another_container_uses(self) -> None:
        plan, _items = self.full_plan()
        # Docker: pinka's container, network and images; an image another
        # container uses stays.
        self.assertEqual(list(named(plan, "container")), ["pinka-postgres"])
        self.assertIn("pinka-core_default", named(plan, "network"))
        images = named(plan, "image")
        cuda = next(i for n, i in images.items() if "cuda" in n)
        self.assertEqual(cuda["action"], "keep")
        self.assertIn("owner-web", cuda["reason"])

    def test_incus_objects_go_in_order_the_pool_last(self) -> None:
        _plan, items = self.full_plan()
        # Incus: pinka installed it, so its personal projects go too; the
        # owner's certificate and the default pool stay; the pool goes last.
        incus = [i for i in items if i["phase"] == "incus"]
        self.assertEqual(incus[0]["name"], "bench/t1")
        self.assertEqual(incus[-1]["name"], "pinka-targets")
        removed = {i["name"] for i in incus if i["action"] == "remove"}
        self.assertTrue(
            {"bench", "user-1001", "bench/target-container", "pinka-metrics"} <= removed
        )
        self.assertFalse({"default", "owner-laptop", "default/mine"} & removed)
        networks = [i["name"] for i in incus if i["kind"] == "network"]
        self.assertEqual(networks[0], "r-abc")

    def test_the_firewall_goes_back_to_how_it_was(self) -> None:
        plan, _items = self.full_plan()
        # Firewall: the access guard reopens what was closed with consent.
        tables = named(plan, "nft_table")
        self.assertEqual(set(tables), {"inet pinka_filter", "inet pinka_access_guard"})
        # The listeners the record says were closed with consent are named.
        self.assertEqual(
            tables["inet pinka_access_guard"]["reason"],
            "the access guard: tcp/8080, closed with consent, reachable again",
        )
        rules = named(plan, "docker_user_rule")
        self.assertEqual(len(rules), 2)
        # Tailscale: pinka joined the node.
        self.assertIn("https:443", named(plan, "serve"))
        self.assertIn("the tailnet node", named(plan, "logout"))

    def test_the_guard_names_the_install_path(self) -> None:
        recorded = type("Recorded", (), {})()
        recorded.added = {"closed_access": ["install_path", "tcp/8080"]}
        self.assertEqual(
            mod.guard_reason(recorded),
            "the access guard: the install's access path, tcp/8080, closed with consent,"
            " reachable again",
        )

    def test_packages_go_after_their_holds_and_repositories(self) -> None:
        plan, _items = self.full_plan()
        # Packages: the hold, the repository, the packages, then the dependencies.
        packages = named(plan, "package")
        self.assertEqual(packages["/usr/bin/curl"]["action"], "keep")
        self.assertEqual(
            {n for n, i in packages.items() if i["action"] == "remove"},
            {"containerd.io", "docker-ce", "incus", "jq", "tailscale"},
        )
        self.assertIn("/etc/apt/preferences.d/pinka-pins", named(plan, "hold_file"))
        self.assertTrue(
            named(plan, "hold_file")["/etc/apt/preferences.d/pinka-pins"]["delete_if_empty"]
        )
        self.assertEqual(
            set(named(plan, "repo_file")),
            {
                "/etc/apt/sources.list.d/pinka-docker-deb.sources",
                "/etc/apt/keyrings/pinka-docker-deb.asc",
            },
        )
        self.assertIn("dependencies of pinka's packages", named(plan, "dependencies"))

    def test_accounts_and_what_pinka_added_to_the_owners(self) -> None:
        plan, _items = self.full_plan()
        # Accounts: what pinka added to the owner's account, the platform's,
        # pinka's; the researcher's home stays; docker's group goes with Docker.
        self.assertEqual(named(plan, "membership")["owner1 in pinka"]["action"], "remove")
        self.assertEqual(len(named(plan, "key")), 1)
        accounts = named(plan, "account")
        self.assertEqual(set(accounts), {"pinka-build", "refplat-api", "researcher1"})
        self.assertIn("/home/researcher1 is kept", accounts["researcher1"]["reason"])
        self.assertFalse(accounts["researcher1"]["remove_home"])
        groups = named(plan, "group")
        self.assertTrue(
            {"pinka", "pinka-admin", "refplat-api", "refplat-db", "pinka-build", "researcher1"}
            <= set(groups)
        )
        self.assertNotIn("docker", groups)  # it existed before pinka

    def test_drop_ins_and_settings_come_back(self) -> None:
        plan, _items = self.full_plan()
        # Drop-ins: the SSH Match block's file goes, the Include line pinka
        # added goes with the original mode, the settings come back.
        files = named(plan, "file")
        self.assertEqual(files["/etc/ssh/sshd_config.d/10-pinka.conf"]["action"], "remove")
        self.assertEqual(files["/etc/ssh/sshd_config.d/10-pinka.conf"]["reload"], "sshd")
        self.assertEqual(files["/etc/sudoers.d/90-pinka-admin"]["reload"], "sudoers")
        self.assertIn("/etc/systemd/system/nvidia-cdi-refresh.service.d/50-pinka.conf", files)
        edited = named(plan, "edited_line")["/etc/ssh/sshd_config"]
        self.assertEqual((edited["action"], edited["mode"]), ("restore", "0600"))
        self.assertEqual(named(plan, "timezone")["the time zone"]["value"], "Europe/Example")
        self.assertEqual(named(plan, "sysctl")["net.ipv4.ip_forward"]["value"], "0")
        states = named(plan, "unit_state")
        self.assertEqual(states["docker.service"]["action"], "restore")
        self.assertNotIn("ssh.service", states)  # enabled before pinka

    def test_paths_go_last_the_data_stays(self) -> None:
        plan, items = self.full_plan()
        # Paths: the record last, the data and the logs kept, /opt/pinka to bootstrap.
        paths = [i for i in items if i["phase"] == "paths"]
        self.assertEqual([p["kind"] for p in paths[-2:]], ["record", "tooling"])
        self.assertEqual(paths[-1]["reason"], mod.PENDING_REASON)
        self.assertIn("/usr/local/bin/restic", named(plan, "path"))
        self.assertIn("/usr/local/bin/refplat", named(plan, "path"))
        self.assertEqual(named(plan, "data")["/srv/pinka"]["action"], "keep")
        self.assertEqual(named(plan, "log")["/var/log/pinka-bootstrap"]["action"], "keep")
        self.assertEqual(
            set(named(plan, "remote")), {"the restic repository", "the platform's deploy key"}
        )
        self.assertTrue(plan["backup"])
        self.assertEqual(plan["record"], "present")
        self.assertEqual(plan["refusal"], "")
        self.assertEqual(plan["to_remove"], sum(1 for i in items if i["action"] != "keep"))
        text = mod.plan_text(plan)
        self.assertTrue(
            text.startswith(
                "Uninstall plan for lab1 (install record: present; the lab's data: kept)"
            )
        )
        self.assertIn("\nKept:\n", text)
        self.assertIn("firewall: firewall\n", text)

    def test_components_that_existed_before_pinka_stay(self) -> None:
        full_install(self.fake)
        rec = record(
            incus_objects=[
                "project:bench",
                "network:pinka-build0",
                "pool:pinka-targets",
                "certificate:pinka-metrics",
            ],
            changed={"incus:config:core.https_address": "192.0.2.10:8443"},
            packages=["jq"],
        )
        self.fake.file("var/lib/pinka/install-record.json", json.dumps(rec))
        plan = plan_of(self.fake)
        components = named(plan, "component")
        self.assertEqual(components["Docker Engine"]["action"], "keep")
        self.assertIn("logged in before pinka", components["Tailscale"]["reason"])
        self.assertEqual(components["Incus"]["action"], "keep")
        self.assertNotIn("the tailnet node", named(plan, "logout"))
        # Only what the record names, the run networks, and pinka's images.
        removed = {
            i["name"] for i in plan["items"] if i["phase"] == "incus" and i["action"] == "remove"
        }
        self.assertTrue(
            {"bench", "pinka-build0", "r-abc", "pinka-targets", "pinka-metrics"} <= removed
        )
        self.assertFalse({"user-1001", "incusbr-1001"} & removed)
        config = named(plan, "incus_config")["core.https_address"]
        self.assertEqual((config["action"], config["value"]), ("restore", "192.0.2.10:8443"))
        self.assertNotIn("docker-ce", named(plan, "package"))
        self.assertNotIn("/etc/docker/daemon.json", named(plan, "file"))

    def test_without_a_record_only_pinka_names_go(self) -> None:
        full_install(self.fake)
        (self.fake.root / "var/lib/pinka/install-record.json").unlink()
        self.fake.file("etc/docker/daemon.json", "{}\n")
        plan = plan_of(self.fake)
        self.assertEqual(plan["record"], "missing")
        components = named(plan, "component")
        self.assertIn("--remove-component docker", components["Docker Engine"]["reason"])
        self.assertIn("origin unknown", components["Tailscale"]["reason"])
        self.assertEqual(named(plan, "package"), {})
        self.assertEqual(named(plan, "dependencies"), {})
        # Reserved names go; incus-user's names stay.
        incus = {i["name"]: i["action"] for i in plan["items"] if i["phase"] == "incus"}
        self.assertEqual(incus["bench"], "remove")
        self.assertEqual(incus["user-1001"], "keep")
        self.assertEqual(incus["incusbr-1001"], "keep")
        accounts = named(plan, "account")
        self.assertEqual(accounts["pinka-build"]["action"], "remove")
        self.assertEqual(accounts["researcher1"]["action"], "keep")
        self.assertEqual(named(plan, "edited_line")["/etc/ssh/sshd_config"]["action"], "keep")
        self.assertNotIn("/etc/docker/daemon.json", named(plan, "file"))
        # --remove-component docker: its packages go, from every source group.
        plan = plan_of(self.fake, remove_components=["docker"])
        self.assertEqual(set(named(plan, "package")), {"docker-ce", "containerd.io"})
        self.assertEqual(named(plan, "file")["/etc/docker/daemon.json"]["action"], "keep")
        self.assertEqual(plan["refusal"], "")

    def test_remove_component_is_refused_where_the_record_decides(self) -> None:
        full_install(self.fake)
        plan = plan_of(self.fake, remove_components=["docker"])
        self.assertIn("applies only on a host without a usable install record", plan["refusal"])
        self.assertIn("Refused: ", mod.plan_text(plan))

    def test_a_legacy_record_decides_nothing(self) -> None:
        full_install(self.fake)
        rec = record(components={"docker": True}, packages=["docker-ce"])
        rec["legacy"] = True
        self.fake.file("var/lib/pinka/install-record.json", json.dumps(rec))
        plan = plan_of(self.fake)
        self.assertEqual(plan["record"], "legacy")
        self.assertEqual(named(plan, "package"), {})

    def test_purge_data_removes_the_data_and_the_researchers_homes(self) -> None:
        full_install(self.fake)
        plan = plan_of(self.fake, purge_data=True)
        self.fake.file("var/lib/docker/volumes/x", "")
        plan = plan_of(self.fake, purge_data=True)
        self.assertEqual(named(plan, "data")["/srv/pinka"]["action"], "remove")
        self.assertEqual(named(plan, "data")["/var/lib/docker"]["action"], "remove")
        self.assertTrue(named(plan, "account")["researcher1"]["remove_home"])
        self.assertIn("removed (--purge-data)", mod.plan_text(plan))

    def test_warnings_for_the_session_and_the_reboot(self) -> None:
        full_install(self.fake)
        plan = plan_of(self.fake, session={"via": "ssh", "tailnet": True})
        self.assertTrue(any("logs the node out" in w for w in plan["warnings"]))

    def test_a_host_without_pinka_has_nothing_to_remove_but_the_tooling(self) -> None:
        self.fake.commands = {"systemctl"}
        plan = plan_of(self.fake, opt_existed=False)
        self.assertEqual(plan["to_remove"], 0)
        self.assertEqual([i["kind"] for i in plan["items"]], ["tooling"])


class ApplyTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.fake = FakeHost(Path(self._tmp.name))
        full_install(self.fake)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def apply(
        self, phase: str, kinds: Sequence[str] = (), **overrides: Any
    ) -> dict[str, dict[str, Any]]:
        p = params(self.fake, **overrides)
        mod.run_module(p, run=self.fake.run)
        result = mod.run_module({**p, "state": "apply", "phase": phase}, run=self.fake.run)
        return {r["name"]: r for r in result["results"] if not kinds or r["kind"] in kinds}

    def test_units_go_with_their_links_and_a_second_run_finds_them_gone(self) -> None:
        self.fake.answer("systemctl", rc=0)
        results = self.apply("units")
        self.assertEqual(results["pinka-core.service"]["result"], "removed")
        self.assertFalse((self.fake.root / "etc/systemd/system/pinka-core.service").exists())
        self.assertFalse(
            (
                self.fake.root / "etc/systemd/system/pinka-lab.target.wants/pinka-core.service"
            ).is_symlink()
        )
        self.assertTrue(self.fake.ran("systemctl", "stop", "pinka-gpu-job@*.service"))
        self.assertTrue(self.fake.ran("systemctl", "daemon-reload"))
        again = mod.run_module(
            {**params(self.fake), "state": "apply", "phase": "units"}, run=self.fake.run
        )
        self.assertEqual({r["result"] for r in again["results"]}, {"already_gone"})

    def test_drop_ins_are_restored_and_put_back_when_sshd_refuses(self) -> None:
        self.fake.answer("systemctl", rc=0)
        self.fake.answer(
            "/usr/sbin/sshd",
            "-t",
            rc=255,
            err="/etc/ssh/sshd_config line 3: Bad configuration option",
        )
        self.fake.answer("visudo", rc=0)
        self.fake.answer("timedatectl", rc=0)
        self.fake.answer("sysctl", rc=0)
        results = self.apply("dropins")
        sshd = self.fake.root / "etc/ssh/sshd_config"
        # sshd -t failed: both SSH files are as they were, the item failed.
        self.assertEqual(results["/etc/ssh/sshd_config"]["result"], "failed")
        self.assertIn("put back as it was", results["/etc/ssh/sshd_config"]["reason"])
        self.assertEqual(sshd.read_text(), "Include /etc/ssh/sshd_config.d/*.conf\nPort 22\n")
        self.assertTrue((self.fake.root / "etc/ssh/sshd_config.d/10-pinka.conf").exists())
        self.assertEqual(results["/etc/sudoers.d/90-pinka-admin"]["result"], "removed")
        self.assertEqual(results["the time zone"]["result"], "restored")
        self.assertIn(["sysctl", "-w", "net.ipv4.ip_forward=0"], self.fake.calls)
        # sshd accepts it now: the line pinka added goes, with the mode before pinka.
        self.fake.answer("/usr/sbin/sshd", "-t", rc=0)
        results = self.apply("dropins")
        self.assertEqual(results["/etc/ssh/sshd_config"]["result"], "restored")
        self.assertEqual(sshd.read_text(), "Port 22\n")
        self.assertEqual(sshd.stat().st_mode & 0o777, 0o600)
        self.assertFalse((self.fake.root / "etc/ssh/sshd_config.d/10-pinka.conf").exists())

    def test_packages_never_take_one_another_package_needs(self) -> None:
        installed = {
            "docker-ce",
            "containerd.io",
            "incus",
            "tailscale",
            "jq",
            "bash",
            "openssh-server",
            "libfoo",
        }

        def listing(argv: Sequence[str]) -> tuple[int, str, str]:
            return 0, "".join(f"{n}\t1\n" for n in sorted(installed)), ""

        def test_remove(argv: Sequence[str]) -> tuple[int, str, str]:
            if "jq" in argv:
                return (
                    1,
                    "",
                    "dpkg: dependency problems prevent removal of jq:\n"
                    " owner-tool depends on jq.\n",
                )
            return 0, "", ""

        def remove(argv: Sequence[str]) -> tuple[int, str, str]:
            installed.difference_update(argv[2:])
            return 0, "", ""

        def autoremove(argv: Sequence[str]) -> tuple[int, str, str]:
            names = ["libfoo", "bash"] if "libfoo" in installed else []
            return 0, "".join(f"Remv {n} [1]\n" for n in names), ""

        self.fake.answer_with("dpkg-query", call=listing)
        self.fake.answer_with("dpkg", "--dry-run", "--purge", call=test_remove)
        self.fake.answer_with("dpkg", "--purge", call=remove)
        self.fake.answer_with("apt-get", "--simulate", "autoremove", call=autoremove)
        answer_the_guard(self.fake)
        results = self.apply("packages")
        self.assertEqual(results["jq"]["result"], "kept")
        self.assertEqual(results["docker-ce"]["result"], "removed")
        self.assertEqual(results["/usr/bin/curl"]["result"], "kept")
        # libfoo came with pinka's packages; bash was there before pinka.
        self.assertEqual(results["dependencies of pinka's packages"]["result"], "removed")
        self.assertIn("libfoo", results["dependencies of pinka's packages"]["reason"])
        self.assertIn("bash", installed)
        self.assertIn("jq", installed)
        self.assertFalse((self.fake.root / "etc/apt/preferences.d/pinka-pins").exists())
        self.assertFalse(
            (self.fake.root / "etc/apt/sources.list.d/pinka-docker-deb.sources").exists()
        )

    def test_accounts_report_a_logged_in_account(self) -> None:
        self.fake.answer("gpasswd", rc=0)
        self.fake.answer("userdel", rc=0)
        self.fake.answer(
            "userdel",
            "researcher1",
            rc=8,
            err="userdel: user researcher1 is currently used by process 4242",
        )
        self.fake.answer("groupdel", rc=0)
        results = self.apply("accounts", kinds=("account", "membership"))
        self.assertEqual(results["researcher1"]["result"], "failed")
        self.assertIn("logged in", results["researcher1"]["reason"])
        self.assertEqual(results["pinka-build"]["result"], "removed")
        self.assertIn(["gpasswd", "-d", "owner1", "pinka"], self.fake.calls)
        keys = (self.fake.root / "home/owner1/.ssh/authorized_keys").read_text()
        self.assertEqual(keys, "ssh-ed25519 AAAAC3Nz owner-key\n")
        self.assertTrue((self.fake.root / "home/researcher1").exists())

    def test_paths_keep_the_record_to_the_end_and_leave_the_tooling_to_bootstrap(self) -> None:
        results = self.apply("paths")
        self.assertEqual(results["/var/lib/pinka"]["result"], "removed")
        self.assertEqual(results["/var/lib/pinka/install-record.json"]["result"], "removed")
        self.assertFalse((self.fake.root / "var/lib/pinka").exists())
        self.assertFalse((self.fake.root / "etc/pinka").exists())
        self.assertFalse((self.fake.root / "usr/local/bin/restic").is_symlink())
        self.assertEqual(results["/srv/pinka"]["result"], "kept")
        self.assertTrue((self.fake.root / "srv/pinka/state/postgres/PG_VERSION").exists())
        self.assertEqual(results["/opt/pinka"]["result"], "pending")
        self.assertTrue((self.fake.root / "opt/pinka/bin/uv").exists())

    def test_an_item_that_raises_fails_alone(self) -> None:
        self.fake.answer(
            "docker", "rm", rc=1, err="Error response from daemon: device or resource busy"
        )
        self.fake.answer(
            "docker", "network", "rm", rc=1, err="Error: No such network: pinka-core_default"
        )
        self.fake.answer("docker", "image", "rm", rc=0)
        results = self.apply("compose")
        self.assertEqual(results["pinka-postgres"]["result"], "failed")
        self.assertIn("device or resource busy", results["pinka-postgres"]["reason"])
        self.assertEqual(results["pinka-core_default"]["result"], "already_gone")


# Data a package script could delete, with the script that would (owner, D2 review).
DATA = {
    "/var/lib/docker/volumes/owner-db/_data/PG_VERSION": "18\n",
    "/var/lib/containerd/io.containerd.content.v1.content/blob": "x",
    "/var/lib/tailscale/tailscaled.state": "{}",
    "/var/lib/incus/database/global/db.bin": "x",
    "/srv/pinka/models/m1/weights.bin": "x",
}
SCRIPTS = {
    # The worst case: Docker's scripts delete nothing, these would on remove.
    "docker-ce": {"remove": ["/var/lib/docker"]},
    "containerd.io": {"remove": ["/var/lib/containerd"]},
    # As the packages' own postrm does on purge (Tailscale's, Zabbly's incus-base).
    "tailscale": {"purge": ["/var/lib/tailscale"]},
    "incus-base": {"purge": ["/var/lib/incus", "/var/cache/incus"]},
    # A broken script that deletes the research space.
    "jq": {"remove": ["/srv/pinka"]},
}


class KeptDataTest(unittest.TestCase):
    """Without --purge-data nothing kept is lost: not to a package script, not with a path."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.fake = FakeHost(Path(self._tmp.name))
        full_install(self.fake)
        rec_path = self.fake.root / "var/lib/pinka/install-record.json"
        rec = json.loads(rec_path.read_text())
        rec["added"]["packages"].append("incus-base")
        rec_path.write_text(json.dumps(rec))
        for name, text in DATA.items():
            self.fake.file(name, text)
        self.installed = {"bash", "openssh-server", *SCRIPTS, "incus"}

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def apply(self, phase: str, **overrides: Any) -> dict[str, dict[str, Any]]:
        p = params(self.fake, **overrides)
        mod.run_module(p, run=self.fake.run)
        result = mod.run_module({**p, "state": "apply", "phase": phase}, run=self.fake.run)
        return {r["name"]: r for r in result["results"]}

    def surviving(self) -> set[str]:
        return {name for name in DATA if (self.fake.root / name.lstrip("/")).exists()}

    def test_no_package_script_deletes_kept_data(self) -> None:
        readonly = answer_the_guard(self.fake)
        FakeDpkg(self.fake, self.installed, SCRIPTS, readonly)
        self.fake.answer("systemctl", "is-enabled", "tailscaled.service", out="masked\n")
        self.fake.answer("systemctl", "unmask", rc=0)
        plan = plan_of(self.fake)
        guard = named(plan, "guard")["the kept data"]
        self.assertEqual(
            guard["paths"],
            [
                "/srv/pinka",
                "/var/lib/pinka-kept",
                "/var/log/pinka-bootstrap",
                "/var/log/pinka-uninstall",
                "/var/lib/docker",
                "/var/lib/containerd",
                "/var/lib/incus",
                "/var/lib/tailscale",
            ],
        )
        results = self.apply("packages")
        self.assertEqual(self.surviving(), set(DATA))
        self.assertEqual(results["the kept data"]["result"], "kept")
        self.assertIn("/var/lib/docker", results["the kept data"]["reason"])
        for name in ("docker-ce", "containerd.io", "jq", "tailscale", "incus-base"):
            self.assertEqual(results[name]["result"], "removed", name)
        # Removed, not purged: their purge deletes the data and the configuration.
        self.assertIn("removed, not purged", results["tailscale"]["reason"])
        removed_keeping = [c for c in self.fake.ran("dpkg", "--remove")]
        self.assertEqual(removed_keeping, [["dpkg", "--remove", "incus-base", "tailscale"]])
        purged = {n for c in self.fake.ran("dpkg", "--purge") for n in c[2:]}
        self.assertFalse({"tailscale", "incus-base"} & purged)
        # Tailscale's removal masks tailscaled; uninstall unmasks it.
        self.assertIn(["systemctl", "unmask", "tailscaled.service"], self.fake.calls)
        # The services stop first, the guard is up while packages go, then released.
        calls = self.fake.calls
        stop = calls.index(["systemctl", "stop", *mod.NAMES["component_units"]["docker"]])
        first_mount = next(n for n, c in enumerate(calls) if c[:1] == ["mount"])
        first_removal = next(n for n, c in enumerate(calls) if c[:2] == ["dpkg", "--purge"])
        last_umount = max(n for n, c in enumerate(calls) if c[:1] == ["umount"])
        self.assertLess(stop, first_mount)
        self.assertLess(first_mount, first_removal)
        self.assertLess(first_removal, last_umount)
        self.assertEqual(readonly, set())
        self.assertFalse((self.fake.root / "run/pinka-uninstall-guards").exists())

    def test_without_the_guard_no_package_goes(self) -> None:
        FakeDpkg(self.fake, self.installed, SCRIPTS, set())
        self.fake.answer("systemctl", "stop", rc=0)
        self.fake.answer("mount", rc=32, err="mount: permission denied")
        results = self.apply("packages")
        self.assertEqual(self.surviving(), set(DATA))
        self.assertEqual(self.fake.ran("dpkg", "--purge") + self.fake.ran("dpkg", "--remove"), [])
        self.assertEqual(results["the kept data"]["result"], "failed")
        self.assertIn("permission denied", results["the kept data"]["reason"])
        self.assertEqual(results["docker-ce"]["result"], "failed")
        self.assertIn("could not be guarded", results["docker-ce"]["reason"])
        self.assertEqual(results["dependencies of pinka's packages"]["result"], "failed")

    def test_purge_data_purges_without_a_guard(self) -> None:
        # The control: unguarded, the fake scripts do delete.
        FakeDpkg(self.fake, self.installed, SCRIPTS, set())
        self.fake.answer("systemctl", rc=0)
        plan = plan_of(self.fake, purge_data=True)
        self.assertNotIn("the kept data", named(plan, "guard"))
        results = self.apply("packages", purge_data=True)
        self.assertEqual(self.fake.ran("mount"), [])
        self.assertEqual(self.fake.ran("dpkg", "--remove"), [])
        self.assertEqual(results["tailscale"]["result"], "removed")
        self.assertEqual(self.surviving(), set())

    def test_dockers_data_root_is_where_docker_says(self) -> None:
        self.fake.answer(
            "docker", "info", "--format", "{{.DockerRootDir}}", out="/srv/docker-data\n"
        )
        self.fake.file("srv/docker-data/volumes/v/_data/x", "x")
        plan = plan_of(self.fake)
        guard = named(plan, "guard")["the kept data"]
        self.assertIn("/srv/docker-data", guard["paths"])
        self.assertNotIn("/var/lib/docker", guard["paths"])
        self.assertEqual(named(plan, "data")["/srv/docker-data"]["action"], "keep")

    def backup_files(self) -> None:
        self.fake.file(
            "etc/pinka/secrets/backup/repository", "sftp:backup@backup.example.com:/r\n", 0o600
        )
        self.fake.file("etc/pinka/secrets/backup/password", "example-password\n", 0o600)
        self.fake.file("etc/pinka/secrets/backup/ssh_key", "key\n", 0o600)
        self.fake.file("etc/pinka/secrets/vault-pass", "example-vault-password\n", 0o600)
        self.fake.file("etc/pinka/backup/known_hosts", "backup.example.com ssh-ed25519 AAAA\n")
        self.fake.file("var/lib/pinka/backup/last.json", "{}")
        self.fake.file("opt/pinka/restic/0.18.0/restic", "#!/bin/sh\n", 0o755)
        (self.fake.root / "usr/local/bin/restic").unlink()
        self.fake.link(
            "usr/local/bin/restic", str(self.fake.root / "opt/pinka/restic/0.18.0/restic")
        )
        self.fake.file("etc/pinka/qa/trusted-keys/qa1.pem", "pem\n")
        self.fake.file("etc/pinka/qa/attestations/a1.json", "{}")
        self.fake.file("var/lib/pinka/gpu/runs/r1.json", "{}")

    def test_the_backups_stay_usable_after_the_paths_go(self) -> None:
        self.backup_files()
        plan = plan_of(self.fake)
        kept = "/var/lib/pinka-kept"
        self.assertEqual(plan["backup_access"], f"{kept}/backup/secrets")
        self.assertEqual(
            set(named(plan, "keep_copy")),
            {
                "/etc/pinka/secrets/backup",
                "/etc/pinka/backup",
                "/var/lib/pinka/backup",
                "/usr/local/bin/restic",
                "/etc/pinka/secrets/vault-pass",
                "/etc/pinka/qa",
                "/var/lib/pinka/gpu",
            },
        )
        text = mod.plan_text(plan)
        self.assertIn(
            f"The kept backups open with the key and settings in {kept}/backup/secrets", text
        )
        # The first thing to do afterwards: take the kept secrets off the host.
        first = plan["follow_ups"][0]
        self.assertTrue(
            first.startswith(
                f"{kept} holds the restic repository's password and location and the SFTP key "
                "of the backup server and the vault password, which opens the vault with the "
                "repository's password: they grant access to the backups."
            ),
            first,
        )
        self.assertIn("tar -C /var/lib -cf - pinka-kept | ssh <you>@<safe-host>", first)
        self.assertIn(f"find {kept} -type f -exec shred -u {{}} + && rm -rf {kept}", first)
        self.assertIn("\nAfterwards:\n  - " + first, text)
        results = self.apply("keep")
        self.assertEqual({r["result"] for r in results.values()}, {"kept"})
        root = self.fake.root / kept.lstrip("/")
        self.assertEqual(root.stat().st_mode & 0o777, 0o700)
        self.assertEqual((root / "backup/secrets/password").read_text(), "example-password\n")
        self.assertEqual((root / "backup/secrets/password").stat().st_mode & 0o777, 0o600)
        self.assertEqual((root / "backup/restic").read_text(), "#!/bin/sh\n")
        self.assertFalse((root / "backup/restic").is_symlink())
        self.assertTrue((root / "vault-pass").exists())
        self.assertTrue((root / "qa/attestations/a1.json").exists())
        readme = (root / "README.txt").read_text()
        self.assertIn(f"RESTIC_PASSWORD_FILE={kept}/backup/secrets/password", readme)
        self.assertIn("This directory grants access to the backups", readme)
        self.assertIn(f"find {kept} -type f -exec shred -u {{}} + && rm -rf {kept}", readme)
        self.assertIn(f"UserKnownHostsFile={kept}/backup/config/known_hosts", readme)
        self.assertEqual((root / "README.txt").stat().st_mode & 0o777, 0o600)
        # Then the paths go; the kept directory and the research space stay.
        paths = self.apply("paths")
        self.assertEqual(paths["/etc/pinka"]["result"], "removed")
        self.assertEqual(paths[kept]["result"], "kept")
        self.assertTrue((root / "backup/secrets/repository").exists())
        report = mod.build_report({"results": list(paths.values())}, plan)
        self.assertEqual(report["backup_access"], f"{kept}/backup/secrets")
        self.assertEqual(report["follow_ups"][0], first)
        # A later run still says so while the copy is on the host.
        self.assertEqual(plan_of(self.fake)["follow_ups"][0], first)

    def test_a_missing_kept_copy_keeps_its_path(self) -> None:
        self.backup_files()
        results = self.apply("paths")  # the keep phase never ran
        self.assertEqual(results["/etc/pinka"]["result"], "failed")
        self.assertIn("/etc/pinka/secrets/backup", results["/etc/pinka"]["reason"])
        self.assertTrue((self.fake.root / "etc/pinka/secrets/backup/password").exists())
        self.assertEqual(results["/var/lib/pinka"]["result"], "failed")

    def test_purge_data_keeps_the_backups_access_only(self) -> None:
        self.backup_files()
        plan = plan_of(self.fake, purge_data=True)
        self.assertEqual(
            set(named(plan, "keep_copy")),
            {
                "/etc/pinka/secrets/backup",
                "/etc/pinka/backup",
                "/var/lib/pinka/backup",
                "/usr/local/bin/restic",
                "/etc/pinka/secrets/vault-pass",
            },
        )
        self.assertEqual(named(plan, "kept")["/var/lib/pinka-kept"]["action"], "keep")
        self.assertIn("grant access to the backups", plan["follow_ups"][0])

    def researcher_instance(self) -> None:
        self.fake.answer(
            "incus",
            "query",
            "/1.0/instances?all-projects=true&recursion=1",
            out=json.dumps(
                [
                    {"name": "t1", "project": "bench"},
                    {"name": "thesis", "project": "user-1001"},
                ]
            ),
        )

    def test_incus_stays_with_the_researchers_work(self) -> None:
        self.researcher_instance()
        FakeDpkg(self.fake, self.installed, {}, set())
        self.fake.file("etc/apt/sources.list.d/pinka-zabbly-incus-lts.sources", "Types: deb\n")
        packages = {
            **params(self.fake)["component_packages"],
            "incus": ["incus", "incus-base", "incus-client"],
        }
        plan = plan_of(
            self.fake,
            component_packages=packages,
            component_repos={"incus": ["zabbly-incus-lts"], "docker": ["docker-deb"]},
        )
        self.assertIn("user-1001", plan["keep_for_data"]["incus"])
        incus = {i["name"]: i for i in plan["items"] if i["phase"] == "incus"}
        self.assertEqual(incus["user-1001"]["action"], "keep")
        self.assertIn("1 instance(s)", incus["user-1001"]["reason"])
        self.assertEqual(incus["incusbr-1001"]["action"], "keep")
        self.assertEqual(incus["pinka-targets"]["action"], "keep")
        self.assertEqual(incus["Incus"]["action"], "keep")
        self.assertEqual(incus["bench"]["action"], "remove")
        pkgs = named(plan, "package")
        self.assertEqual(pkgs["incus"]["action"], "keep")
        self.assertEqual(pkgs["incus-base"]["action"], "keep")
        self.assertEqual(pkgs["docker-ce"]["action"], "remove")
        repos = named(plan, "repo_file")
        self.assertEqual(
            repos["/etc/apt/sources.list.d/pinka-zabbly-incus-lts.sources"]["action"], "keep"
        )
        self.assertEqual(
            repos["/etc/apt/sources.list.d/pinka-docker-deb.sources"]["action"], "remove"
        )
        guard = named(plan, "guard")["the kept data"]
        self.assertNotIn("/var/lib/incus", guard["paths"])
        self.assertNotIn("/var/lib/incus", named(plan, "data"))
        rules = named(plan, "docker_user_rule")
        self.assertTrue(rules and all("incusbr" not in n for n in rules))
        # --purge-data: the work goes, and Incus with it.
        plan = plan_of(self.fake, purge_data=True, component_packages=packages)
        self.assertEqual(plan["keep_for_data"], {})
        incus = {i["name"]: i for i in plan["items"] if i["phase"] == "incus"}
        self.assertEqual(incus["user-1001/thesis"]["action"], "remove")
        self.assertEqual(named(plan, "package")["incus"]["action"], "remove")

    def test_the_ssh_server_is_never_removed(self) -> None:
        rec_path = self.fake.root / "var/lib/pinka/install-record.json"
        rec = json.loads(rec_path.read_text())
        rec["added"]["packages"].append("openssh-server")
        rec_path.write_text(json.dumps(rec))
        plan = plan_of(self.fake)
        ssh = named(plan, "package")["openssh-server"]
        self.assertEqual(ssh["action"], "keep")
        self.assertIn("host keys", ssh["reason"])


class FollowUpTest(unittest.TestCase):
    """The report says what the owner does outside the host, and whether to reboot."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.fake = FakeHost(Path(self._tmp.name))
        full_install(self.fake)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_the_tailnet_node_and_the_deploy_key(self) -> None:
        self.fake.answer(
            "tailscale",
            "status",
            "--json",
            out=json.dumps({"Self": {"HostName": "lab1", "TailscaleIPs": ["192.0.2.20"]}}),
        )
        self.fake.commands.add("ssh-keygen")
        self.fake.answer("ssh-keygen", "-l", out="256 SHA256:examplefingerprint deploy (ED25519)\n")
        active = json.loads((self.fake.root / "etc/pinka/platform/active.json").read_text())
        active["release"] = {"target": "/srv/pinka/platforms/refplat/releases/v1"}
        self.fake.file("etc/pinka/platform/active.json", json.dumps(active))
        self.fake.file(
            "srv/pinka/platforms/refplat/releases/v1/DEPLOYED_FROM.json",
            json.dumps({"source": {"repo": "git@git.example.com:example-org/refplat.git"}}),
        )
        plan = plan_of(self.fake)
        self.assertEqual(
            plan["follow_ups"],
            [
                "Delete the node lab1 (192.0.2.20) in the Tailscale admin console (Machines): "
                "uninstall logs it out, which does not take it off the tailnet.",
                "Revoke the platform's read-only deploy key SHA256:examplefingerprint on the Git "
                "host of git@git.example.com:example-org/refplat.git: uninstall removes its "
                "private half from this host.",
            ],
        )
        text = mod.plan_text(plan)
        self.assertIn("\nAfterwards:\n  - Delete the node lab1", text)
        report = mod.build_report({"results": []}, plan)
        self.assertEqual(report["follow_ups"], plan["follow_ups"])

    def test_none_when_pinka_neither_joined_nor_deployed(self) -> None:
        rec_path = self.fake.root / "var/lib/pinka/install-record.json"
        rec = json.loads(rec_path.read_text())
        rec["added"]["components"]["tailscale"] = False
        rec_path.write_text(json.dumps(rec))
        (self.fake.root / "var/lib/pinka/git/deploy_key.pub").unlink()
        plan = plan_of(self.fake)
        self.assertEqual(plan["follow_ups"], [])
        self.assertNotIn("Afterwards:", mod.plan_text(plan))

    def test_the_report_says_whether_to_reboot(self) -> None:
        plan = plan_of(self.fake)

        def result(kind: str, name: str, outcome: str) -> dict[str, Any]:
            return {
                "phase": "dropins",
                "kind": kind,
                "name": name,
                "action": "restore",
                "result": outcome,
                "reason": "",
            }

        report = mod.build_report(
            {"results": [result("file", "/etc/sysctl.d/60-pinka.conf", "removed")]}, plan
        )
        self.assertFalse(report["reboot_needed"])
        self.assertEqual(report["reboot_reasons"], [])
        report = mod.build_report(
            {
                "results": [
                    result("initramfs", "the initramfs", "restored"),
                    result("mok", "/var/lib/dkms/mok.pub", "kept"),
                ]
            },
            plan,
        )
        self.assertTrue(report["reboot_needed"])
        self.assertEqual(len(report["reboot_reasons"]), 2)
        self.assertIn("nouveau", report["reboot_reasons"][0])


class BootTimeTest(unittest.TestCase):
    """What takes effect at boot comes back and is regenerated (owner, D2 review)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.fake = FakeHost(Path(self._tmp.name))
        full_install(self.fake)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_boot_files_go_and_what_reads_them_is_regenerated(self) -> None:
        for name in (
            "etc/modprobe.d/pinka-blacklist-nouveau.conf",
            "etc/sysctl.d/60-pinka.conf",
            "etc/ld.so.conf.d/pinka-wsl.conf",
        ):
            self.fake.file(name, "x\n")
        for command in ("systemctl", "sysctl", "ldconfig", "update-initramfs", "visudo"):
            self.fake.answer(command, rc=0)
        self.fake.answer("/usr/sbin/sshd", "-t", rc=0)
        self.fake.answer("timedatectl", rc=0)
        self.fake.answer("systemctl", "is-enabled", out="disabled\n")
        self.fake.answer("systemctl", "is-enabled", "docker.service", out="enabled\n")
        p = params(self.fake)
        mod.run_module(p, run=self.fake.run)
        plan = json.loads((self.fake.root / p["plan_file"].lstrip("/")).read_text())
        self.assertIn("the initramfs", named(plan, "initramfs"))
        result = mod.run_module({**p, "state": "apply", "phase": "dropins"}, run=self.fake.run)
        results = {r["name"]: r for r in result["results"]}
        for name in (
            "/etc/modprobe.d/pinka-blacklist-nouveau.conf",
            "/etc/sysctl.d/60-pinka.conf",
            "/etc/ld.so.conf.d/pinka-wsl.conf",
        ):
            self.assertEqual(results[name]["result"], "removed", name)
            self.assertFalse((self.fake.root / name.lstrip("/")).exists(), name)
        self.assertEqual(results["the initramfs"]["result"], "restored")
        for argv in (
            ["sysctl", "--system"],
            ["ldconfig"],
            ["update-initramfs", "-u", "-k", "all"],
            ["systemctl", "daemon-reload"],
            ["systemctl", "disable", "--now", "docker.service"],
        ):
            self.assertIn(argv, self.fake.calls)
        # The initramfs is rebuilt after the blacklist is gone.
        order = [r["name"] for r in result["results"]]
        self.assertGreater(
            order.index("the initramfs"),
            order.index("/etc/modprobe.d/pinka-blacklist-nouveau.conf"),
        )
        report = mod.build_report({"results": result["results"]}, plan)
        self.assertTrue(report["reboot_needed"])
        self.assertIn("nouveau", report["reboot_reasons"][0])


class ReportTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.fake = FakeHost(Path(self._tmp.name))
        full_install(self.fake)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_the_report_names_every_item_and_leaves_one_pending(self) -> None:
        p = params(self.fake)
        mod.run_module(p, run=self.fake.run)
        paths = mod.run_module({**p, "state": "apply", "phase": "paths"}, run=self.fake.run)
        result = mod.run_module(
            {**p, "state": "report", "results": paths["results"], "repo_commit": "a" * 40},
            run=self.fake.run,
        )
        report_path = self.fake.root / p["report_file"].lstrip("/")
        text = report_path.read_text()
        report = json.loads(text)
        self.assertEqual(report_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(report["machine"], "lab1")
        self.assertEqual(report["started_at"], "2026-10-22T10:00:00Z")
        self.assertEqual(report["repo_commit"], "a" * 40)
        self.assertTrue(report["finished_at"].endswith("Z"))
        plan = json.loads((self.fake.root / p["plan_file"].lstrip("/")).read_text())
        self.assertEqual(len(report["items"]), len(plan["items"]))
        phases = [mod.PHASES.index(i["phase"]) for i in report["items"]]
        self.assertEqual(phases, sorted(phases))
        # The phases that never ran are failures in the report.
        stop = next(i for i in report["items"] if i["name"] == "pinka stop")
        self.assertEqual(stop["result"], "failed")
        self.assertIn("not carried out", stop["reason"])
        # Bootstrap finishes /opt/pinka's item with sed: one line each.
        self.assertEqual(text.count('"result": "pending"'), 1)
        self.assertEqual(text.count(f'"reason": "{mod.PENDING_REASON}"'), 1)
        self.assertEqual(result["counts"]["pending"], 1)
        self.assertGreater(result["failed"], 0)

    def test_errors(self) -> None:
        p = params(self.fake)
        with self.assertRaisesRegex(ValueError, "holds no plan"):
            mod.run_module({**p, "state": "apply", "phase": "units"}, run=self.fake.run)
        mod.run_module(p, run=self.fake.run)
        with self.assertRaisesRegex(ValueError, "unknown phase"):
            mod.run_module({**p, "state": "apply", "phase": "everything"}, run=self.fake.run)
        with self.assertRaisesRegex(ValueError, "no such phase"):
            mod.item("everything", "x", "x", "remove", "x")

    def test_check_mode_writes_nothing(self) -> None:
        p = params(self.fake)
        result = mod.run_module(p, run=self.fake.run, check_mode=True)
        self.assertIn("Uninstall plan for lab1", result["text"])
        self.assertFalse((self.fake.root / p["plan_file"].lstrip("/")).exists())


if __name__ == "__main__":
    unittest.main()
