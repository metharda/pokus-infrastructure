"""Uninstall's data against the roles that create each item (BUILD_PROMPT §5.4).

The module ``pinka_uninstall`` keeps pinka's names itself (it runs without
the group variables); these tests keep them in step with what the roles,
the group variables and the contract create, and the play with bootstrap.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pinka_uninstall as mod
import pytest
from test_uninstall_module import DEBIAN

from ansible_testlib import ANSIBLE, FAMILIES, GROUP_VARS, INFRA, ROLES, load_yaml, template_value

ROLE = ROLES / "uninstall"
GROUP = load_yaml(GROUP_VARS)
NAMES = mod.NAMES


def family(name: str) -> dict[str, Any]:
    return dict(load_yaml(ROLE / "vars" / f"{name}.yml")["uninstall_family"])


def written_by_roles() -> str:
    """Every task, template and file of the roles but uninstall, as one text."""
    parts = []
    for path in sorted(ROLES.rglob("*")):
        if (
            path.is_file()
            and "uninstall" not in path.parts
            and path.suffix in (".yml", ".j2", ".py", ".sh")
        ):
            parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


ROLES_TEXT = written_by_roles()


def test_the_tests_copy_of_the_debian_commands_is_the_file() -> None:
    assert family("debian") == DEBIAN


@pytest.mark.parametrize("name", FAMILIES)
def test_every_family_names_the_same_commands(name: str) -> None:
    assert set(family(name)) == set(DEBIAN)
    for key in ("blocked", "unneeded_line"):
        re.compile(family(name)[key])
    # The initramfs is rebuilt as the driver's role builds it.
    commands = load_yaml(ROLES / "provider_baremetal" / "defaults" / "main.yml")
    initramfs = load_yaml(ROLES / "platform_facts" / "vars" / f"{name}.yml")[
        "platform_facts_family"
    ]
    tool = initramfs["initramfs_tool"]
    assert family(name)["initramfs"] == commands["provider_baremetal_initramfs_commands"][tool]
    hold = initramfs["version_hold_method"]
    assert family(name)["hold_method"] == hold


def test_the_phases_are_the_modules() -> None:
    assert tuple(load_yaml(ROLE / "vars" / "main.yml")["uninstall_phases"]) == mod.PHASES


def test_accounts_and_groups_are_the_group_variables() -> None:
    assert NAMES["infra_accounts"] == [a["name"] for a in GROUP["infra_accounts"]]
    assert NAMES["reserved_groups"] == [g["name"] for g in GROUP["pinka_groups"]]
    assert NAMES["pinka_root"] == GROUP["pinka_root"]


def test_containers_projects_and_incus_objects_are_pinkas() -> None:
    reserved = load_yaml(INFRA / "contract" / "reserved-names.yml")["namespaces"]
    assert all(c.startswith(NAMES["container_prefix"]) for c in reserved["containers"]["names"])
    projects = sorted(p.name for p in (INFRA / "compose").iterdir() if (p / "compose.yml").exists())
    assert NAMES["compose_projects"] == [
        f"pinka-{p}" for p in ("core", "observability", "inference")
    ]
    assert sorted(f"pinka-{p}" for p in projects) == sorted(NAMES["compose_projects"])
    incus = NAMES["incus"]
    incus_defaults = load_yaml(ROLES / "incus" / "defaults" / "main.yml")
    assert incus["pools"] == [incus_defaults["incus_pool_name"]]
    assert incus["networks"] == [incus_defaults["incus_build_network"]]
    assert incus["acls"] == [incus_defaults["incus_run_acl"]]
    assert set(incus["projects"]) <= set(reserved["incus"]["names"])
    assert incus["run_network_patterns"] == reserved["incus"]["patterns"]
    assert NAMES["interfaces"] == ["pinka-otlp0"]
    assert set(NAMES["interfaces"]) <= set(reserved["incus"]["names"])
    patterns = GROUP["pinka_incus_bridge_patterns"] + GROUP["pinka_incus_personal_bridge_patterns"]
    assert sorted(NAMES["bridge_patterns"]) == sorted(p.replace("*", "+") for p in patterns)
    assert sorted(NAMES["firewalld_trusted_interfaces"]) == sorted(
        p.replace("*", "+")
        for p in ["pinka-build0", *GROUP["pinka_incus_personal_bridge_patterns"]]
    )
    assert incus["personal_network_patterns"] == GROUP["pinka_incus_personal_bridge_patterns"]


def test_firewall_objects_are_the_firewall_roles() -> None:
    firewalld = (ROLES / "firewall" / "tasks" / "firewalld.yml").read_text(encoding="utf-8")
    assert f"loop: [{', '.join(NAMES['firewalld_zones'])}]" in firewalld
    ufw = (ROLES / "firewall" / "tasks" / "ufw.yml").read_text(encoding="utf-8")
    assert f"comment: {NAMES['ufw_comment']}" in ufw
    tables = re.findall(r"table inet (pinka_\w+)", ROLES_TEXT)
    assert tables and all(t.startswith(NAMES["nft_table_prefix"]) for t in tables)


def test_every_shared_file_the_record_keeps_is_restored() -> None:
    managed = load_yaml(ROLES / "install_record" / "defaults" / "main.yml")[
        "install_record_managed_files"
    ]
    handled = {d["path"] for d in NAMES["dropins"]} | set(NAMES["edited"])
    by_packages = {
        DEBIAN["hold_file"],
        family("redhat")["lock_file"],
        family("suse")["lock_file"],
        family("archlinux")["hold_file"],
    }
    assert sorted(set(managed) - handled - by_packages) == []
    # And every file uninstall restores or removes is one a role writes.
    for dropin in NAMES["dropins"]:
        assert dropin["path"] in ROLES_TEXT, dropin["path"]
    for path, edit in NAMES["edited"].items():
        assert edit["line"] in ROLES_TEXT, path


def test_drop_in_reloads_are_known() -> None:
    known = {"", "sshd", "sudoers", "journald", "sysctl", "ldconfig", "initramfs", "daemon-reload"}
    assert {d["reload"] for d in NAMES["dropins"]} <= known


def test_the_kept_copies_are_where_the_roles_write() -> None:
    """Uninstall copies out what the backups and the kept records need (owner, D2 review)."""
    sources = {c["source"]: c for c in NAMES["kept_copies"]}
    backup = load_yaml(ROLES / "backup" / "vars" / "main.yml")
    always = {
        backup["backup_secrets_dir"],
        backup["backup_config_dir"],
        backup["backup_state_dir"],
        backup["backup_restic"],
    }
    assert backup["backup_known_hosts"].startswith(backup["backup_config_dir"] + "/")
    stages = (INFRA / "bootstrap" / "lib" / "stages.sh").read_text(encoding="utf-8")
    vault = re.search(r"readonly STORED_VAULT_PASS=(\S+)", stages)
    assert vault
    always.add(vault[1])
    arbiter = (ROLES / "gpu_arbiter" / "files" / "pinka_gpu_arbiter.py").read_text(encoding="utf-8")
    arbiter_state = re.search(r'state: Path = Path\("([^"]+)"\)', arbiter)
    assert arbiter_state
    records = {
        "/etc/pinka/qa",  # trusted keys and attestations (contracts.md)
        arbiter_state[1],
        load_yaml(ROLES / "lab_session" / "vars" / "main.yml")["lab_session_sessions_dir"],
        load_yaml(ROLES / "doctor" / "vars" / "main.yml")["doctor_fingerprint_dir"],
        load_yaml(ROLES / "nvidia_container_toolkit" / "defaults" / "main.yml")[
            "nvidia_container_toolkit_verify_dir"
        ],
        next(e["path"] for e in GROUP["pinka_system_layout"] if e["path"].endswith("/platforms")),
    }
    assert {s for s, c in sources.items() if c["always"]} == always
    assert {s for s, c in sources.items() if not c["always"]} == records
    assert "/etc/pinka/qa/trusted-keys/" in ROLES_TEXT
    # Every copy lands in its own place below the keep directory.
    dests = [c["dest"] for c in NAMES["kept_copies"]]
    assert len(set(dests)) == len(dests)
    assert NAMES["keep_dir"] not in NAMES["paths"]
    assert not NAMES["keep_dir"].startswith(NAMES["state_dir"] + "/")
    # The README's commands name the copies they use.
    readme = mod.KEEP_README.format(
        when="x", copies="", keep=NAMES["keep_dir"], parent="/var/lib", name="pinka-kept"
    )
    for dest in ("backup/secrets/repository", "backup/secrets/password", "backup/restic"):
        assert f"{NAMES['keep_dir']}/{dest}" in readme
    # What grants access to the backups is marked, and the README says to take it away.
    assert {s for s, c in sources.items() if c.get("secret")} == {
        backup["backup_secrets_dir"],
        vault[1],
    }
    assert f"find {NAMES['keep_dir']} -type f -exec shred -u {{}} +" in readme
    backup_tasks = (ROLES / "backup" / "tasks" / "main.yml").read_text(encoding="utf-8")
    for name in ("repository", "password", "ssh_key"):
        assert f"{{{{ backup_secrets_dir }}}}/{name}" in backup_tasks, name


def test_pinkas_containers_keep_their_data_below_the_research_space() -> None:
    """Removing pinka's containers (docker rm --volumes) takes no data (owner, D2 review).

    Every store is a bind mount: below the research space, or a host path
    that is no data; no Compose project has a named volume.
    """
    for compose in sorted((INFRA / "compose").glob("*/compose.yml")):
        data = load_yaml(compose)
        assert "volumes" not in data, compose
        for name, service in data["services"].items():
            for volume in service.get("volumes", []):
                spec = volume["source"] if isinstance(volume, dict) else str(volume)
                source = re.match(r"(\$\{\w+:\?\}[^:]*|[^:]+)", spec)
                assert source, spec
                assert source[1].startswith(
                    ("/", "./", "${PINKA_ROOT:?}/", "${DOCKER_DATA_ROOT")
                ), (
                    compose.parent.name,
                    name,
                    volume,
                )


GUARD_PROBE = """
import json, subprocess, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import pinka_uninstall as mod

