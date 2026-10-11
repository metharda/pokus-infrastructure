"""Tests for the pinka commands other than doctor and fingerprint."""

from __future__ import annotations

import contextlib
import functools
import io
import json
import unittest
from collections.abc import Callable
from typing import Any

from labfakes import PROFILE, FakeSystem, lab

from pinka_lab import commands, docs_store
from pinka_lab.commands import CommandError

INFRA = "/srv/checkout/pinka/infra"
BOOTSTRAP = f"{INFRA}/bootstrap/bootstrap.sh"
HOST_VARS = f"{INFRA}/ansible/inventory/host_vars/pinka-lab.yml"
KEY = "ssh-ed25519 " + "A" * 68 + " researcher3@example.com\n"
TOOL = commands.PLATFORM_TOOL
ACTIVE = json.dumps(
    {"name": "demo", "version": "1.2.0", "release": {"name": "demo-v1.2.0-0123456789ab"}}
)


def captured(call: Callable[[], int]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = call()
    return code, out.getvalue(), err.getvalue()


class LabTest(unittest.TestCase):
    def test_preflight_json(self) -> None:
        system = FakeSystem(
            {"/usr/local/lib/pinka/preflight/pinka-preflight *": (10, '{"outcome": "degraded"}')}
        )
        code, out, _ = captured(lambda: commands.preflight(system, lab(), True))
        self.assertEqual(code, 10)
        self.assertEqual(json.loads(out), {"outcome": "degraded"})
        self.assertEqual(
            system.calls[0][1:],
            ["--no-write", "--srv-path", "/srv/pinka", "--quiet"],
        )

    def test_preflight_summary_runs_interactively(self) -> None:
        system = FakeSystem(interactive_rc=0)
        self.assertEqual(commands.preflight(system, lab(), False), 0)
        argv, _ = system.interactive_calls[0]
        self.assertIn("--no-write", argv)
        self.assertEqual(argv[-2], "--output")

    def test_root_only(self) -> None:
        user = FakeSystem(euid=1000)
        calls: list[Callable[[], int]] = [
            lambda: commands.start(user, lab()),
            lambda: commands.stop(user, lab(), 0),
            lambda: commands.backup_now(user, lab()),
            lambda: commands.backup_list(user, lab()),
            lambda: commands.restore(user, lab(), []),
            lambda: commands.images_build(user, lab(), None, False, []),
            lambda: commands.images_export(user, lab(), []),
            lambda: commands.update_host(user, lab(), True, []),
        ]
        for call in calls:
            with self.assertRaisesRegex(CommandError, "needs root"):
                call()
        self.assertEqual(user.calls, [])

    def test_start(self) -> None:
        system = FakeSystem({"systemctl start pinka-lab.target": (0, "")})
        code, out, _ = captured(lambda: commands.start(system, lab()))
        self.assertEqual((code, out), (0, "pinka-lab.target started\n"))
        self.assertEqual(len(system.calls), 1)  # no platform: no health hook
        failing = FakeSystem({"systemctl start pinka-lab.target": (1, "", "health gate failed\n")})
        code, _, err = captured(lambda: commands.start(failing, lab()))
        self.assertEqual((code, err), (1, "health gate failed\n"))

    def test_start_waits_for_the_health_hook(self) -> None:
        healthy = {"platform": "demo", "ok": True, "skipped": False, "error": None}
        system = FakeSystem(
            {"systemctl start pinka-lab.target": (0, ""), f"{TOOL} hook health *": (0, healthy)},
            files={commands.ACTIVE_PLATFORM: ACTIVE},
        )
        code, out, _ = captured(lambda: commands.start(system, lab(lab={"health_wait": 90})))
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines()[-1], "platform demo is healthy")
        self.assertEqual(system.calls[1], [TOOL, "hook", "health", "--until-healthy", "90"])
        sick = {**healthy, "ok": False, "error": '"ok" is not true'}
        system = FakeSystem(
            {"systemctl start pinka-lab.target": (0, ""), f"{TOOL} hook health *": (1, sick)},
            files={commands.ACTIVE_PLATFORM: ACTIVE},
        )
        code, out, _ = captured(lambda: commands.start(system, lab(lab={"health_wait": 90})))
        self.assertEqual(code, 1)
        self.assertIn("platform demo is unhealthy after 90 seconds", out)

    def test_drain(self) -> None:
        self.assertIn("no active platform", commands.drain(FakeSystem(), lab(), 10))
        answers = {
            (0, json.dumps({"platform": "demo", "ok": True})): "platform demo drained",
            (0, json.dumps({"platform": "demo", "ok": True, "skipped": True})): (
                "platform demo has no drain hook"
            ),
            (1, json.dumps({"platform": "demo", "ok": False, "error": "timeout"})): (
                "platform demo did not drain (timeout); its services stop anyway"
            ),
            (1, ""): "the platform did not drain (boom); its services stop anyway",
        }
        for answer, message in answers.items():
            system = FakeSystem(
                {f"{TOOL} hook drain *": (*answer, "boom")},
                files={commands.ACTIVE_PLATFORM: ACTIVE},
            )
            self.assertEqual(commands.drain(system, lab(), 10.5), message)
            self.assertEqual(system.calls, [[TOOL, "hook", "drain", "--timeout", "10"]])

    def test_stop_drains_first(self) -> None:
        system = FakeSystem({"systemctl stop pinka-lab.target": (0, "")})
        code, out, _ = captured(lambda: commands.stop(system, lab(), 5))
        self.assertEqual(code, 0)
        self.assertEqual(
            out.splitlines(),
            ["no active platform; nothing to drain", "pinka-lab.target stopped"],
        )

    def test_stop_drains_the_platform_then_stops_its_target(self) -> None:
        system = FakeSystem(
            {
                f"{TOOL} hook drain *": (0, {"platform": "demo", "ok": True}),
                "systemctl stop *": (0, ""),
            },
            files={commands.ACTIVE_PLATFORM: ACTIVE},
        )
        code, out, _ = captured(lambda: commands.stop(system, lab(lab={"drain_timeout": 60})))
        self.assertEqual(code, 0)
        self.assertEqual(
            system.calls,
            [
                [TOOL, "hook", "drain", "--timeout", "60"],
                ["systemctl", "stop", "pinka-platform.target"],
                ["systemctl", "stop", "pinka-lab.target"],
            ],
        )
        self.assertEqual(out.splitlines()[0], "platform demo drained")

    def test_status(self) -> None:
        system = FakeSystem(
            {
                "systemctl is-active *": (0, "active\n"),
                "docker inspect --format *": (0, "running healthy\n"),
                "incus list --all-projects --format json": (
                    0,
                    [
                        {"project": "bench", "status": "Running"},
                        {"project": "bench", "status": "Stopped"},
                        {"project": "bench", "status": "Running"},
                    ],
                ),
                "nvidia-smi *": (0, "Example GPU, 100 MiB, 12288 MiB, 0 %\n"),
                "df *": (0, "Avail Size\n 200G  1.0T\n"),
            },
            files={"/var/lib/pinka/backup/state.json": json.dumps({"backup": {"last_success": 0}})},
        )
        code, out, _ = captured(lambda: commands.status(system, lab()))
        self.assertEqual(code, 0)
        self.assertIn("pinka-lab.target", out)
        self.assertRegex(out, r"pinka-postgres\s+running healthy")
        self.assertRegex(out, r"bench\s+2 running, 1 stopped")
        self.assertIn("gpu: Example GPU", out)
        self.assertIn("disk /srv/pinka: 200G  1.0T", out)
        self.assertIn("last backup: never", out)
        self.assertIn("platform: none", out)
        system._euid = 1000
        _, _, err = captured(lambda: commands.status(system, lab()))
        self.assertIn("sudo pinka status", err)

    def test_status_shows_the_platform_and_its_health(self) -> None:
        answers = [
            ((0, {"platform": "demo", "ok": True}), "  health: healthy"),
            (
                (0, {"platform": "demo", "ok": True, "skipped": True}),
                "  health: the platform has no health hook",
            ),
            (
                (1, {"platform": "demo", "ok": False, "error": "db down"}),
                "  health: unhealthy (db down)",
            ),
        ]
        for answer, line in answers:
            system = FakeSystem(
                {"systemctl is-active *": (0, "active\n"), f"{TOOL} hook health": answer},
                files={commands.ACTIVE_PLATFORM: ACTIVE},
            )
            _, out, _ = captured(functools.partial(commands.status, system, lab()))
            self.assertIn("platform: demo 1.2.0 (release demo-v1.2.0-0123456789ab)", out)
            self.assertRegex(out, r"pinka-platform\.target\s+active")
            self.assertIn(line + "\n", out)
        user = FakeSystem(files={commands.ACTIVE_PLATFORM: ACTIVE}, euid=1000)
        _, out, _ = captured(lambda: commands.status(user, lab()))
        self.assertIn("health: needs root", out)
        self.assertFalse([c for c in user.calls if c[0] == TOOL])


