"""The incus role's desired state, its templates, and the step 6 templates.

``desired.yml`` is evaluated the way Ansible runs it (its ``set_fact`` tasks in
order, with loops and conditions), so the tests see exactly the projects,
profiles, networks and preseed a host would get.
"""

from __future__ import annotations

import configparser
import ipaddress
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from ansible_testlib import (
    INFRA,
    ROLES,
    evaluate,
    fixture_profile,
    init_plugins,
    load_yaml,
    render_file,
    render_template,
    role_vars,
    template_value,
)
from pinka_preflight.detect.conflicts import MANAGED_MARKER

INCUS_DIR = INFRA / "incus"
FIXTURE = "ubuntu2404-baremetal-nvidia-driver-loaded"
# Config keys incus-user sets on a personal project (Incus 6.0.7 cmd/incus-user/server.go).
INCUS_USER_KEYS = {
    "features.images",
    "features.networks",
    "features.networks.zones",
    "features.profiles",
    "features.storage.volumes",
    "features.storage.buckets",
    "restricted",
    "restricted.containers.nesting",
    "restricted.devices.disk",
    "restricted.devices.disk.paths",
    "restricted.devices.gpu",
    "restricted.idmap.uid",
    "restricted.idmap.gid",
    "restricted.networks.access",
}
PASSWD = {
    "researcher1": ["x", "1001", "1001", "Researcher 1", "/home/researcher1", "/bin/bash"],
    "researcher2": ["x", "1002", "1002", "Researcher 2", "/home/researcher2", "/bin/bash"],
}


def incus_vars(vm_targets: bool = True, **extra: Any) -> dict[str, Any]:
    profile = fixture_profile(FIXTURE)
    features = {**profile["features"], "vm_targets": vm_targets}
    return role_vars(
        "incus",
        "debian",
        host_profile=profile,
        pinka_features=features,
        pinka_dir=str(INFRA),
        ansible_facts={"getent_passwd": PASSWD},
        **extra,
    )


def run_set_facts(path: Path, variables: dict[str, Any]) -> dict[str, Any]:
    """Run the set_fact tasks of a task file in order (loops, when, loop_control)."""
    # Imported here, after ansible_testlib has pointed Ansible at build/.
    from ansible.parsing.dataloader import DataLoader  # noqa: PLC0415
    from ansible.template import Templar  # noqa: PLC0415

    init_plugins()
    loader = DataLoader()
    loader.set_basedir(str(path.parent))
    tasks = loader.load_from_file(str(path), trusted_as_template=True)
    facts = dict(variables)
    for task in tasks:
        if "ansible.builtin.include_tasks" in task:
            facts = run_set_facts(path.parent / task["ansible.builtin.include_tasks"], facts)
            continue
        if "ansible.builtin.set_fact" not in task:
            continue
        templar = Templar(loader=loader, variables=facts)
        items = templar.template(task["loop"]) if "loop" in task else [None]
        for item in items:
            scope = {**facts, "item": item} if item is not None else facts
            templar = Templar(loader=loader, variables=scope)
            when = task.get("when")
            if when is not None and not templar.evaluate_conditional(when):
                continue
            facts.update(templar.template(task["ansible.builtin.set_fact"]))
    return facts


@pytest.fixture(scope="module")
def desired() -> dict[str, Any]:
    variables = incus_vars(
        researchers=[{"name": "researcher1"}, {"name": "researcher2"}, {"name": "gone"}]
    )
    return run_set_facts(ROLES / "incus" / "tasks" / "desired.yml", variables)


def by_name(items: list[dict[str, Any]], name: str, project: str | None = None) -> dict[str, Any]:
    return next(
        i for i in items if i["name"] == name and (project is None or i.get("project") == project)
    )


def all_strings(mapping: dict[str, Any]) -> bool:
    return all(isinstance(v, str) for v in mapping.values())


def test_researchers_and_networks(desired: dict[str, Any]) -> None:
    assert desired["incus_researchers"] == [
        {
            "name": "researcher1",
            "uid": 1001,
            "gid": 1001,
            "home": "/home/researcher1",
            "network": "incusbr-1001",
        },
        {
            "name": "researcher2",
            "uid": 1002,
            "gid": 1002,
            "home": "/home/researcher2",
            "network": "incusbr-1002",
        },
    ]
    names = [n["name"] for n in desired["incus_networks"]]
    assert names == ["pinka-build0", "incusbr-1001", "incusbr-1002"]
    build = by_name(desired["incus_networks"], "pinka-build0")
    assert build["config"] == {"ipv4.address": "auto", "ipv4.nat": "true", "ipv6.address": "none"}
    assert "incusbr0" not in names


THIN = {"lvm.use_thinpool": "true", "lvm.thinpool_metadata_size": "1GiB", "volume.size": "3GiB"}


def test_server_and_pool(desired: dict[str, Any]) -> None:
    # On pinka's own Incus (the fixture's: preflight found none) no image of
    # the default project updates itself; the server's interval is left alone.
    assert desired["incus_own"] is True
    assert desired["incus_server_config"] == {"core.https_address": "127.0.0.1:8443"}
    assert desired["incus_default_project_config"] == {"images.auto_update_interval": "0"}
    assert desired["incus_pool"] == {
        "name": "pinka-targets",
        "description": "pinka instances",
        "driver": "lvm",
        # incus_pool_size and incus_pool_volume_size: auto, computed by preflight.
        "config": {"size": "181GiB", **THIN},
    }


