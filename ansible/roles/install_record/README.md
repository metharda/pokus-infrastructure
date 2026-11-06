# install_record

Runs right after `preflight_gate`, before any change to the host.
It writes `/var/lib/pinka/install-record.json` (0600
root) once, at the first install, with what existed before pinka:

- the components preflight found (`host_profile.components`: Docker Engine,
  Tailscale, LXD, Podman, Incus, the NVIDIA driver, with their versions and
  state);
- the full package list with versions, the enabled and the masked units, the
  accounts and the groups with their members;
- the content of every file in `install_record_managed_files` that already
  existed, so that uninstall can restore what pinka edited;
- the access to the host (`host_profile.access`): the
  listeners outside loopback, the firewall manager, the login accounts that
  are not pinka's, dedicated or shared, and the install's session.

Its pre-existing part is never rewritten. The roles then add what they
create through the module `pinka_install_record` (`state: add`): a
component before pinka installs or joins it (`components`), the packages
the `pkg` role installs that were absent at the first install, the holds
it adds, the accounts and groups `users_groups` creates, the groups and
keys it adds to pre-existing accounts, the `tailscale serve` entries, and
the pre-existing access closed with the owner's consent (`closed_access`:
`tcp/8080`, `install_path`).
`state: remove` takes a listener out of `closed_access` when the owner no
longer wants it closed, and nothing else. Preflight reads the record to
tell a component pinka installed from one that was there before; uninstall
removes only the added part.

On a host that pinka provisioned before the record existed
(`/etc/pinka/bootstrap-state.json` is present) the record is marked
`legacy`: its "pre-existing" part then contains pinka's own items, so it
decides nothing, and pinka's marks decide as before.

An uninstall without `--purge-data` keeps the accounts and groups that own
the lab's data, the accounts locked, and notes them in
`/var/lib/pinka/kept-accounts.json` (root only) with their IDs and what the
lock changed. At the next install `init` takes them over: an account or
group that still has the noted UID or GID is pinka's again (in the added
part, not in the pre-existing one; a group of Docker's or Incus's packages
only leaves the pre-existing part), and the accounts' notes go into the
record (`kept_accounts`). When uninstall did not finish and kept the
record, they are added to it. Then the file goes; the play shows what was
taken over. `state: unlock` undoes the lock of the accounts a role names,
right before it manages them: `users_groups` names infra's accounts and
the researchers in `researchers`, the platform role the accounts it
renders. The login shell and the expiry come back as noted, and the
password is unlocked only where uninstall locked it; an account whose lock
someone changed since, or another account with the name, is left as it
is. Each named note then leaves the record. A kept account no role names
stays locked.

## Facts it sets

| Fact | Content |
| --- | --- |
| `pinka_preexisting` | the record's `preexisting` part; empty for a legacy record |
| `pinka_closed_access` | the record's `added.closed_access` |

Before anything changes, the role checks every entry of
`firewall_close_listeners` against the recorded listeners (the filter
`pinka_listener_rules` names a wrong one).

## Module

`library/pinka_install_record.py` (`state: init`, `add`, `remove`, `unlock` or
`read`; check mode writes nothing). `ansible.cfg` puts the role's library on the module
path, so every role and playbook can call it.

## Variables

See `meta/argument_specs.yml`.

## Idempotency

The record is created once. `add` writes only when an item is new, `init`
on an existing record only when an uninstall kept something, and `unlock`
only when a named account has a note; so a second run reports no change.
