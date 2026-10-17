"""refplat's hooks (contract v2 §12.9): ``refplat-admin migrate|reconcile|drain|health``.

Each prints exactly one JSON line and exits 0 on success.

* ``migrate`` (as the migration account, before the new release starts):
  the tables, owned by the migrator, with the controller's grants (infra's
  bootstrap gives the controller no schema privilege; the restricted check
  allows the controller's).
* ``reconcile`` (before the services start): the controller's state.
* ``drain [--timeout N]`` (as the controller): no new runs; waits until no
  run is active.
* ``health`` (as the controller): the controller answers and reaches its
  database.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Sequence

from refplat import pg
from refplat.common import (
    API_SOCKET,
    DB_SECRETS,
    RefplatError,
    endpoints,
    hook_output,
    request,
    state_dir,
)

TABLES = (
    "CREATE TABLE IF NOT EXISTS public.refplat_datasets ("
    "sha256 text PRIMARY KEY, instances integer NOT NULL, "
    "created_at timestamptz NOT NULL DEFAULT now())",
    "CREATE TABLE IF NOT EXISTS public.refplat_runs ("
    "run text PRIMARY KEY, job text NOT NULL, seed integer NOT NULL, dataset text NOT NULL, "
    "status text NOT NULL, adapter text, updated_at timestamptz NOT NULL DEFAULT now())",
    "CREATE TABLE IF NOT EXISTS restricted.refplat_cases ("
    "id text NOT NULL, split text NOT NULL, dataset text NOT NULL, PRIMARY KEY (id, dataset))",
)


def migrate() -> int:
    """Create the tables and grant the controller what it uses."""
    config = endpoints()
    postgres = config["postgres"]
    controller = pg.identifier(str(postgres["roles"]["ctl"]))
    statements = [
        *TABLES,
        f"GRANT USAGE ON SCHEMA public, restricted TO {controller}",
        "GRANT SELECT, INSERT, UPDATE ON public.refplat_datasets, public.refplat_runs "
        f"TO {controller}",
        f"GRANT SELECT, INSERT ON restricted.refplat_cases TO {controller}",
    ]
    conn = pg.connect(
        str(postgres["socket_dir"]),
        str(postgres["migrator_role"]),
        str(postgres["database"]),
        DB_SECRETS,
    )
    try:
        pg.execute(conn, statements)
    finally:
        conn.close()
    hook_output({"ok": True, "tables": 3})
    return 0


def reconcile() -> int:
    """The controller's state directories exist (infra creates them; nothing else to do)."""
    state = state_dir(str(endpoints()["platform"]))
    missing = [d for d in ("records", "datasets") if not (state / d).is_dir()]
    hook_output({"ok": not missing, "missing": missing})
    return 1 if missing else 0


def drain(timeout: int) -> int:
    """Stop new runs and wait until none is active."""
    deadline = time.monotonic() + timeout
    while True:
        answer = request(API_SOCKET, {"command": "drain"})
        if answer.get("drained"):
            hook_output({"ok": True, "drained": True})
            return 0
        if time.monotonic() >= deadline:
            hook_output({"ok": False, "drained": False, "active": answer.get("active", [])})
            return 1
        time.sleep(min(5.0, max(0.0, deadline - time.monotonic())))


def health() -> int:
    """The controller answers and reaches its database."""
    answer = request(API_SOCKET, {"command": "status"})
    hook_output({"ok": bool(answer.get("ok")), "database": answer.get("database")})
    return 0 if answer.get("ok") else 1


def main(argv: Sequence[str] | None = None) -> int:
    """Run a hook."""
    parser = argparse.ArgumentParser(prog="refplat-admin", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="hook", required=True)
    sub.add_parser("migrate")
    sub.add_parser("reconcile")
    drain_cmd = sub.add_parser("drain")
    drain_cmd.add_argument("--timeout", type=int, default=600)
    sub.add_parser("health")
    args = parser.parse_args(argv)
    try:
        if args.hook == "migrate":
            return migrate()
        if args.hook == "reconcile":
            return reconcile()
        if args.hook == "drain":
            return drain(args.timeout)
        return health()
    except (RefplatError, pg.PgError, OSError, KeyError, ValueError) as exc:
        hook_output({"ok": False, "error": str(exc)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
