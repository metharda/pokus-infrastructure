"""refplat's controller ``ctl`` (profile ``controller``, BUILD_PROMPT §7.18).

It answers on the two sockets infra hands it by socket activation: ``api``
for researchers and root (the ``refplat`` CLI and the hooks) and ``session``,
dedicated to the agent. Through ``api`` it builds a dataset from the
``train``-split cases of its restricted content package, publishes it through
the GPU arbiter, submits the job ``tune`` (or ``tunecpu``), follows the run
and imports the adapter's lineage. Its tables: ``public.refplat_runs`` and
``public.refplat_datasets``, and ``restricted.refplat_cases`` for the case ids
of every dataset (restricted data stays in the restricted schema). Records
go to its state (``records``, backed up).
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import socket
import sys
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from refplat import PLATFORM, pg, training
from refplat.common import (
    ADAPTERS,
    DB_SECRETS,
    RefplatError,
    cases_dir,
    endpoints,
    listen_sockets,
    read_line,
    send_line,
    settings,
    state_dir,
)

Database = Callable[[], pg.Connection]


def _now() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Controller:
    """The controller's state and its answers."""

    def __init__(
        self,
        *,
        config: Mapping[str, Any],
        settings_: Mapping[str, Any],
        database: Database | None,
        state: Path,
        cases: Path,
        adapters: Path = ADAPTERS,
    ) -> None:
        """Set up.

        Args:
            config: ``endpoints.toml``.
            settings_: ``platform_settings.ctl``.
            database: Opens a connection as the controller's role.
            state: The controller's state directory.
            cases: The restricted content package ``cases``.
            adapters: The adapters area.
        """
        self.config = config
        self.platform = str(config.get("platform") or PLATFORM)
        self.settings = dict(settings_)
        self.database = database
        self.state = state
        self.cases = cases
        self.adapters = adapters
        self.lock = threading.Lock()
        self.draining = False

    # --- records ------------------------------------------------------------------------

    def _records(self) -> Path:
        return self.state / "records"

    def _load(self, name: str) -> dict[str, Any]:
        try:
            value = json.loads((self._records() / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def _save(self, name: str, value: Mapping[str, Any]) -> None:
        path = self._records() / name
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{name}.tmp")
        temp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temp.replace(path)

    def _sql(self, statements: list[str]) -> None:
        if self.database is None:
            return
        conn = self.database()
        try:
            pg.execute(conn, statements)
        finally:
            conn.close()

    def _arbiter(self) -> str:
        return str(self.config.get("gpu", {}).get("arbiter_socket", "/run/pinka/gpu.sock"))

    # --- api ------------------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Whether the controller works, its database included."""
        database = "not configured"
        if self.database is not None:
            try:
                conn = self.database()
                try:
                    conn.query("SELECT 1 FROM public.refplat_runs LIMIT 1")
                    database = "ok"
                finally:
                    conn.close()
            except (OSError, pg.PgError) as exc:
                database = str(exc)
        runs = self._load("runs.json")
        return {
            "ok": database in ("ok", "not configured"),
            "database": database,
            "draining": self.draining,
            "runs": len(runs),
            "agent_report": bool(self._load("agent-probe.json")),
        }

    def dataset(self) -> dict[str, Any]:
        """Build the training dataset from the content package and publish it."""
        cases = training.read_cases(self.cases)
        name = dt.datetime.now(dt.UTC).strftime("train-%Y%m%dT%H%M%S")
        directory, provenance = training.build_dataset(cases, name, self.state)
        sha = training.publish(self._arbiter(), directory, provenance)
        ids = [i["id"] for i in provenance["instances"]]
        self._sql(
            [
                pg.sql(
                    "INSERT INTO public.refplat_datasets (sha256, instances) VALUES ({}, {}) "
                    "ON CONFLICT (sha256) DO NOTHING",
                    sha,
                    len(ids),
                ),
                *[
                    pg.sql(
                        "INSERT INTO restricted.refplat_cases (id, split, dataset) "
                        "VALUES ({}, 'train', {}) ON CONFLICT DO NOTHING",
                        i,
                        sha,
                    )
                    for i in ids
                ],
            ]
        )
        datasets = self._load("datasets.json")
        datasets[sha] = {"instances": len(ids), "published_at": _now(), "directory": name}
        self._save("datasets.json", {**datasets, "latest": sha})
        return {"ok": True, "dataset": sha, "instances": len(ids)}

    def train(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Submit a training run of the latest (or a named) dataset."""
        if self.draining:
            raise RefplatError("the controller is draining; no new runs")
        datasets = self._load("datasets.json")
        dataset = str(request.get("dataset") or datasets.get("latest") or "")
        if not dataset:
            raise RefplatError("no dataset yet: run refplat dataset first")
        job = str(request.get("job") or self.settings.get("job") or "tunecpu")
        seed = int(request.get("seed", 1))
        base = self.settings.get("base_model") if job == "tune" else None
        answer = training.arbiter(
            self._arbiter(),
            {
                "request": "train",
                "job": job,
                "submission": training.submission(
                    dataset, seed=seed, base_model=base, settings={**self.settings, **request}
                ),
            },
        )
        run = str(answer["run"])
        runs = self._load("runs.json")
        runs[run] = {"job": job, "seed": seed, "dataset": dataset, "status": "submitted"}
        self._save("runs.json", runs)
        self._sql(
            [
                pg.sql(
                    "INSERT INTO public.refplat_runs (run, job, seed, dataset, status) "
                    "VALUES ({}, {}, {}, {}, 'submitted')",
                    run,
                    job,
                    seed,
                    dataset,
                )
            ]
        )
        return {"ok": True, "run": run, "spec_sha256": answer.get("spec_sha256")}

    def runs(self) -> dict[str, Any]:
        """Every run, followed through the arbiter; lineage imported once it succeeded."""
        runs = self._load("runs.json")
        status = training.arbiter(self._arbiter(), {"request": "status"})
        changed = False
        for run, item in runs.items():
            if item.get("status") in training.FINAL:
                continue
            new = training.run_status(status, run)
            lineage = training.find_lineage(run, self.adapters)
            if lineage is not None:
                new = "succeeded"
                item["adapter"] = lineage["adapter"]["sha256"]
            if new == item.get("status"):
                continue
            item["status"] = new
            changed = True
            self._sql(
                [
                    pg.sql(
                        "UPDATE public.refplat_runs SET status = {}, adapter = {}, "
                        "updated_at = now() WHERE run = {}",
                        new,
                        item.get("adapter"),
                        run,
                    )
                ]
            )
        if changed:
            self._save("runs.json", runs)
        return {"ok": True, "runs": runs}

    def lineage(self, run: str) -> dict[str, Any]:
        """A succeeded run's lineage record."""
        found = training.find_lineage(run, self.adapters)
        if found is None:
            raise RefplatError(f"run {run} has registered no adapter")
        return {"ok": True, "lineage": found}

    def drain(self) -> dict[str, Any]:
        """Start no new runs; drained once no run is active (a suspended one is not)."""
        self.draining = True
        active = [
            r for r, item in self.runs()["runs"].items() if item.get("status") in training.ACTIVE
        ]
        return {"ok": True, "drained": not active, "active": active}

    def handle_api(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Answer a request on ``api``."""
        command = request.get("command")
        handlers: dict[str, Callable[[], dict[str, Any]]] = {
            "status": self.status,
            "dataset": self.dataset,
            "train": lambda: self.train(request),
            "runs": self.runs,
            "lineage": lambda: self.lineage(str(request.get("run"))),
            "probe-report": lambda: {"ok": True, "report": self._load("agent-probe.json")},
            "drain": self.drain,
        }
        handler = handlers.get(str(command))
        if handler is None:
            raise RefplatError(f"unknown command {command!r}")
        with self.lock:
            return handler()

    def handle_session(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Answer the agent on ``session``: it may only say hello and report."""
        command = request.get("command")
        if command == "hello":
            return {"ok": True, "platform": self.platform}
        if command == "report" and isinstance(request.get("report"), dict):
            report = request["report"]
            with self.lock:
                self._save("agent-probe.json", {"received_at": _now(), **report})
            return {"ok": True, "reached": len(report.get("reached", []))}
        raise RefplatError("the session socket takes hello and report only")


def serve_socket(
    listener: socket.socket, handle: Callable[[Mapping[str, Any]], dict[str, Any]]
) -> None:
    """Answer connections on one socket, one JSON line each, until the process ends."""
    while True:
        conn, _ = listener.accept()
        threading.Thread(target=_answer, args=(conn, handle), daemon=True).start()


def _answer(conn: socket.socket, handle: Callable[[Mapping[str, Any]], dict[str, Any]]) -> None:
    with conn:
        conn.settimeout(3600)
        try:
            answer = handle(read_line(conn))
        except (RefplatError, pg.PgError, OSError, ValueError, KeyError) as exc:
            answer = {"ok": False, "error": str(exc)}
        with contextlib.suppress(OSError):
            send_line(conn, answer)


def main() -> int:
    """Run the controller on the sockets systemd hands over."""
    sockets = listen_sockets()
    missing = {"api", "session"} - set(sockets)
    if missing:
        sys.stderr.write(
            f"refplat-ctl: no socket {', '.join(sorted(missing))} (socket activation)\n"
        )
        return 1
    config = endpoints()
    platform = str(config.get("platform") or PLATFORM)
    postgres = config.get("postgres", {})

    def database() -> pg.Connection:
        return pg.connect(
            str(postgres["socket_dir"]),
            str(postgres["roles"]["ctl"]),
            str(postgres["database"]),
            DB_SECRETS,
        )

    controller = Controller(
        config=config,
        settings_=settings(),
        database=database if postgres else None,
        state=state_dir(platform),
        cases=cases_dir(platform),
    )
    threads = [
        threading.Thread(target=serve_socket, args=(sockets["api"], controller.handle_api)),
        threading.Thread(target=serve_socket, args=(sockets["session"], controller.handle_session)),
    ]
    for thread in threads:
        thread.daemon = True
        thread.start()
    for thread in threads:
        thread.join()
    return 0


if __name__ == "__main__":
    sys.exit(main())
