---
title: "ADR-0016: Telemetry reached only through the query proxy and the agent socket"
type: explanation
audience: [developer, operator, reader]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
---

# ADR-0016: Telemetry reached only through the query proxy and the agent socket

- **Status:** proposed (plan step 3d.4; the owner decides with the step's commit)
- **Date:** 2026-10-10
- **Deciders:** pokus-infrastructure maintainers (BUILD_PROMPT §7.9, §7.11)

## Context

A platform's services must query telemetry only through the query proxy,
which forwards the query endpoints of Prometheus, Loki and Tempo and refuses
everything else; they send telemetry only to the collector; the
`untrusted-agent` sends only through its own socket, and its telemetry is
marked as untrusted. BUILD_PROMPT §7.9 asks for the mechanism in this order
of preference: (a) the stores have no listener reachable from the host
loopback, or Prometheus requires client authentication that only the proxy
and the collector hold; (b) otherwise, platform accounts get a dedicated UID
range and an nftables output rule drops their connections to the backend
ports.

Option (a) does not hold on this lab:

- Loki and Tempo listen on TCP only; neither can serve on a Unix socket.
- Prometheus scrapes exporters, the Incus API and vLLM on the host loopback,
  so it uses the host network (as every observability container does).
- Moving Loki and Tempo to a Docker network would not hide them either: the
  host reaches the addresses of its bridges, and platform services run on
  the host.
- Client authentication would cover Prometheus only.

## Decision

1. **Option (b).** Platform accounts get UIDs from 60578-61183
   (`pinka_platform_uids`), a range that systemd's allocation table
   (`docs/UIDS-GIDS.md`, v255) lists as unused, between the host users mapped
   into containers (60514-60577) and the dynamic service users (61184-65519).
   The deployment tool keeps an account's UID, gives a new account the lowest
   UID of the range that no account on the host uses, and refuses an existing
   account outside the range instead of moving it.
2. **The telemetry guard.** Its own nftables table `inet
   pinka_telemetry_guard`, loaded by `pinka-telemetry-guard.service` on every
   firewall backend, drops TCP connections from the range to Prometheus,
   Loki and Tempo (HTTP, gRPC, Tempo's OTLP receiver), the collector's
   metrics exporter and the query proxy's loopback port. The collector's OTLP
   receivers stay open: platform services send telemetry there.
3. **The query proxy** is an nginx container (the pinned nginx release of the
   golden images) in the observability project. It listens on a socket that
   `/run/pinka/query.sock` links to (0660, group `pinka-query`) and on a
   loopback port for Grafana, whose Prometheus, Loki and Tempo data sources
   go through it. Its allowlist of methods and paths was checked against the
   routes of the pinned releases; everything else is answered with 403.
4. **The agent socket.** `pinka-otlp-agent.socket` (`/run/pinka/otlp/agent.sock`)
   starts systemd's socket proxy, which forwards OTLP/HTTP to the collector's
   receiver `otlp/agent` on a loopback port of its own. Its pipelines stamp
   every resource with `pinka.trust=untrusted`, overwriting what the sender
   set; Loki indexes `pinka.trust` as a label.

## Consequences

- The isolation does not depend on the stores' own features, and the same
  rule extends to the inference API in plan step 3d.5 (§7.11 option b).
- The range overlaps Debian's band for globally allocated, on-demand accounts
  (60000-64999). The deployment tool never takes a UID in use, so a later
  package account in the range only narrows the free UIDs.
- Humans and infra's own accounts are outside the range: researchers can
  still reach the stores on the host loopback directly. Closing that too
  (an allowlist of infra's UIDs instead of a denylist of the platform's) is
  open for the owner.
- The socket path is a link: the proxy's container cannot create a socket
  directly in `/run/pinka`, which belongs to root.
- The isolation probe of the reference platform (§7.18, plan step 3d.6)
  proves it on a host; doctor checks the guard on every host.

## Alternatives considered

- **Basic authentication on Prometheus only** (option a's fallback): leaves
  Loki and Tempo open.
- **An allowlist of infra UIDs** for the backend ports: also closes the
  stores to researchers on the host, but needs every container's UID fixed
  and is stricter than §7.9 asks.
- **A Docker network for the stores**: reachable from the host anyway.