class BackupTest(unittest.TestCase):
    def test_backup_now(self) -> None:
        system = FakeSystem({"systemctl start *": (0, "")})
        self.assertEqual(captured(lambda: commands.backup_now(system, lab()))[0], 0)
        self.assertEqual(
            system.calls[0], ["systemctl", "start", "pinka-pgdump.service", "pinka-backup.service"]
        )
        inference = FakeSystem({"systemctl start *": (1, "")})
        code, _, err = captured(lambda: commands.backup_now(inference, lab(node_role="inference")))
        self.assertEqual(code, 1)
        self.assertEqual(inference.calls[0], ["systemctl", "start", "pinka-backup.service"])
        self.assertIn("failed", err)

    def test_backup_list(self) -> None:
        snaps = [
            {"short_id": "0123abcd", "time": "2026-01-01T03:00:00.123Z", "tags": ["pinka-lab"]}
        ]
        system = FakeSystem({"/usr/local/libexec/pinka/backup snapshots": (0, snaps)})
        code, out, _ = captured(lambda: commands.backup_list(system, lab()))
        self.assertEqual(code, 0)
        self.assertEqual(out, "0123abcd   2026-01-01T03:00:00  pinka-lab\n")

    def test_restore(self) -> None:
        system = FakeSystem(interactive_rc=0)
        commands.restore(system, lab(), ["--non-interactive"])
        self.assertEqual(
            system.interactive_calls[0][0],
            [BOOTSTRAP, "--machine", "pinka-lab", "--restore", "--non-interactive"],
        )