def conditions(task: dict[str, Any]) -> list[str]:
    """A task's ``when``, as a list of conditions."""
    when = task.get("when", [])
    return [when] if isinstance(when, str) else list(when)


DEFAULT_PROJECT_TASK = "Stop the images of the default project from updating themselves"


def task_named(path: Any, name: str) -> dict[str, Any]:
    """The task of a task file with this name."""
    return next(t for t in load_yaml(path) if t["name"] == name)


def test_the_default_project_keeps_its_images() -> None:
    project = task_named(ROLES / "incus" / "tasks" / "objects.yml", DEFAULT_PROJECT_TASK)
    assert project["pinka_incus_object"] == {
        "kind": "project",
        "name": "default",
        "config": "{{ incus_default_project_config }}",
    }
    assert project["when"] == "incus_own | bool"
    names = [t["name"] for t in load_yaml(ROLES / "incus" / "tasks" / "objects.yml")]
    assert names.index(DEFAULT_PROJECT_TASK) < names.index("Converge the projects")


# Each kind of host, as uninstall's incus_scope reads its record: what the
# role decides and which record it writes ("objects": record.yml, for an
# Incus that is not pinka's; "settings": record_settings.yml, for pinka's own).
HOST_KINDS: list[tuple[dict[str, Any], bool, bool, str | None]] = [
    # The first run installs Incus: preflight found none (uninstall: "all").
    ({}, False, True, "settings"),
    # A later run: the record names Incus as pinka's ("all").
    ({"pinka_record_added": {"components": {"incus": True}}}, True, True, "settings"),
    # An Incus that was there at the first install (adopted; "recorded").
    ({"pinka_record_added": {}}, True, False, "objects"),
    # One installed by its owner and adopted after the first install
    # ("recorded"): what pinka creates there is recorded too.
    ({"pinka_record_added": {"components": {}}}, True, False, "objects"),
    # A legacy record decides nothing, as for uninstall ("names"): pinka
    # leaves Incus as it is and records nothing uninstall would not read.
    (
        {"pinka_record_added": {"components": {"incus": True}}, "pinka_record_legacy": True},
        True,
        False,
        None,
    ),
    ({"pinka_record_added": {}, "pinka_record_legacy": True}, False, False, None),
]


@pytest.mark.parametrize(("facts", "installed_before", "own", "records"), HOST_KINDS)
def test_only_pinkas_own_incus_stops_image_updates(
    facts: dict[str, Any], installed_before: bool, own: bool, records: str | None
) -> None:
    variables = incus_vars()
    variables.update(facts)
    profile = dict(variables["host_profile"])
    profile["components"] = {
        **profile.get("components", {}),
        "incus": {"installed": installed_before},
    }
    variables["host_profile"] = profile
    result = run_set_facts(ROLES / "incus" / "tasks" / "desired.yml", variables)
    assert result["incus_own"] is own
    assert result["incus_server_config"] == {"core.https_address": "127.0.0.1:8443"}
    # Only on pinka's own Incus is the default project's interval set.
    project = task_named(ROLES / "incus" / "tasks" / "objects.yml", DEFAULT_PROJECT_TASK)
    assert evaluate(project["when"], {**facts, **result}) is own
    main = {t["name"]: t for t in load_yaml(ROLES / "incus" / "tasks" / "main.yml")}
    runs = {
        kind: all(evaluate(condition, {**facts, **result}) for condition in conditions(main[name]))
        for kind, name in (
            ("objects", "Record what pinka creates in an Incus that is not its own"),
            ("settings", "Record the settings pinka changes in its own Incus"),
        )
    }
    assert runs == {"objects": records == "objects", "settings": records == "settings"}


def test_the_settings_of_pinkas_own_incus_are_recorded() -> None:
    main = {t["name"]: t for t in load_yaml(ROLES / "incus" / "tasks" / "main.yml")}
    record = main["Record what pinka creates in an Incus that is not its own"]
    own = main["Record the settings pinka changes in its own Incus"]
    assert record["when"] == [
        "not (incus_own | bool)",
        "not (pinka_record_legacy | default(false) | bool)",
    ]
    assert own["when"] == "incus_own | bool"
    assert record["ansible.builtin.include_tasks"] == "record.yml"
    assert own["ansible.builtin.include_tasks"] == "record_settings.yml"
    names = list(main)
    # Before anything configures the server.
    assert names.index(own["name"]) < names.index("Initialize a new installation")
    assert names.index(own["name"]) < names.index("Converge the Incus objects")
    tasks = {t["name"]: t for t in load_yaml(ROLES / "incus" / "tasks" / "record_settings.yml")}
    for name, url, result in (
        ("Read the Incus server's settings", "/1.0", "incus_server_now"),
        (
            "Read the default project's settings",
            "/1.0/projects/default",
            "incus_default_project_now",
        ),
    ):
        read = tasks[name]
        assert read["ansible.builtin.command"]["argv"] == ["{{ incus_cli }}", "query", url]
        assert read["check_mode"] is False
        assert read["failed_when"] == f"{result}.rc != 0 and not ansible_check_mode"
    entry = tasks["Record the settings pinka changes, with their value before"]
    assert entry["pinka_install_record"]["state"] == "add"
    assert entry["when"] == ["incus_server_now.rc == 0", "incus_default_project_now.rc == 0"]
    changed = entry["pinka_install_record"]["added"]["changed"]
    for server, project, expected in (
        (
            {},
            {"features.images": "true"},
            {
                "incus:config:core.https_address": "",
                "incus:project:default:images.auto_update_interval": "",
            },
        ),
        (
            {"core.https_address": "127.0.0.1:8443", "images.auto_update_interval": "3"},
            {"images.auto_update_interval": "12"},
            {"incus:project:default:images.auto_update_interval": "12"},
        ),
        (
            {"core.https_address": "127.0.0.1:8443"},
            {"images.auto_update_interval": "0"},
            {},
        ),
    ):
        variables = {
            "incus_server_now": {"stdout": json.dumps({"config": server})},
            "incus_default_project_now": {"stdout": json.dumps({"config": project})},
            "incus_server_config": {"core.https_address": "127.0.0.1:8443"},
            "incus_default_project_config": {"images.auto_update_interval": "0"},
        }
        assert template_value(changed, variables) == expected


