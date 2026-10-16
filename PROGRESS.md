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

- **CI after every push (owner, with the "commit" for 3d.7 and for its CI
  fix):** verbatim in `CLAUDE.md` ("CI after every push"). After every push
  the CI run of the pushed commit is checked and every job reported
  separately in the next summary (passed, failed, or the known red:
  `lock-check` while `TODO(verify)` markers remain, nothing else). A failure
  in any other job, the non-blocking `check-mode-extended` lanes included,
  blocks the next step. So does a failure of a weekly scheduled run (owner,
  with the "commit" for the symbolic-link fix: "a difference found by the
  weekly run is a blocker like any other CI failure").
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
- **No pull requests, no merges (owner, with the "commit" for 3d.8,
  verbatim; part of the 3g plan):**

  > - Never open a pull request and never merge anything. Finish all work on
  >   claude/zen-albattani-qfwwps. After the rewrite, I push it myself to the new
  >   repository as moving/on, and I merge moving/on into main myself. Record this in
  >   CLAUDE.md and PROGRESS.md as part of the 3g plan.
  > - In HANDOFF.md (3h), include the exact commands for that push and for merging
  >   moving/on into main, and what to check after the merge (CI on main, the weekly
  >   jobs, which only run from main).

  The root `CLAUDE.md` carries the same wording.
- **Provisioning runs after 3f, and the end of the exception (owner, after
  3e, verbatim):**

  > - Start 3f now, under the scoped exception. I will do the provisioning runs after
  >   3f, on the final layout, so the tested tree is the one that ships.
  > - After 3f is committed and its CI is reported, give me the provisioning runs as one
  >   checklist, updated for the new paths. For each item:
  >   - what it tests, and which earlier step's unverified parts it covers;
  >   - the machine it needs: a disposable VM or a real host, the distribution, CPU, RAM
  >     and disk, whether it needs KVM or nested virtualization, a GPU, or network
  >     access to specific hosts;
  >   - whether it can run on a Linux machine where I am root but which is not
  >     disposable, and what it would change on that machine;
  >   - the exact commands, in order, from a fresh clone;
  >   - exactly what to send back to you (exit codes, the commit hash, which log
  >     directories or files).
  >   Order the list so the cheapest checks come first and a failure early stops me
  >   from running the expensive ones.
  > - After 3f, the normal commit rule applies again: every fix that comes out of my
  >   runs waits for my "commit".
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
- **Quiet gate decisions (owner, with the "commit" for the quiet gate):**
  CI runs with `VERBOSE=1`; the success line gives counts and the log
  directory; a warning is counted at least once (twice is possible, fewer
  never); the hash format and the `training_caps` warning belong to 3d.6.
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
  deliverables pending](#owner-deliverables-pending).

### Status of the plan of section J

| Item | Content | Status | Commit |
| --- | --- | --- | --- |
| 3a | `PROGRESS.md`: final commit rule, rename decisions, plan; root `CLAUDE.md` with the commit rule | done | `836cfce` |
| 3b | Spec update (contract v2, naming, rename decisions) | done | `d99d2ed` |
| 3b2 | Spec edit before 3c (proposals of steps 7-11, items A-D, isolation probe per profile) | done | `169266e` |
| 3c | Rename `pokus` → `pinka`/`potka` | done | `63b7e56` |
| 3c2 | Images free of project names; workflows read `PINKA_PRIVACY_DENYLIST` | done | `502f15c` |
| 3c3 | Doctor's firewalld zone check | done | `3ce8d43` |
| 3d | Contract v2, steps 1-8 (with 3d.4b between 4 and 5) | done | `912b240` (1), `9ece578` (2), `212121c` (3), `9b932f6` (4), `9c152a3` (4b), `3da2671` (5), `a686e28` (6), `0d72e54` (7), `e525a1e` (8), `a425c7f` (CI fix of 8 and the `release-package` job) |
| 3e | Infra self-test without provisioning | done without provisioning; the VM, host, Incus, Docker and GPU runs wait for the owner (after 3f) | `5ea9b82` |
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

- **3e** Infra self-test: everything that runs without provisioning, as in
  the master prompt's self-test rules (never weaken tests; "not tested"
  stated honestly). Anything that needs a VM or a real host is described
  exactly (what and where) and runs only after the owner's confirmation.
  Results, the faults it found, what ran in CI instead, and the exact
  commands for each untested part: `infra/docs/IMPLEMENTATION_NOTES.md`,
  "Infra self-test without provisioning".
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
  `moving/on` there (owner decision with the "commit" for 3d.3). The owner
  pushes the rewritten history to the new repository and merges `moving/on`
  into `main` himself; the assistant never opens a pull request and never
  merges anything, finishes all work on `claude/zen-albattani-qfwwps`, and
  gives the push commands (owner, with the "commit" for 3d.8). Then the
  assistant gives the owner a checklist of what to recreate by hand on the
  new repository (secrets including `PINKA_PRIVACY_DENYLIST`, branch
  protection, Actions settings and the rest), and one small commit proposal
  updates every reference to the repository's name and URL (README, docs,
  the `CITATION.cff` placeholder, examples).
