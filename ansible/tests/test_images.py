"""Tests for the golden image pipeline.

Blueprints are checked against their schema, the lock and each other; the
in-image templates are rendered; the leakage lint and the export helper run
on fixture trees; the role's decisions are evaluated with Ansible's
templating. Nothing here talks to Incus.
"""

from __future__ import annotations

import base64
import bz2
import gzip
import hashlib
import io
import json
import lzma
import os
import re
import tarfile
from pathlib import Path
from typing import Any

import pinka_image_files as image_files
import pinka_leakage_lint as lint
import pytest
import yaml
from pinka_lab import checks as lab_checks
from pinka_lab import golden

import pinka
from ansible_testlib import (
    GROUP_VARS,
    HEADER,
    INFRA,
    ROLES,
    ansible_vars,
    evaluate,
    load_yaml,
    render_file,
    template_value,
)
from pinka_lockcheck import checks

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
        "base",
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


# Incus's rule for an image alias name, as Incus 6.0.6 applies it (the version
# the lock pins; tag v6.0.6, commit 5231e7a1beca905d57208b441040f9ebdc6a2c6f):
# validate.IsAPIName(name, true), shared/validate/validate.go lines 562-599.
# The daemon applies it to a new alias in imageAliasesPost
# (cmd/incusd/images.go line 3491) and to a renamed one in imageAliasPost
# (line 4103). `incus publish --alias` (cmd/incus/publish.go line 334) and
# `incus image import --alias` (cmd/incus/image.go line 932), the two commands
# the images role names an alias with, create it through ensureImageAliases
# (cmd/incus/utils.go lines 164-202), whose CreateImageAlias posts it to
# /1.0/images/aliases (client/incus_images.go lines 1036-1044), that is to
# imageAliasesPost.
INCUS_RULE_VERSION = "6.0.6"
# unicode.IsSpace (Go): the Latin-1 white space, then Unicode's White_Space.
GO_SPACE = frozenset(
    "\t\n\v\f\r \x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007"
    "\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000"
)
INCUS_RESERVED = ("$", "?", "&", "+", '"', "'", "`", "*")
INCUS_ENDS = re.compile(r"[a-zA-Z0-9]+.*[a-zA-Z0-9]+")


def incus_api_name_error(value: str, allow_slashes: bool) -> str | None:
    """Incus 6.0.6's IsAPIName: the error it returns for VALUE, or None."""
    if len(value.encode("utf-8")) > 64:  # Go's len() counts bytes
        return "Maximum name length is 64 characters"
    if any(c in GO_SPACE for c in value):
        return "Name cannot contain white space"
    reserved = INCUS_RESERVED if allow_slashes else (*INCUS_RESERVED, "/")
    for char in reserved:
        if char in value:
            # Go's %q; json.dumps quotes each of these characters the same way.
            return f"Name contains invalid character {json.dumps(char)}"
    # Go's regexp: ^...$ match the whole text and `.` matches no newline.
    if not INCUS_ENDS.fullmatch(value):
        return "Names must start and end with an alphanumeric character"
    return None


@pytest.mark.parametrize(
    ("value", "error"),
    [
        ("base/1.0.1", None),
        ("web-nginx-flask/1.0.1", None),
        ("a1", None),
        ("a/b", None),
        ("a" * 64, None),
        ("_base/1.0.0", "Names must start and end with an alphanumeric character"),
        ("base-", "Names must start and end with an alphanumeric character"),
        ("a", "Names must start and end with an alphanumeric character"),
        ("", "Names must start and end with an alphanumeric character"),
        ("\xe9/1", "Names must start and end with an alphanumeric character"),
        ("a\nb", "Name cannot contain white space"),
        ("a\xa0b", "Name cannot contain white space"),
        ("a\u3000b", "Name cannot contain white space"),
        ("a" * 65, "Maximum name length is 64 characters"),
        ("\xe9" * 33, "Maximum name length is 64 characters"),
        ("base/1.0.1+1", 'Name contains invalid character "+"'),
        ("a`b", 'Name contains invalid character "`"'),
        ('a"b', 'Name contains invalid character "\\""'),
        # The order of the checks: length, white space, the reserved characters
        # in their order, then the first and last character.
        ("a" * 64 + " ", "Maximum name length is 64 characters"),
        ("a b+", "Name cannot contain white space"),
        ("-+", 'Name contains invalid character "+"'),
        ("a*$b", 'Name contains invalid character "$"'),
    ],
)
def test_the_incus_alias_name_rule(value: str, error: str | None) -> None:
    assert incus_api_name_error(value, True) == error


# Each reserved character as Go's %q prints it.
GO_QUOTED = {
    "$": '"$"',
    "?": '"?"',
    "&": '"&"',
    "+": '"+"',
    '"': '"\\""',
    "'": '"\'"',
    "`": '"`"',
    "*": '"*"',
}


def test_the_reserved_characters_are_incus_s() -> None:
    assert tuple(GO_QUOTED) == INCUS_RESERVED


@pytest.mark.parametrize(("char", "quoted"), GO_QUOTED.items())
def test_every_reserved_character_is_refused(char: str, quoted: str) -> None:
    assert incus_api_name_error(f"a{char}b", True) == f"Name contains invalid character {quoted}"


def test_the_incus_rule_without_slashes() -> None:
    assert incus_api_name_error("a/b", False) == 'Name contains invalid character "/"'
    assert incus_api_name_error("a-b", False) is None


def test_the_alias_rule_is_the_pinned_incus_version() -> None:
    # The rule above is read from this version's source: read it again when the pin moves.
    pins = [p for p in LOCK["packages"]["incus"].values() if isinstance(p, dict)]
    assert pins
    for pin in pins:
        # A Debian version: an optional epoch, the upstream version, a revision.
        version = rf"([0-9]+:)?{re.escape(INCUS_RULE_VERSION)}([-~+].+)?"
        assert re.fullmatch(version, pin["incus"]), pin


def role_alias(name: str) -> str:
    """The alias the images role publishes and imports a blueprint's image with."""
    tasks = load_yaml(TASKS / "blueprint.yml")
    values = [
        t["ansible.builtin.set_fact"]["images_alias"]
        for t in tasks
        if "images_alias" in t.get("ansible.builtin.set_fact", {})
    ]
    assert len(values) == 1
    alias = template_value(values[0], {"images_name": name, "images_specs": {name: spec(name)}})
    assert isinstance(alias, str)
    return alias


@pytest.mark.parametrize("name", BLUEPRINTS)
def test_the_role_s_alias_is_blueprint_slash_version(name: str) -> None:
    assert role_alias(name) == f"{name}/{spec(name)['version']}"


@pytest.mark.parametrize("name", BLUEPRINTS)
def test_the_blueprint_s_alias_passes_incus_s_name_rule(name: str) -> None:
    alias = role_alias(name)
    assert incus_api_name_error(alias, True) is None, alias


