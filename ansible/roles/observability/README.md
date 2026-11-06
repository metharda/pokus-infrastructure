# observability

Telemetry, metrics, logs, traces and dashboards of the lab
(`docs/contracts.md#telemetry`), in the `observability` Compose project.
Every container uses the host network and binds `127.0.0.1` (ports in
`contracts.md#ports` and `pinka_internal_ports`), so Prometheus scrapes the
Incus API and the exporters on loopback and nothing listens elsewhere:

- **OpenTelemetry Collector gateway**: OTLP on `127.0.0.1:4317/4318`
  (services, targets through their proxy devices) and, with `vm_targets`, on
  the VM listener address (C4); `memory_limiter` → `resource` (`pinka.machine`,
  `pinka.lab_mode`) → `batch`; logs to Loki's native OTLP endpoint, traces to
  Tempo, metrics to a Prometheus exporter that Prometheus scrapes, with
  resource attributes (such as a platform's run identifiers) as labels.
- **Journald collector**: a second collector instance that reads the host
  journal, the persistent and the volatile one, with the host's `journalctl`
  and forwards it to the gateway; separate so that the instance that accepts
  OTLP from targets has no access to the host's journal. It runs as
  `pinka-otel-journald`, an account of its own (the gateway's is
  `pinka-otelcol`), with the host's `systemd-journal` group as its only group,
  without capabilities. Besides its configuration and its state directory,
  it sees of the host only, read-only: the two journal directories, the
  machine ID, the host's `/usr/bin`, `/usr/lib`, `/lib` and `lib64`
  directories (`journalctl` and the libraries it loads) and
  `/etc/ld.so.cache`. A directory the host lacks is mounted from an empty
  directory of pinka's, so Docker never creates one; one that appears later
  is read after the next bootstrap.
- **Prometheus** (retention `observability_prometheus_retention`, and by size
  `observability_prometheus_retention_size`), scraping
  the collectors, node_exporter, cAdvisor, the Postgres exporter, Loki,
  Tempo, Grafana, Incus (metrics-only certificate) and, where the lab uses the
  GPU, the GPU exporter and, with `gpu_inference`, vLLM.
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
  `/var/lib/pinka/node-exporter`, as `pinka-node-exporter`), **cAdvisor**
  and, where the lab uses the GPU, the nvidia-smi based **GPU exporter** (as
  `pinka-gpu-exporter`; Compose profile
  `gpu-cdi` or `gpu-runtime`, after the method in `/etc/docker/daemon.json`).
  The DCGM exporter is refused while its image is unverified.
- **cAdvisor runs as root**: its image sets no user, and it reads container
  metadata through Docker's and containerd's sockets and the files of
  Docker's data root, most of them root's. In its view of the host, empty
  `tmpfs` mounts mask the lab's root, `/etc/pinka`, `/var/lib` (with
  `/var/lib/pinka` and `/var/lib/pinka-kept`) and `/run/pinka`, at their real
  paths, except Docker's data root, which stays visible: cAdvisor reads every
  container's layer ID there and measures its writable layer and log
  directory. The masks hide these paths only: a link below them, another
  mount of the same files or a copy elsewhere on the host stays visible. A
  mask needs its mount point, so `/run/pinka` exists on every node, also at
  boot (`/etc/tmpfiles.d/pinka-observability.conf`).
- **VM listener (C4)**: with `vm_targets`, `pinka-vm-otlp-address.service`
  puts `198.18.0.1/32` on the dummy interface `pinka-otlp0` before Docker
  starts; the gateway also listens there, the run ACL and the firewall admit
  only OTLP from run networks.

## Variables

See `meta/argument_specs.yml`; derived values are in `vars/main.yml`.

## Feature flags

`gpu_inference` or `gpu_training` after the switches (the GPU exporter),
`gpu_inference` (vLLM scrape job on `full` and `inference` nodes),
`vm_targets` (VM listener).

## Idempotency

Configuration files are templated; a change recreates exactly the services
of that configuration directory (configuration hash). Grafana passwords are
only reset when logging in with the vault's password fails. The Grafana role
of a researcher is set when the account is created
(`observability_grafana_user_role`).
