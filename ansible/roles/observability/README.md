# observability

Telemetry, metrics, logs, traces and dashboards of the lab (BUILD_PROMPT
§7.9, `docs/contracts.md#telemetry`), in the `observability` Compose project.
Every container uses the host network and binds `127.0.0.1` (ports in
`contracts.md#ports` and `pinka_internal_ports`), so Prometheus scrapes the
Incus API and the exporters on loopback and nothing listens elsewhere:

- **OpenTelemetry Collector gateway**: OTLP on `127.0.0.1:4317/4318`
  (services, targets through their proxy devices) and, with `vm_targets`, on
  the VM listener address (C4); `memory_limiter` → `resource` (`pinka.machine`,
  `pinka.lab_mode`) → `batch`; logs to Loki's native OTLP endpoint, traces to
  Tempo, metrics to a Prometheus exporter that Prometheus scrapes (owner
  answer E14), with resource attributes (such as a platform's run
  identifiers) as labels.
- **Journald collector**: a second collector instance that reads the host
  journal with the host's `journalctl` (chroot into the read-only host root)
  and forwards it to the gateway; separate so that the instance that accepts
  OTLP from targets has no access to the host's files.
- **Prometheus** (retention `observability_prometheus_retention`), scraping
  the collectors, node_exporter, cAdvisor, the Postgres exporter, Loki,
  Tempo, Grafana, Incus (metrics-only certificate) and, with `gpu_inference`,
  the GPU exporter and vLLM.
- **Loki** and **Tempo**, single binaries with filesystem storage under
  `/srv/pinka/state/` and retention by their compactors; rings in memory, so no
  cluster port opens.
- **Grafana** with provisioned data sources (Prometheus, Loki with trace
  links, Tempo and, when the active platform has a database, its PostgreSQL
  database on the read-only role `<platform>_grafana` through the reader
  socket directory) and the dashboards of `config/grafana/dashboards/`: host
  overview, GPU and vLLM, Incus targets and lab services health. The active
  platform's dashboards (the JSON files of the manifest's `dashboards`
  directory in its published release) are provisioned read-only into a
  folder named after the platform; run dashboards ship with the platform,
  never with infra. Anonymous access is off; the admin password
  and one account per researcher (named after `display_name`) come from the
  vault; researchers with `grafana: false` and removed researchers are
  disabled.
- **node_exporter** (host PID namespace, textfile collector directory
  `/var/lib/pinka/node-exporter`), **cAdvisor** and, with `gpu_inference`, the
  nvidia-smi based **GPU exporter** (Compose profile `gpu-cdi` or
  `gpu-runtime`, after the method in `/etc/docker/daemon.json`). The DCGM
  exporter is refused while its image is unverified.
- **VM listener (C4)**: with `vm_targets`, `pinka-vm-otlp-address.service`
  puts `198.18.0.1/32` on the dummy interface `pinka-otlp0` before Docker
  starts; the gateway also listens there, the run ACL and the firewall admit
  only OTLP from run networks.

## Variables

See `meta/argument_specs.yml`; derived values are in `vars/main.yml`.

## Feature flags

`gpu_inference` (GPU exporter; vLLM scrape job on `full` and `inference`
nodes), `vm_targets` (VM listener).

## Idempotency

Configuration files are templated; a change recreates exactly the services
of that configuration directory (configuration hash). Grafana passwords are
only reset when logging in with the vault's password fails. The Grafana role
of a researcher is set when the account is created
(`observability_grafana_user_role`).
