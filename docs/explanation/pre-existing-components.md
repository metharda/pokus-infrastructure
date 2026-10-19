---
title: Components that existed before pinka
type: explanation
audience: [operator, developer, reader]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: What pinka uses as it is when Docker, Tailscale, LXD, Podman, Incus, an NVIDIA driver, accounts or other access were on the machine first, what it refuses, and the trade-offs of an adopted Tailscale node.
---

# Components that existed before pinka

A lab host is often a machine that already does other work: Docker runs
someone's containers, Tailscale connects it to a personal tailnet, LXD or
Podman runs other systems. pinka shares such a machine without taking these
components over. It never changes, stops, restarts or reconfigures a
component it did not install. Where the lab cannot run without such a
change, preflight stops with `needs_action` before anything on the host
changes, and names the exact change for the owner to make. No override
continues past it.

## How pinka knows what is its own

The **install record**, `/var/lib/pinka/install-record.json` (root only,
mode 0600), is written once, at the first install and before any change to
the host. Its `preexisting` part lists what was there: the components with
their versions and settings, the installed packages, the enabled and masked
units, the accounts and groups, and the contents of the few files pinka
shares with the system. Its `added` part grows as the roles work: every role
notes a package, a hold, an account, a group membership, an SSH key, a
component or a `tailscale serve` entry **before** it creates it. So the
record never misses something pinka made, even when a run stops halfway.

Preflight reads the record to give each component its origin in the host
profile (`components.<name>.origin`): `pinka` when the record's `added` part
names it, `pre-existing` otherwise. A machine pinka provisioned before the
record existed gets a *legacy* record, which decides nothing; there, and on
a machine without a record, pinka's own marks decide: the `org.pinka.managed`
label of a `daemon.json` pinka wrote, a node tag starting with `tag:pinka-`,
the `/etc/pinka/managed-incus` marker.

Doctor checks that the record is present, readable and only root can change
it (`install.record`). The record is never rewritten and never edited by
hand.

## What pinka does with each component

| Component | Used as it is | Refused when |
| --- | --- | --- |
| Docker Engine | pinka never writes its `daemon.json`, never restarts it, never pins, holds or replaces its packages | it is not running; the Compose v2 or buildx plugin is missing; a GPU feature is on and CDI is off |
| Tailscale, logged in | no login, no new host name, tags or settings, no repository and no package change; `tailscale serve` only with the owner's consent | Funnel is on; Tailscale SSH is on |
| Tailscale, installed but logged out | not in use, so pinka joins it with the lab's tag; its package stays as it is | — |
| LXD | never changed, stopped or reconfigured; pinka's input policy admits DHCP and DNS on LXD's bridges, so its guests keep the service of LXD's dnsmasq, and pinka filters no forwarding but that of its run networks | Incus next to it would clash: bridge names, subnets, a DNS or DHCP server on every address, the pool device, or a Docker that pinka would install |
| Podman | never changed | it provides Docker's command or socket (`podman-docker`) |
| Incus | used only when the owner adopts it with bootstrap's `--adopt-incus` ([options](../reference/cli/bootstrap.md)) | it holds data and was neither installed nor adopted by pinka |
| NVIDIA driver | never replaced, removed or upgraded; a driver too old for the vLLM image leaves the GPU unused, with the remediation | — |
| User accounts | an existing account gets the lab's groups and the researcher's keys added, its own keys stay | its name is one of the lab's reserved accounts |

Preflight refuses with `needs_action` before anything changes; an Incus
with data that pinka neither installed nor adopted is a blocking conflict
instead, and an account or group with one of the lab's reserved names stops
the play before it creates any account.

## Docker settings live in pinka's own projects

The lab needs a log size limit for its containers, a cgroup parent
(`pinka-services.slice`) and networks that do not overlap Incus, Tailscale or
home networks. On a Docker pinka installs, Docker's daemon-wide defaults
could carry these; on a Docker that was there first, they would change every
other container on the machine. So pinka sets them in its Compose projects
and on its training containers, the same way on every install, and writes
`daemon.json` only on a Docker it installed itself, with only what such an
install needs (`live-restore`, `data-root`, SELinux, and `features.cdi` on a
distribution Docker older than 28.2).

GPU containers need CDI, which Docker Engine enables by default from 28.2.
pinka reads how containers reach the GPU and where Docker keeps its data
from the running engine (`docker info`), whoever installed it, and doctor
checks CDI (`gpu.cdi`).

## The trade-off of an adopted Tailscale node

A node logged in before pinka usually belongs to a person's account. pinka
uses it as it is, which has two consequences doctor reports as warnings
(`network.tailscale_identity`), not failures:

- **No lab tag.** The node may lack `tag:pinka-lab`, so the tailnet policy
  cannot select it by tag. The ACL has to name the node another way (its
  host name or address) to let researchers reach SSH and nothing else.
- **An expiring key.** The key of a personal login expires after the period
  the tailnet sets. When it does, the lab leaves the tailnet until the owner signs
  in again; the lab itself keeps running.

Tagging the node in the admin console, or disabling its key expiry, removes
both warnings. A node with Funnel or Tailscale SSH on is refused: Funnel
would publish lab services to the internet, and Tailscale SSH would bypass
`sshd` and the researchers' keys.

Publishing Grafana or the docs with `tailscale serve` changes the node's
serve configuration, which may hold the owner's own entries. On an adopted
node it is therefore off unless the owner consents
(`tailscale_serve_consent: true`, a checkbox in the installer); pinka notes
every entry it adds, so only those are ever removed.

## Access that existed before pinka

A machine that already does other work is reachable in ways the lab did not
choose: a web server on the LAN, the owner's SSH logins with their own
policy, the SSH session the install runs in. Preflight records this access
(BUILD_PROMPT §2.2): the listeners outside loopback that are not the lab's
or a component pinka installed, the login accounts that are not pinka's, and
the install's session. A host where only sshd listens (DHCP clients and
Tailscale's port aside) and every login account is a researcher is
*dedicated*; any other is *shared*.

On a dedicated host pinka's strict policy is the right default: nothing
else uses the machine. On a shared host it would cut off other people's
work, so pinka keeps what was reachable reachable and closes a listener only
with the owner's consent, one checkbox per listener. Closing never stops the
service, and it does not touch the owner's firewall: pinka's own nftables
table `inet pinka_access_guard` drops new connections to what was closed, the
same way with nftables, firewalld or ufw, so removing that table (as
uninstall does) makes everything reachable as it was. A port Docker
publishes for a container is forwarded to the container and never reaches
the host's input policy, so pinka cannot close it and says so. pinka's sshd
settings apply only to the members of `pinka` on a shared host (a
`Match Group pinka` block): the owner's accounts keep the machine's own SSH
policy.

The install's own access path needs one more safeguard, on every host: an
installer working from the LAN must not lose the machine because the lab's
policy came into force. The path closes only when the owner consented and a
researcher logged in with a key over the tailnet during the same run, which
shows that the lab can be reached the intended way; until then it stays open
and doctor warns. The install record lists what was reachable and what was
closed with consent.

## Related pages

- [How to install next to existing Docker, Tailscale, LXD or Podman](../how-to/install-next-to-existing-components.md)
- [How the preflight gate decides](preflight-gate.md)
- [Security of the lab host](../security.md)
- [Host profile schema](../reference/host-profile.md)
