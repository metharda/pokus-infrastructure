"""The CI skip list: every skip is named, and in CI only the listed ones may skip."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from pinka_repo_checks import ci_skips, unittest_run

ROOT = Path(__file__).resolve().parents[3]
REPO_CHECKS = ROOT / "tools" / "repo-checks"
TESTS = """
import pytest

def test_runs():
    pass

def test_listed():
    pytest.skip("listed reason")

def test_unlisted():
    pytest.skip("needs a GPU")
"""


def skip_list(path: Path, *entries: tuple[str, str]) -> Path:
    skips = [{"test": test, "run": run, "reason": "a reason"} for test, run in entries]
    path.write_text(json.dumps({"skips": skips}), encoding="utf-8")
    return path


def run_pytest(
    tmp_path: Path, ci: bool, *extra: str, module_skip: bool = True
) -> subprocess.CompletedProcess[str]:
    (tmp_path / "test_a.py").write_text(TESTS, encoding="utf-8")
    if module_skip:
        (tmp_path / "test_gone.py").write_text(
            "import pytest\npytest.skip('module gone', allow_module_level=True)\n",
            encoding="utf-8",
        )
    listed = skip_list(tmp_path / "skips.json", ("test_a.py::test_listed", "pytest"))
    env = {k: v for k, v in os.environ.items() if k != "CI"}
    env["PYTHONPATH"] = str(REPO_CHECKS)
    if ci:
        env["CI"] = "true"
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-p",
            "pinka_repo_checks.skip_policy",
            "-c",
            os.devnull,
            "--rootdir",
            str(tmp_path),
            "-o",
            f"ci_skip_list={listed}",
            *extra,
            str(tmp_path),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_every_skip_is_named_outside_ci(tmp_path: Path) -> None:
    done = run_pytest(tmp_path, ci=False)
    assert done.returncode == 0, done.stdout
    lines = [line for line in done.stdout.splitlines() if line.startswith("skipped test: ")]
    assert sorted(lines) == [
        "skipped test: test_a.py::test_listed (reason: listed reason)",
        "skipped test: test_a.py::test_unlisted (reason: needs a GPU)",
        "skipped test: test_gone.py (reason: module gone)",
    ]


def test_with_pytest_xdist_every_skip_is_named_and_judged_too(tmp_path: Path) -> None:
    # The workers run the tests; the main process prints the summary.
    done = run_pytest(tmp_path, False, "-n", "2")
    assert done.returncode == 0, done.stdout
    lines = [line for line in done.stdout.splitlines() if line.startswith("skipped test: ")]
    assert sorted(lines) == [
        "skipped test: test_a.py::test_listed (reason: listed reason)",
        "skipped test: test_a.py::test_unlisted (reason: needs a GPU)",
        "skipped test: test_gone.py (reason: module gone)",
    ]
    done = run_pytest(tmp_path, True, "-n", "2", module_skip=False)
    assert done.returncode == 1
    assert "skipped test: test_a.py::test_listed (reason: listed reason)" in done.stdout
    assert ci_skips.refusal("test_a.py::test_unlisted", "needs a GPU") in done.stdout
    assert "1 failed, 1 passed, 1 skipped" in done.stdout


def test_in_ci_only_the_listed_skip_passes(tmp_path: Path) -> None:
    done = run_pytest(tmp_path, ci=True, module_skip=False)
    assert done.returncode == 1
    assert "skipped test: test_a.py::test_listed (reason: listed reason)" in done.stdout
    assert ci_skips.refusal("test_a.py::test_unlisted", "needs a GPU") in done.stdout
    assert "1 failed, 1 passed, 1 skipped" in done.stdout
    # A module skipped as a whole is a collection error in CI: nothing runs.
    done = run_pytest(tmp_path, ci=True)
    assert done.returncode == 2
    assert ci_skips.refusal("test_gone.py", "module gone") in done.stdout


def test_the_unittest_runs_name_their_skips_and_obey_the_list(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    request: pytest.FixtureRequest,
) -> None:
    # A module name no other test uses, removed afterwards: unittest's discovery
    # refuses a module of the same name that an earlier test left imported.
    (tmp_path / "test_skips_case.py").write_text(
        "import unittest\n\n"
        "class Case(unittest.TestCase):\n"
        "    def test_runs(self):\n        pass\n"
        "    def test_listed(self):\n        self.skipTest('listed reason')\n"
        "    def test_unlisted(self):\n        self.skipTest('needs a GPU')\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    request.addfinalizer(lambda: sys.modules.pop("test_skips_case", None))
    listed = skip_list(
        tmp_path / "skips.json", ("x/tests/test_skips_case.py::Case::test_listed", "py39")
    )
    argv = ["--prefix", "x/tests", "--skip-list", str(listed), "discover", "-s", str(tmp_path)]
    monkeypatch.delenv("CI", raising=False)
    assert unittest_run.main(argv) == 0
    out = capsys.readouterr().out
    unlisted = "x/tests/test_skips_case.py::Case::test_unlisted"
    assert f"skipped test: {unlisted} (reason: needs a GPU)" in out
    monkeypatch.setenv("CI", "true")
    assert unittest_run.main(argv) == 1
    out = capsys.readouterr().out
    assert ci_skips.refusal(unlisted, "needs a GPU") in out
    assert "test_listed (reason" in out and "refusal" not in out
    # An entry names one run: the py314 run is refused a skip listed for py39 only.
    assert unittest_run.main(["--run", "py314", *argv]) == 1
    out = capsys.readouterr().out
    assert ci_skips.refusal("x/tests/test_skips_case.py::Case::test_listed", "listed reason") in out
    # And the reverse: entries for py314 alone pass the py314 run and not the py39 run.
    only314 = skip_list(
        tmp_path / "skips314.json",
        ("x/tests/test_skips_case.py::Case::test_listed", "py314"),
        (unlisted, "py314"),
    )
    argv314 = ["--prefix", "x/tests", "--skip-list", str(only314), "discover", "-s", str(tmp_path)]
    assert unittest_run.main(["--run", "py314", *argv314]) == 0
    capsys.readouterr()
    assert unittest_run.main(argv314) == 1
    out = capsys.readouterr().out
    assert ci_skips.refusal(unlisted, "needs a GPU") in out


def test_the_list_names_real_tests_with_their_reasons() -> None:
    data = json.loads(ci_skips.LIST.read_text(encoding="utf-8"))
    for entry in data["skips"]:
        assert set(entry) == {"test", "run", "reason"}, entry
        assert entry["run"] in ("pytest", *unittest_run.RUNS), entry
        assert len(entry["reason"]) > 20, entry  # a reason, not a word
        path, *names = entry["test"].split("::")
        text = (ROOT / path).read_text(encoding="utf-8")
        assert f"def {names[-1]}(" in text, entry
    # Empty or nearly so: a test that can run in CI runs there.
    assert len(data["skips"]) <= 3


def test_every_pytest_run_loads_the_plugin_and_the_unittest_runs_use_the_runner() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "-p pinka_repo_checks.skip_policy" in pyproject
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    for target, run in (("test-py39", None), ("test-py314", "py314")):
        block = makefile[makefile.index(f"\n{target}:") :].split("\n\n", 1)[0]
        commands = [line for line in block.splitlines() if not line.lstrip().startswith("@#")]
        assert not [line for line in commands if "python -m unittest" in line], target
        # preflight, pinka, the install record module and the uninstall module
        assert block.count("python -m pinka_repo_checks.unittest_run") == 4, target
        if run:
            # Each names its run, so the CI skip list's entries for it apply.
            assert block.count(f"--run {run}") == 4, target
