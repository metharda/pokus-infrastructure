# compose

Helper role (included by `postgres`, `opa`, `observability` and `vllm`) for
the Compose projects of the lab services (BUILD_PROMPT §7.6, owner answer
E15):

- copies `compose/<project>/compose.yml` to `/srv/pinka/services/<project>/`
  and writes `.env` with every image pinned by digest (from
  `versions.lock.yml`), the ports of `contracts.md` and the paths; the Compose
  file refers to them as `${...}` and fails if one is missing;
- adds the Docker data root, the GIDs of the group `pinka` and of the active
  platform's database socket group `<platform>-db` (`root` without a
  platform; containers join them to reach the socket directories) and, for
  projects
  with `compose_gpu`, `COMPOSE_PROFILES` (`gpu-cdi` or `gpu-runtime`, after
  the GPU method the docker role wrote into `/etc/docker/daemon.json`);
- checks the project with `docker compose config`;
- installs `pinka-<project>.service`: `Type=oneshot`, `RemainAfterExit=yes`,
  `docker compose up --detach --wait` to start (and reload), `down` to stop,
  part of `pinka-lab.target`;
- writes one hash per configuration directory (`config/<name>/`) into
  `.env` as `<NAME>_CONFIG_HASH`; each service carries its directory's hash
  as the label `org.pinka.config-hash`, so `docker compose up` recreates
  exactly the services whose bind-mounted configuration changed (Compose
  itself does not look inside bind-mounted files);
- starts the services with `docker compose up --wait`.

Projects: `core` (PostgreSQL, OPA), `observability` (collector, Prometheus,
Loki, Tempo, Grafana, exporters) and `inference` (vLLM).

## Variables

See `meta/argument_specs.yml`.

## Feature flags

None.

## Idempotency

Files are copied and templated; `docker compose up` reports a change only
when a container or network was created, recreated or started (Compose
5.6's plain progress events `Created`, `Recreated`, `Started`), which
happens only on the first run or after a definition or configuration change.
