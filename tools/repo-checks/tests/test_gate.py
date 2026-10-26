"""Tests for the quiet gate (make lint, test and docs without VERBOSE=1)."""

from __future__ import annotations

import io
import re
import sys
from pathlib import Path

import pytest

from pinka_repo_checks import cli, gate

INFRA = Path(__file__).resolve().parents[3]

# A stand-in for make: it records its arguments and environment in its output
# and behaves by the step's name.
FAKE_MAKE = r"""
import os, sys
step = sys.argv[-1]
print("args:", " ".join(sys.argv[1:]))
print("pytest-addopts:", os.environ.get("PYTEST_ADDOPTS", ""))
if step == "setup" and os.environ.get("FAIL_SETUP"):
    print("uv sync failed")
    sys.exit(2)
if step == "lint-ok":
    print("all fine")
elif step == "lint-warn":
    print("warning: The `X` environment variable is deprecated")
    print("docs/a.md:1: warning: FM100 page has never been verified")
elif step == "lint-bad":
    print("finding one")
    print("finding two")
    sys.exit(1)
elif step == "test-ok":
    print("..s.")
    print("======== 3 passed, 1 skipped, 2 warnings in 1.20s ========")
    print("skipped test: t/test_a.py::test_c (reason: needs a GPU)")
elif step == "test-bad":
    print("Traceback (most recent call last):")
    print('  File "t.py", line 3, in test_x')
    print("AssertionError: assert 1 == 2")
    print("======== 1 failed, 4 passed in 0.50s ========")
    sys.exit(1)
"""


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (
            "======= 2 failed, 2769 passed, 1 skipped, 1 warning in 241.60s (0:04:01) =======",
            (2769, 2, 1, 1),
        ),
        ("====== 2771 passed, 1 skipped in 227.50s (0:03:47) ======", (2771, 0, 1, 0)),
        ("==== 1 passed, 2 deselected, 1 xfailed, 1 xpassed, 3 errors in 1.00s ====", (1, 4, 3, 0)),
        ("====================== no tests ran in 0.01s ======================", (0, 0, 0, 0)),
    ],
)
def test_counts_pytest_results(line: str, expected: tuple[int, int, int, int]) -> None:
    counts = gate.parse(f"...\n{line}\n")
    assert counts.tests
    assert (counts.passed, counts.failed, counts.skipped, counts.warnings) == expected


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ("OK", (114, 0, 0)),
        ("OK (skipped=1)", (113, 0, 1)),
        ("FAILED (failures=1, errors=2, skipped=3)", (108, 3, 3)),
        ("OK (skipped=1, expected failures=2)", (111, 0, 3)),
        ("FAILED (unexpected successes=1)", (113, 1, 0)),
    ],
)
def test_counts_unittest_results(result: str, expected: tuple[int, int, int]) -> None:
    output = f"....\n------\nRan 114 tests in 1.301s\n\n{result}\n"
    counts = gate.parse(output)
    assert counts.tests
    assert (counts.passed, counts.failed, counts.skipped) == expected


def test_adds_several_suites_of_one_step() -> None:
    output = "Ran 114 tests in 1.3s\n\nOK (skipped=1)\nRan 205 tests in 2.4s\n\nOK\n"
    counts = gate.parse(output)
    assert (counts.passed, counts.skipped) == (318, 1)


@pytest.mark.parametrize(
    "line",
    [
        "warning: The `UV_NATIVE_TLS` environment variable is deprecated",
        "WARNING -  Doc file 'a.md' contains a link 'b.md', but the target is not found",
        "\x1b[33mWARNING\x1b[0m -  colored by mkdocs",
        " │  ⚠  Warning from the Material for MkDocs team",
        "docs/how-to/x.md:1: warning: FM100 page has never been verified",
        "scripts/a.sh:3:5: warning: SC2034 x appears unused",
        "tools/x.py:12: DeprecationWarning: datetime.utcnow() is deprecated",
        "  3:1       warning  missing document start",
    ],
)
def test_counts_a_warning_line(line: str) -> None:
    assert gate.parse(line).warnings == 1


