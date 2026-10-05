# preflight_gate

First role after `platform_facts` (BUILD_PROMPT §2.6, §7.1). It runs
preflight again, read-only (`--no-write`), and compares outcome, provider and
feature flags with the host profile that bootstrap validated for this run. If
anything differs and bootstrap was not run with `--refresh-profile`, the play
stops with bootstrap signal 77 and lists the differences: a host whose GPU or
KVM disappeared is never provisioned as if nothing happened.

It also refuses an outcome that the recorded consent does not cover (for
example `degraded` without `accept_degraded`), so the play cannot be run
around bootstrap's gate.

## Facts it sets

| Fact | Content |
| --- | --- |
| `pokus_host_profile` | the fresh host profile |
| `pokus_features` | its feature flags (`gpu_inference`, `vm_targets`, `btrfs_pool`, `lvm_thin_pool`, `dcgm_metrics`) |

## Variables

See `meta/argument_specs.yml`.

## Idempotency

Read-only: preflight runs with `--no-write` even outside check mode.
