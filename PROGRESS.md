# PROGRESS

Single source of truth for the build of Pokus in this repository: the
current state, the remaining plan, open items, every approved deviation, the
process rules in force (the end of the scoped commit exception, the
one-time history rewrite and the 3g plan among them) and the platform spec changes pending.
Completed plan steps, the owner's earlier answers and the record of each
step's decisions are in [PROGRESS-ARCHIVE.md](PROGRESS-ARCHIVE.md), moved
there unchanged (owner decision with the "commit" for 3d.5; nothing was
deleted).

**After a context reset or in a new session:** read this file, then
`CLAUDE.md` and `docs/BUILD_PROMPT.md`, the specs by section for the step
at hand; check the repository state with read-only
git commands (`git status`, `git log`); continue from
[Next step](#next-step). Never redo a completed step from memory; the
archive and `docs/IMPLEMENTATION_NOTES.md` hold the record of each.

This file is part of the public repository and follows the privacy invariant:
no personal or installation-specific data.

## Specifications

`specs/` holds the master copies, read-only for the assistant. The
exceptions are plan item 3b ([section J](PROGRESS-ARCHIVE.md#j-plan-after-infra-step-14-this-repository-becomes-pokus-infrastructure)),
the spec update for contract v2 (`d99d2ed`), one further edit before 3c
(3b2 in section J), the 3f-1 spec edit (owner, with the decisions on
the 3f-1 proposal: "I authorize one spec edit now, limited to 3f-1 D1, D2,
A, B, C, E and F with these decisions. [...] Specs are read-only for you
again after that commit."), and the small spec edit after D1 (owner, with
the "commit" for `74377ec`: "Decisions on points 1 and 4, for the small
spec edit I authorized", limited to the GPU guard and the pre-existing
access). After the commit of each, the specs are read-only again.
Component copies are byte-identical.

| File | Title | sha256 |
| --- | --- | --- |
| `specs/pokus-infrastructure-claude-code-prompt.md` | Build Prompt: pokus-infrastructure (Pokus Infrastructure, pinka) | `e01ad3d75a8b3d2792d6ee56c2e4c7cda281cf817d4ac178b5e356b82e732492` |
| `specs/pokus-docs-specification.md` | Pokus Documentation Specification | `5cd75c355eaf3b7d237e87f6260e61080672a8b328b3276828d1a3601b9711f8` |
| `specs/pokus-infrastructure-qa-spec.md` | Pokus Infrastructure — Real-Machine Test Specification | `79bcb4fc75343b736a1f5e728cbee12d12574a2d2524530a4efff79bfaf936cc` |

These are the specs updated in plan item 3b: contract v2 with decision 7's
GPU training, N2-N4, the pinka/potka naming and the rename decisions (section
I), applied from the proposal's Spec changes list, with the owner's review
of the first draft applied (see the 3b notes in section J), and the edit
3b2 before 3c (section J). They keep the privacy
invariant (infra §13.12, platform §16.9, docs spec §A.4 and §A.6) and the
docs-alongside rule (infra §0.8, platform §0.7). The previous checksums were
`4c7de813…` (infra), `ba3626f3…` (platform) and `717f3583…` (docs). Copies:
`docs/BUILD_PROMPT.md` (infra prompt) and `docs/DOCS_SPEC.md` (docs spec),
byte-identical. At 3f the infra master was renamed from
`specs/pokus-infra-claude-code-prompt.md` (Q13; its content and checksum
are unchanged), and the platform prompt's master
(`specs/pokus-platform-claude-code-prompt.md`, `cda5274f…`) left this
repository; the owner keeps it for `pokus-platform` (section J). The 3f-1
spec edit changed the infra prompt from `a07bbc3b…` to `a661077c…`
(sections listed in `docs/IMPLEMENTATION_NOTES.md`, "3f-1 spec edit"), and
the small spec edit after D1 from `a661077c…` to the checksum above
(`docs/IMPLEMENTATION_NOTES.md`, "Spec edit after D1"); its copy
`docs/BUILD_PROMPT.md` has the same checksum.

The real-machine test specification (`specs/pokus-infrastructure-qa-spec.md`)
was written outside this session and added verbatim with the records of
3f-1 (owner, after 3f): "Add the file verbatim to specs/ in the commit that
records 3f-1, record its sha256 in PROGRESS.md, and treat it as read-only
like the other spec masters: never edit it, and it is not part of your
gate." It instructs a test agent on the owner's test computers; the owner
edits it. Real-machine tests are named by its numbers (4.1, 4.2, ...): "From
then on, give me the real-machine tests as the file numbers them (4.1, 4.2,
...), so the agent and I use the same numbers." It is reviewed in 3f-1.G.
The owner replaced it with a version that changes only tests 4.1 and 4.4
(owner, with the "commit" for `74377ec`: "Replace the file verbatim in the
same commit as the small spec edit, record the new sha256 in PROGRESS.md,
and keep it read-only."); the previous checksum was `65e2ac38…`.

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
  set in the local git configuration only. `CLAUDE.md` states the same rule
  with the same wording.
- **Scoped commit exception (owner, with the "commit" for 3c, verbatim):**

  > "For plan items 3c2, the firewalld fix, 3e (non-provisioning parts only) and 3f, the
  > assistant commits and pushes each completed item to the working branch
  > claude/zen-albattani-qfwwps without waiting for 'commit', after its checks pass. Never to
  > main, no amend, no force push, no history rewrite, no tags. 3d is NOT covered: every 3d
  > step waits for my 'commit' as usual. The exception ends automatically when 3f is
  > committed; from then on the final commit rule applies again unchanged. It never covers
  > hook prompts."

  It covered only 3c2, the firewalld fix, the non-provisioning parts of 3e
  and 3f, each committed after its checks passed; every 3d step waited for
  "commit". It ended when 3f was committed: from then on every commit waits
  for the owner's "commit" under the final commit rule. `CLAUDE.md` says
  that it ended and points here for its text.
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
  "commit" for 3d.1; its fresh repository is replaced after 3f by the
  rename below):** "I choose a fresh repository. 3g changes: instead of
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
  `claude/zen-albattani-qfwwps` here. `CLAUDE.md` carries the same
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

  `CLAUDE.md` carries the same wording.
- **Change to the 3g plan: the repository is renamed (owner, after 3f,
  verbatim; replaces the fresh repository of the decision with the
  "commit" for 3d.2):**

  > Change to the 3g plan: no fresh repository. There were never any pull requests,
  > so I will rename this repository to pokus-infrastructure on GitHub myself. After
  > the rewrite and my "rewrite":
  > - give me the exact commands to force-push the rewritten main and to push the
  >   rewritten working branch as moving/on, then delete claude/zen-albattani-qfwwps
  >   on the remote; I run them myself, you never force-push main;
  > - list what to check afterwards (secrets, branch protection, Actions, the default
  >   branch, that no remote ref still reaches the old commits), and how to update any
  >   other clone I have (re-clone, or fetch and reset --hard to moving/on) so the old
  >   history does not come back through a merge;
  > - update CLAUDE.md, PROGRESS.md and the docs: the repository is renamed, not
  >   recreated, and moving/on is the working branch.
  > Record this change to the 3g plan with the next commit I approve.

  So the rewrite plan stands (the two `Co-Authored-By` trailers removed,
  the privacy scan's two known exceptions removed, every hash updated), but
  the rewritten history goes to this repository, renamed, with the
  branches of the decision with the "commit" for 3d.3 (`main` and
  `moving/on` only); the owner runs every push, the force-push of `main`
  included, and the assistant never force-pushes.
- **Commit identities in the same rewrite (owner, after 3f, verbatim):**

  > - Before the rewrite, list every distinct author and committer identity
  >   (name and email) in the whole history of main and the working branch, with how
  >   many commits each has and their hashes if fewer than ten.
  > - The rewrite sets both author and committer of every commit to my identity (the
  >   one on bef51af), keeping each commit's original author and committer dates,
  >   content and message, except the trailer removal already planned. No commit is
  >   dropped or reordered.
  > - Show me the before/after mapping with the old and new identity per commit, and
  >   after the rewrite run the same identity listing again; it must show only my
  >   identity.
  > - Also confirm no commit message contains any other attribution line
  >   ("Generated with", "Co-Authored-By", or similar).
  > - The rewrite waits for my explicit "rewrite".
- **Order of work after 3f (owner, verbatim):** "Order from here: commits 1
  and 2, then 3f-1, then the real-machine tests on my computers, then the
  history rewrite, 3g and 3h."
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

  `make lint`, `make test` and `make docs` are quiet by default: one line
  per target with the counts of passed, failed and skipped items and of
  warnings, the complete output of every failing step first, step logs in
  `build/gate/<target>/`; `VERBOSE=1` prints
  everything as before (CI uses it).

## Current state

- Working branch `claude/zen-albattani-qfwwps` of this repository; the
  owner merges to `main`. The infrastructure is at the repository root
  since 3f; the state before the move, with the records of 3d, is in the
  [archive](PROGRESS-ARCHIVE.md#moved-from-progressmd-with-3f).
- Committed: infra steps 1-14, I1 and I2, plan items 3a-3f (3d steps 1-8
  with 3d.4b). The commit of each is in the table below and, for the steps
  before section J, in the archive.
- 3e, the self-test without provisioning, is committed (`5ea9b82`), with the
  owner's decisions after it (`bef51af`); the CI runs of both passed every
  job but the known red `lock-check` (38 `TODO(verify)` errors and the
  postgres tag warning).
- 3f moved everything below `infra/` to the repository root with `git mv`,
  so history follows: one `CLAUDE.md`, the workflows in `.github/workflows/`
  without wrappers (the former `repo-root.yml` check is the `privacy` job
  of `ci.yml`), bootstrap's tree hash and dirty check over the whole
  repository, release tags `vX.Y.Z`, the platform spec master removed and
  the infra master renamed (Q13). Its record is in
  `docs/IMPLEMENTATION_NOTES.md`, "Move to the repository root (plan step
  3f)". It is `68967e9`; its CI passed every job but the known red
  `lock-check` (38 marker errors, and 2 warnings: the postgres tag and the
  moved `ubuntu:24.04` tag of `ci_images.ubuntu_2404`), the new `privacy`
  job, all six `check-mode` and all six `check-mode-extended` jobs,
  `release-package` and `lock-collections` included. The scoped commit
  exception ended with the 3f commit: every later commit waits for the
  owner's "commit".
- After 3f (owner decisions): `make lock-pins` pinned the vendor sources of
  provisioning item 0 that this environment reaches (`1a46f64`; GitHub's
  SSH host keys wait for the owner's run), and every test VM recipe meets
  preflight's minimums (`b7be7ff`) (`docs/IMPLEMENTATION_NOTES.md`, "Pins
  of the vendor sources (make lock-pins)" and "Sizes of the test VMs (after
  3f)"). CI of both passed every job but the known red `lock-check` (24
  marker errors, the postgres and `ubuntu:24.04` tag warnings, no error for
  any new pin); the extended check-mode lanes of `ubuntu_2404` and
  `debian_13` passed with the `tailscale` and `incus` roles, which they no
  longer skip; `release-package` and `lock-collections` ran for `1a46f64`
  and passed.
- 3f-1 (owner, after 3f): recorded with the QA specification in `eb02d37`;
  its CI passed every job but the known red `lock-check` (24 marker errors,
  the postgres and `ubuntu:24.04` tag warnings). The owner decided the
  proposal (verbatim under 3f-1 in the remaining plan) and authorized one
  spec edit, limited to D1, D2, A, B, C, E and F: it is `4591db9`. D1, the
  first feature step (the install record and the components that existed
  before pinka), is `8558bee`; its record is in
  `docs/IMPLEMENTATION_NOTES.md`, "3f-1.D1 Pre-existing components and the
  install record". CI of `8558bee`: every job passed but the known red
  `lock-check` (24 marker errors, the postgres and `ubuntu:24.04` tag
  warnings) and both `almalinux_9` check-mode lanes, which failed in the
  install record module: `datetime.UTC` does not exist on the container's
  Python 3.9. The fix (`timezone.utc`, and the module's tests on CPython 3.9
  in `make test-py39`) is `74377ec`; its CI passed every job but the known
  red `lock-check` (24 marker errors, the same two tag warnings), all twelve
  check-mode lanes included. The small spec edit after D1 (the owner's
  decisions 1 and 4, with the owner's new QA specification) is `0378425`.
  Found while building the GPU guard: the installer wrote `null` in two
  forms depending on what the same process had written before; the fix
  (every written file the same bytes, the equivalence test with the replay
  in its own process, and the CI job `test-random-order`) is `32b2c95`,
  recorded in `docs/IMPLEMENTATION_NOTES.md`, "Installer: the same bytes in
  every written file". The GPU support guard (decision 1: a driver only for
  a GPU the pinned release's open kernel module supports, from NVIDIA's
  `supported-gpus.json` pinned in `versions.lock.yml`) is the commit after
  it, recorded in `docs/IMPLEMENTATION_NOTES.md`, "GPU support guard
  (decision 1 after D1)". The three are pushed together; their CI is
  reported job by job before the next step.
- Everything that needs a VM, a real host, Incus, Docker or a GPU waits for
  the owner, as listed under
  [Owner deliverables pending](#owner-deliverables-pending).

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
| 3f | Move infra to the repository root | done | `68967e9` |
| After 3f | `make lock-pins` and the vendor-source pins; test VMs sized to preflight's minimums | done | `1a46f64`, `b7be7ff` |
| 3f-1 | Features before 3g: D uninstall, A computed requirements, B capacity plan screen, C Ubuntu 26.04, E DCGM wiring, F Zabbly version per architecture, G review of the real-machine test specification | spec edit, D1 and its CI fix, the small spec edit after D1, the installer fix of the written files and the GPU guard done; then the pre-existing access follow-up (with the SSH `Match` block), D2, A, B, C, E, F, G | `eb02d37` (records), `4591db9` (spec edit), `8558bee` (D1), `74377ec` (CI fix), `0378425` (spec edit after D1), `32b2c95` (installer fix), the GPU guard after it |
| Real-machine tests | The specification's tests on the owner's computers A and B | after 3f-1 | — |
| 3g | History rewrite (identities and trailers), the repository renamed to `pokus-infrastructure` | not started | — |
| 3h | `TEST_REPORT.md`, `HANDOFF.md` | not started | — |

## Remaining plan

From section J of the owner answers; its completed items are in the
[archive](PROGRESS-ARCHIVE.md#j-plan-after-infra-step-14-this-repository-becomes-pokus-infrastructure),
the records of 3d and the definition of 3f in
[Moved from PROGRESS.md with 3f](PROGRESS-ARCHIVE.md#moved-from-progressmd-with-3f).

This repository holds `pokus-infrastructure` (its fresh repository of that
name follows in 3g): infra was finished here and moved to the repository
root (3f). The platform, the catalog and the Pokus
docs site live in the separate repository `pokus-platform`, which the owner
starts; the assistant does not start them. Each remaining item ends with
its checks green and a commit proposal, and waits for the owner's
"commit".

- **3e** Infra self-test: everything that runs without provisioning, as in
  the master prompt's self-test rules (never weaken tests; "not tested"
  stated honestly). Anything that needs a VM or a real host is described
  exactly (what and where) and runs only after the owner's confirmation.
  Results, the faults it found, what ran in CI instead, and the exact
  commands for each untested part: `docs/IMPLEMENTATION_NOTES.md`,
  "Infra self-test without provisioning".
- **3f-1** Features before 3g (owner, after 3f, verbatim; recorded with
  the first commit approved after it):

  > NEW PLAN ITEM 3f-1 — features before 3g. Record it in PROGRESS.md with the next
  > commit I approve. Order from here: commits 1 and 2, then 3f-1, then the
  > real-machine tests on my computers, then the history rewrite, 3g and 3h.
  >
  > 3f-1 works like 3d: first one proposal covering all features below, with the
  > exact list of spec sections each changes and the effort. I decide, then authorize
  > one spec edit. Then each feature is built as its own step, in the order
  > D, A, B, C, E, F, G, each step with the gate green, docs alongside, PROGRESS.md
  > updated, CI reported job by job after the push, and waiting for my "commit".
  > Do not build anything before my decision.
  >
  > 3f-1.D Uninstallation (first, because my test computers are not disposable).
  > A full uninstall of the lab (for example pinka uninstall). It must:
  > - remove only what pinka itself installed or created. Bootstrap records at install
  >   time what was already present (packages, repositories, users, groups, services,
  >   firewall configuration, Docker, Incus, Tailscale) and what it added; uninstall
  >   removes only the added part and leaves the rest as it found it. Say how existing
  >   hosts without that record are handled.
  > - reverse everything else bootstrap does: stop and remove units, timers, sockets
  >   and containers; Incus projects, profiles, networks, images and the storage pool
  >   it created; firewall rules; vendor repositories, keys and package holds;
  >   accounts and groups; sudoers and other drop-ins; /etc/pinka, /opt/pinka,
  >   /run/pinka, /var/lib/pinka, /usr/local paths.
  > - Tailscale: both my computers already run Tailscale on my own tailnet. If
  >   Tailscale is already installed and logged in, bootstrap must not log it out,
  >   re-authenticate it, change its tags, hostname or settings, or replace its
  >   package; it uses the existing node, or refuses with a clear message if the
  >   existing node cannot be used. Uninstall logs out or removes Tailscale only if
  >   pinka installed or joined it. The same rule for Docker and any other component
  >   that was already present and in use.
  > - treat data separately: /srv/pinka, the database, backups and models are kept
  >   by default. Before deleting anything, offer a backup. Deleting data needs a
  >   separate flag and typing the machine name, like pinka platform purge.
  > - show a dry-run of everything it will remove before it removes anything;
  >   work on partial or failed installs; be idempotent; have its own exit codes.
  > - leave a report of what was removed and what was kept.
  > - be reachable from the installer TUI as well, behind the same typed confirmation.
  > - be testable: test-distros gets a lane that installs, uninstalls and checks the
  >   host against a snapshot taken before install (packages, users and groups,
  >   enabled units, firewall rules, listening ports, files under the pinka paths,
  >   Tailscale and Docker state), failing on any difference not listed as kept on
  >   purpose. Include a variant where Tailscale and Docker were present before the
  >   install.
  >
  > 3f-1.A Requirements computed from the user's choices.
  > The infrastructure must also serve small-scale use, independent of any platform.
  > The lab must not decide the user's scale; it only checks that the user's own
  > choices fit.
  > - For each component (PostgreSQL, the observability stack, OPA, Incus and each
  >   target, vLLM including host RAM while loading a model, a training job, the
  >   installer and bootstrap itself) give your estimate of RAM, CPU and disk, and
  >   where each number comes from.
  > - Memory: an absolute floor for the core stack below which preflight refuses;
  >   everything above it is computed from the enabled features and choices
  >   (gpu_inference, gpu_training, node_role, concurrent targets, and vLLM's host RAM
  >   computed from the selected model's size). Between the floor and the recommended
  >   value preflight reports degraded and asks for consent. Every threshold tolerates
  >   the kernel's reservation, so a machine with the nominal amount passes (16 GiB of
  >   RAM shows about 15.1-15.6 GiB in MemTotal). Fixtures just above and just below
  >   each threshold.
  > - Disk: drop the fixed 150 + 100 GiB. Split it in two:
  >   (a) What is known at install time, preflight computes and checks: the selected
  >       models' sizes (from the model lock), the container images of the enabled
  >       features, the configured Incus pool size, the golden images to build, and a
  >       small fixed floor for pinka itself. If it does not fit, refuse and show the
  >       shortfall item by item (for example "model X: 10 GiB, missing 8 GiB").
  >   (b) What grows with use (telemetry, artifacts, datasets, adapters, backups) is
  >       not a reason to refuse. Pool size and every retention setting are
  >       configurable; the installer shows the expected growth for the chosen
  >       settings; doctor monitors free space and warns before it runs out.
  > - These values are provisional until the first real installs; the real-machine
  >   tests measure the real footprint at idle and during a run, so the final values
  >   come from measurement.
  > - Test VM sizes and the drift test then follow the computed minimum for the
  >   configuration each target installs.
  >
  > 3f-1.B Capacity plan screen in the installer.
  > Merge the resource, storage and GPU/model choices into one capacity plan screen:
  > - the detected machine: RAM, CPU, free disk per mount point, GPU with VRAM,
  >   compute capability and driver state;
  > - the choices: features, node role, model (only models that fit the GPU, each
  >   with its size), Incus pool size, concurrent targets, retention settings;
  > - live budget bars for RAM, disk and VRAM, each expandable to its items; green
  >   fits, yellow degraded (needs consent), red does not fit (shows the shortfall
  >   and blocks going on);
  > - the expected disk growth per day and week for the chosen retention, and roughly
  >   when the disk fills at that rate;
  > - every number marked as measured or estimated.
  > The decision logic stays in preflight; the screen only shows it. A
  > non-interactive install with an answers file runs the same computation and writes
  > the same breakdown to the log. Snapshot tests for fits, degraded and does-not-fit.
  >
  > 3f-1.C Ubuntu 26.04 LTS as a supported distribution.
  > It is the current Ubuntu LTS. Is there any reason it cannot be supported? If not,
  > add it as supported next to 24.04: Docker, Zabbly Incus, Tailscale, NVIDIA driver
  > and Container Toolkit packages verified and pinned through make lock-pins, a CI
  > check-mode image, a test-distros target, preflight fixtures. It is claimed as
  > supported only after its test-distros run passes; until then mark it as pending
  > verification.
  >
  > 3f-1.E DCGM wiring. A setting that silently does something else is not
  > acceptable: when dcgm is chosen, compose runs the DCGM exporter at the pinned
  > digest instead of the nvidia-smi exporter, with a test. Its driver and CUDA
  > compatibility is checked on the GPU host; record that as an open check.
  >
  > 3f-1.F Zabbly version per architecture. Approved as a spec deviation; fold it
  > into the same spec edit.
  >
  > 3f-1.G Real-machine test specification — review only, last step of 3f-1.
  > I attach specs/pokus-infrastructure-qa-spec.md, written outside this session. It
  > instructs a test agent that runs on my own test computers and does, one test at a
  > time, the tests you cannot do because they need real machines, VMs, KVM, Incus,
  > Docker or a GPU. There is no separate QA repository; the agent only reads a
  > checkout and writes reports, which I pass to you.
  > Add the file verbatim to specs/ in the commit that records 3f-1, record its sha256
  > in PROGRESS.md, and treat it as read-only like the other spec masters: never edit
  > it, and it is not part of your gate. As the last step of 3f-1, review it against
  > the spec masters, the approved deviations and what was built, without changing
  > it, and give me a list of:
  > - names, paths, commands, flags or behaviors it states that differ from the specs
  >   or from what was built;
  > - real-machine checks the specs or the 3f-1 features need that it does not cover;
  > - anything it asks for that the specs do not define, or that could be done
  >   without a real machine and therefore belongs to CI instead.
  > I decide each point and edit the file myself. From then on, give me the
  > real-machine tests as the file numbers them (4.1, 4.2, ...), so the agent and I
  > use the same numbers.

  Other decisions given with it (owner, verbatim): "Tailscale rpm: stays
  unverified for now; it must be closed before any rpm-family target is
  claimed as tested." "Base image pins: approved, including the lock-check
  warning."

  Decisions on the 3f-1 proposal (owner, with the "commit" for `eb02d37`,
  verbatim):

  > 1. D in two steps (D1, D2): approved.
  > 2. Adopting an existing Tailscale node: approved, including the refusal when
  >    Funnel or Tailscale SSH is on. Additions:
  >    - an adopted node may not carry tag:pinka-lab and its key may expire with a
  >      personal account: doctor reports that as a warning, not a failure, and the
  >      docs explain the trade-off;
  >    - on an adopted node, tailscale serve for Grafana or docs is off by default and
  >      needs explicit consent; uninstall removes only the serve entries pinka added.
  > 3. Existing Docker: approved: pinka never writes daemon.json and never restarts
  >    a Docker it did not install. Verify whether the pinned Docker version enables
  >    CDI by default. If it does, keep the rule the same everywhere. If it does not,
  >    pinka may configure CDI only on a Docker it installed itself (per the install
  >    record); on a pre-existing Docker it refuses with the exact change, as
  >    proposed. The DOCKER-USER rules pinka adds for Incus bridges must not affect
  >    existing containers' networking (real-machine test 4.4) and are removed by
  >    uninstall.
  > 4. Hosts without an install record: approved.
  > 5. Exit codes 80 and 81: approved.
  > 6. Memory floor 8 GiB nominal and the 0.94 tolerance: approved.
  > 7. Image sizes as compressed size x 2.5: approved as an estimate; once an image
  >    is pulled on a host, preflight and doctor use its real size there.
  > 8. Incus on Ubuntu 26.04: option (a), Ubuntu's incus from universe. State in the
  >    support matrix that universe has no guaranteed security updates; make
  >    lock-pins report when Zabbly's lts-6.0 publishes a 26.04 suite, so the choice
  >    is revisited.
  > 9. "Pending verification" cleared only by a passing test-distros result: approved.
  > 10. DCGM without profiling metrics and without added capabilities: approved.
  >
  > Addition to D1: LXD and Podman are pre-existing components too. Detect them;
  > never change, stop or reconfigure them; check that Incus next to LXD does not
  > clash (bridge names, dnsmasq, firewall, storage); if it would, refuse with a
  > clear message instead of installing.
  >
  > Spec edit: I authorize one spec edit now, limited to 3f-1 D1, D2, A, B, C, E and F
  > with these decisions. Edit the spec master and docs/BUILD_PROMPT.md together, keep
  > them byte-identical, record the checksums, and show me a section-by-section
  > summary of the diff with the commit proposal. Specs are read-only for you again
  > after that commit. Then build D1 and wait.

  Checked for these decisions (2026-10-18, details in
  `docs/IMPLEMENTATION_NOTES.md`, "3f-1 spec edit"):
  - Decision 3: the pinned Docker Engine 29.8.2 enables CDI by default. In
    the moby source at tag `docker-v29.8.2` (`daemon/command/daemon.go`,
    sha256 `39f0b29b…d712b`), `cdiEnabled` returns true on Linux unless
    `features.cdi` is set, and false only when it is set to `false`; the
    default changed in 28.2.0 (28.1.1 registered CDI only with
    `features.cdi: true`). `docker info` reports no CDI spec directories
    when CDI is off. So the rule is the same everywhere: pinka writes no
    CDI setting on a Docker of 28.2 or later, and a pre-existing Docker
    with CDI off is refused with the exact change when a GPU feature is
    on. The one exception follows the decision's other branch: a GPU
    host whose Docker pinka installs from a distribution package older
    than 28.2 (possible only on openSUSE, whose Docker pin is still
    unverified) gets `features.cdi: true` in pinka's own `daemon.json`.
  - Item C: Ubuntu 26.04 (`resolute`) ships `python3` 3.14.3-0ubuntu2 in
    main (the signed `InRelease` checked with the Ubuntu archive key, the
    index's sha256 matching it).

  Decisions with the "commit" for the spec edit and D1 (`4591db9`,
  `8558bee`) (owner, verbatim):

  > commit both: commit 1 (spec edit) first, then commit 2 (D1), then push and report
  > CI job by job, including whether install_record passes in every check-mode lane.
  >
  > Decisions:
  > 1. GPU driver: skip on computer B is right, but fix the general case in D2's step
  >    or as its own small commit: with install_nvidia_driver: auto, bootstrap never
  >    installs a driver for a GPU below the minimum compute capability or one the
  >    pinned driver branch does not support; preflight marks it as no usable GPU and
  >    the install continues without GPU features. Fixtures for Kepler and Pascal
  >    cards. A user must not need to set skip by hand.
  > 2. LXD guests: DHCP and DNS only on LXD bridges, approved. Confirm that pinka's
  >    rules leave LXD guests' and Docker containers' forwarded traffic (their own
  >    outbound access) as it was, and make real-machine test 4.4 check it.
  > 3. SSH on hosts with other accounts: option (b), a Match Group pinka block,
  >    approved.
  > 4. Pre-existing listeners: on a host with existing accounts or services, pinka's
  >    default-drop input policy must not cut off access that existed before the
  >    install (for example SSH from the LAN, or another service already listening on
  >    a non-loopback address). Preflight records the non-loopback listeners and how
  >    they were reachable; pinka keeps them reachable as found, or asks for explicit
  >    consent to close each one; the install record lists them and uninstall restores
  >    the firewall as it was. On a fresh dedicated host the strict default stays.
  >    Propose where this fits (D1 follow-up or D2) and the spec sections it touches;
  >    it needs a small spec edit, which I authorize together with point 1.
  >
  > Then continue with D2.

  Decisions with the "commit" for `74377ec` (owner, verbatim; the
  attachment is the new `specs/pokus-infrastructure-qa-spec.md`):

  > commit
  >
  > Decisions on points 1 and 4, for the small spec edit I authorized:
  >
  > Point 1, GPU guard: approved in principle, but a Ubuntu-only list would stop
  > automatic driver installs on every other supported distribution, Debian 13
  > included, even for supported cards. Check first whether the pinned NVIDIA driver
  > release ships a machine-readable list of supported GPUs with PCI IDs and open
  > kernel module support (for example supported-gpus.json in the driver package or
  > its documentation), usable for every distribution. If it does, pin that list
  > through make lock-pins and use it everywhere, with the Ubuntu index as a
  > cross-check. If it does not, use the Ubuntu list as proposed and tell me which
  > families lose automatic installs. In every case, a GPU below the minimum compute
  > capability never gets a driver installed automatically. Fixtures: Kepler, Pascal,
  > a Turing control, and one per family.
  >
  > Point 4, existing listeners: a D1 follow-up built before D2, as proposed, with the
  > spec sections you listed. Your rule for a dedicated host (no non-loopback
  > listener besides sshd and no login account that is not a researcher) is
  > approved, with one safeguard that applies to every host, dedicated or not: the
  > access path used for the install (for example SSH from the LAN) is never closed
  > until SSH over the tailnet has been verified to work for at least one researcher
  > in that same run, and closing it needs explicit consent on the Remote access
  > screen or an answers-file key. Without both, it stays open and doctor reports it
  > as a warning. Uninstall restores the firewall as it was.
  >
  > Point 2: thanks, and I added your suggested probe to real-machine test 4.4.
  >
  > QA spec update: I attach a new version of specs/pokus-infrastructure-qa-spec.md
  > (sha256 79bcb4fc75343b736a1f5e728cbee12d12574a2d2524530a4efff79bfaf936cc). It
  > changes only tests 4.1 and 4.4. Replace the file verbatim in the same commit as
  > the small spec edit, record the new sha256 in PROGRESS.md, and keep it read-only.
  >
  > Then the small spec edit, the GPU guard, the listeners follow-up and D2, each
  > waiting for my "commit", with CI reported job by job after each push.

  Checked for point 1 (2026-10-18, details in `docs/IMPLEMENTATION_NOTES.md`,
  "Spec edit after D1"): the pinned release 580.178.04 ships NVIDIA's
  `supported-gpus.json` (in Ubuntu's `nvidia-kernel-common-580`, from the
  signed index), with a `legacybranch` per GPU the release no longer
  supports and the `kernelopen` feature per GPU its open kernel module
  supports. Its 449 current device IDs equal the `Modaliases` of Ubuntu's
  `nvidia-driver-580`, its 289 `kernelopen` IDs equal those of the pinned
  `nvidia-driver-580-open` and the table of NVIDIA's open-gpu-kernel-modules
  README at tag 580.178.04, which states that the open modules "can be used
  on any Turing or later GPU". The GT 730 (`10de:1287`) carries
  `legacybranch: 470.xx`; the GT 1030 (`10de:1d01`) is current but without
  `kernelopen`. No family loses automatic installs through the list; they
  are limited only by their own driver pins, still unverified outside Ubuntu.

  Decisions with the "commit" for the spec edit after D1 (`0378425`) (owner,
  verbatim):

  > commit, in three commits instead of two: proposal 1 (spec edit) first, then the
  > group-vars writer fix on its own, then the GPU guard. Push and report CI job by
  > job.
  >
  > The group-vars writer fix is not unrelated: if one write changes how null is
  > written for every later YAML write in the same process, a real TUI install may
  > write later config files differently from an answers-file replay. Before the
  > commit, tell me:
  > - which files a real install writes after the group-vars writer runs, and whether
  >   any of them were written wrongly before the fix;
  > - whether the answers-equivalence test would have caught it, and if not, why not.
  > Its commit message says what it fixes for a real install, not only for the test.
  >
  > Add, in the same fix commit or as a small follow-up: the Python test suite also
  > runs in random order with a fixed, logged seed (one CI job), so order-dependent
  > tests show up in CI instead of by chance. If random order exposes more failures,
  > list them before fixing.
  >
  > Record as an open item: the driver package pins outside Ubuntu are still
  > unverified; they must be verified before a GPU install on those families is
  > claimed to work.
  >
  > Then the pre-existing access follow-up and D2, each waiting for my "commit".

  Answered before the commits (details in `docs/IMPLEMENTATION_NOTES.md`,
  "Installer: the same bytes in every written file"): after the group_vars
  write of a plan come the users' key files, the vault and the answers file,
  and in the same process every later plan (the TUI plans again after an
  "Edit" on the review screen and in the pairing step). No value was wrong,
  but host_vars of a machine without a default model read
  `vllm_default_deployment:` from a first plan and an answers replay, and
  `vllm_default_deployment: null` from a later plan, so the same
  configuration gave different `host_vars_sha256` and fingerprints, and a
  modify run that changed nothing rewrote host_vars. The equivalence test ran
  the replay in its own process and passed the review screen once: in the
  suite both sides inherited the change and matched. Random order exposed
  no further failure (the whole suite with seeds 20261019, 1, 2 and 3); on
  the old code it did not expose this one either in six seeds, and in about
  6% of 10,000 simulated seeds, while each test file in its own process
  exposes it every time.
- **Real-machine tests** (owner, after 3f, verbatim; after 3f-1 and before
  the rewrite):

  > Real-machine tests, after 3f-1 and before the rewrite, on two Linux computers
  > (not WSL2). Neither is disposable.
  >
  > Computer A: [a VM host: x86_64 bare metal with systemd and KVM, enough
  > memory for a 16 GiB VM, a GPU below compute capability 7.5 without an NVIDIA
  > driver, no Secure Boot; Docker, LXD and Tailscale already installed and in
  > use.]
  > Every test on A runs inside a VM; nothing on the host may change except what a
  > test VM needs. LXD is already there; use LXD VMs unless you have a reason to
  > prefer another tool, and say how to create and delete each VM.
  >
  > Computer B: [bare metal: x86_64 with systemd and KVM, a nominal 16 GiB
  > machine, a Kepler GPU without an NVIDIA driver, no Secure Boot; Tailscale
  > already installed and in use.]
  > It cannot host a 16 GiB VM, so on B tests run directly on the host. Nothing is
  > installed on B until the uninstall tests (including the variant with Tailscale
  > already present) have passed in VMs on A. Before the first install on B, give me
  > the steps to take a full backup of B and to restore it. Then B is for: a real
  > bare-metal install of the smallest configuration on a supported distribution,
  > the memory tolerance on a real 16 GiB machine, a GPU whose driver current NVIDIA
  > releases no longer support (it must be treated as no usable GPU, without breaking
  > the install), the disk check with a small model and pool, and install plus
  > uninstall with the host snapshot comparison.
  >
  > When 3f-1 is done, give me the real-machine tests one at a time, starting with
  > 4.1 (read-only preflight on both computers) and 4.2 (bootstrap in check mode in
  > an Ubuntu 24.04 LXD VM on A): the exact steps from a clean start to cleanup, the
  > VM size, and what to send back. With the first test, also tell me:
  > - which tests run on A, which on B, and which need different hardware (GPU
  >   inference and training need compute capability >= 7.5; Secure Boot enrollment
  >   needs Secure Boot);
  > - how to run make test-distros (4.14) on A without breaking LXD or Docker on the
  >   host: Incus next to LXD, Incus inside a large VM with nested KVM, or something
  >   else, with the risks of each;
  > - whether preflight with --no-write writes or installs anything.
  >
  > I will run make lock-pins on my machine and paste the output separately.
- **3g** History rewrite and the renamed repository (owner, after 3f,
  replacing the fresh repository of the decision with the "commit" for
  3d.2; see "Process rules in force"): after the real-machine tests, the
  assistant lists every author and committer identity of `main` and the
  working branch, then proposes the one-time rewrite of the full history
  that removes the two `Co-Authored-By` trailers, sets author and committer
  of every commit to the owner's identity (the one on `bef51af`) with the
  original dates, content and messages, drops and reorders nothing, and
  removes the privacy scan's two known exceptions; with the before/after
  hash and identity mapping and every hash in `PROGRESS.md` and the docs
  updated. It waits for the owner's "rewrite". The owner renames this
  repository to `pokus-infrastructure` on GitHub. After "rewrite" the
  assistant gives the exact commands to force-push the rewritten `main`,
  push the rewritten working branch as `moving/on` and delete
  `claude/zen-albattani-qfwwps` on the remote; the owner runs them, and the
  assistant never force-pushes. Then: the list of what to check afterwards
  (secrets including `PINKA_PRIVACY_DENYLIST`, branch protection, Actions,
  the default branch, no remote ref still reaching the old commits), how to
  update any other clone (re-clone, or fetch and `reset --hard` to
  `moving/on`), and `CLAUDE.md`, `PROGRESS.md` and the docs updated: the
  repository is renamed, not recreated, and `moving/on` is the working
  branch, to which every rule that names `claude/zen-albattani-qfwwps`
  applies (owner decision with the "commit" for 3d.3). The owner merges
  `moving/on` into `main` themselves; the assistant never opens a pull
  request and never merges anything (owner, with the "commit" for 3d.8). A
  small commit proposal updates every reference to the repository's name
  and URL (README, docs, the `CITATION.cff` placeholder, examples).
- **3h** Final report: `TEST_REPORT.md` and `HANDOFF.md` for infra as the
  master prompt describes, their commit proposal, then stop. `HANDOFF.md`
  gives the exact commands for pushing the rewritten history to the renamed
  repository as `moving/on` and for merging `moving/on` into `main`, and
  what to check after the merge: CI on `main`, and the weekly jobs, which
  run only from `main` (owner, with the "commit" for 3d.8). After 3h the
  assistant waits for the owner's explicit instruction.

## Approved deviations

Resolutions that differ from the spec text (the owner answers they come from
are in the archive). Contract-level resolutions are also recorded with citations in `docs/contracts.md#resolutions`.

| ID | Spec section | Spec text (short) | Approved resolution |
| --- | --- | --- | --- |
| A2 | master prompt §4 | the owner makes every commit | assistant runs the approved commit commands on "commit" (cloud only) |
| A4 | platform §17 | Postgres via testcontainers or CI service | micromamba + conda-forge PostgreSQL locally; CI service container |
| B1 | docs spec §A.4 | every Markdown page has front matter | spec copies and IMPLEMENTATION_NOTES are excluded from builds, kept verbatim |
| B2 | infra §7.14 | clone pokus-platform / pokus-catalog at a ref | bare mirror + `git archive` of one subtree + `DEPLOYED_FROM.json` |
| B3 | infra §5.2 step 1 | refuse a dirty tree | dirty check limited to `infra/`; commit + `infra/` tree hash recorded. **Ended with 3f:** bootstrap records the commit and the tree hash of the whole repository, and its dirty check covers the whole repository, as §5.2 step 1 states |
| B4 | docs spec §B.1, platform §4.1.2 | RC triple of three repositories | three commits of one repository + tree hashes; prefixed tags. **Ended with 3f:** infra, platform and catalog live in their own repositories; this repository's release tags are `vX.Y.Z` (package `pokus-infrastructure-X.Y.Z.tar.gz`) |
| B5 | infra §4, §11 | `.github/workflows/ci.yml` per repository | canonical per component + thin root wrappers; restricted job set. **Ended with 3f:** the wrappers are gone; `.github/workflows/ci.yml` (with the `privacy` job of the former `repo-root.yml`), `lock-collections.yml`, `release-package.yml` and `release.yml` run at the repository root |
| B6 | master prompt §3 | components splittable without changes | catalog lint uses a permanent `../platform` path dependency. **Ended with 3f:** no platform or catalog code is in this repository; they live in `pokus-platform` |
| C1 | platform §18 | layout without `docs/contracts.md` | `platform/docs/contracts.md` added, CI identity check. **Ended with 3f:** replaced by the copy rule (owner, with the "commit" for 3d.8): a platform repository that keeps a copy of `docs/contracts.md` keeps it identical to the infra release it pins, and its own CI checks it |
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
| P3b | infra §2.2, §7.5, §7.9, §7.11, §8.1, §12.3, §12.6, §12.11, §13.3, §19.10 | `127.0.0.1:<port>` unspecified | the port table with its defaults (also in `contracts.md`); ports overridable in host_vars (`ports:`), effective values in `endpoints.toml`; preflight checks the configured ports; `vm_collector_address` configurable with its conflict check; a non-loopback block (the inference node's 18704 and 18710, the lab node's tailnet 4317, the VM collector address). Approved in the owner's review of the 3b spec update; implemented in 3d.4 (preflight reads listeners from `/proc/net/tcp*`, details in `docs/IMPLEMENTATION_NOTES.md`) |
| P3c | infra §7.4, §7.19, §8.1, §9, §12.11; docs spec §B.4 | `pinka docs serve` (docs spec only) | the docs service `pinka-docs.service` on `127.0.0.1:18706`, `docs_release` in host_vars, `pinka docs install` and `docs rollback` (root only), three versions kept, `docs_tailscale_serve`. Approved in the owner's review of the 3b spec update; implemented in 3d.4 (the service runs `pinka docs serve`) |
| V1 | infra §12.1 ("Sources in one repository"), §7.14 step 4 | a content package may not overlap the release path in the same repository | content packages from one repository may not overlap each other either (`source-overlap`). Approved with the "commit" for 3d.1 as stricter validation; implemented in 3d.2; for the next spec edit |
| V2 | infra §12.1 (`requires`) | the health gate checks exactly the declared facilities | a manifest that uses a facility it does not declare is refused (`facility-undeclared`): profiles `sandbox-operator` and `restricted-operator` need `incus`, `inference-broker` needs `inference`, `gpu-trainer` jobs need `gpu`; the fields `policy` need `opa`, `dashboards` and `telemetry` need `telemetry`, `backup.database: true` needs `postgres` (`contract/profiles.yml`, `facilities`). Approved with the "commit" for 3d.1 as stricter validation; implemented in 3d.2; for the next spec edit |
| V3 | infra §7.14 step 5, §12.1 (`runtime: python-uv`); platform spec (release layout) | `uv sync --frozen` builds the release | two build phases: locked wheels with network, nothing built or compiled; then the release built offline with the backend from a `build` dependency group. A release with a `[build-system]` locks its backend in that group, and every dependency is a wheel. Approved with the "commit" for 3d.3; to be carried into the platform spec too |
| V4 | infra §7.14 (hardening baseline), §6.2 | `RuntimeDirectory=pinka/platform/<service>` | no `RuntimeDirectory=` on the socket directories: socket units create `/run/pinka/platform/<service>/` (root, 0755). Approved with the "commit" for 3d.3 on condition that a service that needs its own runtime directory gets one at a separate path that contains no sockets; done in 3d.4 (`/run/pinka/platform-runtime/<service>`) |
| V5 | infra §11 (neutrality lint) | the lint excludes test fixtures, docs and the reserved list | the installer and its answers files are excluded too, as an explicit named exclusion in the lint configuration with a comment pointing to 3d.7. Approved with the "commit" for 3d.3; removed in 3d.7 (the installer and `tests/answers/` are scanned, a test keeps it so) |
| V6 | infra §7.14 (agent allowlist), §7.14 step 5, §12.1 (`untrusted-agent`); platform spec (release layout) | the agent's allowlist binds the platform's code it needs | an `untrusted-agent` service names its own package in the manifest (`package`, its directory); the build gives it a separate environment with only that package and its locked dependencies; the agent binds only that package and that environment, never the rest of the release; a manifest whose agent package depends, directly or transitively, on another package of the release is refused. Approved with the "commit" for 3d.4; implemented in 3d.4b (`package`, `own_package`, `.agent-venvs/<service>/`; details in `docs/IMPLEMENTATION_NOTES.md`, "Contract v2, step 4b") |
| V7 | infra §2.2, §2.3 (`gpu_inference`) | NVIDIA GPU with compute capability ≥ 7.0 | compute capability ≥ 7.5, matching the vLLM image's kernels (`TORCH_CUDA_ARCH_LIST` from 7.5); preflight refuses GPU inference below it. Approved with the "commit" for 3d.4; implemented in 3d.4b |
| P3d | infra §7.20, §8.1, §9, §13.10 | the QA maintenance window (docs spec how-to only) | the record `/etc/pinka/maintenance-window.json`, `pinka window open` and `window close`, fail-safe expiry (staging stops and refuses; production stays stopped until `window close`), `staging_shared_hardware` with the GPU-idle and port checks. Approved in the owner's review of the 3b spec update; implemented in 3d.4 |
| St12-1 | infra §11 (Ansible) | `make test-roles`: the provider-agnostic roles in a throwaway Incus system container or VM, only when invoked | `make check-mode` (the provider-agnostic roles `base`, `users_groups`, `ssh` and `layout` in check mode in a disposable Docker systemd container per family, also CI's required job, I2) and `make test-distros` (the full bootstrap in disposable Incus VMs, §19.9) replace it; there is no `test-roles` target and no alias. Raised by the 3e self-test; approved by the owner after 3e ("record it as an approved deviation (make check-mode and make test-distros replace it). No alias."); built in step 12 |
| SSH1 | infra §7.3 | the drop-in sets `AllowGroups pinka`, `PasswordAuthentication no` and the other settings for the whole host | pinka's sshd settings apply in a `Match Group pinka` block, so accounts outside `pinka` keep the host's own policy (owner, decision 3 after D1, approving option (b) of the open question raised with the 3f-1 spec edit); built with the D1 follow-up |

## Platform spec changes pending

Approved deviations after the 3b2 spec edit that the platform must follow;
the owner folds them into the platform spec
(its master, which the owner keeps for `pokus-platform`). Each names what changes for
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
follow-ups after step 13 that `docs/IMPLEMENTATION_NOTES.md` lists as
"awaiting owner confirmation" (archive, section J, "Interpretations"); the notes keep
the original wording and the findings of each step. Still open, from step 1
("Questions raised by step 1"): reference agents for scenario validation and
the QA results schema (both for `pokus-platform` and `pokus-qa`), and the
page-ownership table (H5). Settled since: the port table (P3b), the
project files at the repository root (3f) and `make test-roles` (St12-1,
after 3e).

Raised by the pins of `make lock-pins` (after 3f), for the owner; decided
with the plan of 3f-1 (owner, after 3f): Zabbly's per-architecture version
is approved as a spec deviation (3f-1.F), DCGM is wired in 3f-1.E, and
Tailscale on the rpm releases stays unverified until it is closed before
any rpm-family target is claimed as tested. The records as they were
raised:

- **Zabbly's per-architecture versions.** Zabbly builds Incus for each
  architecture separately, and the version names the build
  (`1:6.0.6-ubuntu24.04-202603272003` on x86_64, `…-202603272249` on
  aarch64). The lock holds one version per release (infra §19.4), so the
  pins are the x86_64 builds and an aarch64 host (experimental) cannot
  install them. Recommendation: allow a per-architecture mapping for such a
  package (`incus: {x86_64: ..., aarch64: ...}`) in the lock, its schema and
  `pinka_lock_packages`, as a spec deviation; the alternative is to leave
  aarch64 hosts without Incus from Zabbly.
- **DCGM is not wired.** `observability_gpu_exporter: dcgm` passes the
  role's check once the image is pinned, but `compose/observability` always
  runs the nvidia-smi exporter (`GPU_EXPORTER_IMAGE`, with that exporter's
  flags); nothing uses `DCGM_EXPORTER_IMAGE`. Recommendation: wire the DCGM
  exporter as its own compose service with its own flags and capabilities,
  with a render test, or refuse `dcgm` until it is; either way its image's
  CUDA requirement must be checked against driver 580 on a GPU host (its
  configuration is behind `layers.nvcr.io`, which this environment cannot
  reach).
- **Tailscale on the rpm releases.** Tailscale's repositories use one layout
  per distribution (`stable/fedora/$basearch` with no release, also for
  AlmaLinux and Rocky Linux per its installer; `stable/centos/<major>/`,
  `stable/rhel/<major>/`; `stable/opensuse/leap/<version>/`), which the one
  template of `tailscale-rpm` cannot express. Recommendation: one
  repository entry per source group, with a `{version}` placeholder for
  openSUSE Leap, then their pins with `make lock-pins`.

Raised by the 3f-1 spec edit, for the owner (decided after D1: option (b), approved deviation SSH1):

- **SSH policy on a host with other accounts.** The `ssh` role's drop-in
  (§7.3) sets `AllowGroups pinka` and `PasswordAuthentication no` for the
  whole host, so every account outside `pinka` loses SSH access and
  password logins end, also for accounts that existed before pinka
  (computer B). Under the new pre-existing rule this changes how existing
  accounts are used, but §7.3 was outside the authorized edit, so it is
  unchanged. Options: (a) keep it and say so in the pre-existing
  components how-to and in the dry run; (b) put pinka's settings in a
  `Match Group pinka` block, so other accounts keep the host's own policy;
  (c) refuse with `needs_action` when SSH-capable accounts outside `pinka`
  exist, unless the owner consents. Recommendation: (b), with sshd still
  reachable only on `tailscale0` and the allowed interfaces.

Raised by D1 of 3f-1, for the owner (decided after D1: the GPU guard in its own commit; LXD guests: DHCP and DNS only, approved):

- **Computer B's Kepler GPU.** With `install_nvidia_driver: auto` and no usable
  driver, bootstrap blacklists nouveau and installs the pinned open driver
  branch, which does not support Kepler GPUs such as the GT 730. D1 leaves a
  driver that existed before pinka alone, but a host without one still gets
  the pinned branch. Options: (a) `install_nvidia_driver: skip` on B (the
  GPU features stay off; the installer offers it); (b) a supported-device
  guard: preflight compares the GPU's PCI ID with the pinned branch's
  supported list and reports the GPU unusable instead of installing.
  Recommendation: (a) now for the real-machine tests; (b) as a later item
  if hosts with old GPUs are expected.
- **LXD's guests and pinka's input policy** (§7.7: "pinka's rules must not
  change LXD's traffic"). On the nftables backend pinka's input chain drops
  by default, which would also drop the DHCP and DNS requests of LXD's
  guests to LXD's dnsmasq. D1 admits DHCP and DNS on LXD's bridges (as for
  Incus's), and pinka's forward chain filters only its run networks, so
  LXD's guests keep their addresses, names and outbound traffic. Their other
  traffic to the host itself (a service of the host on the bridge address)
  meets pinka's input policy like any interface. Accepting all of it would
  also let LXD's guests reach the split-inference ports, whose guard filters
  only `tailscale0`. Interpretation built: (a) DHCP and DNS only; the
  alternative (b) accepts all traffic from LXD's bridges and extends the
  split guard to every interface. Recommendation: (a).

## Owner deliverables pending

- **`make lock-pins` on the owner's machine** (owner decision on item 0,
  after 3f): this environment reaches every source but GitHub's meta API
  and SSH port, so GitHub's SSH host keys need the owner's run on a fresh
  Ubuntu 24.04 machine (prerequisites and commands in
  `docs/how-to/pin-vendor-sources.md`); the output, pasted back, is
  recorded and its commit proposed. The run also cross-checks every pin
  recorded from here. The Incus base image pins age out within days: renew
  them with `make lock-pins` right before the first golden image build.
- **The real-machine tests** (owner, after 3f): after 3f-1, on the owner's
  computers A and B, one at a time, named by the numbers of
  `specs/pokus-infrastructure-qa-spec.md` and starting with 4.1 and 4.2;
  the plan is under "Real-machine tests" in the remaining plan. They
  replace the checklist of the 3e runs, which the owner asked for after 3e
  (owner instruction with the "commit" for `a425c7f`: stop and say exactly
  what to run and where), listed in `docs/IMPLEMENTATION_NOTES.md`, "Infra
  self-test without provisioning", "Not tested":
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
- Approval of the model candidates in `models.catalog.yml` (owner answer
  E7): four permissive candidates and one restricted one, all `proposed`, with
  `TODO(verify)` revisions because Hugging Face is not reachable from the build
  environment.

- Domain allowlist for the environment (A3); the list is in
  `docs/IMPLEMENTATION_NOTES.md`. Since step 3, GitHub release
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
marker, 118 with the owner's collection pins, 113 after 3e, 96 with the
pins of `make lock-pins` after 3f, 98 with D1 of 3f-1; details in
`docs/IMPLEMENTATION_NOTES.md`, "Open `TODO(verify)` items after step 4"
through "after step 13", and "Pins of the vendor sources (make lock-pins)"):

- Ubuntu 26.04: Incus, NVIDIA Container Toolkit and Tailscale support
  (`preflight/support-matrix.yml`).
- openSUSE Leap 15.6 end of support (`preflight/support-matrix.yml`).
- 16 endpoint URLs whose hosts are blocked from the build environment
  (`preflight/endpoints.yml`), including `releases.astral.sh`, from
  which uv 0.12.23 downloads the pinned CPython.
- Output formats of `timedatectl timesync-status` and `chronyc -n tracking`
  (`preflight/pinka_preflight/detect/timesync.py`).
- Detection of an incomplete dnf transaction
  (`preflight/pinka_preflight/detect/conflicts.py`).
- 32 lines of `versions.lock.yml`: the DCGM exporter image (nvcr.io);
  Tailscale and Zabbly repository keys; Docker packages for openSUSE Leap;
  Incus and Tailscale packages for every pinned release; Incus base image
  fingerprints; GitHub SSH host keys (api.github.com/meta is not readable
  from this environment). After 3f, `make lock-pins` pinned the DCGM image,
  the Tailscale and Zabbly keys, Incus (Zabbly) and Tailscale on the apt
  releases and the Incus base images; still open: GitHub's SSH host keys
  (the owner's run of `make lock-pins`), Docker on openSUSE Leap, Incus on
  the Fedora, EL and openSUSE releases, and Tailscale on the rpm releases.
- Step 5: 7 more lock lines (NVIDIA driver pins for the releases other than
  Ubuntu); 17 `verify` entries of `ansible/component-sources.yml`
  (Tailscale and Zabbly repository layouts; Docker, Incus and NVIDIA package
  names and methods outside Ubuntu); 20 notes in role files (package and
  service names of the Red Hat, SUSE and Arch families, dnf5 and zypper
  behavior, Incus's firewalld/ufw/Docker guidance, the WSL library path, the
  `tailscale serve` status format, the Secure Boot MOK flow); the Windows
  helper `contrib/windows/pinka-wsl.ps1` (not run on Windows).
- Step 6: resolved the three firewall and Docker coexistence notes from
  Incus's documentation; added the Incus group, unit and path names of
  Zabbly's packages and of the Red Hat, SUSE and Arch families
  (`ansible/roles/incus/vars/`).
- Step 7: the revisions of the five candidate models in
  `models.catalog.yml` (with two explanatory lines) and the field names
  of the Hugging Face tree API in `docs/how-to/add-a-model.md` (with the
  catalog warning); Hugging Face is not reachable from the build environment.
- Step 8: nothing added or resolved; the Incus base image fingerprints now
  block every golden image build (images.linuxcontainers.org is not reachable
  from the build environment).
- Step 9: nothing added or resolved.
- Step 10: nothing added or resolved.
- Step 11: nothing added or resolved; vLLM's TLS options, planned as a
  marker, were verified in vLLM's source (and are not used). GitHub's SSH
  host keys (above) now also gate SSH deployments from github.com.
- Step 12: the VM image aliases of `tests/distros/targets.yml`
  (images.linuxcontainers.org is not reachable from the build environment).
- Step 13: the Hugging Face Hub API field names the installer reads when it
  pins a model (`installer/pinka_installer/models.py`), and the MOK
  enrollment with `mokutil` (`installer/pinka_installer/tui/screens_install.py`).
- 3d.6: the release tarball URL and sha256 of each pinned Ansible
  collection (`ansible_collections.fallback` in `versions.lock.yml`).
- 3d.7: the Galaxy artifact URL and sha256 of each of the five pinned
  collections (`ansible_collections.artifacts` in `versions.lock.yml`)
  and their copy in the generated `bootstrap/lib/pins.sh`. Resolved
  with the owner's `make lock-collections` output (`4aeb35b`); the four
  `ansible_collections.fallback` entries go with the fallback in 3d.8.
- 3f-1.D1: the JSON of `tailscale serve status --json` and `tailscale
  debug prefs` of the pinned Tailscale, and of `lxc network list` and `lxc
  storage list` of the LXD releases in use
  (`preflight/pinka_preflight/detect/components.py`).

## Known issues

- **NVIDIA driver pins outside Ubuntu** (open item, owner, with the
  "commit" for the GPU guard). The driver packages of Debian 13, Fedora 43
  and 44, EL 9 and 10 and openSUSE Leap 15.6 and 16.0 are still unverified
  (`packages.nvidia_driver` is `TODO(verify)`, and the `verify` entries of
  `nvidia_driver` in `ansible/component-sources.yml`), so the play stops
  for a manual install there instead of installing; Arch, a rolling
  release, installs its current `nvidia-open` without a pin, judged by the
  pinned release's GPU list. They must be verified (with the GPU list of
  their release, which `make lock-check` then requires) before a GPU
  install on those families is claimed to work.
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
  git-ignored `.ansible-collections/`, which setup verifies and keeps.
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
  (`lock-check` warns about it). The same for `containers.ci_images.ubuntu_2404`
  (owner decision after 3f: keep the pin, update it with the postgres tag):
  its tag `24.04` now points to
  `sha256:68ee22004a9109b44973313b02ff7b1de250f1de0f5ecb628d73f579aaff33ac`;
  the lock keeps
  `sha256:534baea6a22c03a63003dbc8dbe78fe34bc0d7e595d9a9dc9834884ff530eb55`.
- `make lock-collections` refused every symbolic link in an artifact, so
  the owner's first run stopped at `community.general`'s
  `tests/utils/shippable/aix.sh`. Fixed after `d5fe24d` (separate commit):
  one artifact rule (`tools/lockcheck/pinka_lockcheck/artifact.py`)
  for `make lock-collections`, `make setup` and bootstrap, accepting only
  relative links that stay inside the collection (`3d4bab2`).
- MkDocs 1.6.1 (pinned) has had no release since 2024-08-30, and the Material
  for MkDocs team announces that MkDocs 2.0 removes plugins. Builds work with
  the pin; the replacement decision belongs to the docs-site plugin review
  (docs spec §B.2) and needs an ADR and owner approval.
- The build environment's default uv (0.8.17) is older than the pinned
  0.12.23; since 3d.6 `make setup` installs the pinned uv into
  `build/tools/` (checked against its sha256) and runs `uv sync` with
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
  `docs/IMPLEMENTATION_NOTES.md`).
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

Order after 3f (owner): commits 1 and 2 are done (`1a46f64`, `b7be7ff`),
and 3f-1 is recorded (`eb02d37`). The spec edit (`4591db9`), D1
(`8558bee`) and its CI fix (`74377ec`) are committed, then the small spec
edit after D1 (`0378425`), the installer fix of the written files
(`32b2c95`) and the GPU support guard, pushed together. Then the
pre-existing access with the SSH `Match` block as a D1 follow-up, then D2,
A, B, C, E, F and G, each as its own step waiting for "commit". Then
the real-machine tests on the owner's computers
(4.1 and 4.2 first), then the history rewrite, 3g and 3h. The owner runs
`make lock-pins` on their machine and pastes the output separately. Still
open: the remaining `TODO(verify)` items (98), which need network access or
reference hardware.
