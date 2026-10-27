---
title: "ADR-0014: Platforms plug in through manifests and fixed privilege profiles"
type: explanation
audience: [developer, operator, reader]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
---

# ADR-0014: Platforms plug in through manifests and fixed privilege profiles

- **Status:** accepted: contract v2, with the eight privilege profiles
  (`gpu-trainer` the eighth), the dedicated agent sockets, the reserved names
  and the neutrality lint
- **Date:** 2026-10-09 (when the decision was written down; it has no
  recorded date)
- **Deciders:** pokus-infrastructure maintainers

## Context

Contract v1 (`contract_version 1`) was written for one platform. It named
the Pokus Platform's seven services, their accounts, units and
supplementary groups (`potka-svc`, `potka-exec-clients`,
`potka-gw-clients`), every socket the agent runner could reach and the
paths hidden from it, and infra's own `deploy_source` and `catalog` roles
deployed that platform and its scenario catalog.

This had three effects:

- A change to the platform's services, sockets or groups was a contract
  change: bump `contract_version`, change both copies of the contract in the
  same commit and adapt both components. Infra changed with every such
  platform change.
- A second platform could not plug in. Every account, unit, group and path
  in infra's code was the first platform's.
- Privileges came from shared groups. The v1 agent runner was a member of
  the shared service group and kept TCP access; contract v2 had to close
  both, with tests proving it.

Contract v2 makes infra a standalone lab product that any
testing platform plugs into through a manifest, with the Pokus Platform
(`potka`) as the first platform. Whatever a manifest says, infra must still
keep restricted data (ground truth is one case of it) away from the agent and
from training jobs.

## Decision

1. **One manifest per release.** Every platform release carries
   `platform-manifest.yaml` at its root. Infra validates it against the
   published `contract/platform-manifest.schema.json` (JSON Schema draft
   2020-12, unknown fields are errors) and then checks the invariants the
   schema cannot express: exactly one controller, commands that are regular
   files inside the release, limits within infra's caps, the socket
   connection rules, no overlap between the release and its content packages
   in one repository, and every facility the platform uses declared in
   `requires`. `pinka-platform validate` runs these checks; a refused
   release ends bootstrap with exit code 65 before anything on the host
   changes. The schema, `contract/profiles.yml`, the reserved names and the
   record schemas in `contract/records/` are published with every infra
   release and change only with a contract version bump; a manifest's
   `contract_version` must equal the installed infra's.
2. **Eight fixed privilege profiles.** Each service or job names exactly one
   profile: `controller`, `inference-broker`, `sandbox-operator`,
   `restricted-operator`, `restricted-reader`, `observer`, `untrusted-agent`
   or `gpu-trainer` (jobs only, [ADR-0015](0015-gpu-training-container-jobs.md)).
   Its privileges are exactly that profile's grants for the database, Incus,
   telemetry, inference and the GPU, restricted data, the file system and the
   network. A manifest adds only its own state directories, secrets, limits
   within infra's caps and the one fixed relaxation `wx-memory`, which
   `untrusted-agent` never gets. `contract/profiles.yml` is the single source
   of the profile table, the socket connection rules and the caps; the
   validator, the renderer, doctor's platform checks and the isolation
   probe's expectations follow it.
3. **Infra renders, the platform declares.** From the manifest, the profiles
   and the host settings, infra renders the accounts
   (`<platform>-<service>`, `<platform>-<job>`), groups, database roles,
   Incus certificates, configuration and secret files, the systemd units in
   `pinka-platform.target` with the hardening baseline (never overridable by
   a manifest beyond the `wx-memory` relaxation), the socket units with their
   client groups and ACLs, the OPA bundle link, the dashboards and the CLI
   link. The platform reads names, UIDs and addresses from
   `/etc/pinka/platform/principals.json` and
   `/etc/pinka/platform/endpoints.toml` instead of hard-coding them, and
   infra drives it only through four hooks (`migrate`, `reconcile`, `drain`,
   `health`).
4. **Isolation the manifest cannot change.** `untrusted-agent` and
   `gpu-trainer` never reach the restricted area, the `pinka-restricted`
   group or the `restricted` schema. Every socket the agent may use is
   dedicated to it (`untrusted_clients: true`, no other client); sockets of
   the `restricted-*` profiles accept only the controller and root; after
   every migration infra revokes the `restricted` schema from every role
   outside the three restricted-capable profiles.
