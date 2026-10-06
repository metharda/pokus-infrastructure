# doctor

The operator CLI `pokus-lab` (BUILD_PROMPT §9) and the lab description its
commands read; tag `doctor`. It runs before `lab_session`, because the
session record of every boot calls `pokus-lab fingerprint` and
`pokus-lab preflight`.

- `/usr/local/lib/pokus/pokus-lab/pokus_lab/`: the CLI, Python standard
  library only (3.9 or newer).
- `/usr/local/lib/pokus/preflight/`: a copy of preflight with its compiled
  support matrix, endpoints and schemas. `pokus-lab preflight` and doctor run
  this copy, read-only (`--no-write`), so they do not depend on the checkout.
- `/usr/local/bin/pokus-lab`: a POSIX shell wrapper that runs the package with
  the pinned CPython (`/opt/pokus/python`) or the first `python3` 3.9 or
  newer, isolated (`-I`), with both directories on `sys.path`.
- `/etc/pokus/lab.json` (0644 root): what doctor, `status` and the
  fingerprint compare the host with, rendered from this host's variables:
  units of `pokus-lab.target`, containers with their pinned digests, the
  layout, the accounts, the Incus pool, projects, profiles and client
  certificates, the PostgreSQL roles, the observability endpoints, the golden
  image blueprints, the backup state and the WSL expectations. It also
  carries the sha256 of `versions.lock.yml` and of the host_vars file for the
  fingerprint. pokus-lab never reads the repository or the vault.
- `/var/lib/pokus/fingerprint/` (0755): the fingerprint cache written by root
  (`current.json`, 0644), which other users read (contracts.md C9c).

Modules removed from the sources are removed from the installed copy.
`hardening` and `sockets` stay empty until the platform roles describe their
units and sockets.

## Variables

See `meta/argument_specs.yml`.

## Feature flags

`gpu_inference` adds the GPU exporter container and the GPU checks on `full`
and `inference` nodes; `vm_targets` adds the `target-vm` profile, the VM
telemetry listener address and the `/dev/kvm` expectation on WSL.

## Idempotency

Every file is only rewritten when its content changes; the lab description
changes when a variable it is rendered from changes, or when
`versions.lock.yml` or the host_vars file change (their sha256 is in it).