class ImagesTest(unittest.TestCase):
    def test_chain(self) -> None:
        self.assertEqual(
            commands.image_chain(lab(), "web-nginx-flask"), ["_base", "web-nginx-flask"]
        )
        with self.assertRaisesRegex(CommandError, "unknown blueprint 'nope'"):
            commands.image_chain(lab(), "nope")

    def test_list(self) -> None:
        images = [
            {
                "fingerprint": "a" * 64,
                "aliases": [{"name": "_base/1.0.0"}],
                "properties": {"pinka.built_at": "2026-01-01T00:00:00Z"},
            },
            {"fingerprint": "b" * 64, "aliases": [{"name": "web-nginx-flask/1.0.0"}]},
            {"fingerprint": "c" * 64, "aliases": [{"name": "personal/1"}]},
        ]
        system = FakeSystem(
            {"incus image list --format json": (0, images)},
            files={
                "/srv/pinka/images/_base/1.0.0/build.json": json.dumps({"fingerprint": "a" * 64})
            },
        )
        code, out, _ = captured(lambda: commands.images_list(system, lab()))
        lines = out.splitlines()
        self.assertEqual(code, 0)
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("_base/1.0.0"))
        self.assertIn("built 2026-01-01T00:00:00Z  exported", lines[0])
        self.assertIn("NOT EXPORTED", lines[1])

    def test_build(self) -> None:
        system = FakeSystem()
        commands.images_build(system, lab(), None, False, [])
        argv, env = system.interactive_calls[0]
        self.assertEqual(argv, [BOOTSTRAP, "--machine", "pinka-lab", "--tags", "images"])
        self.assertEqual(env, {})
        commands.images_build(system, lab(), "web-nginx-flask", True, [])
        _, env = system.interactive_calls[1]
        self.assertEqual(
            env,
            {
                "PINKA_IMAGES_BLUEPRINTS": "_base,web-nginx-flask",
                "PINKA_IMAGES_REBUILD": "web-nginx-flask",
            },
        )
        with self.assertRaisesRegex(CommandError, "needs a blueprint"):
            commands.images_build(system, lab(), None, True, [])

    def test_export(self) -> None:
        def verify(argv: Any) -> tuple[int, Any]:
            ok = argv[argv.index("--fingerprint") + 1] == "a" * 64
            return (0 if ok else 1), {"ok": ok}

        system = FakeSystem(
            {"/usr/local/libexec/pinka/image-files verify *": verify},
            files={
                "/srv/pinka/images/_base/1.0.0/build.json": json.dumps({"fingerprint": "a" * 64}),
                "/srv/pinka/images/web-nginx-flask/1.0.0/build.json": json.dumps(
                    {"fingerprint": "b" * 64}
                ),
            },
            dirs={
                "/srv/pinka/images/_base": ["1.0.0"],
                "/srv/pinka/images/web-nginx-flask": ["1.0.0"],
            },
        )
        code, out, _ = captured(lambda: commands.images_export(system, lab(), []))
        self.assertEqual(code, 1)
        self.assertEqual(
            out.splitlines(),
            ["_base/1.0.0: ok", "web-nginx-flask/1.0.0: DOES NOT MATCH ITS FINGERPRINT"],
        )
        failing = FakeSystem(interactive_rc=4)
        self.assertEqual(commands.images_export(failing, lab(), []), 4)


