"""What of the host the infra containers see (compose/*/compose.yml).

Docker runs without user namespace remapping, so UID 0 in a container is the
host's root: without capabilities it cannot bypass permission bits, but it
owns every root-owned file and socket, the restricted area and the secrets
included. A container of infra that mounts one of pinka's paths, or a parent
of one, therefore either declares a non-root user or masks each of those
paths with an empty tmpfs where its view of the host would show it.
"""

from __future__ import annotations

import posixpath
import re
from typing import Any

import pytest

from ansible_testlib import (
    GROUP_VARS,
    INFRA,
    ROLES,
    ansible_vars,
    load_yaml,
    render_template,
    template_value,
)

PROJECTS = sorted(p.parent.name for p in (INFRA / "compose").glob("*/compose.yml"))
OBSERVABILITY = INFRA / "compose" / "observability" / "compose.yml"
TASKS = ROLES / "observability" / "tasks" / "main.yml"
# pinka's own paths on the host, where they really are: the lab's root, the
# settings and secrets, the state, what an uninstall keeps, and the runtime
# directory with the sockets.
PINKA_PATHS = (
    "${PINKA_ROOT_REAL}",
    "${ETC_PINKA_REAL}",
    "${VAR_LIB_PINKA_REAL}",
    "${VAR_LIB_PINKA_KEPT_REAL}",
    "${RUN_PINKA_REAL}",
)
# The host paths that the variables in a mount stand for: the default
# layout; the lab's root inside /var/lib and Docker's data root elsewhere;
# the data root inside the lab's root; the lab's root and /var/lib behind
# links; /var/lib/pinka behind a link out of /var/lib (the real paths are
# what the observability role renders).
LAYOUTS = [
    {"PINKA_ROOT": "/srv/pinka", "DOCKER_DATA_ROOT": "/var/lib/docker"},
    {"PINKA_ROOT": "/var/lib/lab", "DOCKER_DATA_ROOT": "/data/docker"},
    {"PINKA_ROOT": "/data/lab", "DOCKER_DATA_ROOT": "/data/lab/docker"},
    {
        "PINKA_ROOT": "/srv/pinka",
        "PINKA_ROOT_REAL": "/data/pinka",
        "VAR_LIB_REAL": "/data/var-lib",
        "DOCKER_DATA_ROOT": "/data/var-lib/docker",
    },
    {
        "PINKA_ROOT": "/srv/pinka",
        "VAR_LIB_PINKA_REAL": "/data/pinka-state",
        "DOCKER_DATA_ROOT": "/var/lib/docker",
    },
]
# What the observability role renders for the journal collector's sources:
# the host's own directory, or the empty one where the host lacks it.
JOURNAL_SOURCES = {
    "JOURNAL_PERSISTENT_DIR": ("/var/log/journal",),
    "JOURNAL_VOLATILE_DIR": ("/run/log/journal",),
    "LIB64_DIR": ("/lib64",),
    "USR_LIB64_DIR": ("/usr/lib64",),
}
VARIABLE = re.compile(r"\$\{([A-Z0-9_]+)(?::\?[^}]*)?\}")
# /var/run is a link to /run on every supported family.
ALIASES = {"/var/run": "/run"}


def values_of(layout: dict[str, str]) -> dict[str, tuple[str, ...]]:
    var_lib = layout.get("VAR_LIB_REAL", "/var/lib")
    real = {
        "PINKA_ROOT_REAL": layout["PINKA_ROOT"],
        "ETC_PINKA_REAL": "/etc/pinka",
        "VAR_LIB_REAL": var_lib,
        "RUN_PINKA_REAL": "/run/pinka",
        "VAR_LIB_PINKA_REAL": f"{var_lib}/pinka",
        "VAR_LIB_PINKA_KEPT_REAL": f"{var_lib}/pinka-kept",
    }
    empty = f"{layout['PINKA_ROOT']}/services/observability/empty"
    return {
        **{key: (value,) for key, value in (real | layout).items()},
        **{key: (*paths, empty) for key, paths in JOURNAL_SOURCES.items()},
    }


