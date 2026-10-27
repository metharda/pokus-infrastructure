---
title: "ADR-0004: Family-based portability with a component source matrix"
type: explanation
audience: [developer, reader]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
---

# ADR-0004: Family-based portability with a component source matrix

- **Status:** accepted
- **Date:** 2026-10-05
- **Deciders:** pokus-infrastructure maintainers

## Context

Pokus Infrastructure (pinka) must install on Debian, Ubuntu, Fedora, RHEL-compatible distributions,
openSUSE, Arch and their derivatives. Writing and testing roles per
distribution does not scale, and scattering `when: ansible_distribution ==
...` conditions through the roles makes it impossible to see what differs
between hosts or to add a distribution safely. Components such as Docker,
Incus and Tailscale also come from different places on different
distributions: the vendor's repository on some, the distribution's own
packages on others.

## Decision

1. Hosts are grouped into four **families** (`debian`, `redhat`, `suse`,
   `archlinux`) that share a package manager, a hold mechanism, service names
   and paths. Derivatives use their parent's family and release identity.
2. Distribution differences may live only in the support matrix,
   `ansible/component-sources.yml`, `vars/<family>.yml` files and small
   `tasks/<name>_<family>.yml` files. The `platform_facts` role is the only
   code that interprets the distribution; `make lint` fails on distribution
   facts used anywhere else.
3. `ansible/component-sources.yml` is a **declarative matrix**: for each
   component and source group (`ubuntu`, `debian`, `fedora`, `rhel_like`,
   `opensuse`, `arch`, with a `default`), where the component comes from
   (vendor repository, distribution, community repository) and which packages
   make it up. Versions are pinned per release in `versions.lock.yml`;
   repository keys are pinned by fingerprint there too.
4. Entries that could not be verified carry a `verify` marker; the play
   refuses them instead of guessing. Community repositories need explicit
   consent and are recorded in the fingerprint.
5. All four families are implemented from the start, not added later.

## Consequences

- What differs between two hosts can be read from four kinds of files and
  from the [Component sources](../reference/component-sources.md) page,
  which is generated from the matrix.
- Adding a family is a checklist
  ([How to add a distribution family](../distros/adding-a-family.md)), and the
  role tests check that every family has its vars and task files.
- Some distributions get a component from their own packages at a version
  different from the vendor's; the installed versions are always recorded in
  the fingerprint.

## Alternatives considered

- **Per-distribution roles**: clearer per role, but four to six copies of
  the same logic to keep in step.
- **Ansible's generic `package` module everywhere**: hides the package
  manager but cannot pin versions, hold packages or add repositories
  uniformly.
- **Containers for everything, including Docker and Incus**: impossible for
  the components that manage containers themselves.
