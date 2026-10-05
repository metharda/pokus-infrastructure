# Pokus Documentation Specification

> Shared by every bot and human that writes code for Pokus. Commit it as `docs/DOCS_SPEC.md` in `pokus-infra`, `pokus-platform`, `pokus-catalog` and `pokus-qa`, and as the build prompt of the `pokus-docs` repository (Part B).
>
> Rule of thumb: **a feature is not done until it is documented.** Every BUILD_PROMPT's "definition of done" includes the documentation required here.

---

## Part A — What the documentation must contain

### A.1 Goals

The documentation must let each of these readers succeed **without asking anyone**:

| Reader | Must be able to… |
|---|---|
| **Researcher** (one of the three lab users) | log in, submit and monitor campaigns, approve actions, inspect runs, compare models, export data, reproduce a past result |
| **Lab operator / admin** | install the lab on any supported distro, operate it daily, add users, update, back up, restore, migrate, troubleshoot |
| **Scenario author** | write, test and publish a new fault scenario that is solvable, reproducible and leak-free |
| **Platform developer** | understand the architecture, set up a dev environment, change a service safely, add tools/primitives/policy rules, respect the contract |
| **QA bot** | find the exact expected behavior for any requirement and execute every tutorial verbatim |
| **Paper or thesis reader / reviewer** | understand and judge the benchmark methodology, the metrics, the validity controls and the limitations, and reproduce published numbers |

### A.2 Structure (Diátaxis)

All documentation follows the Diátaxis framework. Every page belongs to exactly one type, and says so in its front matter.

| Type | Purpose | Style |
|---|---|---|
| **Tutorial** | learning by doing, one guaranteed path | step by step, every step has a visible result, no choices |
| **How-to guide** | accomplish a specific task | goal in the title ("How to restore from a backup"), prerequisites, numbered steps, verification step at the end |
| **Reference** | exact facts | complete, neutral, mostly **generated from code** |
| **Explanation** | understanding and rationale | prose, diagrams, trade-offs, links to ADRs |

### A.3 Required pages

**Getting started**
- What Pokus is (one page: problem, approach, what it is not)
- Concepts and glossary: campaign, run, scenario, scenario instance, seed, blueprint, golden image, target, node alias, ticket, evidence bundle, tool, primitive, probe, policy decision, approval mode, agent mode, run style, deployment, activation, fingerprint, session, validity vs outcome, official vs exploratory, ground truth, attestation
- A 10-minute tour with annotated screenshots (TUI, `pokus` CLI, Grafana run explorer, a report)

**Tutorials**
1. Install a lab on WSL2 (reference setup)
2. Install a lab on bare-metal Linux
3. Your first campaign: submit, watch, inspect a run, read the report
4. Write your first scenario (from template to passing lint and oracle/null checks)
5. Compare two models on the same scenario set
6. Compare a base model with a fine-tuned LoRA adapter
7. Reproduce a published result from a snapshot and its manifests

**How-to guides — operators**
- Start, stop and drain the lab; what happens on power loss
- Add, remove or lock a researcher; rotate a researcher's SSH key
- Update the host safely (`pokus-lab update-host`) on each distro family
- Pin, update and verify versions (`versions.lock.yml` workflow)
- Add a model, verify it, create and activate a deployment
- Build, test and export golden images
- Back up, restore, and run a restore drill
- Migrate the lab to a new machine
- Pair a separate inference node (`node_role`)
- Configure firewall backends (nftables, firewalld, ufw); work with SELinux enforcing
- Open a QA maintenance window and run staging
- Publish a QA attestation
- Rotate secrets and certificates
- Troubleshoot by bootstrap exit code and by doctor check ID (one section per code / check)

**How-to guides — researchers**
- Connect over Tailscale and SSH; reach Grafana; use the ad-hoc LLM endpoint
- Write a campaign spec (every field explained with examples)
- Monitor a campaign; find invalid runs and why
- Approve or deny actions (`human` approval mode)
- Inspect a run: events, tool calls, LLM calls, artifacts, traces
- Run exploratory experiments (`pokus dev`) and their limits
- Run evaluations, snapshots, reports and comparisons
- Export data (Parquet/CSV/JSON) for external analysis
- Cite a result correctly (which IDs and hashes to include)

**How-to guides — scenario authors**
- Anatomy of a scenario and its ground truth
- Parameters and seed derivation
- Choosing primitives and probes; writing confirm, verification and collateral rules
- Writing tickets that do not leak (with good/bad examples)
- Control scenarios
- Validating: `pokus catalog lint`, injection reliability, oracle/null agent checks
- Versioning and publishing a scenario change

