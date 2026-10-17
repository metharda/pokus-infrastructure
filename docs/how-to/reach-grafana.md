---
title: How to reach Grafana and the lab database
type: how-to
audience: [researcher]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: Open Grafana through an SSH port forward over the tailnet, find a run in the run explorer, and use the read-only database credential.
---

# How to reach Grafana and the lab database

Grafana and every other lab service listen only on the lab host's
`127.0.0.1`. You reach them through an SSH port forward over the tailnet;
nothing is exposed anywhere else.

!!! note "Prerequisites"
    - A researcher account on the lab host with your SSH key, and your laptop
      in the lab's tailnet.
    - Your Grafana password from the lab's operator (the accounts come from
      the lab's vault).

## Open Grafana

1. Forward Grafana's port when you log in:

    <!-- qa:skip reason="needs a tailnet and a researcher account" -->
    ```bash
    ssh -L 18700:127.0.0.1:18700 <name>@<lab-host>
    ```

    `<name>` is your account, `<lab-host>` the lab host's tailnet name.

2. Open `http://127.0.0.1:18700` on your laptop and sign in with your account
   name and Grafana password. If the lab publishes Grafana on the tailnet with
   `tailscale serve`, the operator gives you that address instead.

3. The dashboards are in the folder **pinka**: host overview, GPU and vLLM,
   Incus targets, lab services health, and the run explorer.

## Look at one run

Open **potka: run explorer** and paste the run's ID into `run_id`. The
dashboard shows the run's database row, its logs, the CPU and memory of its
targets and its traces; paste a trace ID from the trace table into
`trace_id` to open that trace. In Explore, the same filters are
`{service_name=~".+"} | potka_run_id="<run_id>"` (Loki) and
`{ resource.potka.run_id = "<run_id>" }` (Tempo).

## Use the read-only database credential

The researchers' database role `potka_reader` can read the platform's tables
but never the ground truth (schema `gt`). Its credential is
`/etc/pinka/postgres-reader/pgpass`, readable by the group `pinka`. PostgreSQL
clients ignore a password file that others may read, so copy it first (this
replaces an existing `~/.pgpass`; append the line instead if you have one):

<!-- qa:run env=E2 -->
```bash
install -m 0600 /etc/pinka/postgres-reader/pgpass ~/.pgpass
```

A PostgreSQL client on the lab host then connects through the reader socket:

<!-- qa:skip reason="needs a PostgreSQL client on the lab host" -->
```bash
psql "host=/run/pinka/postgres-reader dbname=potka user=potka_reader"
```

`/run/pinka/postgres-reader` is the reader socket directory; the database
has no network listener at all ([ADR-0010](../adr/0010-postgres-socket-only.md)).

## Verify access

With the SSH session from "Open Grafana" still open, ask Grafana for its
health on your laptop:

<!-- qa:skip reason="needs the SSH port forward of a researcher" -->
```bash
curl -fsS http://127.0.0.1:18700/api/health
```

The answer contains `"database": "ok"`. On the lab host, the reader
credential works when this prints `potka_reader`:

<!-- qa:skip reason="needs a PostgreSQL client on the lab host" -->
```bash
psql "host=/run/pinka/postgres-reader dbname=potka user=potka_reader" -tAc "SELECT current_user"
```

Related: [How telemetry, metrics and logs flow through the lab](../explanation/telemetry.md).
