"""Every test job writes JUnit XML, and CI keeps it as an artifact."""

from __future__ import annotations

import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import yaml

from pinka_repo_checks import unittest_run

ROOT = Path(__file__).resolve().parents[3]
REPO_CHECKS = ROOT / "tools" / "repo-checks"
CASES = """
import unittest


class Case(unittest.TestCase):
    def test_passes(self):
        pass

    def test_fails(self):
        self.assertEqual(1, 2)

    def test_errors(self):
        raise RuntimeError("boom")

    def test_skips(self):
        self.skipTest("no GPU")

    def test_sub(self):
        for n in (1, 2):
            with self.subTest(n=n):
                self.assertEqual(n, 1)

    @unittest.expectedFailure
    def test_known(self):
        self.assertEqual(1, 2)


class Broken(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raise RuntimeError("no fixture")

    def test_never(self):
        pass
"""


def results(path: Path) -> dict[tuple[str, str], str]:
    suite = ET.parse(path).getroot()  # noqa: S314 - a file this test wrote
    found = {}
    for case in suite.iter("testcase"):
        kinds = [child.tag for child in case if child.tag in ("failure", "error", "skipped")]
        found[(case.get("classname", ""), case.get("name", ""))] = ",".join(kinds) or "passed"
    return found


def test_the_unittest_runs_write_pytests_junit_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    request: pytest.FixtureRequest,
) -> None:
    tests = tmp_path / "x" / "tests"
    tests.mkdir(parents=True)
    # A module name no other test uses, removed from sys.modules afterwards:
    # unittest's discovery refuses a module of the same name that an earlier
    # test left imported.
    (tests / "test_junit_case.py").write_text(CASES, encoding="utf-8")
    request.addfinalizer(lambda: sys.modules.pop("test_junit_case", None))
    monkeypatch.syspath_prepend(str(tests))
    monkeypatch.delenv("CI", raising=False)
    out = tmp_path / "junit" / "py39.xml"
    argv = ["--prefix", "x/tests", "--junit-xml", str(out), "discover", "-s", str(tests)]
    assert unittest_run.main(argv) == 1
    capsys.readouterr()
    found = results(out)
    assert found == {
        ("x.tests.test_junit_case.Case", "test_passes"): "passed",
        ("x.tests.test_junit_case.Case", "test_fails"): "failure",
        ("x.tests.test_junit_case.Case", "test_errors"): "failure",  # pytest's form
        ("x.tests.test_junit_case.Case", "test_skips"): "skipped",
        ("x.tests.test_junit_case.Case", "test_sub"): "failure",
        ("x.tests.test_junit_case.Case", "test_known"): "skipped",
        # The failed class fixture is a case of its own; its test never ran.
        ("x.tests.test_junit_case.Broken", "setUpClass"): "error",
        ("x.tests.test_junit_case.Broken", "test_never"): "error",
    }
    suite = ET.parse(out).getroot()[0]  # noqa: S314 - a file this test wrote
    assert suite.get("name") == "py39 x/tests"
    counts = [suite.get(key) for key in ("tests", "failures", "errors", "skipped")]
    assert counts == ["8", "3", "2", "2"]
    # pytest names the same tests the same way, with the same results.
    pytest_xml = tmp_path / "junit" / "pytest.xml"
    env = {k: v for k, v in os.environ.items() if k not in ("CI", "PYTEST_ADDOPTS")}
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-c",
            os.devnull,
            "--rootdir",
            str(tmp_path),
            f"--junitxml={pytest_xml}",
            str(tests / "test_junit_case.py"),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        check=False,
    )
    del found[("x.tests.test_junit_case.Broken", "setUpClass")]
    assert results(pytest_xml) == found


def test_the_isolated_run_writes_one_file_per_test_file(tmp_path: Path) -> None:
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "test_a.py").write_text("def test_a():\n    pass\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in ("CI", "PYTEST_ADDOPTS")}
    env["PYTHONPATH"] = str(REPO_CHECKS)
    done = subprocess.run(
        [sys.executable, "-m", "pinka_repo_checks.isolated", "--junit-dir", "junit"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stdout
    assert results(tmp_path / "junit" / "sub" / "test_a.py.xml") == {
        ("sub.test_a", "test_a"): "passed"
    }


def test_every_test_job_writes_and_keeps_junit_xml() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    block = makefile[makefile.index("\ntest-py39:") :].split("\n\n", 1)[0]
    assert block.count("--junit-xml $(CURDIR)/$(JUNIT)/py39-") == 3
    for line in (
        "--junitxml=$(JUNIT)/pytest.xml",
        "--junitxml=$(JUNIT)/random-order.xml",
        "--junit-dir $(JUNIT)/isolated",
        "JUNIT := $(BUILD)/junit",
    ):
        assert line in makefile, line
    for workflow, name in (("ci.yml", "test"), ("isolated.yml", "test-isolated")):
        path = ROOT / ".github" / "workflows" / workflow
        jobs = yaml.safe_load(path.read_text(encoding="utf-8"))
        upload = jobs["jobs"][name]["steps"][-1]
        assert upload["uses"].startswith("actions/upload-artifact@"), name
        assert upload["if"] == "${{ !cancelled() }}", name  # failed runs too
        assert upload["with"] == {
            "name": "junit-${{ github.job }}-attempt-${{ github.run_attempt }}",
            "path": "build/junit/",
            "if-no-files-found": "error",
        }, name