5. **Reserved names.** The schema carries one reserved list per namespace
   (accounts and groups, systemd units, containers, networks and Incus
   objects, PostgreSQL, path segments, commands and platform names),
   generated from infra's own data, so a new infra name is reserved
   automatically. The validator derives every name the manifest would create
   and refuses one that is reserved, clashes with another derived name, or
   already exists on the host.
6. **Neutrality.** No infra code names a platform. `make lint-neutral`, part
   of `make lint`, fails on `potka` and on an explicit list of the Pokus
   Platform's service and unit names, matched as whole tokens, in infra's
   roles, tools and templates; test fixtures, docs and the reserved-name list
   are excluded. The Pokus Platform's manifest is only a test fixture.
7. **The reference platform proves that a second platform fits.** `refplat`
   (`tests/reference-platform/`), written with the Python standard library
   only, is a real release with its own manifest. `make test-distros` deploys
   it through the same code as any platform; its `replace` stage deploys a
   renamed copy with `--replace-platform`, and the isolation probe runs for
   its agent and for every profile with loopback access.
8. **One platform at a time, replaced explicitly.**
   `/etc/pinka/platform/active.json` records the active platform. A release
   with another name is refused unless the operator passes
   `--replace-platform <old name>`, which drains and stops the old platform,
   removes its units, sockets and CLI link, locks its accounts, makes its
   database roles `NOLOGIN` and removes its Incus certificates from the trust
   store. Its state, database, restricted area and releases stay on disk
   until `pinka platform purge <name>`, which needs a typed confirmation.
   Infra refuses to run two platforms at once.

## Consequences

- A platform change that stays within the profiles (a new service, socket,
  job or state directory) needs no infra change, and infra's code and tests
  hold no special case for any platform.
- Isolation is defined once per profile and checked the same way for every
  platform: rendering golden files (with a fixture that uses all eight
  profiles), doctor's checks per profile and the isolation probe per
  profile.
- The v1 agent runner's TCP access and group membership are gone:
  `untrusted-agent` runs with `PrivateNetwork=yes`, has no supplementary
  groups except the client groups of its own sockets, and sees only its
  allowlist: its own package and environment of the release (V6).
- The installer and `bootstrap.sh --inspect-platform` show what a release
  would get before anything on the host changes.
- Profiles are coarse. A service gets everything its profile grants, and a
  service that needs a combination no profile offers must be split into
  services that each fit one, or wait for a new profile, which is a contract
  version bump.
- A restricted Incus certificate has full access inside its projects: a
  `sandbox-operator` service can do in `bench` whatever the controller can.
  Narrowing that needs Incus's fine-grained authorization, recorded as out
  of scope for contract v2.
- An infra release that bumps the contract version refuses the manifests of
  the previous version, so every platform needs a matching release before it
  can be deployed on that infra.
- What infra cannot inspect stays the platform's obligation: no endpoint on
  a dedicated agent socket may return restricted data (a platform test must
  prove it), and the dataset provenance it writes must be complete and
  truthful.
- One platform per lab: running another platform on the same host means
  replacing the active one, and the retired platform's data stays on disk
  until it is purged.

## Alternatives considered

- **Contract v1 extended per platform**: a contract and infra roles that
  name each platform's services, groups and sockets. Every platform change
  would stay an infra change, and each new platform would add infra code
  whose isolation would have to be reviewed and tested on its own.
- **Grants chosen per service in the manifest**: a manifest picking
  database, Incus, telemetry, restricted-data and network access field by
  field. The socket connection rules, doctor's platform checks
  and the isolation probe are written per profile; free
  combinations would make each platform's grants a new case to review and
  test, and the restricted-data guarantees would rest on rules about
  combinations instead of on a closed table.
- **An OCI runtime for platform services** (every service in a container):
  out of scope for contract v2. Services stay systemd units with the
  hardening baseline, which doctor reads back with `systemctl show`; only
  training jobs run in containers, with options infra fixes
  ([ADR-0015](0015-gpu-training-container-jobs.md)).
- **Several platforms at once**: out of scope for contract v2 as well.
  The GPU arbiter's socket belongs to one controller, the
  inference broker's researcher endpoint is one fixed port
  (`127.0.0.1:18705`), and the targets of every platform would share the
  Incus project `bench`.

The interface as a whole is the
[integration contract](../contracts.md) (contract v2). The manifest fields
are described in [Platform manifest](../reference/platform-manifest.md), the
grants in [Platform privilege profiles](../reference/platform-profiles.md)
and why they are cut this way in
[Privilege profiles and restricted data](../explanation/privilege-profiles.md),
and replacing a platform in
[How to replace or purge a platform](../how-to/replace-platform.md).