@pytest.mark.parametrize("name", BLUEPRINTS)
def test_the_build_instance_is_named_the_same_everywhere(name: str) -> None:
    facts = load_yaml(TASKS / "build.yml")[0]["ansible.builtin.set_fact"]
    variables = {"images_name": name, "images_project": "build"}
    instance = template_value(facts["images_instance"], variables)
    command = template_value(facts["images_incus_exec"], variables)
    assert instance == f"build-{name}"
    assert command == ["incus", "exec", "--project", "build", instance, "--"]


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


def installed(path: Path) -> bool:
    """Whether a recipe file is installed into the image (``files/``)."""
    return path.parent.name == "files"


@pytest.mark.parametrize(
    "path",
    [p for p in recipe_files() if not installed(p)],
    ids=lambda p: p.relative_to(IMAGES).as_posix(),
)
def test_recipe_files_carry_the_managed_header(path: Path) -> None:
    relative = path.relative_to(INFRA).as_posix()
    head = path.read_text(encoding="utf-8").splitlines()[:3]
    assert any(HEADER.format(path=relative) in line for line in head), relative


@pytest.mark.parametrize(
    "path",
    [p for p in recipe_files() if installed(p)],
    ids=lambda p: p.relative_to(IMAGES).as_posix(),
)
def test_installed_files_carry_no_managed_header(path: Path) -> None:
    # Images name none of the project's names.
    assert "Managed by" not in path.read_text(encoding="utf-8"), path


# --- in-image files ---------------------------------------------------------------------


def agent_config(name: str, endpoint: str = "127.0.0.1:4317") -> dict[str, Any]:
    text = render_file(
        IMAGES / "base" / "files" / "otel-agent.yaml.j2",
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
        pinka_dir=str(INFRA),
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
        "pokus",
        "Pinka",
        "POTKA",
        "pinka-lab",
        "potka_orch",
        "org.pinka.managed",
    ],
)
def test_default_patterns_catch_the_leakage_tokens(token: str) -> None:
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
    source = (ROLES / "images" / "files" / "pinka_leakage_lint.py").read_text(encoding="utf-8")
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


# metadata.yaml in the form Incus 6.0.6 writes it at publication
# (gopkg.in/yaml.v2, internal/server/instance/drivers/driver_lxc.go, Export).
# The reader takes every scalar as its text, so a value YAML would read as a
# timestamp or a number is a string whether it is quoted (the all-digit
# commit) or plain (pinka.built_at).
METADATA_YAML = b"""architecture: x86_64
creation_date: 1794268800
properties:
  description: debian 12
  os: Debian
  pinka.built_at: 2026-11-08T00:00:00Z
  pinka.recipe_commit: "1234567"
  requirements.nesting: "true"
templates:
  hostname.tpl:
    when:
    - create
    template: hostname.tpl
"""


def tarball(
    members: list[tuple[str, bytes | None]],
    compression: str = "gz",
    kinds: dict[str, bytes] | None = None,
    pax: dict[str, str] | None = None,
) -> bytes:
    """A tarball as Incus exports it (gzip; a PAX header where a file has extended attributes).

    Args:
        members: Name and content (``None``: a directory).
        compression: ``gz``, ``xz``, ``bz2`` or ``none``.
        kinds: Member type by name (``tarfile.SYMTYPE`` and the like).
        pax: PAX records of every member (``SCHILY.xattr.*``).
    """
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.pax_headers = dict(pax or {})
            kind = (kinds or {}).get(name)
            if kind is not None:
                info.type = kind
                info.linkname = "elsewhere"
                tar.addfile(info)
            elif data is None:
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            else:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    data = buffer.getvalue()
    if compression == "xz":
        return lzma.compress(data)
    if compression == "bz2":
        return bz2.compress(data)
    if compression == "none":
        return data
    return gzip.compress(data)


def exported(directory: Path, meta: bytes, rootfs: bytes | None = None) -> image_files.ImageFiles:
    """An export of a split image (with ``rootfs``) or a unified one, verified."""
    if rootfs is None:
        fingerprint = hashlib.sha256(meta).hexdigest()
        (directory / f"{fingerprint}.tar.gz").write_bytes(meta)
    else:
        fingerprint = hashlib.sha256(meta + rootfs).hexdigest()
        (directory / f"meta-{fingerprint}.tar.gz").write_bytes(meta)
        (directory / f"{fingerprint}.tar.gz").write_bytes(rootfs)
    return image_files.verify(directory, fingerprint)


PUBLISHED = {
    "description": "debian 12",
    "os": "Debian",
    "pinka.built_at": "2026-11-08T00:00:00Z",
    "pinka.recipe_commit": "1234567",
    "requirements.nesting": "true",
}
XATTRS = {"SCHILY.xattr.security.selinux": "system_u:object_r:tmp_t:s0"}


def test_properties_come_from_the_metadata_of_a_split_export(tmp_path: Path) -> None:
    meta = tarball(
        [
            ("metadata.yaml", METADATA_YAML),
            ("templates", None),
            ("templates/hostname.tpl", b"{{ container.name }}\n"),
        ]
    )
    image = exported(tmp_path, meta, tarball([("rootfs", None)]))
    before = sorted(tmp_path.iterdir())
    # Every value a string, as Incus stores it: the timestamp and the
    # all-digit commit too.
    assert image_files.properties(tmp_path, image) == PUBLISHED
    # Nothing extracted to disk.
    assert sorted(tmp_path.iterdir()) == before


def test_properties_come_from_the_metadata_of_a_unified_export(tmp_path: Path) -> None:
    # The root file system follows, with links and devices; only the first
    # member is read. A PAX header carries the file's extended attributes.
    meta = tarball(
        [
            ("metadata.yaml", METADATA_YAML),
            ("rootfs", None),
            ("rootfs/bin", None),
            ("rootfs/dev/null", None),
        ],
        kinds={"rootfs/bin": tarfile.SYMTYPE, "rootfs/dev/null": tarfile.CHRTYPE},
        pax=XATTRS,
    )
    image = exported(tmp_path, meta)
    assert image_files.properties(tmp_path, image) == PUBLISHED


def test_an_export_without_properties_has_none(tmp_path: Path) -> None:
    image = exported(tmp_path, tarball([("metadata.yaml", b"architecture: x86_64\n")]))
    assert image_files.properties(tmp_path, image) == {}


