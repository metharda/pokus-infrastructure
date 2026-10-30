"""Rendering from the profiles: golden files, units, accounts, isolation."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from platform_testlib import CONTRACT, FIRST_PLATFORM, GOLDEN, VALID, manifest

from pinka_platform import accounts, agentpkg, database, layout, names, rendering, units

PORTS = {
    "otlp_grpc": 4317,
    "otlp_http": 4318,
    "opa": 8181,
    "incus_https": 8443,
    "grafana": 18700,
    "prometheus": 18701,
    "loki": 18702,
    "tempo": 18703,
    "vllm": 18704,
    "gateway_adhoc": 18705,
    "docs_serve": 18706,
    "inference_control": 18710,
}
SETTINGS = {"allprof": {"ctl": {"workers": 2}}}
SYSTEMD_ANALYZE = shutil.which("systemd-analyze")
HOST_UNITS = Path("/usr/lib/systemd/system")


def render(fixture: str, **changes: Any) -> dict[str, Any]:
    data = manifest(fixture)
    data.update(changes)
    return rendering.render(
        data,
        CONTRACT,
        release_target=f"/srv/pinka/platforms/{data['name']}/releases/{data['name']}-v1",
        ports=PORTS,
        settings=SETTINGS.get(fixture, {}),
        machine_name="pinka-lab",
    )


def golden(name: str, text: str) -> None:
    path = GOLDEN / name
    if os.environ.get("PINKA_UPDATE_SNAPSHOTS") == "1":
        path.write_text(text, encoding="utf-8")
    assert path.is_file(), f"{path} is missing: run with PINKA_UPDATE_SNAPSHOTS=1 and review it"
    assert text == path.read_text(encoding="utf-8"), f"{name} differs from its golden file"


def units_text(document: dict[str, Any]) -> str:
    return "".join(f"===== {u['name']}\n{u['content']}" for u in document["units"])


def unit(document: dict[str, Any], name: str) -> dict[str, list[str]]:
    """A unit's settings: key to every value it is given (in order)."""
    text = next(u["content"] for u in document["units"] if u["name"] == name)
    out: dict[str, list[str]] = {}
    for line in text.splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            out.setdefault(key, []).append(value)
    return out


def agent_unit(document: dict[str, Any]) -> dict[str, list[str]]:
    p = document["platform"]
    agent = next(a for a in document["accounts"] if a["profile"] == "untrusted-agent")
    return unit(document, f"{names.account(p, agent['member'])}.service")


# --- golden files (units, sockets, ACLs, accounts, groups, SQL, Incus) ---------


@pytest.mark.parametrize("fixture", VALID)
def test_units_golden(fixture: str) -> None:
    golden(f"{fixture}.units.txt", units_text(render(fixture)))


@pytest.mark.parametrize("fixture", VALID)
def test_accounts_golden(fixture: str) -> None:
    document = render(fixture)
    data = {k: document[k] for k in ("infra_groups", "groups", "accounts")}
    golden(f"{fixture}.accounts.json", json.dumps(data, indent=2) + "\n")


@pytest.mark.parametrize("fixture", VALID)
def test_database_golden(fixture: str) -> None:
    db = render(fixture)["database"]
    if db is None:
        assert "postgres" not in manifest(fixture).get("requires", [])
        return
    golden(f"{fixture}.bootstrap.sql", db["bootstrap_sql"])
    golden(f"{fixture}.restricted-check.sql", db["restricted_check_sql"])


@pytest.mark.parametrize("fixture", VALID)
def test_rest_of_the_rendering_golden(fixture: str) -> None:
    document = render(fixture)
    skip = {"units", "infra_groups", "groups", "accounts"}
    rest = {k: v for k, v in document.items() if k not in skip}
    if rest["database"]:
        rest["database"] = {k: v for k, v in rest["database"].items() if not k.endswith("_sql")}
    golden(f"{fixture}.render.json", json.dumps(rest, indent=2, sort_keys=True) + "\n")


def test_rendered_json_drops_the_configuration_contents() -> None:
    public = rendering.public(render("allprof"))
    assert public["config_files"] == [
        {
            "service": "ctl",
            "path": "/etc/pinka/platform/config/ctl.json",
            "owner": "root",
            "group": "allprof-ctl",
            "mode": "0640",
        }
    ]
    assert json.loads(render("allprof")["config_files"][0]["content"]) == {"workers": 2}


# --- systemd-analyze verify on every unit ---------------------------------------


