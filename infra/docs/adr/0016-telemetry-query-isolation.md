---
title: "ADR-0016: Telemetry reached only through the query proxy and the agent socket"
type: explanation
audience: [developer, operator, reader]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
---

# ADR-0016: Telemetry reached only through the query proxy and the agent socket

- **Status:** accepted with the owner's alternative (with the "commit" for
  3d.4; implemented in 3d.4b): an allowlist of infra's own accounts instead
  of a denylist of the platform's UID range
- **Date:** 2026-10-10, decided 2026-10-11
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

1. **Option (b), as an allowlist.** Only infra's own clients of the stores
   reach them directly (`pinka_telemetry_clients` in group_vars): root
   (doctor, the health gate, administrators through sudo), the gateway
   collector, Prometheus, Loki, Tempo, Grafana and the query proxy. Each
   container runs as an infra account of its own (`pinka-otelcol`,
   `pinka-prometheus`, `pinka-loki`, `pinka-tempo`, `pinka-grafana`,
   `pinka-query-proxy`), so the allowlist names no image's default user
   (`nobody`, 10001, 472), which other host processes may share; Incus's
   dnsmasq, for one, can run as `nobody` and answers untrusted targets.
2. **The telemetry guard.** Its own nftables table `inet
   pinka_telemetry_guard`, loaded by `pinka-telemetry-guard.service` on every
   firewall backend, holds the UIDs of those accounts in the set
   `telemetry_clients` (resolved from the host's accounts when the firewall
   role runs) and resets every other account's TCP connection on the host
   loopback to Prometheus, Loki and Tempo (HTTP, gRPC, Tempo's OTLP
   receiver), the collector's metrics exporter and the query proxy's
   loopback port. Researchers, the platform's services and everyone else
   query through the proxy (researchers with Grafana, the platform's
   services with its socket); the collector's OTLP receivers stay open, so
   everyone still sends telemetry. A test loads the rendered rule in a
   private network namespace and connects as one UID per account class.
3. **The query proxy** is an nginx container (the pinned nginx release of the
   golden images) in the observability project. It listens on a socket that
   `/run/pinka/query.sock` links to (0660, group `pinka-query`) and on a
   loopback port for Grafana, whose Prometheus, Loki and Tempo data sources
   go through it. Its allowlist of methods and paths was checked against the
   routes of the pinned releases; everything else is answered with 403.
   Doctor checks the link's target and that its directory belongs to
   `pinka-query-proxy` (owner's condition for the link).
4. **The agent socket.** `pinka-otlp-agent.socket` (`/run/pinka/otlp/agent.sock`)
   starts systemd's socket proxy, which forwards OTLP/HTTP to the collector's
   receiver `otlp/agent` on a loopback port of its own. Its pipelines stamp
   every resource with `pinka.trust=untrusted`, overwriting what the sender
   set; Loki indexes `pinka.trust` as a label.
5. **The platform's UID range** (60578-61183) stays: the deployment tool
   still gives platform accounts UIDs from it, between the host users mapped
   into containers (60514-60577) and the dynamic service users
   (61184-65519), but the guard no longer depends on it.

## Consequences

- The isolation does not depend on the stores' own features, and the same
  allowlist idea extends to the inference API in plan step 3d.5 (§7.11
  option b).
- Researchers no longer reach Prometheus, Loki or Tempo directly, also not
  through an SSH forward (sshd forwards as the researcher's account); they
  query through Grafana. The proxy's socket stays for the profiles that
  query (§7.9).
- An infra component that needs the stores directly needs an account on the
  allowlist; adding one is a change to `pinka_telemetry_clients`.
- The socket path is a link: the proxy's container cannot create a socket
  directly in `/run/pinka`, which belongs to root.
- The isolation probe of the reference platform (§7.18, plan step 3d.6)
  proves it on a host; doctor checks the guard on every host.
