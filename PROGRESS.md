# PROGRESS

Single source of truth for the build of Pokus in this repository: owner
answers, approved deviations, contract changes, step status with commit SHAs,
open `TODO(verify)` items, known issues and the next step.

**After a context reset or in a new session:** read this file, then the
`BUILD_PROMPT.md` and `CLAUDE.md` of the component you work in; check the
repository state with read-only git commands (`git status`, `git log`);
continue from [Next step](#next-step). Never redo a completed step from memory.

This file is part of the public repository and follows the privacy invariant:
no personal or installation-specific data.

## Specifications

`specs/` holds the master copies (never edited). Component copies are
byte-identical.

| File | Title | sha256 |
| --- | --- | --- |
| `specs/pokus-infra-claude-code-prompt.md` | Build Prompt: pokus-infra | `4c7de8136328f39e71f3d4dd891be2f4b9e67bda4f4e36646d38ed62ed00728a` |
| `specs/pokus-platform-claude-code-prompt.md` | Build Prompt: pokus-platform | `ba3626f3b768f2f0e3ad329b214d69b83e2931d90a1b69cc1240ef34eb2e8aee` |
| `specs/pokus-docs-specification.md` | Pokus Documentation Specification | `717f3583ee56783a93bf16334f1c88e031d1a4bf21ac7b97ecfdda5c594725f5` |

These are the updated specs that contain the privacy invariant (infra §13.12,
platform §16.9, docs spec §A.4 and §A.6) and the docs-alongside rule (infra
§0.8, platform §0.7). Copies: `infra/docs/BUILD_PROMPT.md` (infra prompt),
`infra/docs/DOCS_SPEC.md` (docs spec); platform, catalog and docs-site copies
are created in their phases.

## Process rules in force

- **Commits (owner answer A2, amended by later owner instructions; cloud
  environment only).** Infra steps 1-4 were committed on the owner's
  "commit". For the rest of infra (steps 5-14) the owner instructed: commit
  and push each step to `main` as soon as it passes `make lint test docs`,
  without waiting for "commit" (`git add <step files>`,
  `git commit -m "<Conventional Commit message>"`, `git push -u origin main`);
  no amend, no force push, no history rewrite. After infra is finished the
  assistant stops and waits before the platform phase. Commit messages carry
  no co-author or session trailers: only the owner's git identity appears.
  The identity is set in the local git configuration only and never written
  into any repository file. All work lands directly on `main` (owner
  instruction at the first commit, replacing the original working branch
  `claude/zen-albattani-qfwwps`).
- **Tags** are never created by the assistant; it proposes tag commands.
- **Privacy.** No file may contain real personal or installation-specific data.
  The project's public repository URL is added only once it is published (H2);
  until then placeholders are used.
- **Checks per step.** Each numbered step ends with the spec's checks
  (`make lint test docs` for every component touched), a summary, and proposed
  commits.

## Owner answers

Recorded from the owner's answers to the open questions (§5 of the master
prompt). "Approved" means the assistant's stated default is implemented
exactly.

### A. Inputs, process, environment