def test_explicit_sizes_win() -> None:
    variables = incus_vars(researchers=[], incus_pool_size="300GiB", incus_pool_volume_size="5GiB")
    facts = run_set_facts(ROLES / "incus" / "tasks" / "desired.yml", variables)
    assert facts["incus_pool"]["config"] == {"size": "300GiB", **THIN, "volume.size": "5GiB"}


def test_a_device_pool() -> None:
    variables = incus_vars(
        researchers=[],
        incus_pool_source="device",
        incus_pool_device="/dev/disk/by-id/example-disk",
    )
    facts = run_set_facts(ROLES / "incus" / "tasks" / "desired.yml", variables)
    assert facts["incus_pool"]["driver"] == "lvm"
    assert facts["incus_pool"]["config"] == {"source": "/dev/disk/by-id/example-disk", **THIN}


def test_lvm_thin_only() -> None:
    # The play refuses any other backend before an Incus object is described.
    tasks = yaml.safe_load((ROLES / "incus" / "tasks" / "desired.yml").read_text())
    check = next(t for t in tasks if t["name"] == "Check that the host has LVM thin provisioning")
    assert "host_profile.storage_backend == 'lvm'" in check["ansible.builtin.assert"]["that"]


def test_the_metadata_size_is_what_preflight_counts() -> None:
    defaults = yaml.safe_load((ROLES / "incus" / "defaults" / "main.yml").read_text())
    requirements = yaml.safe_load((INFRA / "preflight" / "requirements.yml").read_text())
    assert defaults["incus_pool_metadata_size"] == f"{requirements['pool']['metadata_gib']}GiB"


def test_volume_size_is_kept_on_an_existing_pool_and_the_loop_file_preallocated() -> None:
    tasks = yaml.safe_load((ROLES / "incus" / "tasks" / "objects.yml").read_text())
    create = next(t for t in tasks if t["name"].startswith("Create the storage pool"))
    assert create["pinka_incus_object"]["update"] is False  # created once, never rewritten
    keep = next(t for t in tasks if t["name"].startswith("Set the image volume size"))
    assert keep["pinka_incus_object"]["config"] == {
        "volume.size": "{{ incus_pool_volume_size_value }}"
    }
    assert "update" not in keep["pinka_incus_object"]  # updated on every run
    allocate = next(t for t in tasks if t["name"] == "Preallocate the loop pool's file")
    assert allocate["ansible.builtin.command"]["argv"][:2] == ["fallocate", "--length"]


def test_projects(desired: dict[str, Any]) -> None:
    projects = desired["incus_projects"]
    assert [p["name"] for p in projects] == ["bench", "build", "user-1001", "user-1002"]
    for project in projects:
        assert all_strings(project["config"]), project["name"]
        assert project["config"]["restricted"] == "true"
    bench = by_name(projects, "bench")["config"]
    assert bench["features.networks"] == "false"  # run networks are bridges (default project)
    assert bench["features.images"] == "false"
    assert bench["restricted.devices.proxy"] == "allow"
    assert bench["limits.virtual-machines"] == "4"
    # Two concurrent targets (the default), each a VM (vm_targets) of 2 GiB
    # with a probe client of 512 MiB.
    assert bench["limits.memory"] == f"{2 * (2048 + 512)}MiB"
    # Their root disks with each VM target's 500 MiB state volume.
    assert bench["limits.disk"] == f"{2 * (16384 + 500 + 4096) * 2**20}B"  # exact bytes
    assert bench["restricted.snapshots"] == "block"
    build = by_name(projects, "build")["config"]
    assert build["restricted.networks.access"] == "pinka-build0"
    # Two build instances, each with a VM's 500 MiB state volume.
    assert build["limits.disk"] == f"{2 * (16384 + 500) * 2**20}B"
    assert build["restricted.snapshots"] == "block"
    user = by_name(projects, "user-1001")["config"]
    assert set(user) >= INCUS_USER_KEYS
    assert user["restricted.devices.gpu"] == "block"
    assert user["restricted.idmap.uid"] == "1001"
    assert user["restricted.devices.disk.paths"] == "/home/researcher1"
    assert user["restricted.networks.access"] == "incusbr-1001"
    assert user["limits.memory"] == "8GiB"
    assert user["limits.disk"] == "20GiB"


BUDGET_UNITS = {"MiB": 2**20, "GiB": 2**30, "GB": 10**9, "MB": 10**6, "B": 1}