@pytest.mark.parametrize(
    ("members", "kinds", "message"),
    [
        (
            [("templates", None), ("metadata.yaml", METADATA_YAML)],
            {},
            "first member is 'templates'",
        ),
        ([("./metadata.yaml", METADATA_YAML)], {}, "first member is './metadata.yaml'"),
        ([("rootfs/metadata.yaml", METADATA_YAML)], {}, "first member is 'rootfs/metadata.yaml'"),
        ([("metadata.yaml", None)], {}, "is not a regular file"),
        ([("metadata.yaml", b"")], {"metadata.yaml": tarfile.SYMTYPE}, "is not a regular file"),
        ([("metadata.yaml", b"")], {"metadata.yaml": tarfile.LNKTYPE}, "is not a regular file"),
        ([("metadata.yaml", b"")], {"metadata.yaml": tarfile.CHRTYPE}, "is not a regular file"),
        ([("metadata.yaml", b"")], {"metadata.yaml": tarfile.BLKTYPE}, "is not a regular file"),
        ([("metadata.yaml", b"")], {"metadata.yaml": tarfile.FIFOTYPE}, "is not a regular file"),
        ([], {}, "first member is nothing"),
        ([("metadata.yaml", b"- a list\n")], {}, "is not a mapping"),
        ([("metadata.yaml", b"")], {}, "is not a mapping"),
        ([("metadata.yaml", b"properties: [a]\n")], {}, "are not strings"),
        ([("metadata.yaml", b"properties:\n  os:\n    nested: map\n")], {}, "are not strings"),
        ([("metadata.yaml", b"properties: {os: [a]}\n")], {}, "are not strings"),
        ([("metadata.yaml", b"properties: {os: '\n")], {}, "is not YAML"),
    ],
)
def test_properties_refuse_an_export_they_cannot_read(
    tmp_path: Path, members: list[tuple[str, bytes | None]], kinds: dict[str, bytes], message: str
) -> None:
    image = exported(tmp_path, tarball(members, kinds=kinds))
    with pytest.raises(ValueError, match=re.escape(message)):
        image_files.properties(tmp_path, image)


@pytest.mark.parametrize("compression", ["xz", "bz2", "none"])
def test_properties_read_only_gzip(tmp_path: Path, compression: str) -> None:
    # The build publishes with --compression gzip.
    image = exported(tmp_path, tarball([("metadata.yaml", METADATA_YAML)], compression))
    with pytest.raises(ValueError, match="not a gzip tarball"):
        image_files.properties(tmp_path, image)


@pytest.mark.parametrize("data", [b"", b"\x1f", b"PK\x03\x04"])
def test_properties_refuse_a_file_too_short_or_without_gzip_magic(
    tmp_path: Path, data: bytes
) -> None:
    image = exported(tmp_path, data, b"root file system")
    with pytest.raises(ValueError, match=r"not a gzip tarball$"):
        image_files.properties(tmp_path, image)


def test_properties_refuse_a_file_that_is_not_gzip(tmp_path: Path) -> None:
    image = exported(tmp_path, b"\x1f\x8b but no deflate stream", b"root file system")
    with pytest.raises(ValueError, match="not a gzip tarball"):
        image_files.properties(tmp_path, image)


def test_properties_refuse_a_sparse_metadata(tmp_path: Path) -> None:
    # A PAX GNU sparse 1.0 member reads as a regular file named metadata.yaml;
    # only its sparse map tells it apart.
    real = b"properties: {os: x}\n"
    sparse_map = b"1\n0\n%d\n" % len(real)
    data = sparse_map + b"\0" * (512 - len(sparse_map)) + real
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as tar:
        info = tarfile.TarInfo("GNUSparseFile.0/metadata.yaml")
        info.size = len(data)
        info.pax_headers = {
            "GNU.sparse.major": "1",
            "GNU.sparse.minor": "0",
            "GNU.sparse.name": "metadata.yaml",
            "GNU.sparse.realsize": str(len(real)),
        }
        tar.addfile(info, io.BytesIO(data))
    image = exported(tmp_path, gzip.compress(buffer.getvalue()))
    with pytest.raises(ValueError, match="is not a regular file"):
        image_files.properties(tmp_path, image)


