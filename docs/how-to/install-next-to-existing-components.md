---
title: How to install next to existing Docker, Tailscale, LXD or Podman
type: how-to
audience: [operator]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: Install the lab on a machine where Docker Engine, Tailscale, LXD or Podman already run, and resolve what preflight asks the owner to change.
---

# How to install next to existing Docker, Tailscale, LXD or Podman

pinka uses Docker Engine, a logged-in Tailscale, LXD and Podman as they are
and never changes them. When the lab cannot run with one of them as it is,
preflight stops with exit code 30 (`needs_action`) before anything on the
machine changes, and names the change. This guide checks the machine first,
makes the changes preflight names, and then installs.
[Components that existed before pinka](../explanation/pre-existing-components.md)
explains why pinka works this way.

!!! note "Prerequisites"
    - A machine that passes the other checks of
      [How to check whether a machine can host the lab](check-a-host.md).
    - Root on the machine, and the right to change its Docker, Tailscale or
      LXD settings (they may serve other people's work).

## Check what preflight finds

1. Run preflight read-only:

    <!-- qa:run env=E2 -->
    ```bash
    sudo ./preflight/pinka-preflight --no-write
    ```

2. Read the `Notes` of the summary: each component that was there before
   pinka is named with "pinka uses it as it is" or "pinka leaves it as it
   is". The host profile lists them under `components`, with
   `origin: pre-existing`.

3. Read `Action needed`. Each action below has its own section; make the
   change, then run preflight again until none is left.

## Docker Engine

pinka never writes the `daemon.json` of a Docker it did not install and
never restarts it.

- **`docker_not_running`:** start and enable Docker:

    <!-- qa:skip reason="changes the host's Docker service" -->
    ```bash
    sudo systemctl enable --now docker.service
    ```

- **`docker_plugins_missing`:** install the Compose v2 and buildx plugins from
  the same source as Docker. For Docker's own repositories these are the
  packages `docker-compose-plugin` and `docker-buildx-plugin`.

- **`docker_cdi_off`** (only with a usable GPU): the lab's GPU containers
  need CDI. In `/etc/docker/daemon.json`, remove `"features": {"cdi": false}`
  (on Docker older than 28.2, add `"features": {"cdi": true}`), then restart
  Docker at a time that suits its other containers:

    <!-- qa:skip reason="restarts the host's Docker" -->
    ```bash
    sudo systemctl restart docker.service
    ```

The lab's own containers carry their log limits, cgroup parent and network
subnets in pinka's Compose projects, so Docker's other containers keep their
settings.

## Tailscale

A node logged in before pinka keeps its host name, tags and settings, and
pinka never logs it in or out.

- **`tailscale_funnel_on`:** the lab never runs with Funnel. Turn it off:

    <!-- qa:skip reason="changes the Tailscale configuration" -->
    ```bash
    sudo tailscale funnel reset
    ```

- **`tailscale_ssh_on`:** Tailscale SSH would bypass `sshd` and the
  researchers' keys. Turn it off:

    <!-- qa:skip reason="changes the Tailscale configuration" -->
    ```bash
    sudo tailscale set --ssh=false
    ```

Then decide two things:

1. **The tailnet policy.** If the node lacks `tag:pinka-lab`, either tag it
   in the Tailscale admin console or name it by its host name in the ACL
   lines the installer shows. Doctor warns while the tag is missing
   (`network.tailscale_identity`).
2. **Publishing with `tailscale serve`.** On this node it is off unless you
   consent: tick the consent checkbox on the installer's Remote access
   screen, or set it in the machine's host_vars:

    <!-- qa:skip reason="example host_vars excerpt" -->
    ```yaml
    grafana_tailscale_serve: true
    tailscale_serve_consent: true
    ```

A Tailscale that is installed but logged out is not in use: pinka logs it
in with the lab's tag and leaves its package as it is.

## LXD

pinka never changes, stops or reconfigures LXD. **`lxd_incus_clash`** names
each clash; resolve it on LXD's side or in the machine's host_vars:

- **A network name pinka's Incus uses** (`pinka-build0`, or one starting
  with `r-`): rename the LXD network; pinka's names are fixed.
- **A subnet that overlaps pinka's Docker address pool:** readdress the LXD
  network, or give pinka another pool (`docker_address_pools` in the
  machine's host_vars).
- **A DNS or DHCP server on UDP port 53 or 67 of every address:** Incus runs
  a dnsmasq on each of its bridges. Bind that server to its own addresses
  only.
- **The pool device is LXD's storage:** choose another disk for
  `incus_pool_device`, or a loop file.
- **Docker Engine would stop LXD's forwarding:** a Docker that pinka
  installs sets the firewall's `FORWARD` policy to `DROP`, which stops LXD's
  bridged guests. Install Docker Engine yourself first, with the rules LXD
  needs (LXD's documentation describes them); pinka then uses that Docker as
  it is.

## Podman

pinka leaves Podman as it is. **`podman_docker`:** Docker Engine needs the
`docker` command and socket. Remove the `podman-docker` package (or the link
from `/var/run/docker.sock` to Podman's socket); Podman itself stays.

## Install

Run the installer or bootstrap as usual
([How to install a lab with the installer TUI](install-with-the-tui.md)).
The first run writes the install record before anything else changes; it
notes what was there before pinka.

## Verify the result

1. Doctor passes, or warns only about `network.tailscale_identity` on an
   adopted node:

    <!-- qa:run env=E2 -->
    ```bash
    sudo pinka doctor
    ```

2. The install record names Docker, Tailscale or LXD as there before pinka:

    <!-- qa:run env=E2 -->
    ```bash
    sudo python3 -c 'import json; r = json.load(open("/var/lib/pinka/install-record.json")); print({n: c.get("origin") for n, c in r["preexisting"]["components"].items() if isinstance(c, dict)})'
    ```

3. The containers that ran before the install still run, with their own
   networks and settings.
