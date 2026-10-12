"""A small uv workspace shaped like a platform release, for the agent environment tests (V6).

Every package builds with an in-tree PEP 517 backend and the third-party
dependencies are local wheels, so uv locks and builds it without network.
The members carry the roles of the packages of a platform like the reference platform's: the agent,
the shared core, and the packages whose code the agent must never import
(the injector, its fault primitives, the verifier and the evaluator).
"""

from __future__ import annotations

import base64
import hashlib
import os
import shutil
import subprocess
import textwrap
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
import yaml

# The backend each member builds with (no requirements, standard library only).
BACKEND = textwrap.dedent(
    '''
    """A minimal PEP 517 backend: a wheel of the module named like the project."""
    import base64, hashlib, os, tomllib, zipfile

    def _project():
        with open("pyproject.toml", "rb") as f:
            return tomllib.load(f)["project"]

    def get_requires_for_build_wheel(config_settings=None):
        return []

    def _record(name, data):
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        return f"{name},sha256={digest},{len(data)}"

    def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
        project = _project()
        name, version = project["name"], project["version"]
        module = name.replace("-", "_")
        files = {}
        for directory, _, names in os.walk(module):
            for n in names:
                if n.endswith(".py"):
                    path = os.path.join(directory, n)
                    with open(path, "rb") as f:
                        files[path] = f.read()
        info = f"{module}-{version}.dist-info"
        meta = ["Metadata-Version: 2.1", f"Name: {name}", f"Version: {version}"]
        meta += [f"Requires-Dist: {d}" for d in project.get("dependencies", [])]
        files[f"{info}/METADATA"] = ("\\n".join(meta) + "\\n").encode()
        files[f"{info}/WHEEL"] = (
            b"Wheel-Version: 1.0\\nGenerator: test\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n"
        )
        scripts = project.get("scripts", {})
        if scripts:
            lines = "".join(f"{k} = {v}\\n" for k, v in scripts.items())
            files[f"{info}/entry_points.txt"] = ("[console_scripts]\\n" + lines).encode()
        record = [_record(k, v) for k, v in files.items()] + [f"{info}/RECORD,,"]
        files[f"{info}/RECORD"] = ("\\n".join(record) + "\\n").encode()
        wheel = f"{module}-{version}-py3-none-any.whl"
        with zipfile.ZipFile(os.path.join(wheel_directory, wheel), "w") as z:
            for k, v in files.items():
                z.writestr(k, v)
        return wheel
    '''
)
AGENT = "demo-agent"
AGENT_DIR = "packages/demo-agent"
# The modules the agent must never import, and the one it may share with them.
FORBIDDEN = ("demo_injector", "demo_primitives", "demo_verifier", "demo_evaluator")
SHARED = "demo_core"
# Local wheels: the agent's dependency, another package's, the build backend group.
AGENT_DEPENDENCY = "sockio"
OTHER_DEPENDENCY = "faultlib"
BUILD_TOOL = "buildtool"


def _wheel(directory: Path, name: str, version: str = "1.0") -> None:
    module = name.replace("-", "_")
    info = f"{module}-{version}.dist-info"
    files = {
        f"{module}/__init__.py": f"NAME = {name!r}\n".encode(),
        f"{info}/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n".encode(),
        f"{info}/WHEEL": b"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\n"
        b"Tag: py3-none-any\n",
    }
    record = []
    for key, data in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        record.append(f"{key},sha256={digest},{len(data)}")
    record.append(f"{info}/RECORD,,")
    files[f"{info}/RECORD"] = ("\n".join(record) + "\n").encode()
    with zipfile.ZipFile(directory / f"{module}-{version}-py3-none-any.whl", "w") as archive:
        for key, data in files.items():
            archive.writestr(key, data)