@pytest.mark.parametrize("vm_targets", [True, False])
@pytest.mark.parametrize(
    "overrides",
    [
        {},
        # decimal units, as Incus reads them: exact bytes, never rounded up
        {
            "incus_build_limits": {"limits.disk": "50GB"},
            "incus_bench_limits": {"limits.disk": "40000000001B"},
            "incus_pool_volume_size": "3500MB",
        },
    ],
)
def test_the_platforms_disk_budget_is_the_rendered_configuration(
    vm_targets: bool, overrides: dict[str, Any]
) -> None:
    # endpoints.toml's [incus] disk budget (the platform role includes
    # budget.yml) equals the projects' limits.disk and the pool's volume.size.
    from pinka_platform import render  # noqa: PLC0415

    facts = run_set_facts(
        ROLES / "incus" / "tasks" / "desired.yml",
        incus_vars(vm_targets=vm_targets, researchers=[], **overrides),
    )
    projects = {p["name"]: p["config"] for p in facts["incus_projects"]}

    def size(text: str) -> int:
        number = text.rstrip("BKMGTi")
        return int(number) * BUDGET_UNITS.get(text[len(number) :], 1)

    budget = {k: int(v) for k, v in facts["incus_disk_budget"].items()}
    assert set(budget) == set(render.INCUS_DISK)
    assert budget == {
        "bench_limits_disk": size(projects["bench"]["limits.disk"]),
        "build_limits_disk": size(projects["build"]["limits.disk"]),
        "pool_volume_size": size(facts["incus_pool"]["config"]["volume.size"]),
    }
    tasks = yaml.safe_load((ROLES / "platform" / "tasks" / "main.yml").read_text())
    block = next(t for t in tasks if "block" in t)["block"]
    include = next(t for t in block if "ansible.builtin.include_role" in t)
    assert include["ansible.builtin.include_role"] == {"name": "incus", "tasks_from": "budget"}
    names = [t["name"] for t in block]
    assert names.index(include["name"]) < names.index("Fetch, stage and validate the release")


def test_profiles(desired: dict[str, Any]) -> None:
    profiles = desired["incus_profiles"]
    keys = [(p["project"], p["name"]) for p in profiles]
    assert keys == [
        ("bench", "target-container"),
        ("bench", "probe-client"),
        ("build", "build"),
        ("bench", "target-vm"),
        ("user-1001", "default"),
        ("user-1002", "default"),
    ]
    for profile in profiles:
        assert all_strings(profile["config"])
        assert profile["devices"]["root"]["pool"] == "pinka-targets"
        if profile["project"] == "bench":
            assert profile["config"]["security.guestapi"] == "false"  # contracts.md#incus
            assert "eth0" not in profile["devices"]  # the platform adds the run network NIC
    assert by_name(profiles, "build")["devices"]["eth0"]["network"] == "pinka-build0"
    vm_root = by_name(profiles, "target-vm")["devices"]["root"]
    assert (vm_root["size"], vm_root["size.state"]) == ("16GiB", "500MiB")
    assert by_name(profiles, "default", "user-1001")["devices"]["root"]["size"] == "4GiB"
    assert by_name(profiles, "default", "user-1002")["devices"]["eth0"]["network"] == "incusbr-1002"


def test_no_vms_without_vm_targets() -> None:
    facts = run_set_facts(
        ROLES / "incus" / "tasks" / "desired.yml", incus_vars(vm_targets=False, researchers=[])
    )
    assert "target-vm" not in [p["name"] for p in facts["incus_profiles"]]
    assert all(p["config"]["limits.virtual-machines"] == "0" for p in facts["incus_projects"])


@pytest.mark.parametrize(
    ("targets", "vm_targets", "memory"), [(1, False, "1536MiB"), (5, True, "12800MiB")]
)
def test_bench_memory_follows_the_concurrent_targets(
    targets: int, vm_targets: bool, memory: str
) -> None:
    """The bench project holds the chosen targets, not a share of the host."""
    facts = run_set_facts(
        ROLES / "incus" / "tasks" / "desired.yml",
        incus_vars(vm_targets=vm_targets, researchers=[], incus_concurrent_targets=targets),
    )
    assert by_name(facts["incus_projects"], "bench")["config"]["limits.memory"] == memory


def test_preseed_lists_everything(desired: dict[str, Any]) -> None:
    text = render_file(INCUS_DIR / "preseed.yaml.j2", desired)
    assert text.startswith("# Managed by pinka (incus/preseed.yaml.j2).")
    preseed = yaml.safe_load(text)
    assert set(preseed) == {"config", "storage_pools", "networks", "projects", "profiles"}
    assert preseed["storage_pools"] == [desired["incus_pool"]]
    assert preseed["projects"] == desired["incus_projects"]
    assert preseed["profiles"] == desired["incus_profiles"]


def test_incus_templates_have_headers() -> None:
    for path in sorted(INCUS_DIR.rglob("*.j2")):
        relative = path.relative_to(INFRA).as_posix()
        assert (
            f"# Managed by pinka ({relative}). Local changes will be overwritten."
            in path.read_text(encoding="utf-8").splitlines()[0]
        )


def test_events_unit() -> None:
    text = render_template("incus", "pinka-incus-events.service.j2", incus_vars())
    unit = configparser.ConfigParser(interpolation=None, strict=True)
    unit.optionxform = str  # type: ignore[assignment,method-assign]
    unit.read_string(text)
    assert unit["Service"]["ExecStart"] == (
        "/usr/bin/incus monitor --all-projects --type=lifecycle --type=logging --format=json"
    )
    assert unit["Service"]["SyslogIdentifier"] == "pinka-incus-events"
    assert unit["Unit"]["PartOf"] == "incus.service"


