# CLAUDE.md

Read PROGRESS.md first, then the BUILD_PROMPT.md and CLAUDE.md of the
component you are working in.

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

## One-time history rewrite (owner, after the 3d.1 commit)

Verbatim:

> "History rewrite: NOT now. After 3f is committed and before 3g, propose a
> one-time rewrite of claude/zen-albattani-qfwwps that removes the two
> Co-Authored-By trailers and nothing else (git filter-repo with a message
> callback), updates every commit hash referenced in PROGRESS.md and the
> docs, removes the two known exceptions from the check, and shows me the
> before/after mapping. It needs my explicit "rewrite" before you
> force-push; it is the only exception to the no-history-rewrite rule and
> it never covers main."
