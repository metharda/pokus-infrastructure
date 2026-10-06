---
title: How to add, remove or lock a researcher
type: how-to
audience: [operator]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: Add a researcher account, rotate an SSH key, or lock a researcher who left, on a provisioned lab host.
---

# How to add, remove or lock a researcher

Researcher accounts, their groups and their SSH keys are managed by the
`users_groups` role from two inputs on the lab host: the `researchers` list in
the machine's host_vars and one public key file per researcher. Changing
either and re-running bootstrap for that role applies the change. Accounts are
never deleted: a researcher who leaves is locked and their files are kept.

!!! note "Prerequisites"
    - A provisioned lab host and an admin account on it (member of `pokus-admin`).
    - The researcher's SSH public key (for a new researcher or a key rotation).
    - The infra directory of the checkout that bootstrap runs from.

!!! warning "Keys and names stay on the host"
    Key files and the real researcher list are installation data. They live
    only in the lab's own configuration (`users/*.pub` and
    `ansible/inventory/host_vars/<machine>.yml` are excluded by `.gitignore`)
    and are never committed to the public project.

## Add a researcher

!!! tip "Shortcut"
    `sudo pokus-lab user add <name> --key-file <key-file>` (with `--admin` for
    sudo) does steps 1 and 2 and prints the command of step 3. It needs the
    `researchers` list written with one `{name: ...}` entry per line, as below,
    and never changes the host itself.

1. Copy the researcher's public key into the infra checkout, one file per
   researcher (it may hold several keys, one per line):

    <!-- qa:skip reason="changes the lab host's configuration" -->
    ```bash
    cd <infra-checkout>
    sudo install -m 0644 <key-file> users/<name>.pub
    ```

    `<infra-checkout>` is the infra directory bootstrap runs from, `<key-file>`
    the key you received, and `<name>` the new account name (lowercase letters
    and digits).

2. Add the researcher to `researchers` in
   `ansible/inventory/host_vars/<machine>.yml`. The list in host_vars replaces
   the default list completely, so it must contain every researcher:

    <!-- qa:skip reason="example of a host_vars file" -->
    ```yaml
    researchers:
      - {name: researcher1, admin: true, shell: /bin/bash, keys_file: users/researcher1.pub}
      - {name: researcher2, admin: false, shell: /bin/bash, keys_file: users/researcher2.pub}
      - {name: <name>, admin: false, shell: /bin/bash, keys_file: users/<name>.pub}
    ```

    `<name>` is the account name from step 1; `<machine>` is the name you pass
    to bootstrap with `--machine`. Set `admin: true` only for researchers who
    need sudo.

3. Apply the accounts and create the researcher's directory:

    <!-- qa:skip reason="provisions the lab host" -->
    ```bash
    sudo ./bootstrap/bootstrap.sh --machine <machine> --tags users_groups,layout
    ```

    `<machine>` is the name of this lab host's host_vars file.

## Rotate a researcher's SSH key

`sudo pokus-lab user keys <name> --key-file <key-file>` replaces the key file
for you. Either way, replace the key file and apply the role again. The file is the only source of
the account's `authorized_keys`: a key that is no longer in the file is
removed from the host.

<!-- qa:skip reason="changes the lab host's configuration" -->
```bash
sudo install -m 0644 <key-file> users/<name>.pub
sudo ./bootstrap/bootstrap.sh --machine <machine> --tags users_groups
```

`<key-file>` is the new key, `<name>` the account and `<machine>` this host.

## Lock a researcher who left

!!! tip "Shortcut"
    `sudo pokus-lab user remove <name>` does step 1 and prints the command of
    step 2.

1. In `ansible/inventory/host_vars/<machine>.yml`, remove the researcher from
   `researchers` and add the name to `researchers_removed`:

    <!-- qa:skip reason="example of a host_vars file" -->
    ```yaml
    researchers_removed: [<name>]
    ```

    `<name>` is the account to lock; `<machine>` is this host.

2. Apply the role:

    <!-- qa:skip reason="provisions the lab host" -->
    ```bash
    sudo ./bootstrap/bootstrap.sh --machine <machine> --tags users_groups
    ```

    `<machine>` is this host. The account's password is locked, its shell set
    to `nologin`, the account expired, and it is taken out of `pokus-admin`.
    Its home directory and `/srv/pokus/users/<name>/` are kept; move or
    archive them yourself if your data policy requires it.

## Verify the change

Check the account; for a new researcher the groups include `pokus`, for an
admin also `pokus-admin`, and never `docker` or `incus-admin`:

<!-- qa:run env=E2 -->
```bash
id <name>
sudo passwd -S <name>
```

`<name>` is the account you changed. For a locked researcher, the second
field of the `passwd -S` output is `L` (locked). For a new or rotated key,
the researcher should now be able to log in with `ssh <name>@<machine>` over
the tailnet, where `<machine>` is the host's tailnet name.
