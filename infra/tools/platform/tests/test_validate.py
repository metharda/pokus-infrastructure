"""Manifest schema and invariant tests: valid fixtures, one invalid case per rule (§11)."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any

import jsonschema
import pytest
import yaml
from platform_testlib import CONTRACT, FIRST_PLATFORM, VALID, dump, manifest

from pinka_platform import checks, names, validate
from pinka_platform.contract import Namespace
from pinka_platform.problems import ManifestError
from pinka_platform.schema import errors

Mutation = Callable[[dict[str, Any]], None]
ZERO = "0" * 64


def codes(data: dict[str, Any], host: names.HostFacts | None = None) -> set[str]:
    result = validate.validate_text(dump(data), CONTRACT, host)
    return {p.code for p in result.problems}


@pytest.mark.parametrize("name", VALID)
def test_valid_fixtures_pass(name: str) -> None:
    result = validate.validate_text(dump(manifest(name)), CONTRACT, names.HostFacts())
    assert [str(p) for p in result.problems] == []
    assert result.ok


@pytest.mark.parametrize("name", VALID)
def test_valid_fixtures_pass_the_reference_validator(name: str) -> None:
    validator = jsonschema.Draft202012Validator(CONTRACT.schema)
    assert [e.message for e in validator.iter_errors(manifest(name))] == []


def _service(data: dict[str, Any], name: str) -> dict[str, Any]:
    return next(s for s in data["services"] if s["name"] == name)


def _set(path: str, value: Any) -> Mutation:
    def mutate(data: dict[str, Any]) -> None:
        node: Any = data
        parts = path.split(".")
        for part in parts[:-1]:
            node = node[int(part)] if part.isdigit() else node[part]
        last = parts[-1]
        if last.isdigit():
            node[int(last)] = value
        else:
            node[last] = value

    return mutate


def _drop_requirement(facility: str) -> Mutation:
    def mutate(data: dict[str, Any]) -> None:
        data["requires"] = [r for r in data["requires"] if r != facility]

    return mutate


def _delete(key: str) -> Mutation:
    def mutate(data: dict[str, Any]) -> None:
        del data[key]

    return mutate


def _add_service(**fields: Any) -> Mutation:
    def mutate(data: dict[str, Any]) -> None:
        data["services"].append(fields)

    return mutate


def _add_socket(**fields: Any) -> Mutation:
    def mutate(data: dict[str, Any]) -> None:
        data.setdefault("sockets", []).append(fields)

    return mutate


def _drop(path: str) -> Mutation:
    def mutate(data: dict[str, Any]) -> None:
        node: Any = data
        parts = path.split(".")
        for part in parts[:-1]:
            node = node[int(part)] if part.isdigit() else node[part]
        del node[parts[-1]]

    return mutate


def _both(*mutations: Mutation) -> Mutation:
    def mutate(data: dict[str, Any]) -> None:
        for m in mutations:
            m(data)

    return mutate


# (case, fixture, mutation, expected problem code): one invalid fixture per rule.
INVALID: list[tuple[str, str, Mutation, str]] = [
    # schema
    ("unknown top-level field", "refplat", _set("extra", 1), "schema"),
    (
        "unknown service field",
        "refplat",
        _set("services.0.capabilities", ["CAP_NET_ADMIN"]),
        "schema",
    ),
    ("unknown socket field", "refplat", _set("sockets.0.mode", "0666"), "schema"),
    ("missing health hook", "refplat", _drop("hooks.health"), "schema"),
    ("contract version 1", "refplat", _set("contract_version", 1), "schema"),
    ("contract version as a string", "refplat", _set("contract_version", "2"), "schema"),
    ("reserved platform name", "refplat", _set("name", "infra"), "schema"),
    ("platform name with a hyphen", "refplat", _set("name", "ref-plat"), "schema"),
    ("platform name starting with a digit", "refplat", _set("name", "1plat"), "schema"),
    ("version not SemVer", "refplat", _set("version", "1.0"), "schema"),
    ("unknown runtime", "refplat", _set("runtime", "docker"), "schema"),
    ("unknown facility", "refplat", _set("requires", ["postgres", "network"]), "schema"),
    (
        "service with the job profile",
        "refplat",
        _set("services.1.profile", "gpu-trainer"),
        "schema",
    ),
    (
        "unknown relaxation",
        FIRST_PLATFORM,
        _set("services.6.relaxations", ["no-sandbox"]),
        "schema",
    ),
    (
        "job without digest",
        "refplat",
        _set("jobs.0.image", "docker.io/library/python:3.12"),
        "schema",
    ),
    (
        "job with a tag but no digest",
        "refplat",
        _set("jobs.0.image", "python@sha256:abc"),
        "schema",
    ),
    ("job with another profile", "refplat", _set("jobs.0.profile", "controller"), "schema"),
    ("job with two GPUs", "refplat", _set("jobs.0.resources.gpus", 2), "schema"),
    (
        "job output other than an adapter",
        "refplat",
        _set("jobs.0.outputs", {"dataset": "x"}),
        "schema",
    ),
    ("absolute config path", "refplat", _set("services.0.config", "/etc/passwd"), "schema"),
    (
        "config path escapes the release",
        "refplat",
        _set("services.0.config", "a/../../x"),
        "schema",
    ),
    ("docs path not normalized", "refplat", _set("docs", "./docs"), "schema"),
    ("backup path escapes", "refplat", _set("backup.paths", ["ctl/../../etc"]), "schema"),
    (
        "five telemetry labels",
        FIRST_PLATFORM,
        _set("telemetry.labels", ["a", "b", "c", "d", "e"]),
        "schema",
    ),
    ("unknown tool", FIRST_PLATFORM, _set("tools", ["texlive"]), "schema"),
    (
        "decision path with /v1/data",
        FIRST_PLATFORM,
        _set("policy.decision_path", "/v1/data/somewhere/x"),
        "schema",
    ),
    ("size without unit", "refplat", _set("jobs.0.resources.memory_max", "4096"), "schema"),
    # invariants
    (
        "second controller",
        "refplat",
        _add_service(name="ctl2", profile="controller", start=["bin/x"]),
        "controller-count",
    ),
    ("no controller", "refplat", _set("services.0.profile", "observer"), "controller-count"),
    (
        "second inference broker",
        FIRST_PLATFORM,
        _add_service(name="gw2", profile="inference-broker", start=["bin/x"]),
        "broker-count",
    ),
    (
        "duplicate service name",
        "refplat",
        _add_service(name="ctl", profile="observer", start=["bin/x"]),
        "duplicate-name",
    ),
    ("service and job share a name", "refplat", _set("jobs.0.name", "ctl"), "duplicate-name"),
    ("duplicate content package", "allprof", _set("content.1.name", "cases"), "duplicate-name"),
    ("agent secrets", "refplat", _set("services.1.secrets", ["key"]), "secrets-forbidden"),
    (
        "agent relaxation",
        "refplat",
        _set("services.1.relaxations", ["wx-memory"]),
        "relaxation-forbidden",
    ),
    (
        "agent on a non-dedicated socket",
        "refplat",
        _set("sockets.1", {"name": "session", "service": "ctl", "clients": ["untrusted-agent"]}),
        "agent-socket-not-dedicated",
    ),
    (
        "agent on a shared socket",
        "refplat",
        _set("sockets.0.clients", ["researchers", "untrusted-agent"]),
        "agent-socket-not-dedicated",
    ),
    (
        "dedicated socket shared with others",
        "refplat",
        _set("sockets.1.clients", ["untrusted-agent", "researchers"]),
        "dedicated-socket-shared",
    ),
    (
        "dedicated flag without the agent",
        "refplat",
        _set("sockets.0.untrusted_clients", True),
        "dedicated-socket-shared",
    ),
    (
        "agent on a restricted-operator socket",
        "allprof",
        _add_socket(
            name="agent", service="rop", clients=["untrusted-agent"], untrusted_clients=True
        ),
        "socket-client",
    ),
    (
        "agent on an observer socket",
        "allprof",
        _add_socket(
            name="agent", service="obs", clients=["untrusted-agent"], untrusted_clients=True
        ),
        "socket-client",
    ),
    (
        "researchers on a restricted-operator socket",
        "allprof",
        _set("sockets.7.clients", ["controller", "researchers"]),
        "socket-client",
    ),
    (
        "researchers on a sandbox-operator socket",
        "allprof",
        _set("sockets.6.clients", ["controller", "researchers"]),
        "socket-client",
    ),
    (
        "observer on a restricted-reader socket",
        "allprof",
        _set("sockets.8.clients", ["controller", "observer"]),
        "socket-client",
    ),
    (
        "job profile as a socket client",
        "allprof",
        _set("sockets.1.clients", ["gpu-trainer"]),
        "socket-client",
    ),
    (
        "agent serves a socket",
        "allprof",
        _add_socket(name="api", service="agt", clients=["controller"]),
        "socket-server",
    ),
    (
        "socket of an unknown service",
        "refplat",
        _set("sockets.0.service", "nope"),
        "socket-service",
    ),
    (
        "socket twice",
        "refplat",
        _add_socket(name="api", service="ctl", clients=["root"]),
        "socket-duplicate",
    ),
    (
        "service memory over cap",
        "refplat",
        _set("services.0.limits", {"memory_max": "64G"}),
        "over-cap",
    ),
    (
        "service tasks over cap",
        "refplat",
        _set("services.0.limits", {"tasks_max": 100000}),
        "over-cap",
    ),
    ("hook timeout over cap", "refplat", _set("hooks.drain.timeout_seconds", 7200), "over-cap"),
    ("job memory over cap", "refplat", _set("jobs.0.resources.memory_max", "128G"), "over-cap"),
    ("job CPUs over cap", "refplat", _set("jobs.0.resources.cpus", 64), "over-cap"),
    ("job timeout over cap", "refplat", _set("jobs.0.timeout_seconds", 100000), "over-cap"),
    (
        "start command escapes the release",
        "refplat",
        _set("services.0.start", ["../bin/x"]),
        "path",
    ),
    ("absolute start command", "refplat", _set("services.0.start", ["/usr/bin/python3"]), "path"),
    (
        "hook command escapes the release",
        "refplat",
        _set("hooks.health.run", ["a/../../b"]),
        "path",
    ),
    (
        "backup outside the state directories",
        "refplat",
        _set("backup.paths", ["ctl/other"]),
        "backup-path",
    ),
    ("start order on an unknown service", "refplat", _set("services.1.after", ["nope"]), "after"),
    ("start order on itself", "refplat", _set("services.1.after", ["agent"]), "after"),
    ("start order cycle", "refplat", _set("services.0.after", ["agent"]), "after"),
    (
        "decision path in infra's namespace",
        FIRST_PLATFORM,
        _set("policy.decision_path", "pinka/placeholder/active"),
        "policy-namespace",
    ),
    (
        "decision path in OPA's namespace",
        FIRST_PLATFORM,
        _set("policy.decision_path", "system/authz/allow"),
        "policy-namespace",
    ),
    # derived names
    (
        "database named like a reserved database",
        "refplat",
        _set("name", "postgres"),
        "reserved-name",
    ),
    ("roles matching a reserved pattern", "refplat", _set("name", "pg"), "reserved-name"),
    ("CLI with a reserved name", "refplat", _set("cli.name", "pinka"), "reserved-name"),
    (
        "service named like the migrate account",
        "refplat",
        _add_service(name="migrate", profile="observer", start=["bin/x"]),
        "name-clash",
    ),
    (
        "service named like the socket group",
        "refplat",
        _add_service(name="db", profile="observer", start=["bin/x"]),
        "name-clash",
    ),
    (
        "service named like the reader role",
        "refplat",
        _add_service(name="reader", profile="observer", start=["bin/x"]),
        "name-clash",
    ),
    (
        "service named like a group role",
        "refplat",
        _add_service(name="observer", profile="observer", start=["bin/x"]),
        "name-clash",
    ),
    (
        "client group longer than 32 characters",
        "refplat",
        _both(
            _set("name", "abcdefghijkl"),
            _set("sockets.0.name", "abcdefghijklmnop"),
            _set("services.0.name", "abcdefghij"),
            _set("services.1.after", ["abcdefghij"]),
            _set("sockets.1.service", "abcdefghij"),
            _set("sockets.0.service", "abcdefghij"),
            _set("backup.paths", ["abcdefghij/records"]),
        ),
        "name-too-long",
    ),
    # Owner decision on 3d.1: everything a manifest uses is declared in requires.
    (
        "inference broker without inference",
        FIRST_PLATFORM,
        _drop_requirement("inference"),
        "facility-undeclared",
    ),
    (
        "sandbox operator without incus",
        FIRST_PLATFORM,
        _drop_requirement("incus"),
        "facility-undeclared",
    ),
    (
        "restricted operator without incus",
        "refplat",
        _add_service(name="rop", profile="restricted-operator", start=[".venv/bin/rop"]),
        "facility-undeclared",
    ),
    ("job without gpu", "refplat", _drop_requirement("gpu"), "facility-undeclared"),
    (
        "CPU-only job without gpu",
        "refplat",
        _both(_drop_requirement("gpu"), _set("jobs.0.resources.gpus", 0)),
        "facility-undeclared",
    ),
    ("policy without opa", FIRST_PLATFORM, _drop_requirement("opa"), "facility-undeclared"),
    (
        "dashboards without telemetry",
        "allprof",
        _both(_drop_requirement("telemetry"), _delete("telemetry")),
        "facility-undeclared",
    ),
    (
        "telemetry labels without telemetry",
        "refplat",
        _both(_drop_requirement("telemetry"), _set("telemetry", {"labels": ["refplat.run"]})),
        "facility-undeclared",
    ),
    (
        "database backup without postgres",
        "refplat",
        _drop_requirement("postgres"),
        "facility-undeclared",
    ),
]


@pytest.mark.parametrize(
    ("case", "fixture", "mutate", "code"), INVALID, ids=[c[0] for c in INVALID]
)
def test_invalid_fixture(case: str, fixture: str, mutate: Mutation, code: str) -> None:
    data = manifest(fixture)
    mutate(data)
    found = codes(data)
    assert code in found, (case, found)


@pytest.mark.parametrize(
    ("case", "fixture", "mutate", "code"), INVALID, ids=[c[0] for c in INVALID]
)
def test_schema_agrees_with_the_reference_validator(
    case: str, fixture: str, mutate: Mutation, code: str
) -> None:
    data = manifest(fixture)
    mutate(data)
    ours = list(errors(data, CONTRACT.schema))
    reference = list(jsonschema.Draft202012Validator(CONTRACT.schema).iter_errors(data))
    assert bool(ours) == bool(reference), (case, ours, [e.message for e in reference])
    assert bool(ours) == (code == "schema"), case


def _contract_reserving(namespace: str, name: str) -> Any:
    reserved = dict(CONTRACT.reserved)
    current = reserved[namespace]
    reserved[namespace] = Namespace(current.names | {name}, current.patterns)
    return dataclasses.replace(CONTRACT, reserved=reserved)


@pytest.mark.parametrize(
    ("namespace", "name"),
    [
        ("accounts_groups", "refplat-ctl"),
        ("accounts_groups", "refplat-ctl-api"),
        ("units", "refplat-agent"),
        ("postgresql_roles", "refplat_ctl"),
        ("postgresql_databases", "refplat"),
        ("commands", "refplat"),
        ("platform_names", "refplat"),
    ],
)
def test_reserved_name_per_namespace(namespace: str, name: str) -> None:
    contract = _contract_reserving(namespace, name)
    problems = validate.check_manifest(manifest("refplat"), contract)
    assert any(p.code == "reserved-name" and name in p.message for p in problems), problems


def test_reserved_path_segment() -> None:
    paths = dict(CONTRACT.reserved_paths)
    paths["/srv/pinka/platforms"] = frozenset({"refplat"})
    contract = dataclasses.replace(CONTRACT, reserved_paths=paths)
    problems = validate.check_manifest(manifest("refplat"), contract)
    assert any(
        p.code == "reserved-name" and "/srv/pinka/platforms/refplat" in p.message for p in problems
    )


def test_names_existing_on_the_host() -> None:
    host = names.HostFacts(accounts={"refplat-ctl"}, groups={"refplat-db"}, commands={"refplat"})
    problems = validate.check_manifest(manifest("refplat"), CONTRACT, host)
    taken = {p.message.split("'")[1] for p in problems if p.code == "name-exists-on-host"}
    assert taken == {"refplat-ctl", "refplat-db", "refplat"}


def test_names_of_the_same_platform_are_its_own() -> None:
    derived = {d.name for d in names.derive(manifest("refplat"), CONTRACT)}
    host = names.HostFacts(
        accounts=set(derived), groups=set(derived), commands={"refplat"}, owned=derived
    )
    assert validate.check_manifest(manifest("refplat"), CONTRACT, host) == []


def test_owned_names_come_from_the_active_platform(tmp_path: Any) -> None:
    (tmp_path / "active.json").write_text('{"name": "refplat", "names": ["refplat-ctl"]}')
    assert names.host_facts("refplat", tmp_path).owned == {"refplat-ctl"}
    assert names.host_facts("other", tmp_path).owned == set()
    (tmp_path / "active.json").write_text("[]")
    assert names.host_facts("refplat", tmp_path).owned == set()


def test_derived_names_of_the_reference_platform() -> None:
    derived = {(d.namespace, d.name) for d in names.derive(manifest("refplat"), CONTRACT)}
    assert ("accounts_groups", "refplat-ctl") in derived
    assert ("accounts_groups", "refplat-agent") in derived
    assert ("accounts_groups", "refplat-tune") in derived
    assert ("accounts_groups", "refplat-ctl-session") in derived
    assert ("accounts_groups", "refplat-migrate") in derived
    assert ("postgresql_roles", "refplat_controller") in derived
    assert ("postgresql_roles", "refplat_ctl") in derived
    # The agent has no database role at all.
    assert ("postgresql_roles", "refplat_agent") not in derived
    assert ("postgresql_roles", "refplat_untrusted_agent") not in derived


@pytest.mark.parametrize(
    "text",
    [
        "a: &x 1\nb: *x\n",
        "contract_version: 2\ncontract_version: 2\n",
        "? [1, 2]\n: 3\n",
        "- 1\n- 2\n",
        "a: [1\n",
    ],
    ids=["alias", "duplicate-key", "non-string-key", "not-a-mapping", "broken"],
)
def test_unsafe_yaml_is_refused(text: str) -> None:
    result = validate.validate_text(text, CONTRACT)
    assert [p.code for p in result.problems] == ["yaml"]
    assert result.manifest is None


def test_strict_loader_reads_plain_yaml() -> None:
    from pinka_platform.loader import load_text  # noqa: PLC0415 - the unit under test

    assert load_text("a: {b: [1, 'x']}\n") == {"a": {"b": [1, "x"]}}
    with pytest.raises(ManifestError):
        load_text("")


@pytest.mark.parametrize(
    ("release_path", "package_path", "same_repo", "overlaps"),
    [
        ("platform", "cases", True, False),
        ("platform", "", True, True),
        ("", "cases", True, True),
        ("platform", "platform/cases", True, True),
        ("platform/sub", "platform", True, True),
        ("platform", "platforms", True, False),
        ("platform/", "./platform", True, True),
        ("platform", "", False, False),
    ],
)
def test_release_and_content_paths_must_not_overlap_in_one_repository(
    release_path: str, package_path: str, same_repo: bool, overlaps: bool
) -> None:
    repo = "git@github.com:example-org/pokus-platform.git"
    other = repo if same_repo else "https://git.example.com/example-org/cases.git"
    problems = checks.check_sources(
        checks.SourceRef(repo, release_path),
        {
            "cases": checks.SourceRef(
                other.removesuffix(".git") if same_repo else other, package_path
            )
        },
    )
    assert bool(problems) == overlaps
    assert all(p.code == "source-overlap" for p in problems)


@pytest.mark.parametrize(
    ("first", "second", "same_repo", "overlaps"),
    [
        ("cases", "datasets", True, False),
        ("cases", "cases/restricted", True, True),
        ("cases", "", True, True),
        ("cases", "cases", True, True),
        ("cases", "cases", False, False),
    ],
)
def test_content_packages_must_not_overlap_each_other_in_one_repository(
    first: str, second: str, same_repo: bool, overlaps: bool
) -> None:
    # Owner decision on 3d.1, an approved deviation: package against package too.
    repo = "https://git.example.com/example-org/content.git"
    problems = checks.check_sources(
        checks.SourceRef("https://git.example.com/example-org/platform.git", ""),
        {
            "cases": checks.SourceRef(repo, first),
            "notes": checks.SourceRef(
                repo if same_repo else "https://git.example.com/example-org/other.git", second
            ),
        },
    )
    assert [(p.code, p.where) for p in problems] == (
        [("source-overlap", "content.cases")] if overlaps else []
    )


def test_an_undeclared_facility_names_its_users() -> None:
    data = manifest("refplat")
    _add_service(name="rop", profile="restricted-operator", start=[".venv/bin/rop"])(data)
    data["requires"].remove("gpu")
    data["policy"] = {"bundle": "policy", "decision_path": "refplat/decide"}
    problems = validate.check_manifest(data, CONTRACT)
    assert [str(p) for p in problems] == [
        "requires: 'incus' is not declared but used by services 'rop' (restricted-operator)"
        " [facility-undeclared]",
        "requires: 'opa' is not declared but used by field 'policy' [facility-undeclared]",
        "requires: 'gpu' is not declared but used by jobs 'tune' (gpu-trainer)"
        " [facility-undeclared]",
    ]


def test_facilities_declared_but_unused_are_fine() -> None:
    data = manifest("refplat")
    data["requires"] = [*data["requires"], "opa", "incus", "inference"]
    assert codes(data) == set()
    data = manifest("refplat")
    data["requires"].remove("postgres")
    data["backup"]["database"] = False
    assert codes(data) == set()


def test_problems_render_as_one_line() -> None:
    data = manifest("refplat")
    data["services"][1]["secrets"] = ["key"]
    result = validate.validate_text(dump(data), CONTRACT)
    assert [str(p) for p in result.problems] == [
        "services[1].secrets: untrusted-agent has no secrets [secrets-forbidden]"
    ]


def test_summary_marks_dedicated_sockets_and_restricted_access() -> None:
    data = manifest(FIRST_PLATFORM)
    summary = validate.summary(data, CONTRACT)
    dedicated = {(s["service"], s["name"]) for s in summary["sockets"] if s["agent_dedicated"]}
    assert dedicated == {
        (s["service"], s["name"]) for s in data["sockets"] if s.get("untrusted_clients")
    }
    assert len(dedicated) == 3
    restricted = {s["name"] for s in summary["services"] if s["restricted_data"]}
    capable = {"controller", "restricted-operator", "restricted-reader"}
    assert restricted == {s["name"] for s in data["services"] if s["profile"] in capable}
    assert len(restricted) == 4
    text = validate.summary_text(summary)
    service, socket = sorted(dedicated)[0]
    assert f"socket {service}/{socket}: untrusted-agent  [dedicated to the agent]" in text
    assert f"content {data['content'][0]['name']} (restricted)" in text
    assert yaml.safe_load(dump(data))["name"] == summary["name"]