HOST_VARS_TEXT = """\
machine_name: pinka-lab

researchers:
  - {name: researcher1, admin: true, shell: /bin/bash, keys_file: users/researcher1.pub}
  # a comment
  - {name: researcher2, admin: false, shell: /bin/bash, keys_file: users/researcher2.pub}

node_role: full
"""


class UsersTest(unittest.TestCase):
    def host(self, text: str = HOST_VARS_TEXT, **files: str) -> FakeSystem:
        return FakeSystem(files={HOST_VARS: text, "/tmp/key.pub": KEY, **files})

    def test_entries(self) -> None:
        start, end, names = commands.researcher_entries(HOST_VARS_TEXT.splitlines())
        self.assertEqual((start, end, names), (2, 6, ["researcher1", "researcher2"]))
        self.assertEqual(commands.researcher_entries(["a: 1"]), (-1, 1, []))
        with self.assertRaisesRegex(CommandError, "one {name: ...} entry per line"):
            commands.researcher_entries(["researchers:", "  - name: researcher1"])

    def test_add(self) -> None:
        system = self.host()
        code, out, _ = captured(
            lambda: commands.user_add(system, lab(), "researcher3", "/tmp/key.pub", False)
        )
        self.assertEqual(code, 0)
        lines = system.files[HOST_VARS].splitlines()
        self.assertEqual(
            lines[6],
            "  - {name: researcher3, admin: false, shell: /bin/bash, keys_file: users/researcher3.pub}",
        )
        self.assertEqual(lines[8], "node_role: full")
        self.assertEqual(system.written[f"{INFRA}/users/researcher3.pub"], (KEY, 0o644))
        self.assertIn("--tags users_groups,layout", out)
        self.assertEqual(system.calls, [])

    def test_add_without_list(self) -> None:
        system = self.host("machine_name: pinka-lab\n")
        captured(lambda: commands.user_add(system, lab(), "researcher3", "/tmp/key.pub", True))
        self.assertIn("researchers:\n  - {name: researcher3, admin: true", system.files[HOST_VARS])

    def test_add_refusals(self) -> None:
        system = self.host(**{"/tmp/bad.pub": "not a key\n"})
        with self.assertRaisesRegex(CommandError, "account names"):
            commands.user_add(system, lab(), "Bad-Name", "/tmp/key.pub", False)
        with self.assertRaisesRegex(CommandError, "already a researcher"):
            commands.user_add(system, lab(), "researcher1", "/tmp/key.pub", False)
        with self.assertRaisesRegex(CommandError, "only OpenSSH public keys"):
            commands.user_add(system, lab(), "researcher3", "/tmp/bad.pub", False)
        with self.assertRaisesRegex(CommandError, "cannot read"):
            commands.user_add(system, lab(), "researcher3", "/tmp/none.pub", False)
        self.assertEqual(system.written, {})

    def test_remove(self) -> None:
        system = self.host()
        captured(lambda: commands.user_remove(system, lab(), "researcher1"))
        text = system.files[HOST_VARS]
        self.assertNotIn("name: researcher1", text)
        self.assertIn("name: researcher2", text)
        self.assertTrue(text.endswith("researchers_removed: [researcher1]\n"))
        captured(lambda: commands.user_remove(system, lab(), "researcher2"))
        self.assertIn("researchers_removed: [researcher1, researcher2]", system.files[HOST_VARS])
        with self.assertRaisesRegex(CommandError, "not in the researchers list"):
            commands.user_remove(system, lab(), "researcher9")

    def test_keys(self) -> None:
        target = f"{INFRA}/users/researcher1.pub"
        system = self.host(**{target: "ssh-ed25519 old\n"})
        captured(lambda: commands.user_keys(system, lab(), "researcher1", "/tmp/key.pub"))
        self.assertEqual(system.files[target], KEY)
        with self.assertRaisesRegex(CommandError, "add the researcher first"):
            commands.user_keys(system, lab(), "researcher9", "/tmp/key.pub")


