"""Deployment (§7.14): prepare, build, pull, publish, activate, replace and purge."""

from __future__ import annotations

import json
import stat
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from deploy_testlib import (
    ME,
    NOW,
    add_release,
    git,
    make_repo,
    prepare,
    schema,
    settings,
)
from platform_testlib import CONTRACT, manifest

from pinka_platform import cli, deploy, mirror, names


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return make_repo(tmp_path)


@pytest.fixture
def paths(tmp_path: Path) -> deploy.Paths:
    return deploy.Paths.under(tmp_path / "host")


def codes(exc: pytest.ExceptionInfo[deploy.Refused]) -> list[str]:
    return [p.code for p in exc.value.problems]


def refused(paths: deploy.Paths, s: deploy.Settings) -> list[str]:
    with pytest.raises(deploy.Refused) as exc:
        prepare(paths, s)
    assert not list(paths.staging.glob("run-*")), "a refusal leaves no run directory"
    return codes(exc)


def deployed(paths: deploy.Paths, s: deploy.Settings) -> dict[str, Any]:
    plan = prepare(paths, s)
    deploy.publish(plan, paths, now=lambda: NOW)
    deploy.activate(plan, paths, now=lambda: NOW)
    deploy.clean(Path(plan["run_dir"]), paths)
    return plan


# --- prepare --------------------------------------------------------------------


def test_prepare_stages_and_plans(repo: Path, paths: deploy.Paths) -> None:
    plan = prepare(paths, settings(repo))
    run = Path(plan["run_dir"])
    assert stat.S_IMODE(run.stat().st_mode) == 0o700
    assert stat.S_IMODE(paths.staging.stat().st_mode) == 0o700
    assert (run / "release" / "platform-manifest.yaml").is_file()
    assert (run / "content" / "cases" / "scenarios" / "s1.yml").is_file()
    assert not list(run.rglob(".git"))
    commit = git(repo, "rev-parse", "HEAD")
    assert plan["platform"]["name"] == "allprof"
    assert plan["release"]["commit"] == commit
    assert plan["release"]["tree"] == git(repo, "rev-parse", "HEAD:platform")
    assert plan["release"]["name"] == "allprof-v1.2.3"
    assert plan["release"]["target"] == str(paths.release_dir("allprof", "allprof-v1.2.3"))
    assert plan["content"]["cases"]["restricted"] is True
    assert plan["content"]["cases"]["target"] == str(
        paths.content_dir("allprof", "cases", restricted=True, name="cases-v1")
    )
    assert plan["content"]["guides"]["name"] == commit  # a branch: the commit names it
    assert plan["content"]["guides"]["restricted"] is False
    assert plan["jobs"] == [{"name": "trn", "image": manifest("allprof")["jobs"][0]["image"]}]
    assert "allprof-ctl" in plan["names"]["accounts_groups"]
    assert json.loads((run / deploy.PLAN).read_text())["run"] == run.name
    assert len(list(paths.git.glob("*.git"))) == 1, "one mirror per repository"


def test_inspect_changes_nothing_but_the_mirror(repo: Path, paths: deploy.Paths) -> None:
    plan = prepare(paths, settings(repo), inspect=True)
    assert plan["summary"]["name"] == "allprof"
    assert not list(paths.staging.glob("run-*"))
    assert not paths.srv.exists()
    assert not paths.active.exists()


def test_settings_problems_refuse_before_fetching(repo: Path, paths: deploy.Paths) -> None:
    s = settings(repo, platform={"repo": "http://git.example.com/x.git", "ref": "main"})
    assert refused(paths, s) == ["source-transport"]
    assert not paths.git.exists()


def test_overlapping_sources_are_refused(repo: Path, paths: deploy.Paths) -> None:
    url = f"file://{repo}"
    s = settings(repo, content={"cases": {"repo": url, "ref": "cases-v1", "path": ""}})
    assert refused(paths, s) == ["source-overlap"]


def test_the_manifest_is_validated(repo: Path, paths: deploy.Paths) -> None:
    data = manifest("allprof")
    data["services"][6]["secrets"] = ["key"]  # the agent has no secrets
    add_release(repo, data)
    git(repo, "add", "-A")
    git(repo, "commit", "--quiet", "-m", "bad")
    s = settings(repo, platform={"repo": f"file://{repo}", "ref": "main", "path": "platform"})
    assert refused(paths, s) == ["secrets-forbidden"]