def test_properties_refuse_a_global_header(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with tarfile.open(
        fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT, pax_headers={"comment": "x"}
    ) as tar:
        info = tarfile.TarInfo("metadata.yaml")
        info.size = len(METADATA_YAML)
        tar.addfile(info, io.BytesIO(METADATA_YAML))
    image = exported(tmp_path, gzip.compress(buffer.getvalue()))
    with pytest.raises(ValueError, match="a global header"):
        image_files.properties(tmp_path, image)


def test_properties_refuse_metadata_over_the_limit(tmp_path: Path) -> None:
    large = b"x" * (image_files.METADATA_LIMIT + 1)
    image = exported(tmp_path, tarball([("metadata.yaml", large)]))
    with pytest.raises(ValueError, match="larger than"):
        image_files.properties(tmp_path, image)


def test_the_uncompressed_bytes_read_are_limited(tmp_path: Path) -> None:
    # Ten MiB of PAX records before metadata.yaml compress to a few KiB: no
    # more than the limit is decompressed, and the member is incomplete.
    image = exported(
        tmp_path,
        tarball([("metadata.yaml", METADATA_YAML)], pax={"SCHILY.xattr.user.x": "0" * (10 << 20)}),
    )
    path = tmp_path / image.files[0]
    assert path.stat().st_size < 64 << 10
    assert len(image_files.head(path)) == image_files.UNCOMPRESSED_LIMIT
    with pytest.raises(ValueError, match=r"no complete metadata\.yaml"):
        image_files.properties(tmp_path, image)


def test_the_compressed_bytes_read_are_limited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Random bytes do not compress: with a small compressed limit the reader
    # stops reading the file there.
    noise = os.urandom(256 << 10)
    image = exported(tmp_path, tarball([("metadata.yaml", METADATA_YAML), ("rootfs/x", noise)]))
    path = tmp_path / image.files[0]
    monkeypatch.setattr(image_files, "COMPRESSED_LIMIT", 4096)
    monkeypatch.setattr(image_files, "UNCOMPRESSED_LIMIT", 1 << 30)
    reads: list[int] = []
    real_open = Path.open

    def counting_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        handle = real_open(self, *args, **kwargs)
        real_read = handle.read

        def read(size: int = -1) -> bytes:
            data: bytes = real_read(size)
            reads.append(len(data))
            return data

        handle.read = read
        return handle

    monkeypatch.setattr(Path, "open", counting_open)
    start = image_files.head(path)
    assert sum(reads) == 4096
    # The start of the tarball, no more than those bytes give.
    assert start[:13] == b"metadata.yaml"
    assert len(start) < 16 << 10


def test_helper_cli_reports_the_properties_only_when_asked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    image = exported(tmp_path, tarball([("metadata.yaml", METADATA_YAML)]))
    args = ["verify", "--dir", str(tmp_path), "--fingerprint", image.fingerprint]
    assert image_files.main(args) == 0
    assert "properties" not in json.loads(capsys.readouterr().out)
    assert image_files.main([*args, "--properties"]) == 0
    assert json.loads(capsys.readouterr().out)["properties"] == PUBLISHED
    other = tmp_path / "other"
    other.mkdir()
    bare = exported(other, tarball([("templates", None)]))
    args = ["verify", "--dir", str(other), "--fingerprint", bare.fingerprint]
    assert image_files.main(args) == 0
    capsys.readouterr()
    assert image_files.main([*args, "--properties", "--write-sums"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False
    assert "first member is 'templates'" in result["error"]
    # No sums are written for an export whose properties cannot be read.
    assert not (other / "SHA256SUMS").exists()


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


IMAGE = {"fingerprint": "f" * 64, "aliases": [{"name": "base/1.0.0"}]}

# What the build publishes (build.yml, "Publish the image") and the export
# records (export.yml, "Write the export record") for one image.
GOLDEN_PROPERTIES = {
    "pinka.recipe_commit": "c" * 40,
    "pinka.built_at": "2026-01-01T12:00:00Z",
    "pinka.base_fingerprint": "b" * 64,
    "requirements.nesting": "false",
    "description": "the base",
}
GOLDEN_RECORD: dict[str, Any] = {
    "schema_version": 2,
    "fingerprint": "f" * 64,
    "nesting": False,
    "properties": dict(GOLDEN_PROPERTIES),
}
GOLDEN = dict(IMAGE, properties=dict(GOLDEN_PROPERTIES))


@pytest.mark.parametrize(
    ("rebuild", "names", "image", "record", "action"),
    [
        (False, [], {}, {}, "build"),
        (False, [], IMAGE, {"fingerprint": "f" * 64}, "none"),
        (False, [], {}, {"fingerprint": "f" * 64}, "import"),
        # Not exported again: the next task refuses both (an alias without its export).
        (False, [], IMAGE, {}, "none"),
        (False, [], IMAGE, {"fingerprint": "e" * 64}, "none"),
        (True, [], IMAGE, {"fingerprint": "f" * 64}, "build"),
        (False, ["base"], IMAGE, {"fingerprint": "f" * 64}, "build"),
        (False, ["probe-client"], IMAGE, {"fingerprint": "f" * 64}, "none"),
    ],
)
def test_action_of_a_blueprint(
    rebuild: bool, names: list[str], image: dict[str, Any], record: dict[str, Any], action: str
) -> None:
    template = task("blueprint.yml", "Choose the action for the blueprint")[
        "ansible.builtin.set_fact"
    ]
    result = evaluate(
        expression(template["images_action"]),
        {
            "images_rebuild": rebuild,
            "images_rebuild_names": names,
            "images_name": "base",
            "images_image": image,
            "images_record": record,
        },
    )
    assert result.strip() == action


@pytest.mark.parametrize(
    ("selected", "rebuild", "order", "names"),
    [
        ("", "", None, []),
        ("base,probe-client", "probe-client", ["base", "probe-client"], ["probe-client"]),
    ],
)
def test_blueprints_of_a_run(
    selected: str, rebuild: str, order: list[str] | None, names: list[str]
) -> None:
    """``pinka images build`` selects blueprints through the environment."""
    facts = task("main.yml", "Choose the blueprints of this run")["ansible.builtin.set_fact"]
    variables = {
        "images_selected": selected,
        "images_rebuild_selected": rebuild,
        "images_blueprints": DEFAULTS["images_blueprints"],
    }
    assert evaluate(expression(facts["images_order"]), variables) == (
        order or DEFAULTS["images_blueprints"]
    )
    assert evaluate(expression(facts["images_rebuild_names"]), variables) == names


def test_image_by_alias_filter() -> None:
    images = [{"fingerprint": "a", "aliases": None}, IMAGE]
    assert evaluate("images | pinka_image_by_alias('base/1.0.0')", {"images": images}) == IMAGE
    assert evaluate("images | pinka_image_by_alias('x/1')", {"images": images}) == {}


# dpkg-query output of a build instance (synthetic versions).
DPKG_OUTPUT = (
    "install ok installed\tzlib1g\t1:1.0-1\tamd64\n"
    "install ok installed\tlibc6\t2.0-1\tamd64\n"
    "install ok installed\tlibc6\t2.0-1\ti386\n"
    "deinstall ok config-files\told-tool\t0.9-1\tamd64\n"
    "hold ok installed\ttzdata\t3.0-1\tall\n"
)
PACKAGES = [
    {"name": "libc6", "version": "2.0-1", "architecture": "amd64"},
    {"name": "libc6", "version": "2.0-1", "architecture": "i386"},
    {"name": "tzdata", "version": "3.0-1", "architecture": "all"},
    {"name": "zlib1g", "version": "1:1.0-1", "architecture": "amd64"},
]


def test_the_build_records_every_installed_package() -> None:
    # Name, version and architecture of every installed package, so every
    # result can be traced to the exact package versions of its targets.
    argv = task("build.yml", "Record the installed packages")["ansible.builtin.command"]["argv"]
    exec_prefix = ["incus", "exec", "build-base", "--"]
    assert evaluate(expression(argv), {"images_incus_exec": exec_prefix}) == [
        *exec_prefix,
        "dpkg-query",
        "--show",
        "--showformat",
        pinka.DPKG_FORMAT,
    ]
    facts = task("build.yml", "Use the published image")["ansible.builtin.set_fact"]
    listed = evaluate(
        expression(facts["images_package_list"]),
        {"images_packages": {"stdout": DPKG_OUTPUT}},
    )
    assert listed == PACKAGES  # removed packages left out, sorted by name and architecture


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("install ok installed\tlibc6\t2.0-1\n", "four fields"),
        ("install ok installed\tlibc6\t\tamd64\n", "four fields"),
        ("install ok installed\tlibc6\t2.0-1\tamd64\n" * 2, "listed twice"),
    ],
)
def test_a_malformed_package_list_is_refused(text: str, message: str) -> None:
    with pytest.raises(pinka.PinkaFilterError, match=message):
        pinka.dpkg_packages(text)


@pytest.mark.parametrize(
    "record", [{}, {"fingerprint": "e" * 64, "packages": [PACKAGES[0]]}, {"fingerprint": "f" * 64}]
)
def test_the_export_record_carries_the_package_list(record: dict[str, Any]) -> None:
    # The record names the packages the build of this run listed, whatever an older record had.
    content = task("export.yml", "Write the export record")["ansible.builtin.copy"]["content"]
    text = evaluate(
        expression(content),
        {
            "images_name": "base",
            "images_spec": {"version": "1.0.0", "type": "container", "nesting": False, "base": {}},
            "images_alias": "base/1.0.0",
            "images_image": {**IMAGE, "architecture": "x86_64", "properties": {}},
            "images_written": {"stdout": json.dumps({"files": ["f.tar.gz"], "properties": {}})},
            "images_package_list": PACKAGES,
            "images_record": record,
        },
    )
    assert json.loads(text)["packages"] == PACKAGES


def test_provisioning_variables() -> None:
    content = task(
        "build.yml", "Write the provisioning variables and the leakage lint configuration"
    )
    variables = {
        "pinka_versions": {
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
            "images_alias": "base/1.0.0",
            "images_image": {},
            "pinka_bootstrap": {"repo_commit": "c" * 40, "infra_dirty": True},
            "images_base_fingerprint": "b" * 64,
            "images_spec": spec("docker-host"),
        },
    )
    assert argv[:9] == [
        "incus",
        "publish",
        "--project",
        "build",
        "build-base",
        "--alias",
        "base/1.0.0",
        # The export record reads metadata.yaml from the export
        # (pinka_image_files.py --properties), whatever compression the server
        # or the project names.
        "--compression",
        "gzip",
    ]
    properties = dict(a.split("=", 1) for a in argv[9:])
    assert properties["pinka.recipe_commit"] == "c" * 40 + "-dirty"
    assert properties["pinka.base_fingerprint"] == "b" * 64
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", properties["pinka.built_at"])
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
    [
        ({}, True),
        (dict(IMAGE, properties=dict(GOLDEN_PROPERTIES)), True),
        ({"fingerprint": "f" * 64, "properties": {}}, True),
        (dict(IMAGE, properties={}, update_source={"server": "https://example.com"}), False),
        (dict(IMAGE, fingerprint="e" * 64, properties=dict(GOLDEN_PROPERTIES)), False),
    ],
)
def test_restore_keeps_aliases_and_checks_fingerprints(image: dict[str, Any], ok: bool) -> None:
    passed, message = refusal(
        "restore_one.yml",
        "Refuse an alias that is not the golden image of its export",
        image,
        GOLDEN_RECORD,
        images_alias="base/1.0.0",
        images_export_dir="/srv/pinka/images/base/1.0.0",
    )
    assert passed is ok
    if not ok:
        assert "sudo incus image alias delete base/1.0.0" in message
        assert "run the restore again" in message


