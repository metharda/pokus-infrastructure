"""The agent's own package and environment (V6, owner decision with the "commit" for 3d.4).

The lock tests read hand-written ``uv.lock`` files. The environment tests
build a small workspace with uv, running exactly the uv commands of the
build's sandbox (without ``systemd-run``), and prove what the agent's
environment holds and what it cannot import.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from agent_testlib import (
    AGENT,
    AGENT_DEPENDENCY,
    AGENT_DIR,
    BUILD_TOOL,
    FORBIDDEN,
    SHARED,
    lock,
    make_workspace,
    uv_environment,
)

from pinka_platform import agentpkg, deploy

INDEX = 'registry = "https://pypi.org/simple"'
WHERE = "services[1].package"


def write_lock(release: Path, body: str) -> None:
    release.mkdir(parents=True, exist_ok=True)
    (release / "uv.lock").write_text("version = 1\nrevision = 3\n" + body, encoding="utf-8")


def agent_package(release: Path, name: str = "agent", directory: str = "packages/agent") -> None:
    (release / directory).mkdir(parents=True, exist_ok=True)
    (release / directory / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "0.1.0"\n', encoding="utf-8"
    )


def package(name: str, source: str, deps: str = "", extra: str = "") -> str:
    return (
        f'\n[[package]]\nname = "{name}"\nversion = "1.0"\nsource = {{ {source} }}\n'
        f"dependencies = [{deps}]\n{extra}"
    )


def depends(release: Path) -> list[str]:
    return [p.message for p in agentpkg.dependency_problems(release, "packages/agent", WHERE)]


# --- the lock -------------------------------------------------------------------------


def test_an_agent_with_index_dependencies_passes(tmp_path: Path) -> None:
    agent_package(tmp_path)
    write_lock(
        tmp_path,
        package("agent", 'editable = "packages/agent"', '{ name = "httpx" }')
        + package("httpx", INDEX, '{ name = "idna" }')
        + package("idna", INDEX)
        + package("core", 'editable = "packages/core"'),
    )
    assert depends(tmp_path) == []
    packages = agentpkg.read_lock(tmp_path)
    member = agentpkg.member_for(packages, "packages/agent/")
    assert member is not None
    assert [p.name for p, _ in agentpkg.closure(packages, member)] == ["agent", "httpx", "idna"]


@pytest.mark.parametrize(
    ("source", "reason"),
    [
        ('editable = "packages/core"', "another package of the same release"),
        ('directory = "packages/core"', "another package of the same release"),
        ('virtual = "."', "another package of the same release"),
        ('path = "dist/core-1.0-py3-none-any.whl"', "a source other than a package index"),
        ('git = "https://example.com/platform.git?rev=main#0"', "a source other than"),
        ('url = "https://example.com/core-1.0.tar.gz"', "a source other than a package index"),
        ('registry = "wheels"', "a source other than a package index"),
        ('registry = "http://example.com/simple"', "a source other than a package index"),
    ],
)
def test_a_dependency_from_the_release_or_another_source_is_refused(
    tmp_path: Path, source: str, reason: str
) -> None:
    agent_package(tmp_path)
    write_lock(
        tmp_path,
        package("agent", 'editable = "packages/agent"', '{ name = "core" }')
        + package("core", source),
    )
    [message] = depends(tmp_path)
    assert message.startswith("agent -> core: core (")
    assert reason in message


def test_a_transitive_dependency_on_the_release_is_refused(tmp_path: Path) -> None:
    # The agent -> an index package -> the release's injector (owner: "directly
    # or transitively").
    agent_package(tmp_path)
    write_lock(
        tmp_path,
        package("agent", 'editable = "packages/agent"', '{ name = "helper" }')
        + package("helper", INDEX, '{ name = "injector" }')
        + package("injector", 'editable = "packages/injector"', '{ name = "primitives" }')
        + package("primitives", 'editable = "packages/primitives"'),
    )
    messages = depends(tmp_path)
    assert [m.split(":")[0] for m in messages] == [
        "agent -> helper -> injector",
        "agent -> helper -> injector -> primitives",
    ]


def test_markers_do_not_hide_a_dependency_and_extras_count(tmp_path: Path) -> None:
    agent_package(tmp_path)
    write_lock(
        tmp_path,
        package(
            "agent",
            'editable = "packages/agent"',
            '{ name = "core", marker = "sys_platform == \'win32\'" }, '
            '{ name = "lib", extra = ["fast"] }',
        )
        + package(
            "lib",
            INDEX,
            extra='[package.optional-dependencies]\nfast = [{ name = "evaluator" }]\n',
        )
        + package("core", 'editable = "packages/core"')
        + package("evaluator", 'editable = "packages/evaluator"'),
    )
    assert sorted(m.split(":")[0] for m in depends(tmp_path)) == [
        "agent -> core",
        "agent -> lib -> evaluator",
    ]


def test_an_unrequested_extra_does_not_count(tmp_path: Path) -> None:
    agent_package(tmp_path)
    write_lock(
        tmp_path,
        package("agent", 'editable = "packages/agent"', '{ name = "lib" }')
        + package(
            "lib",
            INDEX,
            extra='[package.optional-dependencies]\nfast = [{ name = "evaluator" }]\n',
        )
        + package("evaluator", 'editable = "packages/evaluator"'),
    )
    assert depends(tmp_path) == []


def test_two_locked_versions_are_told_apart(tmp_path: Path) -> None:
    agent_package(tmp_path)
    write_lock(
        tmp_path,
        package("agent", 'editable = "packages/agent"', '{ name = "lib", version = "1.0" }')
        + package("lib", INDEX)
        + '\n[[package]]\nname = "lib"\nversion = "2.0"\nsource = { editable = "packages/lib" }\n',
    )
    assert depends(tmp_path) == []


def test_the_package_must_be_a_locked_workspace_member(tmp_path: Path) -> None:
    agent_package(tmp_path)
    write_lock(tmp_path, package("other", 'editable = "packages/other"'))
    [problem] = agentpkg.dependency_problems(tmp_path, "packages/agent", WHERE)
    assert problem.code == "agent-package-not-member"
    # Locked at the directory, but under another name than its pyproject says.
    write_lock(tmp_path, package("renamed", 'editable = "packages/agent"'))
    assert [p.code for p in agentpkg.dependency_problems(tmp_path, "packages/agent", WHERE)] == [
        "agent-package-not-member"
    ]
    (tmp_path / "uv.lock").write_text("not toml [", encoding="utf-8")
    assert [p.code for p in agentpkg.dependency_problems(tmp_path, "packages/agent", WHERE)] == [
        "agent-package-lock"
    ]


def test_installed_names_and_the_environment_check(tmp_path: Path) -> None:
    agent_package(tmp_path)
    write_lock(
        tmp_path,
        package("agent", 'editable = "packages/agent"', '{ name = "Zope.Interface" }')
        + package("zope-interface", INDEX),
    )
    env = tmp_path / ".agent-venvs" / "agent"
    assert [p.code for p in agentpkg.env_problems(tmp_path, "agent", "packages/agent", WHERE)] == [
        "agent-env-missing"
    ]
    site = env / "lib" / "python3.12" / "site-packages"
    site.mkdir(parents=True)
    (env / "pyvenv.cfg").write_text("home = /opt/pinka/python/bin\n", encoding="utf-8")
    for info in ("agent-0.1.0.dist-info", "zope_interface-6.0.dist-info"):
        (site / info).mkdir()
    assert agentpkg.installed(env) == ["agent", "zope-interface"]
    assert agentpkg.env_problems(tmp_path, "agent", "packages/agent", WHERE) == []
    (site / "hatchling-1.27.0.dist-info").mkdir()
    [problem] = agentpkg.env_problems(tmp_path, "agent", "packages/agent", WHERE)
    assert (problem.code, problem.where) == ("agent-env-extra", ".agent-venvs/agent")
    assert problem.message.startswith("hatchling is installed")


def test_start_paths() -> None:
    agent = {"name": "agent", "package": "packages/a/", "start": [".venv/bin/run", "-v"]}
    assert agentpkg.start_path(agent, "python-uv") == ".agent-venvs/agent/bin/run"
    assert agentpkg.start_path(agent, "static") == ".venv/bin/run"
    other = {"name": "ctl", "start": [".venv/bin/ctl"]}
    assert agentpkg.start_path(other, "python-uv") == ".venv/bin/ctl"
    assert agentpkg.package_dir(agent) == "packages/a"


# --- the environment, built with uv ----------------------------------------------------


def build_with_uv(
    uv: str, release: Path, cache: Path
) -> dict[str, subprocess.CompletedProcess[str]]:
    """Run the build's uv commands (from :func:`deploy.sandbox_commands`) in the release.

    Args:
        uv: The pinned uv.
        release: The workspace.
        cache: The uv cache.

    Returns:
        The finished phases by name.
    """
    plan = {"run": "run-test", "run_dir": str(release.parent), "release": {"target": str(release)}}
    commands = deploy.sandbox_commands(
        plan,
        deploy.Paths(uv_cache=cache),
        deploy.Owners(),
        build_group=True,
        agents=[("agent", AGENT)],
    )
    done = {}
    for phase, argv in commands:
        properties = [argv[i + 1] for i, a in enumerate(argv) if a == "--property"]
        env = dict(
            p.removeprefix("Environment=").split("=", 1)
            for p in properties
            if p.startswith("Environment=")
        )
        uv_argv = argv[argv.index("--") + 1 :]
        assert uv_argv[0] == deploy.UV and uv_argv[-2:] == ["--python", deploy.PYTHON]
        run = [uv, *uv_argv[1:-1], sys.executable]
        env["UV_CACHE_DIR"] = str(cache)
        env.pop("HOME", None)
        done[phase] = subprocess.run(
            run,
            cwd=release,
            env=uv_environment(cache, env),
            capture_output=True,
            text=True,
            check=False,
        )
        assert done[phase].returncode == 0, (phase, done[phase].stderr)
    return done


def python_of(env: Path, code: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(env / "bin" / "python"), "-I", "-c", code],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture(scope="module")
def built(uv: str, tmp_path_factory: pytest.TempPathFactory) -> Path:
    base = tmp_path_factory.mktemp("agent-env")
    release = make_workspace(base / "release")
    cache = base / "cache"
    lock(release, uv, sys.executable, cache)
    build_with_uv(uv, release, cache)
    return release


def test_the_agent_environment_holds_only_its_package_and_dependencies(built: Path) -> None:
    env = built / agentpkg.env_dir("agent")
    assert agentpkg.installed(env) == sorted([AGENT, AGENT_DEPENDENCY])
    # The build backend of the "build" group was synced away again.
    assert BUILD_TOOL not in agentpkg.installed(env)
    assert agentpkg.env_problems(built, "agent", AGENT_DIR, "services[1].package") == []
    # The shared environment has everything, the build backend included.
    shared = agentpkg.installed(built / ".venv")
    assert {"demo-injector", "demo-primitives", "demo-verifier", "demo-evaluator"} <= set(shared)
    # No path file points anywhere into the release.
    for pth in env.glob("lib/python*/site-packages/*.pth"):
        assert str(built) not in pth.read_text(encoding="utf-8"), pth


def test_the_forbidden_modules_cannot_be_imported_by_the_agent(built: Path, tmp_path: Path) -> None:
    env = built / agentpkg.env_dir("agent")
    for module in (*FORBIDDEN, SHARED):
        done = python_of(env, f"import {module}", tmp_path)
        assert done.returncode != 0, module
        assert f"No module named '{module}'" in done.stderr, done.stderr
        # They are there in the shared environment: the test is not vacuous.
        assert python_of(built / ".venv", f"import {module}", tmp_path).returncode == 0
    assert python_of(env, "import demo_agent, sockio", tmp_path).returncode == 0
    script = env / "bin" / "demo-agent-runner"
    runner = subprocess.run([str(script)], capture_output=True, text=True, check=True)
    assert runner.stdout.strip() == AGENT
    assert script.read_text(encoding="utf-8").startswith(f"#!{env}/bin/python")


def test_uv_refuses_nothing_that_validation_must_refuse(uv: str, tmp_path: Path) -> None:
    # An agent package depending on the release's core: uv locks it happily,
    # so the refusal must come from the validation (owner decision on V6).
    release = make_workspace(tmp_path / "release", agent_members=["demo-core"])
    lock(release, uv, sys.executable, tmp_path / "cache")
    messages = [p.message for p in agentpkg.dependency_problems(release, AGENT_DIR, "x")]
    assert any(m.startswith("demo-agent -> demo-core: ") for m in messages), messages
    assert any("another package of the same release" in m for m in messages)
    # The local wheels of the test's own index are refused too: only an
    # https:// package index counts.
    assert any(m.startswith("demo-agent -> sockio: ") for m in messages), messages


# --- the CI rule for these tests (owner decision with the "commit" for 3d.4b) -----------

CI_PROBE = """
import pytest

