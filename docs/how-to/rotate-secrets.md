---
title: How to rotate secrets and certificates
type: how-to
audience: [operator]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: Change vault passwords with bootstrap, and rotate the host-generated database passwords, the Incus client certificates, the backup repository password and the run-manifest signing key with rotate.yml.
---

# How to rotate secrets and certificates

Secrets of the lab come from two places. Passwords in the vault (the
platform's database roles, the reader, Grafana) change when you edit the
vault and run bootstrap. Secrets the host generated itself (the database
superuser and exporter passwords, the Incus client certificates of the
platform services and Prometheus) and the backup repository's password change
with `ansible/rotate.yml`. Rotating the database passwords or the
certificates restarts the lab, so do it in a maintenance window.

!!! note "Prerequisites"
    - An admin account on the lab host (member of `pinka-admin`) and the infra
      directory bootstrap runs from.
    - The vault's password.
    - No campaign running: stop or drain the lab first.

## Change a password that lives in the vault

1. Edit the value in the vault (for example
   `vault_postgres_passwords.potka_orch` or
   `vault_grafana_user_passwords.researcher1`):

    <!-- qa:skip reason="edits the encrypted vault" -->
    ```bash
    ansible-vault edit ansible/group_vars/all/vault.yml
    ```

2. Apply it:

    <!-- qa:skip reason="provisions the lab host" -->
    ```bash
    sudo ./bootstrap/bootstrap.sh --machine <machine> --tags postgres,observability
    ```

    `<machine>` is this lab host's host_vars file. The PostgreSQL role sets
    the new password of every role whose stored secret no longer matches and
    rewrites the password files; restart the platform's services afterwards
    so that they read them.

## Rotate the host's own secrets

Run `rotate.yml` with the pinned Ansible of bootstrap. Without `--tags` it
rotates everything; `postgres`, `incus`, `restic` and `signing` select parts:

<!-- qa:skip reason="rotates secrets on the lab host" -->
```bash
cd ansible
sudo ANSIBLE_CONFIG=$PWD/ansible.cfg /opt/pinka/ansible-venv/bin/ansible-playbook \
  -i inventory/hosts.yml rotate.yml -e machine=<machine> --ask-vault-pass --tags postgres,incus
```

`<machine>` is this lab host's host_vars file. The play stops
`pinka-lab.target`, then:

- `postgres`: sets a new superuser password while the old one still works
  (the new one is staged in
  `/etc/pinka/secrets/containers/postgres-superuser.new` until then), writes a
  new exporter password, and recreates the database containers so that they
  see the new secret files;
- `incus`: issues new client certificates for the platform services and
  Prometheus, trusts them, and removes the old certificates from the Incus
  trust store, so the old keys stop working;
- `signing` (with the platform enabled): generates a new run-manifest signing
  key for the orchestrator and publishes its public key in
  `/srv/pinka/shared/signing-keys/<machine>-<date>.pub`; the old public keys
  stay published, so manifests signed before the rotation remain verifiable;

and starts the lab again.

## Change the backup repository's password

A repository password cannot simply be overwritten: the repository must
learn the new one first.

1. Put the new password into `vault_restic_password` and keep it outside the
   lab as well (`ansible-vault edit ansible/group_vars/all/vault.yml`).
2. Run `rotate.yml` with the tag `restic` (command above, `--tags restic`).
   It runs `restic key passwd`, which adds a key for the new password and
   removes the old one, and only then replaces the password file. If it
   fails, the repository and the file keep the old password.

## Verify the rotation

The lab is up again:

<!-- qa:run env=E2 -->
```bash
systemctl is-active pinka-lab.target pinka-health-gate.service
sudo /usr/local/libexec/pinka/backup snapshots | jq length
```

Both units are `active`, and the backup helper still lists the snapshots
(with the new password, after a `restic` rotation). After an `incus`
rotation, `sudo incus config trust list --columns nfi` (name, fingerprint,
issue date) shows exactly one `pinka-<service>` entry per service, issued
today.

Related: [How to back up the lab, restore it, and run a restore drill](backup-and-restore.md),
[Integration contract](../contracts.md#paths).