def test_content_must_match_the_manifest(repo: Path, paths: deploy.Paths) -> None:
    url = f"file://{repo}"
    unknown = settings(repo)
    unknown_content = {**unknown.content, "extra": {"repo": url, "ref": "main", "path": "extra"}}
    (repo / "extra").mkdir()
    (repo / "extra" / "x").write_text("x")
    git(repo, "add", "-A")
    git(repo, "commit", "--quiet", "-m", "extra")
    assert refused(paths, settings(repo, content=unknown_content)) == ["content-unknown"]
    missing = {"guides": unknown.content["guides"]}
    assert refused(paths, settings(repo, content=missing)) == ["content-missing"]
    optional_left_out = {"cases": unknown.content["cases"]}
    assert prepare(paths, settings(repo, content=optional_left_out))["content"].keys() == {"cases"}


def test_unknown_refs_and_paths(repo: Path, paths: deploy.Paths) -> None:
    url = f"file://{repo}"
    s = settings(repo, platform={"repo": url, "ref": "nope", "path": "platform"})
    assert refused(paths, s) == ["source-ref"]
    s = settings(repo, platform={"repo": url, "ref": "main", "path": "missing"})
    assert refused(paths, s) == ["source-path"]
    (repo / "top.txt").write_text("a file, not a directory\n")
    git(repo, "add", "-A")
    git(repo, "commit", "--quiet", "-m", "file")
    s = settings(repo, platform={"repo": url, "ref": "main", "path": "top.txt"})
    assert refused(paths, s) == ["source-path"]


def test_symlinks_leaving_a_source_are_refused(repo: Path, paths: deploy.Paths) -> None:
    (repo / "catalog" / "escape").symlink_to("/etc/passwd")
    git(repo, "add", "-A")
    git(repo, "commit", "--quiet", "-m", "link")
    url = f"file://{repo}"
    s = settings(
        repo,
        content={"cases": {"repo": url, "ref": "main", "path": "catalog"}},
    )
    assert refused(paths, s) == ["source-unsafe-entry"]


def tree_with(repo: Path, base: str, name: str, entry: str) -> str:
    """A tree like ``base`` with one more entry (git refuses to commit a .git path)."""
    lines = git(repo, "ls-tree", base).splitlines()
    return git_input(repo, "\n".join([*lines, f"{entry}\t{name}"]) + "\n", "mktree")


def git_input(repo: Path, text: str, *args: str) -> str:
    import subprocess  # noqa: PLC0415 - only this helper feeds stdin

    return subprocess.run(
        ["git", *args], cwd=repo, input=text, capture_output=True, text=True, check=True
    ).stdout.strip()


def test_a_dot_git_entry_is_refused(repo: Path, paths: deploy.Paths) -> None:
    blob = git_input(repo, "gitdir: /elsewhere\n", "hash-object", "-w", "--stdin")
    catalog = tree_with(repo, "HEAD:catalog", ".git", f"100644 blob {blob}")
    root = git(repo, "rev-parse", "HEAD^{tree}")
    lines = [
        line if not line.endswith("\tcatalog") else f"040000 tree {catalog}\tcatalog"
        for line in git(repo, "ls-tree", root).splitlines()
    ]
    tree = git_input(repo, "\n".join(lines) + "\n", "mktree")
    commit = git(repo, "commit-tree", tree, "-p", "HEAD", "-m", "dot git")
    git(repo, "update-ref", "refs/heads/dotgit", commit)
    url = f"file://{repo}"
    s = settings(repo, content={"cases": {"repo": url, "ref": "dotgit", "path": "catalog"}})
    assert refused(paths, s) == ["source-git-entry"]


def test_required_signatures_need_a_release_tag(repo: Path, paths: deploy.Paths) -> None:
    url = f"file://{repo}"
    branch = settings(
        repo,
        platform={"repo": url, "ref": "main", "path": "platform", "tag_prefix": "allprof-v"},
        require_signed_tags=True,
    )
    assert "tag-signature" in refused(paths, branch)
    signers = paths.git.parent / "allowed_signers"
    signers.parent.mkdir(parents=True, exist_ok=True)
    signers.write_text("releases@example.com ssh-ed25519 AAAAC3Nza\n")
    unsigned = settings(repo, require_signed_tags=True, allowed_signers=str(signers))
    # The release and cases tags carry no signature; guides is deployed from a branch.
    assert refused(paths, unsigned) == ["tag-signature"] * 3


