# Build Prompt: `pokus-platform` — Pokus Platform (potka), the Research Platform Services of the Pokus Lab

> Paste this whole file into Claude Code as the initial task, or commit it as `docs/BUILD_PROMPT.md` and tell Claude Code: "Read docs/BUILD_PROMPT.md completely and build the repository it describes."
>
> Companion document: the `pokus-infrastructure` BUILD_PROMPT (Pokus Infrastructure, technical name `pinka`: host provisioning). This repository runs **on top of** what `pokus-infrastructure` provisions and plugs into it through contract v2 (§3). "Pokus" names the project as a whole.

---

## 0. How you (Claude Code) must work on this task

These services are the scientific core of the benchmark, and the project is published for anyone to use. A benchmark result is only worth something if the platform can prove that (a) the model saw nothing but legitimate evidence, (b) every action went through policy, (c) every number in a report can be traced back to raw, sealed records. Design for **correctness, auditability and reproducibility first**, performance second.

Working rules:

1. **Read this entire document before writing code.** Then write `docs/IMPLEMENTATION_NOTES.md` with your understanding, planned packages, and open questions, and **ask me the open questions** before implementing anything that depends on them.
2. **Never invent library versions, API details or flags.** Verify against current documentation (Incus REST API, vLLM OpenAI-compatible server, OPA, OpenTelemetry, Textual if used, etc.). Unverified items are marked `TODO(verify)` and listed in the notes. `make verify-todos` fails while any remain.
3. **Do not run anything against a real Incus daemon, vLLM server, Postgres or host** unless I ask. All services must be fully testable with fakes (§17).
4. Small Conventional Commits; every commit passes `make lint test`.
5. Write `CLAUDE.md` summarizing the invariants of §2, §16 and §18 for future sessions.
6. When this document and reality disagree, follow reality, explain why in the commit, and record it in the notes.
7. **Documentation follows `docs/DOCS_SPEC.md`** (the Pokus Documentation Specification, committed next to this file). Write documentation **alongside** the code in every step, not at the end: required pages, page conventions, docstrings/argument specs, generated references, and the docs quality gates in that spec are part of every step's definition of done.
8. **Contract v2.** The platform targets a pinned `pokus-infrastructure` release and consumes the contract v2 published with it (manifest schema, profiles, principals and endpoints files); it keeps no copy of the contract. Contract changes are requested in the infrastructure repository.

---

## 1. What the platform does (one paragraph)

A researcher submits a **campaign** ("run model X with agent config Y on these 500 scenario instances, 3 repetitions each"). The platform expands it into **runs**. For each run it provisions a fresh, isolated target environment from golden images, verifies it is healthy, injects a named and versioned fault, confirms the fault really manifested, collects an evidence snapshot, lets the AI agent investigate and act **only through structured, policy-checked tools**, independently verifies the outcome and checks for collateral damage, tears everything down, and seals an immutable, signed record of everything that happened. Ground truth is joined **only later, in a separate evaluation step**, which computes accuracy, calibration, selective risk, resolution, safety and latency metrics and produces CSV/JSON/Parquet exports and HTML/PDF reports.

The platform also closes the research loop: test a model, train an adapter on `train`-split data with the lab's GPU, serve the adapter, and re-test on the same benchmark (§4.1, §8, §11).

---

## 2. Non-negotiable invariants

