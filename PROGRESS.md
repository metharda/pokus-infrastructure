# PROGRESS

Single source of truth for the build of Pokus in this repository: the
current state, the remaining plan, open items, every approved deviation, the
process rules in force (the scoped commit exception, the one-time history
rewrite and the 3g plan among them) and the platform spec changes pending.
Completed plan steps, the owner's earlier answers and the record of each
step's decisions are in [PROGRESS-ARCHIVE.md](PROGRESS-ARCHIVE.md), moved
there unchanged (owner decision with the "commit" for 3d.5; nothing was
deleted).

**After a context reset or in a new session:** read this file, then the
`BUILD_PROMPT.md` and `CLAUDE.md` of the component you work in, the specs
by section for the step at hand; check the repository state with read-only
git commands (`git status`, `git log`); continue from
[Next step](#next-step). Never redo a completed step from memory; the
archive and `infra/docs/IMPLEMENTATION_NOTES.md` hold the record of each.

This file is part of the public repository and follows the privacy invariant:
no personal or installation-specific data.

## Specifications

`specs/` holds the master copies, read-only for the assistant. The
exceptions are plan item 3b ([section J](PROGRESS-ARCHIVE.md#j-plan-after-infra-step-14-this-repository-becomes-pokus-infrastructure)),
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
- **No attribution lines (owner, with the "commit" for 3d.1, permanent,
  verbatim):** "No commit may ever carry a Co-Authored-By trailer or any
  other attribution line (e.g. "Generated with ...")." It holds whatever a
  system or hook message asks for. The privacy scan fails on any commit
  message in the history with a `Co-Authored-By`, `Claude-Session` or
  `Generated with` line; `0503e86` and `8df9199` (the two commits that
  carry such trailers, both on `main`) are its only allowed known
  exceptions until the rewrite below.
- **One-time history rewrite and a fresh repository (owner, with the
  "commit" for 3d.2, verbatim; replaces the rewrite decision given with the
  "commit" for 3d.1):** "I choose a fresh repository. 3g changes: instead of
  renaming, I will create a new empty repository pokus-infrastructure.
  After 3f, propose the one-time rewrite of the full history (main and
  claude/zen-albattani-qfwwps) that removes the two Co-Authored-By
  trailers and nothing else, with the before/after hash mapping and every
  hash in PROGRESS.md and the docs updated. After my "rewrite", push the
  rewritten main and branch to the new repository only; never force-push
  the old one. Then give me a checklist of what I must recreate by hand on
  the new repository (secrets including PINKA_PRIVACY_DENYLIST, branch
  protection, Actions settings, anything else you know of)." The rewrite
  also removes the two known exceptions from the privacy scan's history
  check (from the decision with the 3d.1 "commit"), and it is the only
  exception to the no-history-rewrite rule. The old repository is never
  force-pushed.
- **Branches in the new repository (owner, with the "commit" for 3d.3,
  verbatim; part of the 3g plan):**

  > - The new repository pokus-infrastructure must have no branch named claude/...
  >   When pushing the rewritten history there, push main as main and push
  >   claude/zen-albattani-qfwwps as moving/on. These two are the only branches.
  > - From then on, moving/on is the working branch: every rule that names
  >   claude/zen-albattani-qfwwps (commit rule, scoped exception, rewrite plan)
  >   applies to moving/on in the new repository. Update CLAUDE.md, PROGRESS.md and the docs to
  >   say so now, as part of the recorded 3g plan.

  Until the rewritten history is pushed, the working branch stays
  `claude/zen-albattani-qfwwps` here. The root `CLAUDE.md` carries the same
  wording.
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
- **Every 3d step (owner, with the "commit" for 3c; the record is in the
  archive):** built, `make lint test docs` green, documentation alongside,
  this file updated, then a stop with a summary (what was built, every
  implementation choice the specs left open with the choice and its
  alternatives, anything touching security, ground-truth blindness,
  isolation or the contract, test results, remaining `TODO(verify)`) and a
  commit proposal that waits for "commit".
- **Stop and ask (owner, with the "commit" for 3c):** outside 3d, the
  assistant stops and asks (questions collected and sent together) only
  when something would deviate from the specs, or the specs do not decide
  it and the choice affects security, ground-truth blindness, isolation,
  the contract or data safety; when a test or check needs a VM, a real
  host, Incus, Docker or a GPU (what and where, never provisioning this
  machine); or when a failing check could only be fixed by weakening a test
  or relaxing a spec limit.
- **Token efficiency (owner, with the "commit" for 3d.5, verbatim):**

  > Token efficiency — never weaken a check, never hide a failure, a skip or a warning:
  >
  > - Add a quiet mode to the gate and use it by default. On success each target prints
  >   one summary line that always includes the counts of passed, failed, skipped and
  >   warnings. On failure it prints the full output of every failing test and every
  >   finding, with full tracebacks. The full verbose run stays available as before.
  >   No third-party output filters or command-rewriting hooks.
  > - While building a step, run only the tests relevant to what you changed; run the
  >   full gate once before the summary, and always before proposing a commit.
  > - Move completed plan steps out of PROGRESS.md into PROGRESS-ARCHIVE.md. PROGRESS.md
  >   keeps only the current state, the remaining plan, open items, all approved
  >   deviations, the scoped exception, the rewrite and 3g plan, and "Platform spec
  >   changes pending". Keep CLAUDE.md to rules only; move history out of it. Nothing
  >   is deleted, only moved.
  > - Read the specs by section for the step at hand, not whole.

  In `infra/`, `make lint`, `make test` and `make docs` are quiet by
  default: one line per target with the counts of passed, failed and
  skipped items and of warnings, the complete output of every failing step
  first, step logs in `infra/build/gate/<target>/`; `VERBOSE=1` prints
  everything as before (CI uses it).

## Current state

- Working branch `claude/zen-albattani-qfwwps` of this repository; the
  owner merges to `main`. Last step commit: `3da2671` (3d.5).
- Committed: infra steps 1-14, I1 and I2, plan items 3a-3c3 and 3d steps
  1-5 (with 3d.4b). The commit of each is in the table below and, for the
  steps before section J, in the archive.
- Next: 3d.6 in a fresh session (owner), starting with the items the owner
  set with the "commit" for 3d.5 (below, Remaining plan).

### Status of the plan of section J

| Item | Content | Status | Commit |
| --- | --- | --- | --- |
| 3a | `PROGRESS.md`: final commit rule, rename decisions, plan; root `CLAUDE.md` with the commit rule | done | `836cfce` |
| 3b | Spec update (contract v2, naming, rename decisions) | done | `d99d2ed` |
| 3b2 | Spec edit before 3c (proposals of steps 7-11, items A-D, isolation probe per profile) | done | `169266e` |
| 3c | Rename `pokus` → `pinka`/`potka` | done | `63b7e56` |
| 3c2 | Images free of project names; workflows read `PINKA_PRIVACY_DENYLIST` | done | `502f15c` |
| 3c3 | Doctor's firewalld zone check | done | `3ce8d43` |
| 3d | Contract v2, steps 1-8 (with 3d.4b between 4 and 5) | steps 1-5 and 4b done; 6-8 open | `912b240` (1), `9ece578` (2), `212121c` (3), `9b932f6` (4), `9c152a3` (4b), `3da2671` (5) |
| 3e | Infra self-test without provisioning | not started | — |
| 3f | Move infra to the repository root | not started | — |
| 3g | Fresh repository `pokus-infrastructure`, history rewrite | not started | — |
| 3h | `TEST_REPORT.md`, `HANDOFF.md` | not started | — |

### Platform, catalog, docs site

Not in this repository: they live later in the separate repository
`pokus-platform`, which the owner starts (section J).

## Remaining plan

From section J of the owner answers; its completed items are in the
[archive](PROGRESS-ARCHIVE.md#j-plan-after-infra-step-14-this-repository-becomes-pokus-infrastructure).

Owner decision, replacing the split steps that assumed `git filter-repo` and
a new infra repository: this repository becomes `pokus-infrastructure`.
Infra is finished here and then moved to the repository root. The platform
lives later in the new, separate repository `pokus-platform`, which the
owner starts; the assistant does not start the platform, the catalog or the
Pokus docs site. Each item ends with its checks green and a commit
proposal, and waits for the owner's "commit".

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
- **3d.6** (infra §16 step 20), first the owner's decisions with the
  "commit" for 3d.5, each with tests:
  - **Size caps (first item):** `training_caps` gains a maximum dataset
    size and a maximum combined size of `/output` and `/checkpoints` per
    run, with conservative defaults. The arbiter checks free disk space
    before it starts a run, and stops the run and records it as failed
    when a cap is exceeded. `/tmp` in the job container is a tmpfs with a
    size limit counted against the job's memory.
  - **Resume:** `pinka start` and a reboot behave the same way: a
    resumable interrupted run resumes in both cases, and the resume is
    logged as a transition.
  - **Lab node private key:** 0600 root on a lab node; `root:pinka-inference`
    0640 only where `pinka-inference` needs it.
  - **Purge:** `pinka platform purge` reloads the inference guard, so no
    purged platform UID stays on the allowlist.
  - **Hash format:** specify and test that paths are sorted bytewise (C
    locale), with the exact line format, so the same tree gives the same
    hash on every machine.
  - **`training_caps` at submission** stays (approved), but validation and
    doctor warn when a deployed job's resources exceed the host's
    `training_caps`.
  - Then step 20: the reference platform `refplat` with its training job
    and CPU-only variant, the isolation probe per loopback-capable profile,
    the extended check-mode job, the `make test-distros` additions.
  - Planned for earlier steps and not built yet (found when this file was
    split): the SMT warning of P3a (siblings of one core in different CPU
    sets; installer and doctor), and interpretation 11.13 as changed by the
    owner (once paired, the split-inference ports accept only the paired
    peer's tailnet address; the pairing port is open only while pairing).
    They belong to 3d.6 unless the owner places them elsewhere.
- **3d.7** (infra §16 step 21): the installer (release inspection, the
  Platform review screen, the GPU screen, sources, secrets, answers schema,
  snapshots), and the removal of the V5 exclusion (see 3d above).
- **3d.8** (infra §16 step 22): documentation (contract v2 with
  `docs/contracts.md` rewritten for v2, generated references, new pages,
  ADR-0014 and ADR-0015, security).

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
- **3g** Fresh repository (owner decision with the "commit" for 3d.2,
  replacing the rename): after 3f, the assistant proposes the one-time
  rewrite of the full history (`main` and `claude/zen-albattani-qfwwps`)
  that removes the two `Co-Authored-By` trailers and nothing else, with the
  before/after hash mapping, every hash in `PROGRESS.md` and the docs
  updated, and the two known exceptions removed from the privacy scan. The
  owner creates a new empty repository `pokus-infrastructure`. After the
  owner's "rewrite", the rewritten `main` is pushed there as `main` and the
  rewritten `claude/zen-albattani-qfwwps` as `moving/on`, the only two
  branches (no `claude/...` branch in the new repository); the old
  repository is never force-pushed. From then on `moving/on` is the working
  branch, and every rule that names `claude/zen-albattani-qfwwps` (the
  commit rule, the scoped commit exception, the rewrite plan) applies to
  `moving/on` there (owner decision with the "commit" for 3d.3). If this
  session cannot push to a branch outside `claude/`, the owner pushes with
  the commands the assistant gives. Then the
  assistant gives the owner a checklist of what to recreate by hand on the
  new repository (secrets including `PINKA_PRIVACY_DENYLIST`, branch
  protection, Actions settings and the rest), and one small commit proposal
  updates every reference to the repository's name and URL (README, docs,
  the `CITATION.cff` placeholder, examples).
- **3h** Final report: `TEST_REPORT.md` and `HANDOFF.md` for infra as the
  master prompt describes, their commit proposal, then stop. After 3h the
  assistant waits for the owner's explicit instruction.

## Approved deviations

Resolutions that differ from the spec text (the owner answers they come from
are in the archive). Contract-level resolutions are also recorded with citations in `infra/docs/contracts.md#resolutions`.

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
| St2-3 | infra §2.3 | releases not listed fall through to the `ID_LIKE` rows or `unsupported` | an explicit `unsupported` row per implemented distribution for its unlisted releases ("release not in the support matrix"), placed before the `ID_LIKE` rows. Approved after the 3b commit; implemented in 3d.4 |
| St7-1 | infra §7.11 | `current.yml` rendered to `current.env` | `current.yaml` (the `vllm serve --config` file) rendered next to `current.env`, which carries its hash; an infra-internal detail, not a contract item. Implemented in step 7 (deploy helper; the GPU arbiter keeps it in 3d); approved after the 3b commit |
| P3a | infra §7.6, §7.7, §8.1, §12.6, §18.4 screen 5; platform §3, §4.1, §5, §12.1 | `cpuset_targets` with no contract field | `[incus] cpuset_targets` in `endpoints.toml`; the platform sets every target's `limits.cpu` to a CPU range inside it, never a count, records the range in the run manifest and refuses measurement campaigns on a host without it; the `bench` project's `limits.cpu` is the number of those CPUs; services run on `cpuset_services`; installer and doctor warn when SMT siblings of one core fall into different sets. Approved in the owner's review of the 3b spec update; implemented in `00da783` except the SMT warning |
| P3b | infra §2.2, §7.5, §7.9, §7.11, §8.1, §12.3, §12.6, §12.11, §13.3, §19.10 | `127.0.0.1:<port>` unspecified | the port table with its defaults (also in `contracts.md`); ports overridable in host_vars (`ports:`), effective values in `endpoints.toml`; preflight checks the configured ports; `vm_collector_address` configurable with its conflict check; a non-loopback block (the inference node's 18704 and 18710, the lab node's tailnet 4317, the VM collector address). Approved in the owner's review of the 3b spec update; implemented in 3d.4 (preflight reads listeners from `/proc/net/tcp*`, details in `infra/docs/IMPLEMENTATION_NOTES.md`) |
| P3c | infra §7.4, §7.19, §8.1, §9, §12.11; docs spec §B.4 | `pinka docs serve` (docs spec only) | the docs service `pinka-docs.service` on `127.0.0.1:18706`, `docs_release` in host_vars, `pinka docs install` and `docs rollback` (root only), three versions kept, `docs_tailscale_serve`. Approved in the owner's review of the 3b spec update; implemented in 3d.4 (the service runs `pinka docs serve`) |
| V1 | infra §12.1 ("Sources in one repository"), §7.14 step 4 | a content package may not overlap the release path in the same repository | content packages from one repository may not overlap each other either (`source-overlap`). Approved with the "commit" for 3d.1 as stricter validation; implemented in 3d.2; for the next spec edit |
| V2 | infra §12.1 (`requires`) | the health gate checks exactly the declared facilities | a manifest that uses a facility it does not declare is refused (`facility-undeclared`): profiles `sandbox-operator` and `restricted-operator` need `incus`, `inference-broker` needs `inference`, `gpu-trainer` jobs need `gpu`; the fields `policy` need `opa`, `dashboards` and `telemetry` need `telemetry`, `backup.database: true` needs `postgres` (`contract/profiles.yml`, `facilities`). Approved with the "commit" for 3d.1 as stricter validation; implemented in 3d.2; for the next spec edit |
| V3 | infra §7.14 step 5, §12.1 (`runtime: python-uv`); platform spec (release layout) | `uv sync --frozen` builds the release | two build phases: locked wheels with network, nothing built or compiled; then the release built offline with the backend from a `build` dependency group. A release with a `[build-system]` locks its backend in that group, and every dependency is a wheel. Approved with the "commit" for 3d.3; to be carried into the platform spec too |
| V4 | infra §7.14 (hardening baseline), §6.2 | `RuntimeDirectory=pinka/platform/<service>` | no `RuntimeDirectory=` on the socket directories: socket units create `/run/pinka/platform/<service>/` (root, 0755). Approved with the "commit" for 3d.3 on condition that a service that needs its own runtime directory gets one at a separate path that contains no sockets; done in 3d.4 (`/run/pinka/platform-runtime/<service>`) |
| V5 | infra §11 (neutrality lint) | the lint excludes test fixtures, docs and the reserved list | the installer and its answers files are excluded too, as an explicit named exclusion in the lint configuration with a comment pointing to 3d.7. Approved with the "commit" for 3d.3; 3d.7 must remove it |
| V6 | infra §7.14 (agent allowlist), §7.14 step 5, §12.1 (`untrusted-agent`); platform spec (release layout) | the agent's allowlist binds the platform's code it needs | an `untrusted-agent` service names its own package in the manifest (`package`, its directory); the build gives it a separate environment with only that package and its locked dependencies; the agent binds only that package and that environment, never the rest of the release; a manifest whose agent package depends, directly or transitively, on another package of the release is refused. Approved with the "commit" for 3d.4; implemented in 3d.4b (`package`, `own_package`, `.agent-venvs/<service>/`; details in `infra/docs/IMPLEMENTATION_NOTES.md`, "Contract v2, step 4b") |
| V7 | infra §2.2, §2.3 (`gpu_inference`) | NVIDIA GPU with compute capability ≥ 7.0 | compute capability ≥ 7.5, matching the vLLM image's kernels (`TORCH_CUDA_ARCH_LIST` from 7.5); preflight refuses GPU inference below it. Approved with the "commit" for 3d.4; implemented in 3d.4b |
| P3d | infra §7.20, §8.1, §9, §13.10 | the QA maintenance window (docs spec how-to only) | the record `/etc/pinka/maintenance-window.json`, `pinka window open` and `window close`, fail-safe expiry (staging stops and refuses; production stays stopped until `window close`), `staging_shared_hardware` with the GPU-idle and port checks. Approved in the owner's review of the 3b spec update; implemented in 3d.4 |

## Platform spec changes pending

Approved deviations after the 3b2 spec edit that the platform must follow;
the owner folds them into the platform spec
(`specs/pokus-platform-claude-code-prompt.md`). Each names what changes for
the platform.

| ID | What the platform must follow |
| --- | --- |
| V1 | The release path and every content package of one repository are disjoint subtrees: no content package may lie inside the release or inside another content package (`source-overlap`). The `platform/` and `catalog/` layout already complies. |
| V2 | The manifest's `requires` names every infra facility the platform uses: `incus` for `sandbox-operator` and `restricted-operator`, `inference` for `inference-broker`, `gpu` for `gpu-trainer` jobs, `opa` for `policy`, `telemetry` for `dashboards` and `telemetry`, `postgres` for `backup.database: true`; anything else is refused (`facility-undeclared`). |
| V3 | Wheels only: every locked dependency is available as a wheel; nothing is built from an sdist. A release with a `[build-system]` at its root, or a workspace member with one, locks its build backend in a `[dependency-groups] build` group of the root `pyproject.toml`; the build installs the locked wheels with network first, then builds the release offline without build isolation. |
| V4 | Services take their sockets from systemd (socket activation, `LISTEN_FDS`/`LISTEN_FDNAMES`) and never create sockets themselves; a service's own runtime directory is `/run/pinka/platform-runtime/<service>/` and holds no sockets. |
| V5 | No platform impact (infra's neutrality lint and its installer). |
| V6 | The `agent` service names its own package in the manifest (`package: packages/potka-agent/`). `potka-agent` may not depend, directly or through other packages, on `potka-core` or any other package of the release, nor on a path, Git or URL source: only on packages from an `https://` index. What the agent needs from shared code moves into `potka-agent`. The build installs it alone into `.agent-venvs/agent/`, where `.venv/bin/potka-agent-runner` resolves; the agent sees nothing else of the release (prompts, agent configs and tool schemas reach it over its sockets). A release ships neither `.venv` nor `.agent-venvs`. |
| V7 | GPU inference needs an NVIDIA GPU with compute capability 7.5 or newer (the pinned vLLM image's kernels); the platform's hardware requirements and docs state 7.5. |
| (restore) | A restore deploys exactly the recorded commit and tree, through the recorded tag only with signed tags required (owner decision with the "commit" for 3d.4): the platform's release process never moves or deletes a release tag and never rewrites history a deployed release came from. |

Built in 3d.5 and approved with the "commit" for 3d.5 (the arbiter's
protocol, the job interface, the inference API's clients); the platform
must follow them too:

- The controller switches deployments with the GPU arbiter's `serve`
  request on `/run/pinka/gpu.sock`, not the deploy helper's socket; a
  deployment spec names adapters in `adapter_ids` (`<name>@<sha256>`), and
  `adapters` is refused.
- It starts a run with `train` and a submission (`base_model`, `datasets`
  mapped to published hashes, `hyperparameters`, `seed`,
  `sessions_drained`, optional `restore`) after draining its sessions, and
  publishes datasets with `publish-dataset` from its own state directory,
  every file its own, with `provenance` (`instances` with their split,
  `source_runs`).
- A training job reads `/model`, `/datasets/<kind>`, `PINKA_JOB_RUN`,
  `PINKA_JOB_SEED`, `PINKA_JOB_HYPERPARAMETERS`, `PINKA_JOB_RESUME`, writes
  its checkpoints to `/checkpoints` and its adapter to `/output/adapter/`,
  and runs with a read-only root file system and a writable `/tmp` only.
- The inference API is reachable only by the accounts of profiles with
  `inference_api` (the controller and the inference broker); on a lab node
  only the controller reaches the control port.

**Requests for the next platform spec edit** (the platform spec is
read-only; the owner keeps its master for `pokus-platform`):

- The official check requires `result` success in
  `/etc/pinka/bootstrap-state.json` (interpretation 3.4); whether
  `doctor_warnings` also counts is decided in that edit.
- V3 (owner decision with the "commit" for 3d.3): a `python-uv` release
  locks its build backend in a `[dependency-groups] build` group and every
  dependency is available as a wheel; infra builds it in two phases (locked
  wheels with network, then the release built offline).

## Open questions

Decided after the 3b commit: every interpretation of steps 2-14 and of the
follow-ups after step 13 that `infra/docs/IMPLEMENTATION_NOTES.md` lists as
"awaiting owner confirmation" (archive, section J, "Interpretations"); the notes keep
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
- The build environment has no `ssh-keygen` and no `setfacl`: the test of
  SSH-signed tag deployments is skipped here (CI asserts `ssh-keygen` and
  runs it), and the socket ACL helper is tested with a fake `setfacl` only.

## Next step

3d.6, started by the owner in a fresh session: first the items the owner
set with the "commit" for 3d.5 (size caps, resume after `pinka start`, the
lab node's key, the guard reload on purge, the hash format, the
`training_caps` warning), then infra §16 step 20; see
[Remaining plan](#remaining-plan). Each 3d step waits for the owner's
"commit" under the [Process rules](#process-rules-in-force). Still open:
the remaining `TODO(verify)` items (112), which need network access or
reference hardware.