@pytest.mark.parametrize(
    ("active", "replace", "code"),
    [
        ("other", "", "platform-active"),
        ("other", "wrong", "platform-replace"),
        (None, "other", "platform-replace"),
        ("allprof", "allprof", "platform-replace"),
    ],
)
def test_one_platform_per_lab(
    repo: Path, paths: deploy.Paths, active: str | None, replace: str, code: str
) -> None:
    if active:
        paths.active.parent.mkdir(parents=True)
        paths.active.write_text(json.dumps({"name": active, "names": {}}))
    assert refused(paths, settings(repo, replace=replace)) == [code]


def test_replacing_with_the_flag_is_allowed(repo: Path, paths: deploy.Paths) -> None:
    paths.active.parent.mkdir(parents=True)
    paths.active.write_text(json.dumps({"name": "other", "names": {}}))
    assert prepare(paths, settings(repo, replace="other"))["replace"] == "other"


def test_a_moved_tag_is_refused(repo: Path, paths: deploy.Paths) -> None:
    deployed(paths, settings(repo))
    (repo / "platform" / "docs" / "new.md").write_text("new\n")
    git(repo, "add", "-A")
    git(repo, "commit", "--quiet", "-m", "two")
    git(repo, "tag", "-f", "-a", "allprof-v1.2.3", "-m", "moved")
    assert refused(paths, settings(repo)) == ["moved-tag"]


def test_the_host_check_exempts_the_platforms_own_names(repo: Path, paths: deploy.Paths) -> None:
    deployed(paths, settings(repo))
    facts = names.host_facts("allprof", paths.etc_platform)
    assert "allprof-ctl" in facts.owned
    assert "allprof-ctl-api" in facts.owned


# --- publish and activate ----------------------------------------------------------


def mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


def test_publish_places_release_and_content(repo: Path, paths: deploy.Paths) -> None:
    plan = prepare(paths, settings(repo))
    result = deploy.publish(plan, paths, now=lambda: NOW)
    assert result["changed"] is True
    release = paths.release_dir("allprof", "allprof-v1.2.3")
    assert mode(release) == 0o755
    assert mode(release / "platform-manifest.yaml") == 0o644
    assert mode(release / "bin" / "ctl") == 0o755
    restricted = paths.content_dir("allprof", "cases", restricted=True, name="cases-v1")
    assert mode(restricted) == 0o750
    assert mode(restricted / "scenarios" / "s1.yml") == 0o640
    assert mode(restricted / "run.sh") == 0o750
    assert mode(paths.srv / "restricted" / "allprof") == 0o750
    # Restricted content is only in the restricted area (§12.4).
    assert not list((paths.srv / "platforms").rglob("s1.yml"))
    guides = paths.content_dir(
        "allprof", "guides", restricted=False, name=plan["content"]["guides"]["name"]
    )
    assert mode(guides / "intro.md") == 0o644
    assert not list(paths.srv.rglob(".git"))
    for target, kind, package in ((release, "release", None), (restricted, "content", "cases")):
        record = json.loads((target / deploy.PROVENANCE).read_text())
        jsonschema.validate(record, schema("deployed-from"))
        assert record["kind"] == kind
        assert record["package"] == package
        assert record["platform"]["name"] == "allprof"
        assert record["platform"]["manifest_sha256"] == deploy.sha256_file(
            release / "platform-manifest.yaml"
        )
        assert record["deployed_at"] == "2026-01-01T12:00:00Z"
    assert deploy.publish(plan, paths, now=lambda: NOW)["changed"] is False


def test_activate_switches_and_records(repo: Path, paths: deploy.Paths) -> None:
    plan = prepare(paths, settings(repo))
    with pytest.raises(deploy.DeployError, match="not published"):
        deploy.activate(plan, paths)
    deploy.publish(plan, paths, now=lambda: NOW)
    result = deploy.activate(plan, paths, now=lambda: NOW)
    assert result["changed"] is True
    assert (paths.release_dir("allprof") / "current").readlink() == Path("allprof-v1.2.3")
    cases = paths.content_dir("allprof", "cases", restricted=True)
    assert (cases / "current").readlink() == Path("cases-v1")
    active = json.loads(paths.active.read_text())
    jsonschema.validate(active, schema("active-platform"))
    assert active["name"] == "allprof"
    assert active["activated_at"] == "2026-01-01T12:00:00Z"
    log = paths.records / "allprof" / deploy.DEPLOYMENTS
    assert len(log.read_text().splitlines()) == 1
    assert deploy.activate(plan, paths, now=lambda: NOW)["changed"] is False
    assert len(log.read_text().splitlines()) == 1