def fake_root(tmp: Path, document: dict[str, Any], data: dict[str, Any]) -> Path:
    """A root with the units, stand-ins for the units they name and their executables."""
    root = tmp / "root"
    system = root / "etc/systemd/system"
    system.mkdir(parents=True)
    shutil.copytree(HOST_UNITS, root / "usr/lib/systemd/system", symlinks=True)
    p = data["name"]
    release = root / layout.PLATFORMS_DIR.lstrip("/") / p / "releases" / f"{p}-v1"
    release.mkdir(parents=True)
    (release.parent / "current").symlink_to(release.name)
    helper = root / layout.SOCKET_ACL_HELPER.lstrip("/")
    tool = root / layout.PLATFORM_TOOL.lstrip("/")
    starts = [release / agentpkg.start_path(s, data["runtime"]) for s in data["services"]]
    for path in [helper, tool, *starts]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\n", encoding="utf-8")
        path.chmod(0o755)
    stand_ins = (
        "pinka-health-gate",
        "pinka-core",
        "pinka-observability",
        "pinka-inference",
        "pinka-otlp-agent",
    )
    for name in stand_ins:
        (system / f"{name}.service").write_text(
            f"[Unit]\nDescription=stand-in\n[Service]\nExecStart={layout.SOCKET_ACL_HELPER}\n",
            encoding="utf-8",
        )
    (system / "pinka-lab.target").write_text("[Unit]\nDescription=stand-in\n", encoding="utf-8")
    (system / "pinka-otlp-agent.socket").write_text(
        f"[Socket]\nListenStream={layout.AGENT_OTLP_SOCKET}\n", encoding="utf-8"
    )
    for u in document["units"]:
        (system / u["name"]).write_text(u["content"], encoding="utf-8")
    return root


def require_systemd_analyze() -> str:
    """Skip without systemd-analyze locally; in CI (CI=true) it must be there."""
    if SYSTEMD_ANALYZE is not None and HOST_UNITS.is_dir():
        return SYSTEMD_ANALYZE
    if os.environ.get("CI", "").lower() == "true":
        pytest.fail(
            "systemd-analyze or the host's units are missing in CI; the unit check must run"
        )
    pytest.skip("needs systemd-analyze and the host's units; allowed to skip only outside CI")


def test_the_unit_check_only_skips_outside_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys.modules[__name__], "SYSTEMD_ANALYZE", None)
    monkeypatch.setenv("CI", "true")
    with pytest.raises(pytest.fail.Exception, match="missing in CI"):
        require_systemd_analyze()
    monkeypatch.delenv("CI")
    with pytest.raises(pytest.skip.Exception):
        require_systemd_analyze()


