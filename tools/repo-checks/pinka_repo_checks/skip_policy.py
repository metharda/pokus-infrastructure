"""pytest plugin: every skipped test is named, and in CI only the listed ones may skip.

Loaded for every pytest run (``-p pinka_repo_checks.skip_policy`` in
``pyproject.toml``). After the run it prints one line per skipped test with
its reason (:func:`pinka_repo_checks.ci_skips.line`), locally too. In CI
(``CI=true``) a skipped test, or a module skipped at collection, that the CI
skip list does not name for the ``pytest`` run is reported as failed.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest

from pinka_repo_checks import ci_skips

RUN = "pytest"
ALLOWED = pytest.StashKey[dict[str, str]]()
SKIPPED = pytest.StashKey[list[tuple[str, str]]]()


def _reason(report: pytest.TestReport | pytest.CollectReport) -> str:
    longrepr = report.longrepr
    text = longrepr[2] if isinstance(longrepr, tuple) else str(longrepr)
    return text.removeprefix("Skipped: ")


def pytest_addoption(parser: pytest.Parser) -> None:
    """The ``ci_skip_list`` ini option (the plugin's own tests use another list)."""
    parser.addini("ci_skip_list", "the CI skip list", default=str(ci_skips.LIST))


def pytest_configure(config: pytest.Config) -> None:
    """Read the CI skip list once."""
    config.stash[ALLOWED] = ci_skips.allowed(RUN, Path(str(config.getini("ci_skip_list"))))
    config.stash[SKIPPED] = []


def _judge(config: pytest.Config, report: pytest.TestReport | pytest.CollectReport) -> None:
    reason = _reason(report)
    if ci_skips.in_ci() and report.nodeid not in config.stash[ALLOWED]:
        report.outcome = "failed"
        report.longrepr = ci_skips.refusal(report.nodeid, reason)
    else:
        config.stash[SKIPPED].append((report.nodeid, reason))


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    """Record a skipped test; in CI fail it unless the list names it."""
    report = yield
    if report.skipped:
        _judge(item.config, report)
    return report


@pytest.hookimpl(wrapper=True)
def pytest_make_collect_report(
    collector: pytest.Collector,
) -> Generator[None, pytest.CollectReport, pytest.CollectReport]:
    """The same for a module skipped as a whole at collection."""
    report = yield
    if report.skipped:
        _judge(collector.config, report)
    return report


def pytest_terminal_summary(
    terminalreporter: pytest.TerminalReporter, config: pytest.Config
) -> None:
    """Name every skipped test, also with ``-q``."""
    for test, reason in config.stash[SKIPPED]:
        terminalreporter.write_line(ci_skips.line(test, reason))