def test_a_new_release_switches_current_and_keeps_the_old_one(
    repo: Path, paths: deploy.Paths
) -> None:
    deployed(paths, settings(repo))
    data = manifest("allprof")
    data["version"] = "1.3.0"
    add_release(repo, data)
    git(repo, "add", "-A")
    git(repo, "commit", "--quiet", "-m", "next")
    git(repo, "tag", "-a", "allprof-v1.3.0", "-m", "allprof 1.3.0")
    s = settings(repo)
    s = deploy.Settings(**{**s.__dict__, "platform": {**s.platform, "ref": "allprof-v1.3.0"}})
    deployed(paths, s)
    assert (paths.release_dir("allprof") / "current").readlink() == Path("allprof-v1.3.0")
    assert paths.release_dir("allprof", "allprof-v1.2.3").is_dir()
    assert json.loads(paths.active.read_text())["version"] == "1.3.0"


def test_clean_only_removes_run_directories(repo: Path, paths: deploy.Paths) -> None:
    plan = prepare(paths, settings(repo))
    with pytest.raises(deploy.DeployError, match="not a run directory"):
        deploy.clean(paths.srv, paths)
    deploy.clean(Path(plan["run_dir"]), paths)
    assert not Path(plan["run_dir"]).exists()


# --- replace and purge ---------------------------------------------------------------


def other_platform(repo: Path) -> deploy.Settings:
    data = manifest("refplat")
    data["runtime"] = "static"
    for service in data["services"]:
        service["start"] = [f"bin/{service['name']}"]
    for hook in data["hooks"].values():
        hook["run"] = ["bin/admin", hook["run"][1]]
    data["cli"]["run"] = ["bin/refplat"]
    data.pop("content")
    add_release(repo, data, "refplat")
    git(repo, "add", "-A")
    git(repo, "commit", "--quiet", "-m", "refplat")
    return settings(
        repo,
        platform={"repo": f"file://{repo}", "ref": "main", "path": "refplat"},
        content={},
        replace="allprof",
    )


def test_replace_retires_the_old_platform(repo: Path, paths: deploy.Paths) -> None:
    deployed(paths, settings(repo))
    plan = retire = None
    plan = prepare(paths, other_platform(repo))
    retire = deploy.retire_plan(deploy.read_active(paths) or {}, CONTRACT, paths)
    assert retire["name"] == "allprof"
    assert "allprof-ctl.service" in retire["units"]
    assert "allprof-migrate" in retire["accounts"]
    assert "allprof-trn" in retire["accounts"]
    assert retire["incus_certificates"] == ["allprof-ctl", "allprof-sbx", "allprof-rop"]
    assert retire["cli_link"] == str(paths.bin / "allprof")
    assert "allprof_ctl" in retire["postgresql_roles"]
    deploy.publish(plan, paths, now=lambda: NOW)
    deploy.activate(plan, paths, now=lambda: NOW)
    assert json.loads(paths.active.read_text())["name"] == "refplat"
    retired = json.loads((paths.records / "allprof" / deploy.RETIRED).read_text())
    jsonschema.validate(retired, schema("retired-platform"))
    assert retired["replaced_by"] == "refplat"
    assert retired["active"]["name"] == "allprof"
    # The old platform's files stay until purge.
    assert paths.release_dir("allprof", "allprof-v1.2.3").is_dir()


class FakeRunner:
    def __init__(self, fail_on: str = "") -> None:
        self.calls: list[list[str]] = []
        self.fail_on = fail_on

    def __call__(self, argv: Sequence[str]) -> mirror.Done:
        self.calls.append(list(argv))
        if self.fail_on and any(self.fail_on in a for a in argv):
            return mirror.Done(1, "", "boom")
        return mirror.Done(0, "", "")


def test_purge(repo: Path, paths: deploy.Paths) -> None:
    deployed(paths, settings(repo))
    runner = FakeRunner()
    with pytest.raises(deploy.DeployError, match="active platform"):
        deploy.purge("allprof", "allprof", paths, runner)
    deployed(paths, other_platform(repo))
    with pytest.raises(deploy.DeployError, match="type the platform name"):
        deploy.purge("allprof", "allpro", paths, runner)
    with pytest.raises(deploy.DeployError, match="never replaced"):
        deploy.purge("nothere", "nothere", paths, runner)
    result = deploy.purge("allprof", "allprof", paths, runner, now=lambda: NOW)
    assert result["databases"] == ["allprof"]
    sql = [call[-1] for call in runner.calls]
    assert sql[0] == 'DROP DATABASE IF EXISTS "allprof" WITH (FORCE)'
    assert 'DROP ROLE IF EXISTS "allprof_ctl"' in sql
    assert all(
        call[:5] == ["docker", "exec", "--user", "postgres", "pinka-postgres"]
        for call in runner.calls
    )
    assert not (paths.srv / "platforms" / "allprof").exists()
    assert not (paths.srv / "restricted" / "allprof").exists()
    assert paths.release_dir("refplat").is_dir()
    retired = json.loads((paths.records / "allprof" / deploy.RETIRED).read_text())
    assert retired["purged_at"] == "2026-01-01T12:00:00Z"


