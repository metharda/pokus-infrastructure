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