**How-to guides — developers**
- Development environment with fakes (no Incus/GPU needed)
- Repository layout and package boundaries
- Adding a tool, a fault primitive, a probe, a policy rule
- Adding a DB migration
- Proposing a contract change between infra and platform
- Adding a distro family (links to infra's checklist)
- Release process (tags, changelog, QA attestation)

**Reference (generated wherever marked ⚙)**
- ⚙ CLI reference: `pokus`, `pokus-lab`, `pokus-preflight`, `bootstrap.sh` (from argument parsers / `--help`)
- ⚙ HTTP API reference per platform service (OpenAPI from FastAPI, rendered), including which callers each endpoint allows
- ⚙ Configuration reference: platform TOML settings (from pydantic settings models), Ansible variables per role (from `meta/argument_specs.yml`), host_vars and group_vars keys, installer answers file
- ⚙ Schemas: scenario, ground truth, taxonomy, blueprint, campaign, agent config, deployment, host profile, fingerprint, run manifest, QA results, attestation (from JSON Schemas)
- ⚙ Tool registry (every tool, risk, args, exact argv template, post-check)
- ⚙ Fault primitives and probes (from their `describe()` and params models)
- ⚙ Policy rules (from Rego rule annotations: ID, order, condition, effect, rationale)
- ⚙ Database schema (ER diagram + table/column descriptions from migration comments)
- ⚙ Event catalog (every CloudEvents type, source, data schema)
- ⚙ Telemetry: span names, attributes, metric names, log fields
- ⚙ Support matrix (distros, tiers, last test results) and component source matrix
- ⚙ Pinned versions (from `versions.lock.yml`)
- Exit codes (bootstrap, preflight, doctor, CLI)
- Doctor checks (ID, what it checks, remediation)
- Metric definitions with formulas (accuracy, macro P/R/F1, coverage, selective risk, AURC, ECE variants, Brier, resolution rate, action precision, harmful/false-positive action rates, MTTD/MTTR with censoring) and the statistics used (cluster bootstrap, McNemar, Holm)
- File system layout with owners and modes; accounts, groups and sockets matrix

**Explanation**
- Architecture: trust zones, components, data flows (Mermaid diagrams), the infra/platform split and the contract
- Run lifecycle: every phase, owner, timeout, failure mapping (sequence diagram)
- Ground-truth blindness: how it is enforced (OS, systemd, DB roles, data flow) and how it is tested
- The agent boundary: tool calls, policy, approvals, why confidence never authorizes alone
- Target isolation: Incus, per-run networks, quotas, telemetry via proxy devices
- Reproducibility: pinning, fingerprints, sessions, seeds, sealing and signatures, what makes two runs comparable
- Portability: families, source matrix, firewall/MAC backends, node roles, the VM escape hatch
- Threat model and security design
- QA and validity controls: reference agents, red team, attestations
- **Benchmark methodology** (written so it can be cited in publications): research questions the lab supports, experimental protocol, metric definitions, validity controls, statistical analysis, known limitations and threats to validity (container vs VM kernel effects, WSL virtualization overhead, oracle-approval leakage, model non-determinism, scenario coverage)
- ADR index (links to every ADR in every repo)

**Project pages**
- Changelog per repo (Keep a Changelog format, generated from Conventional Commits and edited by a human at release)
- Release notes per release candidate (RC triple, QA attestation link, notable changes, migration notes)
- `CITATION.cff` and "How to cite Pokus"
- Contributing guide and code of conduct
- License

### A.4 Page conventions

Every Markdown page starts with front matter:

```yaml
---
title: How to restore from a backup
type: how-to            # tutorial | how-to | reference | explanation
audience: [operator]    # researcher | operator | scenario-author | developer | qa | reader
applies_to: {infra: ">=1.2", platform: ">=1.0"}
last_verified: {date: 2026-10-05, rc: "infra@abc123 platform@def456 catalog@789abc", by: qa-run-2026-10-05-01}
---
```

- **One task or topic per page.** Title says what the reader achieves.
- **Prerequisites** box at the top of tutorials and how-tos.
- **Verification step** at the end of every how-to ("you should now see …").
- **Copy-pasteable commands** in fenced blocks with a language tag; outputs in separate blocks labelled as output. Mark blocks that QA bots must execute with `<!-- qa:run env=E2 -->` and blocks that are illustrative with `<!-- qa:skip reason=... -->`.
- **Placeholders** in `<angle-brackets>` and listed under the block.
- **Admonitions** for warnings (data loss, security) — never bury a destructive step in prose.
- **Diagrams** as Mermaid source in the page (never binary images of diagrams).
- **Screenshots** of the TUI are generated from the TUI snapshot tests (SVG), never hand-made, so they stay current.
- **Cross-links** instead of repetition; each fact has one home page.
- **Language:** English is canonical. The structure must be i18n-ready (`mkdocs-static-i18n` or equivalent) so researcher-facing pages can be translated later; translated pages carry the canonical page's `last_verified` and are flagged stale when the English page changes.
- **No personal data:** documentation, examples, screenshots and generated pages follow the build prompts' privacy invariant: placeholders such as `researcher1`, `example-org`, `example.com`, documentation IP ranges; never real names, accounts, hosts or addresses. Public services, vendors and the project's own public repository may be named normally.
- **Style:** plain, direct, active voice, no marketing language; define a term on first use and link the glossary; units and timezones explicit (all times UTC).

### A.5 Docs inside the code

- Python: Google-style docstrings for every public module, class and function; `mypy --strict` types are part of the documentation.
- Ansible: every role has `README.md` and `meta/argument_specs.yml` (the variable reference is generated from it; undocumented variables fail CI).
- Rego: every rule has a METADATA annotation (`title`, `description`, `custom.rule_id`, `custom.order`).
- Tools, primitives, probes: `description` / `describe()` is mandatory and user-readable.
- Migrations: table and column comments are mandatory (they feed the DB reference).
- Config models: every field has a description and an example.

### A.6 Documentation quality gates (CI in every repo)

- `make docs` builds the repo's docs with **strict mode** (warnings fail): broken internal links, missing pages in navigation, invalid front matter.
- **Docs coverage check:** every CLI command and option, HTTP endpoint, config key, Ansible variable, schema, tool, primitive, probe, policy rule, event type, metric, exit code, doctor check and DB table has a reference entry. Missing entries fail CI.
- **Drift check:** regenerate all ⚙ pages and fail if the committed version differs.
- **Link check** for external links (weekly, non-blocking but reported).
- Privacy scan of all pages and the built site (same rules as the build prompts' privacy invariant).
- Spell check with a project dictionary; Markdown lint; Mermaid syntax validation.
- `last_verified` older than the last release on a page whose `applies_to` covers that release → warning in CI, failure at release time.

### A.7 Definition of done (documentation part, for every change)

- New or changed behavior is described in the right page type(s).
- Generated references regenerated and committed.
- Tutorials and how-tos touched by the change still pass when executed by QA (§A.8).
- Changelog entry written.
- If the change affects methodology (metrics, scenario semantics, validity rules), the Benchmark methodology page is updated and the change is called out in release notes.

### A.8 Docs are tested

QA bots (pokus-qa) execute every `qa:run` block of every tutorial and how-to in the indicated environment, compare outputs where the page states expected output, and file findings with the page path and block number when a step fails. A tutorial that does not work is an **S1** finding; a wrong reference entry is **S2**; a wrong statement in the Benchmark methodology page is **S1**.

---

## Part B — The `pokus-docs` site repository (build prompt)

> Paste Part B into Claude Code to build the combined documentation site. Follow the working rules of the other BUILD_PROMPTs: never invent versions, small Conventional Commits, `make lint test` green, ask open questions first.

### B.1 What it is

A repository that assembles the documentation of all Pokus repositories into **one versioned website**, built from an exact release-candidate triple (infra, platform, catalog commits) plus the `pokus-qa` commit.

### B.2 Technology

- MkDocs with the Material theme (pinned, hash-locked via uv), plus: a monorepo/aggregation approach (git submodules pinned to the RC commits, or a fetch step that checks out exact commits), `mkdocs-static-i18n`, Mermaid support, `mkdocstrings` (Python API pages), an OpenAPI renderer for service APIs, search, and versioning (`mike` or equivalent) so every release has its own docs version.
- Verify every plugin's current name, maintenance status and compatibility at build time; choose replacements if needed and record them in an ADR.

### B.3 Site navigation

```
Home
Getting started   (what is Pokus · concepts & glossary · tour)
Tutorials
Guides
  ├── Researchers
  ├── Operators
  ├── Scenario authors
  └── Developers
Reference
  ├── CLI · APIs · Configuration · Schemas
  ├── Tools · Primitives & probes · Policy rules
  ├── Database · Events · Telemetry
  ├── Metrics & statistics
  ├── Support matrix · Versions · Exit codes · Doctor checks
  └── Filesystem, accounts & sockets
Explanation
  ├── Architecture · Run lifecycle · Ground-truth blindness · Agent boundary
  ├── Target isolation · Reproducibility · Portability · Security
  ├── QA & validity controls
  └── Benchmark methodology
Scenario catalog   (generated: one page per scenario, public fields only — no ground truth)
Releases           (release notes, QA attestations, changelogs)
About              (how to cite · contributing · license)
```

The **scenario catalog** pages are generated from public scenario metadata only (ID, title, blueprint, risk level, isolation, tags, split). Ground truth, injection steps and verification details are **never** published on the site; a CI check scans the built site for ground-truth fields and fails if any appear.

### B.4 Build and publish

- `make site RC=<infra-sha>,<platform-sha>,<catalog-sha> QA=<qa-sha>`: fetch exact commits, run each repo's generators, build in strict mode, run the site-wide checks (links, coverage, GT-leak scan), produce `site/` and a tarball with its sha256.
- Every page footer shows the RC triple, the docs build date, and a link to the QA attestation if one exists for that triple.
- Publishing targets (ask me which): the Git host's Pages (private), a static tarball attached to each release, and optionally served on the lab itself through `pokus-lab docs serve` bound to `127.0.0.1` (reachable by researchers via SSH port forwarding or `tailscale serve`), so docs are available offline.
- PDF export of the Benchmark methodology and Architecture explanations (for publication appendices), with the RC triple on the title page.

### B.5 Definition of done

- The site builds in strict mode from a given RC triple with zero warnings.
- Coverage check reports 100 % for reference entries.
- GT-leak scan clean.
- Every tutorial carries a `last_verified` from a QA run on the same RC.
- Versioned docs for at least the current and previous release are browsable.