def test_purge_removes_nothing_when_the_database_fails(repo: Path, paths: deploy.Paths) -> None:
    deployed(paths, settings(repo))
    deployed(paths, other_platform(repo))
    with pytest.raises(deploy.DeployError, match="psql failed"):
        deploy.purge("allprof", "allprof", paths, FakeRunner(fail_on="DROP DATABASE"))
    assert (paths.srv / "platforms" / "allprof").is_dir()


# --- build ---------------------------------------------------------------------------


def uv_release(repo: Path) -> deploy.Settings:
    if not (repo / "refplat").exists():
        add_release(repo, manifest("refplat"), "refplat")
        git(repo, "add", "-A")
        git(repo, "commit", "--quiet", "-m", "refplat")
    return settings(
        repo,
        platform={"repo": f"file://{repo}", "ref": "main", "path": "refplat"},
        content={"cases": {"repo": f"file://{repo}", "ref": "main", "path": "catalog"}},
    )


class FakeUv:
    """Stands in for systemd-run: creates the console scripts uv would create."""

    def __init__(self, plan: dict[str, Any], *, touch: str = "", rc: int = 0) -> None:
        self.release = Path(plan["run_dir"]) / "release"
        self.touch = touch
        self.rc = rc
        self.argv: list[str] = []

    def __call__(self, argv: Sequence[str]) -> mirror.Done:
        self.argv = list(argv)
        if self.rc:
            return mirror.Done(self.rc, "", "uv failed")
        data = manifest("refplat")
        commands = [s["start"][0] for s in data["services"]]
        commands += [h["run"][0] for h in data["hooks"].values()] + [data["cli"]["run"][0]]
        for relative in commands:
            path = self.release / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("#!/bin/sh\n")
            path.chmod(0o755)
        (self.release / ".venv" / "bin" / "python").symlink_to("/opt/pinka/python/bin/python3")
        if self.touch:
            (self.release / self.touch).write_text("changed by the build\n")
        return mirror.Done(0, "", "")


def property_values(argv: Sequence[str]) -> list[str]:
    return [argv[i + 1] for i, a in enumerate(argv) if a == "--property"]


def test_build_runs_uv_in_the_sandbox(repo: Path, paths: deploy.Paths) -> None:
    plan = prepare(paths, uv_release(repo))
    fake = FakeUv(plan)
    assert deploy.build(plan, paths, CONTRACT, fake) == {"changed": True, "built": True}
    props = property_values(fake.argv)
    final = plan["release"]["target"]
    assert f"User={ME}" in props
    assert f"BindPaths={plan['run_dir']}/release:{final}" in props
    assert f"ReadWritePaths={paths.uv_cache}" in props
    assert "InaccessiblePaths=-/var/lib/pinka/git" in props
    assert "InaccessiblePaths=-/srv/pinka/restricted" in props
    assert "ProtectSystem=strict" in props
    assert "NoNewPrivileges=yes" in props
    assert f"WorkingDirectory={final}" in props
    assert fake.argv[fake.argv.index("--") + 1 :] == [
        deploy.UV,
        "sync",
        "--frozen",
        "--all-packages",
        "--no-dev",
        "--no-editable",
        "--compile-bytecode",
        "--python",
        deploy.PYTHON,
    ]
    assert not Path(final).exists(), "the mount point is removed again"


def test_a_build_that_changes_the_release_is_refused(repo: Path, paths: deploy.Paths) -> None:
    plan = prepare(paths, uv_release(repo))
    with pytest.raises(deploy.Refused) as exc:
        deploy.build(plan, paths, CONTRACT, FakeUv(plan, touch="uv.lock"))
    assert codes(exc) == ["build-modified-release"]