def substitute(text: str, layout: dict[str, str]) -> list[str]:
    """Every host path a source or target can be in this layout.

    Args:
        text: The source or target as the Compose file has it.
        layout: The values of the variables a mount may use.

    Returns:
        The paths, one per value of the variables it uses.
    """
    values = values_of(layout)
    names = VARIABLE.findall(text)
    unknown = sorted(set(names) - set(values))
    assert not unknown, f"no host path known for {unknown} in {text!r}"
    results = [text]
    for name in names:
        results = [replace(r, name, value) for r in results for value in values[name]]
    for result in results:
        # Any other form ($X, ${X:-...}) would stay unresolved and unchecked.
        assert "$" not in result, f"a variable this test does not resolve: {text!r}"
    # Docker cleans a path; normpath keeps two leading slashes.
    return [re.sub(r"^/+", "/", posixpath.normpath(r)) for r in results]


def replace(text: str, name: str, value: str) -> str:
    def one(match: re.Match[str]) -> str:
        return value if match.group(1) == name else match.group(0)

    return VARIABLE.sub(one, text)


def host_path(source: str, project: str, layout: dict[str, str]) -> list[str]:
    if source.startswith("./"):
        source = f"${{PINKA_ROOT}}/services/{project}/{source[2:]}"
    assert source.startswith(("/", "${")), f"a named volume or a relative path: {source!r}"
    paths = substitute(source, layout)
    return [next((t + p[len(a) :] for a, t in ALIASES.items() if within(p, a)), p) for p in paths]


def within(path: str, parent: str) -> bool:
    return path == parent or path.startswith(parent.rstrip("/") + "/")


def mounts(service: dict[str, Any]) -> list[tuple[str, str, str]]:
    """The service's mounts as (type, source, target), short and long syntax."""
    found = []
    for volume in service.get("volumes", []):
        if isinstance(volume, str):
            # SOURCE:TARGET[:OPTIONS], a ${...:?} variable kept whole.
            match = re.fullmatch(r"((?:\$\{[^}]*\}|[^:$])+):((?:\$\{[^}]*\}|[^:$])+)(:.*)?", volume)
            assert match, volume
            found.append(("bind", match.group(1), match.group(2)))
        else:
            found.append((volume["type"], volume.get("source", ""), volume["target"]))
    tmpfs = service.get("tmpfs", [])
    for entry in [tmpfs] if isinstance(tmpfs, str) else tmpfs:
        found.append(("tmpfs", "", entry.split(":")[0]))
    return found


def compose_env() -> dict[str, str]:
    """The observability role's variables of its Compose file."""
    (start,) = [t for t in load_yaml(TASKS) if t["name"] == "Start the observability services"]
    env: dict[str, str] = start["vars"]["compose_extra_env"]
    return env


def uid_of(service: dict[str, Any]) -> str:
    """The user part of `user`, a `${...}` variable kept whole."""
    match = re.match(r"(\$\{[^}]*\}|[^:]*)", str(service.get("user", "")))
    assert match
    return match.group(1)


def declares_a_non_root_user(service: dict[str, Any]) -> bool:
    uid = uid_of(service)
    if uid in ("", "0", "root"):
        return False
    match = re.fullmatch(r"\$\{([A-Z_]+_UID):\?\}", uid)
    if match:
        # The UID of one of infra's accounts (test_the_uid_variables_are_infra_accounts).
        return match.group(1) in compose_env()
    return uid.isdigit() and int(uid) != 0


def test_the_uid_variables_are_infra_accounts() -> None:
    variables = ansible_vars(GROUP_VARS, ROLES / "observability" / "vars" / "main.yml")
    accounts = variables["observability_container_accounts"]
    infra = {a["name"] for a in variables["infra_accounts"]}
    uids = {key: value for key, value in compose_env().items() if key.endswith("_UID")}
    assert {"OTELCOL_UID", "JOURNALD_UID"} <= set(uids)
    for name, value in uids.items():
        match = re.fullmatch(r"\{\{ observability_ids\.([a-z_]+)\.uid \}\}", value)
        assert match, (name, value)
        assert accounts[match.group(1)] in infra, name
    # The journal collector's user and its state directory's owner are the same account.
    assert uids["JOURNALD_UID"] == "{{ observability_ids.otel_journald.uid }}"
    dirs = {d["name"]: d for d in variables["observability_state_dirs"]}
    assert dirs["otelcol-journald"] == {"name": "otelcol-journald", "container": "otel_journald"}
    services = load_yaml(OBSERVABILITY)["services"]
    assert uid_of(services["otel_journald"]) == "${JOURNALD_UID:?}"
    assert declares_a_non_root_user(services["otel_journald"])
    assert not declares_a_non_root_user(services["cadvisor"])


