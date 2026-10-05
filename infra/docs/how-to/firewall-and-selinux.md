---
title: How to configure the firewall backend and SELinux
type: how-to
audience: [operator]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: Choose the firewall backend, open SSH on an extra interface, inspect Pokus's rules, and work with SELinux enforcing.
---

# How to configure the firewall backend and SELinux

Pokus applies one firewall policy with the backend the host already uses:
SSH (and the Grafana serve port, if enabled) only on `tailscale0`, DHCP and DNS
from Incus instances to the host, and nothing else inbound. It only ever
changes its own table, zone or rules. This guide shows how to choose the
backend, allow SSH on another interface, inspect the result, and keep SELinux
enforcing.

!!! note "Prerequisites"
    - A provisioned lab host and an admin account on it.
    - The infra checkout bootstrap ran from.
    - For a different backend: that firewall manager installed and enabled.

!!! warning "Do not flush other tables"
    Never flush the ruleset or tables owned by Docker, Incus, firewalld or
    ufw (`nft flush ruleset`, `iptables -F`): it breaks container networking
    and the lab's isolation until the services restart.

## Choose the backend

1. See which backend preflight detected:

    <!-- qa:run env=E2 -->
    ```bash
    jq .firewall /etc/pokus/host-profile.json
    ```

    `active_manager` is the firewall manager that was running (`firewalld`,
    `ufw` or `null`); `backend` is what Pokus uses with
    `firewall_backend: auto`: firewalld if active, else ufw if active, else
    Pokus's own nftables table.

2. To use another backend, set it in
   `ansible/inventory/host_vars/<machine>.yml`, and to accept SSH on an
   interface besides `tailscale0` (for example a management network), list it:

    <!-- qa:skip reason="example of a host_vars file" -->
    ```yaml
    firewall_backend: nftables
    ssh_extra_allowed_interfaces: [<interface>]
    ```

    `<machine>` is this host's host_vars name and `<interface>` a network
    interface name from `ip link`.

3. Apply the firewall role:

    <!-- qa:skip reason="provisions the lab host" -->
    ```bash
    sudo ./bootstrap/bootstrap.sh --machine <machine> --tags firewall
    ```

    `<machine>` is this host.

## Inspect the rules

Each backend shows Pokus's part with its own tool:

| Backend | Command | What you should see |
| --- | --- | --- |
| nftables | `sudo nft list table inet pokus_filter` | input policy `drop`, `tcp dport { 22 }` on `tailscale0` |
| firewalld | `sudo firewall-cmd --zone=pokus-tailnet --list-all` and `--zone=pokus-run` | interface `tailscale0`, port `22/tcp`; interface `r-+` with the services `dhcp`, `dhcpv6`, `dns` |
| ufw | `sudo ufw status verbose` | `22/tcp on tailscale0 ALLOW IN` and DHCP/DNS rules on `r-+`, all with comment `pokus-infra` |

Run networks (`r-*`) only get DHCP and DNS from the host; the build bridge
and the researchers' bridges (`incusbr-*`) are trusted as Incus documents.
On every backend `pokus-docker-incus-compat.service` keeps Incus bridges
working next to Docker by adding `ACCEPT` rules to Docker's `DOCKER-USER`
chain. How runs are kept apart is explained in
[How benchmark targets are isolated](../explanation/target-isolation.md).

## Work with SELinux enforcing

On Fedora, RHEL-compatible and some openSUSE hosts SELinux stays enforcing;
Pokus never switches it to permissive. The `mac` role adds persistent file
contexts for the research space (`state/` writable by containers, `models/`
and `services/` read-only for containers) and relabels it.

1. Check the mode and the Pokus labels:

    <!-- qa:run env=E2 -->
    ```bash
    getenforce
    sudo semanage fcontext -l | grep /srv/pokus
    ls -Zd /srv/pokus/state /srv/pokus/models
    ```

2. If a lab service fails with a permission error, look for denials from the
   last minutes instead of disabling SELinux:

    <!-- qa:run env=E2 -->
    ```bash
    sudo ausearch -m avc -ts recent
    ```

    Report denials that name a Pokus path or container; files you copied into
    the research space by hand may need `sudo restorecon -R /srv/pokus`.

## Verify the policy

From another device in the tailnet, SSH to the host's tailnet name works;
from the local network, the host's LAN address does not accept SSH (unless
you listed that interface):

<!-- qa:skip reason="runs on a second machine" -->
```bash
ssh <user>@<machine>
ssh -o ConnectTimeout=5 <user>@<lan-address>
```

`<user>` is your account, `<machine>` the host's tailnet name and
`<lan-address>` its address on the local network. The first command logs you
in; the second times out.
