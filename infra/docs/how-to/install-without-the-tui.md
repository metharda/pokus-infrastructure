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
    - A clone of the pokus-infrastructure repository owned by your own user, with a clean
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
    sudo install -m 600 /dev/null /root/pinka-vault-pass
    read -rsp 'vault password: ' pass && echo "$pass" | sudo tee /root/pinka-vault-pass >/dev/null
    unset pass
    ```

4. Provide the secrets that the answers mark as *provided*, as environment
   variables. Everything else is generated into the vault:

    | Answers field | Variable |
    | --- | --- |
    | `remote_access.tailscale_login: auth_key` | `PINKA_SECRET_TAILSCALE_AUTHKEY` |
    | `backups.password: provided` | `PINKA_SECRET_RESTIC_PASSWORD` |
    | `secrets.grafana_admin: provided` | `PINKA_SECRET_GRAFANA_ADMIN_PASSWORD` |
    | `secrets.hf_token: provided` | `PINKA_SECRET_HF_TOKEN` |
    | `secrets.platform: {<service>.<name>: provided}` | `PINKA_SECRET_PLATFORM_<SERVICE>_<NAME>` |

    A platform secret's variable is its key in capitals with every other
    character than a letter or digit replaced by `_`: the secret `api_token`
    of the service `ctl` is `PINKA_SECRET_PLATFORM_CTL_API_TOKEN`. The
    secrets a release names are listed by the TUI's Platform screen, or by
    `pinka-platform inspect-release` (see
    [pinka-platform](../reference/cli/pinka-platform.md)).

    Read them without echoing them, so they stay out of the shell history:

    <!-- qa:skip reason="reads a secret from the terminal" -->
    ```bash
    read -rsp 'Tailscale auth key: ' PINKA_SECRET_TAILSCALE_AUTHKEY && export PINKA_SECRET_TAILSCALE_AUTHKEY
    ```

    On an installed lab, a secret that the vault already holds is kept; a
    variable replaces it.

5. With a platform release over SSH (`platform.repo` or a `content` source
   such as `git@github.com:example-org/example-platform.git`), create this
   host's read-only deploy key once and add its public half to the Git host
   as a read-only deploy key. The installation uses the same key:

    <!-- qa:skip reason="creates a key on the host" -->
    ```bash
    sudo install -d -m 700 /var/lib/pinka/git
    sudo ssh-keygen -q -t ed25519 -N '' -C pinka-deploy@<machine> -f /var/lib/pinka/git/deploy_key
    sudo cat /var/lib/pinka/git/deploy_key.pub
    ```

    `<machine>` is the machine name in the answers file. GitHub's host keys are pinned; for another SSH host, put its
    `known_hosts` lines into `source_trust.known_hosts`.

6. On a host with Secure Boot and no NVIDIA driver, consent to the Machine
   Owner Key (MOK) enrollment in the answers file, next to
   `install_driver: auto`:

    ```yaml
    gpu:
      install_driver: auto
      accept_mok_enrollment: true
    ```

    Bootstrap then also needs a one-time MOK password, which you choose now
    and type again at the blue MOK Manager screen after the reboot. It is
    never stored: bootstrap reads it from its standard input (next section).

## Run the installation

1. Run bootstrap with the answers, passing the secret variables through
   `sudo`:

    <!-- qa:skip reason="provisions the host" -->
    ```bash
    cd <checkout>/infra
    sudo --preserve-env=PINKA_SECRET_TAILSCALE_AUTHKEY ./bootstrap/bootstrap.sh \
      --answers installer-answers/<machine>.yml --non-interactive \
      --vault-password-file /root/pinka-vault-pass
    ```

    `<checkout>` is the directory of your clone and `<machine>` the
    machine name in the answers file. List every variable you set in
    `--preserve-env`, separated by commas. Bootstrap checks the answers
    before it changes anything: a problem ends with exit code 77 and lists
    every wrong field. With a platform, it then inspects the release, its
    content packages and its settings with the deployment's own checks (this
    reads the Git host; the release's code never runs) and stops the same way
    with the reasons when they fail; a release of another platform than the
    active one needs `--replace-platform <active platform>` on the same
    command line. It then writes `host_vars/<machine>.yml`, the
    researchers in `group_vars/all/main.yml`, `users/*.pub`, the encrypted
    vault and the answers file, and runs the installation. In restore mode
    it restores the snapshot of `restore.snapshot` afterwards.

    With `accept_mok_enrollment: true`, pipe the one-time MOK password in and
    add `--mok-password-stdin`:

    <!-- qa:skip reason="provisions the host" -->
    ```bash
    read -rsp 'one-time MOK password: ' mok && echo
    printf '%s\n' "$mok" | sudo --preserve-env=PINKA_SECRET_TAILSCALE_AUTHKEY \
      ./bootstrap/bootstrap.sh --answers installer-answers/<machine>.yml \
      --non-interactive --vault-password-file /root/pinka-vault-pass --mok-password-stdin
    unset mok
    ```

    `<machine>` is the same machine name. The installation stops with exit
    code 76 after importing the key; reboot, enroll it and run the same
    command again (see
    [Bare-metal hosts](../providers/baremetal.md#secure-boot-and-reboots)).

2. If the answers generated a restic password, bootstrap prints a note. Copy
   `vault_restic_password` out of the vault and store it outside the host:
   without it, no backup can be restored.

    <!-- qa:skip reason="needs the vault password" -->
    ```bash
    sudo /opt/pinka/ansible-venv/bin/ansible-vault view \
      --vault-password-file /root/pinka-vault-pass ansible/group_vars/all/vault.yml
    ```

3. Commit the written files on a branch of your lab's private configuration
   repository, never the public project.

## Verify the result

You should now have a lab that doctor accepts, and a second replay changes
nothing:

<!-- qa:skip reason="needs a provisioned lab host" -->
```bash
sudo pinka doctor
echo "exit code: $?"
git status --short
```

The exit code is 0, or 1 when only warnings remain (see
[Fix a failed doctor check](fix-doctor-checks.md)), and `git status` lists
no file after you committed them.
