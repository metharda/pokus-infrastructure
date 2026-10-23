"""The pytest suites in a random order with a logged seed (``make test-random-order``).

A test that relies on what an earlier test left in the process (a class
attribute, a module cache, a registered handler) passes in the usual order by
chance and fails when it runs first. This pytest plugin shuffles the collected
tests with a seed, so such a test shows up in CI: the modules in a random
order, and within each module its classes and their tests. A module's tests
stay together, so module-scoped fixtures are set up once.

The seed comes from ``--order-seed``; the header and the summary print it,
and the same seed gives the same order again (``make test-random-order
SEED=<seed>``). Enabled only with ``-p pinka_repo_checks.random_order``; the
gate's own runs keep the usual order.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest
    from _pytest.terminal import TerminalReporter


def shuffled(node_ids: Sequence[str], seed: int) -> list[int]:
    """The order to run tests in.

    Args:
        node_ids: The collected tests' node IDs (``path::Class::test``).
        seed: The random seed.

    Returns:
        The indexes of ``node_ids`` in the new order: modules shuffled, and
        within each module its groups (a class, or the module's own
        functions) and the tests of each group; every index exactly once.
    """
    rng = random.Random(seed)  # noqa: S311 - an order of tests, not a secret
    modules: dict[str, dict[str, list[int]]] = {}
    for index, node_id in enumerate(node_ids):
        parts = node_id.split("::")
        group = "::".join(parts[:-1]) if len(parts) > 2 else parts[0]  # noqa: PLR2004
        modules.setdefault(parts[0], {}).setdefault(group, []).append(index)
    order: list[int] = []
    names = list(modules)
    rng.shuffle(names)
    for name in names:
        groups = list(modules[name].values())
        rng.shuffle(groups)
        for group_indexes in groups:
            rng.shuffle(group_indexes)
            order += group_indexes
    return order


def pytest_addoption(parser: pytest.Parser) -> None:
    """``--order-seed``."""
    parser.addoption(
        "--order-seed",
        type=int,
        required=True,
        help="the seed of the random test order (printed in the header and the summary)",
    )


def pytest_report_header(config: pytest.Config) -> str:
    """Name the seed before the run."""
    return f"random test order, seed {config.getoption('order_seed')}"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Reorder the collected tests."""
    order = shuffled([item.nodeid for item in items], config.getoption("order_seed"))
    items[:] = [items[index] for index in order]


def pytest_terminal_summary(terminalreporter: TerminalReporter, config: pytest.Config) -> None:
    """Name the seed after the run, also with ``-q``."""
    seed = config.getoption("order_seed")
    terminalreporter.write_line(
        f"random test order, seed {seed}: make test-random-order SEED={seed} repeats it"
    )