root = Path(sys.argv[2])

def run(argv):
    done = subprocess.run(list(argv), capture_output=True, text=True, check=False)
    return done.returncode, done.stdout, done.stderr

ctx = mod.Context(mod.Host(run, str(root)), {})
guarded, problems = mod.guard_data(ctx, ["/srv/pinka", "/var/lib/docker", "/var/lib/absent"])
# A package script that deletes the data, as incus-base's purge does.
script = subprocess.run(["rm", "-rf", str(root / "srv/pinka"), str(root / "var/lib/docker")],
                        capture_output=True, text=True, check=False)
kept = sorted(str(p.relative_to(root)) for d in ("srv", "var") for p in (root / d).rglob("*")
              if p.is_file())
state = json.loads((root / "run/pinka-uninstall-guards").read_text())
mod.release_guards(ctx)
(root / "srv/pinka/after").write_text("writable again")
print(json.dumps({"guarded": guarded, "problems": problems, "script_rc": script.returncode,
                  "kept": kept, "state": state,
                  "state_left": (root / "run/pinka-uninstall-guards").exists()}))
"""


def as_root() -> list[str]:
    """How to run as root: directly, or with sudo -n in CI; skip otherwise."""
    if os.geteuid() == 0:
        return []
    sudo = shutil.which("sudo")
    if sudo and subprocess.run([sudo, "-n", "true"], check=False).returncode == 0:
        return [sudo, "-n"]
    if os.environ.get("CI") == "true":
        pytest.fail("the read-only guard needs root or sudo -n in CI")
    pytest.skip("needs root or sudo -n; allowed to skip only outside CI")


def test_the_guard_stops_a_package_script_on_a_real_mount(tmp_path: Path) -> None:
    """The guard's bind mounts, in a private mount namespace: rm -rf cannot delete kept data."""
    unshare = shutil.which("unshare")
    if unshare is None:
        if os.environ.get("CI") == "true":
            pytest.fail("unshare is required in CI")
        pytest.skip("unshare is not installed")
    root = tmp_path / "host"
    for name, text in {
        "srv/pinka/models/m1/weights.bin": "w",
        "srv/pinka/state/postgres/PG_VERSION": "18",
        "var/lib/docker/volumes/v/_data/x": "x",
    }.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    (root / "run").mkdir()
    probe = tmp_path / "probe.py"
    probe.write_text(GUARD_PROBE, encoding="utf-8")
    library = str(ROLE / "library")
    done = subprocess.run(
        [
            *as_root(),
            unshare,
            "--mount",
            "--propagation",
            "private",
            sys.executable,
            "-I",
            str(probe),
            library,
            str(root),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout)
    assert result["problems"] == []
    assert result["guarded"] == ["/srv/pinka", "/var/lib/docker"]
    assert result["state"] == {"guarded": ["/srv/pinka", "/var/lib/docker"]}
    assert result["script_rc"] != 0
    assert result["kept"] == [
        "srv/pinka/models/m1/weights.bin",
        "srv/pinka/state/postgres/PG_VERSION",
        "var/lib/docker/volumes/v/_data/x",
    ]
    assert result["state_left"] is False
    assert (root / "srv/pinka/after").read_text() == "writable again"


# Boot-time state (owner, D2 review): where a file pinka writes takes effect at
# boot, and what uninstall runs after it removes or restores it there.
BOOT_LOCATIONS = {
    "/etc/modprobe.d/": "initramfs",
    "/etc/sysctl.d/": "sysctl",
    "/etc/ld.so.conf.d/": "ldconfig",
    "/etc/systemd/": "daemon-reload",
}
# Boot-time places no role writes: uninstall has nothing of pinka's to set back
# there. A role that starts writing one fails here until uninstall restores it.
NEVER_WRITTEN = re.compile(
    r"/etc/default/grub|/etc/kernel/cmdline|/etc/modules-load\.d|/etc/modules\b"
    r"|system-preset|/etc/dracut\.conf|/etc/initramfs-tools|/boot/|grub2?-mkconfig"
    r"|update-grub|grubby|kernelstub|bootctl|systemctl preset|persistent: present"
)


def test_boot_time_state_is_restored_and_regenerated() -> None:
    written = set(
        re.findall(r"(/etc/(?:modprobe\.d|sysctl\.d|ld\.so\.conf\.d)/[\w.-]+\.conf)", ROLES_TEXT)
    )
    assert written == {
        "/etc/modprobe.d/pinka-blacklist-nouveau.conf",
        "/etc/sysctl.d/60-pinka.conf",
        "/etc/ld.so.conf.d/pinka-wsl.conf",
    }
    reloads = {d["path"]: d["reload"] for d in NAMES["dropins"]}
    for path in written:
        expected = next(r for prefix, r in BOOT_LOCATIONS.items() if path.startswith(prefix))
        assert reloads[path] == expected, path
    assert NEVER_WRITTEN.findall(ROLES_TEXT) == []
    # Units and drop-ins of units: a daemon-reload after each phase that changes them.
    assert ["systemctl", "daemon-reload"] in mod.EPILOGUES["units"]
    assert ["systemctl", "daemon-reload"] in mod.EPILOGUES["dropins"]
    # The kernel settings go back to their values before pinka with sysctl -w
    # (test_base_records_the_time_zone_and_the_kernel_settings_it_changes).


def test_sleep_targets_and_keys_are_the_providers() -> None:
    defaults = load_yaml(ROLES / "provider_baremetal" / "defaults" / "main.yml")
    assert NAMES["sleep_targets"] == defaults["provider_baremetal_sleep_targets"]
    assert NAMES["mok_keys"] == defaults["provider_baremetal_mok_keys"]
    assert NAMES["sleep_marker"] in ROLES_TEXT


def test_paths_are_pinkas() -> None:
    for path in NAMES["paths"]:
        assert "pinka" in path, path
        assert (
            path in ROLES_TEXT or path in (INFRA / "bootstrap" / "lib" / "stages.sh").read_text()
        ), path
    assert NAMES["opt_dir"] in (INFRA / "bootstrap" / "lib" / "tooling.sh").read_text()
    for link in NAMES["links"]:
        assert link in ROLES_TEXT, link
    assert set(NAMES["logs"]) == {
        "/var/log/pinka-bootstrap",
        "/var/log/pinka-uninstall",
    }
    stages = (INFRA / "bootstrap" / "lib" / "stages.sh").read_text()
    for log in NAMES["logs"]:
        assert log in stages


def test_units_pinka_enables_are_known() -> None:
    for unit in NAMES["never_stop"]:
        assert unit in NAMES["distribution_units"]
    for unit in (
        "docker.service",
        "incus.service",
        "tailscaled.service",
        "nvidia-cdi-refresh.path",
    ):
        assert unit in NAMES["distribution_units"]


def test_component_sources_name_real_components() -> None:
    sources = load_yaml(ANSIBLE / "component-sources.yml")["components"]
    defaults = load_yaml(ROLE / "defaults" / "main.yml")["uninstall_component_sources"]
    assert sorted(defaults) == sorted(mod.COMPONENTS)
    for entries in defaults.values():
        assert set(entries) <= set(sources)


def test_the_play_runs_without_group_variables() -> None:
    play = load_yaml(ANSIBLE / "uninstall.yml")
    roles = [
        t.get("ansible.builtin.import_role", {}).get("name")
        for p in play
        for t in p.get("tasks", [])
    ]
    assert roles == ["platform_facts", "uninstall"]
    stages = (INFRA / "bootstrap" / "lib" / "stages.sh").read_text()
    assert "ANSIBLE_VARS_ENABLED=''" in stages
    # Nothing the play reaches reads a group variable.
    tasks = "\n".join(
        p.read_text()
        for p in [
            *(ROLE / "tasks").glob("*.yml"),
            ROLES / "platform_facts" / "tasks" / "family.yml",
        ]
    )
    for name in GROUP:
        assert not re.search(rf"\b{re.escape(name)}\b", tasks), name


def test_the_family_facts_are_set_as_platform_facts_sets_them() -> None:
    main = (ROLES / "platform_facts" / "tasks" / "main.yml").read_text()
    family_tasks = load_yaml(ROLES / "platform_facts" / "tasks" / "family.yml")
    facts = next(t for t in family_tasks if "ansible.builtin.set_fact" in t)[
        "ansible.builtin.set_fact"
    ]
    for name, expression in facts.items():
        assert (
            f"{name}: {expression!r}".replace("'", '"') in main or f'{name}: "{expression}"' in main
        ), name


def test_pinka_paths_in_bootstrap_match() -> None:
    stages = (INFRA / "bootstrap" / "lib" / "stages.sh").read_text()
    assert 'readonly PENDING_ITEM=\'"result": "pending"\'' in stages
    assert f"readonly PENDING_REASON='{mod.PENDING_REASON}'" in stages


def test_the_session_keys_are_the_host_profiles() -> None:
    schema = json.loads((INFRA / "preflight" / "schema" / "host-profile.schema.json").read_text())
    session = schema["properties"]["access"]["properties"]["session"]["properties"]
    source = Path(mod.__file__).read_text()
    read = set(re.findall(r'session\.get\("([a-z_]+)"\)', source))
    assert read and read <= set(session), read - set(session)


def test_the_play_reads_every_option_bootstrap_passes() -> None:
    stages = (INFRA / "bootstrap" / "lib" / "stages.sh").read_text()
    body = stages.split("uninstall_vars_json() {", 1)[1].split("\n}\n", 1)[0]
    passed = re.findall(r'^  "([a-z_]+)":', body, re.M)
    assert len(passed) > 5
    tasks = (ROLE / "tasks" / "main.yml").read_text()
    assert [k for k in passed if f"pinka_uninstall.{k}" not in tasks] == []


def test_module_names_are_paths() -> None:
    for key in ("record", "lab", "active_platform", "rendered_platform", "pinka_cli", "opt_dir"):
        assert Path(NAMES[key]).is_absolute(), key


# --- what the roles record before they change the host ----------------------------------------


def task(path: Path, name: str) -> dict[str, Any]:
    """A task of a task file by its name."""
    for entry in load_yaml(path):
        if entry.get("name") == name:
            return dict(entry)
    raise AssertionError(f"{path.name} has no task {name!r}")


def with_task_vars(entry: dict[str, Any], variables: dict[str, Any]) -> dict[str, Any]:
    """The variables plus the task's own vars, templated in order."""
    merged = dict(variables)
    for key, value in (entry.get("vars") or {}).items():
        merged[key] = template_value(value, merged)
    return merged


def test_base_records_the_time_zone_and_the_kernel_settings_it_changes() -> None:
    entry = task(
        ROLES / "base" / "tasks" / "main.yml", "Record the settings pinka changes, as they were"
    )
    sysctl = load_yaml(ROLES / "base" / "defaults" / "main.yml")["base_sysctl"]
    lines = [
        "timezone=Europe/Example",
        "sysctl:fs.inotify.max_user_watches=1048576",
        "sysctl:fs.inotify.max_user_instances=128",
        "sysctl:net.ipv4.ip_forward=0",
    ]
    variables = with_task_vars(
        entry, {"base_settings_now": {"stdout_lines": lines}, "base_sysctl": sysctl}
    )
    assert template_value(entry["pinka_install_record"]["added"]["changed"], variables) == {
        "timezone": "Europe/Example",
        "sysctl:fs.inotify.max_user_instances": "128",
        "sysctl:net.ipv4.ip_forward": "0",
    }
    again = with_task_vars(
        entry,
        {
            "base_settings_now": {"stdout_lines": ["timezone=UTC", "sysctl:net.ipv4.ip_forward=1"]},
            "base_sysctl": sysctl,
        },
    )
    assert template_value("{{ " + entry["when"] + " }}", again) is False


def test_firewalld_records_the_zones_it_changes() -> None:
    path = ROLES / "firewall" / "tasks" / "firewalld.yml"
    entry = task(path, "Record the zone of tailscale0 before pinka")
    for result, expected in (
        ({"rc": 0, "stdout": "public\n"}, {"firewalld:zone-of:tailscale0": "public"}),
        ({"rc": 2, "stdout": "no zone\n"}, {"firewalld:zone-of:tailscale0": ""}),
    ):
        variables = {
            "firewall_tailnet_interface": "tailscale0",
            "firewall_tailnet_zone_before": result,
        }
        assert (
            template_value(entry["pinka_install_record"]["added"]["changed"], variables) == expected
        )
    gone = {
        "firewall_tailnet_interface": "tailscale0",
        "firewall_tailnet_zone_before": {"rc": 0, "stdout": "pinka-tailnet\n"},
    }
    assert template_value("{{ " + entry["when"] + " }}", gone) is False
    entry = task(path, "Record the zones where pinka opens SSH")
    results = [
        {"rc": 1, "item": {"item": "eth1", "stdout": "public\n"}},
        {"rc": 0, "item": {"item": "eth2", "stdout": "internal\n"}},
        {"rc": 1, "item": {"item": "eth3", "stdout": "public\n"}},
    ]
    variables = with_task_vars(entry, {"firewall_extra_ssh_before": {"results": results}})
    assert template_value(entry["pinka_install_record"]["added"]["changed"], variables) == {
        "firewalld:service:public:ssh": "absent"
    }


def test_incus_records_what_it_creates_in_an_incus_it_did_not_install() -> None:
    import json  # noqa: PLC0415 - only here

    entry = task(
        ROLES / "incus" / "tasks" / "record.yml",
        "Record what pinka creates and changes in this Incus",
    )
    found = [
        {
            "item": {"key": "server"},
            "stdout": json.dumps({"config": {"core.https_address": "[::]:8443"}}),
        },
        {"item": {"key": "storage-pools"}, "stdout": json.dumps(["/1.0/storage-pools/default"])},
        {"item": {"key": "networks"}, "stdout": json.dumps(["/1.0/networks/incusbr-1000"])},
        {"item": {"key": "network-acls"}, "stdout": "[]"},
        {
            "item": {"key": "projects"},
            "stdout": json.dumps(["/1.0/projects/default", "/1.0/projects/user-1000"]),
        },
        {"item": {"key": "certificates"}, "stdout": "[]"},
    ]
    profiles = [
        {"item": "bench", "rc": 1, "stdout": ""},
        {"item": "user-1000", "stdout": json.dumps(["/1.0/profiles/default?project=user-1000"])},
    ]
    variables = with_task_vars(
        entry,
        {
            "incus_found_objects": {"results": found},
            "incus_found_profiles": {"results": profiles},
            "incus_pool": {"name": "pinka-targets"},
            "incus_networks": [{"name": "pinka-build0"}, {"name": "incusbr-1000"}],
            "incus_run_acl": "pinka-run-default",
            "incus_projects": [{"name": "bench"}, {"name": "user-1000"}],
            "incus_profiles": [
                {"project": "bench", "name": "target-container"},
                {"project": "user-1000", "name": "default"},
            ],
            "incus_service_certificates": [],
            "incus_metrics_certificate": {"name": "pinka-metrics"},
            "incus_server_config": {"core.https_address": "127.0.0.1:8443"},
        },
    )
    added = entry["pinka_install_record"]["added"]
    assert template_value(added["incus_objects"], variables) == [
        "acl:pinka-run-default",
        "certificate:pinka-metrics",
        "network:pinka-build0",
        "pool:pinka-targets",
        "profile:bench/target-container",
        "project:bench",
    ]
    assert template_value(added["changed"], variables) == {
        "incus:config:core.https_address": "[::]:8443"
    }


def test_incus_records_the_researchers_it_adds_to_its_group() -> None:
    entry = task(
        ROLES / "incus" / "tasks" / "access.yml",
        "Record the researchers pinka adds to the Incus user group",
    )
    for group, expected in (
        ({"incus": ["x", "995", "researcher2"]}, [{"user": "researcher1", "group": "incus"}]),
        (
            {},
            [{"user": "researcher1", "group": "incus"}, {"user": "researcher2", "group": "incus"}],
        ),
    ):
        variables = with_task_vars(
            entry,
            {
                "ansible_facts": {"getent_group": group},
                "incus_groups": {"user": "incus"},
                "researchers": [{"name": "researcher1"}, {"name": "researcher2"}],
            },
        )
        assert (
            template_value(entry["pinka_install_record"]["added"]["memberships"], variables)
            == expected
        )


def test_the_component_packages_come_from_the_source_matrix() -> None:
    entry = task(ROLE / "tasks" / "main.yml", "Work out the plan")
    variables = with_task_vars(
        entry,
        {
            "uninstall_component_sources": load_yaml(ROLE / "defaults" / "main.yml")[
                "uninstall_component_sources"
            ],
            "uninstall_sources": load_yaml(ANSIBLE / "component-sources.yml"),
        },
    )
    packages = variables["uninstall_component_packages"]
    assert sorted(packages) == sorted(mod.COMPONENTS)
    assert "docker-ce" in packages["docker"]
    assert "tailscale" in packages["tailscale"]
    assert "nvidia-container-toolkit" in packages["nvidia"]
    # A kept component keeps its repository files (pinka-<repo>.*), named here.
    repos = variables["uninstall_component_repos"]
    assert sorted(repos) == sorted(mod.COMPONENTS)
    assert "zabbly-incus-lts" in repos["incus"]
    assert {"docker-deb", "docker-rpm-fedora"} <= set(repos["docker"])
