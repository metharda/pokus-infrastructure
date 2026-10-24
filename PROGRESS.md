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

## Handover (end of the session after 3f-1 A, 2026-10-24)

The owner's handover request is verbatim under "The follow-up and four
more commits" in the remaining plan. A new session starts B from the
repository alone; read this section, then `CLAUDE.md` ("Standing rules at
a glance" first), then the step's spec sections.

**Where 3f-1 stands.** On `moving/on`, each commit pushed on its own, its
CI reported job by job, green but the known red `lock-check` (24
`TODO(verify)` marker errors, 103 markers in the repository):

| Part of 3f-1 | Commit | Record |
| --- | --- | --- |
| D1 (install record, pre-existing components) with its follow-ups | `8558bee` to `16bf749` (status table) | `docs/IMPLEMENTATION_NOTES.md` |
| One CI run per commit | `6bab38b` | idem |
| D2 (uninstall) with the owner's five fixes | `8696cb3` | idem |
| A (requirements computed from the choices) | `00ff2c4` | "Requirements computed from the choices (A, 3f-1)" |
| A's follow-up (inference and training switches, computed training cap) | `a9bb439` | "Inference and training switches, the computed training cap" |
| Item 1: the cap leaves vLLM out | `1a8b18b` | "Correction: vLLM and a job never add up" |
| Item 2: the Incus pool's default size is computed | `5fb3fe8` | "The computed Incus pool size" |
| Item 3: restore.yml gets the feature flags | `d9a98b0` | "restore.yml without the gate" |
| Item 4: the Dependabot alerts, every lock at a fixed version | `ac157ad` | "Dependabot alerts and the locks" |

Nothing of 3f-1 is verified end to end until `make test-distros` and the
real-machine tests pass (4.4, 4.7, 4.8 and 4.14 above all).

**Next step: B**, the capacity plan screen in the installer (definition
verbatim as "3f-1.B" in the remaining plan; the decision logic stays in
preflight, which already computes everything the screen shows:
`requirements` of the host profile, with the switches, the training cap
and the pool). **Then, in this order:** C (Ubuntu 26.04 LTS), E (DCGM
wiring), F (Zabbly version per architecture), G (review of the
real-machine test specification, last), each its own step with the full
gate, waiting for the owner's "commit", its CI reported after the push;
then the **feature-complete checkpoint** the owner asked for (verbatim
under "Order within 3f-1, and the checkpoint before the tests" in the
process rules); then the real-machine tests (4.1 and 4.2 first) and 3h.

**Open items and open decisions** (each with its details where it is
recorded):

- For the owner's confirmation, built as proposed: the training cap's
  floor and ceiling (4 and 24 GiB), a host not degraded for a feature its
  switch turns off, `gpu_training` in the support matrix, the arbiter
  staying with both switches off ("Open questions", A's follow-up); the
  pool's floor of 30 GiB and its 50 % margin (approved deviation PL1,
  item 2); the two updates beyond the 16 alerts, filelock in the shipped
  models lock and zod (item 4).
- For the next spec edit: approved deviations PL1 (the computed pool
  size) and V1, and the others marked so in "Approved deviations".
- Still open from step 1: the reference agents for scenario validation,
  the QA results schema, the page-ownership table (H5) ("Open questions").
- Known issues: NVIDIA driver pins outside Ubuntu and the rest of "Known
  issues" (`restore.yml`'s flags are fixed, item 3); Tailscale on the rpm
  releases stays unverified until it is closed before an rpm family is
  claimed as tested ("Open questions").
- To remove when possible: the uv override of pytest (`pyproject.toml`),
  once pytest-textual-snapshot or syrupy allows pytest 9; the npm
  `overrides` (`package.json`), once markdownlint-cli2 and mermaid require
  the fixed versions.
- Owner deliverables: `make lock-pins` on the owner's machine (GitHub's
  SSH host keys among them), the real-machine tests, the test tailnet's
  policy for the uninstall lane's variant ("Owner deliverables pending").
- After the owner merges `moving/on` into `main`: check that the
  Dependabot alerts on `main` close (this session cannot read them: the
  API answers 403), and the weekly runs, which run only from `main`.
- Candidates for the next maintenance update: the tags `lock-check` warns
  about (postgres, refplat_job, ci_images ubuntu_2404 and debian_13), by
  "How to run a maintenance update of the locks".

**Rules the owner gave in this session**, all now in `CLAUDE.md`
("Standing rules at a glance" and the sections it names): each commit
pushed on its own and its CI reported job by job; no side work during
3f-1 (an open item instead; a defect in an existing feature is fixed as
its own small commit when the owner says so, as item 3); the push guard
with the command that restores `origin` after a restart; the CI skip
list; the local random-order and per-file runs before every proposal;
the re-run rule; unsigned commits under the owner's noreply identity; no
attribution lines; merges by fast-forward or merge commit only; no
Dependabot pull requests, and the alerts reviewed and the locks updated
at each maintenance update. "Do not start B in this session" applied to
this session only.

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
access), and the small spec edit for A's follow-up (owner, with the
"commit" for A: "This touches §2.3 and the variables; I authorize the small
spec edit it needs.", limited to the inference and training switches and
the computed training cap). After the commit of each, the specs are
read-only again.
Component copies are byte-identical.

