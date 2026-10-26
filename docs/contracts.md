---
title: Integration contract between infra and a platform (contract_version 2)
type: reference
audience: [developer, operator]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: The versioned interface through which a research platform plugs into the lab, with the manifest, the privilege profiles, the files infra writes for the platform and every address, path and port.
---

# Integration contract (contract_version 2)

This page is the interface between **pokus-infrastructure** (the lab host)
and a **platform** (the research services that run on it). A platform pins
an infra release and plugs in through one file, its manifest; infra turns
the manifest into accounts, units, sockets, containers and grants, and
provides every item below. No infra code names a particular platform. The
Pokus Platform (`potka`) is the first platform; the reference platform
`refplat` (`tests/reference-platform/`) shows that a second one fits without
any infra change. What belongs to one platform (its services, the endpoints
on its sockets, its agent's rules, its attestation format and its official
check) is in that platform's own documentation.

**What a release publishes.** Infra publishes this page with every release,
together with the directory `contract/`: the manifest's JSON Schema
(`platform-manifest.schema.json`), the profile definitions (`profiles.yml`),
the reserved names (`reserved-names.yml`), the schemas of the principals and
endpoints files (`principals.schema.json`, `endpoints.schema.json`) and, in
`contract/records/`, the schemas of the provenance, lineage and session
records and of the fingerprint document. A platform repository that keeps a
copy of this page keeps it byte-identical to the copy of the infra release
it pins.

