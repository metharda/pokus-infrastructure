"""The run-network helper against a fake Incus.

Create and delete with the fixed settings; refusal of another name, request,
setting or peer; the operation log; reconcile removing an orphaned network;
and the list of networks that neither infra created nor the helper's log
records, whatever their names.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import os
import socket
from pathlib import Path
from typing import Any

import pinka_run_network as helper
import pytest

from ansible_testlib import GROUP_VARS, ROLES, ansible_vars, render_template

CONTROLLER = 4242
NOW = dt.datetime(2026, 5, 1, 12, 0, 0, tzinfo=dt.UTC)
NAME = "r-abcdefgh1234"


class FakeIncus:
    """The parts of the Incus API the helper uses."""

    def __init__(self) -> None:
        self.networks: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str, Any]] = []

    def __call__(self, method: str, path: str, body: Any) -> Any:
        self.calls.append((method, path, body))
        if path.startswith("/1.0/networks/"):
            name = path.rsplit("/", 1)[1]
            if name not in self.networks:
                raise helper.IncusError("Network not found", 404)
            if method == "GET":
                return self.networks[name]
            if method == "DELETE":
                del self.networks[name]
                return None
        if (method, path) == ("POST", "/1.0/networks"):
            self.networks[body["name"]] = {**body, "managed": True}
            return None
        if (method, path) == ("GET", "/1.0/networks?recursion=1"):
            return list(self.networks.values())
        raise AssertionError(f"unexpected call {method} {path}")


@pytest.fixture
def settings(tmp_path: Path) -> helper.Settings:
    return helper.Settings(log=tmp_path / "run-networks" / "operations.jsonl")


def serve(
    settings: helper.Settings, incus: FakeIncus, line: str, uid: int = CONTROLLER
) -> tuple[int, dict[str, Any], str]:
    out, journal = io.StringIO(), io.StringIO()
    code = helper.serve(
        settings,
        io.StringIO(line),
        out,
        uid=uid,
        allowed_uid=CONTROLLER,
        query=incus,
        journal=journal,
        now=lambda: NOW,
    )
    return code, json.loads(out.getvalue()), journal.getvalue()


def log(settings: helper.Settings) -> list[dict[str, Any]]:
    return [json.loads(line) for line in settings.log.read_text().splitlines()]


def test_create_uses_the_fixed_settings(settings: helper.Settings) -> None:
    incus = FakeIncus()
    code, answer, journal = serve(settings, incus, f"create {NAME}\n")
    assert (code, answer) == (0, {"ok": True, "network": NAME, "changed": True})
    assert incus.networks[NAME]["type"] == "bridge"
    assert incus.networks[NAME]["config"] == {
        "ipv4.address": "auto",
        "ipv4.nat": "false",
        "ipv6.address": "none",
        "security.acls": "pinka-run-default",
    }
    assert "bridge.external_interfaces" not in incus.networks[NAME]["config"]
    assert log(settings) == [
        {
            "at": "2026-05-01T12:00:00Z",
            "network": NAME,
            "outcome": "created",
            "peer": helper._peer_name(CONTROLLER),
            "peer_uid": CONTROLLER,
            "request": "create",
        }
    ]
    assert journal.startswith("pinka-run-network: {")
    assert settings.log.stat().st_mode & 0o777 == 0o600


def test_create_twice_changes_nothing(settings: helper.Settings) -> None:
    incus = FakeIncus()
    serve(settings, incus, f"create {NAME}\n")
    code, answer, _ = serve(settings, incus, f"create {NAME}\n")
    assert (code, answer["changed"]) == (0, False)
    assert log(settings)[-1]["outcome"] == "exists"


def test_a_network_with_other_settings_is_refused(settings: helper.Settings) -> None:
    incus = FakeIncus()
    incus.networks[NAME] = {"name": NAME, "type": "bridge", "config": {"ipv4.nat": "true"}}
    code, answer, _ = serve(settings, incus, f"create {NAME}\n")
    assert code == 1
    assert answer == {"ok": False, "error": f"{NAME} exists with other settings"}
    assert log(settings)[-1]["outcome"] == "refused"


def test_an_uplink_is_another_setting(settings: helper.Settings) -> None:
    incus = FakeIncus()
    config = {**helper.NETWORK_CONFIG, "bridge.external_interfaces": "eth0"}
    incus.networks[NAME] = {"name": NAME, "type": "bridge", "config": config}
    assert serve(settings, incus, f"create {NAME}\n")[0] == 1


def test_delete_and_reconcile_an_orphan(settings: helper.Settings) -> None:
    incus = FakeIncus()
    serve(settings, incus, f"create {NAME}\n")
    # The platform's reconcile hook finds the network orphaned and deletes it.
    code, answer, _ = serve(settings, incus, f"delete {NAME}\n")
    assert (code, answer) == (0, {"ok": True, "network": NAME, "changed": True})
    assert NAME not in incus.networks
    code, answer, _ = serve(settings, incus, f"delete {NAME}\n")
    assert (code, answer["changed"]) == (0, False)
    assert [e["outcome"] for e in log(settings)] == ["created", "deleted", "absent"]


@pytest.mark.parametrize(
    "line",
    [
        "create r-short\n",
        "create r-ABCDEFGH1234\n",
        "create pinka-build0\n",
        "create incusbr-1000\n",
        "delete r-abcdefgh12345\n",
        "rename r-abcdefgh1234\n",
        "create r-abcdefgh1234 ipv4.nat=true\n",
        "create\n",
        '{"request": "create", "network": "r-abcdefgh1234"}\n',
        "",
    ],
)
def test_other_names_requests_and_settings_are_refused(
    settings: helper.Settings, line: str
) -> None:
    incus = FakeIncus()
    code, answer, _ = serve(settings, incus, line)
    assert code == 1
    assert answer["ok"] is False
    assert incus.calls == []
    assert log(settings)[-1]["outcome"] == "refused"


def test_a_long_request_is_refused(settings: helper.Settings) -> None:
    code, answer, _ = serve(settings, FakeIncus(), "x" * 1000 + "\n")
    assert (code, answer["error"]) == (1, "the request is too long")


def test_other_peers_are_refused(settings: helper.Settings) -> None:
    incus = FakeIncus()
    code, answer, _ = serve(settings, incus, f"create {NAME}\n", uid=CONTROLLER + 1)
    assert code == 1
    assert "controller and root" in answer["error"]
    assert incus.calls == []
    assert log(settings)[-1]["peer_uid"] == CONTROLLER + 1


def test_root_may_always_connect(settings: helper.Settings) -> None:
    assert serve(settings, FakeIncus(), f"create {NAME}\n", uid=0)[0] == 0


def test_no_controller_without_an_active_platform(settings: helper.Settings) -> None:
    out = io.StringIO()
    code = helper.serve(
        settings,
        io.StringIO(f"create {NAME}\n"),
        out,
        uid=CONTROLLER,
        allowed_uid=None,
        query=FakeIncus(),
        journal=io.StringIO(),
    )
    assert code == 1


def test_an_incus_failure_is_logged(settings: helper.Settings) -> None:
    def broken(method: str, path: str, body: Any) -> Any:
        raise helper.IncusError("the daemon is down", 500)

    out = io.StringIO()
    code = helper.serve(
        settings,
        io.StringIO(f"create {NAME}\n"),
        out,
        uid=0,
        allowed_uid=None,
        query=broken,
        journal=io.StringIO(),
    )
    assert code == 1
    assert log(settings)[-1]["outcome"] == "failed"
    assert log(settings)[-1]["error"] == "the daemon is down"


def test_the_controller_comes_from_the_rendering(tmp_path: Path) -> None:
    rendered = tmp_path / "rendered.json"
    settings = helper.Settings(rendered=rendered)
    assert helper.controller_uid(settings) is None
    rendered.write_text(json.dumps({"controller": "root"}))
    assert helper.controller_uid(settings) == 0
    rendered.write_text("not json")
    assert helper.controller_uid(settings) is None


def test_unknown_networks_whatever_their_names(settings: helper.Settings) -> None:
    incus = FakeIncus()
    serve(settings, incus, f"create {NAME}\n")
    incus.networks["r-zzzzzzzzzzzz"] = {"name": "r-zzzzzzzzzzzz", "managed": True}
    incus.networks["sneaky"] = {"name": "sneaky", "managed": True}
    incus.networks["pinka-build0"] = {"name": "pinka-build0", "managed": True}
    incus.networks["incusbr-1000"] = {"name": "incusbr-1000", "managed": True}
    incus.networks["eth0"] = {"name": "eth0", "managed": False}
    networks = incus("GET", "/1.0/networks?recursion=1", None)
    infra = ["pinka-build0", "incusbr-*"]
    lines = settings.log.read_text().splitlines()
    assert helper.unknown(networks, infra, lines) == ["r-zzzzzzzzzzzz", "sneaky"]


def test_peer_uid_of_a_unix_socket() -> None:
    left, right = socket.socketpair(socket.AF_UNIX)
    with left, right:
        assert helper.peer_uid(left.fileno()) == os.getuid()


def unit_settings(text: str) -> dict[str, str]:
    return dict(
        line.split("=", 1) for line in text.splitlines() if "=" in line and not line.startswith("#")
    )


@pytest.mark.parametrize(
    ("rendered", "owner"),
    [({}, "root"), ({"run_network": {"socket_owner": "demo-ctl"}}, "demo-ctl")],
)
def test_the_socket_belongs_to_the_controller(rendered: dict[str, Any], owner: str) -> None:
    variables = ansible_vars(
        GROUP_VARS, ROLES / "incus" / "defaults" / "main.yml", active_platform=rendered
    )
    settings = unit_settings(render_template("incus", "pinka-run-network.socket.j2", variables))
    assert settings["ListenStream"] == "/run/pinka/run-network.sock"
    assert settings["SocketUser"] == owner
    assert settings["SocketGroup"] == "root"
    assert settings["SocketMode"] == "0600"
    assert settings["Accept"] == "yes"


def test_the_helper_unit() -> None:
    variables = ansible_vars(GROUP_VARS, ROLES / "incus" / "defaults" / "main.yml")
    settings = unit_settings(render_template("incus", "pinka-run-network@.service.j2", variables))
    assert settings["ExecStart"] == "/usr/local/libexec/pinka/run-network serve"
    assert settings["StandardInput"] == "socket"
    assert settings["PrivateNetwork"] == "yes"
    assert settings["RestrictAddressFamilies"] == "AF_UNIX"
    assert settings["CapabilityBoundingSet"] == ""
    assert settings["ReadWritePaths"] == "/var/lib/pinka/run-networks"
    assert settings["Slice"] == "pinka-services.slice"
    assert "run_network.yml" in (ROLES / "incus" / "tasks" / "main.yml").read_text()
