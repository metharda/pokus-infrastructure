# install_record

Runs right after `preflight_gate`, before any change to the host
(BUILD_PROMPT §7.21). It writes `/var/lib/pinka/install-record.json` (0600
root) once, at the first install, with what existed before pinka:

- the components preflight found (`host_profile.components`: Docker Engine,
  Tailscale, LXD, Podman, Incus, the NVIDIA driver, with their versions and
  state);
- the full package list with versions, the enabled and the masked units, the
  accounts and the groups with their members;
- the content of every file in `install_record_managed_files` that already
  existed, so that uninstall can restore what pinka edited.

The record is never rewritten. The roles then add what they create through
the module `pinka_install_record` (`state: add`): a component before pinka
installs or joins it (`components`), the packages the `pkg` role installs
that were absent at the first install, the holds it adds, the accounts and
groups `users_groups` creates, the groups and keys it adds to pre-existing
accounts, and the `tailscale serve` entries. Preflight reads the record to
tell a component pinka installed from one that was there before; uninstall
(§5.4) removes only the added part.

On a host that pinka provisioned before the record existed
(`/etc/pinka/bootstrap-state.json` is present) the record is marked
`legacy`: its "pre-existing" part then contains pinka's own items, so it
decides nothing, and pinka's marks decide as before.

## Facts it sets

| Fact | Content |
| --- | --- |
| `pinka_preexisting` | the record's `preexisting` part; empty for a legacy record |

## Module

`library/pinka_install_record.py` (`state: init`, `add` or `read`; check
mode writes nothing). `ansible.cfg` puts the role's library on the module
path, so every role and playbook can call it.

## Variables

See `meta/argument_specs.yml`.

## Idempotency

The record is written once; `add` writes only when an item is new, so a
second run reports no change.
