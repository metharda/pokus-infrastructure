---
title: Integration contract between infra and platform (contract_version 1)
type: reference
audience: [developer, operator]
applies_to: {infra: ">=0.1", platform: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
---

# Integration contract (contract_version 1)

This page is the single, resolved interface between **pokus-infrastructure** (host
provisioning) and **pokus-platform** (research services). Infra provides every
item below; the platform consumes exactly these items and nothing else. The
platform contains no distribution-specific code: everything host-specific
reaches it through this contract.

An identical copy of this file lives in `infra/docs/contracts.md` and
`platform/docs/contracts.md`; CI fails if the two copies differ. The build
prompts' contract tables (infra BUILD_PROMPT §12, platform BUILD_PROMPT §3) are
the origin of this contract; where they differed or left gaps, the resolutions
in [Resolutions](#resolutions) apply and are cited by ID (for example C2).

**Changing the contract** means: bump `contract_version`, change both copies in
the same commit, describe the change in both changelogs, and adapt both
components. A component never works around a missing item; it requests a
contract change instead.

## Accounts, groups and services

Short names (`<svc>`) are used in every path below (resolution C7).

| `<svc>` | Console script | systemd unit | Account | Supplementary groups |
| --- | --- | --- | --- | --- |
| `orch` | `potka-orchestrator` | `potka-orchestrator.service` | `potka-orch` | `potka-svc`, `pinka-gt`, `potka-exec-clients` |
| `orch` | `potka-reconcile` | `potka-reconcile.service` (oneshot, `Before=potka-orchestrator.service`) | `potka-orch` | as above |
| `agent` | `potka-agent-runner` | `potka-agent-runner.service` | `potka-agent` | `potka-svc`, `potka-exec-clients`, `potka-gw-clients` |
| `gw` | `potka-llm-gateway` | `potka-llm-gateway.service` | `potka-gw` | `potka-svc` |
| `exec` | `potka-executor` | `potka-executor.service` | `potka-exec` | `potka-svc` |
| `inject` | `potka-injector` | `potka-injector.service` | `potka-inject` | `potka-svc`, `pinka-gt` |
| `verify` | `potka-verifier` | `potka-verifier.service` | `potka-verify` | `potka-svc`, `pinka-gt` |
| `eval` | `potka-evaluator` | `potka-evaluator.service` (worker polling evaluation jobs) | `potka-eval` | `potka-svc`, `pinka-gt` |
| — | `potka-migrate` | none; infra runs it as root at deploy time (C9f) | root | — |
| — | `potka` (CLI) | none; researchers run it | the researcher | `pinka` |

| Group | Members |
| --- | --- |
| `pinka` | all researchers |
| `pinka-admin` | admin researchers; "admin only" endpoints require membership (owner answer F8) |
| `pinka-gt` | `potka-orch`, `potka-inject`, `potka-verify`, `potka-eval` |
| `potka-svc` | all service accounts |
| `potka-exec-clients` | `potka-agent`, `potka-orch` |
| `potka-gw-clients` | `potka-agent` |

Infra renders all units with the hardening baseline of infra BUILD_PROMPT §7.14.
Platform services start only after `pinka-health-gate.service` succeeds
(platform §3; infra §7.16).

`potka-agent-runner.service` reaches exactly three sockets: the gateway's
`agent.sock`, the executor's `api.sock` and the orchestrator's `svc.sock`
(resolution C2). Its `InaccessiblePaths=` contains `/srv/pinka/catalog`,
`/srv/pinka/artifacts`, `/srv/pinka/services`, `/etc/pinka/secrets`,
`/etc/pinka/incus`, `/var/lib/incus`, `/run/docker.sock`, `/var/lib/pinka/git`,
`/run/pinka/postgres`, `/run/pinka/postgres-reader`, `/run/pinka/inject`,
`/run/pinka/verify`, `/run/pinka/vllm-deploy.sock`,
`/run/pinka/orch/api.sock` and `/run/pinka/gw/svc.sock`.

## Deployment of platform and catalog

Platform and catalog are deployed from the deployer's copy of the pokus-platform
repository (`pinka_source_repo`, supplied at deploy time) through a root-only
bare mirror, never from a working tree (resolutions B2, E3):

| Item | Value |
| --- | --- |
| Bare mirror | `/var/lib/pinka/git/potka.git` (root:root, directory 0700). Tag and signature checks run here. |
| Platform deployment | `git archive <commit> platform/` extracted without the `platform/` prefix into `/srv/pinka/deploy/platform/<ref>/` (root:root 0755); `uv sync --frozen` creates `<ref>/.venv`; `current` is an atomically replaced symlink to `<ref>`. |
| Catalog deployment | `git archive <commit> catalog/` extracted without the `catalog/` prefix into `/srv/pinka/catalog/<ref>/` (root:pinka-gt 0750, files 0640); `current` symlink as above. |
| `<ref>` directory name | the tag name when the ref is a component tag (`platform-v0.1.0`), otherwise the full 40-hex commit |
| Never | a `.git` directory in any deployed path; catalog content anywhere outside `/srv/pinka/catalog` (doctor check and test) |
| Provenance | `DEPLOYED_FROM.json` in every deployment directory (0644 root), schema below |
| Infra provenance | `/etc/pinka/bootstrap-state.json` (0644 root) with `repo_commit` and `infra_tree` (tree hash of `infra/` at that commit) and `infra_dirty` (resolution B3) |

`DEPLOYED_FROM.json`:

```json
{
  "schema_version": 1,
  "component": "platform",
  "source_repo": "git@github.com:example-org/pokus-platform.git",
  "requested_ref": "platform-v0.1.0",
  "tag": "platform-v0.1.0",
  "commit": "0000000000000000000000000000000000000000",
  "subdirectory": "platform",
  "tree": "0000000000000000000000000000000000000000",
  "signed_tag": {"required": false, "verified": false, "signer": null},
  "deployed_at": "2026-10-05T09:12:44Z"
}
```

`tag` is non-null only if the commit carries a tag with the component's prefix
(`platform-v…`, `catalog-v…`; resolution B4). "Deployed from a tag" in the
platform's official check means exactly that. `tree` is
`git rev-parse <commit>:<subdirectory>`.

## Paths

| Item | Path | Owner, mode |
| --- | --- | --- |
| Platform install | `/srv/pinka/deploy/platform/current` | root:root 0755 |
| Catalog | `/srv/pinka/catalog/current` | root:pinka-gt 0750 |
| Non-secret config | `/etc/pinka/platform/<svc>.toml`, `/etc/pinka/platform/endpoints.toml` | root:root 0644 |
| Secrets | `/etc/pinka/secrets/<svc>/` | directory 0750 root:`<account>`, files 0640 |
| Postgres passwords | `/etc/pinka/secrets/postgres/<role>` | directory 0750 root:potka-svc; file 0640 root:`<account of that role>`; `potka_migrator` 0600 root |
| Researcher reader credential | `/etc/pinka/postgres-reader/pgpass` | 0640 root:pinka (resolution E13) |
| Incus client certificates | `/etc/pinka/incus/<svc>/{client.crt,client.key,server.crt}` for `orch`, `exec`, `inject`, `verify` | 0600 owned by the service account |
| Session records | `/var/lib/pinka/sessions/<boot_id>.json` | 0644 root |
| Fingerprint cache | `/var/lib/pinka/fingerprint/current.json` | 0644 root (C9c) |
| Host profile | `/etc/pinka/host-profile.json` | 0644 root |
| Signing key | `/etc/pinka/signing/manifest-signing.key` | 0400 potka-orch (C9d) |
| Signing public keys | `/srv/pinka/shared/signing-keys/<machine>-<YYYYMMDD>.pub` | 0644, kept forever |
| Artifact store | `/srv/pinka/artifacts` | potka-orch:pinka 0750 |
| Reports | `/srv/pinka/reports` | potka-eval:pinka 2775; Quarto CLI pinned by infra |
| Model weights | `/srv/pinka/models/<hf_repo>/<revision>/` with `MANIFEST.json` | potka-orch:pinka, weights 0444 |
| LoRA adapters | `/srv/pinka/models/adapters/<name>/<sha256>/` with `MANIFEST.json` | as model weights (C9e) |
| vLLM deployment specs | `/etc/pinka/vllm/deployments/<id>@<version>.yml` | 0644 root |
| Inference mTLS material | `/etc/pinka/inference/` | 0750 root:potka-orch |
| QA trusted keys | `/etc/pinka/qa/trusted-keys/*.pem` | 0644 root |
| QA attestations | `/etc/pinka/qa/attestations/*.json` and `*.json.sig` | root-owned, readable by potka-orch |
| CLI on PATH | `/usr/local/bin/potka` → `/srv/pinka/deploy/platform/current/.venv/bin/potka` (C9g) | root:root 0755 |

## Sockets

HTTP over Unix domain sockets. Socket permissions are the first gate; the
service's `SO_PEERCRED` allowlist per endpoint is the second (resolution C8).
Where two principals need a socket, it gets a group plus a POSIX ACL entry. If
the host's `/run` does not support POSIX ACLs, the fallback column applies and
peercred alone separates the principals. Doctor verifies effective permissions.

| Socket | Owner:group, mode | ACL | Fallback without ACLs | Peers (peercred) |
| --- | --- | --- | --- | --- |
| `/run/pinka/orch/api.sock` | potka-orch:pinka 0660 | `g:potka-svc:rw` | potka-orch:pinka 0660 | researchers (`pinka`), admin endpoints `pinka-admin` only, root (`pinka stop` drain) |
| `/run/pinka/orch/svc.sock` | potka-orch:potka-svc 0660 | — | — | `potka-exec`, `potka-gw`, `potka-inject`, `potka-verify` per endpoint; `potka-agent` only on the agent-session endpoints (C2) |
| `/run/pinka/exec/api.sock` | potka-exec:potka-exec-clients 0660 | — | — | `potka-agent` (tool calls with a session token), `potka-orch` (targets, evidence profile) |
| `/run/pinka/gw/agent.sock` | potka-gw:potka-gw-clients 0660 | — | — | `potka-agent` |
| `/run/pinka/gw/svc.sock` | potka-gw:potka-gw 0600 | `u:potka-orch:rw` | potka-gw:potka-svc 0660 | `potka-orch` |
| `/run/pinka/inject/api.sock` | potka-inject:potka-inject 0600 | `u:potka-orch:rw` | potka-inject:pinka-gt 0660 | `potka-orch` |
| `/run/pinka/verify/api.sock` | potka-verify:potka-verify 0600 | `u:potka-orch:rw` | potka-verify:pinka-gt 0660 | `potka-orch` |
| `/run/pinka/vllm-deploy.sock` | potka-orch:potka-orch 0600, systemd socket unit | — | — | `potka-orch` (C3) |
| `/run/pinka/postgres/` | Postgres socket directory for service roles, 0750 999:potka-svc | — | — | members of `potka-svc` with a password file; infra's metrics exporter (`pinka_monitor`) |
| `/run/pinka/postgres-reader/` | Postgres socket directory, 0750 999:pinka | — | — | researchers as `potka_reader`; Grafana as `potka_grafana`. `pg_hba` cannot tell the two directories apart: the directory group decides who reaches a socket, the password files decide the role |

Runtime directories `/run/pinka/<svc>/` are created by `RuntimeDirectory=` with
mode 0755; the socket modes above do the gating.

## Configuration

The platform's pydantic settings models are the source of truth for every TOML
file; the platform exports them as JSON Schemas (`platform/schemas/config/`) and
infra renders files that conform (resolution C9b). Secrets are read from files,
never from command-line arguments.

`/etc/pinka/platform/endpoints.toml` carries the host facts the platform needs:

```toml
[lab]
machine_name = "pinka-lab"
lab_mode = "production"          # production | staging (D1)
node_role = "full"               # full | lab | inference

[telemetry]
otlp_grpc = "127.0.0.1:4317"
otlp_http = "127.0.0.1:4318"

[query]
prometheus_url = "http://127.0.0.1:18701"
loki_url = "http://127.0.0.1:18702"
tempo_url = "http://127.0.0.1:18703"

[opa]
url = "http://127.0.0.1:8181"
decision_path = "/v1/data/potka/action/decision"

[incus]
url = "https://127.0.0.1:8443"
cpuset_targets = "6-19"          # only when host_vars sets cpuset_targets

[inference]
mode = "local"                   # local | remote | scripted (C5, D3)

[inference.local]
api_url = "http://127.0.0.1:18704"
deploy_helper_socket = "/run/pinka/vllm-deploy.sock"

[inference.remote]
api_url = "https://<inference-node>:18704"
control_url = "https://<inference-node>:18710"
tls_dir = "/etc/pinka/inference"

[inference.scripted]
api_url = "http://127.0.0.1:<port>"

[gateway]
adhoc_listen = "127.0.0.1:18705"
```

Only the table for the active inference mode is present. `<inference-node>` is
the paired node's tailnet name; `<port>` is chosen by the deployer.

## PostgreSQL

| Item | Value |
| --- | --- |
| Listeners | Unix sockets only (`/run/pinka/postgres/`, `/run/pinka/postgres-reader/`); no TCP listener at all (the container has no network) |
| Database | `potka` |
| Roles | `potka_migrator`, `potka_orch`, `potka_gw`, `potka_exec`, `potka_inject`, `potka_verify`, `potka_eval`, `potka_reader`, `potka_grafana` |
| Infra-internal role | `pinka_monitor` (member of `pg_monitor`) for infra's metrics exporter; its password is generated on the host; the platform never uses it |
| Schemas | `public` and `gt`, both owned by `potka_migrator`; infra revokes all privileges on `gt` from `PUBLIC`, `potka_reader` and `potka_grafana` |
| Grants | schema `USAGE` and table privileges come from platform migrations (C9f) |
| Migrations | infra runs `potka-migrate upgrade` as root from the deployed environment at deploy time; it connects as `potka_migrator` with the password file above |
| Reader access | `potka_reader` can never see the `gt` schema (tested by both components) |

## Incus

| Item | Value |
| --- | --- |
| API | HTTPS on `127.0.0.1:8443`, restricted TLS clients |
| Projects | `bench` for all service certificates; `orch` also `build` |
| Per-run networks | named `r-<short>`, where `<short>` is 12 lowercase characters derived by the platform from the run's ULID (opaque, no scenario information); no NAT, no uplink |
| Network ACL | `pinka-run-default`: deny all egress except within the run network |
| Firewall compatibility | infra treats bridges matching `r-*` and `pinka-build0` as Incus bridges |
| Profiles | `target-container`, `target-vm` (only if `vm_targets`), `probe-client`, `build` |
| CPU sets | with `cpuset_targets` in `endpoints.toml` (a Linux cpuset list), the platform sets every bench instance's `limits.cpu` to a range of CPUs inside it, never a plain count; the `bench` project's `limits.cpu` is the number of those CPUs. Lab services run on the other CPUs (`cpuset_services`) |
| Guest API | target profiles disable it (`security.guestapi=false`, the Incus 6.0 key for `/dev/incus`) |
| Golden images | aliases `<blueprint>/<version>` with properties `pinka.recipe_commit`, `pinka.built_at`, `pinka.base_fingerprint`; the Incus property `requirements.nesting` is `true` for blueprints that need nesting (a container engine inside the target), which the platform then launches with `security.nesting=true` |

## Telemetry

| Item | Value |
| --- | --- |
| OTLP for services | `127.0.0.1:4317` (gRPC), `127.0.0.1:4318` (HTTP) |
| Containers | the platform adds a proxy device to each target instance: `listen=tcp:127.0.0.1:4317` (inside) → `connect=tcp:127.0.0.1:4317` (host), `bind=instance` |
| VMs | Incus 6.0 supports proxy devices on VMs only in NAT mode, which requires `bind=host`, so a VM cannot reach the collector through a proxy device. With `vm_targets`, VMs send OTLP to the dedicated collector listener `198.18.0.1:4317` (gRPC) / `:4318` (HTTP), an address on the dummy interface `pinka-otlp0` (`pinka_vm_otlp`); the run ACL `pinka-run-default` has one egress rule for exactly that address and those ports, and the host firewall accepts nothing else from run networks (C4). The platform sets the VM's OTLP endpoint accordingly |
| Identity | the platform sets `OTEL_RESOURCE_ATTRIBUTES=potka.run_id=<run_id>,potka.node=<node_alias>` in the instance environment at launch; the in-image agent attaches them. `run_id` is an opaque ULID. No scenario identifier ever enters an instance. |

## Policy engine (OPA)

`http://127.0.0.1:8181`, with the bundle loaded from
`/srv/pinka/deploy/platform/current/policy/` (a deny-all placeholder bundle when
the platform is disabled). OPA runs with authorization enabled and a
`system.authz` policy that allows only decision queries (`GET`/`POST` on
exactly `/v1/data/potka/action/decision` and
`/v1/data/pinka/placeholder/active`) and health checks, and denies every policy and data write
(resolution C6). Doctor and a test verify that writes are rejected.

## Inference

The orchestrator and the gateway use one `InferenceBackend` interface with
three kinds (C5, D3):

| Mode | Endpoint | Deployment switching |
| --- | --- | --- |
| `local` | vLLM OpenAI-compatible API on `127.0.0.1:18704` | through the deploy helper below |
| `remote` (`node_role: lab`) | API and inference control endpoint of a paired inference node over the tailnet, mutual TLS with material in `/etc/pinka/inference/` | the control endpoint's activate call |
| `scripted` | any OpenAI-compatible endpoint configured in `endpoints.toml` | none; accepted only when `lab_mode = "staging"`; every run using it is `official=false` |

**Deploy helper (C3).** `pinka-vllm-deploy.socket` (systemd, `SocketUser=potka-orch`,
`SocketMode=0600`) activates the root-owned `/usr/local/libexec/pinka/vllm-deploy`.
The helper checks the peer is `potka-orch`, reads one JSON request line
`{"deployment": "<id>@<version>"}`, validates that the ID matches
`^[a-z0-9][a-z0-9-]{0,62}@[0-9]{1,6}$` and names a regular, root-owned file in
`/etc/pinka/vllm/deployments/`, points `current.yml` at it, renders
`/etc/pinka/vllm/current.env` (with `current.yaml`, the `vllm serve --config`
file it renders next to it), restarts `pinka-inference.service`, and answers
one JSON line `{"ok": true}` or `{"ok": false, "error": "<text>"}`. The
orchestrator then waits for vLLM health itself. No sudo is used anywhere.

**Deployment spec** `/etc/pinka/vllm/deployments/<id>@<version>.yml` (C9e):

```yaml
id: dep-example-awq-ada12g
version: 2
model_id: <model id from models.lock.yml>
adapters: []            # optional: "<name>@<sha256>" entries under /srv/pinka/models/adapters/
profile: ada-12g
overrides: {}           # max_model_len, quantization, max_num_seqs, enable_lora, ...
sampling_defaults: {}
```

The platform imports deployment specs and every model and adapter
`MANIFEST.json` read-only; infra's models role verifies their hashes. There is
no training or adapter-creation flow.

## Session records and fingerprint

| Item | Value |
| --- | --- |
| Session records | `/var/lib/pinka/sessions/<boot_id>.json`, written once per boot by `pinka-session-start.service`; the orchestrator imports them. JSON object: `schema_version` (1), `boot_id`, `started_at` (UTC, ISO 8601 with `Z`), `fingerprint_hash`, `fingerprint` (the output of `pinka fingerprint --json`), `preflight_outcome`, and `errors` (field name to reason; a field that could not be computed is `null`) |
| Fingerprint | `pinka fingerprint --json`, callable by `potka-orch`. Run as root it computes the fingerprint and refreshes the cache; run as any other user it prints the cached root-computed fingerprint from `/var/lib/pinka/fingerprint/current.json` with its `computed_at` time and never escalates (C9c). |
| Fingerprint document | JSON object `fingerprint_schema_version` (1), `hash` (`sha256:<hex>` of the canonical JSON of `fingerprint`: sorted keys, no whitespace, UTF-8), `fingerprint` (the hashed fields), `computed_at` and `boot_id` (volatile, not hashed); JSON Schema `infra/tools/pinka-lab/schema/fingerprint.schema.json` |
| Lab drain | `pinka stop` and `pinka update-host`, as root, send `POST /lab/drain` with `{"timeout_seconds": <int>}` to `/run/pinka/orch/api.sock`; the orchestrator answers 200 or 202, starts no new runs and lets active runs continue. `GET /lab/status` answers `{"active_runs": <int>}`; `pinka` polls it until 0 or the timeout, then stops `pinka-lab.target`. Runs still active then are recorded as interrupted. |

## Run-manifest signing (C9d)

| Item | Value |
| --- | --- |
| Private key | ed25519, PEM PKCS#8, `/etc/pinka/signing/manifest-signing.key`, 0400 potka-orch, generated on the host, never in the repository |
| Public key | PEM SubjectPublicKeyInfo, published as `/srv/pinka/shared/signing-keys/<machine>-<YYYYMMDD>.pub` and kept forever |
| Key ID | lowercase hex SHA-256 of the DER-encoded public key; recorded in every manifest |

## External verification hooks (D1, D2)

`lab_mode` is `production` or `staging`. Staging hosts use their own vault,
`/srv/pinka`, Tailscale identity (`tag:pinka-qa`) and signing key, are never
paired with a production host and never write to one.

An attestation is a JSON file in `/etc/pinka/qa/attestations/`, canonicalized
with RFC 8785 (JCS), with a detached ed25519 signature over the canonical bytes
in `<file>.sig` (base64). Trusted public keys are PEM files in
`/etc/pinka/qa/trusted-keys/`. The platform owns the JSON Schema
(`platform/schemas/`).

```json
{
  "schema_version": 1,
  "attestation_id": "<ULID>",
  "rc": {"infra": "<commit>", "platform": "<commit>", "catalog": "<commit>"},
  "tree_hashes": {"infra": "<tree>", "platform": "<tree>", "catalog": "<tree>"},
  "verification_run_id": "<string>",
  "verifier_commit": "<string>",
  "suites": ["<string>"],
  "verdict": "pass",
  "issued_at": "2026-10-05T09:12:44Z",
  "key_id": "<sha256 of the public key>"
}
```

`verdict` is `pass` or `fail`. The platform's official check requires verdict
`pass`, a trusted key whose key ID matches, a valid signature, and `rc` commits
and `tree_hashes` equal to the platform's and catalog's `DEPLOYED_FROM.json` and
to the infra commit and tree recorded in `/etc/pinka/bootstrap-state.json`.

## Ports

Every listener binds to `127.0.0.1` unless stated otherwise; nothing listens on
a public interface except sshd (firewalled). Port numbers avoid common default
ports (resolution C10). **Status: proposed, awaiting owner approval.**

| Port | Listener | Fixed by |
| --- | --- | --- |
| 4317, 4318 | OpenTelemetry Collector OTLP gRPC / HTTP | contract (spec) |
| 8181 | OPA | contract (spec) |
| 8443 | Incus HTTPS API | contract (spec) |
| 18700 | Grafana (optionally exposed on the tailnet through `tailscale serve`) | C10 |
| 18701 | Prometheus HTTP API | C10 |
| 18702 | Loki HTTP API | C10 |
| 18703 | Tempo HTTP API | C10 |
| 18704 | vLLM OpenAI-compatible API (`local`); on an inference node bound to `tailscale0` with mutual TLS | C10 |
| 18705 | LLM gateway ad-hoc endpoint (researchers, via SSH port forwarding) | C10 |
| 18706 | `pinka docs serve` | C10, owner answer H9 |
| 18710 | inference control endpoint, bound to `tailscale0`, mutual TLS (`node_role: inference`) | C10 |
| 4317, 4318 on `198.18.0.1` | collector listener for VM targets (`vm_targets` only), reachable from run networks only | C4 |

Exporters (node, cAdvisor, Postgres, GPU) and the collector's own metrics are
reachable only on the internal Docker network or `127.0.0.1` without a fixed
contract port. PostgreSQL has no TCP listener.

## Resolutions

| ID | Resolution | Reconciles |
| --- | --- | --- |
| B2 | Deploy platform and catalog with `git archive` of one subtree from a root-only bare mirror; `DEPLOYED_FROM.json`; no `.git` in deployed paths | infra §7.14, §12; platform §2.1, §4.1.2 |
| B3 | Bootstrap records the repository commit and the `infra/` tree hash; dirty check limited to `infra/` | infra §5.2 step 1, §10.2 |
| B4 | Component tag prefixes `infra-v`, `platform-v`, `catalog-v`, `docs-v` | platform §4.1.2, §8 |
| C2 | Agent runner reaches gateway `agent.sock`, executor `api.sock` and orchestrator `svc.sock`; orchestrator peercred limits it to agent-session endpoints | infra §7.14; platform §4.3 |
| C3 | vLLM deployment switching through a socket-activated root helper; no sudo | infra §7.14, §12; platform §3, §4.1 |
| C4 | Telemetry alternative for VM targets | infra §12; platform §3 |
| C5 | Inference modes `local`, `remote`, `scripted`; `endpoints.toml`; `/etc/pinka/inference/`; deployment specs on both nodes | platform §3; infra §19.10 |
| C6 | OPA on `127.0.0.1:8181` with `system.authz` allowing only decision queries and health | platform §3; infra §7.10 |
| C7 | `<svc>` short names | infra §7.7, §7.14, §12; platform §3 |
| C8 | Socket groups, POSIX ACLs, fallback and peercred allowlists | platform §3, §4; infra §12 |
| C9a | `lab_mode`, `node_role`, machine name in `endpoints.toml` `[lab]` | platform §4.1.2; infra §8.1 |
| C9b | Platform settings models are the source of truth for TOML keys | platform §3, §4 |
| C9c | Non-root `pinka fingerprint --json` prints the cached root-computed fingerprint | infra §9, §12; platform §3 |
| C9d | Signing key format, file names and key ID | infra §8.2, §12; platform §3, §12 |
| C9e | Deployment spec `<id>@<version>.yml` with `id`/`version`; model and adapter manifests imported read-only | infra §7.11, §7.12; platform §4.1, §6 |
| C9f | Schemas owned by `potka_migrator`; grants from migrations; infra runs `potka-migrate upgrade` | infra §7.8, §7.14; platform §6 |
| C9g | `/usr/local/bin/potka` link to the deployed CLI | platform §4.9 |
| C9h | Health gate and QA paths listed in this contract | infra §7.16, §13.10; platform §3 |
| C10 | Port table above, loopback only, no common default ports | infra §7.9, §7.11, §12; platform §4.4 |
| D1 | `lab_mode` semantics | infra §8.1, §13.10; platform §3 |
| D2 | Attestation format, canonicalization, signature and official check | platform §4.1.2 |
| D3 | `scripted` backend is any configured OpenAI-compatible endpoint; never official | platform §3, §4.1.2 |
| E3 | `pinka_source_repo` required, supplied by the deployer; SSH deploy key or anonymous HTTPS; pinned Git host keys | infra §7.14, §18.4 |
| E13 | Separate reader socket directory and group-readable reader credential | infra §7.8 |
