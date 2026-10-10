---
title: How to replace the lab's platform
type: how-to
audience: [operator]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: Deploy a platform with another name in place of the active one, and later remove the replaced platform's data for good.
---

# How to replace the lab's platform

A lab runs one platform at a time. A release with another name than the
active platform is refused unless you name the platform it replaces. The
replaced platform stops: its units, sockets, configuration and CLI go, its accounts
are locked, its database roles can no longer log in and its Incus
certificates leave the trust store. Its releases, state, restricted area and
database stay on disk until you purge them.

!!! note "Prerequisites"
    - A lab host with an active platform
      ([How to deploy a platform](deploy-platform.md)).
    - The new platform's sources in the host_vars file (`platform`,
      `content`).
    - A recent backup, if you mean to purge the old platform afterwards.

## Replace the platform

1. Check the new release; the check also confirms which platform it replaces:

    <!-- qa:skip reason="fetches from the deployer's repository on the lab host" -->
    ```bash
    sudo ./bootstrap/bootstrap.sh --machine <machine> --inspect-platform --replace-platform <old>
    ```

    `<machine>` is the name of this lab host's host_vars file and `<old>`
    the name of the active platform (`name` in
    `/etc/pinka/platform/active.json`). A wrong name is refused with exit
    code 65.

2. Deploy it:

    <!-- qa:skip reason="provisions the lab host" -->
    ```bash
    sudo ./bootstrap/bootstrap.sh --machine <machine> --replace-platform <old>
    ```

    `<machine>` and `<old>` are the same as in step 1. The replaced platform
    is recorded in `/var/lib/pinka/platforms/<old>/retired.json`.

## Purge the replaced platform

1. Remove its releases, content, state, restricted area and database. This
   cannot be undone:

    <!-- qa:skip reason="removes data on the lab host" -->
    ```bash
    sudo pinka platform purge <old>
    ```

    `<old>` is the replaced platform's name; type it again when asked.
    The active platform is never purged. Its accounts stay locked, its
    secrets directories stay in `/etc/pinka/secrets/`, and its published
    signing keys stay in `/srv/pinka/shared/signing-keys/`; the command
    lists what remains.

2. To remove the replaced platform's secrets directories as well, add
   `--remove-secrets`:

    <!-- qa:skip reason="removes data on the lab host" -->
    ```bash
    sudo pinka platform purge <old> --remove-secrets
    ```

    `<old>` is the replaced platform's name.

## Verify

1. Check the active platform and the purge record:

    <!-- qa:run env=E2 -->
    ```bash
    cat /etc/pinka/platform/active.json
    sudo cat /var/lib/pinka/platforms/<old>/retired.json
    ```

    `<old>` is the replaced platform's name. `active.json` names the new
    platform; `retired.json` has `purged_at` set after a purge.
