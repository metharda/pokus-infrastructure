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
    # To spend fewer GitHub Actions minutes.
    for name in ("check-mode-extended.yml", "release-package.yml"):
        on = workflow(name)[True]  # YAML 1.1 reads the key "on" as True
        # A fork's pull request runs them as a push would (test_triggers.py).
        assert set(on) == {"push", "pull_request", "schedule", "workflow_dispatch"}, name
        assert on["push"]["paths"], name
        assert f".github/workflows/{name}" in on["push"]["paths"], name
    # The isolated files run on every push (a change of the code alone can
    # break a test's isolation), weekly and on demand.
    on = workflow("isolated.yml")[True]
    assert set(on) == {"push", "pull_request", "schedule", "workflow_dispatch"}
    assert on["push"] == {"branches": ["**"]}
    assert on["pull_request"] is None
    ci = workflow("ci.yml")
    assert not {"test-random-order", "test-isolated", "check-mode-extended"} & set(ci["jobs"])
    # One concurrency rule for every workflow that tests a commit: a newer run
    # cancels the older, except on main and moving/on (test_triggers.py).
    for name in ("ci.yml", "isolated.yml", "check-mode-extended.yml", "release-package.yml"):
        assert workflow(name)["concurrency"] == ci["concurrency"], name
        assert workflow(name)["concurrency"]["cancel-in-progress"] is True, name


def test_a_change_of_docs_only_skips_the_ansible_lanes() -> None:
    jobs = workflow("ci.yml")["jobs"]
    assert jobs["check-mode"]["needs"] == "changes"
    assert "needs.changes.outputs.ansible == 'true'" in jobs["check-mode"]["if"]
    required = jobs["required"]
    assert set(required["needs"]) == {"changes", "privacy", "lint", "test", "docs", "check-mode"}
    assert "always()" in required["if"]
    # The extended lanes skip such a push by their paths: Markdown is never an
    # input, a role's README included. The isolated run does not.
    assert workflow("check-mode-extended.yml")[True]["push"]["paths"][-1] == "!**.md"
    assert "paths" not in workflow("isolated.yml")[True]["push"]


def test_jobs_run_files_at_a_time_and_report_them_in_collection_order(tmp_path: Path) -> None:
    # Each file waits until every file has started: it passes only when they run at once.
    started = tmp_path / "started"
    started.mkdir()
    wait = (
        "import time, pathlib\n\ndef test_{n}():\n"
        f"    d = pathlib.Path({str(started)!r})\n"
        "    (d / '{n}').touch()\n"
        "    for _ in range(300):\n"
        "        if len(list(d.iterdir())) == 3:\n"
        "            return\n"
        "        time.sleep(0.1)\n"
        "    raise AssertionError('not at once')\n"
    )
    cwd = project(tmp_path, {f"test_{n}.py": wait.format(n=n) for n in "abc"})
    logs = tmp_path / "logs"
    done = run(cwd, "-m", "pinka_repo_checks.isolated", "--log-dir", str(logs), "--jobs", "3")
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout.startswith(
        "test-isolated: 3 passed, 0 failed, 0 skipped, 0 warnings "
        "(3 files, each in a process of its own, 3 at a time, in "
    ), done.stdout
    assert sorted(p.name for p in logs.iterdir()) == [
        "test_a.py.log",
        "test_b.py.log",
        "test_c.py.log",
    ]
    # A failing file is reported in full, in collection order, whatever finished first.
    (cwd / "test_a.py").write_text(
        "import time\n\ndef test_a():\n    time.sleep(1)\n    assert False\n"
    )
    (cwd / "test_b.py").write_text("def test_b():\n    assert False\n")
    (cwd / "test_c.py").write_text("def test_c():\n    pass\n")
    done = run(cwd, "-m", "pinka_repo_checks.isolated", "--log-dir", str(logs), "--jobs", "3")
    assert done.returncode == 1
    first, second = done.stdout.index("----- test_a.py: "), done.stdout.index("----- test_b.py: ")
    assert first < second
    assert "failing: test_a.py, test_b.py; " in done.stdout
