# doctor

The operator CLI `pinka` and the lab description its
commands read; tag `doctor`. It runs before `lab_session`, because the
session record of every boot calls `pinka fingerprint` and
`pinka preflight`.

- `/usr/local/lib/pinka/pinka-lab/pinka_lab/`: the CLI, Python standard
  library only (3.9 or newer).
- `/usr/local/lib/pinka/preflight/`: a copy of preflight with its compiled
  support matrix, endpoints and schemas. `pinka preflight` and doctor run
  this copy, read-only (`--no-write`), so they do not depend on the checkout.
- `/usr/local/bin/pinka`: a POSIX shell wrapper that runs the package with
  the pinned CPython (`/opt/pinka/python`) or the first `python3` 3.9 or
  newer, isolated (`-I`), with both directories on `sys.path`.
- `/etc/pinka/lab.json` (0644 root): what doctor, `status` and the
  fingerprint compare the host with, rendered from this host's variables:
  units of `pinka-lab.target`, containers with their pinned digests, the
  layout, the accounts, the Incus pool, projects, profiles and client
  certificates, the PostgreSQL roles, the observability endpoints, the golden
  image blueprints, the backup state and the WSL expectations. It also
  carries the sha256 of `versions.lock.yml` and of the host_vars file for the
  fingerprint. pinka never reads the repository or the vault.
- `/var/lib/pinka/fingerprint/` (0755): the fingerprint cache written by root
  (`current.json`, 0644), which other users read (contracts.md C9c).
- `/usr/local/share/pinka/docs/` (0755, empty): where an operator extracts the
  documentation tarball of a release for `pinka docs serve`, which serves
  it on `127.0.0.1` (port `pinka_ports.docs_serve`; both in `lab.json`).

Modules removed from the sources are removed from the installed copy.
`hardening` and `sockets` stay empty until the platform roles describe their
units and sockets.

## Variables

See `meta/argument_specs.yml`.

## Feature flags

`gpu_inference` adds the GPU checks on `full` and `inference` nodes, and
`gpu_inference` or `gpu_training` (after the switches) the
GPU exporter container; `vm_targets` adds the `target-vm` profile, the VM
telemetry listener address and the `/dev/kvm` expectation on WSL.

## Idempotency

Every file is only rewritten when its content changes; the lab description
changes when a variable it is rendered from changes, or when
`versions.lock.yml` or the host_vars file change (their sha256 is in it).