# --- docker and nvidia_container_toolkit ---------------------------------------


@pytest.mark.parametrize(
    ("setting", "mac", "extra"),
    [
        ("cdi", "apparmor", {"features"}),
        ("runtime", "selinux", {"runtimes", "selinux-enabled"}),
        ("none", "none", set()),
    ],
)
def test_docker_daemon_json(setting: str, mac: str, extra: set[str]) -> None:
    # Only for a Docker pinka installed, and only what it
    # needs; the log driver, cgroup parent and networks are in the Compose
    # projects, and CDI is Docker's default from 28.2 (setting "none").
    variables = role_vars("docker", docker_gpu_setting=setting, pinka_mac=mac)
    data = json.loads(render_template("docker", "daemon.json.j2", variables))
    assert set(data) == {"labels", "data-root", "live-restore"} | extra
    assert any(MANAGED_MARKER in label for label in data["labels"])  # preflight recognizes the file
    assert all("=" in label for label in data["labels"])
    assert data["live-restore"] is True
    assert data["data-root"] == "/var/lib/docker"
    if setting == "cdi":
        assert data["features"] == {"cdi": True}
    if setting == "runtime":
        assert data["runtimes"] == {
            "nvidia": {"path": "/usr/bin/nvidia-container-runtime", "args": []}
        }


def test_docker_pools_avoid_incus_tailscale_and_home_lans() -> None:
    pools = role_vars("docker")["docker_address_pools"]
    reserved = [ipaddress.ip_network(n) for n in ("100.64.0.0/10", "192.168.0.0/16", "10.0.0.0/8")]
    for pool in pools:
        network = ipaddress.ip_network(pool["base"])
        assert not any(network.overlaps(r) for r in reserved), pool
        assert pool["size"] > network.prefixlen


def test_cdi_refresh_drop_in() -> None:
    text = render_template("nvidia_container_toolkit", "nvidia-cdi-refresh-pinka.conf.j2", {})
    unit = configparser.ConfigParser(interpolation=None, strict=True)
    unit.optionxform = str  # type: ignore[assignment,method-assign]
    unit.read_string(text)
    assert unit["Service"]["Environment"] == "NVIDIA_CTK_CDI_GENERATE_MODE=wsl"


@pytest.mark.parametrize("vm_targets", [False, True])
def test_run_acl_admits_only_the_vm_listener(vm_targets: bool) -> None:
    rules = evaluate("incus_run_acl_egress", incus_vars(vm_targets=vm_targets, researchers=[]))
    if not vm_targets:
        assert rules == []
        return
    assert rules == [
        {
            "action": "allow",
            "state": "enabled",
            "protocol": "tcp",
            "destination": "198.18.0.1/32",
            "destination_port": "4317,4318",
            "description": "OTLP to the collector listener for VM targets",
        }
    ]


def docker_task(name: str, tasks: str) -> dict[str, Any]:
    found: dict[str, Any] = next(
        t for t in load_yaml(ROLES / "docker" / "tasks" / tasks) if t["name"] == name
    )
    return found


def inner(value: str) -> str:
    """The expression of a task value that is exactly one ``{{ ... }}``."""
    text = value.strip()
    assert text.startswith("{{") and text.endswith("}}"), text
    return text[2:-2]


@pytest.mark.parametrize(
    ("version", "gpu", "setting"),
    [
        ("Docker version 29.8.2, build 0000000", True, "none"),  # CDI is Docker's default
        ("Docker version 28.2.0, build 0000000", True, "none"),
        ("Docker version 27.5.1, build 0000000", True, "cdi"),  # an older distribution package
        ("Docker version 24.0.9, build 0000000", True, "runtime"),  # no CDI support
        ("Docker version 27.5.1, build 0000000", False, "none"),  # no GPU feature
    ],
)
def test_daemon_json_sets_the_gpu_only_below_cdis_default(
    version: str, gpu: bool, setting: str
) -> None:
    # Docker 28.2 and later enable CDI unless features.cdi is false.
    spec = docker_task("Decide what the daemon configuration needs for the GPU", "install.yml")
    variables = role_vars(
        "docker",
        docker_dockerd_version={"stdout": version},
        pinka_features={"gpu_inference": gpu, "gpu_training": False},
    )
    variables["docker_engine_version"] = evaluate(
        inner(spec["vars"]["docker_engine_version"]), variables
    )
    result = evaluate(inner(spec["ansible.builtin.set_fact"]["docker_gpu_setting"]), variables)
    assert result == setting


