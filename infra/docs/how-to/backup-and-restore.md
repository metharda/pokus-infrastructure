---
title: How to back up the lab, restore it, and run a restore drill
type: how-to
audience: [operator]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: Configure the restic repository, check that backups run, restore a snapshot with bootstrap, and rehearse the restore on a spare machine.
---

# How to back up the lab, restore it, and run a restore drill

The `backup` role dumps the platform database every night (when the
platform's manifest sets `backup.database`) and backs up with restic, to a
repository on another machine: infra's own directories under `/srv/pinka`
(`images`, `datasets`, `shared`, `users`, `backups/pg`; `backup_include`),
the restricted area, the training datasets, the adapters with their lineage,
the platform state directories that the manifest lists in `backup.paths`,
and `/etc/pinka` without its secrets. A state directory the manifest leaves
out is not backed up; doctor lists it (`backup.platform_state`). Until a repository is configured the lab runs without backups and
the metric `pinka_backup_configured` is 0. Restoring is part of bootstrap:
`bootstrap.sh --restore` provisions the host and then restores a snapshot.

!!! note "Prerequisites"
    - An admin account on the lab host (member of `pinka-admin`) and the infra
      directory bootstrap runs from.
    - A backup server you control with an SFTP account for the lab (owner
      answer E4; a restic rest-server works too), reachable from the lab host,
      for example over the tailnet.
    - The lab's vault (`ansible/group_vars/all/vault.yml`) and its password.

## Configure the repository

1. Put the repository and a new, long password into the vault:

    <!-- qa:skip reason="edits the encrypted vault" -->
    ```bash
    ansible-vault edit ansible/group_vars/all/vault.yml
    ```

    Set `vault_restic_repository` to
    `sftp:<user>@<backup-host>:<path>` and `vault_restic_password` to a
    password that you also store outside the lab. `<user>` is the SFTP
    account, `<backup-host>` the server's name and `<path>` the repository
    directory on it. Without the password, no backup can ever be read.

2. Read the backup server's host key and check its fingerprint against the
   one the server shows on its own console:

    <!-- qa:skip reason="needs the backup server" -->
    ```bash
    ssh-keyscan -t ed25519 <backup-host> | tee /dev/stderr | ssh-keygen -lf -
    ```

    `<backup-host>` is the name used in the repository location.

3. Add the line that `ssh-keyscan` printed to
   `ansible/inventory/host_vars/<machine>.yml`:

    <!-- qa:skip reason="example of a host_vars entry" -->
    ```yaml
    backup_sftp_known_hosts:
      - "<host-key-line>"
    ```

    `<machine>` is this lab host's host_vars file, `<host-key-line>` the
    line from step 2.

4. Apply the role and authorize the lab's key on the backup server:

    <!-- qa:skip reason="provisions the lab host" -->
    ```bash
    sudo ./bootstrap/bootstrap.sh --machine <machine> --tags backup
    ```

    `<machine>` is the host_vars file from step 3. The role prints the
    public key it generated for the repository (its comment is
    `pinka-backup@` and the lab's `machine_name`). Add it to the SFTP account's
    `authorized_keys` on the backup server. The first backup initializes the
    repository.

## Check that backups run

Start a dump and a backup now instead of waiting for the night:

<!-- qa:run env=E2 -->
```bash
sudo systemctl start pinka-pgdump.service pinka-backup.service
sudo /usr/local/libexec/pinka/backup snapshots | jq -r '.[] | [.short_id, .time] | @tsv'
```

The list ends with the snapshot you just made. The timers run the dump at
02:30, the backup at 03:00 and the retention job on Sundays
(`backup_*_schedule`); a run missed while the host was off happens after the
next boot. `pinka_backup_job_success{job="backup"}` and
`pinka_backup_job_last_success_timestamp_seconds` show the result in Grafana.

## Restore a snapshot

A restore runs the platform's `drain` hook and stops the lab, restores the
research space directories of the snapshot and the parts of `/etc/pinka`
that bootstrap cannot recreate (vLLM deployments, QA keys and attestations),
loads the newest database dump, re-imports the golden images from their
exports and checks the model files by hash (downloading what is missing).
Then it deploys exactly the commit and tree of the platform release and of
each content package that the snapshot's `active.json` records, from the
repositories in the host_vars file (with `require_signed_tags`, through the
recorded tag, which must still point to that commit), starts the lab and
waits for the platform's `health` hook. If a repository no longer has the
recorded commit, or the release's directory has another tree there, the
restore stops before anything on the host changes: `source-ref`,
`source-commit` or `source-tree` name the package. Files newer than the
snapshot are kept.

1. On a new machine, prepare it as for a first installation with the same
   host_vars file and vault (the vault holds the repository and its
   password).
2. Run bootstrap with the restore:

    <!-- qa:skip reason="restores the lab host" -->
    ```bash
    sudo ./bootstrap/bootstrap.sh --machine <machine> --restore
    ```

    `<machine>` is the host_vars file of the backed-up lab. An interactive
    run lists the snapshots of this machine and asks which one
    to restore (empty for the newest). A non-interactive run needs
    `restore_snapshot: <id>` (or `latest`) in the host_vars file, where
    `<id>` is a snapshot ID or its first eight characters.

The play ends with the restored snapshot and the result of `pinka
doctor`.

## Run a restore drill

Rehearse the restore regularly on a spare machine (a VM is enough), never on
the production host.

1. Restore as above onto the spare machine.
2. Right after the restore, stop the spare machine's backup jobs: it uses the
   production repository under the same machine name, so its backups and its
   retention job would mix with production's snapshots.

    <!-- qa:skip reason="needs a restored spare machine" -->
    ```bash
    sudo systemctl disable --now pinka-backup.timer pinka-backup-prune.timer
    ```

3. Compare a few facts with production:

    <!-- qa:skip reason="needs a restored spare machine" -->
    ```bash
    sudo incus image list --format csv --columns l | sort
    sudo ls /srv/pinka/backups/pg | tail -n 1
    ```

The golden image aliases and the newest dump must match those of the
production host at the time of the snapshot. Stop or delete the spare
machine afterwards, so that two labs never run with the same identity.

## Verify the backups

The newest snapshot is at most one day old and the last backup succeeded:

<!-- qa:run env=E2 -->
```bash
cat /var/lib/pinka/node-exporter/pinka_backup_backup.prom
```

`pinka_backup_job_success` is 1 and
`pinka_backup_job_last_success_timestamp_seconds` lies within the last 24
hours.

Related: [How to rotate secrets and certificates](rotate-secrets.md),
[How the lab comes up](../explanation/lab-session.md).
