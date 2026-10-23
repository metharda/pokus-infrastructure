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
  of a denylist of the platform's UID range; Grafana moved off the stores'
  allowlist to the query port's (with the "commit" for 3d.4b; implemented in
  3d.5)
- **Date:** 2026-10-10, decided 2026-10-11 and 2026-10-12; the inference API's
  part (decision 6) proposed with plan step 3d.5
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
   collector, the stores themselves where they talk to each other
   (Prometheus scrapes Loki, Tempo and the collector; Loki and Tempo use their
   own gRPC ports) and the query proxy. Grafana is not one of them: its
   Prometheus, Loki and Tempo data sources point at the query proxy, and only
   root and Grafana reach the proxy's loopback port
   (`pinka_query_port_clients`). So no Grafana user, editor or admin
   included, reaches a push, admin or delete API through Grafana's data
   source proxy or a data source of their own. Each
   container runs as an infra account of its own (`pinka-otelcol`,
   `pinka-prometheus`, `pinka-loki`, `pinka-tempo`, `pinka-grafana`,
   `pinka-query-proxy`), so the allowlist names no image's default user
   (`nobody`, 10001, 472), which other host processes may share; Incus's
   dnsmasq, for one, can run as `nobody` and answers untrusted targets.
2. **The telemetry guard.** Its own nftables table `inet
   pinka_telemetry_guard`, loaded by `pinka-telemetry-guard.service` on every
   firewall backend, holds the UIDs of the stores' clients in the set
   `telemetry_clients` and those of the query port's in the set
   `query_port_clients` (both resolved from the host's accounts when the
   firewall role runs). It resets every other account's TCP connection on
   the host loopback to Prometheus, Loki and Tempo (HTTP, gRPC, Tempo's OTLP
   receiver) and the collector's metrics exporter, and every connection to
   the query proxy's loopback port from an account outside the second set.
   Both sets are of type `uid` and the rules match `meta skuid` only, never
   a group: Grafana's container runs with group 0, as its image expects, and
   group 0 admits nothing. Researchers, the platform's services and everyone else
   query through the proxy (researchers with Grafana, the platform's
   services with its socket); the collector's OTLP receivers stay open, so
   everyone still sends telemetry. A test loads the rendered rule in a
   private network namespace and connects as one UID per account class,
   among them Grafana and an account with GID 0 that is on neither list.
   Doctor compares both loaded sets with the accounts and both rules with
   the ports.
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

6. **The inference API (§7.11), the same way.** BUILD_PROMPT §7.11 asks for
   the mechanism in the same order of preference and records it here. Option
   (a) does not hold for the pinned vLLM (v0.31.0, its
   `vllm/entrypoints/launchers/cli_args.py` and `launcher.py`, read on
   2026-10-12): `--api-key` protects only the `/v1`, `/v2` and `/inference`
   routes, and `/invocations`, which serves the same inference, stays open;
   `--uds` exists and would put the API on a Unix socket, but Prometheus and
   the collector scrape vLLM's `/metrics` over HTTP, and on a lab node the
   inference forwarder must offer the paired node's API on the same loopback
   port as vLLM on a full node (§12.8), so the platform reads one TCP address
   from `endpoints.toml` in every topology. So the inference guard, its own
   table `inet pinka_inference_guard` loaded by
   `pinka-inference-guard.service` on every firewall backend, resets every
   account's connection to vLLM's port (on a lab node the forwarder's) on
   the host loopback except root, infra's scrapers for the node role
   (`pinka_inference_clients`) and the active platform's accounts whose
   profile has `inference_api` (the controller and the inference broker,
   `inference_clients` of `rendered.json`). On a lab node only root and the
   controller reach the forwarder's control port. The loader
   (`/usr/local/libexec/pinka/inference-guard`) resolves the UIDs each time
   it loads, and the platform role loads it again after every deployment
   and retirement. Matching is by UID only, as in decision 2. A kernel test
   connects as one UID per account class; doctor checks both sets
   (`inference.isolation`).

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
- Grafana can no longer use a data source that points at a store directly,
  also not one an admin adds by hand: the connection is reset.
- The socket path is a link: the proxy's container cannot create a socket
  directly in `/run/pinka`, which belongs to root.
- The isolation probe of the reference platform (§7.18, plan step 3d.6)
  proves it on a host; doctor checks the guard on every host.
- The platform's services other than the controller and the inference
  broker cannot bypass the broker to reach the model; a new platform's
  accounts reach the API only after its deployment reloaded the guard.
