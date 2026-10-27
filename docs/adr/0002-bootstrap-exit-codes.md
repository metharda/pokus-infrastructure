---
title: "ADR-0002: Bootstrap exit codes and error handling"
type: explanation
audience: [operator, developer]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
---

# ADR-0002: Bootstrap exit codes and error handling

- **Status:** accepted
- **Date:** 2026-10-05
- **Deciders:** pokus-infrastructure maintainers

## Context

Bootstrap's codes are fixed for the preflight outcomes (0, 10, 20, 30, 40),
for conditions signalled by the play (75, 76, 77), and for the end of a run,
which exits with the exit code of `pinka doctor` (0, 1, 2). That leaves
open how bootstrap ends when the
operator declines the experimental tier, when installing the pinned tooling
fails, when the play fails without a signal, when the command line is
invalid, and when bootstrap itself fails unexpectedly. Reusing an existing
code for these would make the code ambiguous: a download failure reported as
`needs_action` sends the operator looking for a remediation that was never
printed.

## Decision

1. Bootstrap adds five codes, all outside the ranges already assigned:

    | Code | Meaning |
    | --- | --- |
    | 11 | experimental tier without consent (next to 10, the other consent case) |
    | 50 | installing uv, CPython, the Ansible environment or the collections failed, or a checksum did not match |
    | 60 | the play failed without a bootstrap signal |
    | 64 | invalid command line (the conventional `EX_USAGE`) |
    | 70 | unexpected internal error (the conventional `EX_SOFTWARE`) |

2. `bootstrap/lib/exitcodes.sh` is the single definition: one constant per
   line with its title and meaning. The exit code reference page is generated
   from it, and the documentation coverage check requires every code there.
3. Stages end deliberately with `fail CODE MESSAGE` or by returning a
   documented code; an error trap passes documented codes through and turns
   every other failure into 70, naming the command and its `file:line`.
4. Signals from the play use the file `/run/pinka/bootstrap-signal.json`
   (`{code, message}`); only 75, 76 and 77 are accepted from it.

## Consequences

- Every exit code has exactly one meaning, and
  [How to troubleshoot a bootstrap run](../how-to/troubleshoot.md) has one
  section per code.
- The installer TUI and scripts can tell an operator decision (10, 11, 20, 30,
  75, 76, 77) from a failure (40, 50, 60, 70) by the code alone.
- Codes 1 and 2 keep the meaning doctor gives them; a doctor that ends
  unexpectedly is reported as 2.

## Alternatives considered

- **Map every failure to 77 with a message:** rejected; 77 means that the
  operator can act on the printed message, which is not true for a failed
  download or a bug.
- **Pass the exit status of the failing tool through:** rejected; the codes
  of curl, uv and ansible-playbook overlap with the documented codes (curl
  reports a CA certificate problem as 77, for example).