@pytest.mark.parametrize(
    ("stdout", "rc", "method", "args", "root"),
    [
        (
            '["/etc/cdi","/var/run/cdi"]\n{"runc":{"path":"runc"}}\n"/var/lib/docker"\n',
            0,
            "cdi",
            ["--device", "nvidia.com/gpu=all"],
            "/var/lib/docker",
        ),
        (
            'null\n{"nvidia":{"path":"/usr/bin/nvidia-container-runtime"}}\n"/srv/docker"\n',
            0,
            "runtime",
            ["--runtime", "nvidia", "--gpus", "all"],
            "/srv/docker",
        ),
        ('[]\n{"runc":{}}\n"/var/lib/docker"\n', 0, "none", [], "/var/lib/docker"),
        ("", 1, "none", [], "/var/lib/docker"),  # no running engine
    ],
)
def test_docker_facts_come_from_docker_info(
    stdout: str, rc: int, method: str, args: list[str], root: str
) -> None:
    # Whoever installed Docker, the lab reads the GPU method and the data root
    # from the running engine (docker role, facts.yml).
    spec = docker_task("Record what Docker offers", "facts.yml")
    scope = role_vars("docker", docker_info_facts={"stdout": stdout, "rc": rc})
    for name in ("docker_facts_parts", "docker_facts_method"):
        scope[name] = evaluate(inner(spec["vars"][name]), scope)
    facts = spec["ansible.builtin.set_fact"]
    assert evaluate(inner(facts["pinka_docker_gpu_method"]), scope) == method
    assert evaluate(inner(facts["pinka_docker_gpu_args"]), scope) == args
    assert evaluate(inner(facts["pinka_docker_data_root"]), scope) == root


OVERRIDES = {
    "defaults": {},
    # another key only: the sizes keep their defaults (merged key by key)
    "another key": {
        "incus_target_vm": {"memory": "4GiB"},
        "incus_target_container": {"cpu": "2"},
        "incus_user_limits": {"limits.cpu": "8"},
    },
    "sizes": {
        "incus_target_vm": {"root_size": "20GiB", "size_state": "1GiB"},
        "incus_target_container": {"root_size": "10GiB"},
        "incus_probe_client": {"root_size": "6GiB"},
        "incus_build_instance": {"size_state": "2GiB"},
        "incus_user_limits": {"limits.disk": "30GiB"},
    },
    "explicit limits": {
        "incus_bench_limits": {"limits.disk": "100GiB"},
        "incus_build_limits": {"limits.disk": "50GiB"},
    },
    # sizes that are not whole MiB: exact bytes on both sides, never rounded up
    "decimal sizes": {
        "incus_target_vm": {"root_size": "3500MB", "size_state": "100000001B"},
        "incus_target_container": {"root_size": "3500MB"},
        "incus_probe_client": {"root_size": "1000000001B"},
        "incus_build_instance": {"root_size": "7000MB", "size_state": "300000001B"},
    },
}


@pytest.mark.parametrize("vm_targets", [True, False])
@pytest.mark.parametrize("case", sorted(OVERRIDES))
def test_the_limits_are_the_parts_preflight_counts(vm_targets: bool, case: str) -> None:
    # The Incus pool holds what the projects' limits allow: the plan writer
    # and the role merge the same host_vars key by key with the defaults, so
    # preflight's parts equal the rendered limits.disk.
    from pinka_installer import plan as plan_mod  # noqa: PLC0415
    from pinka_preflight import requirements  # noqa: PLC0415

    host_vars = OVERRIDES[case]
    data = requirements.load_requirements()
    plan = plan_mod.plan(data["defaults"], {**host_vars, "researchers": [{"name": "r"}]}, {})
    _, parts, _ = requirements.pool_parts(data, plan, {"vm_targets": vm_targets})
    sizes = {p["id"]: p["size_gib"] for p in parts}
    facts = run_set_facts(
        ROLES / "incus" / "tasks" / "desired.yml",
        incus_vars(vm_targets=vm_targets, researchers=[{"name": "researcher1"}], **host_vars),
    )
    projects = {p["name"]: p["config"] for p in facts["incus_projects"]}

    def size(text: str) -> int:
        number = text.rstrip("BKMGTi")
        return int(number) * BUDGET_UNITS.get(text[len(number) :], 1)

    # exact, to the byte: the rendered limits are what preflight's sum counts
    for name in ("bench", "build"):
        assert size(projects[name]["limits.disk"]) == round(sizes[name] * 2**30), name
    assert size(projects["user-1001"]["limits.disk"]) == round(sizes["personal"] * 2**30)
    if case == "another key":
        # the other keys are taken, the sizes are the defaults'
        assert facts["incus_target_vm"]["memory"] == "4GiB"
        assert facts["incus_target_vm"]["root_size"] == "16GiB"
        assert projects["user-1001"]["limits.cpu"] == "8"
        assert projects["user-1001"]["limits.disk"] == "20GiB"


@pytest.mark.parametrize("value", [None, "", "  "])
@pytest.mark.parametrize("name", ["incus_user_limits", "incus_bench_limits", "incus_build_limits"])
def test_a_null_or_empty_limits_disk_is_refused(name: str, value: str | None) -> None:
    from pinka_installer import plan as plan_mod  # noqa: PLC0415
    from pinka_preflight import requirements  # noqa: PLC0415

    data = requirements.load_requirements()
    with pytest.raises(plan_mod.PlanError, match=f"{name}: limits.disk is null or empty"):
        plan_mod.plan(data["defaults"], {name: {"limits.disk": value}}, {})
    tasks = yaml.safe_load((ROLES / "incus" / "tasks" / "budget.yml").read_text())
    refuse = next(t for t in tasks if t["name"] == "Refuse a limits.disk set to null or empty")
    that = refuse["ansible.builtin.assert"]["that"]
    scope = {"item": {"key": name, "value": {"limits.disk": value}}}
    assert not all(evaluate(f"({c})", scope) for c in that)
    scope = {"item": {"key": name, "value": {"limits.disk": "20GiB"}}}
    assert all(evaluate(f"({c})", scope) for c in that)


