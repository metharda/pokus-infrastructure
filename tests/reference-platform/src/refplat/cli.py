"""``refplat``, the researchers' CLI: it talks to the controller over ``api``.

``refplat status | dataset | train [--job J] [--seed N] [--steps N]
[--step-seconds S] [--wait] | runs | lineage RUN | probe-report``, and
``refplat infer``, which asks the broker over ``infer`` for the models of the
inference API; every answer is printed as JSON.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Sequence
from typing import Any

from refplat.common import API_SOCKET, BROKER_SOCKET, RefplatError, request
from refplat.training import FINAL

WAIT_SECONDS = 5.0


def _call(value: dict[str, Any], *, broker: bool = False) -> dict[str, Any]:
    answer = request(BROKER_SOCKET if broker else API_SOCKET, value, timeout=3600)
    if not answer.get("ok"):
        raise RefplatError(str(answer.get("error")))
    return answer


def wait(run: str, timeout: float) -> dict[str, Any]:
    """Poll ``runs`` until the run is final or the time is up."""
    deadline = time.monotonic() + timeout
    while True:
        item = _call({"command": "runs"})["runs"].get(run, {})
        if item.get("status") in FINAL or time.monotonic() >= deadline:
            return {"ok": item.get("status") == "succeeded", "run": run, **item}
        time.sleep(WAIT_SECONDS)


def main(argv: Sequence[str] | None = None) -> int:
    """Run a command."""
    parser = argparse.ArgumentParser(prog="refplat", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "dataset", "runs", "probe-report", "infer"):
        sub.add_parser(name)
    train = sub.add_parser("train")
    train.add_argument("--job", choices=("tune", "tunecpu"))
    train.add_argument("--seed", type=int, default=1)
    train.add_argument("--steps", type=int)
    train.add_argument("--step-seconds", type=float)
    train.add_argument("--wait", type=float, default=0.0, help="seconds to wait for the run")
    lineage = sub.add_parser("lineage")
    lineage.add_argument("run")
    args = parser.parse_args(argv)
    try:
        if args.command == "train":
            value: dict[str, Any] = {"command": "train", "seed": args.seed}
            if args.job:
                value["job"] = args.job
            if args.steps is not None:
                value["steps"] = args.steps
            if args.step_seconds is not None:
                value["step_seconds"] = args.step_seconds
            answer = _call(value)
            if args.wait:
                answer = wait(str(answer["run"]), args.wait)
        elif args.command == "lineage":
            answer = _call({"command": "lineage", "run": args.run})
        elif args.command == "infer":
            answer = _call({"command": "models"}, broker=True)
        else:
            answer = _call({"command": args.command})
    except RefplatError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    print(json.dumps(answer, indent=2, sort_keys=True))
    return 0 if answer.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
