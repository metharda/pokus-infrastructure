# uninstall

Removes what pinka installed and keeps the lab's data.
Applied only by `ansible/uninstall.yml`, which `bootstrap.sh --uninstall`
runs twice: in mode `plan` the module `pinka_uninstall` reads the install
record, the lab description, the active and retired platforms and
the host, and writes the plan (JSON for the installer and `--json`, text
for people); nothing changes, and `--dry-run` stops there. In mode `apply`
it removes the plan the operator confirmed, phase by phase:

1. `platform`: `pinka stop` (the platform's drain hook, the GPU arbiter
   idle, `pinka-platform.target` and `pinka-lab.target` stopped);
2. `units`: pinka's units, timers, sockets, slices and targets and the
   platforms';
3. `compose`: pinka's containers, Compose networks and pulled images, never
   one another container uses;
4. `incus`: instances, images, volumes and profiles in pinka's projects,
   the projects, the networks, the ACL, the trust entries, the pool last; on
   an Incus that existed before pinka only what the record names, and its
   server setting set back;
5. `firewall`: the tables `inet pinka_*` (the access guard among them, so
   every listener closed with consent is reachable again), the
   `DOCKER-USER` rules for pinka's bridges, pinka's firewalld zones and
   ufw rules, `tailscale0`'s zone and SSH in the owner's zones as recorded;
6. `tailscale`: the serve entries pinka added, a logout only when pinka
   joined the node;
7. `packages`: holds, repository files and keys, pinka's packages (never one
   another package needs), then the dependencies absent at the first install
   that no package needs any more;
8. `accounts`: without `--purge-data`, the lab root locked to root
   (root:root 0700 without ACL entries) as an extra defense, and the note
   of the kept accounts and groups (`/var/lib/pinka/kept-accounts.json`,
   root only: their IDs and what the lock changes, which the next install
   undoes for the accounts it manages). Then memberships and keys pinka
   added to accounts that were there before it go, and memberships pinka
   took away come back. Without `--purge-data` the platforms' accounts and
   the accounts pinka created are then locked (password locked, no login
   shell, expired), and every group is kept, so that no UID or GID that
   owns kept data becomes free. With `--purge-data` the accounts and groups
   go, with the data (a group of Docker's or Incus's packages only with
   that component's data);
9. `dropins`: the shared files as the record kept them (the SSH `Match`
   block's drop-in, the `Include` line pinka added to `sshd_config`,
   sudoers, journald, sysctl, the nouveau blacklist, ...), the time zone and
   kernel settings set back, unit states, the initramfs;
10. `paths`: pinka's paths; the data, the logs, the remote side and the note
    of the kept accounts kept; uninstall's own records last (pinka's state,
    `/etc/pinka` with the machine name, the install record), and only when
    no item before them failed: each phase gets the results so far, and a
    run that did not finish keeps them for the next. Pinka's state holds the
    homes of infra's and the platforms' accounts (below `/var/lib/pinka/`):
    they go with it, also when the accounts stay. Bootstrap removes
    `/opt/pinka` after the play.

Every item ends `removed`, `restored`, `locked`, `kept`, `already_gone` or
`failed`, and the report (`/var/log/pinka-uninstall/<UTC>.json`) lists
them. Without a usable record only pinka's names go (and what the note of
the kept accounts names) and the shared components stay unless
`--remove-component` names them. The package manager's commands live in
`vars/<family>.yml`.
