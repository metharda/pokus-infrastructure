---
title: Reproducibility, fingerprints and sessions
type: explanation
audience: [operator, researcher]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: What the environment fingerprint records, how sessions tie runs to it, and which results may be compared with each other.
---

# Reproducibility, fingerprints and sessions

A benchmark result is only meaningful together with the environment it was
measured in. The lab host makes that environment explicit in three ways:
everything it installs is pinned, every boot of the lab is a session with a
recorded environment fingerprint, and changes to the host go through tools
that show what changed.

## Pinning

Every external artifact (packages, container images, binaries, collections,
golden image bases) is pinned in `versions.lock.yml` with a digest or checksum
and verified before use; see [Pinned versions](reference/versions.md). The
pinned packages are held, so automatic updates leave them alone, and kernels
are held on bare metal unless allowed. Rolling releases get no automatic
updates at all; [`pokus-lab update-host`](how-to/update-host.md) is the only
supported way to update the host.

## The environment fingerprint

`pokus-lab fingerprint` describes everything on the host that can change a
measurement:

- **host**: provider, environment class, hypervisor, operating system and
  version, kernel release, architecture, support tier, release model and the
  operator's overrides (such as `--accept-degraded`) from the host profile;
- **hardware**: CPU model and effective threads, memory, GPU name, count,
  memory, driver version, compute capability and GPU path (`native`,
  `passthrough`, or `wsl-dxg` on WSL);
- **software**: the versions the installed programs report (Docker,
  containerd, NVIDIA Container Toolkit, Incus, Tailscale, the pinned Python,
  ansible-core), the image every lab container runs (with its digest) and the
  Incus fingerprint of every golden image alias;
- **configuration**: the repository commit and `infra/` tree bootstrap ran
  from and whether it had local changes, the sha256 of `versions.lock.yml`
  and of the host_vars file, and the deployed platform and catalog releases;
- **WSL**: the effective `wsl.conf` and the kernel string. The Windows build
  is recorded as `null`: with interop off, Linux cannot read it.

The fields are written as canonical JSON (sorted keys, no whitespace) and
hashed with sha256; the result, `sha256:<hex>`, is the fingerprint hash. The
time of the computation and the boot ID are recorded next to it but are not
part of the hash, so the same environment always has the same hash. Every
field is described in [Fingerprint schema](reference/fingerprint.md).

Only root computes the fingerprint, because reading some of these facts needs
privileges; the result is cached in `/var/lib/pokus/fingerprint/current.json`.
Any other user, including the orchestrator, gets that cached, root-computed
fingerprint and never escalates (contracts.md, resolution C9c).

## Sessions

Each boot of the lab is a session. When `pokus-lab.target` starts, after the
health gate, `pokus-session-start.service` computes the fingerprint and writes
`/var/lib/pokus/sessions/<boot_id>.json` with the fingerprint, its hash and a
fresh preflight outcome (see [How the lab comes up](explanation/lab-session.md)).
The platform imports these records and attaches every run to the session it
ran in, so every run carries the fingerprint hash of its environment. On a
separate inference node, that node's fingerprint is recorded too.

Between two boots the environment should not change. On rolling releases,
where a package update is more likely, doctor compares the running system
with the session's fingerprint (`lab.rolling_drift`).

## Which results may be compared

- **Latency and resource use** (time to diagnose, tokens per second, CPU and
  memory of targets) depend on the whole environment. Compare them only
  between runs with the same fingerprint hash. No subset of fields is
  documented as harmless yet; until one is, a different hash means not
  comparable. `pokus-lab update-host` prints which fields changed.
- **Accuracy and the other outcome metrics** depend on what the agent did,
  not on how fast. Comparing them requires the identical model (weights hash),
  serving profile and agent configuration; the fingerprint may differ.
- Results from different inference placements (local vLLM and a separate
  inference node) are never compared for latency.

The platform's reports apply these rules; this page explains where the
facts behind them come from.
