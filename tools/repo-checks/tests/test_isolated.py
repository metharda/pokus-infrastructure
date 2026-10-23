"""Every pytest file in a process of its own (``make test-isolated``)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[3]
REPO_CHECKS = ROOT / "tools" / "repo-checks"


def project(tmp_path: Path, files: dict[str, str]) -> Path:
    (tmp_path / "pytest.ini").write_text(
        "[pytest]\naddopts = -p pinka_repo_checks.skip_policy\n", encoding="utf-8"
    )
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    return tmp_path


def run(cwd: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in ("CI", "PYTEST_ADDOPTS")}
    env["PYTHONPATH"] = str(REPO_CHECKS)
    return subprocess.run(
        [sys.executable, *argv], cwd=cwd, env=env, capture_output=True, text=True, check=False
    )


def test_a_test_that_passes_only_after_another_file_fails_alone(tmp_path: Path) -> None:
    cwd = project(
        tmp_path,
        {
            "shared.py": "seen = []\n",
            "test_a.py": "import shared\n\ndef test_a():\n    shared.seen.append('a')\n",
            "test_b.py": (
                "import pytest\nimport shared\n\n"
                "def test_b():\n    assert shared.seen == ['a']\n\n"
                "def test_skipped():\n    pytest.skip('a reason')\n"
            ),
        },
    )
    # One process: test_b passes after test_a.
    assert run(cwd, "-m", "pytest", "-p", "no:cacheprovider").returncode == 0
    done = run(cwd, "-m", "pinka_repo_checks.isolated", "--log-dir", "logs")
    assert done.returncode == 1, done.stdout
    assert (
        "----- test_b.py: exit status 1; complete output (logs/test_b.py.log) -----" in done.stdout
    )
    assert "assert [] == ['a']" in done.stdout
    assert "test_a.py: passed" not in done.stdout  # quiet: only failing files
    assert (
        "test-isolated: FAILED: 1 passed, 1 failed, 1 skipped, 0 warnings "
        "(2 files, each in a process of its own, in "
    ) in done.stdout
    assert "failing: test_b.py; logs: logs)" in done.stdout
    assert done.stdout.endswith("\n  skipped test: test_b.py::test_skipped (reason: a reason)\n")
    assert " 1 passed in " in (cwd / "logs" / "test_a.py.log").read_text(encoding="utf-8")
    verbose = run(cwd, "-m", "pinka_repo_checks.isolated", "--log-dir", "logs", "--verbose")
    assert "----- test_a.py: passed; complete output (logs/test_a.py.log) -----" in verbose.stdout


def test_a_file_that_runs_other_tests_alone_fails(tmp_path: Path) -> None:
    cwd = project(
        tmp_path,
        {
            "shared.py": "cases = [1]\n",
            "test_a.py": "import shared\n\nshared.cases += [2, 3]\n\ndef test_a():\n    pass\n",
            "test_b.py": (
                "import pytest\nimport shared\n\n"
                "@pytest.mark.parametrize('n', list(shared.cases))\n"
                "def test_b(n):\n    pass\n"
            ),
        },
    )
    done = run(cwd, "-m", "pinka_repo_checks.isolated", "--log-dir", "logs")
    assert done.returncode == 1, done.stdout
    assert (
        "test_b.py: ran 1 test(s) alone, 3 in the whole collection; complete output" in done.stdout
    )
    assert "test-isolated: FAILED: 2 passed, 1 failed, 0 skipped" in done.stdout


def test_a_passing_run_prints_one_line(tmp_path: Path) -> None:
    cwd = project(tmp_path, {"test_a.py": "def test_a():\n    pass\n"})
    done = run(cwd, "-m", "pinka_repo_checks.isolated", "--log-dir", "logs")
    assert done.returncode == 0, done.stdout
    assert done.stdout.startswith("test-isolated: 1 passed, 0 failed, 0 skipped, 0 warnings ")
    assert len(done.stdout.splitlines()) == 1
    failed = run(cwd, "-m", "pinka_repo_checks.isolated", "--log-dir", "logs", "missing")
    assert failed.returncode == 1
    assert "----- collection failed; complete output -----" in failed.stdout


def workflow(name: str) -> dict[Any, Any]:
    path = ROOT / ".github" / "workflows" / name
    data: dict[Any, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data


def test_ci_runs_it_with_the_test_jobs_setup() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "$(BIN)/python -m pinka_repo_checks.isolated --log-dir $(BUILD)/isolated" in makefile
    test = workflow("ci.yml")["jobs"]["test"]["steps"]
    steps = workflow("isolated.yml")["jobs"]["test-isolated"]["steps"]
    # The same tools, PostgreSQL 18 and nftables: nothing skips there either;
    # the last step keeps the JUnit XML (test_junit.py).
    assert steps[:-2] == test[:-2]
    assert steps[-2] == {"run": "make test-isolated", "env": test[-2]["env"]}
    assert steps[-1] == test[-1]
    # The test job runs the suites in the random order of the Makefile's seed.
    assert test[-2]["run"] == "make test VERBOSE=1 TEST_ORDER=random"


def test_the_heavy_lanes_run_weekly_on_demand_and_when_their_inputs_change() -> None:
    # Owner, with the new plan after 16bf749 (GitHub Actions minutes).
    for name in ("isolated.yml", "check-mode-extended.yml", "release-package.yml"):
        on = workflow(name)[True]  # YAML 1.1 reads the key "on" as True
        assert set(on) == {"push", "schedule", "workflow_dispatch"}, name
        assert on["push"]["paths"], name
        assert f".github/workflows/{name}" in on["push"]["paths"], name
    ci = workflow("ci.yml")
    assert not {"test-random-order", "test-isolated", "check-mode-extended"} & set(ci["jobs"])
    for name in ("ci.yml", "isolated.yml", "check-mode-extended.yml", "release-package.yml"):
        assert workflow(name)["concurrency"] == {
            "group": "${{ github.workflow }}-${{ github.ref }}",
            "cancel-in-progress": True,
        }, name


def test_a_change_of_docs_only_skips_the_ansible_lanes() -> None:
    jobs = workflow("ci.yml")["jobs"]
    assert jobs["check-mode"]["needs"] == "changes"
    assert "needs.changes.outputs.ansible == 'true'" in jobs["check-mode"]["if"]
    required = jobs["required"]
    assert set(required["needs"]) == {"changes", "privacy", "lint", "test", "docs", "check-mode"}
    assert "always()" in required["if"]
    # The extended lanes and the isolated run skip such a push by their paths:
    # Markdown is never an input, a role's README included.
    for name in ("isolated.yml", "check-mode-extended.yml"):
        assert workflow(name)[True]["push"]["paths"][-1] == "!**.md", name