@pytest.mark.parametrize("layout", LAYOUTS, ids=lambda layout: str(sorted(layout.values())))
def test_a_root_container_that_mounts_a_parent_of_pinkas_paths_masks_each(
    layout: dict[str, str],
) -> None:
    pinka = [p for path in PINKA_PATHS for p in substitute(path, layout)]
    seen = set()
    for project in PROJECTS:
        compose = load_yaml(INFRA / "compose" / project / "compose.yml")
        assert not compose.get("volumes"), f"{project}: named volumes"
        for name, service in compose["services"].items():
            found = mounts(service)
            masks = {
                p
                for kind, _, target in found
                if kind == "tmpfs"
                for p in substitute(target, layout)
            }
            assert "/" not in masks, (project, name)
            for kind, source, target in found:
                assert kind in ("bind", "tmpfs"), (project, name, kind)
                if kind != "bind":
                    continue
                for source_path in host_path(source, project, layout):
                    # A bind that lands inside a mask brings back what it
                    # mounts: never one of pinka's paths or a part of one,
                    # but for Docker's data root, which cAdvisor keeps on
                    # purpose (also where it lies inside the lab's root).
                    data_root = name == "cadvisor" and (source, target) == (
                        "${DOCKER_DATA_ROOT:?}",
                        "/rootfs${DOCKER_DATA_ROOT:?}",
                    )
                    if not declares_a_non_root_user(service) and not data_root:
                        for view in substitute(target, layout):
                            if any(within(view, m) for m in masks):
                                assert not any(within(source_path, p) for p in pinka), (
                                    project,
                                    name,
                                    source,
                                    view,
                                )
                    for path in pinka:
                        if not within(path, source_path):
                            continue
                        seen.add(name)
                        if declares_a_non_root_user(service):
                            continue
                        # Where the container would see it: masked there, or
                        # above it (the host's /var/lib for /var/lib/pinka),
                        # by a mask inside this mount's own view, so that a
                        # deeper bind of the path cannot bring it back.
                        rest = posixpath.relpath(path, source_path)
                        for view in substitute(target, layout):
                            shown = posixpath.normpath(posixpath.join(view, rest))
                            covered = [
                                m
                                for m in masks
                                if within(shown, m) and within(m, view) and m != view
                            ]
                            assert covered, (project, name, source, path, shown)
    # The containers that read the host's root: the test sees them.
    assert seen == {"node_exporter", "cadvisor"}


def test_the_journal_collector_mounts_only_the_journal_and_the_program_and_library_dirs() -> None:
    service = load_yaml(OBSERVABILITY)["services"]["otel_journald"]
    # Its own UID variable (the collectors' account), with the host's
    # systemd-journal group as its only group.
    assert service["user"] == "${JOURNALD_UID:?}:${SYSTEMD_JOURNAL_GID:?}"
    assert "cap_add" not in service and "group_add" not in service
    assert service["cap_drop"] == ["ALL"]
    # A setuid file under /usr gives it nothing.
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert [v for v in service["volumes"] if isinstance(v, str)] == [
        "./config/otel:/etc/otelcol:ro",
        "${PINKA_ROOT:?}/state/otelcol-journald:/var/lib/otelcol/journald",
    ]
    host = [v for v in service["volumes"] if not isinstance(v, str)]
    assert [(v["source"], v["target"]) for v in host] == [
        ("${JOURNAL_PERSISTENT_DIR:?}", "/var/log/journal"),
        ("${JOURNAL_VOLATILE_DIR:?}", "/run/log/journal"),
        ("/etc/machine-id", "/etc/machine-id"),
        ("/usr/bin", "/usr/bin"),
        ("/usr/lib", "/usr/lib"),
        ("${USR_LIB64_DIR:?}", "/usr/lib64"),
        ("/lib", "/lib"),
        ("${LIB64_DIR:?}", "/lib64"),
        ("/etc/ld.so.cache", "/etc/ld.so.cache"),
    ]
    for volume in host:
        # Read-only, and never created by Docker (a /var/log/journal it
        # created would turn the journal persistent, root:root).
        assert volume == {
            "type": "bind",
            "read_only": True,
            "bind": {"create_host_path": False},
            "source": volume["source"],
            "target": volume["target"],
        }
    assert "tmpfs" not in service


