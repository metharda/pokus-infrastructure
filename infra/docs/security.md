---
title: Security of the lab host
type: explanation
audience: [operator, developer]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: What the lab host defends against, which mechanism handles each threat, the tailnet policy it needs, and what is out of scope.
---

# Security of the lab host

A Pokus lab runs software whose job is to break things: an AI agent with
tools, and fault injection that damages benchmark targets on purpose. The
lab host is built so that this damage stays inside the run it belongs to.
This page lists the threats the infrastructure defends against, the mechanism
that handles each, and what it does not try to cover. The trust zones are
described in [Lab host architecture](architecture.md#trust-zones); the
platform documents the agent boundary and the policy decisions.

## Threats and mitigations

### A misbehaving agent

The agent under test may try anything its tools allow, including actions
outside its task.

| Risk | Mechanism |
| --- | --- |
| The agent acts on the host instead of a target | The agent runs as `potka-agent`, which can reach only the LLM gateway and the tool executor sockets; it is never in `docker`, `incus-admin` or `pinka-gt` and cannot read secrets, the catalog, artifacts or the database ([Accounts and groups](reference/accounts.md)). |
| The agent reaches another run, the lab services or the internet | Every target action goes through the executor and the Incus API; run networks have no uplink and the empty ACL `pinka-run-default`, and the host firewall drops forwarding out of `r-*` bridges ([How benchmark targets are isolated](explanation/target-isolation.md)). |
| The agent reads the ground truth | Scenario ground truth is readable only by `pinka-gt`; the catalog is deployed root:pinka-gt with mode 0640 and appears nowhere else on the host. |
| The agent exhausts the host | Project and instance limits for CPU, memory and disk; a quota-capable pool (btrfs or LVM thin, never `dir`, [ADR-0009](adr/0009-btrfs-or-lvm-thin-pool.md)); CPU sets keep the lab services apart from the targets. |

### Buggy fault injection

A fault primitive may do more damage than intended.

| Risk | Mechanism |
| --- | --- |
| A fault escapes its target | Faults act inside Incus instances only, through the executor; targets are system containers or VMs, never containers of the lab's Docker daemon ([ADR-0007](adr/0007-incus-for-targets.md)). |
| A fault fills the host disk | Disk quotas of the storage pool; the pool lives on a dedicated device or a loop file, apart from the lab's data. |
| A broken target is reused | Targets start from versioned golden images; every run gets fresh instances, and the images are checked by a leakage lint before they are published ([ADR-0012](adr/0012-leakage-lint.md)). |

### A compromised target

A target may run an attacker's code after a scenario breaks it.

| Risk | Mechanism |
| --- | --- |
| The target attacks the host | No route from run networks to the host or the lab services; telemetry leaves through an Incus proxy device (containers) or one OTLP listener (VMs), nothing else ([How telemetry flows](explanation/telemetry.md)). |
| The target learns about the lab | `security.guestapi=false` removes `/dev/incus`; targets see only an opaque run ID and node alias. |
| The target attacks other runs | One bridge per run with a deny-all ACL; Incus and the host firewall both enforce it. |

### Researcher mistakes

Researchers have real accounts on the host.

| Risk | Mechanism |
| --- | --- |
| A researcher gets root-equivalent access by accident | Researchers are never added to `docker` or `incus-admin`; the break-glass list `break_glass_docker` takes admins only, is logged loudly and is recorded in every fingerprint. |
| A researcher's experiment disturbs official runs | Personal Incus projects (`user-<uid>`) with their own limits and no GPU; personal directories with mode 0700. |
| Secrets leak through a repository or a log | Every secret lives in the Ansible vault, which is encrypted and never committed to the public project; tasks that handle secrets use `no_log`; images are never baked with secrets; the installer keeps secrets in memory only. |
| A staging experiment touches the production lab | `lab_mode: staging` hosts have their own vault, research space, tailnet tag (`tag:pinka-qa`) and signing key, and never pair with or write to a production host. |

## Network exposure

- Nothing listens on a public interface except sshd, and the firewall admits
  SSH only on `tailscale0` and the interfaces listed in
  `ssh_extra_allowed_interfaces`. sshd accepts keys only, no root login, and
  only members of `pinka`.
- Every lab service binds `127.0.0.1`, a Unix socket or an internal Docker
  network ([port table](contracts.md#ports)); researchers use SSH port
  forwarding ([How to connect to the lab](how-to/connect.md)).
- Tailscale Funnel is never enabled; the tailscale role fails the play when
  any Funnel configuration exists. Grafana is published on the tailnet only
  when `grafana_tailscale_serve: true`.
- The firewall role integrates with the backend the host already uses and
  never flushes tables owned by Docker, Incus, firewalld or ufw
  ([ADR-0006](adr/0006-firewall-backend-integration.md)).
- On WSL2, interop and the automount of Windows drives are off, and nothing
  in the lab calls Windows programs.

## Tailnet policy

The lab joins the tailnet with the tag `tag:pinka-lab` (`tag:pinka-qa` on
staging hosts). The tailnet policy must let the researchers' devices reach
the lab on SSH, and on the Grafana serve port when Grafana is published on
the tailnet. The installer's Remote access screen copies these lines; add
them to the tailnet policy file:

<!-- qa:skip reason="tailnet policy fragment, not a command" -->
```json
"tagOwners": {"tag:pinka-lab": ["autogroup:admin"]},
"acls": [
  {"action": "accept", "src": ["autogroup:member"], "dst": ["tag:pinka-lab:22", "tag:pinka-lab:443"]}
]
```

For split inference the lab node and the inference node also need each other
(see [How to run inference on a separate GPU node](how-to/pair-inference-node.md)).
Narrow `src` to a group of researchers if not every member of the tailnet
should reach the lab.

## Integrity and reproducibility

Every external artifact is pinned by version and digest or checksum, and
repository keys by fingerprint ([Pinned versions](reference/versions.md));
the play refuses sources that are not verified. Every override an operator
grants (an unsupported host, accepted degradations, break-glass groups) is
recorded in the host profile and in every environment fingerprint
([Reproducibility](reproducibility.md)). Run manifests are signed with a key
generated on the host.

## Out of scope

- The security of the Windows side of a WSL2 host, of the hypervisor of a VM
  host, and of the host's firmware.
- Physical access to the machine, and its disks when they are not encrypted.
- A compromise of the Tailscale coordination service or of a researcher's
  own computer.
- Side channels between instances that share the host kernel; scenarios that
  need a separate kernel use VM targets.
- Administrators: members of `pinka-admin` can become root.
