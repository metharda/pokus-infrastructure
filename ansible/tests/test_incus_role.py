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
)
from pinka_preflight.detect.conflicts import MANAGED_MARKER

INCUS_DIR = INFRA / "incus"
FIXTURE = "ubuntu2404-baremetal-nvidia-driver-loaded"
# Config keys incus-user sets on a personal project (Incus 6.0.6 cmd/incus-user/server.go).
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


def test_server_and_pool(desired: dict[str, Any]) -> None:
    assert desired["incus_server_config"] == {"core.https_address": "127.0.0.1:8443"}
    assert desired["incus_pool"] == {
        "name": "pinka-targets",
        "description": "pinka instances",
        "driver": "btrfs",
        "config": {"size": "300GiB"},
    }


@pytest.mark.parametrize(
    ("backend", "source", "driver", "config"),
    [
        ("lvm", "loop", "lvm", {"size": "300GiB", "lvm.use_thinpool": "true"}),
        ("btrfs", "device", "btrfs", {"source": "/dev/disk/by-id/example-disk"}),
    ],
)
def test_pool_variants(backend: str, source: str, driver: str, config: dict[str, str]) -> None:
    variables = incus_vars(
        researchers=[], incus_pool_source=source, incus_pool_device="/dev/disk/by-id/example-disk"
    )
    variables["host_profile"] = {**variables["host_profile"], "storage_backend": backend}
    facts = run_set_facts(ROLES / "incus" / "tasks" / "desired.yml", variables)
    assert facts["incus_pool"]["driver"] == driver
    assert facts["incus_pool"]["config"] == config


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
    assert by_name(projects, "build")["config"]["restricted.networks.access"] == "pinka-build0"
    user = by_name(projects, "user-1001")["config"]
    assert set(user) >= INCUS_USER_KEYS
    assert user["restricted.devices.gpu"] == "block"
    assert user["restricted.idmap.uid"] == "1001"
    assert user["restricted.devices.disk.paths"] == "/home/researcher1"
    assert user["restricted.networks.access"] == "incusbr-1001"
    assert user["limits.memory"] == "8GiB"


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
    """The bench project holds the chosen targets, not a share of the host (§2.3)."""
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
    # BUILD_PROMPT §7.6: only for a Docker pinka installed, and only what it
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
    # BUILD_PROMPT §7.6: Docker 28.2 and later enable CDI unless features.cdi is false.
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
