"""Datasets, training runs and lineage through infra's GPU arbiter (§7.18).

The controller builds a dataset from the ``train``-split cases of its
restricted content package, asks the arbiter to publish it, submits a job and
reads the registered adapter's lineage back. Test-split cases never leave the
restricted area: the dataset holds only training cases, and its provenance
lists every one of them with its split, which the arbiter checks again.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from refplat.common import ADAPTERS, RefplatError, request

DATASET_FILE = "train.jsonl"
ADAPTER = "refplat-tune"
# A run's statuses in the controller's records: final ones never change again;
# drain waits for the active ones.
FINAL = ("succeeded", "failed", "interrupted", "ended")
ACTIVE = ("submitted", "running")


def read_cases(directory: Path) -> list[dict[str, Any]]:
    """Every case of the content package (``cases.jsonl``: one object per line).

    Args:
        directory: The content package.

    Returns:
        The cases with ``id`` and ``split``.

    Raises:
        RefplatError: On a malformed line.
    """
    cases = []
    for number, line in enumerate((directory / "cases.jsonl").read_text("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        case = json.loads(line)
        if not isinstance(case, dict) or not case.get("id") or not case.get("split"):
            raise RefplatError(f"cases.jsonl:{number}: a case needs an id and a split")
        cases.append(case)
    return cases


def build_dataset(
    cases: Iterable[Mapping[str, Any]], name: str, state: Path
) -> tuple[Path, dict[str, Any]]:
    """Write the training dataset into the controller's state.

    Args:
        cases: The content package's cases.
        name: Directory name below ``datasets/``.
        state: The controller's state directory.

    Returns:
        The dataset directory and its provenance (``instances`` and
        ``source_runs``) for the arbiter.

    Raises:
        RefplatError: If there is no training case.
    """
    train = [c for c in cases if c.get("split") == "train"]
    if not train:
        raise RefplatError("the content package has no train-split case")
    directory = state / "datasets" / name
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True, mode=0o700)
    lines = [json.dumps(c, sort_keys=True) for c in sorted(train, key=lambda c: str(c["id"]))]
    (directory / DATASET_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")
    provenance = {
        "instances": [{"id": str(c["id"]), "split": "train"} for c in train],
        "source_runs": [],
    }
    return directory, provenance


def arbiter(socket: str, value: Mapping[str, Any]) -> dict[str, Any]:
    """One request to the GPU arbiter; a refusal raises.

    Raises:
        RefplatError: If the arbiter refuses or cannot be reached.
    """
    answer = request(Path(socket), value, timeout=3600)
    if not answer.get("ok"):
        raise RefplatError(f"the GPU arbiter refused {value.get('request')}: {answer.get('error')}")
    return answer


def publish(socket: str, directory: Path, provenance: Mapping[str, Any]) -> str:
    """Publish a dataset; returns its hash."""
    answer = arbiter(
        socket, {"request": "publish-dataset", "source": str(directory), "provenance": provenance}
    )
    return str(answer["dataset_sha256"])


def submission(
    dataset: str, *, seed: int, base_model: str | None, settings: Mapping[str, Any]
) -> dict[str, Any]:
    """A training submission for the arbiter.

    Args:
        dataset: The published dataset's hash.
        seed: The seed.
        base_model: A model id, or None for the CPU-only job.
        settings: The controller's settings (``steps``, ``step_seconds``).

    Returns:
        The submission.
    """
    return {
        "base_model": base_model,
        "datasets": {"train": dataset},
        "hyperparameters": {
            "steps": int(settings.get("steps", 8)),
            "step_seconds": float(settings.get("step_seconds", 0)),
        },
        "seed": seed,
        "sessions_drained": True,
    }


def lineage(sha256: str, adapters: Path = ADAPTERS) -> dict[str, Any]:
    """The lineage record of a registered adapter.

    Raises:
        RefplatError: If the adapter is not registered.
    """
    path = adapters / ADAPTER / sha256 / "LINEAGE.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RefplatError(f"no lineage for adapter {ADAPTER}@{sha256}: {exc}") from exc
    if not isinstance(value, dict):
        raise RefplatError(f"{path}: not an object")
    return value


def find_lineage(run: str, adapters: Path = ADAPTERS) -> dict[str, Any] | None:
    """The lineage of the adapter a run registered, or None (yet).

    Args:
        run: The arbiter's run ID.
        adapters: The adapters area.

    Returns:
        The lineage record.
    """
    for path in sorted((adapters / ADAPTER).glob("*/LINEAGE.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(value, dict) and (value.get("job") or {}).get("run") == run:
            return value
    return None


def run_status(status: Mapping[str, Any], run: str) -> str:
    """A run's status from the arbiter's ``status`` answer.

    The run being trained, or suspended while the lab is stopped, is the
    arbiter's current one; a run that ended has its last transition out of
    ``training``. A run that is neither ended where the controller could not
    see it (the answer shows only the last transitions).

    Args:
        status: The arbiter's ``status`` answer.
        run: The run ID.

    Returns:
        ``running``, ``suspended``, ``succeeded``, ``failed``, ``interrupted``
        or ``ended``.
    """
    if status.get("mode") == "training" and status.get("run") == run:
        current = (status.get("job") or {}).get("status")
        return "suspended" if current == "suspended" else "running"
    for item in reversed(status.get("transitions") or []):
        if (
            item.get("run") == run
            and item.get("from") == "training"
            and item.get("to") != "training"
        ):
            outcome = str(item.get("outcome"))
            return "succeeded" if outcome == "ok" else outcome
    return "ended"