@pytest.mark.parametrize(
    ("stat", "runs"),
    [
        (f"{100 * 2**30} 2048 512", True),  # 1 MiB allocated of a 100 GiB file: sparse
        (f"{100 * 2**30} {100 * 2**21} 512", False),  # fully allocated
        (f"{120 * 2**30} {100 * 2**21} 512", True),  # grown since (incus storage set ... size=)
    ],
)
def test_the_loop_file_is_preallocated_when_sparse(stat: str, runs: bool) -> None:
    tasks = yaml.safe_load((ROLES / "incus" / "tasks" / "objects.yml").read_text())
    allocate = next(t for t in tasks if t["name"] == "Preallocate the loop pool's file")
    scope = {
        **incus_vars(researchers=[]),
        "incus_pool_source": "loop",
        "incus_pool_file_stat": {"rc": 0, "stdout": stat},
    }
    assert all(evaluate(f"({c})", scope) for c in allocate["when"]) is runs
    size = evaluate(allocate["vars"]["incus_pool_file_size"].strip()[2:-2], scope)
    assert int(size) == int(stat.split(maxsplit=1)[0])


@pytest.mark.parametrize(
    ("source", "mode", "runs"),
    [
        ("loop", "  passdown\n", True),  # LVM's default on a new thin pool
        ("loop", "  nopassdown\n", False),  # already set: no change on a second run
        ("loop", "", True),  # check mode on a new pool: nothing to read yet
        ("device", "  passdown\n", False),  # a device-backed pool keeps LVM's default
    ],
)
def test_a_loop_pool_passes_no_discards_down(source: str, mode: str, runs: bool) -> None:
    tasks = yaml.safe_load((ROLES / "incus" / "tasks" / "objects.yml").read_text())
    read = next(
        t for t in tasks if t["name"] == "Read the discards mode of the loop pool's thin pool"
    )
    assert read["ansible.builtin.command"]["argv"][:4] == ["lvs", "--noheadings", "-o", "discards"]
    assert read["check_mode"] is False
    change = next(t for t in tasks if t["name"] == "Pass no discards down to the loop file")
    assert change["ansible.builtin.command"]["argv"] == [
        "lvchange",
        "--discards",
        "nopassdown",
        "{{ incus_pool_thinpool }}",
    ]
    scope = {
        **incus_vars(researchers=[]),
        "incus_pool_source": source,
        "incus_pool_discards": {"rc": 0, "stdout": mode},
    }
    assert all(evaluate(f"({c})", scope) for c in change["when"]) is runs
    names = [t["name"] for t in tasks]
    assert names.index(read["name"]) > names.index("Create the storage pool {{ incus_pool_name }}")


DISKS = "/var/lib/incus/disks"
NOCOW_BLOCK = "Give a new loop pool's file No_COW on btrfs"


def objects_task(name: str) -> dict[str, Any]:
    """A task of objects.yml by its name, inside a block or not."""
    for entry in yaml.safe_load((ROLES / "incus" / "tasks" / "objects.yml").read_text()):
        if entry["name"] == name:
            return dict(entry)
        for inner in entry.get("block") or []:
            if inner["name"] == name:
                return {**inner, "outer": entry}
    raise AssertionError(name)


def holds(entry: dict[str, Any], scope: dict[str, Any]) -> bool:
    when = entry.get("when", [])
    conditions = [when] if isinstance(when, str) else list(when)
    return all(evaluate(f"({c})", scope) for c in conditions)


def nocow_scope(file_exists: bool, dir_exists: bool, fs: str, attrs: str) -> dict[str, Any]:
    block = objects_task(NOCOW_BLOCK)
    scope: dict[str, Any] = {
        **incus_vars(researchers=[]),
        "incus_pool_source": "loop",
        "incus_pool_paths": {
            "results": [{"stat": {"exists": file_exists}}, {"stat": {"exists": dir_exists}}]
        },
        "incus_pool_dir_fs": {"stdout": f"{fs}\n"},
        "incus_pool_dir_attr": {"stdout": f"{attrs} {DISKS}\n"},
    }
    for key, value in block["vars"].items():
        scope[key] = template_value(value, scope)
    return scope


@pytest.mark.parametrize(
    ("file_exists", "dir_exists", "fs", "attrs", "runs"),
    [
        (False, True, "btrfs", "----------------------", True),  # an existing directory
        (False, False, "btrfs", "", True),  # the role creates the directory
        (False, True, "btrfs", "---------------C------", False),  # No_COW already: no change
        (False, True, "ext2/ext3", "----------------------", False),  # not btrfs: left alone
        (False, True, "xfs", "----------------------", False),
        (True, True, "btrfs", "----------------------", False),  # the file exists: preflight's
    ],
)
def test_a_new_loop_file_on_btrfs_gets_no_cow_from_its_directory(
    file_exists: bool, dir_exists: bool, fs: str, attrs: str, runs: bool
) -> None:
    scope = nocow_scope(file_exists, dir_exists, fs, attrs)
    block = objects_task(NOCOW_BLOCK)
    chattr = objects_task("Set No_COW on the loop pool's directory")
    assert chattr["ansible.builtin.command"]["argv"][:2] == ["chattr", "+C"]
    sets = holds(block, scope) and holds(chattr, scope)
    assert sets is runs
    record = objects_task("Record the loop pool's directory or its No_COW before")
    create = objects_task("Create the loop pool's directory")
    if sets:
        # recorded before it changes: the path it creates, or the attribute's value before
        assert holds(record, scope)
        added = template_value(record["pinka_install_record"]["added"], scope)
        if dir_exists:
            assert added == {"changed": {f"nocow:{DISKS}": "off"}}
            assert not holds(create, scope)
        else:
            assert added == {"paths": [DISKS]}
            assert holds(create, scope)
            assert create["ansible.builtin.file"]["mode"] == "0700"  # as Incus creates it
    names = [t["name"] for t in block["block"]]
    assert names.index(record["name"]) < names.index(create["name"]) < names.index(chattr["name"])
    tasks = [
        t["name"] for t in yaml.safe_load((ROLES / "incus" / "tasks" / "objects.yml").read_text())
    ]
    assert tasks.index(NOCOW_BLOCK) < tasks.index("Create the storage pool {{ incus_pool_name }}")
    assert tasks.index("Create the storage pool {{ incus_pool_name }}") < tasks.index(
        "Preallocate the loop pool's file"
    )


