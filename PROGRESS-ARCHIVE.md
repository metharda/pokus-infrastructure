# PROGRESS archive

Completed plan steps, the owner's answers and the record of each step's
decisions, moved here unchanged from [PROGRESS.md](PROGRESS.md) (owner
decision with the "commit" for 3d.5: nothing is deleted, only moved).
`PROGRESS.md` holds the current state, the remaining plan, open items,
every approved deviation, the process rules in force and the platform spec
changes pending. Links to sections that stayed there point to it; records
written before 3c keep the names of their time.

## Owner answers

Recorded from the owner's answers to the open questions (§5 of the master
prompt). "Approved" means the assistant's stated default is implemented
exactly.

### A. Inputs, process, environment

- **A1** `specs/` copies: approved; byte-identical, sha256 above.
- **A2** Git process: see [Process rules](PROGRESS.md#process-rules-in-force).
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

### I. Contract v2: plug-in platforms (owner decision after infra step 14)

Infra becomes a standalone lab product that any testing platform plugs into
through a manifest, with Pokus as the first platform. Approved overall with
changes; the proposal document itself is kept outside the repository. The
per-section spec changes and the rename table are part of the proposal. The
repository split of decision 12 is replaced by the plan in
[section J](#j-plan-after-infra-step-14-this-repository-becomes-pokus-infrastructure):
no v2 code before the spec update (3b) and the rename (3c).

- **Approved as recommended:** decisions 2 (profiles), 4 (runtimes
  `python-uv` and `static`, builds as `pokus-build`), 5 (hooks as commands),
  6 (`<platform>-migrate` account), 8 (optional pinned tools), 9 (extra
  manifest fields), 11 (`file://` sources), 13 (installer inspects the
  release).
- **1, naming (replaced after infra step 14; also replaces N1):** "Pokus"
  is only the overall project name and no longer a technical identifier of
  either product. Infrastructure product: repository `pokus-infrastructure`,
  display name "Pokus Infrastructure", technical short name `pinka`; infra's
  whole namespace becomes `pinka` (paths `/srv/pinka`, `/etc/pinka`,
  `/var/lib/pinka`, `/run/pinka`, `/opt/pinka`, `/usr/local/share/pinka`,
  `/usr/local/libexec/pinka`; CLIs `pinka`, `pinka-preflight`; groups,
  units, targets, slices, containers, Incus networks `pinka-*`; DB role
  `pinka_monitor`; telemetry `pinka.*`; Python packages `pinka_*`; Tailscale
  `tag:pinka-lab`, `tag:pinka-qa`, default machine `pinka-lab`); this
  repository becomes `pokus-infrastructure` (section J). Platform product:
  repository `pokus-platform` (a new, separate repository the owner starts
  later), display name "Pokus Platform", technical short name `potka`; the manifest's platform name is `potka` and every name
  derived from the platform uses it (accounts `potka-*`, database `potka`,
  roles `potka_*`, CLI `potka`, packages `potka_*`, run attributes
  `potka.run_id`, `potka.node`). Docs say "Pokus Infrastructure (pinka)" and
  "Pokus Platform (potka)"; "Pokus" only for the project as a whole. The
  reserved platform names gain `pinka` and `pokus`; platform objects stay
  `<platform>-<service>` and the schema lists every reserved infra name per
  namespace. The rename is purely mechanical, without behavior change, as
  its own commit (plan item 3c), separate from any functional change,
  before v2 work starts. The neutrality lint rejects
  potka-specific names in infra code outside test fixtures and docs; the
  privacy scan's allowlists follow the rename.
- **3, agent sockets:** a socket marked `untrusted_clients: true` is
  dedicated to `untrusted-agent`, never shared with other profiles or humans;
  the platform spec obliges that its endpoints never return restricted data,
  proven by a platform test; installer and doctor highlight these sockets.
- **7, GPU:** GPU training jobs are a first-class v2 capability:
  - 7.1 an eighth profile `gpu-trainer`, the only one that ever gets GPU
    devices, for the new manifest field `jobs[]` (image pinned by digest,
    pulled at deployment, never at job start);
  - 7.2 a root-owned, socket-activated GPU arbiter (`pokus-gpu-arbiter`,
    `/run/pokus/gpu.sock`) replacing the deploy helper: modes `idle`,
    `serving <deployment>`, `training <run>`, one at a time; `train` only
    with no running or waiting vLLM requests and the controller's sessions
    drained; every transition in `/var/lib/pokus/gpu/transitions.jsonl`;
    admins use `pokus-lab gpu status|serve|train|idle`;
  - 7.3 OCI training containers with options fixed by infra, never from the
    manifest (`--network none`, read-only root, `--cap-drop ALL`,
    no-new-privileges, default seccomp, non-root job account, GPU via CDI,
    limits within infra's caps);
  - 7.4 a datasets area `/srv/pokus/training/datasets/<sha256>/`
    (root:`pokus-datasets`, read-only) published by the arbiter with
    `PROVENANCE.json` (every instance with its split, source runs, builder
    commit); the split guard refuses to publish or mount any dataset that
    lists a `test`-split instance; completeness of the provenance is the
    platform's obligation;
  - 7.5 adapters hashed and registered read-only under
    `/srv/pokus/models/adapters/<name>/<sha256>/` with `LINEAGE.json` (base
    model, dataset and image hashes, job-spec hash, hyperparameters, seed,
    platform and infra commits, times), served as `<name>@<sha256>` in
    `adapter_ids`; adapters and datasets are backed up;
  - 7.6 checkpoints and resume: jobs checkpoint into their run directory; at
    boot a job left in `training` is `interrupted` and resumed from its
    checkpoint only with `resumable: true`, else the arbiter restores the
    previous deployment or stays `idle`;
  - 7.7 a tiny training job in the reference platform, with a CPU-only
    variant (`resources.gpus: 0`) for hosts and CI without a GPU;
  - 7.8 platform-side spec additions: dataset builder with the split guard,
    job submission through the controller, lineage import, base-versus-adapter
    comparison campaigns, and the contamination flag (test results are
    flagged when their scenario template is in the adapter's training data,
    read from the lineage).
- **10, restricted schema:** the revoke and check after every migration is
  the guarantee; the event trigger only after its `GRANT`/`REVOKE` coverage
  is verified on the pinned PostgreSQL release.
- **12, repositories (replaced after infra step 14 by section J):** two
  repositories, as decided: infra with the reference platform, and the
  platform with catalog and the Pokus docs. The way there changed: no new
  infra repository and no `git filter-repo`. This repository becomes
  `pokus-infrastructure`; infra moves to its root at the end (3f) and gets
  `ci.yml` at the root without a wrapper, tags `vX.Y.Z` and its own docs
  site. The platform, catalog and Pokus docs site live later in the new
  repository `pokus-platform`, which the owner starts; its CI validates the
  Pokus manifest against the schema of the infra release it targets, and a
  release candidate becomes an infra release plus the platform and catalog
  commits there. Infra publishes contract v2, the manifest schema and the
  profile table with each release, replacing the byte-identical
  `contracts.md` copies (C1).
- **Additions:** the v1 agent-runner finding (TCP access, `pokus-svc`
  membership) is not patched separately in v1; v2 step 3 includes tests
  proving both are closed. The neutrality lint matches an explicit name list
  as whole words. Out of scope for v2, recorded: an OCI runtime for platform
  services, Incus fine-grained authorization, several platforms at once.
- **New points, decided after infra step 14:** N1 is replaced by the naming
  decision above. N2 approved: every socket the agent may use is dedicated to
  it, including the broker and sandbox-operator sockets; the executor gets
  separate agent and orchestrator sockets. N3 approved for infra: the
  training datasets area belongs to `pinka-datasets` with no human members;
  platform addition (platform §4.1 and §4.9): `potka datasets inspect <hash>`
  shows provenance and sample records through the controller, is allowed
  only for `pinka-admin` members, and is logged as an event. N4 approved: in
  v2, training runs only on `node_role: full`; on lab + inference topologies
  the installer and doctor state clearly that training is unavailable there,
  and the docs record split-node training as future work.
- **Rename decisions (owner, on the rename table's open questions):**
  - Approved as recommended:
    - Q1: header `Managed by pinka (<path>)`, also the `org.pinka.managed`
      label value.
    - Q2: `pokus-lab` becomes the CLI `pinka`, the machine name and
      Tailscale tag `pinka-lab`, `pinka-lab.target`, the package `pinka_lab`
      in `tools/pinka-lab/`, `PINKA_LAB*`, `Start-PinkaLab` and
      `Stop-PinkaLab`, and the reference page `reference/cli/pinka.md`.
    - Q3: `pokus-infra` in derived names becomes `pinka`: `pinka-gen`,
      `pinka_gen`, `pinka-dev`, `pinka-doc-tools`, `pinka_dir`.
    - Q4: the v1 platform groups become `potka-svc`, `potka-exec-clients`,
      `potka-gw-clients` (`POTKA_SVC_GID`, `potka_svc_gid`,
      `compose_potka_svc_gid`); N2 replaces them in v2.
    - Q5: `pokus-gt` becomes `pinka-gt`, then `pinka-restricted` in v2.
    - Q6: `pokus_grafana` becomes `potka_grafana`, its secret
      `/run/secrets/potka-grafana`.
    - Q8: `pokus-run-explorer` becomes `potka-run-explorer` and moves to the
      platform's dashboards in v2.
    - Q9: the bare mirror becomes `/var/lib/pinka/git/potka.git`; v2 keeps
      one mirror per source.
    - Q10: platform-derived names in infra's v1 code become `potka` at the
      rename; the neutrality lint arrives with v2 step 3 and then forbids
      `potka` and `pokus` in infra code outside test fixtures and docs.
    - Q12: the lock-check test key "Pokus Test Key" stays.
    - Q13: the infra spec master is renamed
      `pokus-infrastructure-claude-code-prompt.md` (at 3f).
    - Q14: prose is reviewed sentence by sentence: infra "Pokus
      Infrastructure (pinka)", platform "Pokus Platform (potka)", the
      project "Pokus"; "the lab" stays a common noun.
  - Q7, approved as the permanent design: OPA's authz allows exactly two
    decision paths, the platform's (`potka.action` now, the manifest's path
    in v2) and infra's `pinka.placeholder`, nothing else. A test proves it.
  - Q11: no copy of the privacy scan now; it stays in this repository with
    infra and is handled when `pokus-platform` is created.
  - Q15: the working branch stays `claude/zen-albattani-qfwwps` (until 3g;
    then `moving/on` in `pokus-infrastructure`, owner decision with the
    "commit" for 3d.3).
  - The rename follows the rename table's rules: no behavior change;
    generated files regenerated, never hand-edited; verbatim quotes, commit
    history, upstream names and the spec copies unchanged; the image
    leakage lint forbids `pokus`, `pinka` and `potka`; the installer's
    username check refuses `pinka-`, `potka-` and `pokus-`; a residue check
    lists the remaining `pokus` uses and allows only the project name in
    prose, the repository names, the reserved-name list, verbatim quotes and
    the Q12 test key.
- **Proposal status:** the proposal (decision table, profile table with
  `gpu-trainer` and the arbiter, security model and threat table, training
  flow, repository split, test plan, effort), the per-section spec change
  list for infra, platform and docs, and the rename table with its open
  questions were delivered after infra step 14 and decided as recorded
  here. Nothing of v2 and nothing of the rename is implemented; the order
  of work is section J.

### J. Plan after infra step 14: this repository becomes pokus-infrastructure

The plan's introduction and its remaining items (3d, 3e, 3f, 3g, 3h) are
in [PROGRESS.md, Remaining plan](PROGRESS.md#remaining-plan). The
completed items and the records of each step follow.

- **3a** `PROGRESS.md`: the final commit rule, the rename decisions and this
  plan; the root `CLAUDE.md` states the same commit rule (owner, after 3a
  was proposed: not deferred to 3f), in the same commit.
- **3b** Spec update: the owner authorized the assistant to edit the spec
  masters in `specs/` for this one change: apply the proposal's Spec changes
  list exactly (contract v2 including decision 7's training, N2-N4, the
  pinka/potka naming and the rename decisions) to the infra prompt, the
  platform prompt and the docs spec. Spec changes only, no code. The copies
  in `infra/docs/` stay byte-identical; the new sha256 values are recorded.
  The commit proposal comes with a section-by-section summary of the diff.
  After that commit the specs are read-only again.
- **3b2** Spec edit before 3c (owner decision after the 3b commit; the
  specs are read-only again after its commit), limited to: the proposals of
  steps 7-11 decided as below (2, 3, 4, 6, 7), items A-D, and the isolation
  probe per profile. 3c starts only after this commit and after the open
  interpretations (`infra/docs/IMPLEMENTATION_NOTES.md`) are decided.
- **3c** Rename: the mechanical `pokus` → `pinka`/`potka` rename in `infra/`,
  per the rename table, with the residue check; no behavior change.
- **3c2** Images free of project names (owner decision): a separate small
  commit right after 3c drops the managed header from files installed into
  images and adds `pokus`, `pinka` and `potka` to `images_leakage_patterns`
  (infra §7.13: target images carry none of the three strings).
**3b notes (spec update).** Question **S1** (the Pokus Platform's executor,
a `sandbox-operator` service, runs the agent's `query_logs` and
`query_metrics` tools) is resolved by the owner's review, item K2: every
telemetry query goes through infra's read-only query proxy, and
`sandbox-operator` gets "send; query (via proxy)". Choices made while writing
the spec text, beyond the change list:

- The Pokus manifest's service names are `orch`, `agent`, `gw`, `exec`,
  `inject`, `verify`, `eval`, so accounts and roles keep the v1 suffixes
  (`potka-orch`, `potka_orch`); units become `<platform>-<service>.service`.
  The change list's "eight services" counted reconcile, which is now a hook:
  Pokus has seven services. Its training job is named `lora`.
- The agent runner holds no gateway credential: the `untrusted-agent`
  profile allows no secrets, so the gateway identifies it by peer
  credentials on its dedicated socket and by the session token. The gateway
  header becomes `X-Potka-Session`.
- From the proposal, not listed in the change list: restore redeploys the
  platform and re-registers adapters (§7.15); one signing key per platform
  (§8.2, §12.10); `pinka update-host` runs the `drain` hook and arbiter
  `idle` (§19.5); `pinka platform purge` (§9); the run explorer and
  dashboards move to the platform repository (platform §18).
- The installer's GPU & inference screen is screen 9 in the infra prompt
  (the change list said 7); the new Platform review screen makes the flow 19
  screens. The external verification repository keeps the name `pokus-qa`
  (review item O1); only the Tailscale tag becomes `tag:pinka-qa`.
- Example values in `host_vars` (tag prefixes `platform-v`, `catalog-v`,
  path `platform/`, training caps) are placeholders.

**Owner's review of the first 3b draft** (applied in the working tree, not
committed): K1 the platform repository layout (`platform/` and `catalog/` as
separate subtrees) and the refusal of overlapping release and content paths
from one repository; K2 the read-only telemetry query proxy everywhere,
Grafana's datasources included, with the owner's order of preference for
keeping the backends out of reach (decided at implementation in an ADR) and
a required test in the reference platform's isolation probe; K3
every approved deviation folded into the spec text; O1 the external
verification repository is `pokus-qa`; O2 the attestation format defined in
the platform spec (§12.2); O3 `potka catalog show` through the orchestrator
API, `pinka-admin` only, logged; O4 Grafana mounts the reader socket
directory, no PostgreSQL TCP listener; O5 the NVIDIA Container Toolkit also
for `gpu_training`; O6 the neutrality lint with the `pokus` allowlist,
for repository code only (target images, the platform's trace-cleanup scan
and its ticket lint allow no `pokus`, `pinka` or `potka` string at all); O7
deployment specs named `<id>@<version>.yml`; and the minor items (docs spec
A.1, the infra §9 table rows, the duplicate uv install in infra §5.2,
`require_signed_tags: false` in the example, `QA=` optional).

The owner also confirmed the threat coverage (infra §15, §11, invariant
21) and two interpretations: the SMT warning (siblings of one core in
different CPU sets) and the platform's `docs/` inside `platform/`. The review
also approved the four pending proposals of the report after infra step
14. They had not been recorded as approved before; they are listed as
P3a-P3d under [Approved deviations](PROGRESS.md#approved-deviations) (P for
"proposal", to keep them apart from plan items 3a-3h).

Approved deviations deliberately not folded into the spec text, with the
reason:

- A2 (the assistant runs the approved commit commands): a process rule,
  replaced by the final commit rule in this file and the root `CLAUDE.md`.
- B2 (bare mirror and `git archive` of one subtree): already in the spec
  through contract v2 (infra §7.14).
- B3, B5, C1 (dirty check and tree hash limited to `infra/`, root CI
  wrappers, `platform/docs/contracts.md` copy): monorepo arrangements that
  end with 3f; the spec text describes the infra-only repository, and under
  contract v2 the platform keeps no copy of the contract.
- B4 (one repository's commits as the release candidate): replaced by
  contract v2's release candidate (infra release, platform release, catalog
  content package; infra §12.10, platform §12.2).
- B6 (catalog lint with a `../platform` path dependency): a monorepo
  arrangement; in `pokus-platform` the catalog has no code of its own.
- C2, C3, C8 (agent on `svc.sock`, the deploy helper, socket groups and
  ACLs): superseded by contract v2's dedicated agent sockets (N2), the GPU
  arbiter and the socket rendering of infra §12.3.
- C5, C9b, C9f-C9h, D1, I2: already in the spec text.
- D4 (`qa:run` markers linted only here): describes this repository's role;
  the docs spec's rule that QA bots execute them stays true for `pokus-qa`.
- F9 (`catalog show` for restricted members): superseded by review item O3.
- H7 and H11 (Grafana screenshot pending, versioned docs shown with two test
  versions): interim states, not spec changes.
- The contract proposals of infra steps 7-11 that were never approved (the
  image property `requirements.nesting`, the session record's fields, the
  fingerprint document, the inference control API, `current.yaml`, the
  gateway's ACL on the mTLS material, the `LoadCredential=` question): they
  stay proposals; the lab drain on `api.sock` is replaced by the `drain`
  hook.

Not implemented yet, to be built with contract v2 (infra §16, step 18,
which is step 4 of plan item 3d): the query proxy, the port overrides and
the preflight port and VM-address checks, the docs service (it replaces the
foreground `docs serve` command of step 14), maintenance windows, the SMT
warning, and the reader-socket mount for Grafana. Added by 3b2: the
run-network helper (§16 step 17), the driver CUDA check and the record
schemas in `contract/records/` (step 18), the inference forwarder, the
control API's `POST /activate` through the arbiter and the inference API's
reachability (step 19), and the isolation probe per loopback-capable
profile (step 20); the drain hook replaces the `/lab/drain` calls (step 18).

**Decisions after the 3b commit (owner).** On the contract proposals of
infra steps 7-11 (details in `infra/docs/IMPLEMENTATION_NOTES.md`, steps
7-11):

1. `current.yaml` next to `current.env`: approved as an infra-internal detail
   (approved deviation St7-1).
2. Image property `requirements.nesting`: approved as the fourth image
   property of the contract (3b2: infra §7.13, §12.6; platform §3).
3. Session record: approved; its JSON Schema is published in
   `contract/records/` (3b2: infra §7.16, §12.10; platform §3, §6).
4. Fingerprint document: approved as the contract v2 format, its schema in
   `contract/records/` (3b2: infra §10.2, §12.10; platform §3).
5. `POST /lab/drain` and `GET /lab/status` calls: reverted in 3d step 4; the
   `drain` hook replaces them.
6. Inference client key: option (a), a root-run local mutual-TLS forwarder
   on lab nodes; `controller` and `inference-broker` stay loopback-only (3b2:
   infra §12.2, §12.8, §12.11, §19.10, invariant 22; platform §3). The
   gateway's ACLs on `/etc/pokus/inference/` from step 11 go in 3d.
7. Inference control API: approved, with `POST /activate` sent to the
   inference node's GPU arbiter as `serve <id>@<version>` (3b2: infra §12.8,
   §19.10; platform §3, §4.1).
8. The agent's secrets: closed; the validator refuses `secrets` on an
   `untrusted-agent` service and infra creates no secrets directory for it
   (3d).

Further items (owner, folded in by 3b2): **A** a root-run, socket-activated
run-network helper for the controller only (infra §7.7, §12.2, §12.6,
invariant 23; platform §3, §4.1, §4.2, §5); **B** vLLM reachable only by
`inference-broker`, `controller` and infra's own checks, by the mechanism of
K2 (infra §7.11, §12.2, invariant 22); **C** preflight and doctor check that
the NVIDIA driver supports the pinned vLLM image's CUDA version, with a
remediation (infra §2.2, §2.3, §9, §10.1, §11); **D** the
`debian13-baremetal` fixture is `supported` (infra §11); and the isolation
probe runs for every loopback-capable profile as a transient unit with that
profile's rendered hardening (infra §7.18, §11). Also decided: the image
header fix as 3c2; from step 2, Debian 12 removed from `tested` and an
explicit unsupported row per implemented distribution for unlisted releases
(approved deviations St2-1, St2-3). With the owner's approval of 3b2, item A
was broadened: doctor and the platform's integrity check flag any Incus
network that neither infra created nor the helper's log records, whatever
its name (infra §7.7, §11).

**Interpretations (owner, after the 3b commit).** Every interpretation that
`infra/docs/IMPLEMENTATION_NOTES.md` lists as "awaiting owner confirmation"
in steps 2-14 and the follow-ups after step 13, and that contract v2 does
not supersede, is approved as recommended: 2.4-2.6, 3.1-3.6, 3.8-3.10,
4.1-4.4, 5.1-5.7, 6.1-6.6, 6.8, 7.1, 7.3, 7.7, 7.8, 7.10-7.12, 8.1, 8.2,
8.4-8.11, 9.1-9.11, 10.1-10.12, 11.1, 11.3, 11.5, 11.6, 11.8, 11.10-11.14,
12.1-12.6, 13.1-13.8, 13.10-13.12, CPU sets 1, 2 and 4, and 14.3. With
these notes and one change:

- 3.1: ADR-0002 is accepted (its status is updated with 3b2).
- 3.4: the state file at every exit is approved; "the official check
  requires `result` success" is a request for the next platform spec edit
  (below).
- 4.2: the CUDA 13.0 vLLM image stays; item C makes a too-old driver visible.
- 6.3: the managed `daemon.json` label becomes `org.pinka.managed` in 3c.
- 7.1: Prometheus, Loki and Tempo follow the K2 mechanism and its ADR; the
  collector, Grafana and the exporters stay on the host network.
- 9.7: the health gate's thresholds stay; its facilities follow `requires`
  in 3d. 10.5: the listener allowlist gains the non-loopback block of infra
  §12.11. 10.6: the telemetry round trip goes through the query proxy.
  11.3: the console-script check becomes the manifest command check. 11.5:
  socket expectations come from `principals.json`.
- 11.13, changed: once paired, the split-inference ports accept only the
  paired peer's tailnet address; the pairing port is open only while pairing
  (built in 3d with the inference forwarder).

Superseded by contract v2, reworked in 3d: 7.9 (adapters come only from the
GPU arbiter; adapter entries leave `models.lock.yml`), 11.7 (the evaluator
uses the `wx-memory` relaxation), 14.1 and 14.2 (the docs service, P3c) and
14.4 (maintenance windows, P3d). Decided earlier, not repeated: 2.1-2.3,
4.5 (A3), 5.8 and both check-mode items (I2), 7.2, 7.4-7.6, 8.3, 11.2,
11.4, 11.9, 13.9, CPU sets 3; obsolete: 3.7, 3.11, 6.7.

**Requests for the next platform spec edit** (the platform spec is
read-only; the owner keeps its master for `pokus-platform`):

- The official check requires `result` success in
  `/etc/pinka/bootstrap-state.json` (interpretation 3.4); whether
  `doctor_warnings` also counts is decided in that edit.
- V3 (owner decision with the "commit" for 3d.3): a `python-uv` release
  locks its build backend in a `[dependency-groups] build` group and every
  dependency is available as a wheel; infra builds it in two phases (locked
  wheels with network, then the release built offline).

**3c, the rename (committed `63b7e56`).** `infra/` follows the rename table: 149 paths
moved with `git mv`, every generated file regenerated, the spec copies
untouched; the residue check (`git grep -n -i pokus` in `infra/` and
`.github/`) lists only allowed uses: the project name in prose ("Pokus",
"Pokus lab"), the repository names, the refused username prefix `pokus-`
and the Rego test that the old decision path is rejected, the spelling
dictionary, the step 8 note on the word itself and the test key (Q12). Behavior changes are only the two the owner decided: OPA's
authorization allows exactly the two decision paths (Q7, with a test), and
the installer refuses the prefixes `pinka-`, `potka-` and `pokus-`. The
interpretations and findings are in `infra/docs/IMPLEMENTATION_NOTES.md`,
"Rename (plan step 3c)"; in short: "Pokus lab" stays the project's lab;
markers left on hosts say `pinka` (Q1); Incus client certificates carry
their name (`potka-*`, `pinka-metrics`); found, not fixed: doctor's
firewalld check looks for a zone `pinka` that the firewall role never
creates (fixed in 3c3, owner decision). Records in this file written before 3c keep
the names of their time.

**Owner decisions with the "commit" for 3c.**

- The five rename interpretations of `infra/docs/IMPLEMENTATION_NOTES.md`
  ("Rename (plan step 3c)", items 1-5) are approved.
- The firewalld zone bug is fixed as its own small commit right after 3c2
  (3c3), not in 3d.
- The secret `POKUS_PRIVACY_DENYLIST` never existed (the 3c record wrongly
  assumed it). The owner creates `PINKA_PRIVACY_DENYLIST` as a repository
  secret; 3c2 switches the workflows to it, and until it exists the scan
  runs without a denylist, as it has so far. The owner received the value's
  format and the local commands for the working tree and the whole history
  (also in `infra/README.md`, "Privacy", for the working tree).
- Scoped commit exception: see [Process rules](PROGRESS.md#process-rules-in-force).
- Order: 3c2 → firewalld fix (3c3) → 3d steps 1-8, one at a time → 3e →
  3f. Each 3d step: built, `make lint test docs` green, documentation
  alongside, this file updated, then a stop with a summary (what was built,
  every implementation choice the specs left open with the choice and its
  alternatives, anything touching security, ground-truth blindness,
  isolation or the contract, test results, remaining `TODO(verify)`) and a
  commit proposal that waits for "commit".
- Outside 3d, the assistant stops and asks (questions collected and sent
  together) only when something would deviate from the specs, or the specs
  do not decide it and the choice affects security, ground-truth blindness,
  isolation, the contract or data safety; when a test or check needs a VM,
  a real host, Incus, Docker or a GPU (what and where, never provisioning
  this machine); or when a failing check could only be fixed by weakening a
  test or relaxing a spec limit.
- After 3f: stop and ask the owner to rename the repository to
  `pokus-infrastructure` on the Git host (3g); after the confirmation, 3g
  and 3h under the normal commit rule, then stop. The platform is not
  started. (Changed with the "commit" for 3d.2: a fresh repository instead
  of the rename, see 3g above and
  [Process rules](PROGRESS.md#process-rules-in-force).)

**3c2, images free of project names.** `images_leakage_patterns` gains
`(?i)pokus`, `(?i)pinka` and `(?i)potka` (no allow rule); the files
installed into images (`infra/images/*/files/`) lose the managed header;
the workflows read the secret `PINKA_PRIVACY_DENYLIST`. Details in
`infra/docs/IMPLEMENTATION_NOTES.md`, "Images free of project names".

**3c3, doctor's firewalld check.** Doctor's `network.firewall` check now
asks firewalld for the two zones the firewall role creates,
`pinka-tailnet` and `pinka-run`, instead of a zone `pinka` that never
existed; a test keeps doctor's zone list equal to the role's.

**3d, step 1: the contract core** (`912b240`). `infra/contract/` (the
manifest's JSON Schema, `profiles.yml`, the generated `reserved-names.yml`)
and `infra/tools/platform/` (`pinka-platform validate`: strict YAML, schema,
invariant checks, derived names against the reserved lists, each other and
the host, release directory checks; renderers of `principals.json` and
`endpoints.toml`), with 227 tests, generated reference pages and a how-to
for platform developers. Choices and interpretations are in
`infra/docs/IMPLEMENTATION_NOTES.md`, "Contract v2, step 1".

**Owner decisions with the "commit" for 3d.1.** All interpretations of
3d.1 approved (the own schema validator, names, caps, the post-build
command check, the policy path, `cli.run`, the literal socket table, the
same-platform host exemption, the `endpoints.toml` and `principals.json`
layouts, the signing key at `/etc/pinka/signing/<platform>/signing.key`).
Added in step 2: a test that fails when the manifest schema uses a keyword
the validator does not implement, and a cell-by-cell drift test of
`profiles.yml` against the profile table of infra §12.2. Both findings
approved as stricter validation, recorded as approved deviations V1 and V2
for the next spec edit. The permanent no-attribution rule, the privacy
scan's history check and the one-time rewrite are under
[Process rules](PROGRESS.md#process-rules-in-force). Answer to "are `0503e86` and
`8df9199` reachable from main": yes, both; `0503e86` is `main`'s root
commit, so a rewrite of the working branch changes every commit hash and
leaves `main`'s copies with their trailers unless `main` is replaced too.

**3d, step 2: deployment** (`9ece578`). Infra §16 step 16: the deployment
engine in `infra/tools/platform/` (sources, root-only mirrors per
repository, staging with `git archive`, validation with the one-platform
rule, the sandboxed `python-uv` build as `pinka-build`, job image pulls by
digest, read-only publishing with `DEPLOYED_FROM.json`, `active.json`,
retirement on `--replace-platform`, purge), the `platform` role ordering
those steps, bootstrap's `--inspect-platform`, `--replace-platform` and exit
code 65, `pinka platform purge`, the record schemas in
`infra/contract/records/`, `pinka-restricted` (was `pinka-gt`),
`pinka-build`, the new layout entries, and the step-1 follow-ups (keyword
and §12.2 drift tests, V1, V2). The contract v1 `deploy_source` and
`catalog` roles are gone; contract v1 rendering stays unused until 3d.3.
Choices, interpretations and findings: `infra/docs/IMPLEMENTATION_NOTES.md`,
"Contract v2, step 2".

**Owner decisions with the "commit" for 3d.2.** Approved: where 3d.2 stops,
65 for refusals and 60 for failures, unchanged pre-build files, the purge
order, the exact-name replace, restricted publishing, V1 and V2. Asked for
in step 3:

- purge keeps accounts, secrets and published signing keys by default, prints
  what remains, and gets `--remove-secrets` for the secrets directories;
- releases, content packages and build output refuse device files, FIFOs,
  sockets and any setuid or setgid bit, with one test per case;
- a proposal for a two-phase build (a fetch phase with network that runs no
  package code, locked hashes only, then a build phase without network), or,
  if uv cannot do that cleanly, why not and the residual risk in
  `infra/docs/IMPLEMENTATION_NOTES.md`;
- the signed-tag test runs in CI (`ssh-keygen` present there); it may skip
  only locally.

3g changes to a fresh repository with a rewrite of the full history (see
[Process rules](PROGRESS.md#process-rules-in-force) and 3g above).

**3d, step 3: rendering from the profiles** (`212121c`).
Infra §16 step 17: `pinka-platform deploy render` computes from the
manifest, the profiles and the host settings everything the platform role
applies: accounts and groups with the memberships the profiles allow (the
§12.4 refusals in the tool), directories, the services' configuration
(`platform_settings`, checked against the release's schemas) and secrets
(from the vault; a missing key stops bootstrap with 77), hardened service
units and socket units (sockets by socket activation, ACL entries for the
human groups, the agent's empty lab directories with only its allowlist
bound in), the database with its roles and the closed `restricted` schema
and its check (run against a real PostgreSQL server in the tests), the
Incus certificates, the run-network helper (socket-activated root helper
with fixed bridge settings and its log), OPA's decision path and bundle, the
dashboards with their datasource, the signing key, `principals.json` and
`endpoints.toml`, and the CLI wrapper and link; `rendered.json` lets the
other roles and replacing use the same result. Units are installed, not
started (3d.4). Every platform special case left infra's code and tests;
the neutrality lint (`make lint-neutral`) keeps it so. The 3d.2 follow-ups
are in: special files and setuid/setgid bits refused, the two-phase build,
purge keeps secrets unless `--remove-secrets` and lists what remains, and
CI asserts `ssh-keygen`, the PostgreSQL server binaries and
`systemd-analyze`. Choices, interpretations and findings:
`infra/docs/IMPLEMENTATION_NOTES.md`, "Contract v2, step 3". Proposed
deviations for the owner's decision: V3 (a `build` dependency group for
releases with a `[build-system]`, and wheels only), V4 (no
`RuntimeDirectory=` in the §7.14 baseline: socket units create the socket
directories) and V5 (the installer and its answers files are excluded from
the neutrality lint until 3d.7).

**Owner decisions with the "commit" for 3d.3.** V3 approved, to be carried
into the platform spec as well (wheels only, the build backend locked in a
`build` dependency group). V4 approved on one condition: a service that
needs its own runtime directory gets one at a separate path that contains no
sockets. V5 approved only as an explicit, named exclusion in the lint
configuration with a comment pointing to 3d.7, which must remove it (a
required item of 3d.7 in the plan above). Ground-truth blindness, answered
before 3d.4 and fixed in 3d.4 if needed: the agent must see only the active
release, resolved at render time; what a published release can contain
besides code, and whether anything of a content package, scenario,
injector, verifier or evaluator is visible to the agent, with a proposal to
narrow its view and a test that fails if anything outside that set is
visible. Follow-ups for 3d.4: sockets whose ACL fell back to 0666 recorded
in `rendered.json` and reported by doctor as warnings; the restricted-schema
SQL tests in CI against PostgreSQL 18 from PGDG (16 only if it costs
nothing); `make setup` verifies the lint collections against the lock and
reinstalls them when they differ, and every formatter and linter excludes
`infra/.ansible-collections/`. The branches of the new repository are under
[Process rules](PROGRESS.md#process-rules-in-force).

**Answer on ground-truth blindness (before 3d.4).** A published release is
the whole `platform.path` subtree plus the built `.venv`: besides the code it
can hold the manifest, `pyproject.toml`, `uv.lock`, docs, config schemas,
the policy bundle, dashboards, migrations, any data files the platform keeps
there and `DEPLOYED_FROM.json`; content packages are never in it. Through
its allowlist (`releases/`, bound whole) the agent of 3d.3 sees every
published release; for the Pokus Platform that is the injector with its
fault primitive library (`potka-primitives`: what each fault does and
touches, the "injection details" of the platform spec's first principle),
the verifier with its probes, the evaluator, the migrations (the ground
truth tables), the policy bundle, the docs and the dashboards, all also
importable from the shared `.venv`. Neither restricted nor non-restricted
content packages are visible. Also found: the agent unit lacks the
`InaccessiblePaths` of infra's internals outside the four lab directories
(`/var/lib/incus`, `/var/lib/docker`, the Docker sockets), which §7.14 hides
from every profile. Proposed fix (V6, in 3d.4): an untrusted-agent service
names its workspace package (`python-uv`) or its subdirectory (`static`);
the build gives it an environment of its own with only that package and its
locked dependencies; the agent sees only that directory of the active
release (the resolved path), its configuration, its dedicated sockets, the
agent OTLP socket and its state, and infra's internals are hidden from it;
a test fails if anything else is visible.

**3d.4 status of ground-truth blindness.** Done in 3d.4: the agent binds
only the release being deployed, resolved at render time (never the
`current` link or another published release), runs from it, and infra's
internals outside the lab directories are hidden from it; a rendering test
lists everything the agent may see and fails for other releases, content
packages, other services' state, configuration, sockets and runtime
directories, secrets and infra's internals, and doctor's
`sockets.agent_view` lists the sockets the running agent can see. Waiting
for the owner: V6 proper, the narrowing to the agent's own package and
environment, which needs a manifest field for `untrusted-agent` services
and a separate environment from the build (a contract change).

**3d, step 4: operations** (`9b932f6`). Infra §16 step 18 and the 3d.3
follow-ups, as summarized in `infra/docs/IMPLEMENTATION_NOTES.md`,
"Contract v2, step 4".

**Owner decisions with the "commit" for 3d.4.** V6 approved, as its own
step 3d.4b before 3d.5: an `untrusted-agent` service names its own package
in the manifest; the build gives it a separate environment with only that
package and its locked dependencies; the agent binds only that package and
that environment, not the rest of the release (no other packages,
migrations, policy bundle, docs or dashboards). The tests must prove both
that these files are not visible and that the injector, primitives,
verifier and evaluator modules cannot be imported from the agent's
environment. A manifest whose agent package depends, directly or
transitively, on another package of the same release is refused. Recorded
as V6, with the schema, the how-to and the reference pages updated.
ADR-0016: the alternative is chosen; only an explicit allowlist of infra
UIDs (the collector, Grafana, the query proxy, doctor and whatever else
infra itself needs) may reach the telemetry stores directly; everyone else,
researchers included, goes through the read-only query proxy, with a test
per account class (3d.4b). Restore redeploys exactly the commit and tree
hash recorded in `DEPLOYED_FROM.json` and refuses if the mirror no longer
has that commit or the tree hash differs (3d.4b). V7: the supported GPU
minimum is compute capability 7.5, matching the vLLM image, and preflight
refuses anything below it; support matrix and docs updated (3d.4b). The
query socket link is approved, provided doctor checks the link's target and
the owner of the target directory. All other choices of 3d.4 approved.

**3d, step 4b: the owner's 3d.4 decisions** (`9c152a3`).
V6: the agent's own package (`package`), its own environment
`.agent-venvs/<service>/` with only that package and its index
dependencies, the agent binding nothing else of the release, the refusal of
a dependency on the release's other packages, and tests for visibility and
imports (the import test builds a workspace with the pinned uv). ADR-0016
accepted with the allowlist: the store containers run as infra accounts of
their own, the guard admits only infra's clients, a kernel test per account
class. Restore pins the recorded commits and trees and checks them before it
stops anything. V7: compute capability 7.5. Doctor checks the query socket's
link and directory. Choices, interpretations and findings:
`infra/docs/IMPLEMENTATION_NOTES.md`, "Contract v2, step 4b".

**Owner decisions with the "commit" for 3d.4b.** Grafana comes off the
telemetry allowlist, as the first item of 3d.5: its data sources point at
the read-only query proxy, not at the stores, so no Grafana user (editor or
admin included) reaches a push, admin or delete API through Grafana's data
source proxy or a new data source; the allowlist is then the collector, the
stores where they talk to each other, the query proxy and root; the kernel
test, doctor's check and ADR-0016 follow. The firewall rule matches by UID
only, never by GID, so Grafana's group 0 admits nothing; a test shows that
an account with GID 0 outside the allowlist is reset. Approved: strict
dependency sources, `package` as a directory, restore through signed tags,
a GPU below 7.5 as degraded, and the two stricter checks (no shipped
`.venv`/`.agent-venvs`; the `build` group for members with a build system).
`make setup` installs the pinned uv locally so the V6 uv tests run instead
of skipping; in CI a skipped uv test fails the job. Every approved deviation
the platform must follow is listed under
[Platform spec changes pending](PROGRESS.md#platform-spec-changes-pending); the owner
folds them into the platform spec.

**3d, step 5: the GPU arbiter and training** (`3da2671`). First
the 3d.4b decisions: Grafana off the store allowlist (its own set
`query_port_clients` admits it to the query proxy's port only), the guard
matching by UID only with a GID-0 kernel test, `make setup` installing uv
0.12.23 into `infra/build/tools/uv`, and a skipped uv test failing CI. Then
infra §16 step 19: the root-owned GPU arbiter replacing the deploy helper
(modes, transition log, boot handling, `pinka gpu`), vLLM started only
through it, training runs in fixed containers, published datasets with the
split guard, registered adapters with lineage and `adapter_ids`, the
control API's `POST /activate` through the arbiter, the lab node's inference
forwarder, the inference API's reachability as a UID allowlist (ADR-0016,
decision 6), and six doctor checks. Choices, interpretations and findings:
`infra/docs/IMPLEMENTATION_NOTES.md`, "Contract v2, step 5".

**Owner decisions with the "commit" for 3d.5.** Approved: the inference
API's UID allowlist, the arbiter's process model, the job interface
(mounts, environment, adapter path), the run ID format and the topology;
`training_caps` checked at submission, with a warning from validation and
doctor when a deployed job's resources exceed the host's caps; the
platform impact (the arbiter's protocol, the job interface, the inference
API's clients), marked approved under "Platform spec changes pending". For
3d.6, each with tests: size caps (a maximum dataset size and a maximum
combined size of `/output` and `/checkpoints` per run in `training_caps`,
with conservative defaults; free disk space checked before a run starts; a
run stopped and recorded as failed when a cap is exceeded; `/tmp` in the
job container a size-limited tmpfs counted against the job's memory) as
its first item; `pinka start` and a reboot resume a resumable interrupted
run alike, logged as a transition; the private key 0600 root on a lab
node, `root:pinka-inference` 0640 only where `pinka-inference` needs it;
`pinka platform purge` reloads the inference guard; the hash format
specified and tested (paths sorted bytewise, C locale, the exact line
format). Token efficiency (verbatim in the process rules of
[PROGRESS.md](PROGRESS.md#process-rules-in-force)): a quiet gate by default,
only the relevant tests while building a step, this archive, `CLAUDE.md`
with rules only, the specs read by section; as one small commit after the
3d.5 commit, under the normal rule.

**3d.6** (infra §16 step 20 and the owner's 3d.5 decisions), committed
`a686e28`: size caps in `training_caps` (`dataset_max`, `run_storage_max`,
`tmp_max`) with a disk check before each run; resume after `pinka start` as
after a reboot, logged as a transition; the lab node's key 0600 root; the
inference guard reloaded on purge; the tree hash format (paths sorted
bytewise); validation and doctor warning on jobs above `training_caps`; the
SMT warning of P3a; 11.13 (the split guard: only the paired peer's address
after pairing, the pairing port only during the pairing window, with the
test that an unpaired tailnet address is refused); the setup tooling (uv
bootstrap, Galaxy fallback with `TODO(verify)` pins). Step 20: the reference
platform `refplat` (`infra/tests/reference-platform/`) with `tune` and the
CPU-only `tunecpu`, its content package and tests; the isolation probe of
its agent and per loopback-capable profile; the extended check-mode job with
`refplat` enabled; the `make test-distros` stages `platform`, `isolation`,
`training`, `resume`, `redeploy`, `replace` and `restore`. Fixed: the
arbiter's platform name in lineage records. Choices and findings:
`infra/docs/IMPLEMENTATION_NOTES.md`, "Contract v2, step 6".

**Owner decisions with the "commit" for 3d.6.** Approved: the job image
from the ECR Public mirror, pinned by digest, provided the digest is the
official image's (checked on 2026-10-15: Docker Hub's `library/python`
serves the same digest, and its tag `3.12-slim-trixie` points to it); the
vLLM stand-in during the isolation stage; the renamed copy from a
repository of its own; the harness writing host_vars and the vault until
3d.7; the root-only `resume` key of `idle`; the platform spec change that a
platform's `migrate` hook grants its own services what they use. For 3d.7:
bootstrap installs every Ansible collection, transitive dependencies such as
`community.library_inventory_filtering_v1` included, pinned by version and
sha256 (shared with the fallback entries), downloaded with the verified
downloader, installed with `--offline --no-deps` and checked with
`ansible-galaxy collection verify --offline`; a maintainer script
(`make lock-collections`) that only reads public URLs and prints every
locked collection's artifact URL and sha256 in the lock's format, kept as
maintainer tooling and documented (run only by a maintainer when pinning or
upgrading, its output reviewed before it is committed, an upgrade landing
only after the full gate and CI pass); each golden image's full package list
(name, version, architecture) in its fingerprint record, with a test; a
minimal `inference-broker` service in `refplat`, so the broker's access to
the inference API is proven positive in the VMs. Before anything is built
for the fallback: whether its GitHub file is Galaxy's artifact, and an
alternative if it cannot verify as strictly. To decide on a proposal: the
pinned collection artifacts bundled with infra for an offline bootstrap.
No snapshot repositories for golden images for now.

### Privacy clean-up (owner instruction)

Scan everything already written for personal or installation-specific data,
replace it with placeholders, report changes; add the privacy scan to every
component's `make lint` and to CI. Result at infra step 1: nothing had been
written before the instruction (the repository was empty), so nothing needed
replacing; `make privacy` is part of `infra`'s `make lint`, and CI runs it for
`infra/` and for the root-level files (`.github/workflows/repo-root.yml`).

## Contract changes

`contract_version 1` is defined by `infra/docs/contracts.md` (resolutions B2,
B3, B4, C2-C10, D1-D3, E3, E13). The ports table (C10) and the CPU sets are
approved with the 3b review (P3b, P3a); contract v2 (infra §12) replaces
this contract in plan item 3d. The owner decided every proposal below after
the 3b commit (section J, "Decisions after the 3b commit").

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

Proposed in infra step 11 (same status, except the two port items, which
P3b approved; details in `infra/docs/IMPLEMENTATION_NOTES.md`, "Step 11";
`contracts.md` itself is unchanged):

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

Proposed after infra step 13 (approved as P3a; details in
`infra/docs/IMPLEMENTATION_NOTES.md`, "Follow-up after step 13: CPU sets"):

- Incus: `[incus] cpuset_targets` in `endpoints.toml` when host_vars sets it;
  the platform sets every bench instance's `limits.cpu` to CPUs inside it
  (never a plain count), and the `bench` project's `limits.cpu` is the number
  of those CPUs.

## Step status (completed phase)

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
| 12 | test-distros harness and CI fast lane | done | `f453176` (fast-lane fixes in `b31d54b`, `07e53f3`; whole play in check mode `40e214c`, its findings `0f8f2aa`, `cce39fe`, `8b5a0e8`) |
| 13 | Installer TUI | done | `01fc3e3` (CPU sets in `00da783`) |
| 14 | Documentation completion | done | `a967052` |
| I1, I2 | Secure Boot MOK consent; required and extended fast-lane jobs | done | `ea0c7bf`, `48102a3`, `009edb1` (with the records `4aec5fc`, `4915ce1`; all five committed outside the rule, kept by owner decision) |

## Moved from PROGRESS.md with 3f

Moved unchanged from `PROGRESS.md` when 3f, the move to the repository
root, was committed (token efficiency rule: nothing is deleted, only
moved). Paths in these records name the layout before 3f, with the
infrastructure below `infra/`.

### Current state before 3f

- Working branch `claude/zen-albattani-qfwwps` of this repository; the
  owner merges to `main`. Last step commit: `e525a1e` (3d.8); last commit
  `a425c7f` (the CI fix of 3d.8 and the `release-package` CI job). Before
  them: `0d72e54` (3d.7) and `4aeb35b` (the collection pins, the
  `lock-collections` CI job and the license-file fix).
- Committed: infra steps 1-14, I1 and I2, plan items 3a-3c3 and 3d steps
  1-8 (with 3d.4b). The commit of each is in the table below and, for the
  steps before section J, in the archive.
- 3d.7 is committed (`0d72e54`), and the fix of its CI run (`d5fe24d`,
  every job green but the known red `lock-check`). The symbolic-link fix of
  `make lock-collections` is committed (`3d4bab2`), with the process
  records (`373e693`); their CI run passed every job but the known red
  `lock-check`, `make setup` with the real Galaxy artifacts in all 15 jobs.
  The owner's second `make lock-collections` run (2026-10-15) is committed
  with the `lock-collections` CI job and the license-file fix (`4aeb35b`);
  its CI run passed every job but the known red `lock-check` (42 marker
  errors, the five artifacts' sha256 verified), and the new job printed
  from Galaxy exactly the license files found before. 3d.8 is committed
  (`e525a1e`); its CI run passed `test`, `docs`, `lint`, `privacy` and
  `lock-collections` (the same license files from Galaxy), `lock-check` is
  the known red (38 marker errors), and every `check-mode` and
  `check-mode-extended` job failed at start on a missing `PYTHONPATH`
  entry. The fix, the `release-package` CI job and the owner's 3d.8
  decisions are committed (`a425c7f`). Its CI run passed every job but the
  known red `lock-check` (38 marker errors and 1 warning, the postgres tag),
  all six `check-mode` and all six `check-mode-extended` jobs included. The
  first `release-package` run built two identical packages (sha256
  `6d9463d2d1307b28e968a3a8676a76576c3ec4ac8a0554988fe11c234840cb39`),
  `make release-package-check` found no problem, and its NOTICE confirmed
  every license finding (see "Where the license findings came from" under
  3d.8).
- 3e: the self-test without provisioning is done and committed under the
  scoped exception (`5ea9b82`; its CI passed every job but the known red
  `lock-check`). It found and fixed two faults, each with a test: a
  documented command missing `tools/lockcheck`, and lock recompiles that
  moved unrelated pins on a fresh clone. Everything that needs a VM, a real
  host, Incus, Docker or a GPU waits for the owner, as listed under [Owner
  deliverables pending](PROGRESS.md#owner-deliverables-pending).

### Platform, catalog, docs site

Not in this repository: they live later in the separate repository
`pokus-platform`, which the owner starts (section J).

### Completed 3d records of the remaining plan

- **3d** Contract v2: steps 1-8 of the proposal's "Effort and order", one
  commit proposal per step, following the updated specs. Verification items:
  can a certificate restricted to `bench` create networks in Incus's
  `default` project at all (run-network helper, infra §7.7)? Required in
  3d.7 (owner decision with the "commit" for 3d.3): remove the named
  temporary exclusion of the installer and its answers files from the
  neutrality lint (V5); 3d.7 is not done while it exists. Step 3d.4b
  (owner decision with the "commit" for 3d.4) comes between 3d.4 and 3d.5
  as its own step and waits for the owner's "commit" like every 3d step:
  V6 (the agent's own package and environment), the telemetry stores
  reachable only by an allowlist of infra UIDs (the alternative of
  ADR-0016), restore of exactly the recorded commit and tree hash, V7 (GPU
  compute capability 7.5) and doctor's check of the query socket link.
- **3d.7** (infra §16 step 21): the installer (release inspection, the
  Platform review screen, the GPU screen, sources, secrets, answers schema,
  snapshots), and the removal of the V5 exclusion (see 3d above). With the
  owner's decisions with the "commit" for 3d.6 (recorded in the
  [archive](PROGRESS-ARCHIVE.md#j-plan-after-infra-step-14-this-repository-becomes-pokus-infrastructure)),
  each with tests:
  - **Bootstrap collections:** every collection, transitive dependencies
    such as `community.library_inventory_filtering_v1` included, pinned by
    version and sha256 (shared with the fallback entries), downloaded with
    the existing verified downloader, installed with `--offline --no-deps`,
    then `ansible-galaxy collection verify --offline`.
  - **`make lock-collections`:** maintainer tooling that only reads public
    URLs and prints every locked collection's artifact URL and sha256 in the
    lock's format; documented as run only by a maintainer when pinning or
    upgrading, its output reviewed before it is committed, an upgrade
    landing only after the full gate and CI pass. The owner runs it and
    pastes the output; the assistant records it and removes those
    `TODO(verify)` markers.
  - **Fallback:** nothing is built for it until the owner decides on the
    answer given with this step's start (the GitHub file is not Galaxy's
    artifact; the alternative proposed there).
  - **Bundled collections:** a proposal only (size, license files, fit with
    the release and the distribution tests); the owner decides.
  - **Golden images:** each image's full package list (name, version,
    architecture) in its fingerprint record, with a test. No snapshot
    repositories for now.
  - **refplat:** a minimal `inference-broker` service, so the broker's
    access to the inference API is proven positive in the VMs.
  - Built (waiting for "commit"): all of the above but the fallback and the
    bundling (owner decisions) and the artifact pins (owner's
    `make lock-collections` output); the V5 exclusion is removed; the
    test-distros harness enables, replaces and restores the platform
    through the installer's answers (the interim host_vars and vault
    writes approved until 3d.7 are gone); `--replace-platform` reaches the
    installer's inspection; the CI failures of `a686e28` are fixed (see
    Known issues).
- **3d.8** (infra §16 step 22): documentation (contract v2 with
  `docs/contracts.md` rewritten for v2, generated references, new pages,
  ADR-0014 and ADR-0015, security). With the owner's decisions with the
  "commit" for 3d.7, each with tests:
  - **Fallback:** every GitHub-archive fallback goes, `make setup`'s of 3d.6
    included; Galaxy's artifact and its sha256 are the only pins.
  - **Bundled collections (approved):** a CI job on each `infra-vX.Y.Z` tag
    builds a release package with the five pinned Galaxy artifacts (same
    bytes, same sha256) and a NOTICE file listing their licenses, and
    attaches it to the GitHub release. Bootstrap installs from the bundled
    artifacts when it runs from a release package, offline, verified against
    the lock exactly as now; from a git checkout it uses Galaxy. `make setup`
    may use a published release package as its fallback when Galaxy is
    unreachable, with the same verification. The tarballs are never
    committed to git. `lock-check` and `make test-distros` check the bundled
    files.
  - **NOTICE (owner, with the pins of the second `make lock-collections`
    run):** built from the license texts actually present in each artifact,
    not from the declared field (`ansible.posix` and `community.general`
    declare none). It covers every license a file of the artifact carries:
    `ansible.posix`'s `PSF-license.txt` (for `plugins/module_utils/_version.py`
    and `mount.py`, whose header holds the PSF license text itself), and it
    states a declared license that no file carries, such as
    `community.crypto` 3.4.0's BSD-2-Clause and PSF-2.0 (no file, header,
    SPDX identifier or license text in the artifact; its changelog records
    the ACME code relicensed from BSD-2-Clause to GPL-3.0-or-later, and its
    `plugins/module_utils/_version.py`, which once needed `PSF-license.txt`,
    is now GPL-3.0-or-later and imports `LooseVersion` from ansible-core).
    Owner, with the "commit" for `4aeb35b`: no SPDX texts are added; those
    two are listed as declared in the metadata but carried by no file in the
    artifact. Checked for that commit's report: all 66 files of
    `community.library_inventory_filtering_v1` 1.1.5 declare
    GPL-3.0-or-later or are its text, except the empty `py.typed` marker,
    which carries no license statement. The file list and sha256 values
    came from the `FILES.json` of 1.1.5 as bundled in the `ansible` 14.4.0
    wheel from PyPI, and every file was read from the release tag `1.1.5`
    of the collection's repository, matched by sha256 (Galaxy is not
    reachable from this environment).
  - **Where the license findings came from (owner, with the "commit" for
    `a425c7f`):** every per-file license finding recorded before
    `a425c7f` was made from the `ansible` 14.4.0 wheel on PyPI: its
    `MANIFEST.json` and `FILES.json` for each of the five collections,
    with every listed file read from the collection's release tag (or the
    wheel) and matched by sha256 (215, 579, 642, 3453 and 66 files, all
    matched). Only the license-file names and declared licenses printed by
    `lock-collections` came from Galaxy. The first run of the
    `release-package` job (`a425c7f`) printed the NOTICE built from
    Galaxy's own artifacts, and it equals the NOTICE built from the wheel
    copies in every line it expresses: `ansible.posix` 2.2.2 carries
    `COPYING` and `PSF-license.txt`, declares no license, has 215 files,
    one naming GPL-3.0-or-later and 212 with no SPDX line, among them
    `plugins/module_utils/_version.py` and `plugins/module_utils/mount.py`;
    `community.crypto` 3.4.0 carries `COPYING` and `LICENSES/` Apache-2.0,
    BSD-3-Clause and GPL-3.0-or-later, its files name Apache-2.0 (2),
    BSD-3-Clause (1) and GPL-3.0-or-later (529), it declares
    GPL-3.0-or-later, Apache-2.0, BSD-2-Clause, BSD-3-Clause and PSF-2.0,
    and BSD-2-Clause and PSF-2.0 are carried by no file; 579 files, none
    without a license; `community.docker` 5.3.0 carries `COPYING` and
    `LICENSES/` Apache-2.0 and GPL-3.0-or-later, its files name Apache-2.0
    (49) and GPL-3.0-or-later (581), 642 files, none without a license;
    `community.general` 13.4.0 carries `COPYING` and `LICENSES/`
    BSD-2-Clause, GPL-3.0-or-later and MIT, its files name BSD-2-Clause
    (37), GPL-3.0-or-later (3056) and MIT (2), it declares none, 3453
    files, 30 without an SPDX line (test fixtures and data files, two
    `.keep` files, a test collection's `README.md` and
    `plugins/module_utils/__init__.py`);
    `community.library_inventory_filtering_v1` 1.1.5 carries `COPYING`
    and `LICENSES/GPL-3.0-or-later.txt`, its files name only
    GPL-3.0-or-later (47, the 16 REUSE `.license` files counted with the
    files they annotate), 66 files, only `plugins/plugin_utils/py.typed`
    without a license. No finding differs. Two findings rest on file
    contents NOTICE does not show and so come from the wheel's file list
    with the tagged sources only: `plugins/module_utils/mount.py` of
    `ansible.posix` holds the PSF license text in its header and
    `plugins/module_utils/_version.py` refers to `PSF-license.txt`; and
    `community.crypto`'s changelog records the ACME code relicensed from
    BSD-2-Clause to GPL-3.0-or-later, while its `_version.py` is
    GPL-3.0-or-later and imports `LooseVersion` from ansible-core.
  - **Release package (owner's choice in 3d.8, "Full package"):** the CI job
    on an `infra-vX.Y.Z` tag builds a tarball of the repository's infra tree
    at the tag (`git archive`) plus `release/` with the five Galaxy
    artifacts, `NOTICE` and `RELEASE.json` (tag, commit, infra tree hash).
    Bootstrap runs from it without `.git`: it recomputes the infra tree
    hash of the unpacked files with git (a temporary index), refuses a
    mismatch like a dirty tree (unless `--allow-dirty`, recorded), records
    the commit and tree from `RELEASE.json`, and installs the collections
    offline from `release/` (sha256 against the lock). A restore of a
    package install needs that tag's package.
  - **TUI replacement:** the TUI also offers replacing the active platform,
    behind a confirmation screen that names the active platform and asks
    for its name to be typed, exactly like `pinka platform purge`; without
    the typed name nothing changes. `--replace-platform` keeps working as
    before.
  - Approved: the five 3d.7 interpretations. Generated platform secrets
    come from Python's `secrets.token_urlsafe(32)` (the operating system's
    cryptographically secure generator) and are written only to the vault
    (encrypted, root-only 0600); the platform role writes each to its
    service's secret file (`no_log`).
  - Built (waiting for "commit"): all of the above, and the documentation:
    `docs/contracts.md` as contract v2; the schemas of the principals and
    endpoints files in `contract/` with the generated reference
    `docs/reference/platform-files.md`; the tutorial "Write a minimal
    platform", the explanation "Privilege profiles and restricted data",
    ADR-0014 and ADR-0015, the how-tos "Install from a release package" and
    "Publish a release package"; "Deploy a platform" retitled "Plug a
    platform into the lab"; security, architecture, node roles, bootstrap,
    troubleshoot, backup and restore, WSL2 (CDI for training containers)
    and the remaining contract v1 leftovers updated. A real package build
    could not run here (the proxy refuses Galaxy); the first one is the
    `release` workflow on the first tag. Interpretations for the owner are
    in `infra/docs/IMPLEMENTATION_NOTES.md` ("Contract v2, step 8").

### Definition of 3f

- **3f** Move to root, one structural commit with no other changes:
  `git mv` everything under `infra/` to the root so history follows;
  `infra/.github/workflows/ci.yml` becomes `.github/workflows/ci.yml`, the
  wrapper `infra.yml` goes, `repo-root.yml` is merged into `ci.yml` or kept,
  whichever needs no behavior change; the one-repository rules go:
  bootstrap's tree hash and dirty check cover the whole repository (B3),
  tags have no prefix (`vX.Y.Z`, B4), C1 goes, and every path or link that
  pointed into or out of `infra/` is fixed; `PROGRESS.md` and `CLAUDE.md`
  become infra-only, and the infra-only `CLAUDE.md` keeps the commit rule's
  wording of the root `CLAUDE.md`; the platform spec master
  (`specs/pokus-platform-claude-code-prompt.md`) and anything else that
  belongs only to the platform or catalog is removed (the owner keeps a
  copy for `pokus-platform`); the infra spec master is renamed per Q13.
  Gate: `make lint test docs` green, CI green on the branch, residue check
  clean.

## Next step before the handover after 3f-1 A

Moved unchanged from PROGRESS.md with the handover (2026-10-24).

Order after 3f (owner): commits 1 and 2 are done (`1a46f64`, `b7be7ff`),
and 3f-1 is recorded (`eb02d37`). The spec edit (`4591db9`), D1
(`8558bee`) and its CI fix (`74377ec`) are committed, then the small spec
edit after D1 (`0378425`), the installer fix of the written files
(`32b2c95`) and the GPU support guard (`37c062e`), pushed together and
green but the known red. Then (owner, after `37c062e`): the CI skip list
with the nft checks and the root test in CI (`006c40d`) and the per-file
process run (`2f27132`), JUnit XML of every test job (`fc7cc3b`), the
Galaxy retry with the re-run rule and the action pin check (`5fc33c1`) and
the pre-existing access with the SSH `Match` block as a D1 follow-up
(`16bf749`), all green but the known red; then, each waiting for "commit",
the owner's decisions after `16bf749` (DL1, AG1, the login detection)
with AG1's correction and the CI minutes (`6aa6eab`, not pushed). Then the
owner's new plan after `16bf749` (verbatim in the remaining plan): the
public-readiness audit (reported) and the owner's decisions on it, as
commits without a push (the machine classes, the privacy scan's additions,
the project files), the history rewrite (done with the owner's "rewrite";
the commit after it waits for "commit"), then the commands for the owner's
pushes (step 4; the owner created the fresh repository
`pokus-infrastructure` instead of the rename and pushed `main` and
`moving/on` to it), the checklist for making the repository public (step
5), and the pull request `moving/on` -> `main`, which the owner opens (step
6; merged as `6ea7468`, and `moving/on` fast-forwarded to it); then, on
`moving/on`, the commit for one CI run per commit (`6bab38b`) and D2 with
the owner's five fixes and the kept secrets' follow-up (its report with the
table of what uninstall removes, restores or keeps), then A (`00ff2c4`) and its follow-up (the inference and training
switches with the computed training cap, built, waiting for the owner's
"commit"), B, C, E, F and
G, each its own step with the full gate, waiting for the owner's "commit",
its CI reported after the push, no side work in between (anything that
comes up is an open item), then the feature-complete checkpoint (under
"Process rules in force"), the real-machine tests on the owner's computers (4.1 and 4.2 first) and
3h. The owner runs
`make lock-pins` on their machine and pastes the output separately. Still
open: the remaining `TODO(verify)` items (103), which need network access or
reference hardware.