def test_a_failed_build_fails(repo: Path, paths: deploy.Paths) -> None:
    plan = prepare(paths, uv_release(repo))
    with pytest.raises(deploy.DeployError, match="uv failed"):
        deploy.build(plan, paths, CONTRACT, FakeUv(plan, rc=1))


def test_a_build_without_the_commands_is_refused(repo: Path, paths: deploy.Paths) -> None:
    plan = prepare(paths, uv_release(repo))

    def nothing(argv: Sequence[str]) -> mirror.Done:
        return mirror.Done(0, "", "")

    with pytest.raises(deploy.Refused) as exc:
        deploy.build(plan, paths, CONTRACT, nothing)
    assert set(codes(exc)) == {"command-missing"}


def test_static_and_published_releases_are_not_built(repo: Path, paths: deploy.Paths) -> None:
    plan = prepare(paths, settings(repo))
    assert deploy.build(plan, paths, CONTRACT, FakeRunner())["built"] is False
    deploy.publish(plan, paths)
    uv = prepare(paths, uv_release(repo))
    fake = FakeUv(uv)
    deploy.build(uv, paths, CONTRACT, fake)
    deploy.publish(uv, paths)
    again = prepare(paths, uv_release(repo))
    assert again["release"]["published"] is True
    assert deploy.build(again, paths, CONTRACT, FakeRunner())["built"] is False


# --- pull --------------------------------------------------------------------------


class FakeDocker:
    def __init__(
        self, local: Sequence[str] = (), *, pull_rc: int = 0, digests: Sequence[str] = ()
    ) -> None:
        self.local = list(local)
        self.pull_rc = pull_rc
        self.digests = list(digests)
        self.pulled: list[str] = []

    def __call__(self, argv: Sequence[str]) -> mirror.Done:
        image = argv[-1]
        if argv[1] == "pull":
            if self.pull_rc:
                return mirror.Done(self.pull_rc, "", "denied")
            self.pulled.append(image)
            self.local.append(image)
            return mirror.Done(0, "", "")
        if image not in self.local:
            return mirror.Done(1, "", "No such image")
        name, digest = image.rsplit("@", 1)
        found = self.digests or [f"{name.rsplit(':', 1)[0]}@{digest}"]
        return mirror.Done(0, json.dumps(found), "")


def test_pull_by_digest(repo: Path, paths: deploy.Paths) -> None:
    plan = prepare(paths, settings(repo))
    image = plan["jobs"][0]["image"]
    docker = FakeDocker()
    assert deploy.pull(plan, docker) == {"changed": True, "pulled": [image]}
    assert deploy.pull(plan, docker) == {"changed": False, "pulled": []}
    with pytest.raises(deploy.DeployError, match="denied"):
        deploy.pull(plan, FakeDocker(pull_rc=1))
    with pytest.raises(deploy.DeployError, match="does not carry its digest"):
        deploy.pull(plan, FakeDocker(digests=["registry.example.com/x@sha256:" + "1" * 64]))


# --- CLI ---------------------------------------------------------------------------


def test_cli_deploy_steps(repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "host"
    settings_file = tmp_path / "settings.json"
    s = settings(repo)
    settings_file.write_text(
        json.dumps(
            {
                "platform": dict(s.platform),
                "content": {k: dict(v) for k, v in s.content.items()},
                "owners": s.owners.__dict__,
            }
        )
    )
    common = ["--root", str(root), "--no-host-check"]
    assert cli.main(["deploy", "prepare", "--settings", str(settings_file), *common]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["platform"]["name"] == "allprof"
    assert out["summary_text"].startswith("platform allprof 1.2.3-rc.1 (static)")
    run = out["run_dir"]
    for step in ("build", "publish", "activate", "clean"):
        assert cli.main(["deploy", step, "--run", run, *common]) == 0, step
        capsys.readouterr()
    assert cli.main(["deploy", "retire-plan", *common]) == 0
    assert json.loads(capsys.readouterr().out)["name"] == "allprof"
    assert cli.main(["purge", "allprof", "--confirm", "x", *common]) == cli.EXIT_FAILED
    assert "type the platform name" in json.loads(capsys.readouterr().out)["error"]


def test_cli_refusal_exits_65(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({"platform": {"repo": "http://x/y", "ref": "main"}}))
    argv = ["deploy", "prepare", "--settings", str(settings_file), "--root", str(tmp_path / "h")]
    assert cli.main(argv) == cli.EXIT_REFUSED
    out = json.loads(capsys.readouterr().out)
    assert out["refused"] is True
    assert out["problems"][0]["code"] == "source-transport"
