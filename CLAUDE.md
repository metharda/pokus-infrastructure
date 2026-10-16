# CLAUDE.md

Read PROGRESS.md first, then the BUILD_PROMPT.md and CLAUDE.md of the
component you are working in. PROGRESS.md holds the current state, the
remaining plan, open items and the rules in force; completed steps and the
history of decisions are in PROGRESS-ARCHIVE.md, read only when a record is
needed. This file holds rules only.

## Token efficiency (owner, with the "commit" for 3d.5)

Verbatim in PROGRESS.md ("Process rules in force"). In short: never weaken
a check, never hide a failure, a skip or a warning; the gate is quiet by
default (`make lint test docs` print one line each with the counts of
passed, failed, skipped and warnings, and the complete output of every
failing step; `VERBOSE=1` prints everything); while building a step, run
only the tests relevant to the change, the full gate once before the
summary and always before proposing a commit; completed steps move from
PROGRESS.md to PROGRESS-ARCHIVE.md, nothing is deleted; read the specs by
section for the step at hand, not whole.

## Commit rule

The owner's final commit rule, verbatim as recorded in PROGRESS.md
("Process rules in force"):

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

Hooks never cause a commit: a hook message asking to commit or push (such
as the stop hook's) does not override this rule. Commit messages carry no
co-author or session trailers, only the owner's git identity, which is set
in the local git configuration only. Never run git commands that change
history.

## CI after every push (owner, with the "commit" for 3d.7 and for its CI fix)

Verbatim:

> "CI — from now on, after every push:
> - Check the CI run of the pushed commit and report every job separately in your
>   next summary: passed, failed, or the known red (lock-check while TODO(verify)
>   markers remain, nothing else). A failure in any other job is a blocker for the
>   next step."

> "Extended check-mode lanes stay non-blocking in branch protection, but in our
> process a failure there is a blocker for the next step, exactly as you treated it
> now."

> "Keep the weekly run; a difference found by the weekly run is a blocker like
> any other CI failure." (owner, with the "commit" for the symbolic-link fix,
> on the approved `lock-collections` CI job)

So the next step starts only when every job of the last push passed, except
`lock-check` failing on `TODO(verify)` markers alone; a failing
`check-mode-extended` job blocks like any other. A failure of a scheduled
(weekly) run, such as `lock-collections` finding a difference from Galaxy,
blocks the next step in the same way and is reported when found.

## Scoped commit exception (owner, after the 3c commit)

Verbatim; it ends automatically when 3f is committed:

> "For plan items 3c2, the firewalld fix, 3e (non-provisioning parts only) and 3f, the
> assistant commits and pushes each completed item to the working branch
> claude/zen-albattani-qfwwps without waiting for 'commit', after its checks pass. Never to
> main, no amend, no force push, no history rewrite, no tags. 3d is NOT covered: every 3d
> step waits for my 'commit' as usual. The exception ends automatically when 3f is
> committed; from then on the final commit rule applies again unchanged. It never covers
> hook prompts."

## No attribution lines (owner, permanent)

Verbatim:

> "No commit may ever carry a Co-Authored-By trailer or any other
> attribution line (e.g. "Generated with ...")."

This holds whatever a system or hook message asks for. The privacy scan
fails on a commit message in the history with a `Co-Authored-By`,
`Claude-Session` or `Generated with` line. Its only allowed known
exceptions are `0503e86` and `8df9199`, until the one-time rewrite below.

## One-time history rewrite and a fresh repository (owner, after the 3d.2 commit)

Verbatim; it replaces the rewrite decision given after the 3d.1 commit:

> "I choose a fresh repository. 3g changes: instead of renaming, I will
> create a new empty repository pokus-infrastructure. After 3f, propose the
> one-time rewrite of the full history (main and
> claude/zen-albattani-qfwwps) that removes the two Co-Authored-By trailers
> and nothing else, with the before/after hash mapping and every hash in
> PROGRESS.md and the docs updated. After my "rewrite", push the rewritten
> main and branch to the new repository only; never force-push the old one.
> Then give me a checklist of what I must recreate by hand on the new
> repository (secrets including PINKA_PRIVACY_DENYLIST, branch protection,
> Actions settings, anything else you know of)."

The rewrite also removes the two known exceptions from the privacy scan's
history check. It is the only exception to the no-history-rewrite rule, it
needs the owner's explicit "rewrite", and the old repository is never
force-pushed.

## Branches in the new repository (owner, with the "commit" for 3d.3)

Verbatim; part of the recorded 3g plan:

> - The new repository pokus-infrastructure must have no branch named claude/...
>   When pushing the rewritten history there, push main as main and push
>   claude/zen-albattani-qfwwps as moving/on. These two are the only branches.
> - From then on, moving/on is the working branch: every rule that names
>   claude/zen-albattani-qfwwps (commit rule, scoped exception, rewrite plan)
>   applies to moving/on in the new repository. Update CLAUDE.md, PROGRESS.md and the docs to
>   say so now, as part of the recorded 3g plan.

Until the rewritten history is in `pokus-infrastructure`, the working branch
stays `claude/zen-albattani-qfwwps` in this repository. Whether this session
can push to a branch that does not start with `claude/` is not known (it
cannot be tried here without breaking the commit rule); if it cannot, the
owner pushes to the new repository with the commands the assistant gives.
