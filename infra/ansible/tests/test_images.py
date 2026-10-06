"""Tests for the golden image pipeline (BUILD_PROMPT §7.13).

Blueprints are checked against their schema, the lock and each other; the
in-image templates are rendered; the leakage lint and the export helper run
on fixture trees; the role's decisions are evaluated with Ansible's
templating. Nothing here talks to Incus.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pokus_image_files as image_files
import pokus_leakage_lint as lint
import pytest
import yaml

from ansible_testlib import (
    GROUP_VARS,
    HEADER,
    INFRA,
    ROLES,
    ansible_vars,
    evaluate,
    load_yaml,
    render_file,
)
from pokus_lockcheck import checks

IMAGES = INFRA / "images"
SCHEMA = INFRA / "tools" / "lockcheck" / "schema" / "image-blueprint.schema.json"
LOCK = load_yaml(INFRA / "versions.lock.yml")
BLUEPRINTS = sorted(p.name for p in IMAGES.iterdir() if (p / "image.yaml").is_file())
DEFAULTS = load_yaml(ROLES / "images" / "defaults" / "main.yml")
TASKS = ROLES / "images" / "tasks"


def spec(name: str) -> dict[str, Any]:
    result: dict[str, Any] = load_yaml(IMAGES / name / "image.yaml")
    return result


def default_config() -> dict[str, Any]:
    config = {
        "paths": DEFAULTS["images_leakage_paths"],
        "patterns": DEFAULTS["images_leakage_patterns"],
        "allow": DEFAULTS["images_leakage_allow"],
    }
    config["compiled"] = [re.compile(p) for p in config["patterns"]]
    return config


def added(text: str, path: str = "etc/example.conf") -> list[str]:
    """Tokens of a text that the default configuration would fail a build on."""
    config = default_config()
    return sorted(
        {
            token
            for token in lint.matches(config["compiled"], text)
            if not lint.allowed(("content", path, token), config["allow"], config["compiled"])
        }
    )


# --- blueprints ---------------------------------------------------------------------------


def test_the_required_blueprints_exist() -> None:
    required = {
        "_base",
        "web-nginx-flask",
        "pg-primary",
        "redis-cache",
        "docker-host",
        "probe-client",
    }
    assert required <= set(BLUEPRINTS)
    assert sorted(DEFAULTS["images_blueprints"]) == BLUEPRINTS


@pytest.mark.parametrize("name", BLUEPRINTS)
def test_blueprint_matches_its_schema(name: str) -> None:
    findings = checks.schema_findings(spec(name), SCHEMA)
    assert [str(f) for f in findings] == []
    assert spec(name)["name"] == name


@pytest.mark.parametrize("name", BLUEPRINTS)
def test_blueprint_files(name: str) -> None:
    directory = IMAGES / name
    cloud_init = (directory / "cloud-init.yaml").read_text(encoding="utf-8")
    assert cloud_init.splitlines()[0] == "#cloud-config"
    assert isinstance(yaml.safe_load(cloud_init), dict)
    plays = load_yaml(directory / "provision.yml")
    assert isinstance(plays, list)
    assert [p["hosts"] for p in plays] == ["all"]
    listed = spec(name)["tests"]
    present = sorted(p.relative_to(directory).as_posix() for p in (directory / "tests").iterdir())
    assert sorted(listed) == present


def test_build_order_and_bases() -> None:
    order = DEFAULTS["images_blueprints"]
    upstream = LOCK["incus_base_images"]["images"]
    for index, name in enumerate(order):
        base = spec(name)["base"]
        if "blueprint" in base:
            assert base["blueprint"] in order[:index], name
            assert spec(base["blueprint"])["type"] == spec(name)["type"]
        else:
            assert base["image"] in upstream, name
            types = {"container": "container", "vm": "virtual-machine"}
            assert upstream[base["image"]]["type"] == types[spec(name)["type"]]


@pytest.mark.parametrize("name", BLUEPRINTS)
def test_docker_images_are_pinned_by_digest(name: str) -> None:
    targets = LOCK["containers"]["target_images"]
    for key in spec(name)["docker_images"]:
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", targets[key]["digest"]), key
    if spec(name)["docker_images"]:
        assert spec(name)["nesting"], "a container engine inside a container needs nesting"


def recipe_files() -> list[Path]:
    return sorted(p for p in IMAGES.rglob("*") if p.is_file())


@pytest.mark.parametrize("path", recipe_files(), ids=lambda p: p.relative_to(IMAGES).as_posix())
def test_recipe_files_carry_the_managed_header(path: Path) -> None:
    relative = path.relative_to(INFRA).as_posix()
    head = path.read_text(encoding="utf-8").splitlines()[:3]
    assert any(HEADER.format(path=relative) in line for line in head), relative


# --- in-image files ---------------------------------------------------------------------


def agent_config(name: str, endpoint: str = "127.0.0.1:4317") -> dict[str, Any]:
    text = render_file(
        IMAGES / "_base" / "files" / "otel-agent.yaml.j2",
        {"image": spec(name), "otlp_endpoint": endpoint},
    )
    result: dict[str, Any] = yaml.safe_load(text)
    return result


@pytest.mark.parametrize("name", BLUEPRINTS)
def test_agent_configuration(name: str) -> None:
    config = agent_config(name)
    assert config["exporters"]["otlp_grpc"]["endpoint"] == "127.0.0.1:4317"
    assert "units" not in config["receivers"]["journald"]  # the whole journal
    logs = config["service"]["pipelines"]["logs"]["receivers"]
    log_files = spec(name)["telemetry"]["log_files"]
    assert logs == (["journald", "file_log"] if log_files else ["journald"])
    if log_files:
        assert config["receivers"]["file_log"]["include"] == log_files
    # The agent listens on nothing and carries no identity of a run.
    assert not any(r.startswith("otlp") for r in config["receivers"])
    assert config["processors"]["resource_detection"]["detectors"] == ["env", "system"]


def test_agent_configuration_for_vm_targets() -> None:
    variables = ansible_vars(
        GROUP_VARS,
        ROLES / "images" / "defaults" / "main.yml",
        ROLES / "images" / "vars" / "main.yml",
        pokus_infra_dir=str(INFRA),
    )
    endpoint = evaluate("images_otlp_endpoint.vm", variables)
    assert endpoint == "198.18.0.1:4317"
    assert evaluate("images_otlp_endpoint.container", variables) == "127.0.0.1:4317"


def rendered_recipe_files() -> list[tuple[str, str]]:
    """Every file a recipe installs, rendered with the variables of the build."""
    out = []
    target = LOCK["containers"]["target_images"]["docker_host_web"]
    variables = {
        "image": spec("web-nginx-flask"),
        "otlp_endpoint": "127.0.0.1:4317",
        "web_image": f"{target['image']}@{target['digest']}",
    }
    for path in sorted(IMAGES.glob("*/files/*")):
        text = (
            render_file(path, variables)
            if path.suffix == ".j2"
            else path.read_text(encoding="utf-8")
        )
        out.append((path.relative_to(IMAGES).as_posix(), text))
    return out


@pytest.mark.parametrize(("name", "text"), rendered_recipe_files(), ids=lambda v: v)
def test_installed_files_pass_the_leakage_lint(name: str, text: str) -> None:
    assert added(text) == [], name
    assert added(Path(name).name) == [], name


def test_docker_host_unit_runs_the_pinned_image() -> None:
    target = LOCK["containers"]["target_images"]["docker_host_web"]
    reference = f"{target['image']}@{target['digest']}"
    text = render_file(
        IMAGES / "docker-host" / "files" / "web-container.service.j2", {"web_image": reference}
    )
    assert f"--pull never {reference}" in text


# --- leakage lint -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "token",
    [
        "ABC-123",
        "DOCKER-001",
        "fault",
        "Faults",
        "inject",
        "injected",
        "ground_truth",
        "GroundTruth",
        "scenario",
    ],
)
def test_default_patterns_cover_the_specification(token: str) -> None:
    assert added(f"x = {token}\n") != []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Default-Start: 2 3 4 5\nDefaultLimitCORE=0\nlisten 80 default_server;\n", []),
        ("default_fault_injection = on\n", ["default_fault_injection"]),
        ("XDOCKER-0012\n", ["XDOCKER-0012"]),
        ("ISO-8859-1\n", ["ISO-8859-1"]),
    ],
)
def test_benign_words_are_cut_out_and_the_rest_is_checked(text: str, expected: list[str]) -> None:
    assert added(text) == expected


def test_path_limited_allow_rules() -> None:
    comment = "# lose data in the worst scenario\n"
    assert added(comment, "etc/redis/redis.conf") == []
    assert added(comment, "etc/nginx/nginx.conf") == ["scenario"]


def write(root: Path, relative: str, content: str | bytes) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")


def test_scan_finds_every_kind(tmp_path: Path) -> None:
    write(tmp_path, "etc/app/settings.conf", "mode = fault\n")
    write(tmp_path, "opt/tool/DB-001.txt", "ok\n")
    write(tmp_path, "etc/hostname", "scenario-host\n")
    write(tmp_path, "lib/systemd/system/inject-net.service", "[Unit]\n")
    write(tmp_path, "srv/app/.git/HEAD", "ref: refs/heads/main\n")
    write(tmp_path, "proc/1/environ", "")  # never walked
    write(tmp_path, "proc/x/.git/HEAD", "")
    write(tmp_path, "usr/local/bin/blob", b"\0fault\0")  # binary contents are skipped
    write(tmp_path, "var/lib/fault.txt", "fault\n")  # outside the configured paths
    found = lint.scan(tmp_path, default_config())
    assert found == {
        ("content", "etc/app/settings.conf", "fault"),
        ("name", "opt/tool/DB-001.txt", "DB-001"),
        ("content", "etc/hostname", "scenario-host"),
        ("hostname", "etc/hostname", "scenario-host"),
        ("unit", "inject-net.service", "inject-net"),
        ("git", "srv/app/.git", ".git"),
    }


def test_large_files_and_symlinks_are_not_read(tmp_path: Path) -> None:
    write(tmp_path, "etc/big.txt", "x" * lint.MAX_BYTES + "fault")
    write(tmp_path, "var/target.txt", "fault\n")
    (tmp_path / "etc" / "link.txt").symlink_to(tmp_path / "var" / "target.txt")
    assert lint.scan(tmp_path, default_config()) == set()


def test_tokens_are_bounded() -> None:
    text = "a" * 500 + "fault" + "b" * 500
    (token,) = lint.matches([re.compile("fault")], text)
    assert len(token) == 2 * lint.MAX_TOKEN + len("fault")


def test_check_subtracts_the_baseline() -> None:
    config = default_config()
    base = {("content", "etc/a.conf", "scenario")}
    found = base | {("content", "etc/b.conf", "scenario"), ("content", "etc/c.conf", "defaults")}
    added_findings = lint.check(found, base, config["allow"], config["compiled"])
    assert added_findings == [("content", "etc/b.conf", "scenario")]


def run_lint(tmp_path: Path, *args: str) -> int:
    config = tmp_path / "config.json"
    if not config.exists():
        config.write_text(
            json.dumps({k: v for k, v in default_config().items() if k != "compiled"}),
            encoding="utf-8",
        )
    return lint.main([*args, "--config", str(config)])


def test_cli_scan_then_check(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "root"
    write(root, "etc/security/faillock.conf", "# in this scenario\n")
    baseline = tmp_path / "baseline.json"
    assert run_lint(tmp_path, "scan", "--root", str(root), "--out", str(baseline)) == 0
    assert json.loads(baseline.read_text()) == [
        ["content", "etc/security/faillock.conf", "scenario"]
    ]
    assert run_lint(tmp_path, "check", "--root", str(root), "--baseline", str(baseline)) == 0
    write(root, "etc/motd", "Welcome to CACHE-007\n")
    assert run_lint(tmp_path, "check", "--root", str(root), "--baseline", str(baseline)) == 1
    out = capsys.readouterr().out
    assert "leakage: content etc/motd: CACHE-007" in out
    assert "faillock" not in out


def test_cli_usage_errors(tmp_path: Path) -> None:
    assert run_lint(tmp_path, "check", "--root", str(tmp_path)) == 2  # no baseline
    (tmp_path / "config.json").write_text('{"paths": [], "patterns": ["("], "allow": []}')
    assert run_lint(tmp_path, "scan", "--root", str(tmp_path)) == 2
    (tmp_path / "config.json").write_text('{"paths": [], "patterns": [], "allow": [{}]}')
    assert run_lint(tmp_path, "scan", "--root", str(tmp_path)) == 2
    (tmp_path / "config.json").write_text('{"paths": [], "patterns": []}')
    assert run_lint(tmp_path, "scan", "--root", str(tmp_path)) == 2


def test_lint_runs_on_the_python_of_ubuntu_24_04() -> None:
    source = (ROLES / "images" / "files" / "pokus_leakage_lint.py").read_text(encoding="utf-8")
    imports = set(re.findall(r"^(?:from|import) ([\w.]+)", source, flags=re.MULTILINE))
    assert imports <= {
        "__future__",
        "argparse",
        "json",
        "os",
        "re",
        "sys",
        "collections.abc",
        "pathlib",
        "typing",
    }


# --- export helper ------------------------------------------------------------------------


def make_unified(directory: Path) -> str:
    data = b"unified image tarball"
    fingerprint = hashlib.sha256(data).hexdigest()
    (directory / f"{fingerprint}.tar.gz").write_bytes(data)
    return fingerprint


def make_split(directory: Path) -> str:
    meta, rootfs = b"metadata tarball", b"root file system"
    fingerprint = hashlib.sha256(meta + rootfs).hexdigest()
    (directory / f"meta-{fingerprint}.tar.xz").write_bytes(meta)
    (directory / f"{fingerprint}.squashfs").write_bytes(rootfs)
    return fingerprint


def test_verify_unified_and_split_images(tmp_path: Path) -> None:
    unified = make_unified(tmp_path)
    image = image_files.verify(tmp_path, unified)
    assert (image.split, image.files) == (False, [f"{unified}.tar.gz"])
    split = make_split(tmp_path)
    image = image_files.verify(tmp_path, split)
    assert image.split
    assert image.files == [f"meta-{split}.tar.xz", f"{split}.squashfs"]
    assert image.sha256[f"{split}.squashfs"] == hashlib.sha256(b"root file system").hexdigest()


def test_sums_are_written_once(tmp_path: Path) -> None:
    fingerprint = make_split(tmp_path)
    image = image_files.verify(tmp_path, fingerprint)
    assert image_files.write_sums(tmp_path, image)
    assert not image_files.write_sums(tmp_path, image)
    lines = (tmp_path / "SHA256SUMS").read_text().splitlines()
    assert [line.split("  ")[1] for line in lines] == image.files
    assert (tmp_path / "SHA256SUMS").stat().st_mode & 0o777 == 0o640


@pytest.mark.parametrize(
    ("fingerprint", "message"),
    [("abc", "not a full fingerprint"), ("0" * 64, "expected one image file")],
)
def test_verify_refuses(tmp_path: Path, fingerprint: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        image_files.verify(tmp_path, fingerprint)


def test_verify_detects_changed_files(tmp_path: Path) -> None:
    fingerprint = make_split(tmp_path)
    (tmp_path / f"{fingerprint}.squashfs").write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash to"):
        image_files.verify(tmp_path, fingerprint)


def test_helper_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    fingerprint = make_unified(tmp_path)
    args = ["verify", "--dir", str(tmp_path), "--fingerprint", fingerprint]
    assert image_files.main([*args, "--write-sums"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert (result["ok"], result["changed"], result["files"]) == (
        True,
        True,
        [f"{fingerprint}.tar.gz"],
    )
    assert image_files.main(["verify", "--dir", str(tmp_path), "--fingerprint", "f" * 64]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


# --- role decisions -----------------------------------------------------------------------


def task(file: str, name: str) -> dict[str, Any]:
    def walk(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for t in tasks:
            out.append(t)
            for section in ("block", "rescue", "always"):
                out += walk(t.get(section) or [])
        return out

    matches = [t for t in walk(load_yaml(TASKS / file)) if t.get("name") == name]
    assert len(matches) == 1, name
    return matches[0]


def expression(template: str) -> str:
    text = template.strip()
    assert text.startswith("{{") and text.endswith("}}")
    return text[2:-2]


IMAGE = {"fingerprint": "f" * 64, "aliases": [{"name": "_base/1.0.0"}]}


@pytest.mark.parametrize(
    ("rebuild", "image", "record", "action"),
    [
        (False, {}, {}, "build"),
        (False, IMAGE, {"fingerprint": "f" * 64}, "none"),
        (False, {}, {"fingerprint": "f" * 64}, "import"),
        (False, IMAGE, {}, "export"),
        (False, IMAGE, {"fingerprint": "e" * 64}, "export"),
        (True, IMAGE, {"fingerprint": "f" * 64}, "build"),
    ],
)
def test_action_of_a_blueprint(
    rebuild: bool, image: dict[str, Any], record: dict[str, Any], action: str
) -> None:
    template = task("blueprint.yml", "Choose the action for the blueprint")[
        "ansible.builtin.set_fact"
    ]
    result = evaluate(
        expression(template["images_action"]),
        {"images_rebuild": rebuild, "images_image": image, "images_record": record},
    )
    assert result.strip() == action


def test_image_by_alias_filter() -> None:
    images = [{"fingerprint": "a", "aliases": None}, IMAGE]
    assert evaluate("images | pokus_image_by_alias('_base/1.0.0')", {"images": images}) == IMAGE
    assert evaluate("images | pokus_image_by_alias('x/1')", {"images": images}) == {}


def test_provisioning_variables() -> None:
    content = task(
        "build.yml", "Write the provisioning variables and the leakage lint configuration"
    )
    variables = {
        "pokus_versions": {
            "binaries": LOCK["binaries"],
            "containers": LOCK["containers"],
        },
        "ansible_facts": {"architecture": "aarch64"},
        "images_spec": spec("docker-host"),
        "images_otlp_endpoint": {"container": "127.0.0.1:4317"},
    }
    entry = content["loop"][0]["content"]
    agent = evaluate(expression(entry["otel_agent"]), variables)
    assert agent == LOCK["binaries"]["otelcol_contrib"]["assets"]["aarch64"]
    targets = evaluate(expression(entry["target_images"]), variables)
    assert targets == [LOCK["containers"]["target_images"]["docker_host_web"]]


def test_unverified_upstream_fingerprint_is_refused() -> None:
    check = task("upstream.yml", "Refuse an unverified upstream base image")[
        "ansible.builtin.assert"
    ]
    for value, ok in (("TODO(verify)", False), ("", False), ("a" * 64, True)):
        assert evaluate(check["that"], {"images_base_fingerprint": value}) is ok


def test_publish_sets_the_contract_properties() -> None:
    publish = task("build.yml", "Publish the image")["ansible.builtin.command"]["argv"]
    argv = evaluate(
        expression(publish),
        {
            "images_project": "build",
            "images_instance": "build-base",
            "images_alias": "_base/1.0.0",
            "images_image": {},
            "pokus_bootstrap": {"repo_commit": "c" * 40, "infra_dirty": True},
            "images_base_fingerprint": "b" * 64,
            "images_spec": spec("docker-host"),
        },
    )
    assert argv[:7] == [
        "incus",
        "publish",
        "--project",
        "build",
        "build-base",
        "--alias",
        "_base/1.0.0",
    ]
    properties = dict(a.split("=", 1) for a in argv[7:])
    assert properties["pokus.recipe_commit"] == "c" * 40 + "-dirty"
    assert properties["pokus.base_fingerprint"] == "b" * 64
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", properties["pokus.built_at"])
    assert properties["requirements.nesting"] == "true"  # Incus refuses to start it without nesting
    assert properties["description"] == spec("docker-host")["description"]


def test_clean_step_removes_identity_and_history() -> None:
    script = (ROLES / "images" / "files" / "image-clean.sh").read_text(encoding="utf-8")
    for needle in (
        "apt-get clean",
        ".bash_history",
        "/var/log",
        "cloud-init clean --logs --machine-id",
        "/etc/ssh/ssh_host_*",
        "/etc/hostname",
        "/var/tmp/*",  # noqa: S108 - a string the clean script must contain, not a path used here
    ):
        assert needle in script, needle


@pytest.mark.parametrize(
    ("image", "ok"),
    [({}, True), ({"fingerprint": "f" * 64}, True), ({"fingerprint": "e" * 64}, False)],
)
def test_restore_keeps_aliases_and_checks_fingerprints(image: dict[str, Any], ok: bool) -> None:
    check = task("restore_one.yml", "Refuse an alias that points to a different image")
    assert (
        evaluate(
            check["ansible.builtin.assert"]["that"],
            {"images_image": image, "images_record": {"fingerprint": "f" * 64}},
        )
        is ok
    )
