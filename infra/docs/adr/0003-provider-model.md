---
title: "ADR-0003: One provider role per host environment"
type: explanation
audience: [developer, reader]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
---

# ADR-0003: One provider role per host environment

- **Status:** accepted
- **Date:** 2026-10-05
- **Deciders:** pokus-infra maintainers (BUILD_PROMPT §3)

## Context

A lab host can be a physical machine, a virtual machine or a WSL2
distribution on a Windows workstation. These differ in ways that matter for
provisioning: who installs the GPU driver (the host on bare metal, Windows on
WSL2, the guest only with passthrough in a VM), whether the host may reboot by
itself, how sleep is controlled, and which guest tools are needed. If every
role checked the environment for itself, the checks would drift apart and a
role could reach a different conclusion than preflight did.

## Decision

1. Everything that depends on the environment lives in exactly one
   **provider role**: `provider_baremetal`, `provider_vm` or
   `provider_wsl2`. The play applies the one that matches the provider in the
   host profile.
2. Providers are chosen from the host profile written by preflight, never by
   detecting again. `lab_provider` in host_vars may only confirm the detected
   provider (`auto` or the same value); a mismatch stops the play.
3. All other roles are provider-agnostic. They read feature flags from the
   host profile (`gpu_inference`, `vm_targets`, ...) and the facts of
   `platform_facts`.
4. A provider never changes anything outside the Linux host: nothing on the
   Windows side of WSL2, nothing on the hypervisor of a VM. Where an outside
   setting matters (`.wslconfig`, GPU passthrough), the provider renders a
   recommendation or the documentation explains it, and doctor checks the
   effect.
5. `provider_vm` reuses the bare-metal tasks for the NVIDIA driver (only with
   passthrough), the power policy and reboot handling instead of copying them.

## Consequences

- Adding an environment means adding one role and a support-matrix entry; the
  other roles do not change.
- Conditions only a provider can detect (a changed `wsl.conf`, a pending
  reboot or MOK enrollment) end the play with bootstrap signals 75, 76 and 77,
  so the operator gets one consistent way of resuming.
- Measurements on VMs and WSL2 include virtualization overhead; the provider
  and hypervisor are part of the environment fingerprint, and reports keep
  results from different providers apart.

## Alternatives considered

- **Conditionals in every role** (`when: provider == 'wsl2'`): spreads one
  concern over many files and invites inconsistent checks.
- **Separate playbooks per environment**: duplicates the shared roles and
  their ordering, and makes moving a lab between environments harder.