# --- golden aliases -----------------------------------------------------------------------


def refusal(
    file: str, name: str, image: dict[str, Any], record: dict[str, Any], **variables: Any
) -> tuple[bool, str]:
    """Whether a refusal task of the role lets an image pass, and its message."""
    check = task(file, name)
    # Only the variable the task must read holds the image: a task that read
    # a stale one (such as images_in_store) would see no image at all.
    values = {"images_image": image, "images_record": record, **variables}
    values["images_problems"] = evaluate(expression(check["vars"]["images_problems"]), values)
    assert check["ansible.builtin.assert"]["quiet"] is True
    passed = evaluate(check["ansible.builtin.assert"]["that"], values)
    message = str(template_value(check["ansible.builtin.assert"]["fail_msg"], values))
    return passed, message


def with_properties(**changes: Any) -> dict[str, Any]:
    properties = {k: v for k, v in {**GOLDEN_PROPERTIES, **changes}.items() if v is not None}
    return dict(GOLDEN, properties=properties)


GOLDEN_CASES: list[tuple[dict[str, Any], dict[str, Any], list[str]]] = [
    (GOLDEN, GOLDEN_RECORD, []),
    ({}, GOLDEN_RECORD, []),
    ({}, {}, []),
    (GOLDEN, {}, [f"it points to {'f' * 64}, which has no export record"]),
    (
        GOLDEN,
        dict(GOLDEN_RECORD, fingerprint="e" * 64),
        [f"it points to {'f' * 64}, but its export recorded {'e' * 64}"],
    ),
    (
        dict(GOLDEN, update_source={"server": "https://example.com", "protocol": "incus"}),
        GOLDEN_RECORD,
        ["it has an update source (https://example.com), which infra's images never have"],
    ),
    (
        dict(GOLDEN, update_source={"server": "\x1b]0;x"}),
        GOLDEN_RECORD,
        ['it has an update source ("\\u001b]0;x"), which infra\'s images never have'],
    ),
    (
        dict(GOLDEN, update_source={"server": "x" * 200}),
        GOLDEN_RECORD,
        [f"it has an update source ({'x' * 77}...), which infra's images never have"],
    ),
    (
        dict(GOLDEN, update_source={"server": "https://example.com"}),
        dict(GOLDEN_RECORD, fingerprint="e" * 64),
        [f"it points to {'f' * 64}, but its export recorded {'e' * 64}"],
    ),
    (dict(GOLDEN, update_source=None), GOLDEN_RECORD, []),
    # The properties prove nothing: a client can rewrite them, bootstrap puts them back.
    (with_properties(**{"pinka.recipe_commit": "d" * 40}), GOLDEN_RECORD, []),
    (dict(GOLDEN, properties={}, public=True, auto_update=True), GOLDEN_RECORD, []),
    # Infra reads only records of schema 2, which name every property.
    *[
        (
            GOLDEN,
            dict(GOLDEN_RECORD, schema_version=version),
            [f"its export record has schema {shown}, not 2; build the version again"],
        )
        for version, shown in ((1, "1"), ("2", '"2"'), (2.0, "2.0"), (True, "true"), (3, "3"))
    ],
    (
        GOLDEN,
        {k: v for k, v in GOLDEN_RECORD.items() if k != "schema_version"},
        ["its export record has schema null, not 2; build the version again"],
    ),
    # The fingerprint first: a record of another image is named as such.
    (
        GOLDEN,
        dict(GOLDEN_RECORD, schema_version=1, fingerprint="e" * 64),
        [f"it points to {'f' * 64}, but its export recorded {'e' * 64}"],
    ),
]


@pytest.mark.parametrize(("image", "record", "expected"), GOLDEN_CASES)
def test_golden_image_problems(
    image: dict[str, Any], record: dict[str, Any], expected: list[str]
) -> None:
    assert pinka.golden_image_problems(image, record) == expected
    assert (
        evaluate("image | pinka_golden_image_problems(record)", {"image": image, "record": record})
        == expected
    )


