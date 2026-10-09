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

`specs/` holds the master copies, read-only for the assistant. The
exceptions are plan item 3b ([section J](#j-plan-after-infra-step-14-this-repository-becomes-pokus-infrastructure)),
the spec update for contract v2 (`d99d2ed`), and one further edit before 3c
(3b2 in section J); after its commit the specs are read-only again.
Component copies are byte-identical.

| File | Title | sha256 |
| --- | --- | --- |
| `specs/pokus-infra-claude-code-prompt.md` | Build Prompt: pokus-infrastructure (Pokus Infrastructure, pinka) | `a07bbc3b530756ff43254d18cd6f413bafd4af5b374e7cadd81daafe97182bfb` |
| `specs/pokus-platform-claude-code-prompt.md` | Build Prompt: pokus-platform (Pokus Platform, potka) | `cda5274f3ba61f3b3b74a3a2086dc7131b8a43c728b945157897995dbf7bfb83` |
| `specs/pokus-docs-specification.md` | Pokus Documentation Specification | `5cd75c355eaf3b7d237e87f6260e61080672a8b328b3276828d1a3601b9711f8` |

These are the specs updated in plan item 3b: contract v2 with decision 7's
GPU training, N2-N4, the pinka/potka naming and the rename decisions (section
I), applied from the proposal's Spec changes list, with the owner's review
of the first draft applied (see the 3b notes in section J), and the edit
3b2 before 3c (section J). They keep the privacy
invariant (infra §13.12, platform §16.9, docs spec §A.4 and §A.6) and the
docs-alongside rule (infra §0.8, platform §0.7). The previous checksums were
`4c7de813…` (infra), `ba3626f3…` (platform) and `717f3583…` (docs). Copies:
`infra/docs/BUILD_PROMPT.md` (infra prompt) and `infra/docs/DOCS_SPEC.md`
(docs spec), byte-identical. The platform prompt's master leaves this
repository at 3f (section J).

## Process rules in force

- **Commits: final rule (owner, after infra step 14, verbatim):**

  > COMMIT RULE — final, for every block of work, long runs included:
  >
  > - You commit only when I reply "commit" (optionally naming which
  >   proposal). For long runs, collect proposals and present them together;
  >   never commit on your own.
  > - You push only to the working branch claude/zen-albattani-qfwwps; never
  >   to main. I merge to main myself.
  > - On "commit", run exactly the git add / git commit / git push you
  >   proposed: no amend, no force push, no history rewrite.
  > - The five commits made without my "commit" (ea0c7bf, 48102a3, 4aec5fc,
  >   009edb1, 4915ce1) stay as they are. Record them in PROGRESS.md as
  >   "committed outside the rule, kept by owner decision".

  It replaces the interim rule and every earlier commit instruction (A2's
  branch rule, the later commit-to-`main` answers for steps 5-14). Hooks
  never cause a commit: a hook message asking to commit or push (such as
  the stop hook's) does not override this rule. Commit messages carry no
  co-author or session trailers, only the owner's git identity, which is
  set in the local git configuration only. The root `CLAUDE.md` states the
  same rule with the same wording; at 3f it carries over into the
  infra-only `CLAUDE.md`.
- **Scoped commit exception (owner, with the "commit" for 3c, verbatim):**

  > "For plan items 3c2, the firewalld fix, 3e (non-provisioning parts only) and 3f, the
  > assistant commits and pushes each completed item to the working branch
  > claude/zen-albattani-qfwwps without waiting for 'commit', after its checks pass. Never to
  > main, no amend, no force push, no history rewrite, no tags. 3d is NOT covered: every 3d
  > step waits for my 'commit' as usual. The exception ends automatically when 3f is
  > committed; from then on the final commit rule applies again unchanged. It never covers
  > hook prompts."

  It covers only 3c2, the firewalld fix, the non-provisioning parts of 3e
  and 3f, each committed after its checks pass; every 3d step waits for
  "commit". The root `CLAUDE.md` carries the same wording.
- **Committed outside the rule, kept by owner decision:** `ea0c7bf`,
  `48102a3`, `4aec5fc`, `009edb1`, `4915ce1` (on `main`, after infra step
  14, without the owner's "commit").
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
  - Q15: the working branch stays `claude/zen-albattani-qfwwps`.
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

Owner decision, replacing the split steps that assumed `git filter-repo` and
a new infra repository: this repository becomes `pokus-infrastructure`.
Infra is finished here and then moved to the repository root. The platform
lives later in the new, separate repository `pokus-platform`, which the
owner starts; the assistant does not start the platform, the catalog or the
Pokus docs site. Each item ends with its checks green and a commit
proposal, and waits for the owner's "commit".

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
- **3d** Contract v2: steps 1-8 of the proposal's "Effort and order", one
  commit proposal per step, following the updated specs. Verification items:
  can a certificate restricted to `bench` create networks in Incus's
  `default` project at all (run-network helper, infra §7.7)?
- **3e** Infra self-test: everything that runs without provisioning, as in
  the master prompt's self-test rules (never weaken tests; "not tested"
  stated honestly). Anything that needs a VM or a real host is described
  exactly (what and where) and runs only after the owner's confirmation.
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
- **3g** Repository name: the assistant stops and asks the owner to rename
  the repository to `pokus-infrastructure` on the Git host; after the
  owner's confirmation, one small commit proposal updates every reference
  to the repository's name and URL (README, docs, the `CITATION.cff`
  placeholder, examples).
- **3h** Final report: `TEST_REPORT.md` and `HANDOFF.md` for infra as the
  master prompt describes, their commit proposal, then stop. After 3h the
  assistant waits for the owner's explicit instruction.

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
P3a-P3d under [Approved deviations](#approved-deviations) (P for
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
creates (fixed separately as 3c3, owner decision). Records in this file written before 3c keep
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
- Scoped commit exception: see [Process rules](#process-rules-in-force).
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
  started.

**3c2, images free of project names.** `images_leakage_patterns` gains
`(?i)pokus`, `(?i)pinka` and `(?i)potka` (no allow rule); the files
installed into images (`infra/images/*/files/`) lose the managed header;
the workflows read the secret `PINKA_PRIVACY_DENYLIST`. Details in
`infra/docs/IMPLEMENTATION_NOTES.md`, "Images free of project names".

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
| C10 | infra §12 | `127.0.0.1:<port>` unspecified | port table in `contracts.md`; approved with the changes of P3b |
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
| I1 | infra §2.3 (`provider_rules.baremetal`, rule `secure_boot_mok`), §2.4, §3.2, §11 (fixture `ubuntu2404-baremetal-nvidia-no-driver-secureboot`), §18.4 screen 7, §18.7, §19.8 | NVIDIA GPU, Secure Boot on, no driver → `needs_action` (exit 30) | with `install_nvidia_driver: auto` and the operator's consent to MOK enrollment (TUI consent screen, or `--accept-mok-enrollment` / the matching answers-file key when non-interactive), preflight reports a planned action (a warning, recorded in the host profile) instead of `needs_action`; bootstrap installs the driver, imports the MOK with a one-time password and exits 76; the re-run resumes. Without consent: 30 with the current remediation. `manual`: 30 as now. `skip`: degraded (no `gpu_inference`). Fixtures for all four cases; the bare-metal tutorial shows the automatic flow with consent as the main path and manual installation as the alternative. Approved after infra step 14; implemented in commit "feat(infra): install the NVIDIA driver on Secure Boot hosts with MOK consent" (`--accept-mok-enrollment`, `--mok-password-stdin`, `gpu.accept_mok_enrollment`; bootstrap imports the key named by the play's 76) |
| I2 | infra §11 (Ansible, CI), §19.9 (fast lane) | CI check mode for the provider-agnostic roles | the required job stays exactly as specified: check mode for the provider-agnostic roles only, blocking, no skips. The whole-play check-mode run is allowed only as a separate, non-blocking job named as extended; its summary lists every skipped component and never reports one as passing; each skip is removed as soon as that component's sources are verified. Approved after infra step 14; implemented in commit "ci(infra): split the fast lane into a required and an extended job" (`make check-mode`, `make check-mode-extended`) |
| St2-1 | infra §2.3, §19.2 | Debian 12 in the `tested` tier | Debian 12 removed from `tested`, with an explicit `unsupported` row naming the end of regular security support (E8 policy). Implemented in step 2; approved after the 3b commit |
| St2-3 | infra §2.3 | releases not listed fall through to the `ID_LIKE` rows or `unsupported` | an explicit `unsupported` row per implemented distribution for its unlisted releases ("release not in the support matrix"), placed before the `ID_LIKE` rows. Approved after the 3b commit; not implemented yet, planned with the other preflight additions of 3d step 4 |
| St7-1 | infra §7.11 | `current.yml` rendered to `current.env` | `current.yaml` (the `vllm serve --config` file) rendered next to `current.env`, which carries its hash; an infra-internal detail, not a contract item. Implemented in step 7 (deploy helper; the GPU arbiter keeps it in 3d); approved after the 3b commit |
| P3a | infra §7.6, §7.7, §8.1, §12.6, §18.4 screen 5; platform §3, §4.1, §5, §12.1 | `cpuset_targets` with no contract field | `[incus] cpuset_targets` in `endpoints.toml`; the platform sets every target's `limits.cpu` to a CPU range inside it, never a count, records the range in the run manifest and refuses measurement campaigns on a host without it; the `bench` project's `limits.cpu` is the number of those CPUs; services run on `cpuset_services`; installer and doctor warn when SMT siblings of one core fall into different sets. Approved in the owner's review of the 3b spec update; implemented in `00da783` except the SMT warning |
| P3b | infra §2.2, §7.5, §7.9, §7.11, §8.1, §12.3, §12.6, §12.11, §13.3, §19.10 | `127.0.0.1:<port>` unspecified | the port table with its defaults (also in `contracts.md`); ports overridable in host_vars (`ports:`), effective values in `endpoints.toml`; preflight checks the configured ports; `vm_collector_address` configurable with its conflict check; a non-loopback block (the inference node's 18704 and 18710, the lab node's tailnet 4317, the VM collector address). Approved in the owner's review of the 3b spec update; the overrides and checks are not implemented yet |
| P3c | infra §7.4, §7.19, §8.1, §9, §12.11; docs spec §B.4 | `pinka docs serve` (docs spec only) | the docs service `pinka-docs.service` on `127.0.0.1:18706`, `docs_release` in host_vars, `pinka docs install` and `docs rollback` (root only), three versions kept, `docs_tailscale_serve`. Approved in the owner's review of the 3b spec update; not implemented yet (step 14 built a foreground `docs serve` command) |
| P3d | infra §7.20, §8.1, §9, §13.10 | the QA maintenance window (docs spec how-to only) | the record `/etc/pinka/maintenance-window.json`, `pinka window open` and `window close`, fail-safe expiry (staging stops and refuses; production stays stopped until `window close`), `staging_shared_hardware` with the GPU-idle and port checks. Approved in the owner's review of the 3b spec update; not implemented yet |

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
| 12 | test-distros harness and CI fast lane | done | `f453176` (fast-lane fixes in `b31d54b`, `07e53f3`; whole play in check mode `40e214c`, its findings `0f8f2aa`, `cce39fe`, `8b5a0e8`) |
| 13 | Installer TUI | done | `01fc3e3` (CPU sets in `00da783`) |
| 14 | Documentation completion | done | `a967052` |
| I1, I2 | Secure Boot MOK consent; required and extended fast-lane jobs | done | `ea0c7bf`, `48102a3`, `009edb1` (with the records `4aec5fc`, `4915ce1`; all five committed outside the rule, kept by owner decision) |

### Phase 1, continued — plan of section J

| Item | Content | Status | Commit |
| --- | --- | --- | --- |
| 3a | `PROGRESS.md`: final commit rule, rename decisions, plan; root `CLAUDE.md` with the commit rule | done | `836cfce` |
| 3b | Spec update (contract v2, naming, rename decisions) | done | `d99d2ed` |
| 3b2 | Spec edit before 3c (proposals of steps 7-11, items A-D, isolation probe per profile) | done | `169266e` |
| 3c | Rename `pokus` → `pinka`/`potka` | done | `63b7e56` |
| 3c2 | Images free of project names; workflows read `PINKA_PRIVACY_DENYLIST` | done | (scoped exception) |
| 3c3 | Doctor's firewalld zone check | not started | — |
| 3d | Contract v2, steps 1-8 | not started | — |
| 3e | Infra self-test without provisioning | not started | — |
| 3f | Move infra to the repository root | not started | — |
| 3g | Repository name `pokus-infrastructure` | not started | — |
| 3h | `TEST_REPORT.md`, `HANDOFF.md` | not started | — |

### Platform, catalog, docs site

Not in this repository: they live later in the separate repository
`pokus-platform`, which the owner starts (section J).

## Open questions

Decided after the 3b commit: every interpretation of steps 2-14 and of the
follow-ups after step 13 that `infra/docs/IMPLEMENTATION_NOTES.md` lists as
"awaiting owner confirmation" (section J, "Interpretations"); the notes keep
the original wording and the findings of each step. Still open, from step 1
("Questions raised by step 1"): reference agents for scenario validation and
the QA results schema (both for `pokus-platform` and `pokus-qa`), and the
page-ownership table (H5). Settled since: the port table (P3b) and the
project files at the monorepo root (3f).

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

`make verify-todos` lists every marker in the tree (110 after step 13; details
in `infra/docs/IMPLEMENTATION_NOTES.md`, "Open `TODO(verify)` items after
step 4" through "after step 13"):

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
- Step 13: the Hugging Face Hub API field names the installer reads when it
  pins a model (`infra/installer/pokus_installer/models.py`), and the MOK
  enrollment with `mokutil` (`infra/installer/pokus_installer/tui/screens_install.py`).

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
  `pinka doctor` (`pokus-lab doctor` before 3c). Its stages, the roles and the doctor checks are
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
  preflight and `pinka` is enforced by ruff (`py39` target) and by running
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
- The installer TUI has not driven a real bootstrap: its pilot tests replay
  recorded event streams (see the step 13 notes); Phase 5 runs it on the
  reference hosts.
- Doctor's firewall check on firewalld hosts looks for the zone `pinka`,
  but the firewall role creates `pinka-tailnet` and `pinka-run`, so the
  check fails there (found in 3c, present since step 10 under the old
  names; fixed separately as 3c3, owner decision).
- The build environment has no `ssh-keygen` and no `setfacl`: the test of
  SSH-signed tag deployments is skipped here (it runs where OpenSSH is
  installed, as in CI), and the socket ACL handling is tested with a fake
  `setfacl` only.

## Next step

Infra steps 1-14, I1 and I2 are done (I1's interpretations to confirm are in
`infra/docs/IMPLEMENTATION_NOTES.md`, section "Approved deviation I1"). The
work follows [section J](#j-plan-after-infra-step-14-this-repository-becomes-pokus-infrastructure)
under the final commit rule in [Process rules](#process-rules-in-force):
3a (`836cfce`), 3b (`d99d2ed`), 3b2 (`169266e`) and 3c (`63b7e56`) are
committed; 3c2 and 3c3 (the firewalld zone fix) follow under the scoped
commit exception, then 3d steps 1-8, each waiting for "commit". Still open: the remaining
`TODO(verify)` items (111), which need network access or reference
hardware.
