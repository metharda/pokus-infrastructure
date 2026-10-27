# incus

Incus for benchmark targets and personal sandboxes:

- **Install:** the LTS line (6.0) from the source of
  `ansible/component-sources.yml` (Zabbly's repository on Debian and Ubuntu,
  the distribution or EPEL/COPR/OBS elsewhere; community repositories need
  `--allow-community-repos`), pinned and held. Source and channel (`lts` or
  `non_lts`) are written to `/etc/pinka/incus-source.json` for the
  fingerprint.
- **Ownership:** an Incus that someone else configured is only touched with
  `--adopt-incus`; `/etc/pinka/managed-incus` marks a Pokus Infrastructure (pinka) installation.
- **Storage:** one pool `pinka-targets`, btrfs or LVM thin as preflight
  decided, on a loop file (`incus_pool_size`) or a dedicated device. A device
  is formatted only after the checks of safety invariant 1: the confirmation
  variable repeats the device, nothing is mounted, no signature
  (`wipefs --no-act`, `lsblk`) unless `incus_pool_device_force_wipe`, and an
  interactive confirmation after showing model, size and serial.
- **Networks:** no default `incusbr0`. `pinka-build0` (NAT) for image builds;
  one NAT bridge `incusbr-<uid>` per researcher. Run networks `r-<short>` are
  created by the platform; the ACL `pinka-run-default` has no rules, so
  everything that leaves a run bridge (to the host, to other run networks, to
  the outside) is rejected except Incus's DHCP and DNS.
- **Projects:** `bench` (restricted; nesting, proxy devices and snapshots
  allowed; VMs only with `vm_targets`; its memory from
  `incus_concurrent_targets`, each target with a probe client), `build`
  (restricted to `pinka-build0`), and `user-<uid>` per researcher with the
  restrictions incus-user would set, resource limits and no GPU. Pokus Infrastructure creates
  the personal projects in advance because incus-user sets no limits and
  points its profile at a pool named `default`.
- **Profiles:** `target-container`, `target-vm` (with `vm_targets`),
  `probe-client` in `bench`, `build` in `build`, and the `default` profile of
  each personal project. Target profiles set `security.guestapi=false`.
- **API and trust:** HTTPS on `127.0.0.1:8443`; one restricted client
  certificate per platform service whose profile has Incus access, generated
  on the host from the active platform's rendering (`controller` for
  `bench` and `build`, `sandbox-operator` and `restricted-operator` for
  `bench`), in `/etc/pinka/incus/<platform>-<service>/` (0600, owned by the
  service's account, with the server certificate); a metrics-only
  certificate in `/etc/pinka/incus/metrics/`. The platform role issues the
  certificates (`tasks_from: certificates`) once the accounts exist.
- **Run-network helper:** `pinka-run-network.socket`
  (`/run/pinka/run-network.sock`, 0600, owned by the active platform's
  controller account, root without a platform) starts one root helper per
  connection (`pinka-run-network@.service`, no network, only `AF_UNIX`, no
  capabilities). It checks the peer's UID, accepts exactly
  `create r-<12 letters or digits>` and `delete r-<...>`, creates the bridge
  in the `default` project with fixed settings (`ipv4.address=auto`,
  `ipv4.nat=false`, `ipv6.address=none`, `security.acls=pinka-run-default`,
  no uplink) and refuses an existing network with other settings. Every
  operation goes to `/var/lib/pinka/run-networks/operations.jsonl` (0600) and
  the journal. `run-network unknown` lists the networks that neither infra
  created nor the log records, for doctor.
- **Events:** `pinka-incus-events.service` runs
  `incus monitor --all-projects --type=lifecycle --type=logging --format=json`
  into the journal with the identifier `pinka-incus-events` (blank lines
  between events).
- **Access:** researchers join the `incus` group (incus-user socket); nobody
  is added to `incus-admin`, and service accounts use TLS only.

A new installation is initialized once from `incus/preseed.yaml.j2`; every
run then converges the same objects with the role's module
`pinka_incus_object` (create or update through `incus query`; it never
deletes). The rendered preseed is kept in `/etc/pinka/incus/preseed.yaml`.

## Variables

See `meta/argument_specs.yml`; group names and paths per family are in
`vars/<family>.yml`.

## Feature flags

`vm_targets` (VM limits and the `target-vm` profile).

## Idempotency

The module compares only the fields Pokus Infrastructure manages; the pool and the networks
are created once and never rewritten (their addresses are chosen by Incus);
certificates are issued once and trusted by fingerprint.

## Check mode

Check mode verifies the source and the repository key, but cannot resolve
packages from a repository it did not really add. While Incus is not
installed (`incus` is missing when the play starts), the role ends after
its repository step in check mode.
