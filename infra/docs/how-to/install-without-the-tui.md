---
title: How to install a lab from an answers file
type: how-to
audience: [operator]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: Install a lab host without the TUI, from a saved answers file and secrets in the environment.
---

# How to install a lab from an answers file

An answers file holds every non-secret choice of the installer. Bootstrap
replays it without any prompt: it writes the same files the TUI writes and
runs the same installation. Use it on a terminal the TUI cannot use, for a
second identical host, or to automate a reinstall.

!!! note "Prerequisites"
    - A host that preflight accepts (see
      [How to check whether a machine can host the lab](check-a-host.md)).
    - A clone of the Pokus repository owned by your own user, with a clean
      `infra/` directory.
    - `sudo` rights on the host.
    - An answers file: exported with **Export answers only** on the TUI's
      Review screen, the one an earlier installation wrote to
      `installer-answers/<machine>.yml`, or written by hand following the
      [answers file reference](../reference/answers.md).

## Prepare the answers and the secrets

1. Put the answers file into the checkout, for example as
   `installer-answers/<machine>.yml`, and check the machine name, the storage
   section and the researchers. `<machine>` is the machine name in the file.

    !!! danger "Data loss"
        With `storage.source: device`, the disk in `storage.device` is
        formatted as the Incus storage pool. `storage.device_confirm` must
        repeat the same `/dev/disk/by-id/` path, or nothing is formatted.

2. Put each researcher's public keys into `users/<name>.pub`, one key per
   line, unless the files already exist.

3. Write the vault password into a root-only file. For a new lab, this
   password encrypts the new vault; for an installed lab, it must open the
   existing one:

    <!-- qa:skip reason="reads a password from the terminal" -->
    ```bash
    sudo install -m 600 /dev/null /root/pokus-vault-pass
    read -rsp 'vault password: ' pass && echo "$pass" | sudo tee /root/pokus-vault-pass >/dev/null
    unset pass
    ```

4. Provide the secrets that the answers mark as *provided*, as environment
   variables. Everything else is generated into the vault:

    | Answers field | Variable |
    | --- | --- |
    | `remote_access.tailscale_login: auth_key` | `POKUS_SECRET_TAILSCALE_AUTHKEY` |
    | `backups.password: provided` | `POKUS_SECRET_RESTIC_PASSWORD` |
    | `secrets.grafana_admin: provided` | `POKUS_SECRET_GRAFANA_ADMIN_PASSWORD` |
    | `secrets.hf_token: provided` | `POKUS_SECRET_HF_TOKEN` |

    Read them without echoing them, so they stay out of the shell history:

    <!-- qa:skip reason="reads a secret from the terminal" -->
    ```bash
    read -rsp 'Tailscale auth key: ' POKUS_SECRET_TAILSCALE_AUTHKEY && export POKUS_SECRET_TAILSCALE_AUTHKEY
    ```

    On an installed lab, a secret that the vault already holds is kept; a
    variable replaces it.

## Run the installation

1. Run bootstrap with the answers, passing the secret variables through
   `sudo`:

    <!-- qa:skip reason="provisions the host" -->
    ```bash
    cd <pokus-checkout>/infra
    sudo --preserve-env=POKUS_SECRET_TAILSCALE_AUTHKEY ./bootstrap/bootstrap.sh \
      --answers installer-answers/<machine>.yml --non-interactive \
      --vault-password-file /root/pokus-vault-pass
    ```

    `<pokus-checkout>` is the directory of your clone. List every variable
    you set in `--preserve-env`, separated by commas. Bootstrap checks the
    answers before it changes anything: a problem ends with exit code 77 and
    lists every wrong field. It then writes `host_vars/<machine>.yml`, the
    researchers in `group_vars/all/main.yml`, `users/*.pub`, the encrypted
    vault and the answers file, and runs the installation. In restore mode
    it restores the snapshot of `restore.snapshot` afterwards.

2. If the answers generated a restic password, bootstrap prints a note. Copy
   `vault_restic_password` out of the vault and store it outside the host:
   without it, no backup can be restored.

    <!-- qa:skip reason="needs the vault password" -->
    ```bash
    sudo /opt/pokus/ansible-venv/bin/ansible-vault view \
      --vault-password-file /root/pokus-vault-pass ansible/group_vars/all/vault.yml
    ```

3. Commit the written files on a branch of your lab's private configuration
   repository, never the public project.

## Verify the result

You should now have a lab that doctor accepts, and a second replay changes
nothing:

<!-- qa:skip reason="needs a provisioned lab host" -->
```bash
sudo pokus-lab doctor
echo "exit code: $?"
git status --short
```

The exit code is 0, or 1 when only warnings remain (see
[Fix a failed doctor check](fix-doctor-checks.md)), and `git status` lists
no file after you committed them.