@pytest.mark.parametrize(
    "line",
    [
        "=============================== warnings summary ===============================",
        "  /x/y.py:12: DeprecationWarning: shown by pytest, counted in its summary",
        "    warnings.warn(message)",
        "docs lint: 86 pages, 0 errors, 86 warnings",
        "Aborted with 1 warnings in strict mode!",
        "no warning here",
    ],
)
def test_ignores_lines_that_are_no_warning_of_their_own(line: str) -> None:
    assert gate.parse(line).warnings == 0


def test_counts_ansible_lint_warnings_from_its_summary() -> None:
    line = "Passed: 0 failure(s), 2 warning(s) in 472 files processed of 642 encountered."
    assert gate.parse(line).warnings == 2


def test_a_failing_step_counts_at_least_one_failure() -> None:
    passing = gate.step_counts("all fine", 0)
    assert (passing.passed, passing.failed) == (1, 0)
    assert gate.step_counts("finding", 1).failed == 1
    # A test run that crashed after its summary still fails.
    crashed = gate.step_counts("== 5 passed in 1.0s ==\nSegmentation fault", 139)
    assert (crashed.passed, crashed.failed) == (5, 1)
    # A reported failure counts even if the exit status were 0.
    assert gate.step_counts("== 1 failed, 1 passed in 1.0s ==", 0).failed == 1


@pytest.mark.parametrize(
    ("makeflags", "expected"),
    [
        ("n --no-print-directory", True),
        ("kn", True),
        ("r", False),
        (" -- VERBOSE=0", False),
        ("", False),
    ],
)
def test_detects_a_dry_run(makeflags: str, expected: bool) -> None:
    assert gate.dry_run(makeflags) is expected


def fake_make(tmp_path: Path) -> str:
    script = tmp_path / "fake_make.py"
    script.write_text(FAKE_MAKE, encoding="utf-8")
    return f"{sys.executable} {script}"


def run_gate(
    tmp_path: Path, steps: list[str], environ: dict[str, str] | None = None
) -> tuple[int, str]:
    out = io.StringIO()
    status = gate.run(
        "lint",
        steps,
        make=fake_make(tmp_path),
        log_dir=tmp_path / "gate",
        environ={"PATH": "/usr/bin:/bin", **(environ or {})},
        out=out,
    )
    return status, out.getvalue()


def test_success_prints_one_line_with_every_count_and_names_the_skips(tmp_path: Path) -> None:
    status, text = run_gate(tmp_path, ["lint-ok", "lint-warn", "test-ok"])
    assert status == 0
    assert text == (
        f"lint: 5 passed, 0 failed, 1 skipped, 4 warnings "
        f"(3 steps; logs: {tmp_path / 'gate' / 'lint'})\n"
        "  skipped test: t/test_a.py::test_c (reason: needs a GPU)\n"
    )
    logs = tmp_path / "gate" / "lint"
    assert sorted(p.name for p in logs.iterdir()) == [
        "lint-ok.log",
        "lint-warn.log",
        "setup.log",
        "test-ok.log",
    ]
    # Setup runs once, without the flag; every step with it, and pytest in full.
    assert "args: --no-print-directory setup\n" in (logs / "setup.log").read_text()
    step = (logs / "lint-ok.log").read_text()
    assert "args: --no-print-directory GATE_SETUP_DONE=1 lint-ok\n" in step
    assert "pytest-addopts: --tb=long\n" in step