def test_cadvisor_masks_pinkas_paths_and_keeps_only_the_data_root_of_var_lib() -> None:
    compose = load_yaml(OBSERVABILITY)
    service = compose["services"]["cadvisor"]
    assert "user" not in service  # root: its image sets no user
    assert [v for v in service["volumes"] if isinstance(v, str)] == [
        "/:/rootfs:ro",
        "/var/run:/var/run:ro",
        "/sys:/sys:ro",
        "${DOCKER_DATA_ROOT:?}:/var/lib/docker:ro",
        "/dev/disk:/dev/disk:ro",
    ]
    tmpfs = [v for v in service["volumes"] if not isinstance(v, str) and v["type"] == "tmpfs"]
    assert [v["target"] for v in tmpfs] == [
        "/rootfs${PINKA_ROOT_REAL:?}",
        "/rootfs${ETC_PINKA_REAL:?}",
        "/rootfs${VAR_LIB_REAL:?}",
        "/rootfs${VAR_LIB_PINKA_REAL:?}",
        "/rootfs${VAR_LIB_PINKA_KEPT_REAL:?}",
        "/rootfs${RUN_PINKA_REAL:?}",
        "/var/run/pinka",
    ]
    for volume in tmpfs:
        # Empty and small; writable, so that Docker can create the data
        # root's mount point inside /rootfs/var/lib, mode 0555.
        assert volume == {
            "type": "tmpfs",
            "tmpfs": {"size": 4096, "mode": 0o555},
            "target": volume["target"],
        }
    binds = [v for v in service["volumes"] if not isinstance(v, str) and v["type"] == "bind"]
    assert binds == [
        {
            "type": "bind",
            "read_only": True,
            "bind": {"create_host_path": False},
            "source": "${DOCKER_DATA_ROOT:?}",
            "target": "/rootfs${DOCKER_DATA_ROOT:?}",
        }
    ]
    assert service["tmpfs"] == ["/tmp"]  # noqa: S108 - the Compose file's value, no file is made
    assert compose["x-mask"] == {"type": "tmpfs", "tmpfs": {"size": 4096, "mode": 0o555}}


def test_the_masks_are_rendered_at_the_real_paths() -> None:
    tasks = {t["name"]: t for t in load_yaml(TASKS)}
    task = tasks["Find the real paths of what cAdvisor's view masks"]
    assert task["ansible.builtin.command"]["argv"] == [
        "realpath",
        "--canonicalize-missing",
        "--",
        "{{ pinka_root }}",
        "/etc/pinka",
        "/var/lib",
        "/run/pinka",
        "/var/lib/pinka",
        "/var/lib/pinka-kept",
    ]
    assert task["check_mode"] is False and task["changed_when"] is False
    env = compose_env()
    names = [
        "PINKA_ROOT_REAL",
        "ETC_PINKA_REAL",
        "VAR_LIB_REAL",
        "RUN_PINKA_REAL",
        "VAR_LIB_PINKA_REAL",
        "VAR_LIB_PINKA_KEPT_REAL",
    ]
    for index, name in enumerate(names):
        assert env[name] == f"{{{{ observability_mask_paths.stdout_lines[{index}] }}}}"
    # Distinct, none of them / or the data root (Docker refuses two mounts at one place).
    check = tasks["Check that cAdvisor's masks lie apart"]["ansible.builtin.assert"]["that"]
    variables = {"pinka_docker_data_root": "/var/lib/docker"}
    lines = [
        "/srv/pinka",
        "/etc/pinka",
        "/var/lib",
        "/run/pinka",
        "/var/lib/pinka",
        "/var/lib/pinka-kept",
    ]

    def holds(paths: list[str]) -> bool:
        mask = {"observability_mask_paths": {"stdout_lines": paths}}
        return all(template_value("{{ " + c + " }}", variables | mask) for c in check)

    assert holds(lines)
    assert not holds([*lines[:5], "/srv/pinka"])
    assert not holds([*lines[:5], "/"])
    assert not holds([*lines[:5], "/var/lib/docker"])
    assert not holds(lines[:5])
    order = list(tasks)
    assert order.index("Read where Docker keeps its data") < order.index(
        "Check that cAdvisor's masks lie apart"
    )
    assert tasks["Read where Docker keeps its data"]["ansible.builtin.include_role"] == {
        "name": "docker",
        "tasks_from": "facts",
    }