@pytest.fixture(scope="session")
def uv():
    pytest.skip("no uv here")

@pytest.fixture
def built(uv):
    return uv

def test_direct(uv):
    pass

def test_through_a_fixture(built):
    pass

def test_without_uv():
    pytest.skip("unrelated")
"""


@pytest.mark.parametrize(("ci", "failed"), [("true", 2), ("", 0)])
def test_a_skipped_uv_test_fails_in_ci(tmp_path: Path, ci: str, failed: int) -> None:
    # The conftest's hooks with a uv fixture that always skips.
    conftest = (Path(__file__).parent / "conftest.py").read_text(encoding="utf-8")
    hooks = conftest[conftest.index("def pytest_collection_modifyitems") :]
    imports = "import os\nfrom collections.abc import Generator\nimport pytest\n"
    (tmp_path / "conftest.py").write_text(imports + hooks, encoding="utf-8")
    (tmp_path / "test_probe.py").write_text(CI_PROBE, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in ("CI", "PYTEST_ADDOPTS")}
    env["CI"] = ci
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "--rootdir",
            str(tmp_path),
            "-c",
            os.devnull,
            "-o",
            "markers=uv: test",
            "-rA",
            str(tmp_path),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    summary = done.stdout.strip().splitlines()[-1]
    if failed:
        assert done.returncode != 0, done.stdout
        assert "2 errors" in summary and "1 skipped" in summary, done.stdout
        assert done.stdout.count("a test that needs uv was skipped in CI: ") == 2, done.stdout
    else:
        assert done.returncode == 0, done.stdout
        assert "3 skipped" in summary, done.stdout
