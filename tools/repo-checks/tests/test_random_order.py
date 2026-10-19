"""The random test order of ``make test-random-order``."""

from __future__ import annotations

import itertools
import os
import subprocess
import sys
from pathlib import Path

from pinka_repo_checks import random_order

REPO_CHECKS = Path(__file__).resolve().parents[1]
IDS = [
    "a/test_one.py::test_x",
    "a/test_one.py::test_y",
    "a/test_one.py::Case::test_p",
    "a/test_one.py::Case::test_q",
    "b/test_two.py::test_z[1]",
    "b/test_two.py::test_z[2]",
    "c/test_three.py::test_w",
]


def test_the_order_is_a_permutation_that_keeps_modules_and_classes_together() -> None:
    orders = {tuple(random_order.shuffled(IDS, seed)) for seed in range(40)}
    assert len(orders) > 1  # the seed changes the order
    for order in orders:
        assert sorted(order) == list(range(len(IDS)))
        modules = [IDS[i].split("::")[0] for i in order]
        assert len(modules) - sum(a == b for a, b in itertools.pairwise(modules)) == 3
        case = [i for i, index in enumerate(order) if "::Case::" in IDS[index]]
        assert case[1] - case[0] == 1
    assert random_order.shuffled(IDS, 5) == random_order.shuffled(IDS, 5)  # repeatable


def test_the_plugin_reorders_a_run_and_names_the_seed(tmp_path: Path) -> None:
    log = tmp_path / "ran.txt"
    for module, names in (("test_one.py", "abc"), ("test_two.py", "de")):
        body = "".join(
            f"def test_{name}():\n    open({str(log)!r}, 'a').write('{module}::test_{name}\\n')\n\n"
            for name in names
        )
        (tmp_path / module).write_text(body)
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-p",
            "pinka_repo_checks.random_order",
            "--order-seed",
            "3",
            "-c",
            os.devnull,
            "--rootdir",
            str(tmp_path),
            str(tmp_path),
        ],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(REPO_CHECKS)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "random test order, seed 3: make test-random-order SEED=3 repeats it" in done.stdout
    ran = log.read_text().splitlines()
    collected = sorted(ran, key=lambda n: (n.split("::")[0], n))
    expected = [collected[i] for i in random_order.shuffled(collected, 3)]
    assert ran == expected