@pytest.mark.parametrize(
    ("isdir", "source"),
    [
        (True, "/var/log/journal"),
        (False, "/srv/pinka/services/observability/empty"),
        (None, "/srv/pinka/services/observability/empty"),
    ],
)
def test_a_directory_the_host_lacks_is_mounted_from_the_empty_directory(
    isdir: bool | None, source: str
) -> None:
    tasks = load_yaml(TASKS)
    (task,) = [
        t for t in tasks if t["name"] == "Mount the empty directory in place of each the host lacks"
    ]
    expression = task["ansible.builtin.set_fact"]["observability_host_dirs"]
    variables = ansible_vars(
        GROUP_VARS,
        ROLES / "observability" / "defaults" / "main.yml",
        ROLES / "observability" / "vars" / "main.yml",
    )
    stat = {"exists": isdir is not None} | ({} if isdir is None else {"isdir": isdir})
    item = {"item": "/var/log/journal", "stat": stat}
    first = template_value(expression, variables | {"item": item})
    assert first == {"/var/log/journal": source}
    second = template_value(
        expression,
        variables
        | {
            "observability_host_dirs": first,
            "item": {"item": "/lib64", "stat": {"exists": True, "isdir": True}},
        },
    )
    assert second == {"/var/log/journal": source, "/lib64": "/lib64"}
    (stat_task,) = [
        t
        for t in tasks
        if t["name"] == "Find the journal's and the loader's directories the host has"
    ]
    assert stat_task["ansible.builtin.stat"]["follow"] is True  # /lib64 is a link on merged /usr
    assert variables["observability_journal_optional_dirs"] == [
        "/var/log/journal",
        "/run/log/journal",
        "/lib64",
        "/usr/lib64",
    ]
    # The empty directory exists, 0555 root, before the services start.
    (dirs,) = [t for t in tasks if t["name"] == "Create the configuration directories"]
    assert {"path": "{{ observability_empty_dir }}", "mode": "0555"} in dirs["loop"]
    names = [t["name"] for t in tasks]
    assert names.index("Create the configuration directories") < names.index(
        "Start the observability services"
    )


