---
title: "ADR-0006: Integrate with the host's firewall backend instead of replacing it"
type: explanation
audience: [developer, reader]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
---

# ADR-0006: Integrate with the host's firewall backend instead of replacing it

- **Status:** accepted
- **Date:** 2026-10-05
- **Deciders:** pokus-infra maintainers (BUILD_PROMPT §7.5, §19.6)

## Context

The lab needs a strict input policy (SSH only on the tailnet), DHCP and DNS
for Incus instances, and isolation of run networks. Hosts come with different
firewall managers: Fedora and RHEL-compatible systems usually run firewalld,
some Ubuntu hosts run ufw, others have no manager at all. Docker and Incus
add their own rules, and Docker sets the `FORWARD` policy to drop, which
breaks Incus bridges. Replacing the host's firewall would fight the
distribution's tooling and break whatever else the operator configured.

## Decision

1. The backend is chosen per host: firewalld if it is active, else ufw if it
   is active, else Pokus's own nftables table. `firewall_backend` in host_vars
   overrides the choice.
2. Pokus only ever changes **its own** objects: the `inet pokus_filter` table
   (loaded by `pokus-nftables.service` from `/etc/pokus/nftables/pokus.nft`),
   the `pokus-tailnet` firewalld zone, or ufw rules commented `pokus-infra`.
   It never flushes or rewrites tables, zones or rules owned by Docker, Incus,
   firewalld or ufw, and never edits a distribution's nftables configuration
   file.
3. All backends must produce the same **effective** policy, which doctor
   verifies with connection tests rather than by reading rules.
4. Docker and Incus coexist through `pokus-docker-incus-compat.service`, which
   inserts `ACCEPT` rules for Incus bridges into Docker's `DOCKER-USER` chain
   after Docker starts.
5. Run networks are isolated by the Incus network ACL `pokus-run-default` on
   every backend (nothing leaves a run bridge except DHCP and DNS to the
   host). Each backend adds its own layer for the run bridges, matched by
   interface wildcard (`r-*` in nftables, `r-+` in firewalld and ufw): only
   DHCP and DNS are accepted from them, and on nftables nothing is forwarded
   out of them.

## Consequences

- Operators keep using their distribution's firewall tools; Pokus's rules are
  visible there and labelled.
- Three backends must be tested (`make test-distros` has one variant each).
- Run bridges are created at run time; all three backends match them with
  interface wildcards (firewalld since 0.7.0, ufw passes `+` to iptables), so
  no rule has to be added per run. The Docker compatibility hook is the same
  on all backends.

## Alternatives considered

- **Always nftables, disabling firewalld or ufw**: simplest rules, but
  breaks the distribution's expectations and the operator's other rules.
- **Only firewalld or only ufw**: neither is available or active on every
  family.
- **iptables rules**: deprecated on current distributions in favour of
  nftables.