| File | Title | sha256 |
| --- | --- | --- |
| `specs/pokus-infrastructure-claude-code-prompt.md` | Build Prompt: pokus-infrastructure (Pokus Infrastructure, pinka) | `685306c16b6e6db5f318d59670993bf2c47f301ec78d5967375b476651b246c8` |
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
the small spec edit after D1 from `a661077c…` to `e01ad3d7…`
(`docs/IMPLEMENTATION_NOTES.md`, "Spec edit after D1"), and the small spec
edit for A's follow-up from `e01ad3d7…` to `bc7f11e9…`
(`docs/IMPLEMENTATION_NOTES.md`, "Inference and training switches, the
computed training cap (A follow-up, 3f-1)"), and the owner's correction of
the cap after `a9bb439` (vLLM and a job never add up) from `bc7f11e9…` to
the checksum above; its copy
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
- **CI re-runs (owner, with the "commit" for the JUnit XML; verbatim in
  `CLAUDE.md`, "CI after every push"):** one re-run of a job only for a
  failure in an external service the commit does not touch, reported with
  the error; a job that fails twice is a real failure.
- **Test results by name (owner, with the "commit" for the per-file run;
  verbatim under "JUnit XML and end-to-end verification" in the remaining
  plan):** every test job (`test` with the Python 3.9 runs,
  `test-random-order`, `test-isolated`) writes JUnit XML and CI keeps it as
  the artifact `junit-<job>-attempt-<n>`; a report of a specific test's
  result cites that artifact.
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
  `Generated with` line. The one-time rewrite removed the only two such
  trailers (from the first two commits on `main`); the scan allows no
  exception.
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

  Since the one-time rewrite (with the owner's "rewrite", 2026-10-22) the
  working branch is `moving/on`, held in this checkout with the rewritten
  `main` until the owner pushes both. `CLAUDE.md` carries the same
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
- **Order within 3f-1, and the checkpoint before the tests (owner, with the
  "commit" for D2, verbatim):**

  > Order from here: finish 3f-1 completely before any real-machine test, so the
  > tests cover a finished product once. After D2: A, B, C, E, F, then G, each as
  > its own step with the full gate, waiting for my "commit", and its CI reported
  > after the push. No new side work in between unless CI or a test forces it; if
  > something comes up, record it as an open item and continue.
  >
  > When G is done, give me a feature-complete checkpoint:
  > - every feature of the infrastructure (from the spec masters, the approved
  >   deviations and 3f-1), each with the real-machine test numbers of the QA
  >   specification that verify it, and every feature no test covers;
  > - every open TODO(verify) item, grouped by what closes it (a real-machine test,
  >   make lock-pins on my machine, a vendor publishing something, a maintenance
  >   update);
  > - what I must prepare before the tests: accounts, tailnet tags and ACL rules,
  >   auth keys, secrets, the test agent's machine file, the backup of computer B.
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

- Working branch `moving/on` since the one-time rewrite (3g), in the
  fresh repository `pokus-infrastructure` (this checkout's `origin`; the
  owner created it instead of renaming the old one); the owner merges to
  `main`. The infrastructure is at the repository root
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
  (decision 1 after D1)"; the three were pushed together. CI of `37c062e`
  (run 39217765497): every job passed (privacy, lint, test, the new
  test-random-order, docs, all six check-mode and all six
  check-mode-extended lanes, release-package, lock-collections) but the
  known red `lock-check` (24 marker errors, the postgres and `ubuntu:24.04`
  tag warnings; its new `gpus` check recomputed both lists). The test job
  skipped 8 tests, as on every earlier run that was reported as green
  without naming them: the 7 `nft -c` checks of
  `ansible/tests/test_templates.py` (`test_nft_ruleset_is_valid` with
  `default`, `extra-interfaces-grafana`, `vm-targets` and `lxd`,
  `test_telemetry_guard_is_valid`, `test_inference_guard_ruleset`,
  `test_split_guard_ruleset`: GitHub's runner refuses the unprivileged
  network namespace) and
  `bootstrap/tests/test_bootstrap.py::test_git_state_refuses_a_foreign_checkout`
  (needs root). The CI skip list (owner, after `37c062e`) makes them run in
  CI through `sudo -n` and fails any skip it does not name: `006c40d`. CI of
  `006c40d` (run 39225936248): every job passed (privacy, lint, test,
  test-random-order, docs, all six check-mode and all six
  check-mode-extended lanes; release-package in run 39225936214) but the
  known red `lock-check` (24 marker errors, the postgres and `ubuntu:24.04`
  tag warnings); `lock-collections` did not run (the lock is unchanged) and
  `links` runs weekly. The test job ran the 8 tests by name: the 7 `nft -c`
  checks and `test_git_state_refuses_a_foreign_checkout` passed (pytest 3286
  passed, none skipped); the Python 3.9 runs passed 143 with the one listed
  skip (the reference validator test of `preflight/tests/test_fixtures.py`,
  named in the log), 223 and 7. The per-file process run (owner decision 3
  after `37c062e`) is `2f27132`. CI of `2f27132` (run 39235137608): every
  job passed but the known red `lock-check` (24 marker errors, the postgres
  and `ubuntu:24.04` tag warnings), after one re-run of two jobs:
  `check-mode (ubuntu_2404)` and `check-mode-extended (fedora_44)` failed in
  `make setup` on the first attempt, both at 10:08 UTC with the same Galaxy
  answer (the version listing of `community.crypto` without `results`;
  "nothing unverified was installed"), while the 18 other jobs downloaded
  the same collections; both passed on the re-run. `test-isolated`: 88
  files, 3290 passed, none skipped, 308 s of tests, 6 min 39 s for the job,
  so on every push; `test`: pytest 3290 passed; `test-random-order` (seed
  20261019): 3290 passed; release-package (run 39235137319) passed. JUnit
  XML of every test job (owner, with the "commit" for the per-file run) is
  `fc7cc3b`. CI of `fc7cc3b` (run 39252260630), every job on its first
  attempt: privacy, lint, test, test-random-order, test-isolated, docs, all
  six check-mode and all six check-mode-extended lanes passed;
  release-package (run 39252260657) passed; `lock-check` is the known red
  (24 marker errors, the postgres and `ubuntu:24.04` tag warnings);
  `lock-collections` did not run (the lock is unchanged) and `links` runs
  weekly. `test`: pytest 3293 passed, the Python 3.9 runs as before. The
  artifacts of the run: `junit-test-attempt-1` (ID 12186238418, sha256
  `eeb1323e…dcd5`, 4 files: `pytest.xml` and the three `py39-*.xml`),
  `junit-test-random-order-attempt-1` (ID 12185809509) and
  `junit-test-isolated-attempt-1` (ID 12185269956); this environment cannot
  read them (their download host, `productionresultssa8.blob.core.windows.net`,
  is not in its network allowlist), the owner can. The Galaxy retry, the
  re-run rule and the action pin check (owner, with the "commit" for the
  JUnit XML) are `5fc33c1`. CI of `5fc33c1` (run 39260135484), all 20 jobs
  on their first attempt, no re-run: privacy, lint, test,
  test-random-order, test-isolated, docs, all six check-mode and all six
  check-mode-extended lanes passed; `lock-check` is the known red (24
  marker errors, the postgres and `ubuntu:24.04` tag warnings); `links`
  runs weekly. release-package (run 39260135443) passed;
  `lock-collections` (run 39260135548) ran, because the commit changed its
  client, and passed: 11 requests to Galaxy, each "attempt 1 of 3", and
  "the lock's 5 artifact pins match Galaxy". The pre-existing access
  follow-up (D1 follow-up, decisions 3 and 4 after D1, with SSH1) is
  `16bf749` (`docs/IMPLEMENTATION_NOTES.md`, "Pre-existing access (D1
  follow-up, decisions 3 and 4 after D1)"). CI of `16bf749` (run
  39304851835), all 20 jobs on their first attempt, no re-run: privacy,
  lint, test, test-random-order, test-isolated, docs, all six check-mode
  and all six check-mode-extended lanes passed; `lock-check` is the known
  red (24 marker errors, the postgres and `ubuntu:24.04` tag warnings);
  `links` runs weekly; release-package and lock-collections did not run
  (no change to the lock, its tooling or the Makefile). The owner's
  decisions with its "commit" (approved deviations DL1 and AG1, the
  question on the login detection), AG1's correction and the CI minutes
  (the owner's new plan after `16bf749`, item 1) are `6aa6eab`, committed
  and not pushed (the next push is the rewritten history). The owner's
  decisions after the public-readiness audit follow as commits without a
  push: the test computers generalized to machine classes, the privacy
  scan's additions, the project files; then the rewrite proposal.
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
| 3f-1 | Features before 3g: D uninstall, A computed requirements, B capacity plan screen, C Ubuntu 26.04, E DCGM wiring, F Zabbly version per architecture, G review of the real-machine test specification | spec edit, D1 and its CI fix, the small spec edit after D1, the installer fix of the written files, the GPU guard, the CI skip list, the per-file process run, the JUnit XML, the Galaxy retry and the pre-existing access follow-up (with the SSH `Match` block) done; the decisions after it (DL1, AG1 with its correction, the login detection) and the CI minutes built; not verified end to end until `make test-distros` and real-machine tests 4.4, 4.7, 4.8 and 4.14 pass; then (owner's new plan after `16bf749`) the public-readiness audit, the rewrite and the rename, then D2, A, B, C, E, F, G on `moving/on`; D2 (uninstall) committed after the CI commit (`6bab38b`); A (requirements computed from the choices) committed (record: `docs/IMPLEMENTATION_NOTES.md`, "Requirements computed from the choices (A, 3f-1)"); its follow-up, the inference and training switches with the computed training cap and their small spec edit (owner's decisions 1 and 2 on A), committed, then the owner's four items after it (the cap without vLLM, the computed pool size, restore's flags, the Dependabot alerts); next B (see "Handover") | `eb02d37` (records), `4591db9` (spec edit), `8558bee` (D1), `74377ec` (CI fix), `0378425` (spec edit after D1), `32b2c95` (installer fix), `37c062e` (GPU guard), `006c40d` (CI skip list), `2f27132` (per-file run), `fc7cc3b` (JUnit XML), `5fc33c1` (Galaxy retry), `16bf749` (pre-existing access), `6bab38b` (one CI run per commit), `8696cb3` (D2), `00ff2c4` (A), `a9bb439` (A follow-up), `1a8b18b`, `5fb3fe8`, `d9a98b0`, `ac157ad` (items 1-4) |
| Real-machine tests | The specification's tests on the owner's computers A and B; with `make test-distros`, 4.4, 4.7, 4.8 and 4.14 are the only end-to-end verification of the firewall, privilege profiles, isolation and the install record (3f-1 is not verified until they pass) | after 3f-1 | — |
| 3g | History rewrite (identities and trailers), published as the fresh repository `pokus-infrastructure` instead of the rename (owner, after the rewrite) and made public; moved before D2 by the owner's new plan after `16bf749` | the audit and its decisions, the rewrite (owner's "rewrite") and the commit after it done; the owner created the fresh repository and pushed `main` and `moving/on` to it; then making it public | `a716eae` (the commit after the rewrite) |
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

  Decisions after the CI report of `37c062e` (owner, verbatim):

  > CI result noted, and thank you for the correction on the skips.
  >
  > Decisions:
  > 1. The nftables check commit: approved as proposed, before the pre-existing
  >    access follow-up. Widen it into a general rule:
  >    - CI keeps an allowlist of tests that may skip there, each with its reason;
  >      any skip in CI that is not on the list fails the job;
  >    - the root-only bootstrap test also runs in CI through the runner's sudo, so
  >      the list should be empty or nearly so;
  >    - every gate and CI report from now on names each skipped test, not only the
  >      count. Record the rule in CLAUDE.md.
  > 2. Test counts: the local gate reports 3624 passed, CI's test job 3273 passed
  >    plus 8 skipped. Explain the difference of about 340 tests (for example the
  >    Python 3.9 run or tests outside CI's selection). If any test file or group
  >    never runs in CI, list it and make CI run it, or tell me why it cannot.
  > 3. Per-file process run: yes, as its own small commit after the nftables one.
  >    Each test file runs in a process of its own (make test-isolated and a CI job).
  >    Tell me its CI duration: under about ten minutes, on every push; otherwise
  >    nightly and on demand, required before any release. List any failures it
  >    exposes before fixing them.
  > 4. Gating in a separate worktree: symlinked tool folders must not be able to
  >    change the main tree again (npm emptying node_modules). Use real copies in
  >    gating worktrees, or record the caveat in CLAUDE.md.
  >
  > Order: the nftables/skip commit, the per-file run, then the pre-existing access
  > follow-up and D2, each waiting for my "commit".

  Answer to 2 (details in `docs/IMPLEMENTATION_NOTES.md`, "CI skip list
  (after `37c062e`)"): the gate's test line adds two steps, the Python 3.9
  unittest runs (373 tests: preflight 143, pinka 223, the install record
  module 7; 1 skipped) and pytest (3253 collected here); CI's test job runs
  the same two steps, and the line quoted was pytest's alone. CI's pytest
  collects 28 more (3281): the database tests run once per installed
  PostgreSQL major, 16 and 18 in CI, 16 here (`test_postgres_sql.py` 48
  against 25, `test_refplat.py` 46 against 41). Never run in CI: the 8 skips
  (now run there), the golden images' own tests (`images/*/tests/*.sh`, run
  inside built images on an Incus host; CI only runs ShellCheck on them) and
  `make test-distros` (VMs); CI never provisions anything.
- **JUnit XML and end-to-end verification** (owner, with the "commit" for
  the per-file run, after the CI report of `006c40d`, verbatim):

  > Instead of a separate -v step for the 8 tests: every test job (test,
  > test-random-order, test-isolated, and the Python 3.9 runs if their runner can
  > emit it) writes JUnit XML and uploads it as a CI artifact, so each test's name
  > and result is kept for every run. Fold that into the next commit, and from then
  > on cite the artifact when you report a specific test's result.
  >
  > Record in PROGRESS.md, under the real-machine tests: the firewall, privilege
  > profiles, isolation and the install record are verified end to end only by
  > make test-distros and real-machine tests 4.4, 4.7, 4.8 and 4.14; 3f-1 is not
  > considered verified until those have passed.
  >
  > Then the pre-existing access follow-up and D2, each waiting for my "commit".

  The JUnit XML is the commit after `2f27132` (`docs/IMPLEMENTATION_NOTES.md`,
  "JUnit XML of every test (after `2f27132`)"); the record is under the
  real-machine tests below.
- **Galaxy retry, CI re-runs, action pins** (owner, with the "commit" for
  the JUnit XML, after the CI report of `2f27132`, verbatim):

  > Decisions:
  > 1. Galaxy retry: yes, in setup and in every path that downloads collections
  >    (setup, bootstrap from a checkout, lock-collections). Retry only transport
  >    errors and malformed responses (like the missing results field), up to 3
  >    attempts with increasing delay, each attempt logged. A hash or signature
  >    mismatch is never retried: it stops at once with the expected and actual
  >    values. Tests for both paths. Its own small commit before the pre-existing
  >    access follow-up.
  > 2. CI re-runs: one re-run of a job is acceptable only for a failure in an
  >    external service that the commit does not touch, and every re-run is reported
  >    with the error, as you did. Record that rule in CLAUDE.md with the retry
  >    commit. A job that fails twice is a real failure.
  > 3. Confirm that every third-party action in every workflow is pinned to a full
  >    commit SHA like upload-artifact, with its version in a comment. If any is not,
  >    pin it in the same commit.
  >
  > Then the pre-existing access follow-up and D2, each waiting for my "commit".

  Committed as `5fc33c1` (`docs/IMPLEMENTATION_NOTES.md`, "Retries of the
  collection downloads (after `fc7cc3b`)").
- **Pre-existing access** (D1 follow-up; decision 4 after D1 with its
  safeguard, decision 3 as SSH1, both verbatim above): built in the commit
  after `5fc33c1` (`docs/IMPLEMENTATION_NOTES.md`, "Pre-existing access (D1
  follow-up, decisions 3 and 4 after D1)"). Preflight records the access
  (listeners, login accounts, the install's session) and calls the host
  dedicated or shared; the install record keeps it and what is closed with
  consent; the firewall keeps what was reachable on a shared host, and
  pinka's table `inet pinka_access_guard` drops what the owner closes, on
  every backend; the install's access path closes at the end of a run with
  consent and a researcher's key login over the tailnet during that run;
  sshd's settings are a `Match Group pinka` block on a shared host; doctor
  accepts the recorded listeners and warns while the path is open
  (`network.install_path`); the installer's screen 8 has the checkboxes.
  Interpretations for the owner are listed in the notes. Committed as
  `16bf749`. Decisions with its "commit" (owner, verbatim):

  > Decisions:
  > 1. Doctor's listener check: approved as a deviation, with these conditions:
  >    - a pre-existing listener is accepted only if it matches the install record
  >      exactly (address, port, protocol and owning unit or executable); any listener
  >      that appears after the install still fails doctor;
  >    - the install record is writable only by root, and doctor fails if its owner or
  >      mode is wrong;
  >    - pinka's own services never use the exception;
  >    - on a dedicated host the strict rule of §7.5 applies unchanged.
  >    Record it as an approved deviation with these conditions.
  > 2. The guard table inet pinka_access_guard on every backend: approved. Confirm
  >    with tests that it drops only new connections, never matches tailscale0 or
  >    loopback, and does not depend on ufw's or firewalld's own tables; and add to
  >    the test-distros lanes for ufw and firewalld: reload the user's firewall
  >    (ufw reload, firewall-cmd --reload) and check the guard is still in place and
  >    the closed listener stays closed while SSH over the tailnet still works.
  > 3. Tell me how a researcher's tailnet login during the run is detected (which
  >    log, which fields), and whether a login without a key or from an address
  >    outside the tailnet can be mistaken for one.
  >
  > D2: its report must include a table of everything uninstall removes, restores or
  > keeps, with the guard table, the pre-existing access record and the SSH Match
  > block in it, so I can check it against the install record before the commit.

  Built in the commit after `16bf749` (`docs/IMPLEMENTATION_NOTES.md`,
  "Decisions after `16bf749` (DL1, AG1, the login detection)"): doctor's
  exact match (approved deviation DL1); the guard's drop rules only for new
  connections and never on loopback, with only SSH spared on `tailscale0`
  (corrected with the owner's new plan, below), checked by a static
  test and against the kernel in a network namespace, and the `access`
  stage of `make test-distros` on the ufw and firewalld lanes (approved
  deviation AG1); the answer on the login detection, which is now stricter
  (journald's `_UID` 0 and `_COMM` sshd, an address of a current tailnet
  peer).
- **New plan after `16bf749`** (owner, verbatim; it moves the history
  rewrite, the rename and making the repository public before D2, and
  replaces, for this order only, "after the real-machine tests" in 3g and
  "never opens a pull request" for the one pull request it names; the
  owner opens it). One elision, marked with brackets: the address of item 3
  (the privacy scan refuses any e-mail address outside the example domains
  in a file; the address is in the commit identities after the rewrite):

  > Do not commit yet. New plan: the history rewrite, the rename and making the
  > repository public move forward, before D2. Then I open a pull request from
  > moving/on to main, and you continue with D2 on moving/on.
  >
  > 1. Finish the current step, as one commit:
  >    - correction to decision 2: "never match tailscale0" was too broad on my side.
  >      The intent was that SSH over the tailnet is never cut. On tailscale0, exempt
  >      only the SSH port; every listener I consented to close is closed on the
  >      tailnet too, on every backend. Update the kernel test and the test-distros
  >      access stage (tcp/8080 closed over the tailnet as well, SSH over the tailnet
  >      still works).
  >    - GitHub Actions minutes (the account is at 90 % until the repository is
  >      public): concurrency with cancel-in-progress per branch; the random-order
  >      run becomes the main test job's run, not a separate job; test-isolated,
  >      check-mode-extended and release-package run weekly, on workflow_dispatch and
  >      on pushes that touch their inputs; pushes that change only docs or records
  >      skip the Ansible lanes. You keep running make test-random-order and make
  >      test-isolated locally before every proposal. Give me the estimated minutes
  >      per typical push before and after.
  >    Wait for my "commit".
  > 2. Public-readiness audit, before the rewrite. Report, change nothing yet:
  >    - every workflow: no pull_request_target that checks out PR code; minimal
  >      permissions per job; no secret printed; jobs that need
  >      PINKA_PRIVACY_DENYLIST behave correctly on pull requests from forks, which
  >      get no secrets; anything that would run code from a fork with write access
  >      or secrets;
  >    - personal data in the current tree and in the whole history (every commit's
  >      content and message on every branch): my personal e-mail address (the
  >      non-noreply address in the commit identities) is the only item on my
  >      denylist, so scan the full history for it yourself; also look for any other
  >      e-mail address, names, usernames, hostnames, IP addresses, tailnet names,
  >      home paths, and the descriptions of my test computers in PROGRESS.md and
  >      IMPLEMENTATION_NOTES.md (QA spec rule 12 says machine details stay
  >      private). Propose what to remove or generalize, and whether the rewrite
  >      should also scrub it from history;
  >    - LICENSE, NOTICE, README (with a clear pre-release status), SECURITY.md,
  >      CONTRIBUTING.md: present or missing;
  >    - tell me exactly what to put in the PINKA_PRIVACY_DENYLIST secret (format),
  >      given that my personal e-mail is its only entry.
  > 3. The rewrite, proposed only after 1 and 2 and waiting for my explicit
  >    "rewrite": as recorded (trailers removed, author and committer set to my
  >    identity, dates kept), with this identity: name as on bef51af, e-mail
  >    [the owner's GitHub noreply address], plus any history scrubbing I
  >    approve from 2. Afterwards no commit may carry any other e-mail address. Set
  >    the same identity in this session's git config for every later commit. Show
  >    me the before/after mapping and the identity listing.
  > 4. After my "rewrite": the exact commands for me to force-push main, push the
  >    rewritten work as moving/on, delete claude/zen-albattani-qfwwps on the
  >    remote, and update other clones. Tell me whether this session can push to
  >    moving/on afterwards; if it cannot, say which branch you will push to.
  > 5. A checklist for me after the rename: making it public; branch protection on
  >    main (no force-push, required CI checks); requiring approval for workflows
  >    from first-time contributors; private vulnerability reporting; secret
  >    scanning; re-checking the secrets.
  > 6. The pull request moving/on -> main: the title and description, and how to
  >    merge it so every commit keeps its author date and hash: a merge commit or a
  >    fast-forward push, never squash or rebase-and-merge. Update HANDOFF and
  >    CLAUDE.md to match.

  Item 1 is built in the commit after `16bf749`, together with the
  decisions with its "commit" (one commit, as asked): on `tailscale0` the
  guard spares only tcp/22 and drops what was closed, as on every other
  interface; the kernel test and the `access` stage check it over the
  tailnet. CI minutes: `docs/IMPLEMENTATION_NOTES.md`, "GitHub Actions
  minutes (the owner's new plan after `16bf749`)". Items 2 to 6 follow in
  that order; D2 waits until the owner has opened the pull request.

  Item 1 is `6aa6eab`, committed with the owner's "commit" and not pushed.
  The public-readiness audit (item 2) was reported; the owner's decisions
  on it, verbatim but for one elision in brackets (the privacy scan refuses
  any e-mail address outside the example domains in a file):

  > commit, but do not push yet; the next push is the rewritten history.
  >
  > Decisions:
  > 1. Keep the rename, option (b). I accept that my personal address stays visible
  >    in old, unreferenced commits. To limit the rest (the attribution lines and the
  >    machine descriptions in old commits):
  >    - give me the exact gh commands to delete every workflow run of this
  >      repository, to run after the force-push and before making it public, and a
  >      check that none remain;
  >    I have disabled Actions in this repository until it is public.
  > 2. Test computer descriptions: generalize them to the machine classes as you
  >    proposed, keep the exact details in a git-ignored local file, and scrub the
  >    old text from history in the rewrite.
  > 3. Scan additions: yes, all three (commit messages scanned for e-mail addresses
  >    and denylist terms; every author and committer e-mail must be a GitHub noreply
  >    address or [GitHub's own noreply address]; a visible warning in CI without the denylist).
  >    Move PINKA_PRIVACY_DENYLIST to the lint and privacy jobs only.
  > 4. Project files: add a root NOTICE, the pre-release status line in the README,
  >    SECURITY.md (GitHub private vulnerability reporting, no e-mail address),
  >    CONTRIBUTING.md (the gate, the privacy rules, a noreply identity, no
  >    attribution lines) and CITATION.cff (author as on bef51af).
  >    Add a tag ruleset limiting v* tags to me to the checklist for after the move.
  >
  > Build 2, 3 and 4 as commits before the rewrite, with the full gate, committed
  > without pushing. Then propose the rewrite with the before/after mapping and the
  > identity listing, and wait for my "rewrite". After it, steps 4 to 6 of my
  > earlier message as planned (the commands for me to push, the checklist, the
  > pull request).

  The audit's findings are in `docs/IMPLEMENTATION_NOTES.md`,
  "Public-readiness audit (the owner's new plan after `16bf749`, item 2)".
  From the commit after `6aa6eab` on, this checkout's git identity is the
  owner's name with the GitHub noreply address of item 3 of the new plan
  (set in the local git configuration only), so no later commit carries the
  personal address. Nothing is pushed until the owner pushes the rewritten
  history; Actions is disabled in the repository until it is public, so CI
  of these commits runs after the move.

  Built as commits without a push: decision 2 (the machine classes) is
  `0e89479`; decision 3 (the privacy scan: commit messages and identities,
  the CI warning, the secret only on `lint` and `privacy`) is the commit
  after it (`docs/IMPLEMENTATION_NOTES.md`, "Decision 3: the privacy scan's
  additions"), `7ab1482`; decision 4 (a root `NOTICE`, the pre-release
  status in the README, `SECURITY.md`, `CONTRIBUTING.md`, `CITATION.cff`)
  is the commit after that ("Decision 4: the project files"). The
  checklist for after the move (item 5 of the new plan) gains a tag
  ruleset that lets only the owner create `v*` tags (owner, decision 4).
  Then the rewrite proposal, which waits for "rewrite".

  The rewrite proposal (identity listings, the mapping, the dry run) was
  reported; the owner's answer, verbatim:

  > rewrite, with these decisions:
  >
  > 1. Signatures: (a). The rewrite leaves all 68 commits unsigned, and signing is
  >    off in this checkout's local config for later commits.
  > 2. No old-to-new hash table in any tracked file. Publishing old hashes would let
  >    anyone open the old commits on GitHub, with my personal address, the
  >    attribution lines and the machine details. Instead:
  >    - in the rewrite itself, replace every reference to an old commit hash in
  >      commit messages (full or abbreviated) with the corresponding new hash, the
  >      way git filter-repo does by default; this is the one allowed change to
  >      messages besides the trailer removal;
  >    - in the follow-up commit, replace the 283 references in tracked files with
  >      the new hashes;
  >    - the mapping stays only in local/rewrite-mapping.md, which git ignores.
  >    Afterwards, check that no old hash (from the left column of the mapping, full
  >    or as a 7+ character prefix) appears in any file or message of any commit on
  >    either branch, and report the check.
  > 3. NOTICE stays as it is.
  >
  > Then confirm the tips against the dry run (they will differ where messages
  > changed; say which), build the follow-up commit and wait for my "commit". Then
  > steps 4 to 6.

  Built in this checkout first with the references replaced in messages
  only; the check the owner asked for found old hashes left in the files
  of 66 earlier commits (each commit's records name the commits before
  it), which the follow-up commit could replace at the tip only. The
  owner's answer, verbatim:

  > rewrite files too.
  >
  > Conditions:
  > - replace only whole commit-hash tokens that are in the mapping, never part of a
  >   longer hex string (sha256 digests, fingerprints, IDs), as in your dry run;
  > - verify that in every commit, each changed line differs from the original only
  >   by replaced hashes, apart from the agreed scrub of the machine details;
  > - rerun every earlier check, then my check (old hashes in messages and in the
  >   files of every commit on both branches: 0), and report the new tips.
  >
  > Then rebuild the follow-up commit and present both again; I will reply "commit".

  Done in this checkout (`docs/IMPLEMENTATION_NOTES.md`, "The one-time
  rewrite (3g)"): the rewritten `main` and `moving/on`, 68 commits, every
  author and committer the owner's noreply identity with the original
  dates, unsigned; the two trailers removed; the machine descriptions
  replaced in 13 commits; the references to earlier commits replaced in 16
  messages and in the files of 66 commits; then the commit after the
  rewrite on `moving/on` with the scan's exceptions removed and these
  records. No message and no file of any commit names an old hash.

  That commit is `a716eae`, committed with the owner's "commit". The
  owner's answer after the push commands, verbatim but for the account
  name, elided in brackets (no account name in a tracked file):

  > I did not rename. I created a fresh, empty repository [owner]/pokus-infrastructure
  > and pushed main (4915ce1) and moving/on (a716eae) from the bundle; ls-remote shows
  > exactly those. The old repository [owner]/pokus stays private and is not touched
  > again; its runs are not deleted, since it will never be public.
  > - Attach [owner]/pokus-infrastructure to this session, set it as origin, and test
  >   git push --dry-run origin moving/on. Report the result before anything else.
  > - Then delete the old local branch and old references here, so the old commits are
  >   gone from this checkout too.
  > - In the next commit, record in CLAUDE.md and PROGRESS.md that 3g used a fresh
  >   repository instead of a rename (my decision after the activity-view finding),
  >   and drop the run-deletion steps from the docs.
  > Then continue as planned: CI of the pull request job by job, then D2.

  So 3g used a fresh repository instead of the rename (the owner's decision
  after the audit's finding that a renamed repository keeps the old
  commits reachable and lists the force push in its activity view): the
  rewritten `main` and `moving/on` are the only branches of
  `pokus-infrastructure`, the old repository stays private and untouched,
  and its workflow runs stay (it is never made public), so decision 1
  after the audit (deleting every workflow run) no longer applies. This
  checkout's `origin` is the new repository; `git push --dry-run origin
  moving/on` succeeded; the old local branch, its references and the old
  commits are gone from this checkout.

  CI of `a716eae` in the new repository (pushed by the owner), job by job:
  `changes`, `privacy`, `lint`, `docs`, all six `check-mode` lanes,
  `test-isolated`, all six `check-mode-extended` lanes, `release-package`
  and `lock-collections` passed; `lock-check` is the known red (24
  `TODO(verify)` errors; four tag warnings: postgres, `refplat_job`,
  `ubuntu:24.04` and `debian:13` moved, the pins unchanged); `test` failed
  in `test_the_access_guard_against_the_kernel` (the probe aborted at
  interpreter shutdown, exit -6), so `required` failed too; `links` runs
  weekly. The privacy scan ran without the denylist (no
  `PINKA_PRIVACY_DENYLIST` secret in the new repository yet) and said so
  in a warning. The fix is the commit after `a716eae`
  (`docs/IMPLEMENTATION_NOTES.md`, "CI of `a716eae`: the access guard
  probe's shutdown").
  `claude/zen-albattani-qfwwps` keeps the old history in this checkout
  only; nothing is pushed until the owner runs the pushes.
- **The owner's review of D2 and the CI runs** (after `fb665c2`, verbatim
  but for one elision in brackets):

  > Not yet "commit". I checked the uninstall table; fix these first, in the same
  > D2 commit:
  >
  > 1. Data and package purges: list where every kept data item lives (database,
  >    telemetry stores, models, adapters, artifacts, verification results, keys,
  >    backups), including Docker volumes and Incus storage. For every package
  >    uninstall removes, on every family, check whether its maintainer scripts
  >    delete data on remove or purge (for example /var/lib/docker or
  >    /var/lib/containerd on a Docker pinka installed). Kept data must never be
  >    deleted through a package script: use remove instead of purge where needed,
  >    or keep the data directory explicitly, and add a test with a fake script
  >    that deletes a data path. Without --purge-data, nothing listed as kept may
  >    be lost.
  > 2. Kept backups must stay usable: say where the restic password and repository
  >    settings live. If they are under a path uninstall removes, keep them in a
  >    root-only file outside it, or refuse unless the owner confirms a copy exists.
  >    The report names where the key is. The same for any key needed to verify or
  >    decrypt kept records.
  > 3. Boot-time state: for everything pinka changed that takes effect at boot
  >    (initramfs and the nouveau blacklist, kernel command line or GRUB, modprobe
  >    options, sysctl, ld.so cache, systemd presets), uninstall restores it and
  >    regenerates what is needed (update-initramfs or dracut, the bootloader
  >    config, sysctl --system, ldconfig, daemon-reload). The report says whether a
  >    reboot is needed.
  > 4. The report lists manual follow-ups outside the host: revoke the platform's
  >    deploy key on the Git host, delete the logged-out Tailscale node in the admin
  >    console, if pinka joined it.
  > 5. Push guard: since a session restart reset origin to the old repository,
  >    check before every push that origin is [owner]/pokus-infrastructure, and add
  >    a pre-push hook in this checkout that refuses any other remote URL. Record the
  >    rule in CLAUDE.md.
  >
  > Separately, as its own small commit before D2: CI runs twice for every commit
  > of an open pull request from moving/on (once for the push, once for the pull
  > request). Make it run exactly once per commit:
  > - every commit on main and on moving/on gets one real CI run, whether or not a
  >   pull request is open;
  > - pull requests from forks still get a full CI run;
  > - the required check on a pull request's head commit always comes from a real
  >   run of that commit; it must never be satisfied by a skipped or cancelled job
  >   (a skipped required job counts as passing on GitHub);
  > - the weekly and on-demand workflows keep working.
  > Propose the trigger design and explain how it meets each point, then build it.
  >
  > I have opened the pull request moving/on -> main at fb665c2. I will merge it
  > myself by fast-forward or a merge commit once its required check passes. Then
  > propose D2 again with the fixes above.

  The CI part is the commit after `fb665c2`
  (`docs/IMPLEMENTATION_NOTES.md`, "One CI run per commit"); the five D2
  fixes follow in the D2 commit. A pull request from `moving/on` ran CI
  twice per commit because `ci.yml` and `lock-collections.yml` ran on every
  push and on every pull request.

  The owner merged the pull request with a merge commit, `6ea7468` on
  `main` (2026-10-23, the tree of `fb665c2`). CI of `6ea7468`, job by job:
  `changes`, `privacy`, `lint`, `docs`, `test`, all six `check-mode`
  lanes, `required`, `test-isolated`, all six `check-mode-extended` lanes,
  `release-package` and `lock-collections` passed; `lock-check` is the
  known red (24 `TODO(verify)` errors; four tag warnings: postgres,
  `refplat_job`, `ubuntu:24.04` and `debian:13` moved, the pins
  unchanged); `links` runs weekly. `main` now holds a merge commit that
  `moving/on` does not, so the next merge of `moving/on` is a merge commit
  too.

  The five fixes in the D2 commit (`docs/IMPLEMENTATION_NOTES.md`,
  "Uninstall: what stays and what nothing may delete"):
  1. Where every kept item lives is in `docs/how-to/uninstall.md`'s table
     and the notes: every store of pinka's containers is a bind mount below
     `/srv/pinka` (no named Docker volume, checked by
     `test_pinkas_containers_keep_their_data_below_the_research_space`);
     the data of a Docker, Incus or Tailscale pinka installed stays in
     their own directories (Docker's data root as `docker info` names it).
     The maintainer scripts were read for every package pinka installs
     from a vendor repository (Docker's deb for Ubuntu 24.04, 22.04 and
     Debian 13 and its rpm for Fedora 43 and EL 9, Zabbly's Incus,
     Tailscale's deb and its rpm for Fedora, EL 10 and openSUSE Leap 16,
     the NVIDIA toolkit's deb and rpm) and for Ubuntu's own packages of
     the lists (24.04 and 22.04): Tailscale's deb postrm deletes
     `/var/lib/tailscale` and Zabbly's `incus-base` deletes `/var/lib/incus`
     with its cache, logs and lxcfs state on purge; no other script deletes
     kept data (Ubuntu's sysstat, sudo, systemd-timesyncd and
     selinux-policy-default delete their own state on purge, which is not
     the lab's). The distributions' own packages on Debian, Fedora, EL,
     openSUSE and Arch could not be read here (their mirrors are blocked
     from this environment): an open item under "TODO(verify)"; the guard
     protects the kept data whatever their scripts do, and the uninstall
     lane checks it on every family. Those two are removed without purging (`purge_deletes`;
     Tailscale's removal masks `tailscaled`, which uninstall unmasks), and
     every kept data directory is mounted read-only while packages go; when
     one cannot be, no package is removed. A researcher's personal Incus
     project with instances or volumes keeps Incus installed with its
     pool, network, packages, hold and repository. Tests: a fake dpkg whose
     scripts delete the data paths (`KeptDataTest`), the guard on real
     bind mounts in a private mount namespace, and the uninstall lane marks
     each kept directory before and checks the marks after.
  2. The backups' key and settings (`/etc/pinka/secrets/backup`,
     `/etc/pinka/backup`), the backup state, the restic binary and the
     vault password bootstrap stored are copied to `/var/lib/pinka-kept`
     (root only, also with `--purge-data`) before their paths go, with a
     README of the restic commands; the QA keys, attestations, the
     arbiter's, session, fingerprint, platform and verification records
     too without `--purge-data`. A path whose copy is missing is not
     removed. Plan, report, bootstrap and the installer name where the key
     is; the lane opens the backups with the kept key.
  3. Pinka writes three boot-time files (the nouveau blacklist,
     `60-pinka.conf` in `sysctl.d`, `pinka-wsl.conf` in `ld.so.conf.d`):
     uninstall removes them and rebuilds the initramfs, runs
     `sysctl --system` and `ldconfig`, sets the changed kernel settings back
     and reloads systemd; no role writes the boot loader's configuration,
     the kernel command line, modules-load files or presets (a test fails
     if one starts to). The report has `reboot_needed` and its reasons.
  4. The report's `follow_ups` name the Tailscale node to delete (when
     pinka joined it) and the deploy key to revoke with its fingerprint
     and repository; bootstrap and the installer print them.
  5. The pre-push hook (`.git/hooks/pre-push`, not tracked) refuses every
     URL but the new repository's; the rule is in `CLAUDE.md`. A session
     restart after this review reset `origin` once more; it was set back
     before anything was pushed.

  The owner's "commit" for both (verbatim; its order and checkpoint are
  under "Process rules in force"):

  > commit both, with two additions first:
  >
  > 1. Before the two commits: fast-forward moving/on to main
  >    (git merge --ff-only origin/main), so moving/on contains the merge commit and
  >    later merges can be fast-forwards again. Only a fast-forward; if it is not
  >    possible, stop and tell me.
  > 2. In the D2 commit: /var/lib/pinka-kept holds secrets that can still reach the
  >    backup server (restic password, SFTP key, vault password). The report,
  >    bootstrap and the installer put it at the top of the follow-ups: what is in
  >    it, that it grants access to the backups, and the commands to copy it off the
  >    host and then delete it securely. Doctor is not involved (pinka is gone), but
  >    the how-to uninstall page says the same.
  >
  > Then push each commit on its own, as proposed, and report both runs job by job.

  Done: `moving/on` fast-forwarded from `fb665c2` to `6ea7468`; the commit
  for one CI run per commit is `6bab38b`, pushed on its own. In the D2
  commit, the first follow-up names what `/var/lib/pinka-kept` holds that
  grants access to the backups (the copies marked `secret`: the
  repository's password and location with the SFTP key, the vault
  password), with the commands to stream it off the host (`tar | ssh`) and
  delete it (`shred -u`, then `rm -rf`), and says to change the repository's
  password and replace the SFTP key where `shred` cannot reach every old
  copy (an SSD, a copy-on-write file system); the follow-ups' heading is
  "Afterwards" now, since this one is on the host. The report, bootstrap's
  last lines, the installer's report, the README in `/var/lib/pinka-kept`
  and `docs/how-to/uninstall.md` ("Take the kept secrets off the host") say
  it; a later dry run says it again while the copy is there.

  CI of the two pushes, every job on its first attempt: `6bab38b`
  (infra-ci run 39930292097): changes, privacy, lint, test, docs, all six
  check-mode lanes and `required` passed, `links` skipped (weekly),
  `lock-check` the known red (24 marker errors); test-isolated, all six
  check-mode-extended lanes, release-package and lock-collections passed.
  `8696cb3` (infra-ci run 39933780672): the same jobs passed, `lock-check`
  the known red; test-isolated, all six check-mode-extended lanes and
  release-package passed (lock-collections did not run).
- **A and its follow-up** (owner, with the "commit" for A, verbatim):

  > commit
  >
  > Decisions:
  > 1 and 2 together: features must be switchable, otherwise small-scale use pays
  > for what it does not run. As an A follow-up, before B (B's capacity screen needs
  > them):
  > - inference and training become switches in host_vars and the answers file
  >   (detected features stay the default where they apply); a switched-off feature
  >   counts nothing in memory or disk, and its services, images and models are not
  >   installed;
  > - a training job counts in the recommended memory only when training is on and
  >   possible on that host (a usable GPU, or the CPU training path if it is
  >   chosen);
  > - the default training cap is computed from the host: what is left after the
  >   core stack, the targets and inference, within a floor and a ceiling you
  >   propose; an explicit training_caps.memory_max still wins. Show me what the
  >   default becomes on a nominal 16 GiB and a nominal 32 GiB host.
  > This touches §2.3 and the variables; I authorize the small spec edit it needs.
  > 3\. Artifacts retention: approved as you describe.
  > 4\. Smaller test VMs: approved; if a test shows an estimate is too low, the
  >    estimate changes, not the VMs.

  A is `00ff2c4`. Its CI, every job on its first attempt: infra-ci (run
  39987332225): changes, privacy, lint, test, docs, all six check-mode
  lanes and `required` passed, `links` skipped (weekly), `lock-check` the
  known red (24 marker errors; tag warnings for postgres, refplat_job,
  ubuntu_2404 and debian_13; no image size finding); test-isolated (run
  39987332292), all six check-mode-extended lanes (run 39987332194),
  release-package (run 39987332230) and lock-collections (run
  39987332353) passed. The follow-up (decisions 1 and 2) is built with its
  small spec edit, waiting for the owner's "commit"; its record, with the
  cap on nominal 16 and 32 GiB hosts, is in `docs/IMPLEMENTATION_NOTES.md`,
  "Inference and training switches, the computed training cap (A
  follow-up, 3f-1)". The proposed floor and ceiling of the computed cap are
  4 and 24 GiB.
- **The follow-up and four more commits** (owner, with the "commit" for the
  follow-up, verbatim):

  > commit
  >
  > Then these four, each as its own commit, pushed on its own, CI reported job by
  > job. Do not start B in this session.
  >
  > 1. Correction, my wording was wrong: the arbiter never runs vLLM and a training
  >    job at the same time, so they must not be added. Both the recommended memory
  >    and the computed cap use max(vLLM with its model, the training job), not the
  >    sum: cap = MemTotal minus the recommended memory of the core stack and the
  >    targets, within 4 and 24 GiB. Show the 16 and 32 GiB table again.
  > 2. The Incus pool's default size: 300 GiB counted in full for a loop file means a
  >    host with 144 GiB free cannot install with defaults. Compute the default like
  >    the cap: the golden images, the concurrent targets' root disks and a margin,
  >    limited by the free space on its mount point minus pinka's other needs, with a
  >    floor you propose; an explicit size still wins. Show what it becomes with
  >    144 GiB and 386 GiB free.
  > 3. The restore.yml bug (pinka_features missing on restore) is a defect in an
  >    existing feature, not side work: fix it as its own small commit, with a test
  >    that runs restore without site.yml's gate.
  > 4. Dependabot: the 16 alerts on main are:
  >    - npm, package-lock.json, all marked development and transitive: dompurify
  >      (#18, #19, #20; #20 critical), markdown-it (#15, #16, #17), smol-toml (#14),
  >      katex (#13);
  >    - pip, uv.lock: certifi (#22, critical, compromised emSign intermediates),
  >      virtualenv (#23, #24 high; #25, #26, #28 moderate), black (#27, high; the
  >      advisory is about the Black GitHub Action, which we do not use), pytest
  >      (#21, moderate).
  >    For each: which lock it is in, whether that lock is shipped (bootstrap, the
  >    installer, the playbooks' environment, the reference platform, release
  >    package) or development-only, and why GitHub reads "uv in /infra" when infra/
  >    no longer exists. Update every one to a fixed version, certifi first; for
  >    anything shipped, also check that bootstrap and the release package pick up
  >    the new version and that every pinned hash is updated. Its own commit, with
  >    the full gate. Do not enable Dependabot pull requests: their commits would
  >    not carry my identity. Instead, add to the maintenance checklist that the
  >    Dependabot alerts are reviewed and the locks updated at each maintenance
  >    update.
  >
  > When all four are pushed and their CI is green, prepare a handover for a new
  > session, which will start B from the repository alone:
  > - PROGRESS.md states exactly where 3f-1 stands, the next step (B) and the order
  >   after it (B, C, E, F, G, then the feature-complete checkpoint I asked for),
  >   every open item and open decision, and every rule I gave in this session that
  >   is not yet in CLAUDE.md;
  > - CLAUDE.md has every standing rule: the commit rule, one push per commit, the
  >   push guard and how to restore origin after a restart, the CI skip list, local
  >   random-order and per-file runs before every proposal, the re-run rule,
  >   unsigned commits under my noreply identity, no attribution lines, merges by
  >   fast-forward or merge commit only, no side work during 3f-1;
  > - list for me anything this session knows that is not in the repository
  >   (for example local/test-computers.md and local/rewrite-mapping.md, which are
  >   git-ignored), so I can keep copies;
  > - tell me whether the stop hook that asks you to commit can be turned off in
  >   this environment, and how; it costs a turn every time.
  > Then stop and wait.

  The follow-up is `a9bb439`, pushed on its own. Its CI, every job on its
  first attempt: infra-ci (run 40028169144): changes, privacy, lint, test,
  docs, all six check-mode lanes and `required` passed, `links` skipped
  (weekly), `lock-check` the known red (24 marker errors; tag warnings for
  postgres, refplat_job, ubuntu_2404 and debian_13); test-isolated (run
  40028169081) and all six check-mode-extended lanes (run 40028169140)
  passed; release-package and lock-collections did not run (their inputs
  are unchanged). Item 1: the cap no longer
  subtracts vLLM; the recommended value already counted the larger of vLLM
  and the job. The "Training cap" bullet of §2.3 follows the corrected
  decision (spec checksum above); the table is in
  `docs/IMPLEMENTATION_NOTES.md`, "Correction: vLLM and a job never add
  up".

  Item 1 is `1a8b18b`, pushed on its own. Its CI, every job on its first
  attempt: infra-ci (run 40031287598): changes, privacy, lint, test, docs,
  all six check-mode lanes and `required` passed, `links` skipped (weekly),
  `lock-check` the known red (24 marker errors; tag warnings for postgres,
  refplat_job, ubuntu_2404 and debian_13); test-isolated (run 40031287504)
  and all six check-mode-extended lanes (run 40031287465) passed;
  release-package and lock-collections did not run (their inputs are
  unchanged). Item 2: `incus_pool_size: auto` is the default; preflight
  computes the loop pool from the golden images, the concurrent targets'
  root disks (or the build instance's, when larger) and a 50 % margin,
  limited by the free space on its mount point minus pinka's other needs
  there, never below the proposed floor of 30 GiB (the test VMs' pool); an
  explicit size wins (approved deviation PL1). With defaults on one file
  system it is 66 GiB with VM targets and 42 GiB with containers on both
  144 and 386 GiB free; the table is in `docs/IMPLEMENTATION_NOTES.md`,
  "The computed Incus pool size".

  Item 2 is `5fb3fe8`, pushed on its own. Its CI, every job on its first
  attempt: infra-ci (run 40035463027): changes, privacy, lint, test, docs,
  all six check-mode lanes and `required` passed, `links` skipped (weekly),
  `lock-check` the known red (24 marker errors; tag warnings for postgres,
  refplat_job, ubuntu_2404 and debian_13); test-isolated (run 40035463074)
  and all six check-mode-extended lanes (run 40035462953) passed;
  release-package and lock-collections did not run (their inputs are
  unchanged). Item 3: `restore.yml` sets the
  feature flags (after the switches), `pinka_training` and
  `pinka_training_caps` from the profile bootstrap passes, through the
  gate's own `facts.yml`; a test runs `restore.yml` without the gate (a
  real check-mode run of its always-tagged tasks). Record:
  `docs/IMPLEMENTATION_NOTES.md`, "restore.yml without the gate".

  Item 3 is `d9a98b0`, pushed on its own. Its CI, every job on its first
  attempt: infra-ci (run 40081668579): changes, privacy, lint, test, docs,
  all six check-mode lanes and `required` passed, `links` skipped (weekly),
  `lock-check` the known red (24 marker errors; the same four tag
  warnings); test-isolated (run 40081668541) and all six
  check-mode-extended lanes (run 40081668552) passed. Item 4: every alerted package is
  at a fixed version, certifi first (2026.10.23); none of the 16 alerts is
  in a lock a host installs from (`uv.lock` and `package-lock.json` are
  development only). pytest 9.1.1 needs a uv override of syrupy 4.8.0's
  `pytest<9`; markdown-it, smol-toml and katex need npm `overrides`. Two
  findings beyond the alerts, from the new checklist's checks: filelock in
  the shipped models lock (and `uv.lock`), 4.2.4, and zod (`npm audit`),
  4.6.7. Dependabot pull requests stay off (owner); the alerts are
  reviewed and the locks updated at each maintenance update
  (`docs/how-to/maintenance-update.md`). Record,
  with the alert-by-alert table and why GitHub names `/infra`:
  `docs/IMPLEMENTATION_NOTES.md`, "Dependabot alerts and the locks".

  Item 4 is `ac157ad`, pushed on its own. Its CI, every job on its first
  attempt: infra-ci (run 40083928647): changes, privacy, lint, test (on
  pytest 9.1.1), docs, all six check-mode lanes and `required` passed,
  `links` skipped (weekly), `lock-check` the known red (24 marker errors;
  the same four tag warnings); test-isolated (run 40083928622) and all six
  check-mode-extended lanes (run 40083928664) passed; release-package and
  lock-collections did not run (their inputs are unchanged). The release
  package built locally from `ac157ad` (`make release-package REF=HEAD`)
  carries filelock 4.2.4 with both PyPI hashes in the models lock, and
  `make release-package-check` reports 0 problems. With that, the four
  items are pushed and green; the handover is the next commit.
- **Real-machine tests** (owner, after 3f, verbatim; after 3f-1 and before
  the rewrite). The descriptions of the two computers are generalized to
  machine classes, in brackets (owner, decision 2 after the public-readiness
  audit); the exact details stay in the git-ignored local file
  `local/test-computers.md` (QA specification §2):

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

  End-to-end verification (owner, with the "commit" for the per-file run,
  verbatim above): the firewall, privilege profiles, isolation and the
  install record are verified end to end only by `make test-distros` and
  real-machine tests 4.4, 4.7, 4.8 and 4.14; 3f-1 is not considered verified
  until those have passed. CI checks their parts without a host: the
  rendered nft rulesets with `nft -c`, the guards in a network namespace,
  the isolation probe's expectations per profile, the install record module
  on Python 3.12 and 3.9, and every role in check mode.
- **3g** History rewrite and the renamed repository (owner, after 3f,
  replacing the fresh repository of the decision with the "commit" for
  3d.2; see "Process rules in force"; done, but published as a fresh
  repository after all, by the owner's decision after the rewrite, below
  under the owner's new plan): after the real-machine tests, the
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
| SSH1 | infra §7.3 | the drop-in sets `AllowGroups pinka`, `PasswordAuthentication no` and the other settings for the whole host | pinka's sshd settings apply in a `Match Group pinka` block, so accounts outside `pinka` keep the host's own policy (owner, decision 3 after D1, approving option (b) of the open question raised with the 3f-1 spec edit); on a shared host, and on a dedicated host while the install's access path is open for an account that is not a researcher; built with the D1 follow-up |
| DL1 | infra §7.5 (doctor bullet) | doctor fails on anything listening on `0.0.0.0`/`::` other than sshd | `network.listeners` accepts a listener the install record notes as the host's before pinka, on these conditions (owner, decision 1 with the "commit" for `16bf749`): it matches the record exactly (address, port, protocol and owning unit or executable), and any listener that appears after the install still fails; the install record is writable only by root, and doctor fails if its owner or mode is wrong (`install.record`); pinka's own services never use the exception; on a dedicated host the strict rule of §7.5 applies unchanged |
| AG1 | infra §7.5 (pre-existing access) | firewalld or ufw: pinka leaves the owner's zones and rules as they are; closing unspecified | what the owner consents to close, and the install's access path once closed, are dropped by pinka's own table `inet pinka_access_guard` on every backend, loaded by `pinka-nftables.service` (owner, decision 2 with the "commit" for `16bf749`); tests confirm it drops only new connections and never one on loopback, spares only SSH on `tailscale0` (corrected with the owner's new plan after `16bf749`: what was closed is closed over the tailnet as well, on every backend; SSH over the tailnet is never cut), and depends on no table of ufw or firewalld; the ufw and firewalld lanes of `make test-distros` reload the owner's firewall and check the guard, the closed and the kept listener from the harness host and over the tailnet, and SSH over the tailnet |
| PL1 | infra §8.1 (`incus_pool_size: 300GiB # for loop`), §2.3 (disk) | a loop pool's size from host_vars, 300 GiB in the example | `incus_pool_size: auto` is the default: preflight computes it from the golden images, the concurrent targets' root disks (or the build instance's) and a 50 % margin, limited by the free space on the loop file's mount point minus pinka's other needs there, never below 30 GiB; an explicit size wins; an existing pool keeps its size (owner, item 2 after `a9bb439`: "Compute the default like the cap"). For the next spec edit |

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

Raised by the pre-existing access follow-up, for the owner (decided with the "commit" for `16bf749`: approved deviations DL1, with conditions, and AG1):

- **Doctor's listener check on a shared host.** §7.5's doctor bullet fails
  on anything listening on `0.0.0.0` or `::` other than sshd, which every
  shared host does by definition, and which pinka may not stop.
  Interpretation built: `network.listeners` accepts a listener the install
  record notes as the host's before pinka (same protocol and port, and the
  same address unless it was every address), names it `kept` or `closed by
  the firewall`, and still fails on any other. Recommendation: record it as
  an approved deviation.
- **Closing with a table of pinka's own on every backend.** §7.5 leaves the
  owner's firewalld zones and ufw rules as they are; what the owner
  consents to close is dropped by `inet pinka_access_guard`, loaded by
  `pinka-nftables.service` on every backend (the unit then also exists on
  firewalld and ufw hosts), instead of ufw deny or firewalld reject rules,
  which would have blocked SSH over the tailnet for `tcp/22` on ufw, could
  not reopen, and would have changed the owner's rule lists. A new unit
  name was avoided: it would change `contract/reserved-names.yml`, which
  needs a contract version bump. Recommendation: keep it.

Raised by A of 3f-1 (requirements computed from the choices), for the owner;
recorded as open items while B, C, E, F and G follow (details in
`docs/IMPLEMENTATION_NOTES.md`, "Requirements computed from the choices (A,
3f-1)"). Decided with the "commit" for A (verbatim under "A and its
follow-up" in the remaining plan): the first two together, as switches and a
computed training cap (the A follow-up); "artifacts" retention approved as
described; the smaller test VMs approved ("if a test shows an estimate is
too low, the estimate changes, not the VMs"). The records as they were
raised:

- **The training cap sets the recommended memory.** A training job counts
  at `training_caps.memory_max` in the recommended value (not in required),
  the larger of it and vLLM where vLLM serves. With the defaults (cap 24G)
  a full node recommends 37.2 GiB, so a 16 GiB or 32 GiB machine installs
  `degraded` (with consent) unless the cap is lowered; the message names
  the cap that would fit. Alternatives: count the job only with
  `gpu_training`, or lower the default cap (§7.18, §8.1 name 24G).
- **Features are not part of the plan.** §2.3 lists "features" among the
  plan's choices, but no setting turns a detected feature off today, so the
  plan carries none; adding one would be new behavior.
- **"Artifacts" retention.** §2.3 lists artifacts among the retention
  settings. Infra keeps no artifact it could prune: training runs and
  datasets are research data (shown per run, up to `run_storage_max`), the
  platform's artifacts follow the platform's own settings. The local dumps
  (`backup_pgdump_keep`) are the backups' retention on this host.
- **The test VMs shrink to the computed requirement.** The CI answers
  require 8 GiB and 37.2 GiB of disk; the VMs get 10 GiB and 80 GiB (the
  kernel's and the system's margins as before), down from 18 GiB and
  300 GiB. If a run shows the estimates too low, the estimates change, not
  the VMs.

Raised by A's follow-up (the switches and the computed training cap), for
the owner; built as described, open for the owner's confirmation (details
in `docs/IMPLEMENTATION_NOTES.md`, "Inference and training switches, the
computed training cap (A follow-up, 3f-1)"):

- **The cap's floor and ceiling: 4 and 24 GiB.** The floor is the least a
  job needs besides its model (and what the reference platform's job asks
  for); the ceiling is the former fixed default. On a nominal 16 GiB host
  with a usable GPU and VM targets the floor still counts: it recommends
  17.2 GiB and installs `degraded` (fits: one target, a cap of 2-3G, or
  `training: "off"`); with containers only it fits. On a nominal 32 GiB host
  the cap is 16-18G, with or without a model: vLLM and a job never run at
  once, so the cap leaves vLLM out and the recommended value takes the
  larger of the two (owner's correction after `a9bb439`).
- **A host is not degraded for a feature its switch turns off.** Built into
  §2.3 with the spec edit: `inference: "off"` or `training: "off"`/`cpu`
  turns the missing GPU feature into a note, so a small lab without a GPU
  can install `supported` without consent.
- **`gpu_training` joined the support matrix** as §2.3 defines it; the
  repository's matrix lacked it although the docker role, doctor and O5
  read it. Hosts without a usable GPU now list two disabled features.
- **The arbiter stays with both switches off** on full and inference nodes:
  socket-activated, it serves nothing, refuses every run and answers
  `status` (`training_available: false`) to the platform's controller,
  rather than leaving it a missing socket.

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
- **The test tailnet's policy for the uninstall lane's variant** (D2): the
  target `ubuntu-24.04-preexisting` logs in its own Tailscale node before
  pinka with the tags `tag:pinka-qa` and `tag:pinka-qa-owner`, so the
  policy must let `tag:pinka-qa` own `tag:pinka-qa-owner`
  (`docs/how-to/test-distributions.md`, prerequisites).
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
pins of `make lock-pins` after 3f, 98 with D1 of 3f-1, 103 with D2; details in
`docs/IMPLEMENTATION_NOTES.md`, "Open `TODO(verify)` items after step 4"
through "after step 13", and "Pins of the vendor sources (make lock-pins)"):

- Ubuntu 26.04: Incus, NVIDIA Container Toolkit and Tailscale support
  (`preflight/support-matrix.yml`).
- Uninstall (D2): the output of each family's test removal and
  unneeded-package listing (`ansible/roles/uninstall/vars/*.yml`, four),
  and how rpm 6 names a v6 key (`ansible/roles/pkg/tasks/repo_rpm.yml`).
  And the maintainer scripts of the distributions' own packages that pinka
  may install and uninstall removes, which could not be read from this
  environment (their mirrors are blocked): Debian's (`nvidia-driver` and
  the base packages), Fedora's, EL's (with EPEL) and openSUSE's (Docker,
  Incus, the base packages; RPM Fusion's `akmod-nvidia`) and Arch's
  (Docker, Incus, Tailscale, `nvidia-container-toolkit`, `nvidia-open`):
  whether a removal deletes kept data (the guard keeps it read-only
  meanwhile) or masks a unit (`masked_on_remove`; on Debian also whether
  a purge deletes data, `purge_deletes`). `make test-distros` checks the
  kept data on every family.
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

- **`restore.yml` without the feature flags** (found while building A's
  follow-up; open item, not fixed under the rule against side work).
  `restore.yml` reads `pinka_features`, which only `preflight_gate` of
  `site.yml` sets; restore runs as its own `ansible-playbook` after
  provisioning, so `pinka_features` is undefined there and the model
  re-verification and the re-render of the restored vLLM deployment never
  run. Fix: derive the flags (after the switches) from the host profile
  bootstrap passes, as `preflight_gate` does. **Fixed** (the owner's item 3
  after `a9bb439`, "a defect in an existing feature, not side work", its
  own commit after `5fb3fe8`): `preflight_gate/tasks/facts.yml` sets the
  flags and the training settings for the gate and for `restore.yml`;
  `ansible/tests/test_restore_run.py` runs `restore.yml` on its own.

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

B, the capacity plan screen ("Handover" at the top of this file: where
3f-1 stands, the order after B, the open items). The previous text of
this section is in the
[archive](PROGRESS-ARCHIVE.md#next-step-before-the-handover-after-3f-1-a).