class WslUpdateTest(unittest.TestCase):
    def test_wsl_helpers(self) -> None:
        code, _, err = captured(lambda: commands.wsl_helpers(lab()))
        self.assertEqual(code, 1)
        self.assertIn("not a WSL host", err)
        code, out, _ = captured(lambda: commands.wsl_helpers(lab(provider="wsl2")))
        self.assertEqual(code, 0)
        self.assertIn(f"{INFRA}/contrib/windows", out)
        self.assertIn("never run by bootstrap", out)

    def update_host(self, family: str, **extra_files: str) -> FakeSystem:
        profile = dict(PROFILE, family=family)
        return FakeSystem(
            {"systemctl stop pinka-lab.target": (0, "")},
            files={"/etc/pinka/host-profile.json": json.dumps(profile), **extra_files},
        )

    def test_update_host(self) -> None:
        system = self.update_host("debian", **{"/var/run/reboot-required": ""})
        code, out, _ = captured(lambda: commands.update_host(system, lab(), True, []))
        self.assertEqual(code, 0)
        self.assertEqual(
            [argv for argv, _ in system.interactive_calls],
            [
                ["apt-get", "update"],
                ["apt-get", "--yes", "upgrade"],
                [BOOTSTRAP, "--machine", "pinka-lab"],
            ],
        )
        self.assertIn("fingerprint changes:\n  (none)", out)
        self.assertIn("A reboot is required", out)
        self.assertIn("/var/lib/pinka/fingerprint/current.json", system.written)

    def test_update_failures(self) -> None:
        with self.assertRaisesRegex(CommandError, "unknown family"):
            captured(lambda: commands.update_host(self.update_host("gentoo"), lab(), True, []))
        system = self.update_host("redhat")
        system.interactive_rc = 1
        code, _, _ = captured(lambda: commands.update_host(system, lab(), True, []))
        self.assertEqual(code, 1)
        self.assertEqual(len(system.interactive_calls), 1)


class DocsTest(unittest.TestCase):
    BASE = "/usr/local/share/pinka/docs"
    # §7.19: the service serves the release the current link names.
    SITE = f"{BASE}/current"

    def test_serve_from_the_lab_description(self) -> None:
        system = FakeSystem(files={f"{self.SITE}/index.html": "<html></html>"})
        code, out, _ = captured(lambda: commands.docs_serve(system, lab(), None, None))
        self.assertEqual(code, 0)
        self.assertEqual(system.served, [(self.SITE, "127.0.0.1", 18706)])
        self.assertIn("http://127.0.0.1:18706/", out)
        self.assertIn("ssh -L 18706:127.0.0.1:18706", out)

    def test_serve_a_built_site_without_a_lab(self) -> None:
        system = FakeSystem(files={"/tmp/site/index.html": "<html></html>"})
        captured(lambda: commands.docs_serve(system, None, "/tmp/site", 18800))
        self.assertEqual(system.served, [("/tmp/site", "127.0.0.1", 18800)])
        captured(lambda: commands.docs_serve(system, lab(docs__port=18801), "/tmp/site", None))
        self.assertEqual(system.served[-1], ("/tmp/site", "127.0.0.1", 18801))

    def test_serve_failures(self) -> None:
        with self.assertRaisesRegex(CommandError, "no built documentation in /nowhere"):
            commands.docs_serve(FakeSystem(), None, "/nowhere", None)
        busy = FakeSystem(
            files={f"{self.SITE}/index.html": ""}, serve_error=OSError("Address already in use")
        )
        with self.assertRaisesRegex(CommandError, "cannot listen on 127.0.0.1:18706"):
            captured(lambda: commands.docs_serve(busy, lab(), None, None))

    def test_install_and_rollback(self) -> None:
        system = FakeSystem({"systemctl is-enabled pinka-docs.service": (0, "enabled\n")})
        code, out, _ = captured(
            lambda: commands.docs_install(
                system, lab(), "/tmp/pinka-docs-v1.tar.gz", "a" * 64, None
            )
        )
        self.assertEqual(code, 0)
        self.assertIn("installed pinka-docs-v1", out)
        self.assertEqual(
            system.docs_calls[0],
            ("install", self.BASE, "/tmp/pinka-docs-v1.tar.gz", "a" * 64, None),
        )
        # A service that found nothing to serve at boot starts now.
        self.assertIn(["systemctl", "restart", "pinka-docs.service"], system.calls)
        captured(lambda: commands.docs_install(system, lab(), "/tmp/v2.tgz", "b" * 64, "v2"))
        code, out, _ = captured(lambda: commands.docs_rollback(system, lab()))
        self.assertEqual(code, 0)
        self.assertIn("switched from v2 back to pinka-docs-v1", out)

    def test_install_needs_root_and_reports_refusals(self) -> None:
        with self.assertRaisesRegex(CommandError, "root"):
            commands.docs_install(FakeSystem(euid=1000), lab(), "/tmp/x.tgz", "a" * 64, None)
        with self.assertRaisesRegex(CommandError, "root"):
            commands.docs_rollback(FakeSystem(euid=1000), lab())
        refused = FakeSystem()
        refused.docs_error = docs_store.DocsError("checksum mismatch")
        with self.assertRaisesRegex(CommandError, "checksum mismatch"):
            commands.docs_install(refused, lab(), "/tmp/x.tgz", "a" * 64, None)


