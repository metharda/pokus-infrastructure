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
| The agent acts on the host instead of a target | The platform's agent runs under the `untrusted-agent` profile: no network at all (`PrivateNetwork=yes`, Unix sockets only), no database role, no Incus certificate, no inference API, no secrets; it sees only its own package and environment, its state and the sockets dedicated to it, and `/srv/pinka`, `/etc/pinka`, `/var/lib/pinka` and `/run/pinka` are otherwise empty for it. It is never in `docker`, `incus-admin`, `pinka-restricted` or the database socket group ([Privilege profiles and restricted data](explanation/privilege-profiles.md)). |
| The agent reaches another run, the lab services or the internet | Every target action goes through the platform's own services and the Incus API; run networks have no uplink and the ACL `pinka-run-default`, and the host firewall drops forwarding out of `r-*` bridges ([How benchmark targets are isolated](explanation/target-isolation.md)). The telemetry stores and the inference API admit only a UID allowlist ([ADR-0016](adr/0016-telemetry-query-isolation.md)). |
| The agent reads restricted data (ground truth) | Restricted data lives only in `/srv/pinka/restricted/<platform>/` (root:`pinka-restricted`, 0750/0640) and the database schema `restricted`; only the controller, `restricted-operator` and `restricted-reader` services can read them, the agent and training jobs never, whatever the manifest says; after every migration infra revokes every forbidden grant on the schema. Sockets dedicated to the agent are listed in the installer's review and by doctor; that they return no restricted data is the platform's obligation, proved by its tests. |
| The agent exhausts the host | Project and instance limits for CPU, memory and disk; a quota-capable pool (btrfs or LVM thin, never `dir`, [ADR-0009](adr/0009-btrfs-or-lvm-thin-pool.md)); CPU sets keep the lab services apart from the targets. |
| The isolation regresses unnoticed | Every distribution test deploys the reference platform and runs its isolation probe: as its agent, which must reach nothing, and as a transient unit for each loopback-capable profile, which must reach exactly what the profile allows: no telemetry store directly, the query proxy only for the profiles that query, the inference API and the GPU arbiter only for the platform's own accounts that may ([How to test the distributions](how-to/test-distributions.md#what-the-reference-platform-stages-check)). |

### A platform release

A platform is code that infra deploys but does not write.

| Risk | Mechanism |
| --- | --- |
| A release asks for more than it needs | Its manifest names one of eight fixed profiles per service and job; infra renders exactly that profile's grants, and a manifest that asks for anything else is refused before the host changes (exit 65, [ADR-0014](adr/0014-platforms-through-manifests.md)). |
| A release's code runs as root | Never: a release is built as `pinka-build` in a `systemd-run` sandbox, from locked wheels, with the second phase offline; its services and hooks run under their own accounts with the hardening baseline, which a manifest cannot override. |
| A release names infra's accounts, units or paths | Every name the manifest would create is checked against the reserved names and against what already exists on the host. |
| A moved or forged tag | Sources come from root-only bare mirrors; a moved tag is refused, and with `require_signed_tags` only tags signed by an allowed signer are deployed. |
| A training job escapes | Jobs run as containers with options fixed by the GPU arbiter: no network, a read-only root file system, only the base model, its published datasets and its output mounted, the GPU only while it trains ([ADR-0015](adr/0015-gpu-training-container-jobs.md)). Datasets that list an instance of the `test` split are never published or mounted. |

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
- With split inference, the split-inference ports on `tailscale0` (the vLLM
  API and the control port of an inference node, OTLP gRPC of a lab node)
  admit only the paired node's tailnet address, recorded at pairing, and
  nobody before a pairing (`pinka_split_guard`); the pairing port is open to
  the tailnet only while `pinka pair show` waits. Mutual TLS then admits
  only the paired node's certificate
  ([How to pair an inference node](how-to/pair-inference-node.md)).
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
([Reproducibility](reproducibility.md)). Each platform gets its own signing
key, generated on the host and readable only by its controller; its public
keys are published and kept forever. Every platform release and content
package carries `DEPLOYED_FROM.json` with its commit and tree hash, and
bootstrap records infra's own commit and tree hash, also when it runs from
a release package.

## Out of scope

- The security of the Windows side of a WSL2 host, of the hypervisor of a VM
  host, and of the host's firmware.
- Physical access to the machine, and its disks when they are not encrypted.
- A compromise of the Tailscale coordination service or of a researcher's
  own computer.
- Side channels between instances that share the host kernel; scenarios that
  need a separate kernel use VM targets.
- Administrators: members of `pinka-admin` can become root.
