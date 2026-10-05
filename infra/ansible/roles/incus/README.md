# incus

Incus for benchmark targets and personal sandboxes (BUILD_PROMPT §7.7):

- **Install:** the LTS line (6.0) from the source of
  `ansible/component-sources.yml` (Zabbly's repository on Debian and Ubuntu,
  the distribution or EPEL/COPR/OBS elsewhere; community repositories need
  `--allow-community-repos`), pinned and held. Source and channel (`lts` or
  `non_lts`) are written to `/etc/pokus/incus-source.json` for the
  fingerprint.
- **Ownership:** an Incus that someone else configured is only touched with
  `--adopt-incus`; `/etc/pokus/managed-incus` marks a Pokus installation.
- **Storage:** one pool `pokus-targets`, btrfs or LVM thin as preflight
  decided, on a loop file (`incus_pool_size`) or a dedicated device. A device
  is formatted only after the checks of safety invariant 1: the confirmation
  variable repeats the device, nothing is mounted, no signature
  (`wipefs --no-act`, `lsblk`) unless `incus_pool_device_force_wipe`, and an
  interactive confirmation after showing model, size and serial.
- **Networks:** no default `incusbr0`. `pokus-build0` (NAT) for image builds;
  one NAT bridge `incusbr-<uid>` per researcher. Run networks `r-<short>` are
  created by the platform; the ACL `pokus-run-default` has no rules, so
  everything that leaves a run bridge (to the host, to other run networks, to
  the outside) is rejected except Incus's DHCP and DNS.
- **Projects:** `bench` (restricted; nesting, proxy devices and snapshots
  allowed; VMs only with `vm_targets`; limits from the host's size), `build`
  (restricted to `pokus-build0`), and `user-<uid>` per researcher with the
  restrictions incus-user would set, resource limits and no GPU. Pokus creates
  the personal projects in advance because incus-user sets no limits and
  points its profile at a pool named `default`.
- **Profiles:** `target-container`, `target-vm` (with `vm_targets`),
  `probe-client` in `bench`, `build` in `build`, and the `default` profile of
  each personal project. Target profiles set `security.guestapi=false`.
- **API and trust:** HTTPS on `127.0.0.1:8443`; client certificates generated
  on the host for `pokus-orch` (bench and build), `pokus-exec`,
  `pokus-inject` and `pokus-verify` (bench), restricted to those projects, in
  `/etc/pokus/incus/<svc>/` (0600, owned by the service account, with the
  server certificate); a metrics-only certificate in
  `/etc/pokus/incus/metrics/`.
- **Events:** `pokus-incus-events.service` runs
  `incus monitor --all-projects --type=lifecycle --type=logging --format=json`
  into the journal with the identifier `pokus-incus-events` (blank lines
  between events).
- **Access:** researchers join the `incus` group (incus-user socket); nobody
  is added to `incus-admin`, and service accounts use TLS only.

A new installation is initialized once from `incus/preseed.yaml.j2`; every
run then converges the same objects with the role's module
`pokus_incus_object` (create or update through `incus query`; it never
deletes). The rendered preseed is kept in `/etc/pokus/incus/preseed.yaml`.

## Variables

See `meta/argument_specs.yml`; group names and paths per family are in
`vars/<family>.yml`.

## Feature flags

`vm_targets` (VM limits and the `target-vm` profile).

## Idempotency

The module compares only the fields Pokus manages; the pool and the networks
are created once and never rewritten (their addresses are chosen by Incus);
certificates are issued once and trusted by fingerprint.