@pytest.mark.parametrize("fixture", VALID)
def test_systemd_analyze_verifies_every_unit(tmp_path: Path, fixture: str) -> None:
    analyze = require_systemd_analyze()
    data = manifest(fixture)
    document = render(fixture)
    root = fake_root(tmp_path, document, data)
    system = root / "etc/systemd/system"
    paths = [str(system / u["name"]) for u in document["units"]]
    done = subprocess.run(
        [analyze, "verify", f"--root={root}", *paths],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert done.stderr == "", done.stderr


# --- the hardening baseline -------------------------------------------------


@pytest.mark.parametrize("fixture", VALID)
def test_every_service_unit_carries_the_baseline(fixture: str) -> None:
    document = render(fixture)
    for name in document["service_units"]:
        settings = unit(document, name)
        for key, value in units.BASELINE:
            assert settings[key] == [value], f"{name}: {key}"
        account = name.removesuffix(".service")
        assert settings["User"] == [account]
        assert settings["Group"] == [account]
        assert settings["MemoryMax"] and settings["TasksMax"]
        assert settings["Restart"] == ["on-failure"]


# What a service unit gets besides the baseline the explanation page lists: its
# description, ordering and start, its runtime directory, its identity and
# limits, its paths, the agent's confinement and its profile's network
# allowances. A new setting of a service unit goes onto the page's list or here.
NOT_BASELINE = frozenset(
    {
        "Description", "PartOf", "Requires", "After", "Wants", "WantedBy",
        "Type", "ExecStart", "WorkingDirectory", "Restart", "RestartSec",
        "User", "Group", "SupplementaryGroups", "MemoryMax", "TasksMax",
        "RuntimeDirectory", "RuntimeDirectoryMode", "ReadOnlyPaths", "ReadWritePaths",
        "BindPaths", "BindReadOnlyPaths", "InaccessiblePaths", "TemporaryFileSystem",
        "IPAddressAllow", "SocketBindAllow", "PrivateNetwork",
    }
)  # fmt: skip


def test_the_explanation_page_lists_the_whole_baseline() -> None:
    # The contract sends platform authors to this page for the baseline: its
    # list must hold every setting of it, each once, with the value every
    # service unit gets, and the two exceptions as the code applies them.
    page = Path(__file__).resolve().parents[3] / "docs/explanation/privilege-profiles.md"
    paragraphs = [
        " ".join(part.split()) for part in re.split(r"\n[ \t]*\n", page.read_text(encoding="utf-8"))
    ]
    intro = [
        i for i, part in enumerate(paragraphs) if part.endswith("every platform service's unit:")
    ]
    assert len(intro) == 1, "the list of the baseline is missing"
    section = paragraphs[intro[0] + 1]
    spans = re.findall(r"`([^`]*)`", section)
    pairs = [span.split("=", 1) for span in spans if "=" in span]
    listed = dict(pairs)
    assert len(listed) == len(pairs), "a setting is listed twice"
    assert all(re.fullmatch(r"[A-Za-z]+", key) for key in listed), sorted(listed)
    # The words of the two exceptions, each where it belongs and nowhere else.
    assert Counter(span for span in spans if "=" not in span) == Counter(
        {"untrusted-agent": 2, "no": 1, "wx-memory": 1, "AF_UNIX": 1}
    ), "an exception the list should not state, or one it lacks"
    assert "=" not in re.sub(r"`[^`]*`", "", section), "a setting outside a code span"
    mdwe = "MemoryDenyWriteExecute"
    families = "RestrictAddressFamilies"
    never = "(`no` only with `wx-memory`, which `untrusted-agent` never gets)"
    assert f"`{mdwe}={listed[mdwe]}` {never}" in section
    assert f"`{families}={listed[families]}` (only `AF_UNIX` for `untrusted-agent`)" in section
    assert {key for key, _ in units.BASELINE} <= set(listed)
    keys: set[str] = set()
    branches: Counter[str] = Counter()
    for fixture in VALID:
        data = manifest(fixture)
        document = render(fixture)
        for service in data["services"]:
            settings = unit(document, f"{names.account(data['name'], service['name'])}.service")
            expected = dict(listed)
            if service["profile"] == "untrusted-agent":
                expected[families] = "AF_UNIX"
                branches["untrusted-agent"] += 1
            elif "wx-memory" in service.get("relaxations", []):
                expected[mdwe] = "no"
                branches["wx-memory"] += 1
            else:
                branches["neither"] += 1
            for key, value in expected.items():
                assert settings.get(key) == [value], f"{fixture} {service['name']}: {key}"
            keys |= set(settings)
    # Every exception and the plain case are met by some service of the fixtures.
    assert set(branches) == {"untrusted-agent", "wx-memory", "neither"}, branches
    assert keys - set(listed) == NOT_BASELINE


@pytest.mark.parametrize("fixture", VALID)
def test_every_service_has_a_runtime_directory_without_sockets(fixture: str) -> None:
    # V4 and its condition.
    data = manifest(fixture)
    document = render(fixture)
    sockets = {layout.socket_path(s["service"], s["name"]) for s in data.get("sockets", [])}
    for service in data["services"]:
        settings = unit(document, units.service_unit(data["name"], service["name"]))
        runtime = layout.runtime_dir(service["name"])
        assert settings["RuntimeDirectory"] == [runtime.removeprefix("/run/")]
        assert settings["RuntimeDirectoryMode"] == ["0700"]
        assert not any(s.startswith(f"{runtime}/") for s in sockets)
        assert not runtime.startswith(f"{layout.RUN}/platform/")
    for name in document["socket_units"]:
        settings = unit(document, name)
        assert "RuntimeDirectory" not in settings
        assert not settings["ListenStream"][0].startswith(layout.PLATFORM_RUNTIME_DIR)


def test_limits_and_the_relaxation() -> None:
    document = render("allprof")
    ctl = unit(document, "allprof-ctl.service")
    assert (ctl["MemoryMax"], ctl["TasksMax"]) == (["1G"], ["256"])
    assert ctl["MemoryDenyWriteExecute"] == ["yes"]
    rrd = unit(document, "allprof-rrd.service")
    assert (rrd["MemoryMax"], rrd["TasksMax"]) == (["2G"], ["512"])
    assert rrd["MemoryDenyWriteExecute"] == ["no"]


def test_the_agent_never_gets_the_relaxation() -> None:
    data = manifest("allprof")
    agent = next(s for s in data["services"] if s["profile"] == "untrusted-agent")
    agent["relaxations"] = ["wx-memory"]  # the validator refuses this; the renderer too
    document = rendering.render(
        data, CONTRACT, release_target="/r", ports=PORTS, settings={}, machine_name="m"
    )
    assert agent_unit(document)["MemoryDenyWriteExecute"] == ["yes"]


def test_only_the_inference_broker_may_bind_a_port() -> None:
    document = render("allprof")
    for name in document["service_units"]:
        settings = unit(document, name)
        allow = settings.get("SocketBindAllow", [])
        assert allow == (["tcp:18705"] if name == "allprof-brk.service" else []), name


def test_the_broker_port_follows_the_override() -> None:
    data = manifest("allprof")
    document = rendering.render(
        data,
        CONTRACT,
        release_target="/r",
        ports=dict(PORTS, gateway_adhoc=28705),
        settings={},
        machine_name="m",
    )
    assert unit(document, "allprof-brk.service")["SocketBindAllow"] == ["tcp:28705"]


def test_loopback_profiles_reach_only_localhost() -> None:
    document = render("allprof")
    for name in document["service_units"]:
        settings = unit(document, name)
        if name == "allprof-agt.service":
            continue
        assert settings["IPAddressDeny"] == ["any"]
        assert settings["IPAddressAllow"] == ["localhost"]
        assert settings["RestrictAddressFamilies"] == ["AF_UNIX AF_INET AF_INET6"]


def test_infra_internals_are_hidden() -> None:
    document = render("allprof")
    for name in document["service_units"]:  # the agent included
        hidden = unit(document, name)["InaccessiblePaths"][0].split()
        for path in layout.INFRA_INTERNALS:
            assert f"-{path}" in hidden, (name, path)
    ctl = unit(document, "allprof-ctl.service")["InaccessiblePaths"][0].split()
    assert f"-{layout.GPU_SOCKET}" not in ctl
    assert f"-{layout.RUN_NETWORK_SOCKET}" not in ctl
    assert f"-{layout.SIGNING_DIR}" not in ctl
    obs = unit(document, "allprof-obs.service")["InaccessiblePaths"][0].split()
    for path in (layout.GPU_SOCKET, layout.RUN_NETWORK_SOCKET, layout.SIGNING_DIR):
        assert f"-{path}" in obs
    assert f"-{layout.RESTRICTED_DIR}" in obs
    assert f"-{layout.INCUS_CERT_DIR}" in obs
    rop = unit(document, "allprof-rop.service")["InaccessiblePaths"][0].split()
    assert f"-{layout.RESTRICTED_DIR}" not in rop
    assert f"-{layout.INCUS_CERT_DIR}" not in rop


def test_state_and_configuration_paths() -> None:
    document = render("allprof")
    ctl = unit(document, "allprof-ctl.service")
    assert ctl["ReadWritePaths"] == ["/srv/pinka/platforms/allprof/state/ctl/records"]
    assert ctl["ReadOnlyPaths"] == [
        "/srv/pinka/platforms/allprof/releases/current /etc/pinka/platform/config/ctl.json"
    ]
    assert ctl["ExecStart"] == ["/srv/pinka/platforms/allprof/releases/current/bin/ctl"]
    assert ctl["WorkingDirectory"] == ["/srv/pinka/platforms/allprof/releases/current"]
    assert "ReadWritePaths" not in unit(document, "allprof-brk.service")


def test_ordering_follows_after_and_the_facilities() -> None:
    document = render("allprof")
    brk = unit(document, "allprof-brk.service")
    after = brk["After"][0].split()
    assert "allprof-ctl.service" in after
    assert {"pinka-health-gate.service", "pinka-core.service"} <= set(after)
    assert brk["Requires"] == [
        "pinka-health-gate.service allprof-reconcile.service allprof-brk-agent.socket "
        "allprof-brk-api.socket"
    ]
    assert "allprof-reconcile.service" in after
    assert brk["PartOf"] == ["pinka-platform.target"]
    assert brk["WantedBy"] == ["pinka-platform.target"]


# --- sockets ------------------------------------------------------------------


def test_socket_units_create_the_sockets_with_owner_group_and_mode() -> None:
    document = render("allprof")
    api = unit(document, "allprof-ctl-api.socket")
    assert api["ListenStream"] == ["/run/pinka/platform/ctl/api.sock"]
    assert api["SocketUser"] == ["allprof-ctl"]
    assert api["SocketGroup"] == ["allprof-ctl-api"]
    assert api["SocketMode"] == ["0660"]
    assert api["Service"] == ["allprof-ctl.service"]
    assert api["FileDescriptorName"] == ["api"]
    assert api["ExecStartPost"] == [
        f"{layout.SOCKET_ACL_HELPER} /run/pinka/platform/ctl/api.sock pinka pinka-admin"
    ]


@pytest.mark.parametrize("fixture", VALID)
def test_only_sockets_with_human_clients_get_acl_entries(fixture: str) -> None:
    data = manifest(fixture)
    document = render(fixture)
    for socket in data.get("sockets", []):
        settings = unit(
            document, units.socket_unit(data["name"], socket["service"], socket["name"])
        )
        humans = [c for c in socket["clients"] if c in CONTRACT.human_classes]
        assert ("ExecStartPost" in settings) == bool(humans)


def test_brk_api_acl_names_only_researchers() -> None:
    document = render("allprof")
    assert unit(document, "allprof-brk-api.socket")["ExecStartPost"] == [
        f"{layout.SOCKET_ACL_HELPER} /run/pinka/platform/brk/api.sock pinka"
    ]


# --- the agent: no IP access, no database socket group ----------------


@pytest.mark.parametrize("fixture", VALID)
def test_the_agent_has_no_ip_access(fixture: str) -> None:
    settings = agent_unit(render(fixture))
    assert settings["PrivateNetwork"] == ["yes"]
    assert settings["RestrictAddressFamilies"] == ["AF_UNIX"]
    assert settings["IPAddressDeny"] == ["any"]
    assert "IPAddressAllow" not in settings
    assert settings["SocketBindDeny"] == ["any"]
    assert "SocketBindAllow" not in settings


@pytest.mark.parametrize("fixture", VALID)
def test_the_agent_has_no_database_socket_group(fixture: str) -> None:
    document = render(fixture)
    p = document["platform"]
    agent = next(a for a in document["accounts"] if a["profile"] == "untrusted-agent")
    forbidden = {accounts.db_group(p), accounts.RESTRICTED_GROUP, accounts.QUERY_GROUP}
    assert not forbidden & set(agent["groups"])
    assert not forbidden & set(agent_unit(document)["SupplementaryGroups"][0].split())
    dedicated = {
        names.client_group(p, s["service"], s["name"])
        for s in manifest(fixture).get("sockets", [])
        if s.get("untrusted_clients")
    }
    assert set(agent["groups"]) == dedicated


@pytest.mark.parametrize("fixture", VALID)
def test_the_agent_sees_only_its_allowlist(fixture: str) -> None:
    data = manifest(fixture)
    p = data["name"]
    settings = agent_unit(render(fixture))
    assert settings["TemporaryFileSystem"] == [
        "/srv/pinka:ro /etc/pinka:ro /var/lib/pinka:ro /run/pinka:ro"
    ]
    bound = settings["BindReadOnlyPaths"][0].split()
    sockets = [
        layout.socket_path(s["service"], s["name"])
        for s in data.get("sockets", [])
        if s.get("untrusted_clients")
    ]
    agent = next(s for s in data["services"] if s["profile"] == "untrusted-agent")
    config = [layout.config_file(agent["name"])] if "config" in agent else []
    # Of the release being deployed, resolved at render time (not the current
    # link, not the other published releases), only its own package and, for
    # python-uv, its own environment (V6).
    release = f"{layout.PLATFORMS_DIR}/{p}/releases/{p}-v1"
    package = f"{release}/{agent['package'].rstrip('/')}"
    env = [f"{release}/.agent-venvs/{agent['name']}"] if data["runtime"] == "python-uv" else []
    assert bound == [package, *env, *config, *sockets, f"-{layout.AGENT_OTLP_SOCKET}"]
    assert settings["WorkingDirectory"] == [package]
    start = settings["ExecStart"][0].split()[0]
    assert start.startswith(f"{(env or [package])[0]}/"), start
    state = [layout.state_dir(p, agent["name"], d) for d in agent.get("state", [])]
    writable = [layout.runtime_dir(agent["name"]), *state]
    assert settings["BindPaths"][0].split() == writable
    assert settings["ReadWritePaths"][0].split() == writable
    hidden = settings["InaccessiblePaths"][0].split()
    for path in (*layout.INFRA_INTERNALS, layout.GPU_SOCKET, layout.RUN_NETWORK_SOCKET):
        assert f"-{path}" in hidden, path
    for secret in (
        layout.POSTGRES_SOCKET_DIR,
        layout.RESTRICTED_DIR,
        layout.SECRETS_DIR,
        layout.QUERY_SOCKET,
    ):
        assert not any(b.lstrip("-").startswith(secret) for b in bound), secret


def agent_view(settings: dict[str, list[str]]) -> Callable[[str], bool]:
    """Whether the agent's unit lets it see a path.

    The lab directories are empty, read-only file systems in its namespace
    except for what it binds; everywhere else, its inaccessible paths are
    hidden.
    """
    hidden_roots = [r.split(":")[0] for r in settings["TemporaryFileSystem"][0].split()]
    bound = [
        b.lstrip("-")
        for key in ("BindReadOnlyPaths", "BindPaths")
        for b in settings.get(key, [""])[0].split()
    ]
    inaccessible = [x.lstrip("-") for x in settings["InaccessiblePaths"][0].split()]

    def under(path: str, roots: list[str]) -> bool:
        return any(path == r or path.startswith(r.rstrip("/") + "/") for r in roots)

    def visible(path: str) -> bool:
        if under(path, inaccessible):
            return False
        if under(path, hidden_roots):
            return under(path, bound)
        return True

    return visible


@pytest.mark.parametrize("fixture", VALID)
def test_nothing_outside_the_agents_set_is_visible(fixture: str) -> None:
    # Ground-truth blindness (V6):
    # of the active release the agent sees only its own package and
    # environment; besides, its configuration, its dedicated sockets, the
    # agent telemetry socket and its own runtime and state directories.
    data = manifest(fixture)
    p = data["name"]
    agent = next(s for s in data["services"] if s["profile"] == "untrusted-agent")
    visible = agent_view(agent_unit(render(fixture)))
    release = f"{layout.PLATFORMS_DIR}/{p}/releases/{p}-v1"
    package = f"{release}/{agent['package'].rstrip('/')}"
    python_uv = data["runtime"] == "python-uv"
    allowed = [
        f"{package}/pyproject.toml",
        f"{release}/{agentpkg.start_path(agent, data['runtime'])}",
        *([f"{release}/.agent-venvs/{agent['name']}/lib/python3.12/site-packages"] * python_uv),
        *([layout.config_file(agent["name"])] if "config" in agent else []),
        *(
            layout.socket_path(s["service"], s["name"])
            for s in data.get("sockets", [])
            if s.get("untrusted_clients")
        ),
        layout.AGENT_OTLP_SOCKET,
        f"{layout.runtime_dir(agent['name'])}/work",
        *(f"{layout.state_dir(p, agent['name'], d)}/x" for d in agent.get("state", [])),
    ]
    for path in allowed:
        assert visible(path), path
    others = [s for s in data["services"] if s is not agent]
    assert "package" not in str(others)
    content = [c["name"] for c in data.get("content", [])]
    # The rest of the release: the manifest and its provenance, the shared
    # environment, the other packages (the injector, primitives, verifier and
    # evaluator among them), the migrations, the policy bundle, docs,
    # dashboards, schemas and the other agents' environments.
    rest = [
        "platform-manifest.yaml",
        "DEPLOYED_FROM.json",
        "pyproject.toml",
        "uv.lock",
        ".venv/bin/python",
        ".venv/lib/python3.12/site-packages/injector/__init__.py",
        ".agent-venvs/other/bin/python",
        "packages/injector/pyproject.toml",
        "packages/primitives/primitives/faults.py",
        "packages/verifier/pyproject.toml",
        "packages/evaluator/pyproject.toml",
        "packages/core/pyproject.toml",
        "packages/db/migrations/0001_ground_truth.py",
        *(data[key] for key in ("docs", "dashboards") if key in data),
        *([data["policy"]["bundle"]] if "policy" in data else []),
        *(f"{h['run'][0]}" for h in data["hooks"].values()),
        *(s["start"][0] for s in others if not s["start"][0].startswith(".venv/")),
    ]
    forbidden = [
        *(f"{release}/{r}" for r in rest),
        f"{release}/{agent['package'].rstrip('/')}-other/x",
        f"{layout.PLATFORMS_DIR}/{p}/releases/{p}-v0/.venv/bin/python",
        f"{layout.PLATFORMS_DIR}/{p}/releases/current",
        f"{layout.PLATFORMS_DIR}/{p}/releases/{p}-v2",
        *(f"{layout.PLATFORMS_DIR}/{p}/content/{c}/main/data.json" for c in content),
        *(f"{layout.RESTRICTED_DIR}/{p}/content/{c}/main/data.json" for c in content),
        f"{layout.RESTRICTED_DIR}/{p}/cases",
        *(f"{layout.PLATFORMS_DIR}/{p}/state/{s['name']}/x" for s in others),
        *(layout.config_file(s["name"]) for s in others),
        *(
            layout.socket_path(s["service"], s["name"])
            for s in data.get("sockets", [])
            if not s.get("untrusted_clients")
        ),
        *(layout.runtime_dir(s["name"]) for s in others),
        f"{layout.SECRETS_DIR}/{p}-{agent['name']}/token",
        f"{layout.SIGNING_DIR}/{p}/signing.key",
        layout.RENDERED_FILE,
        f"{layout.POSTGRES_SOCKET_DIR}/.s.PGSQL.5432",
        layout.QUERY_SOCKET,
        layout.GPU_SOCKET,
        layout.RUN_NETWORK_SOCKET,
        layout.SESSIONS_DIR,
        f"{layout.PLATFORM_VAR_DIR}/{p}/active.json",
        f"{layout.VAR_LIB}/git/{p}.git",
        "/var/lib/docker/volumes",
        "/var/lib/incus/unix.socket",
        "/run/docker.sock",
    ]
    for path in forbidden:
        assert not visible(path), path


def test_the_agent_restarts_with_its_sockets() -> None:
    settings = agent_unit(render("allprof"))
    sockets = ["allprof-ctl-agent.socket", "allprof-brk-agent.socket", "allprof-sbx-agent.socket"]
    assert settings["PartOf"] == [" ".join(["pinka-platform.target", *sockets])]
    assert set(sockets) <= set(settings["Requires"][0].split())


def test_forbidden_memberships_are_refused_even_if_the_rules_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = manifest("allprof")
    original = accounts._socket_groups

    def leaky(manifest_: Any, profile: str) -> list[str]:
        found = original(manifest_, profile)
        return [*found, "allprof-db", "pinka-restricted"] if profile == "untrusted-agent" else found

    monkeypatch.setattr(accounts, "_socket_groups", leaky)
    with pytest.raises(accounts.RenderError) as exc:
        accounts.render(data, CONTRACT)
    assert "allprof-agt (untrusted-agent) would join allprof-db, pinka-restricted" in str(exc.value)


@pytest.mark.parametrize(
    ("profile", "group", "message"),
    [
        ("gpu-trainer", "pinka-query", "would join pinka-query"),
        ("observer", "docker", "would join docker"),
        ("controller", "wheel", "would join wheel"),
        ("controller", "somebody-else", "groups infra does not manage"),
    ],
)
def test_enforce_refuses(profile: str, group: str, message: str) -> None:
    data = manifest("allprof")
    built = accounts.build(data, CONTRACT)
    changed = [
        accounts.Account(a.name, a.member, a.kind, a.profile, (*a.groups, group))
        if a.profile == profile
        else a
        for a in built
    ]
    with pytest.raises(accounts.RenderError, match=message):
        accounts.enforce(data, changed)


# --- memberships from the profiles -------------------------------------


def test_memberships_follow_the_profiles() -> None:
    document = render("allprof")
    groups = {a["name"]: set(a["groups"]) for a in document["accounts"]}
    assert groups["allprof-ctl"] >= {"pinka-restricted", "pinka-query", "allprof-db"}
    assert groups["allprof-rop"] >= {"pinka-restricted", "allprof-db"}
    assert "pinka-query" not in groups["allprof-rop"]
    assert groups["allprof-rrd"] >= {"pinka-restricted", "pinka-query", "allprof-db"}
    assert groups["allprof-sbx"] >= {"pinka-query", "allprof-db"}
    assert "pinka-restricted" not in groups["allprof-sbx"]
    assert "pinka-restricted" not in groups["allprof-brk"]
    assert groups["allprof-trn"] == {"pinka-datasets"}
    assert groups["allprof-migrate"] == {"allprof-db"}
    assert "allprof-ctl-api" not in groups["allprof-ctl"]  # humans only, no service client


def test_no_database_group_without_postgres() -> None:
    data = manifest("allprof")
    data["requires"] = [r for r in data["requires"] if r != "postgres"]
    data.pop("backup")
    document = accounts.render(data, CONTRACT)
    assert "allprof-db" not in document["groups"]
    assert all("allprof-db" not in a["groups"] for a in document["accounts"])


# --- the rest of the rendering --------------------------------------------------------


def test_incus_certificates_follow_the_profiles() -> None:
    document = render("allprof")
    assert document["incus_certificates"] == [
        {
            "name": "allprof-ctl",
            "account": "allprof-ctl",
            "short": "allprof-ctl",
            "projects": ["bench", "build"],
            "dir": "/etc/pinka/incus/allprof-ctl",
        },
        {
            "name": "allprof-sbx",
            "account": "allprof-sbx",
            "short": "allprof-sbx",
            "projects": ["bench"],
            "dir": "/etc/pinka/incus/allprof-sbx",
        },
        {
            "name": "allprof-rop",
            "account": "allprof-rop",
            "short": "allprof-rop",
            "projects": ["bench"],
            "dir": "/etc/pinka/incus/allprof-rop",
        },
    ]
    assert document["run_network"] == {"socket_owner": "allprof-ctl"}


def test_without_incus_no_certificates_and_no_run_network() -> None:
    document = render("refplat")
    assert document["incus_certificates"] == []
    assert document["run_network"] is None


def test_opa_dashboards_cli_and_signing() -> None:
    document = render("allprof")
    target = "/srv/pinka/platforms/allprof/releases/allprof-v1"
    assert document["opa"] == {"bundle": f"{target}/policy", "decision_path": "allprof/decide"}
    assert document["dashboards"] == {
        "folder": "allprof",
        "dir": f"{target}/dashboards",
        "database": "allprof",
        "role": "allprof_grafana",
        "socket_dir": "/run/pinka/postgres-reader",
    }
    assert document["cli"]["link"] == "/usr/local/bin/allprof"
    assert document["cli"]["wrapper"] == "/usr/local/lib/pinka/platform/cli/allprof"
    assert document["signing"]["owner"] == "allprof-ctl"
    assert document["signing"]["published_prefix"] == "allprof-pinka-lab-"


def test_cli_wrapper_quotes_the_command() -> None:
    text = rendering.cli_wrapper("demo", ["bin/demo cli", "--mode", "it's"], "demo")
    assert text.splitlines()[-1] == (
        "exec '/srv/pinka/platforms/demo/releases/current/bin/demo cli' --mode 'it'\"'\"'s' \"$@\""
    )


def test_secrets_and_directories() -> None:
    document = render("allprof")
    assert document["secrets"] == [
        {
            "service": "ctl",
            "name": "token",
            "path": "/etc/pinka/secrets/allprof-ctl/token",
            "owner": "root",
            "group": "allprof-ctl",
            "mode": "0640",
        }
    ]
    dirs = {d["path"]: d for d in document["directories"]}
    assert dirs["/etc/pinka/secrets/allprof-ctl"]["mode"] == "0750"
    assert dirs["/srv/pinka/platforms/allprof/state/agt/scratch"]["owner"] == "allprof-agt"
    assert dirs["/srv/pinka/platforms/allprof/state/agt/scratch"]["mode"] == "0700"
    assert dirs["/etc/pinka/signing/allprof"] == {
        "path": "/etc/pinka/signing/allprof",
        "owner": "root",
        "group": "allprof-ctl",
        "mode": "0750",
    }
    assert dirs[layout.QA_ATTESTATIONS_DIR]["group"] == "allprof-ctl"
    assert dirs[layout.POSTGRES_PASSWORD_DIR]["mode"] == "0711"


def test_stale_units_of_the_same_platform() -> None:
    previous = render("allprof")
    previous["units"].append({"name": "allprof-old.service", "content": ""})
    data = manifest("allprof")
    document = rendering.render(
        data,
        CONTRACT,
        release_target="/r",
        ports=PORTS,
        settings={},
        machine_name="m",
        previous=previous,
    )
    assert document["stale_units"] == ["allprof-old.service"]
    other = rendering.render(
        manifest("refplat"),
        CONTRACT,
        release_target="/r",
        ports=PORTS,
        settings={},
        machine_name="m",
        previous=previous,
    )
    assert other["stale_units"] == []  # another platform's units are retired, not stale


def test_stale_roles_become_nologin() -> None:
    previous = render("allprof")
    previous["database"]["all_roles"].append("allprof_gone")
    document = rendering.render(
        manifest("allprof"),
        CONTRACT,
        release_target="/r",
        ports=PORTS,
        settings={},
        machine_name="m",
        previous=previous,
    )
    assert document["database"]["stale_roles"] == ["allprof_gone"]
    assert "allprof_gone" in document["database"]["all_roles"]
    assert "ARRAY['allprof_gone']::text[]" in document["database"]["bootstrap_sql"]


def test_backup_settings() -> None:
    assert render("allprof")["backup"] == {
        "database": False,
        "paths": [
            "/srv/pinka/platforms/allprof/state/ctl/records",
            "/srv/pinka/platforms/allprof/state/rrd/reports/archive",
        ],
    }
    assert render(FIRST_PLATFORM)["backup"]["database"] is True


# --- quoting (systemd command lines) ---------------------------------------------------


@pytest.mark.parametrize(
    ("word", "quoted"),
    [
        ("plain/path-1.0", "plain/path-1.0"),
        ("--flag=value", "--flag=value"),
        ("two words", '"two words"'),
        ("100%", '"100%%"'),
        ("$HOME", '"$$HOME"'),
        ('say "hi"', '"say \\"hi\\""'),
        ("back\\slash", '"back\\\\slash"'),
        ("line\nbreak", '"line\\x0abreak"'),
    ],
)
def test_quote(word: str, quoted: str) -> None:
    assert units.quote(word) == quoted


def test_command_line_needs_an_absolute_executable() -> None:
    assert units.command_line(["/bin/x", "-v"]) == "/bin/x -v"
    with pytest.raises(ValueError, match="absolute"):
        units.command_line(["bin/x"])


# --- the database plan -------------------------------------------------------


def test_database_plan() -> None:
    db = database.plan(manifest("allprof"), CONTRACT)
    assert db["database"] == "allprof"
    assert [login["role"] for login in db["logins"]] == [
        "allprof_ctl",
        "allprof_brk",
        "allprof_sbx",
        "allprof_rop",
        "allprof_rrd",
        "allprof_obs",
    ]
    assert db["restricted_allowed"] == [
        "allprof_controller",
        "allprof_ctl",
        "allprof_migrator",
        "allprof_restricted_operator",
        "allprof_restricted_reader",
        "allprof_rop",
        "allprof_rrd",
    ]
    assert {f["role"]: f["group"] for f in db["password_files"]}["allprof_migrator"] == (
        "allprof-migrate"
    )
    assert "allprof_agt" not in db["vault_roles"]
    assert db["vault_roles"] == sorted(
        [login["role"] for login in db["logins"]]
        + ["allprof_migrator", "allprof_reader", "allprof_grafana"]
    )


def test_the_gpu_arbiter_reads_jobs_and_dataset_sources() -> None:
    # The controller publishes datasets only from its own state; a
    # job's container options are infra's, so none is rendered.
    data = render("allprof")
    training = data["training"]
    controller = data["controller"].removeprefix("allprof-")
    assert training["dataset_sources"] == [
        layout.state_dir("allprof", controller, d)
        for s in manifest("allprof")["services"]
        if s["name"] == controller
        for d in s.get("state", [])
    ]
    for job in training["jobs"]:
        assert job["account"] == names.account("allprof", job["name"])
        assert "@sha256:" in job["image"]
        assert not {"network", "options", "mounts", "privileged"} & set(job)
    assert render("allprof", requires=["postgres"])["training"] is None


def test_only_the_inference_api_profiles_are_its_clients() -> None:
    # The inference guard admits the controller and the inference broker.
    data = render("allprof")
    by_name = {a["name"]: a for a in data["accounts"]}
    profiles = {by_name[a]["profile"] for a in data["inference_clients"]}
    assert profiles == {"controller", "inference-broker"}