**Changing the contract** means: bump `contract_version`, change this page
and `contract/` in the same commit, describe the change in the changelog,
and let platforms adapt before they pin the new release. A platform never
works around a missing item; it requests a contract change instead. The
changes against contract v1 are listed in
[Changes from contract v1](#changes-from-contract-v1); the resolutions in
[Resolutions](#resolutions) apply, and each is cited by its ID (for example
V2).

## The manifest

Every platform release carries `platform-manifest.yaml` at its root. Infra
validates it against `contract/platform-manifest.schema.json` (JSON Schema
draft 2020-12; unknown fields are errors) and refuses the release before
anything on the host changes: `pinka-platform validate` exits with 65, and
bootstrap ends with exit code 65. Every field and every check is in the
generated [Platform manifest](reference/platform-manifest.md) reference;
the reserved names are listed there too.

| Field | Required | Content |
| --- | --- | --- |
| `contract_version` | yes | `2`, the contract version of the installed infra |
| `name` | yes | platform name, 2–12 lowercase letters and digits; not a reserved platform name |
| `version` | yes | SemVer of the release |
| `runtime` | yes | `python-uv` (needs `uv.lock` at the release root) or `static` |
| `requires` | no | infra facilities the platform uses: `postgres`, `incus`, `telemetry`, `inference`, `opa`, `gpu`; the health gate checks exactly these, and using an undeclared one is refused (V2) |
| `services[]` | yes | `name`, `profile` (any profile except `gpu-trainer`), `start`, and optionally `after`, `state`, `config`, `secrets`, `limits`, `relaxations`; an `untrusted-agent` service also names its own `package` (V6). Exactly one `controller`, at most one `inference-broker` |
| `jobs[]` | no | GPU training jobs with profile `gpu-trainer`, an image pinned by `@sha256:` digest, `command`, `inputs`, `outputs` (one adapter), `resources`, `timeout_seconds`, `resumable` |
| `sockets[]` | no | `name`, serving `service`, `clients`, `untrusted_clients` ([Sockets](#sockets)) |
| `hooks` | `health` yes | `migrate`, `reconcile`, `drain`, `health` ([Hooks](#hooks)) |
| `backup` | no | state directories to back up, and whether the platform database is backed up |
| `content[]` | no | content packages deployed from their own sources: `name`, `restricted`, `required` |
| `docs`, `policy`, `dashboards`, `tools`, `cli`, `telemetry.labels` | no | documentation directory, OPA bundle and decision path, Grafana dashboards, optional pinned tools (`quarto`), the researchers' CLI, up to four Loki labels |

Every path in the manifest is relative and normalized; after extraction,
infra also refuses symbolic links that leave the release, device files,
FIFOs, sockets and setuid or setgid bits. The only relaxation is
`wx-memory`, never for `untrusted-agent`.

**Sources in one repository.** A content package may live in the same
repository as the release. The release path and every content package of
one repository are disjoint subtrees: one may not be a prefix of another by
path segments, and an empty path (the repository root) overlaps every path
(V1). Otherwise a restricted package could be extracted as part of the
release.

**Reserved names.** The schema carries one list per namespace, generated
from infra's own data (`contract/reserved-names.yml`), so a name infra
starts using is reserved automatically. The validator derives every name
the manifest would create (accounts and groups, units, PostgreSQL roles and
databases, path segments, the CLI command) and refuses one that is reserved
or already exists on the host and does not belong to this platform.

## Privilege profiles

Each service or job names exactly one of eight profiles, and its privileges
are exactly that profile's grants. A manifest adds only its own state
directories, secrets, limits within infra's caps and the one relaxation.
`contract/profiles.yml` is the single source of the grants; the generated
[Platform privilege profiles](reference/platform-profiles.md) page lists
every flag, socket rule and cap.

| Profile | In short |
| --- | --- |
| `controller` | the one service that drives the platform: own database role (the `restricted` schema allowed), Incus for `bench` and `build`, run networks through the run-network helper, telemetry send and query, the inference API, the only client of the GPU arbiter, restricted data (read), the signing key |
| `inference-broker` | own database role, never `restricted`; telemetry send; the inference API; the only TCP listener a platform may have, the researcher endpoint on `127.0.0.1:18705` |
| `sandbox-operator` | own database role, never `restricted`; Incus for `bench`; telemetry send and query |
| `restricted-operator` | own database role, `restricted` allowed; Incus for `bench`; telemetry send; restricted data (read) |
| `restricted-reader` | own database role, `restricted` allowed; telemetry send and query; restricted data (read); optional tools |
| `observer` | own database role, never `restricted`; telemetry send and query (dashboards, report exporters) |
| `untrusted-agent` | no database role, no Incus, no inference API; telemetry only through the agent OTLP socket; no network at all (`PrivateNetwork=yes`); sees only its own package and environment, its state, its non-secret configuration and its dedicated sockets |
| `gpu-trainer` | jobs only: no database, Incus, telemetry or network; the GPU through CDI only while the arbiter trains this job; mounts only the base model and declared datasets (read-only) and its output and checkpoints |

"Never" means infra refuses the manifest and also revokes the grant at run
time; "none" means the profile has no such access to begin with. Every
service gets the hardening baseline of the build prompt (§7.14), which a
manifest cannot override; `untrusted-agent` gets the additions listed there,
and a `gpu-trainer` container gets options fixed by the GPU arbiter. Why
the profiles are cut this way is explained in
[Privilege profiles and restricted data](explanation/privilege-profiles.md).

From the manifest, infra renders:

| Item | Name |
| --- | --- |
| Account of a service or job | `<platform>-<member>`, a system account with a UID in infra's platform range (60578–61183) |
| Account of the `migrate` hook | `<platform>-migrate` |
| Service unit | `<platform>-<service>.service`, in `pinka-platform.target`, which requires `pinka-health-gate.service` |
| Socket unit and its client group | `<platform>-<service>-<socket>.socket`, group `<platform>-<service>-<socket>` |
| Reconcile unit | `<platform>-reconcile.service` (oneshot, before the services), when the manifest has a `reconcile` hook |
| Database socket group | `<platform>-db` |
| Runtime directory | `/run/pinka/platform-runtime/<service>/`, which holds no sockets (V4) |

## Sockets, principals and endpoints

A platform's services talk over Unix sockets that infra creates and
permissions from the manifest; the only TCP listener a platform may have is
the inference broker's researcher endpoint. Infra refuses any manifest that
breaks the connection rules below.

### Socket rendering and connection rules {#sockets}

Each socket is `/run/pinka/platform/<service>/<socket>.sock`, owned by the
serving account, mode 0660, group its client group, which holds the client
service accounts. Researchers and admins get POSIX ACL entries for the
groups `pinka` and `pinka-admin`. Without ACL support on `/run`, a socket
that humans may use becomes 0666 and the platform's peer-credential check
alone separates the clients; the fallback is recorded and doctor warns
(`sockets.acl_fallback`). A dedicated agent socket has no human clients and
keeps 0660. Root can always connect, for the hooks.
Services take their sockets from systemd (socket activation, the socket's
name in `LISTEN_FDNAMES`) and never create sockets themselves (V4).

| Served by | May be used by | Never by |
| --- | --- | --- |
| `controller` | every service profile; researchers, admins, root; `untrusted-agent` only on a dedicated socket | — |
| `inference-broker` | `controller`, researchers, root; `untrusted-agent` only on a dedicated socket | `restricted-*` |
| `sandbox-operator` | `controller`, root; `untrusted-agent` only on a dedicated socket | researchers, `restricted-*` |
| `restricted-operator` | `controller`, root | everyone else, above all `untrusted-agent` |
| `restricted-reader` | `controller`, root | everyone else, above all `untrusted-agent` |
| `observer` | every service profile except `untrusted-agent`; researchers, admins, root | `untrusted-agent` |
| `untrusted-agent` | serves no sockets | — |
| `gpu-trainer` | serves and uses no sockets | — |

**Dedicated agent sockets.** A socket marked `untrusted_clients: true` lists
`untrusted-agent` as its only client, whichever profile serves it
(`controller`, `inference-broker` or `sandbox-operator`). The validator
refuses the flag together with any other client and refuses
`untrusted-agent` on any socket without it. No endpoint on such a socket may
return restricted data: the platform guarantees that and proves it with a
test; infra makes these sockets visible in the installer's review and in
doctor. Doctor also lists the sockets the running agent can see in its own
mount namespace; anything beyond its allowlist fails the check.

### Principals and endpoints files

Infra writes two files at every deployment, 0644 root, and the platform
reads names, UIDs, paths, addresses and ports from them instead of
hard-coding any:

- `/etc/pinka/platform/principals.json`: every service and job with its
  account and UID, every socket with its path, owner, group, systemd name
  and allowed clients, and the groups of the human classes. The platform
  builds its per-endpoint peer allowlists from it.
- `/etc/pinka/platform/endpoints.toml`: the lab facts (`machine`,
  `lab_mode`, `node_role`) and one table per facility the manifest
  `requires` (PostgreSQL, telemetry, inference, OPA, Incus, GPU), plus the
  signing key, the attestation directories, the session records, the
  fingerprint command and the effective ports.

Their fields, schemas and examples are in the generated
[Principals and endpoints files](reference/platform-files.md) reference.

## Restricted data

Restricted data is a generic class infra provides to every platform (the
Pokus Platform's ground truth is one case): a filesystem area, a group and a
database schema. `untrusted-agent` and `gpu-trainer` never reach any of
them, whatever the manifest says.

| Part | Value |
| --- | --- |
| Area | `/srv/pinka/restricted/<platform>/`, root:`pinka-restricted` 0750, files 0640; restricted content packages under `content/<package>/<name>/` |
| Group | `pinka-restricted`; its members are exactly the accounts of `controller`, `restricted-operator` and `restricted-reader` services |
| Database schema | `restricted` in the platform's database, owned by the migrator role |
| Content | the packages the manifest declares `restricted: true` |

Enforced regardless of the manifest:

- An `untrusted-agent` or `gpu-trainer` account is never a member of
  `pinka-restricted`, of the database socket group or of `pinka-query`; the
  rendering refuses such a membership.
- The restricted area, every restricted content directory and the database
  socket directories are outside the agent's allowlist and are never
  mounted into a training container.
- No socket served by a `restricted-*` profile has a client other than the
  controller and root.
- After every migration, infra revokes all privileges on the schema
  `restricted` from every role outside the three restricted-capable
  profiles, and from the reader and Grafana roles; a grant that a migration
  made to a forbidden role fails the deployment, and the release does not
  start.
- Restricted content is extracted only into the restricted area; doctor
  fails if a restricted package's provenance record appears anywhere else.
- Backups of the restricted area and of the database are written root-only.
- Data derived from restricted data reaches a training job only as a
  published dataset that passed the split guard.

The platform guarantees that endpoints on a dedicated agent socket never
return restricted data and that the dataset provenance it writes is
complete and truthful; infra checks the provenance but cannot verify it
against the restricted data.

## PostgreSQL

| Item | Value |
| --- | --- |
| Listeners | Unix sockets only; no TCP listener at all (the container has no network) |
| Database | one per platform, named after it |
| Login roles | `<platform>_<service>` for every service except `untrusted-agent` |
| Group roles | `<platform>_<profile>` (for example `<platform>_controller`), so migrations can grant per profile |
| Migrator | `<platform>_migrator`, owner of the schemas `public` and `restricted`, used only by the `migrate` hook |
| Privileges | infra gives the service roles none on the schemas; the `migrate` hook grants its own services what they use |
| Reader and Grafana roles | `<platform>_reader` (researchers) and `<platform>_grafana`; never `restricted` |
| Service socket directory | `/run/pinka/postgres/`, group `<platform>-db`: only services with a role; the agent never |
| Reader socket directory | `/run/pinka/postgres-reader/`, group `pinka`: the researchers' reader role and Grafana; the reader's password file `/etc/pinka/postgres-reader/pgpass` is 0640 root:`pinka` |
| Password files | `/etc/pinka/secrets/postgres/<role>`; the migrator's readable only by `<platform>-migrate` |
| Infra's own role | `pinka_monitor` (member of `pg_monitor`) for the metrics exporter; never used by a platform |

## Incus

| Item | Value |
| --- | --- |
| API | HTTPS on `127.0.0.1:8443`, restricted TLS clients and metrics only |
| Credentials | one restricted client certificate per service whose profile has Incus access, `/etc/pinka/incus/<platform>-<service>/{client.crt,client.key,server.crt}` (0600, owned by its account): `controller` for `bench` and `build`, `sandbox-operator` and `restricted-operator` for `bench` |
| Projects | `bench` (official runs), `build` (image builds), personal per-user projects |
| Run networks | named `r-<short>` (12 lowercase letters and digits, opaque, no scenario information), created and deleted by the controller only through the run-network helper `/run/pinka/run-network.sock`, with fixed settings: no NAT, no uplink, ACL `pinka-run-default` |
| Network ACL | `pinka-run-default`: deny all egress except within the run network and to the VM collector listener |
| Firewall compatibility | infra treats bridges matching `r-*` and `pinka-build0` as Incus bridges |
| Profiles | `target-container`, `target-vm` (only with `vm_targets`), `probe-client`, `build` |
| CPU sets | with `cpuset_targets` set, `endpoints.toml` carries `[incus] cpuset_targets` (a Linux cpuset list); the platform sets every target's `limits.cpu` to a CPU range inside it, never a plain count, and refuses measurement campaigns without it; the `bench` project's `limits.cpu` is the number of those CPUs; infra's services run on `cpuset_services` (P3a) |
| Guest API | target profiles turn it off (`security.guestapi=false`), so instance configuration cannot be read from inside |
| Events | `pinka-incus-events.service` ships Incus lifecycle and logging events to Loki for the platform's integrity checks |
| Golden images | aliases `<blueprint>/<version>` with properties `pinka.recipe_commit`, `pinka.built_at`, `pinka.base_fingerprint` and Incus's `requirements.nesting`; the platform launches an image with `security.nesting=true` exactly when it carries `requirements.nesting=true` |

A restricted Incus certificate has full access inside its projects, so a
sandbox operator can do in `bench` whatever the controller can; narrowing
that needs Incus's fine-grained authorization, which is out of scope.

## Telemetry

| Item | Value |
| --- | --- |
| OTLP for services | `127.0.0.1:4317` (gRPC) and `127.0.0.1:4318` (HTTP), for every profile that sends except `untrusted-agent` |
| Agent OTLP | `/run/pinka/otlp/agent.sock` only; everything received there is stamped `pinka.trust=untrusted` |
| Query | Prometheus, Loki and Tempo only through the read-only query proxy `/run/pinka/query.sock`, for the members of `pinka-query` (the profiles that query): query endpoints only; push, remote write, delete, admin and configuration requests are refused. The stores are reachable directly only by infra's UID allowlist (ADR-0016) |
| Containers | the platform attaches a proxy device to each target container: `listen=tcp:127.0.0.1:4317` (inside) → `connect=tcp:127.0.0.1:4317` (host), `bind=instance` |
| VMs | Incus supports proxy devices on VMs only in NAT mode, which needs `bind=host`, so VMs send OTLP to the collector listener on `vm_collector_address` (default `198.18.0.1`, on the dummy interface `pinka-otlp0`), ports 4317 and 4318; the run ACL allows egress to exactly that address and those ports, and the host firewall accepts nothing else from run networks (C4) |
| Identity | the platform sets `OTEL_RESOURCE_ATTRIBUTES` with its own run attributes in each target's environment at launch; the in-image agent honours it. Run identifiers are opaque; no scenario identifier ever enters an instance |
| Loki labels | at most four resource attributes from the manifest's `telemetry.labels` |
| Dashboards | the manifest's `dashboards`, provisioned with a read-only datasource on `<platform>_grafana` through the reader socket directory and the telemetry datasources through the query proxy |

## Policy engine (OPA)

`http://127.0.0.1:8181`. OPA loads the policy bundle the manifest's
`policy` names, read-only from the published release, next to infra's
placeholder (`data.pinka.placeholder.active`, which doctor queries); a
bundle may not declare packages or data below `system` or `pinka`. OPA runs
with authorization enabled and a `system.authz` policy that allows only
decision queries (`GET`/`POST`) at exactly `/v1/data/<decision_path>` of
the manifest and at `/v1/data/pinka/placeholder/active`, and health checks;
every policy or data write, the policy listing and every other API answer
401 (C6). `endpoints.toml` carries the URL and the decision path.

## Inference and the GPU {#inference}

| Item | Value |
| --- | --- |
| Inference API | `local`: vLLM's OpenAI-compatible API on `127.0.0.1:18704`. `remote` (`node_role: lab`): infra's inference forwarder `pinka-inference-forwarder` on the lab node's loopback, which alone holds the mutual-TLS client certificate (`/etc/pinka/inference/`, root only) and dials the paired inference node; it offers the API on the same loopback port as vLLM and the control API on `127.0.0.1:18710`, so platform services handle no TLS and no keys. `scripted`: any OpenAI-compatible endpoint configured for the host, only with `lab_mode: staging`; every run using it is `official=false` (C5, D3) |
| Who reaches it | only the accounts of profiles with the inference API (`controller`, `inference-broker`), by a UID allowlist in nftables; on a lab node only the controller reaches the control port |
| Deployments | specs `/etc/pinka/vllm/deployments/<id>@<version>.yml` with `id` and `version` matching the file name; adapters named in `adapter_ids` as `<name>@<sha256>` |
| Switching | only through the GPU arbiter's `serve <id>@<version>` request, never by restarting units |
| Remote control API | through the forwarder: exactly `GET /health`, `GET /model`, `GET /metrics`, `POST /activate`, `POST /verify`; `POST /activate` reaches the inference node's arbiter as `serve <id>@<version>` |
| GPU arbiter | `/run/pinka/gpu.sock`, for the controller only: `serve`, `train`, `idle`, `status`, `publish-dataset` ([GPU arbiter requests](reference/gpu-arbiter.md)). One mode at a time: serving, training or idle; training runs on full nodes only |
| Training jobs | containers with options fixed by the arbiter (no network, read-only root file system, a writable `/tmp` only); a job reads `/model`, `/datasets/<kind>` and `PINKA_JOB_*`, writes checkpoints to `/checkpoints` and its adapter to `/output/adapter/` ([How to run a training job](how-to/run-training-job.md)) |
| Datasets | published from the controller's state to `/srv/pinka/training/datasets/<sha256>/` with `PROVENANCE.json`; refused at publication and at mount when they list an instance of the `test` split |
| Adapters | registered read-only under `/srv/pinka/models/adapters/<name>/<sha256>/` with `MANIFEST.json` and `LINEAGE.json` |
| Models | `/srv/pinka/models/<hf_repo>/<revision>/` with `MANIFEST.json`, weights 0444; the platform imports manifests read-only |

The record formats are in the generated
[Platform records](reference/platform-records.md) reference; the design is
in [The GPU arbiter](explanation/gpu-arbiter.md).

## Hooks

Infra drives the platform only through four hooks and the paths in its
manifest; `pinka`, doctor, the health gate and backups name no platform
service, socket or endpoint. A hook is a command inside the release. Infra
runs it as a transient systemd unit (`pinka-hook@`) under a fixed account
with that profile's hardening, in the release directory, with a timeout
(SIGTERM, then SIGKILL). The hook prints one JSON line on stdout; everything
else goes to the journal. Exit code 0 with `"ok": true` is success.

| Hook | When infra runs it | Account | Output | On failure |
| --- | --- | --- | --- | --- |
| `migrate` | at deployment, after the build, before the new release starts | `<platform>-migrate` (only the migrator password and the database socket) | `{"ok": true}` | the deployment stops and the platform stays stopped; the restricted-schema check runs after success |
| `reconcile` | at every start, before the services (`<platform>-reconcile.service`) | the controller's account | `{"ok": true}` | the services do not start; doctor reports it |
| `drain` | `pinka stop`, `pinka update-host`, deployment of a new release, restore; argument `--timeout <seconds>` | the controller's account | `{"drained": true, "active": 0}` | infra stops the services after the timeout; the platform records what was interrupted |
| `health` | after start until healthy or timeout; `pinka status`; doctor | the controller's account | `{"ok": true, "detail": {…}}` | the start reports "unhealthy"; doctor warns |

## Paths

| Item | Path | Owner, mode |
| --- | --- | --- |
| Release | `/srv/pinka/platforms/<platform>/releases/<name>/`, `current` a link to the active one; `<name>` is the release tag, or the full commit | root, 0755/0644 |
| Content package | `/srv/pinka/platforms/<platform>/content/<package>/<name>/`; restricted ones only in `/srv/pinka/restricted/<platform>/content/<package>/<name>/`; `current` links | root, 0755/0644; restricted root:`pinka-restricted` 0750/0640 |
| State | `/srv/pinka/platforms/<platform>/state/<service>/<directory>/` | the service's account, 0700 |
| Configuration | `/etc/pinka/platform/config/<service>.json`: the service's values from `platform_settings` in host_vars, checked against the schema its `config` names | root:`<account>` 0640 |
| Principals and endpoints | `/etc/pinka/platform/principals.json`, `/etc/pinka/platform/endpoints.toml` | root 0644 |
| Active platform | `/etc/pinka/platform/active.json` | root 0644 |
| Secrets | `/etc/pinka/secrets/<platform>-<service>/<secret>`, one file per secret the manifest declares, from the vault (`vault_platform_secrets`; the installer generates missing ones with `secrets.token_urlsafe`) | directory root:`<account>` 0750, files 0640 |
| Socket directories | `/run/pinka/platform/<service>/` (created by the socket units) | root 0755 |
| Runtime directories | `/run/pinka/platform-runtime/<service>/` | the service's account |
| Deployment records | `/var/lib/pinka/platforms/<platform>/` (deployments, `retired.json`) | root 0700 |
| Signing key | `/etc/pinka/signing/<platform>/signing.key` and `signing.pub` | key 0400 the controller's account |
| Published signing keys | `/srv/pinka/shared/signing-keys/<platform>-<machine>-<YYYYMMDD>.pub`, kept forever | root 0644 |
| Session records | `/var/lib/pinka/sessions/<boot_id>.json` | root 0644 |
| Fingerprint cache | `/var/lib/pinka/fingerprint/current.json` | root 0644 |
| Host profile, bootstrap state | `/etc/pinka/host-profile.json`, `/etc/pinka/bootstrap-state.json` | root 0644 |
| QA trusted keys, attestations | `/etc/pinka/qa/trusted-keys/*.pem`, `/etc/pinka/qa/attestations/` | root-owned, readable by the active platform's controller |
| Researchers' CLI | `/usr/local/bin/<cli>` → `/usr/local/lib/pinka/platform/cli/<cli>` | root 0755 |

The generated layout table in [Architecture](architecture.md) lists every
directory infra creates below `/srv/pinka`, `/etc/pinka`, `/var/lib/pinka`
and `/run/pinka`.

## Provenance, records and keys

| Item | Value |
| --- | --- |
| Infra provenance | `/etc/pinka/bootstrap-state.json`: `repo_commit`, `infra_tree` (the tree hash of the repository), `ref`, `infra_dirty`, `infra_source` (`git` or `package`), `release_tag` and the `result` of the run |
| Platform provenance | `DEPLOYED_FROM.json` in the release and in each content package: source, commit, tree, tag, platform name, version, runtime and the manifest's SHA-256 (B2) |
| Active and retired platforms | `active.json`, and `retired.json` per replaced platform |
| Session records | written once per boot by `pinka-session-start.service`, read by the controller |
| Fingerprint | `pinka fingerprint --json`, callable by the controller; run by any account but root it prints the cached root-computed fingerprint with its `computed_at` time and never escalates (C9c) |
| Signing key | one per platform, ed25519, PEM PKCS#8, generated on the host, never in a repository; the public key is PEM SubjectPublicKeyInfo; key ID: the lowercase hex SHA-256 of the DER-encoded public key (C9d) |

The JSON Schemas of these records are in `contract/records/` and in the
generated [Platform records](reference/platform-records.md) and
[Fingerprint](reference/fingerprint.md) references. A restore deploys
exactly the recorded commit and tree; through the recorded tag only with
signed tags required.

## External verification hooks (D1, D2)

`lab_mode` is `production` or `staging`. Staging hosts have their own
vault, `/srv/pinka`, Tailscale identity (`tag:pinka-qa`) and signing key,
are never paired with a production host and never write to one; on
production's hardware, staging runs only inside a maintenance window (P3d).
`scripted` inference runs only on staging.

Infra provides the directories `/etc/pinka/qa/trusted-keys/` (PEM public
keys) and `/etc/pinka/qa/attestations/` (attestations and their detached
signatures), readable by the active platform's controller, and the
commits and tree hashes an attestation is checked against: infra's in
`/etc/pinka/bootstrap-state.json`, the release's and the content packages'
in their `DEPLOYED_FROM.json`. The attestation format and the official
check belong to the platform. For the Pokus Platform, a release candidate
is an infra release, a platform release and its content package (the
catalog): the same triple as with contract v1.

## Ports

Every TCP listener of the lab binds to `127.0.0.1` unless this section says
otherwise; nothing listens on a public interface except sshd (firewalled).
Each port can be overridden per machine in host_vars (`ports:`); infra
writes the effective values into `endpoints.toml`, and the platform reads
them from there, never from these defaults (P3b). Preflight reports a
configured port that a process outside the lab already uses. Exporters and
internal health endpoints have no contract port; PostgreSQL has no TCP
listener at all.

| Default port | Listener | Bound to |
| --- | --- | --- |
| 4317, 4318 | OpenTelemetry Collector, OTLP gRPC and HTTP | `127.0.0.1` |
| 8181 | OPA: decision path and health only | `127.0.0.1` |
| 8443 | Incus HTTPS API: restricted TLS clients, metrics | `127.0.0.1` |
| 18700 | Grafana | `127.0.0.1`; optionally `tailscale serve` |
| 18701, 18702, 18703 | Prometheus, Loki and Tempo | `127.0.0.1`, reachable only by the collector, the query proxy and infra's UID allowlist |
| 18704 | vLLM OpenAI-compatible API | `127.0.0.1` |
| 18705 | the inference broker's researcher endpoint | `127.0.0.1` |
| 18706 | docs service (`pinka docs serve`, P3c) | `127.0.0.1`; optionally `tailscale serve` |
| 18704, 18710 | lab node: the inference forwarder's API and control API | `127.0.0.1` |

**Non-loopback block.** Each of these listeners is bound to one specific
address and firewalled; none is public.

| Default port | Listener | Bound to |
| --- | --- | --- |
| 18704, 18710 | inference control endpoint (`node_role: inference`): mutual TLS, reachable only by the paired peer; 18704 forwards the vLLM API, 18710 is the control API; pairing uses 18710 while the endpoint is stopped | the inference node's tailnet address |
| 4317 | OTLP gRPC for the paired inference node's telemetry, mutual TLS (`node_role: lab`) | the lab node's tailnet address |
| 4317, 4318 | collector listener for VM targets (`vm_targets`), reachable from run networks only | `vm_collector_address` (default `198.18.0.1`) on `pinka-otlp0` |

## Changes from contract v1

| Contract v1 | Contract v2 |
| --- | --- |
| one platform's services, accounts, sockets and units listed in the contract | any platform plugs in through its manifest; infra renders accounts, units, sockets and grants from eight fixed profiles; no infra code names a platform |
| platform and catalog deployed as fixed components | a release plus the content packages its manifest declares, from root-only bare mirrors; restricted packages only into the restricted area |
| a fixed socket and ACL table | sockets from the manifest, with the connection rules and `principals.json` |
| `endpoints.toml` with fixed tables | `endpoints.toml` with one table per required facility and the effective ports |
| ground-truth group and schema of one platform | the generic restricted area, group `pinka-restricted` and schema `restricted` |
| deployment switching through the deploy helper socket (C3) | through the GPU arbiter's `serve` request; adapters in `adapter_ids` |
| no training | training jobs as infra-run containers, datasets with provenance, adapters with lineage |
| telemetry query APIs on loopback | the read-only query proxy; agent OTLP socket |
| attestation format and official check in this contract | the platform's |
| one signing key for the lab | one signing key per platform |

## Resolutions

This contract relies on the following resolutions.

| ID | Resolution | Status in contract v2 |
| --- | --- | --- |
| B2 | Deploy from a root-only bare mirror with `git archive` of one subtree; `DEPLOYED_FROM.json`; no `.git` in deployed paths | in force, for the release and every content package |
| B3 | Bootstrap records the repository commit and the `infra/` tree hash; the dirty check covers `infra/` only | ended with the move to the repository root (3f): the tree hash and the dirty check cover the whole repository; a release package records the same from `release/RELEASE.json` |
| B4 | Component tag prefixes (`infra-v`; a platform's prefixes are its sources' `tag_prefix`) | ended with the move to the repository root (3f): this repository's release tags are `vX.Y.Z`; a platform's tags still follow its sources' `tag_prefix` |
| C2, C7, C8 | one platform's socket set, short names, socket groups with ACLs and peercred | replaced by the manifest's sockets, the connection rules and `principals.json` |
| C3 | deployment switching through a socket-activated root helper | replaced by the GPU arbiter |
| C4 | telemetry for VM targets through a dedicated collector listener | in force |
| C5, D3 | inference modes `local`, `remote`, `scripted`; scripted never official | in force; `remote` through the inference forwarder |
| C6 | OPA on 8181 with `system.authz` allowing only decision queries and health | in force, with the manifest's decision path |
| C9a | lab facts in `endpoints.toml` `[lab]` | in force |
| C9b | the platform's settings models are the source of truth for its configuration | in force: the manifest names the schema of each service's configuration |
| C9c | non-root `pinka fingerprint --json` prints the cached fingerprint | in force |
| C9d | signing key format and key ID | in force, one key per platform |
| C9e | deployment specs `<id>@<version>.yml`; model and adapter manifests imported read-only | in force; adapters are registered by the GPU arbiter |
| C9f | schemas owned by the migrator; grants from migrations | in force, run through the `migrate` hook |
| C9g | a link to the platform's CLI in `/usr/local/bin` | replaced by the manifest's `cli` |
| C9h | health gate and QA paths in this contract | in force; the health gate checks the manifest's `requires` |
| C10 | the port table, loopback only, no common default ports | in force, extended by P3b |
| D1 | `lab_mode` semantics | in force |
| D2 | attestation format and official check | moved to the platform |
| E3 | sources supplied by the deployer; SSH deploy key or anonymous HTTPS; pinned host keys | in force, for the release and every content package |
| E13 | separate reader socket directory and group-readable reader credential | in force |
| P3a | `cpuset_targets` in `endpoints.toml`; targets get CPU ranges, never counts | in force |
| P3b | ports overridable in host_vars, effective values in `endpoints.toml`, the non-loopback block | in force |
| P3c | the docs service on 18706 | in force |
| P3d | maintenance windows for staging on production's hardware | in force |
| V1 | the release and the content packages of one repository are disjoint subtrees | in force |
| V2 | a manifest declares every facility it uses in `requires` | in force |
| V3 | a `python-uv` release is built from locked wheels, its backend in a `build` dependency group | in force |
| V4 | services take their sockets from systemd; runtime directories hold no sockets | in force |
| V6 | the agent names its own package and runs from its own environment | in force |
| V7 | GPU inference needs compute capability 7.5 or newer | in force |
