---
title: Operating the lab
type: explanation
audience: [operator]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: The operator's map of the lab's day-to-day work, with a link to the how-to guide for each task.
---

# Operating the lab

This page is the operator's map: what you do with a running lab, in roughly
the order you need it, and where each task is described step by step. Every
command runs on the lab host; `pinka` is installed there by bootstrap,
and most of its commands need `sudo`.

## Every day

| Task | Command | Guide |
| --- | --- | --- |
| See what runs | `sudo pinka status` | [`pinka` reference](reference/cli/pinka.md#pinka-status) |
| Check the lab's health | `sudo pinka doctor` | [Fix a failed doctor check](how-to/fix-doctor-checks.md) |
| Start the lab | `sudo pinka start` | [Start, stop and drain the lab](how-to/start-stop-drain.md) |
| Stop it without losing runs | `sudo pinka stop --timeout <seconds>` | [Start, stop and drain the lab](how-to/start-stop-drain.md) |
| Serve the documentation | `pinka docs serve` | [How to connect to the lab](how-to/connect.md#read-the-documentation-on-the-lab) |
| Install or roll back the served documentation | `sudo pinka docs install <tarball> --sha256 <sum>`, `sudo pinka docs rollback` | [How to serve a documentation release on the lab](how-to/install-docs-release.md) |
| Lend production's hardware to staging | `sudo pinka window open --start <time> --end <time>`, `sudo pinka window close` | [How to run a QA maintenance window on staging](how-to/staging-window.md#staging-on-productions-hardware) |

## People

- Add, remove or lock a researcher, or replace their SSH keys:
  [How to add, remove or lock a researcher](how-to/manage-researchers.md).
- Help researchers connect over Tailscale and SSH, to Grafana, the ad-hoc LLM
  endpoint and the documentation: [How to connect to the lab](how-to/connect.md).

## Models and images

- Pin a model, create a deployment and make it the served one:
  [How to add a model and activate a deployment](how-to/add-a-model.md).
  The GPU arbiter switches the served deployment
  (`sudo pinka gpu serve <id>@<version>`); the platform's next request
  replaces that choice.
- Train an adapter and serve it, or stop a run:
  [How to run a training job and serve its adapter](how-to/run-training-job.md)
  and [The GPU arbiter](explanation/gpu-arbiter.md).
- Build, test and export the golden images of the benchmark targets:
  [How to build, test and export golden images](how-to/build-golden-images.md).

## Keeping the host current and safe

- Update the operating system with the lab's holds kept:
  [How to update the host](how-to/update-host.md).
- Update pinned versions in `versions.lock.yml` and verify them:
  [How to update pinned versions](how-to/update-versions.md).
- Rotate the vault's secrets and the hosts' certificates (`rotate.yml`):
  [How to rotate secrets and certificates](how-to/rotate-secrets.md).
- Keep the firewall backend and SELinux working together:
  [How to configure the firewall backend and SELinux](how-to/firewall-and-selinux.md).

## Data

- Backups run on a timer; back up now, restore, and rehearse a restore:
  [How to back up, restore and run a restore drill](how-to/backup-and-restore.md).
- Move the lab to a new machine (fresh build, restore, tailnet node
  replacement, signing key handover):
  [How to migrate the lab to a new machine](how-to/migrate-lab.md).

## Platform, QA and split inference

- Deploy or update the platform and its content packages:
  [How to plug a platform into the lab](how-to/deploy-platform.md); replace it with another
  platform and purge the old one:
  [How to replace or purge a platform](how-to/replace-platform.md).
- Run a maintenance window on a staging lab:
  [How to run a QA maintenance window on staging](how-to/staging-window.md).
- Install the QA team's trusted keys and attestations:
  [How to install QA trusted keys and attestations](how-to/install-attestation.md).
- Serve inference from a separate GPU machine:
  [How to run inference on a separate GPU node](how-to/pair-inference-node.md).

## When something fails

- Bootstrap stopped: every exit code has a section in
  [How to troubleshoot a bootstrap run](how-to/troubleshoot.md).
- Doctor reports a check: every check ID has a section in
  [How to fix a failed doctor check](how-to/fix-doctor-checks.md).
- Logs: the journal of the lab's units (`journalctl -u pinka-lab.target`,
  `journalctl -u 'pinka-*' -u '<platform>-*'`, where `<platform>` is the
  active platform's name), bootstrap's logs under
  `/var/log/pinka-bootstrap/`, and the run's own telemetry in Grafana.

The security model behind all of this is in [Security of the lab host](security.md).
