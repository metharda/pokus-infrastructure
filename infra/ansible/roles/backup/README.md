# backup

Backups of the lab (BUILD_PROMPT §7.15, owner answer E4); tag `backup`.

- restic from `versions.lock.yml` (`binaries.restic`, bzip2-compressed,
  sha256-checked) in `/opt/pokus/restic/<version>/`, linked as
  `/usr/local/bin/restic`.
- The repository (`vault_restic_repository`) and its password
  (`vault_restic_password`) are files in `/etc/pokus/secrets/backup/` (0600
  root) that reach restic through `RESTIC_REPOSITORY_FILE` and
  `RESTIC_PASSWORD_FILE`. An SFTP repository uses a dedicated ed25519 key
  generated on the host (`ssh_key`; the role prints the public key to
  authorize) and the host keys of `backup_sftp_known_hosts`
  (`StrictHostKeyChecking=yes`). The password file is written once; a
  different vault value is reported and applied by `rotate.yml`.
- No repository means "configure later": the backup and prune timers stay
  disabled and `pokus_backup_configured` is 0.
- `/usr/local/libexec/pokus/backup` (configuration
  `/etc/pokus/backup/backup.json`):
  - `pgdump` (`pokus-pgdump.timer`, `full` and `lab` nodes): `pg_dump -Fc`
    through the PostgreSQL container to `/srv/pokus/backups/pg/`, checked
    with `pg_restore --list`, newest `backup_pgdump_keep` kept;
  - `run` (`pokus-backup.timer`): restic backup of `backup_include` below
    `/srv/pokus` (plus model weights and state directories when asked) and
    `/etc/pokus`, without the secret material of `backup_secret_paths` unless
    `backup_include_secrets` (with `backup_secrets_acknowledged`); the
    repository is initialized on first use;
  - `prune` (`pokus-backup-prune.timer`): `restic forget --prune` with
    `backup_retention`, for this host's snapshots;
  - `snapshots` and `restore`, used by `restore.yml`.
- The timers are `Persistent=true`, so a job missed while the host was off
  runs after boot. Every job writes
  `/var/lib/pokus/node-exporter/pokus_backup_<job>.prom`
  (`pokus_backup_job_success`, last run and last success times, sizes).

## Variables

See `meta/argument_specs.yml`.

## Feature flags

None; the dump runs where PostgreSQL runs (`node_role` `full` and `lab`).

## Idempotency

Downloads are checked by sha256 and skipped when present; files and units are
rewritten only when their content changes; the password file is never
rewritten.