@pytest.mark.parametrize(
    ("configured", "current", "refused"),
    [("150GiB", "181GiB", True), ("181GiB", "181GiB", False), ("200GiB", "181GiB", False)],
)
def test_an_explicit_size_below_the_pools_is_refused(
    configured: str, current: str, refused: bool
) -> None:
    refuse = objects_task("Refuse to lower the loop pool's size")
    scope = {
        **incus_vars(researchers=[]),
        "incus_pool_source": "loop",
        "incus_pool_size": configured,
        "incus_pool_loop_size": configured,
        "incus_pool_size_now": {"rc": 0, "stdout": f"{current}\n"},
    }
    assert holds(refuse, scope)
    passes = all(evaluate(f"({c})", scope) for c in refuse["ansible.builtin.assert"]["that"])
    assert passes is not refused
    message = " ".join(refuse["ansible.builtin.assert"]["fail_msg"].split())
    assert "Incus refuses a smaller size" in message
    # auto never refuses (the role never changes an existing pool's size)
    assert not holds(refuse, {**scope, "incus_pool_size": "auto"})
    create = objects_task("Create the storage pool {{ incus_pool_name }}")
    assert create["pinka_incus_object"]["update"] is False


LIMIT_KEYS = ("limits.memory", "limits.cpu", "limits.instances", "limits.disk")


@pytest.mark.parametrize("vm_targets", [True, False])
@pytest.mark.parametrize("case", sorted(OVERRIDES))
def test_every_argument_templates_before_the_merge(vm_targets: bool, case: str) -> None:
    # Ansible validates a role's arguments, templating each one, before any
    # task runs: every option must template from the defaults and host_vars
    # alone, before budget.yml merges them, to what the projects get after it.
    host_vars = OVERRIDES[case]
    spec = load_yaml(ROLES / "incus" / "meta" / "argument_specs.yml")
    researchers = [{"name": "researcher1"}]
    variables = incus_vars(vm_targets=vm_targets, researchers=researchers, **host_vars)
    before: dict[str, Any] = {}
    for option in spec["argument_specs"]["main"]["options"]:
        if not option.startswith("incus_") and option not in variables:
            continue  # the play passes it (bootstrap's options), not the role
        assert option in variables, option
        before[option] = template_value(variables[option], variables)
    facts = run_set_facts(ROLES / "incus" / "tasks" / "desired.yml", variables)
    projects = {p["name"]: p["config"] for p in facts["incus_projects"]}
    for name in ("bench", "build"):
        expected = {
            **before[f"incus_{name}_limits_defaults"],
            **(before[f"incus_{name}_limits"] or {}),
        }
        rendered = {k: v for k, v in projects[name].items() if k in LIMIT_KEYS}
        assert rendered == {k: str(v) for k, v in expected.items()}, name
    # the profiles merged key by key, computed here, as budget.yml leaves them
    for profile in ("target_container", "target_vm", "probe_client", "build_instance"):
        merged = {
            **variables[f"incus_{profile}_defaults"],
            **(host_vars.get(f"incus_{profile}") or {}),
        }
        assert facts[f"incus_{profile}"] == merged, profile
        assert before["incus_profiles_merged"][profile] == merged, profile
    # the rendered profiles carry the merged sizes
    rendered_profiles = {
        "target_container": by_name(facts["incus_profiles"], "target-container", "bench"),
        "probe_client": by_name(facts["incus_profiles"], "probe-client", "bench"),
        "build_instance": by_name(facts["incus_profiles"], "build", "build"),
    }
    if vm_targets:
        rendered_profiles["target_vm"] = by_name(facts["incus_profiles"], "target-vm", "bench")
    for profile, rendered_profile in rendered_profiles.items():
        sizes = facts[f"incus_{profile}"]
        assert rendered_profile["config"]["limits.memory"] == sizes["memory"], profile
        assert rendered_profile["devices"]["root"]["size"] == sizes["root_size"], profile
    if vm_targets:
        root = rendered_profiles["target_vm"]["devices"]["root"]
        assert root["size.state"] == facts["incus_target_vm"]["size_state"]
    if case == "another key":
        # a target's memory plus the probe client's, for each concurrent target
        target = 4096 if vm_targets else 1024
        assert projects["bench"]["limits.memory"] == f"{2 * (target + 512)}MiB"
