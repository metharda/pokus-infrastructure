---
title: "ADR-0010: PostgreSQL on Unix sockets only"
type: explanation
audience: [developer, reader]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
---

# ADR-0010: PostgreSQL on Unix sockets only

- **Status:** accepted (Grafana and the exporter on sockets: awaiting owner confirmation)
- **Date:** 2026-10-06
- **Deciders:** pokus-infra maintainers (BUILD_PROMPT §7.8, owner answer E13)

## Context

The database holds every run, including the ground truth in schema `gt`,
which the agent and researchers must never read. A TCP listener can be
reached by anything that can open a connection to its address; a Unix socket
is gated by the permissions of its directory before PostgreSQL sees a
connection. The specification asks for sockets for the platform and a
separate path for the researchers' read-only role, and lets Grafana connect
over an internal Docker network.

## Decision

1. PostgreSQL runs in a container **without any network**
   (`network_mode: none`, `listen_addresses = ''`). Clients reach it through
   two socket directories bind-mounted from the host:
   `/run/pokus/postgres/` (group `pokus-svc`: the platform's service roles and
   the metrics exporter) and `/run/pokus/postgres-reader/` (group `pokus`: the
   researchers' `pokus_reader` and Grafana's `pokus_grafana`). `tmpfiles.d`
   recreates both at boot with mode 0750.
2. `pg_hba.conf` admits only the superuser (for maintenance inside the
   container) and the contract's roles plus `pokus_monitor`, all with
   SCRAM-SHA-256. `pg_hba` cannot tell the two directories apart: the
   directory's group decides who reaches a socket, the password files decide
   which role a client can use (`/etc/pokus/secrets/postgres/<role>` 0640
   `root:<account>`, the reader's pgpass 0640 `root:pokus`).
3. Grafana and the exporter use the sockets too, instead of an internal Docker
   network, because the observability stack runs on the host network (see
   [ADR-0011](0011-otel-telemetry.md)) and PostgreSQL then needs no TCP at all.
4. Passwords are compared with the stored SCRAM secrets; a mismatch is
   replaced by a secret computed on the host, so the clear-text password
   reaches neither the server nor a log, and a second run changes nothing.
5. The bootstrap SQL revokes everything on `gt` from `PUBLIC`, `pokus_reader`
   and `pokus_grafana` on every run, so a migration that grants it by mistake
   is undone. A test runs the rendered configuration and the bootstrap SQL
   against a real server and checks that both roles are denied `gt`.

## Consequences

- No database port exists on the host or on any Docker network; a process
  needs group membership and a password file to connect.
- libpq ignores a password file its group may read, so researchers copy the
  reader credential to `~/.pgpass` (mode 0600) or let a client read it.
- Grafana joins the host group `pokus` inside its container
  (`group_add`); the exporter joins `pokus-svc`.
- The integration contract's socket table lists Grafana and the exporter as
  peers; this is a proposed contract change.

## Alternatives considered

- **TCP on an internal Docker network for Grafana and the exporter**, as the
  specification allows: Grafana and the exporter would need a Docker network
  next to the host network the rest of the observability stack uses, and the
  database would have a TCP listener after all.
- **A separate cluster or proxy for the reader socket**, so that `pg_hba`
  could restrict that directory to `pokus_reader`: more moving parts for a
  guarantee the password files already give.