- **3h** Final report: `TEST_REPORT.md` and `HANDOFF.md` for infra as the
  master prompt describes, their commit proposal, then stop. `HANDOFF.md`
  gives the exact commands for pushing the rewritten history to the new
  repository as `moving/on` and for merging `moving/on` into `main`, and
  what to check after the merge: CI on `main`, and the weekly jobs, which
  run only from `main` (owner, with the "commit" for 3d.8). After 3h the
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
| V5 | infra §11 (neutrality lint) | the lint excludes test fixtures, docs and the reserved list | the installer and its answers files are excluded too, as an explicit named exclusion in the lint configuration with a comment pointing to 3d.7. Approved with the "commit" for 3d.3; removed in 3d.7 (the installer and `tests/answers/` are scanned, a test keeps it so) |
| V6 | infra §7.14 (agent allowlist), §7.14 step 5, §12.1 (`untrusted-agent`); platform spec (release layout) | the agent's allowlist binds the platform's code it needs | an `untrusted-agent` service names its own package in the manifest (`package`, its directory); the build gives it a separate environment with only that package and its locked dependencies; the agent binds only that package and that environment, never the rest of the release; a manifest whose agent package depends, directly or transitively, on another package of the release is refused. Approved with the "commit" for 3d.4; implemented in 3d.4b (`package`, `own_package`, `.agent-venvs/<service>/`; details in `infra/docs/IMPLEMENTATION_NOTES.md`, "Contract v2, step 4b") |
| V7 | infra §2.2, §2.3 (`gpu_inference`) | NVIDIA GPU with compute capability ≥ 7.0 | compute capability ≥ 7.5, matching the vLLM image's kernels (`TORCH_CUDA_ARCH_LIST` from 7.5); preflight refuses GPU inference below it. Approved with the "commit" for 3d.4; implemented in 3d.4b |
| P3d | infra §7.20, §8.1, §9, §13.10 | the QA maintenance window (docs spec how-to only) | the record `/etc/pinka/maintenance-window.json`, `pinka window open` and `window close`, fail-safe expiry (staging stops and refuses; production stays stopped until `window close`), `staging_shared_hardware` with the GPU-idle and port checks. Approved in the owner's review of the 3b spec update; implemented in 3d.4 |
| St12-1 | infra §11 (Ansible) | `make test-roles`: the provider-agnostic roles in a throwaway Incus system container or VM, only when invoked | `make check-mode` (the provider-agnostic roles `base`, `users_groups`, `ssh` and `layout` in check mode in a disposable Docker systemd container per family, also CI's required job, I2) and `make test-distros` (the full bootstrap in disposable Incus VMs, §19.9) replace it; there is no `test-roles` target and no alias. Raised by the 3e self-test; approved by the owner after 3e ("record it as an approved deviation (make check-mode and make test-distros replace it). No alias."); built in step 12 |

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
| (contract copy) | A platform repository that keeps a copy of `docs/contracts.md` keeps it identical to the copy in the infra release it pins, and the platform's CI checks it (owner decision with the "commit" for 3d.8). The attestation format and the official check are the platform's, documented on its own site (infra only provides the QA directories, the trusted keys and the commits and tree hashes an attestation is checked against). |

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
- Found in 3d.6, approved with the "commit" for 3d.6: infra's database
  bootstrap gives a platform's service roles no privilege on the schemas
  `public` and `restricted`; the platform's `migrate` hook grants its own
  services what they use (usage on the schemas, rights on the tables), and
  the restricted check accepts only the profiles allowed there. A
  controller that polls the GPU arbiter rarely reads the current run from
  `status` and treats a run with no transition left in the answer (it shows
  the last ten) as ended.

## Open questions

Decided after the 3b commit: every interpretation of steps 2-14 and of the
follow-ups after step 13 that `infra/docs/IMPLEMENTATION_NOTES.md` lists as
"awaiting owner confirmation" (archive, section J, "Interpretations"); the notes keep
the original wording and the findings of each step. Still open, from step 1
("Questions raised by step 1"): reference agents for scenario validation and
the QA results schema (both for `pokus-platform` and `pokus-qa`), and the
page-ownership table (H5). Settled since: the port table (P3b), the
project files at the monorepo root (3f) and `make test-roles` (St12-1,
after 3e).

## Owner deliverables pending

- **The 3e runs that need a VM, a real host, Incus, Docker or a GPU**
  (owner instruction with the "commit" for `a425c7f`: stop and say exactly
  what to run and where). The owner runs them after 3f, on the final
  layout; after 3f's commit and CI report the assistant gives them as one
  checklist ordered cheapest first, with the paths of the root layout
  (owner, after 3e). Listed so far in `infra/docs/IMPLEMENTATION_NOTES.md`,
  "Infra self-test without provisioning", "Not tested":
  1. bootstrap in check mode on a disposable Ubuntu 24.04 VM;
  2. `make test-distros` on a machine with Incus VMs;
  3. a full bootstrap on a real supported host, with a second run at
     `changed=0` and `pinka doctor --smoke`;
  4. a TUI install compared with an answers replay;
  5. the GPU checks: vLLM serving with the tool-call check, GPU training,
     MOK, DCGM;
  6. the first golden image builds;
  7. optionally, the Docker check-mode runs, which CI already runs on every
     push.
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

`make verify-todos` lists every marker in the tree (110 after step 13, 117
after 3d.6 with the Galaxy fallback pins, 129 after 3d.7 with the
collection artifacts, 128 after the lock's header comment stopped naming the
marker, 118 with the owner's collection pins; details
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
- 3d.6: the release tarball URL and sha256 of each pinned Ansible
  collection (`ansible_collections.fallback` in `infra/versions.lock.yml`).
- 3d.7: the Galaxy artifact URL and sha256 of each of the five pinned
  collections (`ansible_collections.artifacts` in `infra/versions.lock.yml`)
  and their copy in the generated `infra/bootstrap/lib/pins.sh`. Resolved
  with the owner's `make lock-collections` output (`4aeb35b`); the four
  `ansible_collections.fallback` entries go with the fallback in 3d.8.

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
- `make setup` installs the pinned Ansible collections from Galaxy and, when
  Galaxy is unreachable (as here), from the GitHub release tarballs pinned in
  `ansible_collections.fallback` of the lock, each only after its sha256
  matched (3d.6). Those pins are `TODO(verify)`: GitHub's web pages and API
  answer 403 here for the ansible-collections organization, so whether each
  release carries the built tarball, and its URL and sha256, could not be
  read (the release tags were confirmed with `git ls-remote`). Until they are
  verified, setup without Galaxy stops and says so; this session's
  collections were installed earlier from the release tags into the
  git-ignored `infra/.ansible-collections/`, which setup verifies and keeps.
  Since 3d.7 the lock also holds `community.library_inventory_filtering_v1`
  1.1.5 (a dependency of `community.docker`); it has no fallback entry
  until the owner decides on the fallback (3d.7 finding: the GitHub
  releases carry no Galaxy artifact), so setup without Galaxy names it as
  missing. In this session it was installed into the same git-ignored
  directory from its release tag 1.1.5 (commit
  `5f70dd8678885157cb186e8b64382a30cab3e12f`), which setup verifies.
- Since 3d.7 bootstrap installs the collections only from the artifacts of
  `ansible_collections.artifacts` in the lock, each after its sha256
  matched. Their URLs, sha256s and sizes are the owner's
  `make lock-collections` output of 2026-10-15 (`4aeb35b`).
- `make lock-collections` listed only `COPYING` as `ansible.posix`'s
  license file: its rule took only names beginning with `COPYING` or
  `LICENSE` and missed `PSF-license.txt`. Fixed with the pins (`4aeb35b`):
  any file in a `LICENSES` directory, or a text file whose name
  contains `COPYING`, `LICENSE`, `LICENCE` or `NOTICE`; REUSE's `.license`
  annotations excluded.
- CI's `infra` workflow was red on `a686e28` (3d.6), found in 3d.7 from
  the run's logs (the build environment cannot run the check-mode lanes:
  no Docker daemon): every `check-mode` and `check-mode-extended` job
  stopped at `users_groups`, whose argument spec still required
  `service_accounts`, which 3d.3 (`212121c`) had removed from group_vars;
  the uv tests failed because 3d.6's Makefile installed uv to a doubled
  path in CI (`infra//home/runner/...`); the drift check failed because the
  generated `pinka-platform` reference held this checkout's path as the
  `--contract` default (since 3d.1). All three are fixed in 3d.7, each with
  a test (every required role option has a source; no generated file names
  the checkout's path; the uv install path). The check-mode lanes have not
  run past `users_groups` since 3d.3; their result after the fix is known
  only from CI. `lock-check` stays red while `TODO(verify)` markers remain.
- CI of `0d72e54` (3d.7): lint, test, docs and the six required
  check-mode jobs passed; `lock-check` was red on `TODO(verify)` markers
  only. Every `check-mode-extended` job stopped at the observability role:
  ansible-core 2.21 refuses a conditional whose result is not a boolean,
  and `when: observability_platform_dashboards` is a mapping (`{}` without
  a platform), so a real provisioning run stopped there too, with or
  without a platform. Ten more such conditionals were in the platform role
  (`active_platform.database`, `.cli`, `.run_network`, `platform_plan.replace`,
  `platform_retire.cli_link`, `.cli_wrapper`); they now use `is truthy`, and
  `ansible/tests/test_conditionals.py` requires a known boolean for every
  bare condition and runs ansible-core's own check. The lock's header
  comment named the marker itself, so `lock-check` would have stayed red
  after every pin was verified (since step 4); reworded, with a test.
- The installer TUI passed `--skip-tags=images` (images chosen, built
  later) and `--tags=...` to bootstrap, whose parser accepted only the
  space-separated form, so such an install stopped with a usage error
  (since step 13). Fixed in 3d.7: options with a value also take
  `--option=value`, with a test that bootstrap accepts every option the
  TUI builds.
- Candidate for the next maintenance update of the pins (owner decision:
  keep the pin until then): `containers.images.postgres`, whose tag
  `18.6-trixie` now points to
  `sha256:74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336`;
  the lock keeps
  `sha256:5a5a84b19854a9ffaa54082c166ff4ec27473a361e496e5ea167f298f2da9722`
  (`lock-check` warns about it).
- `make lock-collections` refused every symbolic link in an artifact, so
  the owner's first run stopped at `community.general`'s
  `tests/utils/shippable/aix.sh`. Fixed after `d5fe24d` (separate commit):
  one artifact rule (`infra/tools/lockcheck/pinka_lockcheck/artifact.py`)
  for `make lock-collections`, `make setup` and bootstrap, accepting only
  relative links that stay inside the collection (`3d4bab2`).
- MkDocs 1.6.1 (pinned) has had no release since 2024-08-30, and the Material
  for MkDocs team announces that MkDocs 2.0 removes plugins. Builds work with
  the pin; the replacement decision belongs to the docs-site plugin review
  (docs spec §B.2) and needs an ADR and owner approval.
- The build environment's default uv (0.8.17) is older than the pinned
  0.12.23; since 3d.6 `make setup` installs the pinned uv into
  `infra/build/tools/` (checked against its sha256) and runs `uv sync` with
  it, so no uv on `PATH` is needed. CPython 3.9.25 and 3.12.11 are installed
  in user space by uv.
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

- A fresh session on a machine that reaches PyPI, npm and GitHub can run
  `make setup` and the full gate from the repository alone (3d.6: setup
  installs the pinned uv itself and falls back to the pinned release
  tarballs when Galaxy is unreachable); only the PostgreSQL 18 SQL tests run
  in CI only (PGDG is not reachable from here; CI installs 18, the SQL tests
  here use the newest local `/usr/lib/postgresql/<major>/bin`, 16 in this
  image). Two conditions remain, stated so they are not hidden: without
  Galaxy the fallback works only once its pins are verified (see the entry
  above), and the machine provides a Chromium or Chrome for the Mermaid check
  (the image's `/opt/pw-browsers`; mermaid-cli's own browser download is
  disabled) and a local PostgreSQL server for the SQL tests. Reachable here:
  PyPI, npm, GitHub releases of uv, OPA and the CPython builds.
- The reference platform's `make test-distros` stages and the extended
  check-mode run with it enabled have not run yet: this environment has
  neither Incus nor Docker. They are covered by tests against fake `incus`
  and `docker` commands; their first real run is the owner's (on a
  developer machine or a stopped lab host). The VMs have no GPU, so the
  training stages use `tunecpu`, and vLLM serving an adapter is tested only
  on GPU hosts.
- The preflight test that compares reports with the reference
  `jsonschema` validator skips in the Python 3.9 run (`make test-py39`,
  standard library only): `jsonschema` 4.26.0, the pinned version, needs
  Python 3.10. The same test runs and passes in the pytest step of every
  `make test` (Python 3.12) and in CI; the skip has existed since step 2
  and is now counted by the quiet gate.

## Next step

3f, the move to the repository root, under the scoped exception (owner,
after 3e). The exception ends when 3f is committed; from then on every
commit waits for the owner's "commit". After 3f's commit and its CI
report: the checklist of provisioning runs on the root layout (see
[Owner deliverables pending](#owner-deliverables-pending)), then 3g. Still
open: the remaining `TODO(verify)` items (113), which need network access
or reference hardware.