DRIFT_CASES: list[tuple[dict[str, Any], dict[str, Any], list[str]]] = [
    (GOLDEN, GOLDEN_RECORD, []),
    # The record names every property: an added key goes, a changed one comes back.
    (
        with_properties(**{"requirements.cgroup": "v1", "description": "x"}),
        GOLDEN_RECORD,
        [
            'its property description is x, not "the base" as in its export record',
            "it has the property requirements.cgroup (v1), which its build did not set",
        ],
    ),
    (
        with_properties(**{"\x1b[2J": "\x1b"}),
        GOLDEN_RECORD,
        ['it has the property "\\u001b[2J" ("\\u001b"), which its build did not set'],
    ),
    # An empty value is no property (Incus stores one only when it creates a
    # record, never when it updates one): neither removed nor reported, and a
    # property that is empty is missing.
    (with_properties(**{"requirements.cgroup": ""}), GOLDEN_RECORD, []),
    (
        with_properties(**{"os": ""}),
        dict(GOLDEN_RECORD, properties=dict(GOLDEN_RECORD["properties"], os="")),
        [],
    ),
    (
        with_properties(**{"pinka.built_at": ""}),
        GOLDEN_RECORD,
        ["it lacks its property pinka.built_at (2026-01-01T12:00:00Z in its export record)"],
    ),
    # A record of another schema: refused, never put back.
    *[
        (
            with_properties(**{"requirements.cgroup": "v1"}),
            dict(GOLDEN_RECORD, schema_version=v),
            [],
        )
        for v in (1, "2", 2.0, True, None)
    ],
    (dict(GOLDEN, public=False, auto_update=False), GOLDEN_RECORD, []),
    ({}, GOLDEN_RECORD, []),
    # Not golden: refused, never put back.
    (with_properties(**{"pinka.built_at": "x"}), {}, []),
    (with_properties(**{"pinka.built_at": "x"}), dict(GOLDEN_RECORD, fingerprint="e" * 64), []),
    (
        dict(with_properties(**{"pinka.built_at": "x"}), update_source={"server": "s"}),
        GOLDEN_RECORD,
        [],
    ),
    (
        with_properties(**{"pinka.recipe_commit": "d" * 40}),
        GOLDEN_RECORD,
        [f"its property pinka.recipe_commit is {'d' * 40}, not {'c' * 40} as in its export record"],
    ),
    (
        with_properties(**{"pinka.built_at": None, "pinka.base_fingerprint": "\x1b[2J"}),
        GOLDEN_RECORD,
        [
            f'its property pinka.base_fingerprint is "\\u001b[2J", not {"b" * 64}'
            " as in its export record",
            "it lacks its property pinka.built_at (2026-01-01T12:00:00Z in its export record)",
        ],
    ),
    (
        with_properties(**{"requirements.nesting": "true"}),
        GOLDEN_RECORD,
        ["its property requirements.nesting is true, not false as in its export record"],
    ),
    # The record's properties decide, not its nesting field.
    (with_properties(**{"requirements.nesting": "false"}), dict(GOLDEN_RECORD, nesting=True), []),
    # A record value that is no text (the helper writes none) names no property.
    (
        with_properties(),
        dict(GOLDEN_RECORD, properties=dict(GOLDEN_RECORD["properties"], **{"pinka.built_at": 5})),
        ["it has the property pinka.built_at (2026-01-01T12:00:00Z), which its build did not set"],
    ),
    (
        with_properties(**{"user.extra": "x"}),
        GOLDEN_RECORD,
        ["it has the property user.extra (x), which its build did not set"],
    ),
    (
        dict(GOLDEN, public=True, auto_update=True),
        GOLDEN_RECORD,
        ["it is public", "its auto_update is on"],
    ),
    (
        with_properties(**{"pinka.recipe_commit": "c" * 40 + "-dirty"}),
        dict(
            GOLDEN_RECORD,
            properties=dict(
                GOLDEN_RECORD["properties"], **{"pinka.recipe_commit": "c" * 40 + "-dirty"}
            ),
        ),
        [],
    ),
]


def put_body(record: dict[str, Any], image: dict[str, Any]) -> dict[str, Any]:
    """The PUT that puts an image back: the record's properties, both flags off, its profiles."""
    wanted = {k: v for k, v in record["properties"].items() if isinstance(v, str) and v}
    body: dict[str, Any] = {"properties": wanted, "public": False, "auto_update": False}
    if isinstance(image.get("profiles"), list):
        body["profiles"] = image["profiles"]
    return body


@pytest.mark.parametrize(("image", "record", "changes"), DRIFT_CASES)
def test_what_a_client_changed_is_put_back(
    image: dict[str, Any], record: dict[str, Any], changes: list[str]
) -> None:
    expected = {"changes": changes, "body": put_body(record, image) if changes else {}}
    assert pinka.golden_image_drift(image, record) == expected
    variables = {"image": image, "record": record}
    assert evaluate("image | pinka_golden_image_drift(record)", variables) == expected


def test_the_put_back_is_bounded_by_the_record() -> None:
    # However many properties a client adds, the body holds the record's
    # properties and the image's profiles as they are.
    added = {f"user.k{n}": "v" * 100 for n in range(5000)}
    image = dict(with_properties(**added), profiles=["default", "gpu"], public=True)
    drift = pinka.golden_image_drift(image, GOLDEN_RECORD)
    assert len(drift["changes"]) == 5001
    assert drift["body"] == {
        "properties": GOLDEN_PROPERTIES,
        "public": False,
        "auto_update": False,
        "profiles": ["default", "gpu"],
    }
    assert len(json.dumps(drift["body"])) < 1024
    # Without a list of profiles the body names none (Incus then links only its default profile).
    assert (
        "profiles"
        not in pinka.golden_image_drift(dict(image, profiles=None), GOLDEN_RECORD)["body"]
    )


@pytest.mark.parametrize(
    ("image", "record"),
    [(i, r) for i, r, _ in GOLDEN_CASES] + [(i, r) for i, r, _ in DRIFT_CASES],
)
def test_doctor_applies_the_same_rules(image: dict[str, Any], record: dict[str, Any]) -> None:
    assert golden.problems(image, record) == pinka.golden_image_problems(image, record)
    assert golden.drift(image, record) == pinka.golden_image_drift(image, record)


def test_the_two_rules_have_the_same_forms() -> None:
    assert golden.RECORD_SCHEMA == pinka.GOLDEN_RECORD_SCHEMA == 2
    assert golden.SHOWN == pinka.GOLDEN_SHOWN
    assert golden.PLAIN.pattern == pinka.GOLDEN_PLAIN.pattern


def test_the_fixture_holds_every_property_the_build_publishes() -> None:
    publish = task("build.yml", "Publish the image")["ansible.builtin.command"]["argv"]
    argv = evaluate(
        expression(publish),
        {
            "images_project": "build",
            "images_instance": "build-base",
            "images_alias": "base/1.0.0",
            "images_image": {},
            "pinka_bootstrap": {"repo_commit": "c" * 40, "infra_dirty": False},
            "images_base_fingerprint": "b" * 64,
            "images_spec": spec("docker-host"),
        },
    )
    assert argv[7:9] == ["--compression", "gzip"]
    properties = dict(a.split("=", 1) for a in argv[9:])
    assert set(properties) == set(GOLDEN_PROPERTIES)


def test_the_export_record_reads_the_properties_from_the_export() -> None:
    argv = task("export.yml", "Check the export and record its sha256 sums")[
        "ansible.builtin.command"
    ]["argv"]
    assert argv[-2:] == ["--write-sums", "--properties"]


def test_an_exported_image_is_golden() -> None:
    # The record export.yml writes for the image the build published passes the rule.
    content = task("export.yml", "Write the export record")["ansible.builtin.copy"]["content"]
    image = dict(GOLDEN, architecture="x86_64")
    # The properties come from the export's metadata.yaml, not from the
    # store's record, which a client can rewrite between the publication and
    # the listing after it.
    raced: dict[str, Any] = {
        **image,
        "properties": {**GOLDEN_PROPERTIES, "requirements.cgroup": "v1"},
    }
    written = {"files": ["f.tar.gz"], "properties": GOLDEN_PROPERTIES}
    text = evaluate(
        expression(content),
        {
            "images_name": "base",
            "images_spec": {"version": "1.0.0", "type": "container", "nesting": False, "base": {}},
            "images_alias": "base/1.0.0",
            "images_image": raced,
            "images_written": {"stdout": json.dumps(written)},
            "images_package_list": [],
            "images_record": {},
        },
    )
    record = json.loads(text)
    assert pinka.golden_image_problems(image, record) == []
    # The record names every property the build published (schema 2): nothing to put back.
    assert record["schema_version"] == 2
    assert record["properties"] == GOLDEN_PROPERTIES
    assert pinka.golden_image_drift(image, record) == {"changes": [], "body": {}}
    assert pinka.golden_image_drift(raced, record)["changes"] == [
        "it has the property requirements.cgroup (v1), which its build did not set"
    ]
    # A client removes them all and adds a requirement that stops every launch:
    # the PUT sets exactly the record's.
    changed = dict(image, properties={"requirements.cgroup": "v1"})
    assert pinka.golden_image_drift(changed, record)["body"]["properties"] == GOLDEN_PROPERTIES


