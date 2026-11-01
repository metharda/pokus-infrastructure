---
title: "ADR-0011: OpenTelemetry as the single telemetry entry point"
type: explanation
audience: [developer, reader]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
---

# ADR-0011: OpenTelemetry as the single telemetry entry point

- **Status:** accepted; who may query the stores on the host network is
  limited as [ADR-0016](0016-telemetry-query-isolation.md) records
- **Date:** 2026-10-06
- **Deciders:** pokus-infrastructure maintainers

## Context

Telemetry comes from the platform's services, from target containers and VMs
that an agent may break or compromise, from vLLM and from the host itself.
Every record of a run must be findable by its run ID across logs, metrics and
traces, without the target learning anything about the scenario. The stores
are Loki, Prometheus and Tempo, shown in Grafana.

## Decision

1. Every source sends **OTLP** to one OpenTelemetry Collector gateway on
   `127.0.0.1:4317`/`4318`: services directly, containers through an Incus
   proxy device, VMs through a dedicated listener on a dummy-interface
   address that only run networks reach, and only on the OTLP ports (C4).
2. The gateway adds `pinka.machine` and `pinka.lab_mode`, then sends logs to
   Loki's native OTLP endpoint, traces to Tempo, and metrics to a Prometheus
   exporter that Prometheus scrapes; resource attributes such as
   `potka.run_id` become labels or structured metadata.
3. The host journal is read by a **second collector instance** with the
   host's `journalctl` in a chroot. Only that instance can read host files;
   the gateway, which accepts data from targets, cannot.
4. The observability containers use the **host network and bind
   `127.0.0.1`**. Prometheus can then scrape the Incus API (loopback only)
   with its metrics certificate and node_exporter sees the host's interfaces.
   Every listener is set explicitly; Loki and Tempo keep their rings in memory
   and open no cluster port; tests check every listen address.

## Consequences

- One protocol and one place to add attributes, filter or limit memory.
- Loki needs `allow_structured_metadata` and the TSDB v13 schema; run
  filters are structured-metadata filters, not stream labels, so many runs do
  not create many streams.
- On the host network, a service with a listener nobody configured would be
  reachable on every interface; the configuration tests and doctor's
  listening-socket check guard against that.
- The VM listener is a deliberate non-loopback listener; the run ACL and the
  firewall restrict it to run networks and the OTLP ports.

## Alternatives considered

- **Prometheus remote write or Prometheus's own OTLP receiver** for metrics:
  fewer moving parts, but the scraped exporter keeps Prometheus pulling and
  the gateway stateless.
- **Bridge networks with published loopback ports**: Prometheus could not
  reach the Incus API and node_exporter would report the container's network
  interfaces; a helper that copies Incus metrics into node_exporter's textfile
  directory would replace Prometheus's own TLS scrape.
- **One collector with the journald receiver**: simpler, but the instance that
  accepts OTLP from targets would then hold the host's root file system.