- **A1** `specs/` copies: approved; byte-identical, sha256 above.
- **A2** Git process: see [Process rules](#process-rules-in-force).
- **A3** Version verification: the owner will allow network access for the
  domains listed in `infra/docs/IMPLEMENTATION_NOTES.md`. Until then items stay
  `TODO(verify)` and `lock-check` / `verify-todos` keep failing; this is the
  correct behavior and is not worked around.
- **A4** Dev tools: approved; user space only, pinned, nothing via apt or under
  `/etc`. PostgreSQL for integration tests: a pinned, checksum-verified static
  `micromamba` installs `postgresql` from conda-forge, pinned to the same major
  version as the Postgres container in infra's `versions.lock.yml`, into a
  user-space prefix; a throwaway cluster runs on a Unix socket in a temporary
  directory; tests read the DSN from an environment variable; CI uses a service
  container of the same major version.

### B. One-repository mapping

- **B1** Spec copies: approved, including `docs-site/docs/BUILD_PROMPT.md` =
  full docs spec; spec copies and `IMPLEMENTATION_NOTES.md` are excluded from
  MkDocs builds.
- **B2** Ground-truth exposure from monorepo deployment: approved exactly as
  proposed: root-only bare mirror in `/var/lib/pokus/git/` (0700); tag and
  signature checks there; `git archive` of a single subtree per deployment;
  never any `.git` in deployed paths; `catalog/` only into
  `/srv/pokus/catalog/<ref>/` (root:pokus-gt 0750); `DEPLOYED_FROM.json`
  provenance file in the contract and used by the platform's official check.
  Add a doctor check and a test asserting that no deployed path contains
  `.git` or catalog content outside `/srv/pokus/catalog`.
- **B3** Bootstrap git checks: record the repository commit plus the `infra/`
  tree hash; dirty-tree check limited to `infra/` (platform and catalog are
  deployed from tags via the mirror, never from the working tree).
- **B4** Tags and versions: prefixes `infra-v`, `platform-v`, `catalog-v`,
  `docs-v`; "deployed from a tag" = the commit carries a tag with that
  component's prefix; RC triple = three commits of this repository plus each
  component's tree hash; all components start at 0.1.0; the owner creates tags.
- **B5** CI: canonical `<component>/.github/workflows/ci.yml` calling make
  targets, thin root workflows with `working-directory` and `paths` filters.
  Runs on GitHub Actions, but only lint, unit, type checks, docs
  build/coverage/drift, lock-check and syntax/check-mode jobs. Never
  provisioning, distro VM harnesses or real-host tests on Actions.
- **B6** Catalog lint dependency: a permanent uv path dependency on
  `../platform` inside this monorepo (lint always runs with the platform of the
  same commit). `catalog/README` documents that a future split replaces it with
  a git URL pinned by commit.
- **B7** Starter catalog for platform tests: `POKUS_TEST_CATALOG`, Makefile
  default `../catalog`; no vendored copy.
- **B8** Starter content is written into `catalog/` during platform step 4;
  Phase 3 completes `catalog/` as a self-contained component.
- **B9** Docs site: no submodules; `git archive` of `infra/`, `platform/`,
  `catalog/` at the given commits.

### C. Integration contract

- **C1** One merged `contract_version 1`, byte-identical `docs/contracts.md` in
  `infra/` and `platform/` (added to the platform layout), a CI check that fails
  if the copies differ. Spec files are not edited; `contracts.md` is the
  resolved contract and each resolution cites the sections it reconciles.
- **C2** Agent-reachable sockets: gateway `agent.sock`, executor socket,
  orchestrator `svc.sock`; every other `/run/pokus/*` path and the Postgres
  socket in `InaccessiblePaths`. The orchestrator's `svc.sock` endpoints
  enforce peercred allowlists so `pokus-agent` can only call the agent-session
  endpoints.
- **C3** vLLM deployment switching: socket-activated root helper unit reachable
  only by `pokus-orch` (peercred-checked); argument = deployment spec ID only,
  validated against `/etc/pokus/vllm/deployments/`; the orchestrator keeps full
  hardening; no sudo anywhere.
- **C4** VM telemetry: the VM alternative is in the merged contract; Incus
  proxy-device support for VMs is verified in infra step 6; if unsupported, the
  ACL-restricted route to a dedicated collector listener is used.
- **C5** Inference modes `local | remote | scripted`, `endpoints.toml`,
  `/etc/pokus/inference/` and deployment specs on both nodes are in the merged
  contract.
- **C6** OPA on `127.0.0.1:8181`, started with authorization enabled and a
  `system.authz` policy allowing only decision queries (GET/POST on
  `/v1/data/pokus/...`) and health, denying all policy and data writes. Verify
  that writes are rejected; add a doctor check and a test.
- **C7** `<svc>` short names `orch, agent, gw, exec, inject, verify, eval`;
  Postgres password files keep role names.
- **C8** Socket permissions as proposed (group + POSIX ACL where two principals
  are needed, peercred allowlists everywhere), with the group + peercred
  fallback if ACLs are unavailable on `/run`; doctor verifies effective
  permissions.
- **C9** Contract gaps (a)-(h) approved as proposed. For (e) LoRA adapters:
  only what the specs define (adapters table, deployment `adapter_ids`, storage
  path `/srv/pokus/models/adapters/<name>/<sha256>/` with a `MANIFEST.json` the
  platform imports read-only and whose hashes infra's models role verifies).
  No training or adapter-creation flow.
- **C10** Ports: proposed in infra step 1 (`infra/docs/contracts.md#ports`).
  Everything on `127.0.0.1`, no common-collision ports (avoid 3000, 5432, 8000,
  8080, 9090 defaults where possible), documented in `contracts.md`.

### D. External verification hooks

The repository that consumes these hooks is not part of this build and its
instructions will not be provided. The hooks below are part of the merged
contract.

- **D1** `lab_mode: production | staging`. Staging hosts use their own vault,
  `/srv/pokus`, Tailscale identity (`tag:pokus-qa`) and signing key, never pair
  with or write to a production host; `scripted` inference backends are
  accepted only in staging.
- **D2** Attestation format owned and verified by the platform (JSON Schema in
  `platform/schemas/`): fields `schema_version`, `attestation_id` (ULID), `rc`
  (infra/platform/catalog commits), `tree_hashes` (same three),
  `verification_run_id`, `verifier_commit`, `suites`, `verdict` (`pass|fail`),
  `issued_at` (UTC ISO 8601 Z), `key_id` (sha256 of the public key).
  Canonicalized with RFC 8785 (JCS); detached ed25519 signature in
  `<file>.sig` (base64); trusted public keys as PEM in
  `/etc/pokus/qa/trusted-keys/`. The official check requires verdict `pass`, a
  trusted key, a valid signature, and rc commits + tree hashes equal to the
  `DEPLOYED_FROM.json` of the deployed platform and catalog and the infra commit
  recorded by bootstrap.
- **D3** `scripted` backend: any OpenAI-compatible endpoint configured in
  `endpoints.toml`; no scripted model server is built; runs using it are always
  `official=false`.
- **D4** `qa:run` / `qa:skip` markers: syntax implemented and validated by a
  docs lint (env labels `E0`-`E3` as opaque strings); nothing here executes them.
- **D5** `last_verified` with no verification runs:
  `{date: null, rc: null, by: null}`, flagged by CI, never fabricated.
- **D6** `make site`: `QA=` optional; the footer reads "no attestation" when absent.

### E. infra

- **E1** Researchers: placeholders only — `researcher1` (admin),
  `researcher2`, `researcher3`, display names "Researcher 1/2/3". No key files
  are ever committed; real researchers and keys are entered through the TUI on a
  deployed host and live in that lab's private configuration. Doctor and TUI
  flag missing keys.
- **E2** Tailscale tags: `tag:pokus-lab` (production), `tag:pokus-qa` (staging).
- **E3** Git URL for deployments: required variable `pokus_source_repo`, asked
  by the TUI, never hard-coded; examples use
  `git@github.com:example-org/pokus.git`. Authentication: SSH with a read-only
  deploy key generated on the host; Git host keys pinned in a managed
  `known_hosts` (for GitHub, its published fingerprints pinned in
  `versions.lock.yml`); anonymous public HTTPS clones also supported. No
  personal tokens.
- **E4** Backup destination: default "configure later" (doctor warns). restic
  over SFTP to a deployer-chosen host is the primary path, rest-server
  secondary, local path test-only. Examples use placeholder hosts only
  (`backup.example.com`, documentation IP ranges).
- **E5** Disk safety: approved.
- **E6** Host vars: only `example-machine.yml` and `tests/answers/ci.yml` committed.
- **E7** Model candidates: permissive, non-gated licenses first (Apache-2.0,
  MIT or equivalent); gated or custom-license models only listed separately and
  only with owner approval; presented in step 7.
- **E8** Distro matrix: changes presented in step 2. Only non-EOL releases in
  any tier; Ubuntu 26.04 added as `tested` if released and supported by Docker,
  Incus, NVIDIA toolkit and Tailscale; EOL releases dropped; supported tier
  stays Ubuntu 24.04 + Debian 13 unless a reason is shown. Incus: newest LTS
  line packaged for the supported-tier distros; report the finding.
- **E9** RHEL 8: explicit `unsupported` row for RHEL-compatible major 8 before
  the `ID_LIKE` rows; remediation names the cgroup v2 kernel argument and
  recommends a supported release; the `rhel8-cgroup-v1` fixture expects
  unsupported.
- **E10** YAML in preflight: generated, committed, drift-checked JSON.
- **E11** Lockfiles: `infra/pyproject.toml` + `uv.lock` for dev tools only; the
  installer keeps its own lockfile; `bootstrap/requirements.lock` stays separate.
- **E12** Certificates and keys: `community.crypto`, pinned.
- **E13** Researcher DB access: separate reader socket directory (group
  `pokus`) and reader credential 0640 group `pokus`; the reader role can never
  see the `gt` schema (tested).
- **E14** Metrics path: approved (collector Prometheus exporter, scraped).
- **E15** Compose unit pattern: approved.
- **E16** `require_signed_tags`: default `false`; SSH-signed tags against a
  committed allowed-signers file when `true`; both paths implemented and tested.

### F. platform

- **F1** SQLAlchemy Core + psycopg 3, with an ADR.
- **F2** Dependencies approved as listed (httpx, PyYAML, jsonschema, pyarrow,
  numpy, scipy, matplotlib, zstandard; own ULID, CloudEvents, canonical JSON
  and unified-diff code) and an argparse CLI. Canonical JSON implements RFC 8785
  (JCS) exactly, tested against the RFC's test vectors. The unified-diff
  applier rejects any fuzz or offset mismatch. All property-tested; versions
  verified and locked.
- **F3** Reports: compute in Python, code-free `.qmd` templates, no Jupyter.
- **F4** Approval delivery: long-poll with the campaign's approval timeout.
- **F5** Logprobs: JSONL + zstd.
- **F6** MTTD/MTTR: Kaplan–Meier median and IQR plus a resolution-by-time
  curve; the method is documented in the metrics reference.
- **F7** `pokus dev`: records and artifacts through new orchestrator
  `api.sock` endpoints, stored `kind=dev`, `official=false`; no new DB role.
- **F8** Admin = membership in `pokus-admin`.
- **F9** `pokus catalog show`: as specified (pokus-gt members only), documented.
- **F10** Policy rule 8: `budgets.max_same_action_repeats` from the agent config.
- **F11** LLM-as-judge: implemented, disabled by default.

### G. catalog

- **G1** Splits `train | dev | test`; all eight starter scenarios in `dev`.
- **G2** Blueprints: DOCKER-001 → docker-host, DB-001 → pg-primary, all others
  including CONTROL-000 → web-nginx-flask; the redis-cache blueprint has no
  starter scenario. Full designs presented in platform step 4.

### H. Documentation and docs-site

- **H1** License: Apache-2.0 for code, CC-BY-4.0 for catalog content and documentation.
- **H2** `CITATION.cff`: project-level only — title "Pokus", authors as the
  entity "The Pokus contributors", license, version and release date filled at
  release time. No personal names, ORCIDs, affiliations or thesis details. The
  public repository URL goes in once published (placeholder until then,
  flagged by CI only at release time).
- **H3** Code of conduct: Contributor Covenant 2.1; enforcement contact is the
  repository's private reporting channel (GitHub private vulnerability
  reporting / a maintainers' issue form), described generically; no personal
  e-mail. Same for `SECURITY.md`.
- **H4** Docs tooling: approved (MkDocs Material pinned, Node tools pinned via
  `package-lock.json`, mermaid-cli with a preinstalled Chromium, weekly link check).
- **H5** Page ownership: approved in principle; the page-by-page table is in
  `infra/docs/IMPLEMENTATION_NOTES.md` for approval.
- **H6** Front matter scope: approved (site pages and ADRs; not README,
  CLAUDE, CHANGELOG, role READMEs, spec copies or IMPLEMENTATION_NOTES).
- **H7** Tour screenshots: report screenshot from the fake end-to-end report;
  Grafana screenshot marked pending a real-host run and listed as a known gap.
- **H8** Changelogs: hand-maintained per component.
- **H9** Publishing: release tarball + `pokus-lab docs serve` (127.0.0.1
  only); no GitHub Pages.
- **H10** PDF export: pinned Quarto/Typst.
- **H11** Versioned docs: mike, demonstrated with two test versions.

### Privacy clean-up (owner instruction)

Scan everything already written for personal or installation-specific data,
replace it with placeholders, report changes; add the privacy scan to every
component's `make lint` and to CI. Result at infra step 1: nothing had been
written before the instruction (the repository was empty), so nothing needed
replacing; `make privacy` is part of `infra`'s `make lint`, and CI runs it for
`infra/` and for the root-level files (`.github/workflows/repo-root.yml`).

## Approved deviations

Resolutions above that differ from the spec text. Contract-level resolutions
are also recorded with citations in `infra/docs/contracts.md#resolutions`.

| ID | Spec section | Spec text (short) | Approved resolution |
| --- | --- | --- | --- |
| A2 | master prompt §4 | the owner makes every commit | assistant runs the approved commit commands on "commit" (cloud only) |
| A4 | platform §17 | Postgres via testcontainers or CI service | micromamba + conda-forge PostgreSQL locally; CI service container |
| B1 | docs spec §A.4 | every Markdown page has front matter | spec copies and IMPLEMENTATION_NOTES are excluded from builds, kept verbatim |
| B2 | infra §7.14 | clone pokus-platform / pokus-catalog at a ref | bare mirror + `git archive` of one subtree + `DEPLOYED_FROM.json` |
| B3 | infra §5.2 step 1 | refuse a dirty tree | dirty check limited to `infra/`; commit + `infra/` tree hash recorded |
| B4 | docs spec §B.1, platform §4.1.2 | RC triple of three repositories | three commits of one repository + tree hashes; prefixed tags |
| B5 | infra §4, §11 | `.github/workflows/ci.yml` per repository | canonical per component + thin root wrappers; restricted job set |
| B6 | master prompt §3 | components splittable without changes | catalog lint uses a permanent `../platform` path dependency |
| C1 | platform §18 | layout without `docs/contracts.md` | `platform/docs/contracts.md` added, CI identity check |
| C2 | infra §7.14 | agent reaches only gateway and executor sockets | also orchestrator `svc.sock`, agent-session endpoints only |
| C3 | platform §3, §4.1; infra §12 | sudo rule for the deploy helper | socket-activated root helper, no sudo |
| C4 | platform §3 | (no VM telemetry alternative) | VM alternative added |
| C5 | infra §12 | (no inference modes) | `local`, `remote`, `scripted` added |
| C6 | platform §3; infra §7.10 | OPA on 8181 or a Unix socket | 8181 with `system.authz` restricting the API |
| C8 | platform §4.1 | api.sock "group pokus + pokus-svc" | group + POSIX ACL, fallback group + peercred |
| C9 | infra §7.11, §7.12, §8.2, §9; platform §3, §6 | (gaps) | resolutions C9a-h in `contracts.md` |
| C10 | infra §12 | `127.0.0.1:<port>` unspecified | port table in `contracts.md` |
| D1-D3 | platform §3, §4.1.2; infra §8.1 | formats defined by the external QA instructions | hooks defined here (D1-D3) |
| D4 | docs spec §A.8 | QA bots execute `qa:run` blocks | markers linted only; nothing executes them here |
| D5 | docs spec §A.4, §B.5 | `last_verified` from a QA run | null until a verification run exists |
| D6 | docs spec §B.4 | `make site ... QA=<qa-sha>` | `QA=` optional |
| E3 | infra §18.4 screen 12 | repo URL per component; HTTPS token option | one `pokus_source_repo`; SSH deploy key or anonymous HTTPS; no tokens |
| E9 | infra §2.3 | matrix rows as listed | explicit unsupported row for RHEL-compatible major 8 |
| F7 | platform §4.1, §10 | dev runs write "the same tables" | through new orchestrator `api.sock` endpoints |
| H7 | docs spec §A.3 | tour with Grafana screenshot | Grafana screenshot pending a real-host run |
| H9 | docs spec §B.4 | Pages (private) as a target | no GitHub Pages |
| H11 | docs spec §B.5 | current and previous release browsable | demonstrated with two test versions until real releases exist |

## Contract changes

`contract_version 1` is defined by `infra/docs/contracts.md` (resolutions B2,
B3, B4, C2-C10, D1-D3, E3, E13). The ports table (C10) is proposed and awaits
owner approval.

Proposed in infra step 7 (awaiting owner approval; written into
`infra/docs/contracts.md`, details in `infra/docs/IMPLEMENTATION_NOTES.md`,
"Step 7"):

- Telemetry, VMs (C4): the dedicated collector listener `198.18.0.1:4317`/
  `4318` on the dummy interface `pokus-otlp0`, one egress rule in
  `pokus-run-default`, nothing else from run networks; listed in the ports table.
- Sockets: `/run/pokus/postgres/` also serves infra's metrics exporter
  (`pokus_monitor`); `/run/pokus/postgres-reader/` also serves Grafana as
  `pokus_grafana`; `pg_hba` cannot tell the directories apart, the password
  files decide the role (replaces "role `pokus_reader` only (`pg_hba`)").
- PostgreSQL: no TCP listener at all; the infra-internal role `pokus_monitor`.
- Inference: the deploy helper also renders `current.yaml` (the
  `vllm serve --config` file) next to `current.env`.

Proposed in infra step 8 (same status; details in
`infra/docs/IMPLEMENTATION_NOTES.md`, "Step 8"):

- Golden images: the Incus property `requirements.nesting` is `true` for
  blueprints that need nesting (a container engine inside the target), so the
  platform launches them with `security.nesting=true`.

Proposed in infra step 9 (same status; details in
`infra/docs/IMPLEMENTATION_NOTES.md`, "Step 9"):

- Session records: the record's fields (`schema_version`, `boot_id`,
  `started_at`, `fingerprint_hash`, `fingerprint`, `preflight_outcome`,
  `errors`), matching the platform's `lab_sessions` table; a field that could
  not be computed is `null` with its reason under `errors`.

Proposed in infra step 10 (same status; details in
`infra/docs/IMPLEMENTATION_NOTES.md`, "Step 10"):

- Fingerprint document: `fingerprint_schema_version`, `hash`
  (`sha256:<hex>` of the canonical JSON of `fingerprint`), `fingerprint`,
  `computed_at` and `boot_id` (not hashed), with the JSON Schema
  `infra/tools/pokus-lab/schema/fingerprint.schema.json`.
- Lab drain: `POST /lab/drain` with `{"timeout_seconds": <int>}` and
  `GET /lab/status` answering `{"active_runs": <int>}` on the orchestrator's
  `api.sock`, called as root by `pokus-lab stop` and `update-host`; runs still
  active when the lab stops are recorded as interrupted.

Proposed in infra step 11 (same status; details in
`infra/docs/IMPLEMENTATION_NOTES.md`, "Step 11"; `contracts.md` itself is
unchanged):

- Inference mTLS material: on lab nodes also `u:pokus-gw:r-x` on
  `/etc/pokus/inference/` and `u:pokus-gw:r--` on `self.key`, because the LLM
  gateway calls the remote inference node too.
- Inference control API: `GET /health` (`ok`, `vllm_status`, `deployment`,
  `fingerprint` with `hash` and `computed_at`), `GET /model` (`deployment`,
  `models`, the served model's verified `manifest`), `GET /metrics`,
  `POST /activate` with `{"deployment": "<id>@<version>"}` (the deploy
  helper's answer) and `POST /verify` (the serving verification's report).
- Ports: pairing uses 18710 while the control endpoint is stopped; a paired
  lab node receives the inference node's telemetry with OTLP gRPC on 4317 of
  its tailnet address (mutual TLS).
- Inference node: vLLM stays on `127.0.0.1:18704`; the control endpoint
  terminates mutual TLS on the tailnet address and forwards 18704 (instead
  of vLLM itself bound to `tailscale0`).
- Question: the agent runner cannot read `/etc/pokus/secrets/agent/`
  because `/etc/pokus/secrets` is in its `InaccessiblePaths` (C2); proposed
  `LoadCredential=` if the agent needs a secret.

## Step status

### Phase 1 — infra/

| Step | Content | Status | Commit |
| --- | --- | --- | --- |
| 1 | Skeleton, tooling, CLAUDE.md, IMPLEMENTATION_NOTES | done | `8df9199` (with steps 2-4; root files in `0503e86`) |
| 2 | Preflight | done | `8df9199` |
| 3 | bootstrap.sh | done | `8df9199` |
| 4 | versions.lock.yml + lock-check | done | `8df9199` |
| 5 | Ansible foundation roles (four families) | done | `ced6b6d` |
| 6 | docker, nvidia_container_toolkit, incus | done | `ea46ba3` |
| 7 | postgres, observability, opa, vllm, models | done | `99768ea` |
| 8 | images | done | `5526e43` |
| 9 | lab_session, backup, restore.yml, rotate.yml | done | `ca9ea42` |
| 10 | pokus-lab CLI with doctor and fingerprint | done | `f89e1c7` |
| 11 | catalog and platform roles, split inference | done | `a9be1d3` (CI fixes in `7955dde`) |
| 12 | test-distros harness and CI fast lane | done | commit "feat(infra): add the distribution tests and the CI fast lane" (SHA recorded with step 13) |
| 13 | Installer TUI | pending | — |
| 14 | Documentation completion | pending | — |

### Phase 2 — platform/ (steps 1-13), Phase 3 — catalog/, Phase 4 — docs-site/, Phase 5 — self-test

Not started.

## Open questions

All listed in `infra/docs/IMPLEMENTATION_NOTES.md`:

- From step 1 ("Questions raised by step 1"): reference agents for scenario
  validation, the QA results schema, project files at the monorepo root,
  approval of the port table (`infra/docs/contracts.md#ports`) and of the
  page-ownership table.
- From step 2 ("Interpretations awaiting owner confirmation"): Debian 12 under
  the E8 policy, the `debian13-baremetal` fixture tier (BUILD_PROMPT §11 vs
  §2.3/E8), rows for unlisted releases of implemented distributions,
  unreachable required endpoints → `needs_action`, storing the profile only as
  root and not with `--no-write`, and the meaning of the `qa:run` labels E0-E3.
- From step 3 ("Interpretations awaiting owner confirmation (step 3)"): the
  additional bootstrap exit codes 11, 50, 60, 64 and 70 (ADR-0002, proposed);
  stopping with 77 on a changed host profile, except after a run that ended
  with a play signal; `--force-unsupported` not skipping required actions; the
  state file written at every exit with `result` (and a proposed contract
  addition so that the official check requires a successful result); check
  mode writing neither profile nor state; `tar` and `git` checks and the
  interactive `wsl.conf` edit in pre-preflight; `--tui`/`--answers` stopping
  with 77 until the installer exists; the vault password order; the
  concurrency lock; `LC_ALL=C.UTF-8` for Ansible; and non-check runs ending
  with 60 until `pokus-lab` exists (step 10).
- From step 4 ("Interpretations awaiting owner confirmation (step 4)" and
  "Findings to report"): the lock structure; newest stable releases rather
  than LTS lines; vLLM 0.31.0 on CUDA 13.0 (a `-cu129` variant exists for
  older drivers); HEAD-only registry checks; the `verify-todos` exclusions; the
  failing `lock-check` CI job; staying on Incus 6.0 LTS although `v7.0.x` tags
  exist; NVIDIA's rpm repository file using `gpgcheck=0` (to be resolved in
  step 6).
- From step 5 ("Interpretations awaiting owner confirmation (step 5)" and
  "Findings to report (step 5)"): unverified component sources as `verify`
  data that the play refuses; `pokus_incus_bridge_patterns` from the contract
  instead of `incus_bridge_prefix`; the distribution-fact check as a
  repository check; `--allow-reboot` scheduling `shutdown -r +1` after signal
  76; removed researchers also leaving `pokus-admin`; Incus's per-user group
  added in step 6; how lab-wide variables are documented; role check-mode
  tests deferred to step 12; run networks on firewalld and ufw hosts relying
  on Incus until step 6; the Secure Boot MOK password on non-interactive
  driver installs.
- From step 6 ("Findings to report (step 6)" and "Interpretations awaiting
  owner confirmation (step 6)"): **a contract question**: a certificate
  restricted to `bench` can create run networks but cannot delete them
  (bridges live in the default project); proposed: a socket-activated root
  helper for `r-*` networks, like C3, reachable only by `pokus-orch`. Also:
  staying on Incus 6.0 although a 7.0 branch exists; `bench` cannot limit
  network access to `r-*` by name; personal projects created in advance with
  limits and without GPU; personal bridges trusted like the build bridge; the
  `daemon.json` label as managed header; the GPU check's device flags; preseed
  once, then convergence; bench limit defaults; Compose units in step 7.
- From step 7 ("Findings to report (step 7)" and "Interpretations awaiting
  owner confirmation (step 7)"): the observability stack on the host network
  bound to `127.0.0.1` (Prometheus must reach the loopback-only Incus API);
  Grafana and the exporter on the PostgreSQL sockets and no TCP listener at
  all (ADR-0010); a second collector instance for the host journal; the VM
  listener address `198.18.0.1` on `pokus-otlp0`; vLLM on the host network on
  `127.0.0.1` only; `current.yaml` rendered next to `current.env` and the
  limits on deployment overrides; immutable deployment files; generated
  superuser and exporter passwords; adapters in `models.lock.yml`; the pinned
  OPA CLI for tests; one GPU service per GPU method; models and vLLM only on
  serving nodes. Findings: libpq ignores the group-readable reader credential
  (researchers copy it to `~/.pgpass`); `pg_hba` cannot tell socket
  directories apart; service accounts now get their own primary groups; the
  run explorer's database panel assumes a table `public.runs` until the
  platform's migrations exist.
- From step 8 ("Findings to report (step 8)" and "Interpretations awaiting
  owner confirmation (step 8)"): the leakage lint's design (baseline of the
  base image, whole-token findings, benign words; ADR-0012) and its default
  allow rules; unpinned distribution packages recorded in `packages.txt`; the
  extra image properties `requirements.nesting` and `description`; `-dirty`
  in `pokus.recipe_commit`; the export layout (`SHA256SUMS`, `build.json`,
  `_upstream/`); importing an export instead of rebuilding; failed builds
  kept for inspection; nesting per build instance; the agent reading the
  whole journal plus per-blueprint log files; each blueprint running only its
  own tests; the role on `full` and `lab` nodes. Findings: no build could be
  run here (no Incus); every build waits for the Incus base image
  fingerprints; in-image files carry the managed-file header that names the
  project, which the default leakage patterns allow.
- From step 9 ("Findings to report (step 9)" and "Interpretations awaiting
  owner confirmation (step 9)"): what a restore brings back (the research
  space directories, and from `/etc/pokus` only vLLM deployments and QA
  files; newer files kept; secrets never restored); the wider set of secret
  paths excluded from backups; the snapshot prompt and `restore_snapshot`;
  the repository password changed only by `rotate.yml` (`restic key
  passwd`); initializing the repository on first use; the SFTP key and pinned
  host keys; the health gate's checks and thresholds (WSL clock within 1 s of
  `pool.ntp.org`); autostart of `pokus-lab.target`; the scope of `rotate.yml`
  (including removing replaced Incus trust entries); schedules and
  retention; backup timers outside the lab target. Findings: nothing could be
  run against real services here; session records and the restore's doctor
  run depend on `pokus-lab` (step 10); a restore drill must disable the spare
  machine's backup timers.
- From step 10 ("Findings to report (step 10)" and "Interpretations awaiting
  owner confirmation (step 10)"): the lab description `/etc/pokus/lab.json`
  as the CLI's only input; package copies (with a copy of preflight) under
  `/usr/local/lib/pokus/`; doctor needing root and exiting 2 when it cannot
  run; which checks are warnings (a fresh lab without a backup repository
  ends bootstrap with 1); the listener allowlist; the telemetry round trip
  and the smoke checks' assertions (VM blueprints skipped); the inference
  project left out of the unit list; `pokus-lab restore -- <options>`;
  `status` needing root for containers and Incus; the reboot notice from
  `/var/run/reboot-required`; `pokus-lab user` refusing host_vars files it
  cannot edit line by line. Findings: nothing ran against a real lab; the
  proposed fingerprint and drain contract additions (the platform must
  implement the drain endpoints); `site.yml` still applies `postgres` and
  `opa` on inference nodes (aligned in step 11); the step 5 sleep inhibitor
  was never enabled (fixed); `pokus-lab docs serve` follows in step 14.
- From step 12 ("Findings to report (step 12)" and "Interpretations awaiting
  owner confirmation (step 12)"): test images resolved at run time with the
  fingerprint recorded; results valid for 90 days shown as a *Valid until*
  date; the stages of a test (first backup before `doctor --smoke`); test VMs
  as staging labs on a test tailnet with an ephemeral key; the harness's
  safety rules; the fast lane's roles, base images (Leap instead of
  Tumbleweed) and profile. Findings: no distribution test has run (no Incus;
  image aliases unverified); `make test-distros` needs the answers replay of
  step 13; the fast lane runs for the first time in CI with this step; no
  derivative target yet.
- From step 11 ("Findings to report (step 11)" and "Interpretations awaiting
  owner confirmation (step 11)"): deployment directory names, refusing moved
  tags, keeping old deployments; SSH deploy keys or anonymous HTTPS only;
  SSH-signed tags; the uv environment and console-script check; schema
  checks with the release's interpreter; socket permissions from a root
  `ExecStartPost=+` helper with the ACL fallback; migrations before `current`
  switches, once per commit; the evaluator without `MemoryDenyWriteExecute`;
  disabling the platform stops but removes nothing; vLLM on loopback behind
  the control endpoint on an inference node; the pairing protocol (P-256
  identities for 3650 days, 60-bit code, HMAC over both fingerprints, five
  attempts, equal `lab_mode`); what an inference node runs and how its
  telemetry reaches the lab node; tailnet addresses read at bootstrap; the
  split-inference firewall ports; doctor on split nodes. Findings: nothing
  ran against a real host; the SSH-signed tag test needs `ssh-keygen`;
  GitHub's SSH host keys are still unverified (SSH sources on github.com are
  refused until then); the contract proposals above; `InaccessiblePaths`
  needs existing paths (unit ordering); existing hosts may recreate the
  collector and store containers once.

## Owner deliverables pending

- Approval of the model candidates in `infra/models.catalog.yml` (owner answer
  E7): four permissive candidates and one restricted one, all `proposed`, with
  `TODO(verify)` revisions because Hugging Face is not reachable from the build
  environment.

- Domain allowlist for the environment (A3); the list is in
  `infra/docs/IMPLEMENTATION_NOTES.md`. Since step 3, GitHub release
  downloads, `api.github.com` and `security.ubuntu.com` are reachable; the
  other listed hosts and `releases.astral.sh` (CPython builds for uv) are
  still blocked.
- The owner supplied the git identity to use (A2); it is set in the local git
  configuration only and not recorded in any repository file. Steps 1-4 are
  committed on `main` (`0503e86`, `8df9199`); those two commits still carry
  co-author and session trailers, which the owner no longer wants, and
  removing them would need a history rewrite that the process forbids.

## TODO(verify)

`make verify-todos` lists every marker in the tree (108 after step 12; details
in `infra/docs/IMPLEMENTATION_NOTES.md`, "Open `TODO(verify)` items after
step 4" through "after step 12"):

- Ubuntu 26.04: Incus, NVIDIA Container Toolkit and Tailscale support
  (`infra/preflight/support-matrix.yml`).
- openSUSE Leap 15.6 end of support (`infra/preflight/support-matrix.yml`).
- 16 endpoint URLs whose hosts are blocked from the build environment
  (`infra/preflight/endpoints.yml`), including `releases.astral.sh`, from
  which uv 0.12.23 downloads the pinned CPython.
- Output formats of `timedatectl timesync-status` and `chronyc -n tracking`
  (`infra/preflight/pokus_preflight/detect/timesync.py`).
- Detection of an incomplete dnf transaction
  (`infra/preflight/pokus_preflight/detect/conflicts.py`).
- 32 lines of `infra/versions.lock.yml`: the DCGM exporter image (nvcr.io);
  Tailscale and Zabbly repository keys; Docker packages for openSUSE Leap;
  Incus and Tailscale packages for every pinned release; Incus base image
  fingerprints; GitHub SSH host keys (api.github.com/meta is not readable
  from this environment).
- Step 5: 7 more lock lines (NVIDIA driver pins for the releases other than
  Ubuntu); 17 `verify` entries of `infra/ansible/component-sources.yml`
  (Tailscale and Zabbly repository layouts; Docker, Incus and NVIDIA package
  names and methods outside Ubuntu); 20 notes in role files (package and
  service names of the Red Hat, SUSE and Arch families, dnf5 and zypper
  behavior, Incus's firewalld/ufw/Docker guidance, the WSL library path, the
  `tailscale serve` status format, the Secure Boot MOK flow); the Windows
  helper `infra/contrib/windows/pokus-wsl.ps1` (not run on Windows).
- Step 6: resolved the three firewall and Docker coexistence notes from
  Incus's documentation; added the Incus group, unit and path names of
  Zabbly's packages and of the Red Hat, SUSE and Arch families
  (`infra/ansible/roles/incus/vars/`).
- Step 7: the revisions of the five candidate models in
  `infra/models.catalog.yml` (with two explanatory lines) and the field names
  of the Hugging Face tree API in `infra/docs/how-to/add-a-model.md` (with the
  catalog warning); Hugging Face is not reachable from the build environment.
- Step 8: nothing added or resolved; the Incus base image fingerprints now
  block every golden image build (images.linuxcontainers.org is not reachable
  from the build environment).
- Step 9: nothing added or resolved.
- Step 10: nothing added or resolved.
- Step 11: nothing added or resolved; vLLM's TLS options, planned as a
  marker, were verified in vLLM's source (and are not used). GitHub's SSH
  host keys (above) now also gate SSH deployments from github.com.
- Step 12: the VM image aliases of `infra/tests/distros/targets.yml`
  (images.linuxcontainers.org is not reachable from the build environment).

## Known issues

- Network access from the build environment is limited (A3); most
  verification hosts are blocked until the owner allows them. The GitHub REST
  API is limited to this session's repositories, so upstream releases are read
  with `git ls-remote`, raw files and release downloads instead.
- Docker Hub answers anonymous manifest requests with HTTP 429 after a few
  requests; `make lock-check` uses HEAD requests and backs off, but CI runs may
  still hit the limit.
- `ansible-playbook` is run with `LC_ALL=C.UTF-8`; its availability on every
  supported family is to be confirmed on real hosts (Phase 5).
- Bootstrap has not been run end to end on a real host (the build
  environment must not be provisioned); since step 10 its last stage runs
  `pokus-lab doctor`. Its stages, the roles and the doctor checks are
  covered by tests that never provision; Phase 5 runs them on real hosts.
- Run networks can be created but not deleted by the platform's restricted
  certificates (see the step 6 contract question); until the owner decides,
  the contract is unchanged and nothing works around it.
- `make setup` also downloads the pinned OPA CLI (`binaries.opa`) from GitHub
  releases and checks its sha256; the OPA policy tests need it.
- `make setup` installs the pinned Ansible collections from Galaxy, which the
  build environment cannot reach; here they were installed from their release
  tags on GitHub into the git-ignored `infra/.ansible-collections/`.
- MkDocs 1.6.1 (pinned) has had no release since 2024-08-30, and the Material
  for MkDocs team announces that MkDocs 2.0 removes plugins. Builds work with
  the pin; the replacement decision belongs to the docs-site plugin review
  (docs spec §B.2) and needs an ADR and owner approval.
- The build environment's default uv is older than the pinned 0.12.23; the
  pinned uv is installed in a user-space scratch directory and put first on
  `PATH` for all make runs. CPython 3.9.25 and 3.12.11 are installed in user
  space by uv.
- mypy 2.x cannot type-check for Python 3.9; the 3.9 compatibility of
  preflight and `pokus-lab` is enforced by ruff (`py39` target) and by running
  their test suites on CPython 3.9 (ADR-0001).
- Preflight fixture paths contain colons (sysfs PCI addresses such as
  `0000:01:00.0`), so the repository cannot be checked out on a Windows
  filesystem; clone it inside WSL or on Linux (the lab is Linux-only anyway).
- Several command output formats are modeled in the preflight fixtures from
  upstream knowledge and must be confirmed on real hosts in Phase 5 (list in
  `infra/docs/IMPLEMENTATION_NOTES.md`).
- CI's `test` and `docs` jobs failed from infra step 7 through step 11
  although `make test docs` passed here: this environment runs as root, CI
  does not. The bootstrap git-state tests and the deploy helper's tests
  assumed root, and the generated `pokus-preflight` reference contained this
  checkout's absolute paths. Fixed after step 11; the suite now also passes
  as an unprivileged user from another checkout path. `lock-check` stays red
  while `TODO(verify)` markers remain (by design).
- The build environment has no `ssh-keygen` and no `setfacl`: the test of
  SSH-signed tag deployments is skipped here (it runs where OpenSSH is
  installed, as in CI), and the socket ACL handling is tested with a fake
  `setfacl` only.

## Next step

Infra step 13: the installer TUI (BUILD_PROMPT §18): Textual screens with the
answers file (`installer/schema/answers.schema.json`, which
`infra/tests/answers/ci.yml` must satisfy), its replay through
`bootstrap.sh --answers` (which also enables `make test-distros` end to end),
secrets into the vault, the Ansible progress callback, resume state, and the
pairing screens for split inference. Then step 14 (documentation completion,
`pokus-lab docs serve`); after step 14 the assistant stops and waits for the
platform phase.