@pytest.mark.parametrize(
    ("image", "record", "ok"),
    [
        (GOLDEN, GOLDEN_RECORD, True),
        (GOLDEN, {}, False),
        (GOLDEN, dict(GOLDEN_RECORD, fingerprint="e" * 64), False),
        (dict(GOLDEN, update_source={"server": "https://example.com"}), GOLDEN_RECORD, False),
        (with_properties(**{"pinka.recipe_commit": "d" * 40}), GOLDEN_RECORD, True),
    ],
)
def test_an_alias_that_is_not_golden_is_refused(
    image: dict[str, Any], record: dict[str, Any], ok: bool
) -> None:
    name = "Refuse an alias that is not the golden image of its export"
    passed, message = refusal(
        "blueprint.yml",
        name,
        image,
        record,
        images_alias="base/1.0.0",
        images_name="base",
        images_export_dir="/srv/pinka/images/base/1.0.0",
    )
    assert passed is ok
    assert task("blueprint.yml", name)["when"] == "images_action == 'none'"
    if not ok:
        assert message.startswith("base/1.0.0 is not the golden image of this version: it")
        assert "(sudo incus image alias delete base/1.0.0)" in message
        assert "sudo pinka images build base --rebuild" in message
        assert "an earlier run stopped before it recorded the export" in message


def test_the_role_exports_only_what_it_built() -> None:
    export = task("blueprint.yml", "Export the blueprint's image")
    assert export["when"] == ["images_action == 'build'", "not ansible_check_mode"]
    choice = task("blueprint.yml", "Choose the action for the blueprint")
    assert "export" not in choice["ansible.builtin.set_fact"]["images_action"]
    tasks = [t["name"] for t in load_yaml(TASKS / "blueprint.yml")]
    # The refusals come before anything uses or exports the image.
    choose = tasks.index("Choose the action for the blueprint")
    assert tasks[choose + 1 : choose + 3] == [
        "Refuse an export record of another schema",
        "Refuse an alias that is not the golden image of its export",
    ]


def schema_refusal(file: str, record: dict[str, Any], **variables: Any) -> tuple[bool, str]:
    """Whether the schema refusal of a task file lets a record pass, and its message."""
    check = task(file, "Refuse an export record of another schema")
    values = {"images_record": record, **variables}
    values["images_unread"] = evaluate(expression(check["vars"]["images_unread"]), values)
    assert check["ansible.builtin.assert"]["quiet"] is True
    passed = evaluate(check["ansible.builtin.assert"]["that"], values)
    message = str(template_value(check["ansible.builtin.assert"]["fail_msg"], values))
    return passed, message


@pytest.mark.parametrize(
    ("record", "ok"),
    [
        (GOLDEN_RECORD, True),
        ({}, True),
        (dict(GOLDEN_RECORD, schema_version=1), False),
        ({k: v for k, v in GOLDEN_RECORD.items() if k != "schema_version"}, False),
    ],
)
def test_a_record_of_another_schema_is_refused_until_rebuilt(
    record: dict[str, Any], ok: bool
) -> None:
    variables = {
        "images_name": "base",
        "images_export_dir": "/srv/pinka/images/base/1.0.0",
        "images_record_path": "/srv/pinka/images/base/1.0.0/build.json",
        "images_alias": "base/1.0.0",
    }
    for file in ("blueprint.yml", "restore_one.yml"):
        passed, message = schema_refusal(
            file, dict(record, blueprint="base") if record else {}, **variables
        )
        assert passed is ok
        if not passed:
            assert (
                "/srv/pinka/images/base/1.0.0/build.json: its export record has schema" in message
            )
            assert "sudo pinka images build base --rebuild" in message
    # Not when the blueprint is built again: the build writes a record of schema 2.
    check = task("blueprint.yml", "Refuse an export record of another schema")
    assert check["when"] == "images_action != 'build'"
    names = [t["name"] for t in load_yaml(TASKS / "restore_one.yml")]
    assert names.index(check["name"]) < names.index("Import the exported image")