def _member(
    root: Path,
    name: str,
    deps: Sequence[str] = (),
    *,
    scripts: Mapping[str, str] | None = None,
    members: Sequence[str] = (),
) -> None:
    directory = root / "packages" / name
    module = name.replace("-", "_")
    (directory / module).mkdir(parents=True)
    (directory / module / "__init__.py").write_text(
        f"NAME = {name!r}\n\n\ndef main() -> None:\n    print(NAME)\n", encoding="utf-8"
    )
    (directory / "_backend").mkdir()
    (directory / "_backend" / "test_backend.py").write_text(BACKEND, encoding="utf-8")
    lines = [
        "[project]",
        f'name = "{name}"',
        'version = "0.1.0"',
        'requires-python = ">=3.12"',
        "dependencies = [" + ", ".join(f'"{d}"' for d in [*deps, *members]) + "]",
    ]
    if scripts:
        lines += ["", "[project.scripts]", *(f'{k} = "{v}"' for k, v in scripts.items())]
    lines += [
        "",
        "[build-system]",
        "requires = []",
        'build-backend = "test_backend"',
        'backend-path = ["_backend"]',
    ]
    if members:
        lines += ["", "[tool.uv.sources]", *(f"{m} = {{ workspace = true }}" for m in members)]
    (directory / "pyproject.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def make_workspace(root: Path, *, agent_members: Sequence[str] = ()) -> Path:
    """Write the workspace (without its lock).

    Args:
        root: The release directory to create.
        agent_members: Workspace members the agent depends on (refused by V6).

    Returns:
        The release directory.
    """
    root.mkdir(parents=True)
    (root / "wheels").mkdir()
    for name in (AGENT_DEPENDENCY, OTHER_DEPENDENCY, BUILD_TOOL):
        _wheel(root / "wheels", name)
    (root / "pyproject.toml").write_text(
        textwrap.dedent(
            f"""\
            [project]
            name = "demo"
            version = "0.1.0"
            requires-python = ">=3.12"
            dependencies = []

            [dependency-groups]
            build = ["{BUILD_TOOL}"]

            [tool.uv]
            find-links = ["wheels"]
            no-index = true

            [tool.uv.workspace]
            members = ["packages/*"]
            """
        ),
        encoding="utf-8",
    )
    _member(root, "demo-core")
    _member(root, "demo-primitives", [OTHER_DEPENDENCY])
    _member(root, "demo-injector", members=["demo-primitives", "demo-core"])
    _member(root, "demo-verifier", members=["demo-core"])
    _member(root, "demo-evaluator", members=["demo-core"])
    _member(
        root,
        AGENT,
        [AGENT_DEPENDENCY],
        scripts={"demo-agent-runner": "demo_agent:main"},
        members=agent_members,
    )
    return root


# versions.lock.yml pins the uv the build runs; the tests use exactly that one.
VERSIONS_LOCK = Path(__file__).resolve().parents[3] / "versions.lock.yml"


def pinned_uv() -> str:
    """The uv version of ``versions.lock.yml``."""
    data = yaml.safe_load(VERSIONS_LOCK.read_text(encoding="utf-8"))
    return str(data["binaries"]["uv"]["version"])


def uv_binary() -> str:
    """The pinned uv (``PINKA_UV`` or the one on the PATH); in CI (``CI=true``) it must be there.

    The build's commands depend on uv's behavior (a single selected package
    inherits the workspace root's dependency groups), so only the uv that
    ``versions.lock.yml`` pins counts.

    Returns:
        Its path.
    """
    found = os.environ.get("PINKA_UV") or shutil.which("uv")
    version = ""
    if found:
        done = subprocess.run([found, "--version"], capture_output=True, text=True, check=False)
        version = done.stdout.split()[1] if done.returncode == 0 and done.stdout else ""
    if version != pinned_uv():
        reason = f"needs uv {pinned_uv()} (found {version or 'none'})"
        if os.environ.get("CI") == "true":
            pytest.fail(f"{reason}; CI installs the pinned uv")
        pytest.skip(f"{reason}; allowed to skip only outside CI (set PINKA_UV)")
    assert found is not None
    return found


def uv_environment(cache: Path, extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment for uv: a private cache, no Python downloads, no network.

    Args:
        cache: The uv cache directory.
        extra: More variables.

    Returns:
        The environment.
    """
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("UV_") and k not in ("VIRTUAL_ENV", "PYTHONPATH")
    }
    env.update({"UV_CACHE_DIR": str(cache), "UV_PYTHON_DOWNLOADS": "never", "UV_OFFLINE": "1"})
    env.update(extra or {})
    return env


def lock(release: Path, uv: str, python: str, cache: Path) -> None:
    """Lock the workspace (its own ``[tool.uv]`` settings find the local wheels).

    Args:
        release: The workspace.
        uv: uv.
        python: The interpreter.
        cache: The uv cache.
    """
    subprocess.run(
        [uv, "lock", "--offline", "--python", python],
        cwd=release,
        env=uv_environment(cache),
        check=True,
        capture_output=True,
        text=True,
    )