if __name__ == "__main__":
    unittest.main()


PURGED = json.dumps(
    {
        "changed": True,
        "removed": ["/srv/pinka/platforms/oldplat", "/srv/pinka/restricted/oldplat"],
        "databases": ["oldplat"],
        "roles": ["oldplat_ctl"],
        "remaining": {
            "locked_accounts": ["oldplat-ctl"],
            "secrets": ["/etc/pinka/secrets/oldplat-ctl"],
            "published_signing_keys": ["/srv/pinka/shared/signing-keys/oldplat-lab-20260101.pub"],
        },
    }
)


class PlatformPurgeTest(unittest.TestCase):
    def test_purge_runs_the_deployment_tool_and_prints_what_remains(self) -> None:
        system = FakeSystem({f"{commands.PLATFORM_TOOL} purge *": (0, PURGED)})
        code, out, _ = captured(
            lambda: commands.platform_purge(system, lab(), "oldplat", "oldplat")
        )
        self.assertEqual(code, 0)
        self.assertEqual(
            system.calls[0], [commands.PLATFORM_TOOL, "purge", "oldplat", "--confirm", "oldplat"]
        )
        self.assertIn("removed /srv/pinka/platforms/oldplat", out)
        self.assertIn("dropped database oldplat", out)
        self.assertIn("remains: locked account oldplat-ctl", out)
        self.assertIn("remains: secret /etc/pinka/secrets/oldplat-ctl", out)
        self.assertIn("remains: published signing key", out)
        self.assertIn("--remove-secrets", out)

    def test_purge_can_remove_the_secrets(self) -> None:
        system = FakeSystem({f"{commands.PLATFORM_TOOL} purge *": (0, PURGED)})
        captured(lambda: commands.platform_purge(system, lab(), "oldplat", "oldplat", True))
        self.assertEqual(system.calls[0][-1], "--remove-secrets")

    def test_purge_reports_a_failure(self) -> None:
        failed = json.dumps({"changed": False, "error": "psql failed: no container"})
        system = FakeSystem({f"{commands.PLATFORM_TOOL} purge *": (1, failed)})
        code, _, err = captured(
            lambda: commands.platform_purge(system, lab(), "oldplat", "oldplat")
        )
        self.assertEqual(code, 1)
        self.assertIn("psql failed", err)

    def test_purge_asks_for_the_name(self) -> None:
        import builtins
        from unittest import mock

        system = FakeSystem({f"{commands.PLATFORM_TOOL} purge *": (0, PURGED)})
        with mock.patch.object(builtins, "input", return_value="oldplat\n"):
            code, out, _ = captured(lambda: commands.platform_purge(system, lab(), "oldplat", None))
        self.assertEqual(code, 0)
        self.assertIn("cannot be undone", out)
        with mock.patch.object(builtins, "input", return_value="other"):  # noqa: SIM117 - Python 3.9
            with self.assertRaisesRegex(CommandError, "not confirmed"):
                captured(lambda: commands.platform_purge(system, lab(), "oldplat", None))
        self.assertEqual(len(system.calls), 1)

    def test_purge_refusals(self) -> None:
        with self.assertRaisesRegex(CommandError, "needs root"):
            commands.platform_purge(FakeSystem(euid=1000), lab(), "oldplat", "oldplat")
        with self.assertRaisesRegex(CommandError, "not a platform name"):
            commands.platform_purge(FakeSystem(), lab(), "../etc", "../etc")
        with self.assertRaisesRegex(CommandError, "not confirmed"):
            commands.platform_purge(FakeSystem(), lab(), "oldplat", "oldpla")