def test_failure_prints_every_failing_step_in_full(tmp_path: Path) -> None:
    status, text = run_gate(tmp_path, ["lint-bad", "lint-ok", "test-bad", "test-ok"])
    assert status == 1
    logs = tmp_path / "gate" / "lint"
    for step in ("lint-bad", "test-bad"):
        log = (logs / f"{step}.log").read_text()
        header = f"----- lint/{step}: exit status 1; complete output ({logs / f'{step}.log'}) -----"
        assert f"{header}\n" in text
        assert log in text  # the whole log, nothing filtered
    assert "Traceback (most recent call last):" in text
    assert "all fine" not in text  # passing steps stay in their logs
    # Every step ran, also after the first failure.
    assert (logs / "test-ok.log").exists()
    assert text.splitlines()[-2:] == [
        f"lint: FAILED: 8 passed, 2 failed, 1 skipped, 2 warnings "
        f"(4 steps; failing: lint-bad, test-bad; logs: {logs})",
        "  skipped test: t/test_a.py::test_c (reason: needs a GPU)",
    ]


def test_a_failed_setup_stops_the_gate(tmp_path: Path) -> None:
    status, text = run_gate(tmp_path, ["lint-ok"], {"FAIL_SETUP": "1"})
    assert status == 1
    assert "uv sync failed" in text
    last = text.splitlines()[-1]
    assert last.startswith("lint: FAILED: 0 passed, 1 failed, 0 skipped, 0 warnings")
    assert not (tmp_path / "gate" / "lint" / "lint-ok.log").exists()


def test_keeps_existing_pytest_options(tmp_path: Path) -> None:
    run_gate(tmp_path, ["lint-ok"], {"PYTEST_ADDOPTS": "-x"})
    log = (tmp_path / "gate" / "lint" / "lint-ok.log").read_text()
    assert "pytest-addopts: -x --tb=long\n" in log


def test_a_dry_run_only_prints_the_commands(tmp_path: Path) -> None:
    status, text = run_gate(tmp_path, ["lint-ok"], {"MAKEFLAGS": "n"})
    assert status == 0
    assert text.splitlines()[-1].endswith("--no-print-directory GATE_SETUP_DONE=1 lint-ok")
    assert not (tmp_path / "gate").exists()


def test_the_command_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(
        ["gate", "docs", "lint-ok", "--make", fake_make(tmp_path), "--log-dir", str(tmp_path)]
    )
    assert code == 0
    assert capsys.readouterr().out.startswith("docs: 1 passed, 0 failed, 0 skipped, ")


def makefile_list(makefile: str, name: str) -> list[str]:
    match = re.search(rf"^{name} := ((?:.*\\\n)*.*)$", makefile, re.MULTILINE)
    assert match, name
    return match.group(1).replace("\\\n", " ").split()


def test_the_makefile_runs_the_same_steps_in_both_modes() -> None:
    makefile = (INFRA / "Makefile").read_text(encoding="utf-8")
    lint = makefile_list(makefile, "LINT_STEPS")
    # Every check the lint target ran before the quiet gate.
    assert lint == [
        "lint-python",
        "lint-shell",
        "lint-yaml",
        "lint-schemas",
        "lint-ansible",
        "lint-portability",
        "lint-neutral",
        "lint-references",
        "lint-references-budget",
        "lint-markdown",
        "lint-spelling",
        "lint-docs",
        "lint-mermaid",
        "privacy",
    ]
    assert makefile_list(makefile, "TEST_STEPS") == ["test-py39", "test-pytest"]
    assert makefile_list(makefile, "DOCS_STEPS") == ["drift-check", "docs-coverage", "docs-build"]
    for target, steps in (("lint", "LINT_STEPS"), ("test", "TEST_STEPS"), ("docs", "DOCS_STEPS")):
        block = makefile[makefile.index(f"\n{target}: ## ") :].split("\nendif", 1)[0]
        assert f"\n{target}: " in block.split("else", 1)[0]
        assert f"$({steps})" in block.split("else", 1)[0]  # VERBOSE=1
        assert f"\t@$(GATE) {target} $({steps})" in block.split("else", 1)[1]  # quiet


def test_ci_keeps_the_verbose_output() -> None:
    text = (INFRA / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    for target in ("lint", "test", "docs"):
        assert re.search(rf"run: make {target} VERBOSE=1( |\n)", text), target
