# uninstall

Removes what pinka installed and keeps the lab's data (BUILD_PROMPT §5.4).
Applied only by `ansible/uninstall.yml`, which `bootstrap.sh --uninstall`
runs twice: in mode `plan` the module `pinka_uninstall` reads the install
record (§7.21), the lab description, the active and retired platforms and
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
8. `accounts`: memberships and keys pinka added, the platforms' accounts and
   groups, the accounts and groups pinka created; memberships pinka took
   away come back;
9. `dropins`: the shared files as the record kept them (the SSH `Match`
   block's drop-in, the `Include` line pinka added to `sshd_config`,
   sudoers, journald, sysctl, the nouveau blacklist, ...), the time zone and
   kernel settings set back, unit states, the initramfs;
10. `paths`: pinka's paths, the install record last; the data, the logs and
    the remote side kept. Bootstrap removes `/opt/pinka` after the play.

Every item ends `removed`, `restored`, `kept`, `already_gone` or `failed`,
and the report (`/var/log/pinka-uninstall/<UTC>.json`) lists them. Without a
usable record only pinka's names go and the shared components stay unless
`--remove-component` names them. The package manager's commands live in
`vars/<family>.yml`.