def test_the_stored_position_changes_owner_without_following_a_link() -> None:
    """The collector owns its state directory: no task recurses into it or follows its links."""
    tasks = {t["name"]: t for t in load_yaml(TASKS)}
    find = tasks["Find what the journal collector stored as root"]["ansible.builtin.find"]
    assert find == {
        "paths": "{{ pinka_root }}/state/otelcol-journald",
        "file_type": "file",
        "hidden": True,
        "recurse": False,
    }
    # Not in check mode on a fresh host, where the directory is not there yet;
    # a link in its place is no directory (stat does not follow it).
    found = tasks["Find what the journal collector stored as root"]
    assert found["when"] == "observability_journald_dir.stat.isdir | default(false)"
    assert tasks["Find the journal collector's state directory"]["ansible.builtin.stat"] == {
        "path": "{{ pinka_root }}/state/otelcol-journald"
    }
    give = tasks["Give it to the journal collector's account"]
    assert give["ansible.builtin.file"] == {
        "path": "{{ item.path }}",
        "state": "file",
        "follow": False,
        "owner": "{{ observability_ids.otel_journald.uid or omit }}",
        "group": "{{ observability_journal_gid or omit }}",
    }
    assert give["loop"] == (
        "{{ observability_journald_state.files | default([])"
        " | selectattr('uid', 'equalto', 0) | list }}"
    )

    def walk(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        found = []
        for task in items:
            found.append(task)
            for key in ("block", "rescue", "always"):
                found += walk(task.get(key) or [])
        return found

    for path in sorted((ROLES / "observability" / "tasks").rglob("*.yml")):
        for task in walk(load_yaml(path) or []):
            assert not (task.get("ansible.builtin.file") or {}).get("recurse"), (path, task)


def test_the_journal_group_is_the_hosts() -> None:
    tasks = {t["name"]: t for t in load_yaml(TASKS)}
    expression = tasks["Find the journal's group"]["ansible.builtin.set_fact"][
        "observability_journal_gid"
    ]
    variables = ansible_vars(ROLES / "observability" / "vars" / "main.yml")
    groups = {"systemd-journal": ["x", "190", ""], "adm": ["x", "4", ""]}
    facts = {"ansible_facts": {"getent_group": groups}}
    assert str(template_value(expression, variables | facts)).strip() == "190"
    missing = {"ansible_facts": {"getent_group": {"adm": ["x", "4", ""]}}}
    assert str(template_value(expression, variables | missing)).strip() == ""
    check = tasks["Check that the journal's group exists"]
    assert check["ansible.builtin.assert"]["that"] == "observability_journal_gid | length > 0"
    now = tasks["Create pinka's runtime directory now"]
    assert now["ansible.builtin.command"]["argv"] == [
        "systemd-tmpfiles",
        "--create",
        "/etc/tmpfiles.d/pinka-observability.conf",
    ]
    assert now["changed_when"] == "observability_runtime_tmpfiles is changed"
    assert now["when"] == "not ansible_check_mode"


def test_the_runtime_directory_exists_on_every_node_before_docker_starts() -> None:
    tasks = load_yaml(TASKS)
    names = [t["name"] for t in tasks]
    (template,) = [t for t in tasks if t["name"] == "Recreate pinka's runtime directory at boot"]
    assert "when" not in template
    assert (
        template["ansible.builtin.template"]["dest"] == "/etc/tmpfiles.d/pinka-observability.conf"
    )
    assert names.index("Create pinka's runtime directory now") < names.index(
        "Start the observability services"
    )
    text = render_template("observability", "pinka-observability.tmpfiles.conf.j2", {})
    assert text.startswith(
        "# Managed by pinka (ansible/roles/observability/templates/"
        "pinka-observability.tmpfiles.conf.j2). Local changes will be overwritten.\n"
    )
    lines = [line for line in text.splitlines() if line and not line.startswith("#")]
    assert lines == ["d /run/pinka 0755 root root -"]


def test_the_journal_is_where_pinkas_settings_put_it_before_the_collector_starts() -> None:
    tasks = {t["name"]: t for t in load_yaml(ROLES / "base" / "tasks" / "main.yml")}
    configure = tasks["Configure journald (persistent, size-limited)"]
    assert "notify" not in configure and configure["register"] == "base_journald"
    assert tasks["Restart journald with these settings"]["when"] == "base_journald is changed"
    # A dangling link counts as absent, a link to a directory as there.
    assert tasks["Find the persistent journal"]["ansible.builtin.stat"] == {
        "path": "/var/log/journal",
        "follow": True,
    }
    flush = tasks["Move the journal to persistent storage"]
    assert flush["ansible.builtin.command"]["argv"] == ["journalctl", "--flush"]
    assert flush["when"] == "not base_journal_before.stat.exists"
    # This machine's directory, which journald names after /etc/machine-id.
    assert tasks["Read the machine ID"]["ansible.builtin.slurp"] == {"src": "/etc/machine-id"}
    name = tasks["Name this machine's journal directory"]["ansible.builtin.set_fact"]
    assert name["base_journal_dir"] == (
        "/var/log/journal/{{ base_machine_id.content | b64decode | trim }}"
    )
    machine = {"base_machine_id": {"content": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAK"}}
    assert template_value(name["base_journal_dir"], machine) == (
        "/var/log/journal/00000000000000000000000000000000"
    )
    assert tasks["Find this machine's persistent journal"]["ansible.builtin.stat"] == {
        "path": "{{ base_journal_dir }}",
        "follow": True,
    }
    modes = tasks["Give this machine's persistent journal journald's group and modes"]
    assert modes["ansible.builtin.command"]["argv"] == "{{ item }}"
    # Only this machine's directory: tmpfiles.d does not follow a link at
    # /var/log/journal and fails on it. Files journald made before keep
    # root's group: a rotation starts new ones.
    assert modes["loop"] == [
        ["systemd-tmpfiles", "--create", "--prefix", "{{ base_journal_dir }}"],
        ["journalctl", "--rotate"],
    ]
    # Whenever the directory lacks journald's group or its setgid bit, also
    # where journald's restart or a person created it.
    assert modes["when"] == (
        "base_journal.stat.exists and (base_journal.stat.gr_name | default('') != "
        "'systemd-journal' or not base_journal.stat.isgid)"
    )
    # The run fails where the host's tmpfiles.d does not fix it, instead of
    # trying again on every run; neither in check mode, where nothing is fixed.
    again = tasks["Find this machine's persistent journal again"]
    assert again["ansible.builtin.stat"] == {"path": "{{ base_journal_dir }}", "follow": True}
    assert again["when"] == "not ansible_check_mode"
    check = tasks["Check that this machine's persistent journal has journald's group"]
    assert check["when"] == "not ansible_check_mode"
    assert check["ansible.builtin.assert"]["that"] == (
        "not base_journal_now.stat.exists or (base_journal_now.stat.gr_name | default('') == "
        "'systemd-journal' and base_journal_now.stat.isgid | bool)"
    )
    message = " ".join(check["ansible.builtin.assert"]["fail_msg"].split())
    assert "needs the group systemd-journal and the setgid bit" in message
    assert "systemd-tmpfiles --create --prefix {{ base_journal_dir }}" in message
    order = list(tasks)
    assert (
        order.index("Restart journald with these settings")
        < order.index("Find the persistent journal")
        < order.index("Move the journal to persistent storage")
        < order.index("Read the machine ID")
        < order.index("Name this machine's journal directory")
        < order.index("Find this machine's persistent journal")
        < order.index("Give this machine's persistent journal journald's group and modes")
        < order.index("Find this machine's persistent journal again")
        < order.index("Check that this machine's persistent journal has journald's group")
    )
    handlers = load_yaml(ROLES / "base" / "handlers" / "main.yml")
    assert "Restart journald" not in [h["name"] for h in handlers]


# PostgreSQL's socket directories (postgres role): 0750, owned by the
# container's postgres UID, with the group that may connect: the service
# directory's is the platform's <platform>-db, or root without a platform.
SOCKET_DIRS = {"/run/pinka/postgres": None, "/run/pinka/postgres-reader": "pinka"}
POSTGRES_UID = "999"
# PostgreSQL's container sets no user: its image's entrypoint runs the
# server as postgres (postgres_container_uid).
IMAGE_USERS = {"core/postgres": POSTGRES_UID}
# The groups the Compose files' GID variables stand for (compose role).
GROUPS = {
    "${PINKA_GID:?}": "pinka",
    "${PINKA_QUERY_GID:?}": "pinka-query",
    "${SYSTEMD_JOURNAL_GID:?}": "systemd-journal",
    "0": "root",
    "65534": "nogroup",
    "999": "postgres",
}


def account_groups() -> dict[str, str]:
    """The observability role's GID variables of infra's accounts' own groups."""
    # Each infra account's primary group is a group of its own name.
    users = load_yaml(ROLES / "users_groups" / "tasks" / "main.yml")
    (create,) = [t for t in users if t["name"] == "Create the infra accounts"]
    assert create["ansible.builtin.user"]["group"] == "{{ item.name }}"
    found = {}
    for key, value in compose_env().items():
        match = re.fullmatch(r"\{\{ observability_ids\.([a-z_]+)\.gid \}\}", value)
        if key.endswith("_GID") and match:
            found[f"${{{key}:?}}"] = f"the own group of {match.group(1)}"
    return found


def groups_of(service: dict[str, Any], service_group: str) -> set[str]:
    """The groups a container's processes hold: its user's GID and group_add."""
    user = str(service.get("user", ""))
    match = re.fullmatch(r"(\$\{[^}]*\}|[^:]*)(?::(.*))?", user)
    assert match, user
    gid = match.group(2) or "0"  # no user: the image's root (or root's group)
    names = [gid, *service.get("group_add", [])]
    found = set()
    for name in names:
        if name == "${POSTGRES_SOCKET_GID:?}":
            found.add(service_group)
        elif name in GROUPS:
            found.add(GROUPS[name])
        else:
            # An infra account's own group, which opens nothing here; any
            # other variable fails until this test knows what it stands for.
            assert name in account_groups(), name
            found.add(account_groups()[name])
    return found


def binds(service: dict[str, Any]) -> list[tuple[str, str, str]]:
    return [m for m in mounts(service) if m[0] == "bind"]


def sees(service: dict[str, Any], project: str, layout: dict[str, str], directory: str) -> str:
    """How a container sees a host directory: "own" mount, unmasked "parent", or ""."""
    found = mounts(service)
    masks = {p for kind, _, target in found if kind == "tmpfs" for p in substitute(target, layout)}
    how = ""
    for kind, source, target in found:
        if kind != "bind":
            continue
        for source_path in host_path(source, project, layout):
            if within(source_path, directory):
                return "own"
            if within(directory, source_path):
                rest = posixpath.relpath(directory, source_path)
                for view in substitute(target, layout):
                    shown = posixpath.normpath(posixpath.join(view, rest))
                    if not any(within(shown, m) and within(m, view) and m != view for m in masks):
                        how = "parent"
    return how


@pytest.mark.parametrize("layout", LAYOUTS, ids=lambda layout: str(sorted(layout.values())))
@pytest.mark.parametrize("platform", [True, False], ids=["platform", "no-platform"])
def test_exactly_the_containers_the_contract_names_reach_the_socket_directories(
    platform: bool, layout: dict[str, str]
) -> None:
    """Who reaches PostgreSQL's two socket directories, as the contract lists them.

    A container reaches a directory when it mounts it (or a part of it), or
    when it can enter it, as its owner or by the directory's group, through
    a parent it mounts unmasked or, where it sees the host's processes (the
    host's PID namespace, or the host's /proc through a bind of /), through
    /proc/<pid>/root of a container with its UID that sees the directory.
    Every container that mounts a parent drops all capabilities and adds
    none (with Docker's default ones a root container would enter whatever
    the mode; the masks test covers root). A container without `user` counts
    as root:root, its image's; one that sets `user` names its group, so no
    supplementary group of its image comes in. A new one fails here; the
    contract's rows (test_core.py) must then name it.
    """
    service_group = "demo-db" if platform else "root"
    defaults = ansible_vars(ROLES / "postgres" / "defaults" / "main.yml")
    assert str(defaults["postgres_container_uid"]) == POSTGRES_UID
    reach: dict[str, set[str]] = {path: set() for path in SOCKET_DIRS}
    own: dict[str, list[tuple[str, str]]] = {}  # UID: (container, directory) by own mount
    host_pid: list[tuple[str, str, set[str]]] = []
    for project in PROJECTS:
        compose = load_yaml(INFRA / "compose" / project / "compose.yml")
        for name, service in compose["services"].items():
            uid = uid_of(service) or IMAGE_USERS.get(f"{project}/{name}", "0")
            groups = groups_of(service, service_group)
            assert service.get("pid") in (None, "host"), (project, name)
            sources = [p for _, s, _ in binds(service) for p in host_path(s, project, layout)]
            if service.get("pid") == "host" or "/" in sources:
                host_pid.append((f"{project}/{name}", uid, groups))
            for directory, own_group in SOCKET_DIRS.items():
                group = own_group or service_group
                if any(within(directory, p) and p != directory for p in sources):
                    # A parent, masked or not: no capability to bypass the mode.
                    assert service.get("cap_drop") == ["ALL"], (project, name)
                    assert not service.get("cap_add") and not service.get("privileged"), name
                how = sees(service, project, layout, directory)
                if how == "own":
                    # Its own mount: it must also be able to enter.
                    assert uid == POSTGRES_UID or group in groups, (name, directory)
                if how:
                    own.setdefault(uid, []).append((f"{project}/{name}", directory))
                if how and (uid == POSTGRES_UID or group in groups):
                    reach[directory].add(f"{project}/{name}")
    for name, uid, groups in host_pid:
        for other, directory in own.get(uid, []):
            group = SOCKET_DIRS[directory] or service_group
            if other != name and (uid == POSTGRES_UID or group in groups):
                reach[directory].add(name)
    assert reach == {
        "/run/pinka/postgres": {"core/postgres", "core/postgres_exporter"},
        "/run/pinka/postgres-reader": {"core/postgres", "observability/grafana"},
    }
