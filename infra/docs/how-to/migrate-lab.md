---
title: How to migrate the lab to a new machine
type: how-to
audience: [operator]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: Move a lab to new hardware with a fresh installation and a restore from backup, replace its tailnet node and hand over the run-manifest signing key.
---

# How to migrate the lab to a new machine

A lab moves by building the new machine from the same configuration and
restoring the old machine's last backup onto it. Nothing is copied between
the machines directly: the backup repository carries the research space and
the database, and your lab's private configuration repository carries the
host_vars file, the researchers' keys and the encrypted vault.

!!! note "Prerequisites"
    - The old lab with a configured backup repository (see
      [How to back up, restore and run a restore drill](backup-and-restore.md)).
    - The lab's configuration: the branch with `ansible/inventory/host_vars/<machine>.yml`,
      the researchers' key files and the vault, and the vault password.
    - A new machine that preflight accepts (see
      [How to check whether a machine can host the lab](check-a-host.md)).
    - Admin access to the lab's tailnet.

## Take the last backup on the old machine

1. Stop the lab so that no run is half-written, then back up:

    <!-- qa:run env=E2 -->
    ```bash
    sudo pokus-lab stop --timeout 1800
    sudo pokus-lab backup now
    sudo pokus-lab backup list
    ```

    The last line of the list is the new snapshot; note its ID.

2. Make sure the configuration is committed and pushed to your private
   configuration repository (the installer's **Commit files** writes it on the
   branch `machine/<machine>`).

## Free the machine's name in the tailnet

The new machine joins the tailnet under the same name. Remove the old
machine's node in the tailnet's admin console (or log it out with
`sudo tailscale logout` on the old machine), otherwise the new node gets a
different name and the researchers' connections break.

!!! warning "Downtime"
    From here on the old machine is no longer reachable for researchers. Keep
    it powered off rather than wiping it until the new lab has passed doctor.

## Build the new machine and restore

1. On the new machine, clone the repository and check out your configuration
   branch, as for a first installation:

    <!-- qa:skip reason="needs the repository address" -->
    ```bash
    git clone <repository-url> ~/pokus
    cd ~/pokus/infra
    git checkout machine/<machine>
    ```

    `<repository-url>` is your lab's private configuration repository and
    `<machine>` the machine name of the lab.

2. Adapt what differs on the new hardware in the host_vars file: the storage
   pool (`incus_pool_source`, `incus_pool_device` and its confirmation), the
   CPU sets, and `expected` on WSL2. Commit the change.

3. Install and restore in one run:

    <!-- qa:skip reason="provisions and restores the host" -->
    ```bash
    sudo ./bootstrap/bootstrap.sh --machine <machine> --restore
    ```

    `<machine>` is the machine name of the lab, as above. Bootstrap
    provisions the machine from the host_vars file, then lists the
    snapshots of this machine and asks which to restore; enter the snapshot
    ID you noted. With the installer, choose *Restore from backup* instead.
    Join the tailnet when the login URL appears.

## Hand over the run-manifest signing key

The signing key never leaves a machine: the new machine generates its own key
and publishes its public key as
`/srv/pokus/shared/signing-keys/<machine>-<date>.pub`. The old machine's
public keys come back with the research space, so manifests signed on the
old machine stay verifiable. Check that both are there:

<!-- qa:run env=E2 -->
```bash
ls -l /srv/pokus/shared/signing-keys/
```

Tell whoever verifies your manifests (for example the QA team) that the new
key belongs to the same lab from now on.

## Pair the inference node again

The pairing material of split inference is a secret of each machine and is
not in the backup. If the lab used a separate inference node, pair the nodes
again ([How to run inference on a separate GPU node](pair-inference-node.md)).

## Verify the migration

You should now have the lab running on the new machine with the old data:

<!-- qa:run env=E2 -->
```bash
sudo pokus-lab doctor
sudo pokus-lab fingerprint
```

Doctor exits with 0, or 1 when only warnings remain. The fingerprint differs
from the old machine's, because the hardware and the host changed: runs from
before and after the migration are not comparable without saying so. A
researcher can log in over the tailnet with the old name, and their files in
`/srv/pokus` are back. Then wipe or reuse the old machine.