def test_every_version_is_put_back() -> None:
    exports = task("blueprint.yml", "Look for the exports of the blueprint")
    assert exports["ansible.builtin.stat"]["path"] == "{{ images_export_root }}/{{ images_name }}"
    files = task("blueprint.yml", "Find the export records of every version of the blueprint")
    assert files["ansible.builtin.find"] == {
        "paths": "{{ images_export_root }}/{{ images_name }}",
        "patterns": "build.json",
        "recurse": True,
        "depth": 2,
    }
    # A blueprint without exports yet: no find (it would warn), nothing read.
    assert files["when"] == "images_exports.stat.isdir | default(false)"
    for stat in ({"exists": False}, {"exists": True, "isdir": False}):
        assert evaluate(files["when"], {"images_exports": {"stat": stat}}) is False
    assert evaluate(files["when"], {"images_exports": {"stat": {"isdir": True}}}) is True
    read = task("blueprint.yml", "Read the export records of every version of the blueprint")
    skipped = {"skipped": True, "changed": False}
    assert evaluate(expression(read["loop"]), {"images_version_files": skipped}) == []
    find = task(
        "blueprint.yml",
        "Find what a client of the image store changed in the blueprint's golden images",
    )
    old = dict(GOLDEN_RECORD, schema_version=2, alias="base/0.9.0", fingerprint="9" * 64)
    old["properties"] = dict(GOLDEN_PROPERTIES)
    current = dict(
        GOLDEN_RECORD, schema_version=2, alias="base/1.0.0", properties=dict(GOLDEN_PROPERTIES)
    )
    extra: dict[str, Any] = {**GOLDEN_PROPERTIES, "user.extra": "x"}
    listing: list[dict[str, Any]] = [
        {**GOLDEN, "fingerprint": "9" * 64, "aliases": [{"name": "base/0.9.0"}], "public": True},
        {**GOLDEN, "properties": extra, "profiles": ["default"]},
    ]
    results = [
        {"content": base64.b64encode(json.dumps(r).encode()).decode()} for r in (old, current)
    ]
    drifts = evaluate(
        expression(find["ansible.builtin.set_fact"]["images_drifts"]),
        {
            "images_list": {"stdout": json.dumps(listing)},
            "images_version_records": {"results": results},
        },
    )
    put_back = {"properties": GOLDEN_PROPERTIES, "public": False, "auto_update": False}
    assert drifts == [
        {
            "alias": "base/0.9.0",
            "fingerprint": "9" * 64,
            "changes": ["it is public"],
            "body": put_back,
        },
        {
            "alias": "base/1.0.0",
            "fingerprint": "f" * 64,
            "changes": ["it has the property user.extra (x), which its build did not set"],
            "body": {**put_back, "profiles": ["default"]},
        },
    ]
    put = task(
        "blueprint.yml",
        "Put back what a client of the image store changed in the blueprint's golden images",
    )
    assert put["when"] == "not ansible_check_mode"
    assert put["loop"] == "{{ images_drifts }}"
    request = {
        key: template_value(value, {"item": drifts[1], "images_incus_socket": "/s"})
        for key, value in put["ansible.builtin.uri"].items()
    }
    # As root (the play's own user), over Incus's socket: no remote, no
    # project, no shell and no command line; a JSON body the record bounds.
    assert request == {
        "unix_socket": "/s",
        "url": f"http://incus/1.0/images/{'f' * 64}",
        "method": "PUT",
        "body_format": "json",
        "body": {**put_back, "profiles": ["default"]},
        "status_code": 200,
        "use_proxy": False,
    }
    assert load_yaml(ROLES / "images" / "vars" / "main.yml")["images_incus_socket"] == (
        "/var/lib/incus/unix.socket"
    )
    # The listing the body's profiles come from is the default project's, the
    # PUT's own, whatever project root's client is switched to.
    for file in ("blueprint.yml", "restore_one.yml"):
        listing_task = task(file, "List the images of the image store")
        assert listing_task["ansible.builtin.command"]["argv"] == [
            "incus",
            "image",
            "list",
            "--project",
            "default",
            "--format",
            "json",
        ]
    tasks = [t["name"] for t in load_yaml(TASKS / "blueprint.yml")]
    refuse = tasks.index("Refuse an alias that is not the golden image of its export")
    assert tasks[refuse + 1 : refuse + 7] == [
        exports["name"],
        files["name"],
        "Read the export records of every version of the blueprint",
        find["name"],
        put["name"],
        "Report the planned action in check mode",
    ]
    # Before anything uses an image: the build of a later blueprint, the import, the export.
    assert tasks.index(put["name"]) < tasks.index("Build the blueprint")
    report = task("blueprint.yml", "Report the planned action in check mode")[
        "ansible.builtin.debug"
    ]["msg"]
    shown = str(
        template_value(
            report,
            {"images_alias": "base/1.0.0", "images_action": "none", "images_drifts": drifts},
        )
    )
    assert shown == (
        "base/1.0.0: none; put back: base/0.9.0: it is public; base/1.0.0: it has the property"
        " user.extra (x), which its build did not set"
    )
    assert (
        str(
            template_value(
                report,
                {"images_alias": "base/1.0.0", "images_action": "none", "images_drifts": []},
            )
        )
        == "base/1.0.0: none"
    )


def test_the_check_mode_report_is_shortened() -> None:
    # A client can add any number of properties: ten changes and a count.
    drifts = [
        {"alias": "base/1.0.0", "changes": [f"change {n}" for n in range(8)]},
        {"alias": "\x1b[2J", "changes": ["it is public", "its auto_update is on", "a", "b"]},
    ]
    assert pinka.golden_drift_report(drifts) == (
        "; ".join(f"base/1.0.0: change {n}" for n in range(8))
        + '; "\\u001b[2J": it is public; "\\u001b[2J": its auto_update is on; and 2 more'
    )
    assert pinka.golden_drift_report(drifts[:1]) == "; ".join(
        f"base/1.0.0: change {n}" for n in range(8)
    )
    ten = [{"alias": "a/1", "changes": [str(n) for n in range(10)]}]
    assert pinka.golden_drift_report(ten).count("; ") == 9
    assert "more" not in pinka.golden_drift_report(ten)
    assert pinka.golden_drift_report(None) == ""
    assert pinka.GOLDEN_SHOWN_CHANGES == lab_checks.GOLDEN_SHOWN_PROBLEMS


def test_drifts_of_versions_that_are_not_golden_are_left_out() -> None:
    record = dict(GOLDEN_RECORD, alias="base/1.0.0")
    changed = dict(GOLDEN, public=True)
    assert pinka.golden_image_drifts([changed], [record])[0]["changes"] == ["it is public"]
    assert pinka.golden_image_drifts([changed], [dict(record, fingerprint="e" * 64)]) == []
    assert pinka.golden_image_drifts([changed], [dict(record, alias="base/2.0.0")]) == []
    assert pinka.golden_image_drifts([changed], [dict(record, alias=None)]) == []
    assert pinka.golden_image_drifts(None, None) == []


def test_a_copy_in_the_store_is_replaced_never_adopted() -> None:
    # Any client can put the export's files in the store with its own properties
    # and update source: the copy is deleted and the export imported with the alias.
    tasks = [t["name"] for t in load_yaml(TASKS / "import.yml")]
    assert tasks == [
        "Check the export before importing it",
        "Find the exported image in the image store",
        "Delete the copy of the exported image that is already in the store",
        "Import the exported image",
    ]
    store = task("import.yml", "Find the exported image in the image store")[
        "ansible.builtin.set_fact"
    ]
    copy = with_properties(**{"pinka.built_at": "2026-02-02T12:00:00Z"})
    listing = {"stdout": json.dumps([{"fingerprint": "e" * 64}, copy])}
    found = evaluate(
        expression(store["images_in_store"]),
        {"images_list": listing, "images_record": GOLDEN_RECORD},
    )
    assert found == copy
    empty = evaluate(
        expression(store["images_in_store"]),
        {"images_list": {"stdout": "[]"}, "images_record": GOLDEN_RECORD},
    )
    assert empty == {}
    delete = task(
        "import.yml", "Delete the copy of the exported image that is already in the store"
    )
    assert delete["when"] == "images_in_store != {}"
    argv = evaluate(
        expression(delete["ansible.builtin.command"]["argv"][3]), {"images_record": GOLDEN_RECORD}
    )
    assert delete["ansible.builtin.command"]["argv"][:3] == ["incus", "image", "delete"]
    assert argv == "f" * 64
    imported = task("import.yml", "Import the exported image")
    assert "when" not in imported
    command = evaluate(
        expression(imported["ansible.builtin.command"]["argv"]),
        {
            "images_import_files": {
                "stdout": json.dumps({"files": ["meta.tar.xz", "root.squashfs"]})
            },
            "images_export_dir": "/srv/pinka/images/base/1.0.0",
            "images_alias": "base/1.0.0",
        },
    )
    assert command == [
        "incus",
        "image",
        "import",
        "/srv/pinka/images/base/1.0.0/meta.tar.xz",
        "/srv/pinka/images/base/1.0.0/root.squashfs",
        "--alias",
        "base/1.0.0",
    ]