1. **Ground-truth blindness.** The model, the agent runner, the LLM gateway and the executor never have access to ground truth, scenario files, injection details, or the run→scenario mapping. This is enforced by OS permissions and systemd sandboxing (provided by infra) **and** by the platform's data flow: these components only ever receive what §7.3 lists.
2. **The model never executes anything directly.** Chain: model → structured tool call → schema validation → policy decision (OPA) → (approval if required) → controlled executor → target → result. No shell strings, no `sh -c`, no interpolation into command lines.
3. **Confidence alone never authorizes an action.** Diagnosis confidence and action confidence are separate fields; policy uses them as necessary, never sufficient, conditions.
4. **Least privilege per service.** Each service runs as its own account with only the credentials it needs (§4). The agent runner has no database, Incus, Docker, catalog or artifact access.
5. **Append-only, sealed records.** Once a run is sealed, nothing about it can be updated or deleted. Corrections are new annotation rows. Every run has a signed manifest.
6. **Validity ≠ outcome.** Infrastructure failures (injection didn't manifest, Incus error, timeout of our own components) make a run *invalid*; they never count against the model.
7. **Everything is versioned and recorded:** catalog version, scenario version, seed, image fingerprints, model revision and hashes, vLLM deployment, agent config and prompt hashes, tool registry version, policy bundle hash, platform version, environment fingerprint.
8. **Targets are disposable.** Every run gets fresh instances and a fresh isolated network; nothing survives a run except artifacts and records.
9. **No hidden channels.** No scenario identifiers, ground-truth hints or lab-internal labels inside target instances, tickets, tool outputs, or prompts.
10. **Crash consistency.** Every state transition is persisted *before* its side effects start, so that the `reconcile` hook (§4.2) can always clean up after a power loss (lab hosts may be workstations that are regularly shut down).
11. **No restricted data on dedicated agent sockets.** No endpoint on a socket dedicated to the agent (`untrusted_clients: true`) returns restricted data; a test proves it.
12. **Training data never contains test data.** Training datasets contain only `train`-split instances and carry complete, truthful provenance (`PROVENANCE.json`).
13. **Contamination is flagged.** Results on scenario templates that appear in an adapter's training data are flagged as contaminated (§11).

---

## 3. Integration contract v2 with `pokus-infrastructure`

**Portability rule:** the platform contains **no distro-specific code or assumptions**. It never calls a package manager, never assumes a distro path outside this contract, never shells out to distro tools, and runs only in the uv-managed Python environment infra creates. Everything host-specific reaches the platform through this contract, so the platform behaves identically on every distribution infra supports.

The platform targets a pinned `pokus-infrastructure` release and consumes its contract v2: the manifest schema, the eight privilege profiles, the principals and endpoints files, the hooks, the GPU arbiter and the training records, as published with that release. It ships one manifest, `platform-manifest.yaml`, at the root of `platform/`; infra validates it against the release's schema and renders accounts, groups, units, sockets, database roles, Incus certificates and training containers from it. If you need something contract v2 does not offer, write it into `docs/IMPLEMENTATION_NOTES.md` as a requested contract change for the infrastructure repository — do not work around it.

**The manifest.** `name: potka`, runtime `python-uv`, `requires: [postgres, incus, telemetry, inference, opa, gpu]`. The service names are the short names below, so infra derives the accounts `potka-<service>`, the units `potka-<service>.service` and the database roles `potka_<service>`.

| Service | Section | Profile | Sockets it serves (clients) | Notes |
|---|---|---|---|---|
| `orch` | §4.1 orchestrator | `controller` | `api` (researchers, admins, root); `svc` (the service profiles); `agent-session` (the agent only, `untrusted_clients: true`) | state `artifacts`; the only service that may send requests to the GPU arbiter |
| `agent` | §4.3 agent runner | `untrusted-agent` | — | uses `agent-session`, the gateway's `agent` socket and the executor's `agent` socket |
| `gw` | §4.4 LLM gateway | `inference-broker` | `agent` (the agent only, `untrusted_clients: true`); `svc` (controller) | the researcher endpoint `127.0.0.1:18705` |
| `exec` | §4.5 executor | `sandbox-operator` | `agent` (the agent only, `untrusted_clients: true`); `api` (controller) | |
| `inject` | §4.6 injector | `restricted-operator` | `api` (controller) | |
| `verify` | §4.7 verifier | `restricted-operator` | `api` (controller) | |
| `eval` | §4.8 evaluator | `restricted-reader` | — | relaxation `wx-memory`, tool `quarto`, state `reports` |

The rest of the manifest: `hooks` — `migrate` (`potka-migrate`), `reconcile` (`potka-reconcile`, §4.2), `drain` and `health` (`potka-orchestrator drain` and `health`, §4.1); `jobs[]` — the training job `lora` (profile `gpu-trainer`, its image built from `training/` in this repository and pinned by digest, `inputs: {base_model: true, datasets: [train]}`, `resumable: true`, with a CPU-only variant for tests); `content` — `catalog` (`restricted: true`, `required: true`); `backup` — `{paths: [orch/artifacts, eval/reports], database: true}`; `docs`; `policy` (the bundle in `policy/`, decision path `potka/action/decision`); `dashboards` (including the run explorer, §14); `tools: [quarto]`; `cli: {name: potka}`; `telemetry.labels: [potka.run_id, potka.node]`.

**What the platform reads from infra.** There are no fixed tables of accounts, groups, sockets, units, roles or certificates here: they come from the principals and endpoints files of the deployed infra release.

| Item | Where it comes from |
|---|---|
| Release | `/srv/pinka/platforms/potka/releases/<ref>/` with `current` (the uv environment built by infra with `uv sync --frozen`) |
| Catalog | the restricted content package `catalog`: `/srv/pinka/restricted/potka/content/catalog/<ref>/` with `current` (root:`pinka-restricted` 0750), readable only by the `controller`, `restricted-operator` and `restricted-reader` services |
| Accounts, groups, sockets | `/etc/pinka/platform/principals.json`: every service with its account and UID, every socket with its path and allowed clients, the researcher and admin groups. Peer allowlists are built from it, never from hard-coded names |
| Endpoints | `/etc/pinka/platform/endpoints.toml`, with the effective ports: the lab facts (machine name, `lab_mode`, `node_role`), the database socket directories and role names, the OTLP endpoints (with the VM collector address) and the agent OTLP socket, the read-only telemetry query proxy (Prometheus, Loki and Tempo query endpoints only; the platform never reaches them directly), the inference API, the OPA decision endpoint, the Incus address, certificate directories and target CPU set, the GPU arbiter socket, the signing-key path, the attestation directories, the session records and the fingerprint command |
| State | `/srv/pinka/platforms/potka/state/<service>/<directory>/` as declared in the manifest (`orch/artifacts`, `eval/reports`) |
| Config and secrets | `/etc/pinka/platform/<service>.toml` (non-secret, checked against the service's config schema); secrets in `/etc/pinka/secrets/potka-<service>/` |
| Postgres | database `potka`; login roles `potka_<service>`; group roles `potka_<profile>`; `potka_migrator` (owner of the schemas, used only by the `migrate` hook); `potka_reader` and `potka_grafana`; schemas `public` and `restricted`; infra revokes `restricted` after every migration from every role outside the restricted-capable profiles |
| Incus | restricted client certificates for `orch` (`bench`, `build`), `exec`, `inject` and `verify` (`bench`) |
| Incus conventions | per-run networks `r-<short>`; ACL `pinka-run-default`; profiles `target-container`, `target-vm`, `probe-client`; target profiles set the guest API off (`security.guestapi=false`, verify key name) so instance config is not readable from inside; CPU set: every target instance's `limits.cpu` is a CPU range inside `cpuset_targets`, never a plain count, and measurement campaigns are refused on a host without `cpuset_targets` (§4.1) |
| Golden images | Incus aliases `<blueprint>/<version>` with properties `pinka.recipe_commit`, `pinka.built_at`, `pinka.base_fingerprint` |
| Telemetry | OTLP gRPC/HTTP at `127.0.0.1:4317/4318` on the host for the services; the agent runner only through the agent OTLP socket. For targets, the platform adds a proxy device per instance: listen `tcp:127.0.0.1:4317` inside → connect `tcp:127.0.0.1:4317` on host, `bind=instance`; VMs send to the VM collector address from `endpoints.toml` instead. Telemetry identity: the platform sets `OTEL_RESOURCE_ATTRIBUTES=potka.run_id=<run_id>,potka.node=<node_alias>` in the instance environment at launch; the in-image OTel agent attaches them. `run_id` is an opaque ULID and carries no scenario information. |
| Inference | `endpoints.toml` declares `inference.mode`: `local`, `remote` or `scripted`. **local:** OpenAI-compatible API at `127.0.0.1:18704` (the effective port from `endpoints.toml`). **remote** (infra `node_role: lab`): API and the inference control endpoint of a paired inference node over the tailnet with mutual TLS (certificates in `/etc/pinka/inference/`). **scripted:** any OpenAI-compatible endpoint configured in `endpoints.toml` (for QA), accepted only on hosts with `lab_mode: staging`; every run using it is forced to `official=false`. The orchestrator and gateway support all three through one `InferenceBackend` interface. Deployments switch only through GPU arbiter requests (`serve`, §4.1); deployment specs `<id>@<version>.yml` live in `/etc/pinka/vllm/deployments/` on both nodes. |
| OPA | HTTP on `127.0.0.1:8181` (from `endpoints.toml`); OPA's authorization allows only the decision path `potka/action/decision` and health; infra loads the bundle from `policy/` in the release |
| GPU and training | the GPU arbiter socket `/run/pinka/gpu.sock` (orchestrator only); training jobs from `jobs[]`, run by infra; datasets published and adapters registered by the arbiter, with `PROVENANCE.json` and `LINEAGE.json` |
| Session records | `/var/lib/pinka/sessions/<boot_id>.json` |
| Fingerprint | `pinka fingerprint --json`: as the orchestrator's account it prints the cached root-computed fingerprint with its `computed_at` time |
| Signing key | `/etc/pinka/signing/potka/` (ed25519, PEM PKCS#8), readable only by the orchestrator; public keys (PEM) in `/srv/pinka/shared/signing-keys/` as `potka-<machine>-<YYYYMMDD>.pub`; key ID: the lowercase hex SHA-256 of the DER-encoded public key, recorded in every run manifest |
| Attestations | `/etc/pinka/qa/trusted-keys/` and `/etc/pinka/qa/attestations/`, readable by the orchestrator; attestations are issued by the external verification repository `pokus-qa` in the format this platform defines (§12.2) |
| Health gate | the services start only after infra's `pinka-health-gate.service` passes for the facilities `requires` lists |

---

## 4. The services

Each service section below follows the same structure. Implement every point.

Common to **all** services:

- Python 3.12, async (FastAPI + uvicorn) for long-running APIs; HTTP **over Unix domain sockets** only (no TCP), except the gateway's researcher endpoint (§4.4).
- **Caller identity** from `SO_PEERCRED` on the Unix socket (UID → service or human class, from `/etc/pinka/platform/principals.json`, which also gives every socket's path), enforced per endpoint by an allowlist. If the ASGI server does not expose the peer socket, implement a small uvicorn protocol subclass or equivalent; verify. Socket file permissions are the first gate, peercred the second.
- Structured JSON logs to stdout (journald) with `run_id`, `campaign_id`, `trace_id` when available. Never log secrets, never log ground truth outside services allowed to see it.
- OpenTelemetry tracing and metrics exported to `127.0.0.1:4317`; the agent runner exports only through the agent OTLP socket named in `endpoints.toml`. Span names and attributes per §14. Services that query telemetry (the orchestrator, the executor, the evaluator) do so only through infra's read-only query proxy named in `endpoints.toml`.
- `GET /healthz` (liveness) and `GET /readyz` (dependencies OK).
- Graceful shutdown on SIGTERM: stop accepting work, finish or checkpoint in-flight work within a timeout, exit 0.
- Config from TOML (pydantic-settings), secrets from files, never from CLI args.
- Every externally visible action emits a **CloudEvents 1.0** event (type namespace `potka.<service>.<event>`) to the orchestrator's event intake or directly into the DB (per §4 ownership).

### 4.1 `potka-orchestrator` — the control plane

**Purpose.** Owns campaigns, runs, the run state machine, scheduling, model deployments and training runs (through infra's GPU arbiter), approvals, sealing and signing. It is the only component that knows which scenario a run belongs to *and* talks to every other service.

**Runs as** the service `orch` with profile `controller` (account `potka-orch`; its groups come from the profile: `pinka-restricted`, `potka-db` and the client groups of the sockets it uses).

**Can access:** catalog (read), Postgres as `potka_orch` (incl. `restricted` schema write for the run→scenario mapping), Incus `bench` + `build`, artifact store (its state, write), signing key, the GPU arbiter socket, session records, the sockets of every other service.

**Cannot access:** model weights directory writes, Docker, host config.

**Sockets.**
- `api` (`/run/pinka/platform/orch/api.sock`) — researchers (`pinka`), admins (`pinka-admin`) and root; human-facing API.
- `svc` — the platform's service profiles; service-to-service API (event intake, approval requests).
- `agent-session` — dedicated to the agent runner (`untrusted_clients: true`); agent session reports. No endpoint on it returns restricted data (§2, invariant 11), and a test proves it.

**API (researcher-facing, via `potka` CLI):**
- `POST /campaigns` (campaign spec, §8), `GET /campaigns`, `GET /campaigns/{id}`, `POST /campaigns/{id}/cancel`, `POST /campaigns/{id}/pause|resume`
- `GET /runs?campaign=&state=&submitter=`, `GET /runs/{id}` (no GT fields), `GET /runs/{id}/events`
- `GET /approvals?state=pending`, `POST /approvals/{id}/decide` (approve/deny + comment)
- `GET /queue`, `GET /gpu` (arbiter mode, active deployment, running training)
- `POST /datasets` (build a training dataset with the split guard), `GET /datasets`, `GET /datasets/{hash}` (provenance and sample records; only callers in `pinka-admin`, each request recorded as an event)
- `POST /training-runs` (submit a training job), `GET /training-runs`, `GET /adapters`
- `GET /catalog/scenarios/{id}` (behind `potka catalog show`; only callers in `pinka-admin`, each request recorded as an event): the scenario as the catalog holds it, ground truth included
- `POST /deployments/{id}/activate` (admin only; normally automatic)
- `POST /lab/drain`, `GET /lab/status` (internal; infra uses the `drain` and `health` hooks, item 11)
- `POST /evaluations` (enqueue an evaluation job for the evaluator)

**API (service-facing, `svc`):** `POST /events` (CloudEvents from executor/injector/verifier/gateway), `POST /approval-requests` (from executor; long-poll or callback for decision). **API (`agent-session`):** `POST /agent-sessions/{sid}/steps` and `/finish` (from the agent runner).

**Responsibilities in detail:**

1. **Campaign intake and expansion.** Validate the campaign spec against the catalog (scenario IDs, versions, splits, tags), the model registry (deployment exists, model hashes verified), agent configs and the tool registry. Expand into runs: for each scenario template × parameter seed × repetition → one run with a fresh ULID. Record the exact expansion (seeds are derived deterministically from `campaign.seed` + scenario ID + instance index + repetition via HMAC-SHA256, documented).
2. **Official vs exploratory.** A campaign is `official=true` only if: platform and catalog are deployed from tags (not dirty), the agent config and policy bundle are from the deployed platform version, the submitter is in the researchers' group (`pinka`), the host is `lab_mode: production`, the inference backend is a real vLLM deployment (never `scripted`), and — when `require_qa_attestation: true` (default) — a valid **QA attestation** (§12.2) exists for the exact release candidate (infra release, platform release with its manifest, catalog content package): `/etc/pinka/qa/attestations/*.json`, issued by the external verification repository `pokus-qa` and ed25519-signed by a QA public key listed in `/etc/pinka/qa/trusted-keys/`. The check (§12.2) uses infra's provenance (the infra release tag, commit and tree in `bootstrap-state.json`; `DEPLOYED_FROM.json` of the release and of the catalog, with the manifest hash) plus the adapter lineage of the served deployment. The attestation ID is recorded on the campaign and in every run manifest. Otherwise `official=false`, and the reason is recorded. Reports exclude non-official runs unless explicitly requested.
3. **Scheduling.**
   - Queue in Postgres using `SELECT … FOR UPDATE SKIP LOCKED`.
   - **Fairness:** round-robin across submitters, then FIFO within a submitter; per-submitter max concurrent runs (config).
   - **Deployment affinity:** prefer runs whose campaign needs the currently active vLLM deployment; switch deployments only when no compatible runs remain or a campaign has waited longer than `max_wait_for_deployment`.
   - **Concurrency:** global `max_concurrent_runs` (config) and per-campaign `concurrency`. A campaign in `measurement` mode runs **exclusively** (no other run and no ad-hoc LLM traffic at the same time; the gateway is told to reject ad-hoc requests during exclusive windows). It is refused on a host without `cpuset_targets` in `endpoints.toml`: unpinned targets would share CPUs with the lab services.
4. **GPU and deployments.** The GPU belongs to infra's GPU arbiter (`/run/pinka/gpu.sock`; modes `idle`, `serving`, `training`). Every run records the inference node's fingerprint hash (`inference_fingerprint_hash`), and evaluation never compares latency across different inference placements. Switching deployment requires no active agent session and no running training. Steps: mark `switching`, send the arbiter `serve <id>@<version>`, wait for vLLM `/health`, call the gateway's verification endpoint (the same tool-call check infra performs), record a `deployment_activation` row (activation ID, deployment ID, served model name, start/end time). Gateway rejects calls for runs whose deployment ≠ active activation.
5. **Run state machine driver** (§5). Persist each transition before acting; dispatch work to injector/verifier/executor/agent runner; enforce per-phase timeouts; map failures to validity codes.
6. **Run target context.** At PROVISION, create instances and the run network; register with the executor a **target context** for the run: `{run_id, session_token, nodes: {alias → instance_name, kind, allowed_read_paths, services, patchable_paths, validators}}`. The context comes from the blueprint, which is not ground truth. Never include scenario data.
7. **Evidence bundle** (EVIDENCE phase): ask the executor to run the evidence profile (a fixed list of read-only tool calls defined in the agent config, §7.6) under caller `orchestrator`; store the bundle as an artifact; record its hash on the run.
8. **Approvals.** Receive approval requests from the executor; resolve by campaign approval mode (§9.4); store requests, decisions, decider, latency; deliver decision back.
9. **Sealing.** After TEARDOWN: assemble the run manifest (§12), compute the event hash chain head, sign with ed25519, store manifest + signature as artifacts, mark `sealed_at`. DB triggers then forbid changes.
10. **Session import.** On start, import new `/var/lib/pinka/sessions/*.json` records into `lab_sessions`; every run references the session it ran in.
11. **Drain and health.** The `drain` hook (`potka-orchestrator drain --timeout <seconds>`, called by infra) and `POST /lab/drain`: stop dequeuing, let running runs finish up to a timeout, then checkpoint (runs still active become `interrupted` on next reconcile); the hook prints `{"drained": true, "active": <n>}`. The `health` hook (`potka-orchestrator health`) prints `{"ok": …, "detail": {…}}` from the readiness checks.
12. **Integrity checks per run** (§13) before sealing; failures mark the run invalid with a reason.
13. **Training flow.** (a) The **dataset builder**: from sealed, valid runs on `train`-split instances only, build a dataset in the orchestrator's state with a complete `PROVENANCE.json` (dataset hash, every instance with its split, the source runs, the builder's commit) and ask the arbiter to `publish-dataset`; the builder refuses any `test`-split instance before infra's split guard sees it. (b) **Job submission**: `train <job> <submission>` (job, base model, dataset hashes, hyperparameters, seed), only while no run or ad-hoc session is active; every arbiter transition is recorded as an event. (c) **Lineage import**: the registered adapter's `LINEAGE.json` goes into `adapters.lineage`. (d) **Dataset inspection**: `GET /datasets/{hash}`, behind `potka datasets inspect`, returns a dataset's provenance and sample records only to callers in `pinka-admin` (peer credentials) and records each request as an event.

**Failure behavior.** Orchestrator crash → systemd restart; on start it runs a lightweight reconcile of its own in-memory state from the DB (runs in non-terminal phases owned by this boot continue only if their phase is resumable; otherwise marked `interrupted`).

**Metrics.** queue depth by state, runs by phase, phase durations, deployment switches, approval latency, invalid runs by reason.

### 4.2 Reconcile — boot-time cleanup (the `reconcile` hook)

**Runs as** the controller's account, through infra's `reconcile` hook (`potka-reconcile`), as a oneshot unit before the services. Prints one JSON line, `{"ok": true}`, on success.

1. Find runs in non-terminal states whose `session_id` ≠ current boot → mark `invalid: interrupted` with the phase they died in.
2. Delete Incus instances and `r-*` networks in project `bench` that belong to no active run (instances carry no lab metadata inside; the mapping lives in the DB).
3. Mark a half-finished deployment switch `failed`; import interrupted training runs from the arbiter's transition records (`interrupted`, and whether the arbiter resumed them).
4. Re-queue interrupted runs according to each campaign's `retry_policy` (new run IDs linked via `retry_of`; the interrupted run stays in the record).
5. Emit a reconcile report event. Idempotent; safe to run repeatedly.

### 4.3 `potka-agent-runner` — the agent loop

**Purpose.** Runs the conversation between the model and the tools for one run (an *agent session*). It is deliberately the least privileged component, because it is closest to the model.

**Runs as** the service `agent` with profile `untrusted-agent` (account `potka-agent`): no network (Unix sockets only), no groups except the client groups of its dedicated sockets.

**Can access:** the gateway's `agent` socket, the executor's `agent` socket and the orchestrator's `agent-session` socket, all dedicated to it, and the agent OTLP socket. **Nothing else** — no network, no DB, no Incus, no catalog, no artifacts, no secrets: the gateway identifies it by its peer credentials on the dedicated `agent` socket and by the session token.

**Input per session** (from orchestrator): `session_token`, `run_id`, agent config (resolved: prompt template text + hash, mode, budgets, sampling params, tool subset), ticket text, tool schemas (generated from the registry for the allowed subset), taxonomy label list (the answer space, not ground truth), evidence bundle (static mode only).

**Protocol with the model** (OpenAI-compatible chat with tools via the gateway):

- **Read-only tools** (§7) — investigate.
- **`submit_diagnosis`** (structured): `{label: enum(taxonomy), root_cause: string, affected_component: string, confidence: number 0..1, evidence_refs: [tool_call_id]}`. May be called more than once; every submission is recorded with a timestamp. The **pre-action diagnosis** is the last one before the first mutating call; the **final diagnosis** is the last one overall.
- **Mutating tools** — every call must include `_meta: {action_confidence: number 0..1, rationale: string, expected_effect: string}`; missing or invalid `_meta` → tool error returned to the model.
- **`abstain`** `{reason}` — ends the session without a diagnosis (counts toward coverage, not accuracy).
- **`finish`** `{claimed_resolved: bool, summary: string}` — ends the session.
- Mutating tools are only offered in modes that allow actions (§9.3). Policy additionally denies mutating calls before any `submit_diagnosis` exists.

**Loop rules:**

- Budgets from agent config: `max_steps`, `max_tool_calls`, `max_tokens_total`, `max_wall_s`. Exceeding a budget ends the session with `budget_exhausted:<which>` (an outcome, not an invalidity).
- Malformed tool calls (unknown tool, schema violation) are returned to the model as tool errors and counted (`invalid_tool_calls`), up to `max_invalid_tool_calls`, after which the session ends with `protocol_failure`.
- If the model replies with plain text and no tool call, the runner sends one fixed nudge message (from the prompt template) and counts it; a second plain reply ends the session with `protocol_failure`.
- Sampling parameters and seed per run come from the orchestrator (seed derived per repetition).
- Every step (model turn, tool call request, tool result as seen by the model, truncations) is reported to the orchestrator's `agent-session` socket as it happens — the runner keeps no durable state of its own.
- **Static mode:** no tools except `submit_diagnosis` and `abstain`; the evidence bundle is rendered into the first user message using a versioned template.
- **Interactive mode:** the ticket is the first user message; evidence must be gathered via tools.

**Failure behavior.** Runner crash → the session is lost; orchestrator marks the run `invalid: agent_runner_failure`. Gateway/vLLM unavailable → `invalid: inference_unavailable` (never counted against the model).

### 4.4 `potka-llm-gateway` — single door to the model

**Purpose.** All model traffic — benchmark and ad-hoc — passes through the gateway, so every token is attributable and measured, and benchmark runs can never silently hit the wrong model.

**Runs as** the service `gw` with profile `inference-broker` (account `potka-gw`). DB role `potka_gw` (insert into `llm_calls`, `llm_usage`).

**Endpoints:**
- `agent` — dedicated to the agent runner (`untrusted_clients: true`). OpenAI-compatible `POST /v1/chat/completions` with an extra required header `X-Potka-Session: <session_token>`.
- `svc` — the orchestrator only: `POST /verify-deployment`, `POST /exclusive-window` (start/stop), `GET /stats`.
- `127.0.0.1:18705` (address from `endpoints.toml`) — **researcher ad-hoc endpoint**, OpenAI-compatible, per-user API keys (issued via `potka llm key create`, stored hashed in DB). Researchers reach it through SSH port forwarding. Rejected during exclusive windows and while the GPU arbiter is training.

**Behavior:**
- Validates the session token with the orchestrator (cached), checks the run's required deployment activation equals the active one → else `409` (the run becomes `invalid: deployment_mismatch`).
- Forces the request's `model` to the served name of the run's deployment; applies the run's sampling params and seed; strips any client attempt to override them in benchmark mode.
- Requests **logprobs** (top-k configurable, default 5) for benchmark calls.
- Streams from vLLM internally to measure **TTFT**, inter-token latency and total time; returns either streamed or complete to the caller.
- Records every benchmark call in `llm_calls`: run_id, step index, deployment activation id, request (artifact ref), response (artifact ref), logprobs (artifact ref, compact binary or JSONL), prompt/completion tokens, cached tokens if reported, TTFT, total latency, finish reason, vLLM request id, trace/span ids. Ad-hoc calls go to `llm_usage` with user, tokens and timing (content not stored unless the user opts in).
- Concurrency limit to vLLM (config) and per-user ad-hoc rate limit.
- Emits GenAI semantic-convention spans (§14).

### 4.5 `potka-executor` — the only hand that touches targets during a run

**Purpose.** Turns validated tool calls into exact, whitelisted commands on the correct target node, after policy (and approval) allows it.

**Runs as** the service `exec` with profile `sandbox-operator` (account `potka-exec`). DB role `potka_exec` (insert `tool_calls`, `policy_decisions`, `executions`). Incus cert restricted to `bench`. Can query Loki and Prometheus through infra's read-only query proxy (`endpoints.toml`), never write to them.

**Cannot access:** catalog, ground truth, the run→scenario mapping.

**Sockets:** `agent` — dedicated to the agent runner (`untrusted_clients: true`): `POST /tool-calls` with a `session_token`; `api` — the orchestrator only: `POST /targets`, `DELETE /targets/{run_id}`, `POST /tool-calls` with `caller=orchestrator` (evidence profile).

**API:**
- `POST /targets` (orchestrator only): register target context for a run.
- `DELETE /targets/{run_id}` (orchestrator only).
- `POST /tool-calls` (agent runner with `session_token`, or orchestrator with `caller=orchestrator` for the evidence profile): `{tool, args, _meta?, tool_call_id}` → result.

**Pipeline for each call (all steps recorded):**
1. Resolve session → run → target context. Unknown/expired session → reject.
2. Look up the tool in the registry (version pinned by the run's agent config). Unknown → error.
3. Validate args with the tool's JSON Schema; validate `node` alias exists in the context; validate value constraints (`from: blueprint.services`, path prefix allowlists, regexes).
4. Build the **policy input** (§9.2) and query OPA. Store the decision with policy bundle hash, rule ID, and reason.
5. If `require_approval`: create an approval request at the orchestrator and wait (timeout per campaign). Denied/timeout → result `denied_by_approval` returned to the model.
6. Render the command **as an argv array** from the tool's template (no shell). Wrap with `timeout --kill-after=<k>s <t>s` inside the target.
7. Execute via the Incus exec API in non-interactive mode with output recording (verify the exact API: `POST /1.0/instances/{name}/exec` with `record-output: true`, then wait for the operation and fetch the recorded stdout/stderr logs).
8. Capture exit code, duration, stdout/stderr. Full output → artifact store; the model receives a truncated version (`max_output_bytes`, with an explicit `[truncated N bytes]` marker).
9. Run the tool's optional `post_check` command and include its result.
10. Emit an event and return a normalized result: `{status: ok|error|denied|timeout, exit_code, stdout, stderr, truncated, post_check, duration_ms, policy: {effect, reason}}`.

**Special tools:**
- `read_file`: via Incus file API, path must match an allowed prefix and not contain `..` after normalization; size cap; binary detection.
- `apply_config_patch` (transactional): pull the file → apply the unified diff in Python (reject fuzzy hunks) → push to a temp path → swap into place keeping a backup → run the path's validator (e.g. `nginx -t`) → on failure restore the backup and return the validator output → on success return the diff summary. Diff and both file versions stored as artifacts.
- `query_logs` / `query_metrics`: the executor builds the LogQL/PromQL query itself from structured args (service, node, since, level / metric name from an allowlist) and **always adds the `potka.run_id` filter server-side**; the model cannot write raw queries.
- `http_probe`: runs `curl` from the run's `probe-client` node against a node alias, never from the host.

### 4.6 `potka-injector` — reproducible faults

**Purpose.** Prepares targets and injects faults exactly as defined by scenario versions. Separate identity from the executor, so the agent's path and the fault path never mix.

**Runs as** the service `inject` with profile `restricted-operator` (account `potka-inject`, member of `pinka-restricted`). DB role `potka_inject`. Incus `bench`.

**Socket:** `api`, orchestrator only.

**API:** `POST /runs/{run_id}/setup`, `POST /runs/{run_id}/inject`, `POST /runs/{run_id}/cleanup-traces` — each with the resolved scenario instance (scenario version + parameters derived from the seed).

**Fault primitive library** (`potka-primitives`): each primitive is a versioned Python class with `name`, `version`, a pydantic params model, `apply(ctx, params)`, `describe(params)` (human text for docs), and declared `side_effects` (files touched, services affected). Implemented strictly with argv commands and Incus file APIs. Required initial set:

`file.write`, `file.mutate` (structured edits), `nginx.corrupt_conf` (defects: missing_semicolon, unclosed_block, unknown_directive, bad_upstream_host), `nginx.set_upstream` (point to dead port), `systemd.action` (start/stop/restart/disable/mask), `systemd.break_unit` (bad ExecStart), `disk.fill` (fallocate to a target percentage on a given mount, respecting quota), `disk.inode_exhaust`, `net.netem` (delay/loss on an interface), `net.block_port` (nftables inside target), `dns.break_resolver` (wrong nameserver / broken resolv.conf / blocked port 53), `dns.wrong_record` (hosts file), `docker.kill_container`, `docker.crashloop` (bad entrypoint env), `docker.stop_daemon`, `pg.stop`, `pg.exhaust_connections`, `pg.break_hba`, `pg.fill_disk`, `redis.stop`, `redis.maxmemory_oom`, `proc.cpu_hog` (stress-ng, pinned duration/workers), `proc.mem_hog`, `proc.fd_exhaust`, `app.bad_config` (Flask env/config), `permissions.break` (chmod/chown on a path).

**Leakage cleanup** (`cleanup-traces`): remove temp files written by primitives, clear shell histories, remove injector helper binaries if any were pushed, and verify with a scan that no `pokus`, `pinka` or `potka` strings, scenario-ID patterns, or primitive names remain in common locations. Fault *effects* stay, by design.

### 4.7 `potka-verifier` — independent judgment of the target state

**Purpose.** Checks preconditions, confirms faults manifested, polls resolution during the agent phase, performs final verification and collateral-damage checks — independently of anything the agent claims.

**Runs as** the service `verify` with profile `restricted-operator` (account `potka-verify`, member of `pinka-restricted`). DB role `potka_verify`. Incus `bench`.

**Socket:** `api`, orchestrator only.

**API:** `POST /runs/{run_id}/preconditions`, `/baseline`, `/confirm`, `/resolution-poll/start|stop`, `/final`, `/collateral`.

**Probe primitive library:** `http_get` (from probe-client; expect status/body regex/error class), `tcp_connect`, `unit_state`, `nginx_config_valid`, `dns_resolve` (expect address / NXDOMAIN / timeout), `disk_usage_below`, `inode_usage_below`, `container_state`, `pg_ready`, `pg_query` (read-only), `redis_ping`, `process_absent`, `load_below`, `integrity` (hash tree of given paths compared with the BASELINE capture), `file_unchanged`.

**Behavior:**
- **BASELINE:** capture integrity hash trees of every path any collateral rule references, plus service states; store as artifact.
- **CONFIRM:** all confirm probes must pass within `confirm_timeout`; else the run is `invalid: injection_not_manifested`.
- **Resolution polling** during AGENT: run the scenario's verification probes every `resolution_poll_interval_s` (default 5 s, configurable per campaign; set to 0 to disable). Records the first time all probes pass (`t_first_resolved`) and any later regression. Probe traffic originates from the probe-client node like a monitoring system would.
- **FINAL:** after the agent session ends, wait `settle_after_agent_s`, run verification probes once more (authoritative), then collateral rules.
- Results stored per probe with raw outputs as artifacts.

### 4.8 `potka-evaluator` — the only place where ground truth meets results

**Runs as** the service `eval` with profile `restricted-reader` (account `potka-eval`, member of `pinka-restricted`), with the relaxation `wx-memory` and the tool `quarto`. DB role `potka_eval` (read everything incl. `restricted`, write `evaluations`, `evaluation_jobs`, `eval_snapshots`, `contamination_flags`).

**Socket:** none required; it is a worker that polls `evaluation_jobs` (enqueued through the orchestrator API). Long-running service.

**Job types:** `score_runs` (per-run scoring for sealed runs not yet scored with the current evaluator version), `snapshot` (freeze a dataset of scored runs into Parquet/CSV/JSON with a content hash), `report` (render a Quarto report from a snapshot), `compare` (paired comparison between two campaigns over identical scenario instances).

**Contamination flag.** A test result is flagged as contaminated when its scenario template appears in the training data of the evaluated deployment's adapter, read from the adapter's lineage and the datasets' provenance; flags are stored in `contamination_flags`.

Details in §11.

### 4.9 `potka` CLI — the researchers' interface

Talks only to the orchestrator's `api` socket (and the gateway ad-hoc endpoint for `potka llm`). Commands:

```
potka campaign submit <spec.yml> [--dry-run]     # dry-run prints the expansion summary
potka campaign list|show|cancel|pause|resume <id>
potka run list [--campaign] [--state] [--mine]
potka run show <run_id> [--events] [--tool-calls] [--llm]
potka run artifacts <run_id> [--download DIR]
potka approvals list
potka approve <request_id> [--comment]
potka deny <request_id> [--comment]
potka queue
potka gpu status                                 # arbiter mode, active deployment, running training
potka datasets build <spec.yml> [--dry-run]      # dataset builder with the split guard
potka datasets list | inspect <hash>             # inspect: pinka-admin members only, logged as an event
potka train <job> --base <model> --dataset <hash>... [--seed <n>]   # job submission through the orchestrator
potka adapters list | show <name>@<sha256>       # registered adapters with their lineage
potka model list | show <id>
potka deployment list | activate <id>                            # activate: pinka-admin only
potka eval score|snapshot|report|compare ...
potka catalog lint [path] | show <scenario>                      # show: pinka-admin only, through the orchestrator API, logged as an event
potka llm key create|list|revoke
potka dev run <scenario> --project user-<uid> ...                # exploratory, see §10
```

Output: human tables by default, `--json` everywhere.

---

## 5. Run lifecycle (state machine)

States are persisted in `runs.phase` with a `phase_history` event per transition. Owner = service doing the work; orchestrator drives transitions.

| # | Phase | Owner | What happens | Timeout | Failure → |
|---|---|---|---|---|---|
| 1 | `queued` | orch | waiting for slot and deployment | — | — |
| 2 | `provisioning` | orch | create run network `r-<short>` (no NAT, ACL default-deny egress), launch instances from pinned image fingerprints with profiles, limits, a CPU range inside `cpuset_targets` (never a plain count), `OTEL_RESOURCE_ATTRIBUTES`, proxy devices (VMs: the VM collector address); random neutral instance names; register target context with executor | config | `invalid: provision_failed` |
| 3 | `preconditions` | verifier | blueprint health probes pass (system is healthy pre-fault) | config | `invalid: precondition_failed` |
| 4 | `baseline` | verifier | capture integrity baselines and service states | config | `invalid: baseline_failed` |
| 5 | `setup` | injector | scenario setup steps (e.g. load data) | config | `invalid: setup_failed` |
| 6 | `injecting` | injector | apply fault primitives; then `cleanup-traces` | config | `invalid: injection_failed` |
| 7 | `confirming` | verifier | confirm probes until pass; records `t_confirm` | scenario | `invalid: injection_not_manifested` |
| 8 | `settling` | orch | wait `settle_seconds` for symptoms to propagate | scenario | — |
| 9 | `evidence` | orch→executor | run evidence profile; store bundle | config | `invalid: evidence_failed` |
| 10 | `agent` | agent runner (+gateway, executor, verifier polling) | agent session within budgets | agent budget + margin | infra errors → `invalid: …`; model behavior → outcome |
| 11 | `verifying` | verifier | final probes + collateral | config | `invalid: verification_error` |
| 12 | `collecting` | orch | wait for telemetry flush (configurable), snapshot relevant log/metric query results as artifacts | config | warning only |
| 13 | `teardown` | orch | optional forensic export; delete instances and network; unregister target context | config | retried by reconcile |
| 14 | `sealed` | orch | integrity checks (§13), manifest, signature, immutability | — | `invalid: integrity_violation` (still sealed) |

Terminal fields on `runs`: `validity` (`valid` | `invalid`), `invalid_reason`, `outcome` (`diagnosed` | `abstained` | `budget_exhausted:*` | `protocol_failure` | `finished`), `sealed_at`. Ground-truth-dependent fields (correctness, resolution scoring) do **not** live on `runs`; they live in `evaluations`.

**Training runs** (§4.1, item 13) have their own states, persisted with every transition in `training_runs`: `submitted` → `running` → `succeeded` | `failed` | `interrupted`, and `succeeded` → `registered` once the adapter is registered and its lineage imported. They map to the arbiter's transitions: `train` accepted → `running`; job exit 0 with the adapter registered by the arbiter → `succeeded`; timeout or non-zero exit → `failed`; a boot during training → `interrupted` (resumed by the arbiter only for `resumable: true` jobs, otherwise final).

Time points recorded (monotonic ns + UTC): `t_queued`, `t_provisioned`, `t_confirm`, `t_agent_start`, `t_first_diagnosis`, `t_pre_action_diagnosis`, `t_first_mutating_action`, `t_first_resolved` (from polling), `t_agent_end`, `t_final_verified`, `t_sealed`.

---

## 6. Data model (Postgres)

Owned by `potka-db` with Alembic migrations, run by infra's `migrate` hook (`potka-migrate`) as `potka_migrator`. Use SQLAlchemy Core (not ORM) or plain psycopg 3; justify in an ADR.

**Schema `public`** (columns abbreviated; add indexes and FKs properly):

- `lab_sessions(id, boot_id, started_at, fingerprint_hash, fingerprint jsonb, preflight_outcome)`
- `catalog_versions(id, ref, commit, imported_at, content_hash)`
- `scenario_versions(id, catalog_version_id, scenario_id, version, title, risk_level, isolation, blueprint, split, tags[], public_meta jsonb)` — **no** inject/verify/GT content here
- `taxonomy_versions(id, version, labels jsonb, hash)`
- `blueprints(id, name, version, image_aliases jsonb, image_fingerprints jsonb, nodes jsonb)`
- `models(id, hf_repo, revision, files jsonb, quantization, license, registered_at)`
- `adapters(id, name, base_model_id, path, sha256, lineage jsonb)` — registered LoRA adapters; `lineage` holds the imported `LINEAGE.json`
- `datasets(sha256 primary key, provenance jsonb, built_by, published_at)` — published training datasets
- `training_runs(id, job, submission jsonb, job_spec_hash, status, transitions jsonb, adapter_id, submitted_by, submitted_at, finished_at)`
- `contamination_flags(id, run_id, adapter_id, scenario_template, dataset_sha256, evaluator_version, created_at)`
- `deployments(id, model_id, adapter_ids[], profile, serving_args jsonb, vllm_image_digest, spec_hash)`
- `deployment_activations(id, deployment_id, served_model_name, started_at, ready_at, ended_at, verification jsonb)`
- `agent_configs(id, name, version, prompt_template_hash, prompt_artifact, mode, budgets jsonb, sampling jsonb, tool_registry_version, tool_subset[], evidence_profile jsonb, policy_bundle_hash, hash)`
- `campaigns(id, name, submitter, spec jsonb, spec_hash, official, state, created_at, platform_version, catalog_version_id, deployment_id, agent_config_id, approval_mode, run_mode, concurrency, seed)`
- `runs(id, campaign_id, scenario_version_id, instance_index, repetition, seed_scenario, seed_sampling, session_id, phase, validity, invalid_reason, outcome, official, retry_of, t_* columns, trace_id, manifest_artifact, signature_artifact, chain_head, sealed_at)`
- `run_events(id, run_id, seq, type, source, time, data jsonb, prev_hash, hash)` — CloudEvents; hash chain per run
- `agent_steps(id, run_id, step, kind, content_artifact, tokens, created_at)`
- `diagnoses(id, run_id, step, label, root_cause, affected_component, confidence, evidence_refs[], is_pre_action, is_final, created_at)`
- `llm_calls(id, run_id, step, deployment_activation_id, request_artifact, response_artifact, logprobs_artifact, prompt_tokens, completion_tokens, ttft_ms, total_ms, finish_reason, vllm_request_id, span_id)`
- `llm_usage(id, user, deployment_activation_id, prompt_tokens, completion_tokens, total_ms, created_at)` — ad-hoc
- `tool_calls(id, run_id, step, caller, tool, tool_version, args jsonb, meta jsonb, valid, validation_error, created_at)`
- `policy_decisions(id, tool_call_id, effect, rule_id, reason, input_hash, bundle_hash, created_at)`
- `approvals(id, tool_call_id, mode, state, decided_by, decision, comment, requested_at, decided_at)`
- `executions(id, tool_call_id, node_alias, instance_name, argv jsonb, exit_code, status, duration_ms, stdout_artifact, stderr_artifact, truncated, post_check jsonb)`
- `probe_results(id, run_id, phase, probe, params jsonb, passed, detail jsonb, output_artifact, created_at)`
- `artifacts(sha256 primary key, size, media_type, created_at, created_by)`
- `annotations(id, run_id, author, kind, body, created_at)` — the only way to "correct" a sealed run
- `evaluation_jobs(id, kind, params jsonb, requested_by, state, created_at, started_at, finished_at, error)`
- `evaluations(id, run_id, evaluator_version, taxonomy_version_id, scores jsonb, created_at)` — many per run over time
- `eval_snapshots(id, params jsonb, content_hash, path, evaluator_version, created_at)`
- `api_keys(id, user, key_hash, created_at, revoked_at)`

**Schema `restricted`** (readable only by the roles of the restricted-capable profiles: orch, inject, verify, eval; infra closes it again after every migration):

- `run_assignments(run_id, scenario_version_id, parameters jsonb, gt_hash)` — the run→scenario link and the **commitment hash** of the resolved ground truth, written at expansion time (before the run starts).
- `scenario_payloads(scenario_version_id, scenario_doc jsonb, ground_truth_doc jsonb)` — imported from the catalog at catalog import.

Views for Grafana/researchers (`potka_reader`, `potka_grafana`): run summaries **without** scenario identity for in-progress runs; with scenario identity only for sealed runs (researchers may know which scenario a sealed run was — they are not the model).

**Immutability:** triggers reject UPDATE/DELETE on run-scoped tables when `runs.sealed_at IS NOT NULL`, and reject any UPDATE/DELETE on `run_events`, `llm_calls`, `tool_calls`, `executions`, `probe_results`, `diagnoses`, `policy_decisions` at all (insert-only). Grants: service roles only INSERT into their tables, SELECT where needed; migrations grant per profile through the group roles `potka_<profile>`.

**Hash chain:** `hash = sha256(canonical_json(event_without_hash) || prev_hash)`; first event's `prev_hash` is the run ID's sha256. The chain head goes into the manifest.

**Artifact store:** `/srv/pinka/platforms/potka/state/orch/artifacts/sha256/<ab>/<cd>/<full-hash>` + optional `.zst` compression flag in the `artifacts` row; files written atomically (temp + rename), chmod 0440, never overwritten.

---

## 7. Tool registry and the agent's world

### 7.1 Registry format (`tools/registry/*.yaml`)

```yaml
- name: restart_service
  version: 3
  description: "Restart a systemd service on a node."   # shown to the model
  risk: medium              # read | low | medium | high | forbidden
  mutating: true
  applicable_categories: [service.stopped, nginx.config.syntax_error, db.unavailable]
  args:
    node: {type: string, from: context.nodes}
    unit: {type: string, pattern: '^[a-z0-9@._-]+\.service$', from: context.node.services}
  exec:
    argv: [systemctl, restart, "{unit}"]
    timeout_s: 30
    kill_after_s: 5
    max_output_bytes: 65536
  post_check:
    argv: [systemctl, is-active, "{unit}"]
```

- `potka-tools` loads, validates (JSON Schema), and compiles the registry into: (a) OpenAI tool schemas for the model (description + args; `_meta` added automatically for mutating tools), (b) OPA data (`data.tools`), (c) docs (`docs/tools.md`, generated).
- The registry has a version (`tools/registry/VERSION`), and its content hash is recorded in agent configs and runs.

### 7.2 Required tools

Read-only (`risk: read`): `list_nodes`, `get_service_status`, `list_failed_units`, `tail_journal` (unit, lines, since), `read_file`, `list_dir`, `check_disk_usage`, `check_inodes`, `check_memory`, `list_processes` (top N by cpu/mem), `network_interfaces`, `list_listening_ports`, `dns_lookup` (from a node), `http_probe` (from probe-client), `nginx_test_config`, `docker_ps`, `docker_logs`, `docker_inspect` (filtered fields), `pg_isready`, `pg_activity_summary`, `redis_ping`, `redis_info` (filtered sections), `query_logs`, `query_metrics`.

Mutating: `start_service` (low), `restart_service` (medium), `nginx_reload` (low), `docker_restart_container` (low), `docker_start_container` (low), `apply_config_patch` (medium), `rotate_and_vacuum_logs` (medium; journald vacuum + logrotate force on allowlisted logs), `remove_file` limited to allowlisted temp/cache prefixes (medium), `kill_process` (high), `stop_service` (high), `docker_remove_container` (high).

Forbidden (in registry with `risk: forbidden` purely so policy and docs can name them and the model gets a clear denial if it hallucinates them): package removal, user management, firewall flush, `rm` outside allowlists, reboot.

### 7.3 What the model is allowed to see (complete list)

The system prompt (template + hash), the ticket text, tool descriptions and schemas, the taxonomy labels, budget information, tool results, approval/denial messages, and in static mode the rendered evidence bundle. **Nothing else.** No scenario IDs, no hostnames other than neutral node aliases (`web`, `db`, `cache`, `client`…), no lab metadata.

### 7.4 Prompts

`prompts/<name>/<version>/system.md.j2` and `user_first.md.j2` etc., rendered with a strict Jinja2 environment (`StrictUndefined`), hashed after rendering-template load (template hash) and recorded; the fully rendered first messages are stored per run as artifacts.

### 7.5 Agent configs (`agent-configs/*.yaml`)

`{name, version, prompt: name@version, mode, run_style: interactive|static, tool_subset, budgets, sampling: {temperature, top_p, max_tokens}, logprobs_top_k, evidence_profile}`. Validated, hashed, imported into `agent_configs`.

### 7.6 Evidence profile

A named, versioned list of read-only tool calls with fixed args (e.g. `list_failed_units` on every node, `tail_journal` 50 lines for each blueprint service, `check_disk_usage`, `check_memory`, `list_listening_ports`, `http_probe` of each public endpoint). Executed by the executor with caller `orchestrator`; identical for every scenario of a blueprint, so it reveals nothing scenario-specific beyond real symptoms.

---

## 8. Campaign spec

```yaml
name: qwen-8b-awq-vs-baseline-test-split
seed: 20261005
selection:
  catalog_ref: catalog-v1.3.0
  split: test
  include_tags: []
  exclude_tags: [slow]
  scenario_ids: []            # empty = all matching
  instances_per_template: 20  # parameter seeds per scenario template
  include_controls: true      # CONTROL-* scenarios mixed in
repetitions: 3
deployment: dep-qwen8b-awq-ada12g@2
agent_config: diag-interactive@4
mode: autonomous_low_risk      # §9.3
approval: oracle               # §9.4
run_mode: throughput           # throughput | measurement
concurrency: 2
retry_policy: {interrupted: retry_once, invalid_infra: retry_once, other_invalid: no_retry}
resolution_poll_interval_s: 5
notes: "…"
```

**Comparison campaigns** compare a base model with an adapter: the same benchmark, the same instances and the same seeds, paired by instance, for a baseline deployment and a candidate deployment that names `adapter_ids` (`<name>@<sha256>`, registered adapters only).

Validated against a JSON Schema exported from the pydantic model. `--dry-run` prints the number of runs, estimated duration, deployment switch needs and disk usage.

---

## 9. Policy, modes and approvals

### 9.1 OPA bundle (`policy/`)

Rego (v1 syntax) with a single decision entry point `data.potka.action.decision` returning `{effect: allow|deny|require_approval, rule_id, reason}`. Unit tests with `opa test` covering every rule. The bundle hash is recorded on every decision.

### 9.2 Policy input

```json
{
  "caller": "agent|orchestrator",
  "mode": "diagnose_only|recommend|autonomous_low_risk|human_in_loop",
  "tool": {"name": "...", "version": 3, "risk": "medium", "mutating": true, "applicable_categories": ["..."]},
  "args": {"...": "..."},
  "meta": {"action_confidence": 0.71},
  "diagnosis": {"present": true, "label": "...", "confidence": 0.82},
  "counters": {"steps": 7, "mutating_calls": 1, "same_action_repeats": 0},
  "budgets": {"max_mutating_calls": 5, "max_same_action_repeats": 2},
  "thresholds": {"min_action_confidence": 0.6, "min_diagnosis_confidence": 0.5}
}
```

### 9.3 Decision rules (evaluate in this order; first match wins)

1. Tool unknown, `risk: forbidden`, or invalid args → **deny**.
2. `caller: orchestrator` and tool is read-only → **allow** (evidence profile). Orchestrator may never call mutating tools.
3. Read-only tool → **allow**.
4. Mode `diagnose_only` → **deny** all mutating.
5. Mode `recommend` → **deny** with reason `recommend_mode_not_executed` (the proposal is still recorded and scored offline).
6. No diagnosis submitted yet → **deny** (`diagnosis_required_before_action`).
7. Diagnosis label not in tool's `applicable_categories` → **deny** (`action_inconsistent_with_diagnosis`).
8. Same mutating action repeated more than `budgets.max_same_action_repeats` times (from the agent config), or mutating budget exceeded → **deny**.
9. `risk: high` → **require_approval** (any mode).
10. `risk: medium` → **require_approval** in `human_in_loop`; in `autonomous_low_risk` → **require_approval**.
11. `risk: low` and `action_confidence ≥ min_action_confidence` and `diagnosis.confidence ≥ min_diagnosis_confidence` → **allow**; otherwise **require_approval** in `human_in_loop`, **deny** in `autonomous_low_risk`.

Thresholds come from the agent config (so they are part of the recorded experimental condition).

### 9.4 Approval modes (campaign-level)

- `deny_all` — every approval request denied (measures fully cautious autonomy).
- `approve_all` — every request approved (measures unguarded autonomy; still bounded by forbidden/deny rules).
- `oracle` — approve iff the action matches the ground truth's `acceptable` set for this run (resolved by the orchestrator, which may read GT). **Documented caveat:** the approval signal leaks correctness feedback to the model; results under `oracle` represent "oracle-gated autonomy" and must be labeled as such in reports.
- `human` — wait for `potka approve/deny` up to `approval_timeout_s`; timeout → deny. Records decider and latency. Not recommended for benchmark campaigns (non-reproducible), fine for demonstrations.

---

## 10. Exploratory runs (`potka dev`)

Researchers can iterate on prompts, tools and scenarios without touching official records:

- `potka dev run` executes the same run pipeline **in-process as the researcher**, against the researcher's personal Incus project (`user-<uid>`), using the researcher's own catalog checkout and the gateway's ad-hoc endpoint.
- Results are recorded through dedicated orchestrator `api` endpoints (a dev run gets no database role of its own) and stored with `official=false`, `submitter=<user>` and `campaign.kind=dev`; they are excluded from official snapshots by default.
- No ground-truth separation guarantees are claimed for dev runs (the researcher can read their own catalog); this is stated in every dev run's manifest.

---

## 11. Evaluation and reporting

### 11.1 Per-run scoring (`score_runs`)

For each sealed, valid run, compute and store in `evaluations.scores` (with evaluator version and taxonomy version):

- `diagnosis_correct_final`, `diagnosis_correct_pre_action` (label ∈ GT `accepted_labels`), `diagnosis_correct_prefix` (hierarchical partial credit, reported separately), `abstained`.
- `diagnosis_confidence_final`, `diagnosis_confidence_pre_action`, and **logprob-derived confidence** for the final label (mean and product of the label tokens' probabilities in the `submit_diagnosis` call; documented method).
- Per mutating action: `acceptable` / `harmful` / `neutral`, its `action_confidence`, policy effect, approval outcome, executed or not.
- `proposed_actions` scoring for `recommend` mode (same classification without execution).
- `resolved` = final verification passed ∧ collateral clean ∧ no harmful action executed.
- `false_positive_action` (control scenario with ≥ 1 executed mutating action).
- `ttd_s` = `t_first_diagnosis_correct − t_confirm` (null if never correct), `ttr_s` = `t_first_resolved − t_confirm` (null if never), `agent_wall_s`.
- Counts: steps, tool calls, invalid tool calls, policy denials, approvals requested/granted, nudges.
- Tokens in/out, mean/median TTFT, total LLM time.
- Resource usage over `[t_agent_start, t_agent_end]` from Prometheus: GPU utilization and memory (mean/max), vLLM KV-cache usage, target CPU/memory (per node).
- `contaminated`: the run's scenario template appears in the training data of the evaluated deployment's adapter (from the lineage and the datasets' provenance); stored in `contamination_flags`.

### 11.2 Aggregate metrics (snapshot/report)

- Accuracy (abstentions counted as incorrect), **coverage** (non-abstained / valid).
- Per-class and macro **precision / recall / F1** over taxonomy labels including `none` (control scenarios).
- **Calibration** for diagnosis confidence and action confidence separately: reliability diagram, **ECE** with 15 equal-width bins and adaptive (equal-mass) ECE, Brier score, for both verbalized and logprob-derived confidence.
- **Selective prediction:** risk–coverage curve, **AURC**, selective risk at fixed coverages (e.g. 50/70/90 %).
- **Resolution rate** (action-enabled modes), **action precision** (acceptable / executed mutating), **harmful action rate**, **false-positive action rate** (control scenarios).
- **MTTD / MTTR**: Kaplan–Meier median and IQR, with undetected or unresolved runs as censored observations (never dropped), plus a resolution-by-time curve; the method is documented in the metrics reference.
- Tool-call counts, tokens, latency (TTFT, end-to-end), resource usage.
- Validity breakdown (why runs were invalid) — always reported.
- Contaminated runs are excluded from aggregates or reported separately, never mixed in silently; every report states how many runs were flagged.

### 11.3 Statistics

- 95 % confidence intervals by **cluster bootstrap** with scenario template as cluster (10 000 resamples, seeded, seed recorded).
- Repetitions: report per-instance agreement (stochasticity) and aggregate per instance (mean / majority) before cross-model comparisons.
- Paired comparisons of two campaigns on identical instances: **McNemar exact test** for correctness, **paired bootstrap** for continuous metrics; Holm–Bonferroni correction when comparing many pairs.
- Only runs with identical environment fingerprint hashes are compared for latency/resource metrics; the report states this and lists excluded runs.
- Base-versus-adapter comparisons are paired by instance and seed (McNemar exact test for correctness, paired bootstrap for continuous metrics).

### 11.4 Outputs

- Snapshot directory `/srv/pinka/platforms/potka/state/eval/reports/snapshots/<id>/`: `runs.parquet`, `actions.parquet`, `llm_calls.parquet`, `metrics.json`, CSV equivalents, `MANIFEST.json` (inputs, filters, evaluator version, content hash).
- Reports `/srv/pinka/platforms/potka/state/eval/reports/<report_id>/`: Quarto templates in `reports/templates/` (`campaign.qmd`, `compare.qmd`, `calibration.qmd`) rendered to HTML and PDF (Typst engine), figures as PNG + SVG, every report footer listing snapshot hash, evaluator version, campaign IDs, fingerprint hashes, and for adapter deployments the adapter's lineage (base model, dataset hashes, job-spec hash).
- Optional LLM-as-judge for free-text `root_cause` (off by default): runs as a separate evaluation job through the gateway with its own recorded deployment and prompt; results stored separately; never mixed into primary metrics; a human-labeled sample is required to report its agreement.

---

## 12. Run manifest, signing and QA attestations

### 12.1 Run manifest

Manifest JSON (canonical, sorted keys) includes: run/campaign IDs, submitter, official flag, session ID and fingerprint hash, platform version and commit, catalog ref and scenario version, **gt_hash commitment**, seeds, blueprint and image fingerprints, the CPU range of every instance, deployment + activation IDs, model revision and file hashes, adapter ids, lineage hashes and dataset hashes of the served deployment, vLLM image digest, serving args, agent config hash, prompt template hash, rendered first-message artifact hashes, tool registry version and hash, policy bundle hash, approval mode, all `t_*` timestamps, validity/outcome, event chain head, artifact list with hashes. Signed with ed25519 (`cryptography` library) using the key whose path `endpoints.toml` gives, with its key ID (§3) in the manifest; signature stored as artifact; `potka run verify <run_id>` recomputes and checks chain, manifest and signature.

### 12.2 QA attestations

An attestation states that the external verification repository `pokus-qa` verified one release candidate. This platform owns the format, its JSON Schema (`schemas/attestation.schema.json`, exported like the other schemas) and the official check; nothing here depends on instructions outside this document.

```json
{
  "schema_version": 1,
  "attestation_id": "<ULID>",
  "rc": {
    "infra": {"release": "<infra release tag>", "commit": "<commit>", "tree": "<tree hash>"},
    "platform": {"commit": "<commit>", "tree": "<tree hash>", "manifest_sha256": "<sha256 of platform-manifest.yaml>"},
    "catalog": {"commit": "<commit>", "tree": "<tree hash>"}
  },
  "verification_run_id": "<string>",
  "verifier_commit": "<pokus-qa commit>",
  "suites": ["<suite name>"],
  "verdict": "pass",
  "issued_at": "2026-10-05T09:12:44Z",
  "key_id": "<lowercase hex SHA-256 of the DER-encoded public key>"
}
```

- `verdict` is `pass` or `fail`; `issued_at` is UTC ISO 8601 with `Z`; `attestation_id` is a ULID. Unknown fields are errors.
- **Canonical form and signature:** the JSON is canonicalized with RFC 8785 (JCS); the detached ed25519 signature over the canonical bytes is stored next to the file as `<file>.sig` (base64).
- **Trusted keys:** PEM public keys (SubjectPublicKeyInfo) in `/etc/pinka/qa/trusted-keys/`, installed by infra; a key's ID is the lowercase hex SHA-256 of its DER encoding.
- **Official check** (§4.1, item 2): verdict `pass`; `key_id` names a trusted key and the signature verifies with it; `rc.infra` equals the release tag, commit and tree in `/etc/pinka/bootstrap-state.json`; `rc.platform` equals the commit and tree in the release's `DEPLOYED_FROM.json`, and `manifest_sha256` equals the deployed manifest's SHA-256; `rc.catalog` equals the commit and tree in the catalog's `DEPLOYED_FROM.json`. A missing attestation, a `fail` verdict or any mismatch makes the campaign `official=false`, with the reason recorded. When several attestations pass, the newest by `issued_at` is recorded.

---

## 13. Integrity checks before sealing

A run that fails any check is sealed as `invalid: integrity_violation:<check>`:

1. During `agent`, every Incus exec/file operation on the run's instances came from the executor's certificate (cross-check Incus lifecycle/logging events shipped to Loki by infra).
2. Every execution has a matching tool call, policy decision with effect allow (or approval granted).
3. The event hash chain verifies.
4. The ticket, rendered prompts and evidence bundle contain no scenario IDs, primitive names, taxonomy labels of the true fault in suspicious positions (ticket lint), or `pokus`, `pinka` or `potka` strings.
5. Telemetry for the run's window exists (warning-level if missing, recorded).
6. All referenced artifacts exist with matching hashes.
7. The deployment's adapters are registered with matching hashes.
8. For an adapter deployment, contamination was evaluated.

---

## 14. Observability of the platform itself

- One trace per run (root span `potka.run`), child spans per phase (`potka.phase.<name>`), per agent step (`potka.agent.step`), per LLM call (`gen_ai.chat` with OpenTelemetry GenAI semantic-convention attributes such as `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`; verify current attribute names), per tool call (`potka.tool.call`), per policy decision, per execution, per probe. vLLM's own spans join through propagated context if supported (verify).
- `runs.trace_id` stored; the run explorer dashboard, shipped with the platform's dashboards, links DB rows ↔ traces ↔ logs (`potka.run_id`) ↔ metrics.
- The agent runner exports through the agent OTLP socket (infra's collector stamps it `pinka.trust=untrusted`).
- Training transitions are recorded as events (`potka.orch.training.*`).
- Service metrics (Prometheus via OTLP): request rates/latencies per endpoint, queue depth, phase durations, policy decision counts by effect and rule, executor timeouts, gateway TTFT histograms, invalid runs by reason.

---

## 15. Catalog formats (owned and validated by the platform)

The platform defines JSON Schemas (exported from pydantic models to `schemas/`) for everything in the catalog (`catalog/` in this repository, deployed as the restricted content package `catalog`), and `potka catalog lint` validates a catalog checkout:

- `scenario.yaml`: `id, version, title, risk_level, isolation, blueprint, split, tags, parameters (choice/range with seed derivation), preconditions[], setup[], inject[] (primitive + params), confirm[], settle_seconds, ticket, budgets_override?`
- `ground_truth.yaml`: `fault {category, component, location}, diagnosis {accepted_labels[]}, actions {acceptable[], then_required[], harmful[]}, verification[], collateral[], resolution_state`
- `taxonomy.yaml`: versioned hierarchical labels incl. `none` and `unknown`.
- `blueprints/*.yaml` (scenario-facing): nodes, aliases, services, allowed read paths, patchable paths + validators, public endpoints for probes, image alias/version.

Lint rules include: `split` (`train`, `dev` or `test`) on every scenario and a stable template identity (the scenario `id` across versions), which the contamination flag uses; schema validity, primitive and probe names exist with compatible versions, parameter templates resolve for every seed, ticket lint (§13.4), control scenarios have `accepted_labels: [none]` and empty `acceptable` actions, and every scenario has at least one confirm probe and one verification probe.

**Starter content to create in `catalog/`**: taxonomy v1, blueprints for `web-nginx-flask`, `pg-primary`, `redis-cache`, `docker-host`, and scenarios `NGINX-001` (config syntax error), `NGINX-002` (upstream failure), `SERVICE-001` (service stopped), `DISK-001` (disk exhaustion), `NETWORK-001` (DNS failure), `DOCKER-001` (container crash), `DB-001` (PostgreSQL unavailable), `CONTROL-000` (healthy system, misleading ticket) — each with parameters, ground truth, and probes, all eight in the `dev` split.

---

## 16. Security invariants (put in `CLAUDE.md`)

1. Agent runner, gateway and executor never read catalog files, the `restricted` schema, or the run→scenario mapping. Tests assert their DB roles cannot `SELECT` from `restricted`.
2. No shell anywhere on the command path: argv arrays only; tests fail on any `shell=True`, `sh -c`, or string-formatted commands in executor/injector/verifier code (enforced by a lint rule).
3. The model never writes raw LogQL/PromQL/SQL; queries are built server-side with the run filter enforced.
4. Paths are normalized and checked against allowlists before any file access; symlink traversal is checked inside the target (`realpath` via exec) before reading or patching.
5. Every endpoint enforces caller identity via peercred allowlists; integration tests cover forbidden callers for every endpoint.
6. No secret or ground-truth value appears in logs, traces, or exported events visible to non-GT roles.
7. Sealed runs are immutable; corrections only via `annotations`.
8. Orchestrator never sends mutating tool calls.
9. **No personal or installation-specific data in the repository.** No real names, usernames, e-mail addresses, organization or institution names, hostnames, domains, IP or MAC addresses, hardware serials/UUIDs, SSH keys, Tailscale identities, or anything else that identifies a person or a specific installation — in code, configuration, examples, fixtures, tests, documentation, screenshots or generated files. Use neutral placeholders: `researcher1`, `researcher2`, …; `example-org`; `example.com` / `example.org` / `example.net` (RFC 2606); documentation IP ranges `192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24`, `2001:db8::/32` (private RFC 1918 and CGNAT ranges are allowed only as technical defaults such as address pools); MACs from `00:00:5e:00:53:00/24` (RFC 7042); zeroed serials and UUIDs. Real deployment data (host_vars of real machines, `users/*.pub`, installer answers, vaults) belongs only in the deploying lab's own private fork or configuration repository: `.gitignore` excludes it from the public project, the installer's commit offer warns when the target remote is the public upstream, and fixtures captured from real hosts pass a documented sanitizer first. A CI **privacy scan** enforces this (e-mail addresses outside RFC 2606 domains, public IP addresses, MAC addresses outside the documentation range, SSH/PGP key material outside clearly marked test fixtures, and an optional maintainer-supplied denylist provided as a CI secret, never committed). This rule is about data tied to a **person or a specific installation**; it does **not** restrict references to public services, vendors and projects (GitHub, GitHub Actions, Docker Hub, Hugging Face, Tailscale, distribution mirrors, upstream documentation, model names) or to the Pokus project's own public repository and organization once it is published. Pokus is an open-source project meant to be deployed and customized by anyone, so every person- or installation-specific value is supplied by the deployer at deploy time and never baked into the project.
10. Dedicated agent sockets return no restricted data; a test covers every endpoint on them.
11. Training datasets come from the `train` split only.
12. The platform never touches GPU devices: training runs only in infra's containers, through the GPU arbiter.

---

## 17. Testing

- **Unit tests** for every package (pytest), including property-based tests (Hypothesis) for argument validation, path normalization, seed derivation, hash chains and canonical JSON.
- **Fakes:** `FakeIncus` (in-memory instances with scripted command responses and a fake filesystem), `FakeVLLM` (scripted model turns incl. tool calls, malformed calls, plain-text replies, logprobs), fake Loki/Prometheus, an OPA test harness (`opa test` + a Python integration test using a real OPA binary in CI).
- **Integration tests** with a real Postgres (a CI service container in CI; locally a conda-forge PostgreSQL installed with micromamba) running migrations, triggers and grants (assert immutability and `restricted` blindness).
- **End-to-end (fake) tests:** a full campaign of `NGINX-001`, `DISK-001`, `CONTROL-000` through all services in-process with fakes, asserting events, records, sealing, signature verification, and evaluator metrics on known inputs.
- **Metric tests:** evaluator metrics on synthetic datasets with hand-computed expected values (ECE, AURC, McNemar, Kaplan–Meier median and IQR with censoring, cluster bootstrap reproducibility with fixed seed).
- **Opt-in real-host tests** (`make e2e-real`, never in CI): run against the real lab services on a provisioned host; I will run these.
- Lint: ruff, ruff format, mypy --strict, a custom check banning `shell=True`/`os.system`/`subprocess` with string commands in sensitive packages.
- **Contract tests:** CI validates `platform-manifest.yaml` against the schema of the targeted `pokus-infrastructure` release; a test proves that no endpoint on a dedicated agent socket (`agent-session`, the gateway's and the executor's `agent`) returns restricted data; tests for the dataset builder's split guard, the contamination flag and the lineage import.

---

## 18. Repository layout

```
pokus-platform/
├── README.md  CLAUDE.md  PROGRESS.md  LICENSE
├── .github/workflows/          # CI for both subtrees
├── platform/                   # the release; infra deploys it with path platform/ (§3)
│   ├── Makefile  pyproject.toml  uv.lock
│   ├── platform-manifest.yaml   # contract v2 manifest (§3)
│   ├── packages/
│   │   ├── potka-core/          # pydantic schemas, ULIDs, canonical JSON, CloudEvents, config, logging, OTel, UDS client/server, peercred auth
│   │   ├── potka-db/            # SQLAlchemy Core tables, Alembic migrations, repositories
│   │   ├── potka-incus/         # thin Incus REST client + FakeIncus
│   │   ├── potka-tools/         # registry loader/compiler, arg validation, argv rendering
│   │   ├── potka-primitives/    # fault and probe primitives
│   │   ├── potka-orchestrator/  # incl. reconcile, scheduler, state machine, sealing
│   │   ├── potka-agent/         # agent runner
│   │   ├── potka-gateway/
│   │   ├── potka-executor/
│   │   ├── potka-injector/
│   │   ├── potka-verifier/
│   │   ├── potka-evaluator/
│   │   └── potka-cli/
│   ├── tools/registry/          # tool definitions + VERSION
│   ├── policy/                  # Rego + tests (bundle)
│   ├── prompts/                 # versioned prompt templates
│   ├── agent-configs/
│   ├── campaigns/examples/
│   ├── schemas/                 # exported JSON Schemas (generated)
│   ├── reports/templates/       # Quarto templates
│   ├── dashboards/              # Grafana dashboards, including the run explorer (§14)
│   ├── training/                # sources of the training job image (built and published by digest)
│   ├── tests/{integration,e2e,fixtures}/
│   └── docs/
│       ├── BUILD_PROMPT.md  IMPLEMENTATION_NOTES.md
│       ├── architecture.md      # services, trust boundaries, sequence diagrams (Mermaid) for a full run
│       ├── services/<service>.md
│       ├── data-model.md        # generated ER diagram + table docs
│       ├── metrics.md           # exact metric definitions (from §11)
│       ├── tools.md             # generated from registry
│       ├── policy.md
│       ├── catalog-format.md
│       ├── reproducibility.md
│       └── adr/
└── catalog/                    # the restricted content package `catalog`; infra deploys it with path catalog/ (§3, §15)
    ├── taxonomy.yaml
    ├── blueprints/              # scenario-facing blueprint descriptions
    └── scenarios/<ID>/          # scenario.yaml, ground_truth.yaml
```

Repository paths in this document are relative to `platform/`, except `catalog/` and the root files. The two subtrees never overlap: infra deploys `platform/` as the release and `catalog/` as the content package, and refuses a deployment whose release and content paths overlap (infra §7.14).

---

## 19. Implementation order (within this single build)

1. Skeleton, tooling, `CLAUDE.md`, `docs/IMPLEMENTATION_NOTES.md` → **stop and ask me the open questions**.
2. `platform-manifest.yaml` and its CI validation against the schema of the targeted `pokus-infrastructure` release.
3. `potka-core` (schemas, IDs, canonical JSON, events, UDS server/client with peercred, config, logging, OTel; peer allowlists from `principals.json`).
4. `potka-db`: tables, migrations, triggers, grants, repositories, integration tests.
5. Catalog schemas + `potka catalog lint` + starter catalog content.
6. `potka-incus` + `FakeIncus`; `potka-tools` + registry; `potka-primitives`.
7. Executor + OPA bundle + policy tests.
8. Injector and verifier.
9. Gateway + `FakeVLLM`.
10. Agent runner.
11. Orchestrator (expansion, scheduler, state machine, approvals, deployments, sealing, drain) + reconcile.
12. CLI.
13. Evaluator + Quarto templates + metric tests.
14. Training flow: dataset builder with the split guard, job submission, lineage import, comparison campaigns, contamination flag.
15. Fake end-to-end campaign test; docs; final `make lint test`.

After each step: documentation for what the step built written or updated (per `docs/DOCS_SPEC.md`), `make lint test docs` green, commit, short summary to me including remaining `TODO(verify)` items.

## 20. Definition of done

- `make lint test` passes; `make verify-todos` passes (or remaining items approved by me).
- The fake end-to-end campaign produces sealed, signature-verified runs and a rendered HTML report whose metrics match hand-computed expectations.
- Tests prove: `restricted` is unreadable by agent/gateway/executor roles; sealed runs cannot be modified; no shell execution paths exist; every endpoint rejects unauthorized callers; the model cannot act on another run's nodes; policy rules behave per §9.3.
- `potka catalog lint` passes on the starter catalog; every starter scenario runs through the fake pipeline.
- Pokus deploys through its manifest on the targeted infra release, and the obligation tests pass (no restricted data on dedicated agent sockets; the dataset split guard and complete provenance; the contamination flag).
- Docs for every service exist and match the implementation: `make docs` strict build with zero warnings; docs coverage 100 % (every endpoint with its allowed callers, CLI command, config key, schema, tool, primitive, probe, policy rule, event type, metric, DB table); OpenAPI rendered per service; the **Benchmark methodology**, run lifecycle, ground-truth blindness and agent boundary explanation pages exist and match §2, §5, §9 and §11; researcher and scenario-author tutorials and how-tos from `docs/DOCS_SPEC.md` §A.3 exist with `qa:run` markers.
