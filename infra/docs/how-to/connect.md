---
title: How to connect to the lab
type: how-to
audience: [researcher, operator]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: Reach the lab host over Tailscale and SSH, forward Grafana, the ad-hoc LLM endpoint and the documentation to your own computer.
---

# How to connect to the lab

The lab host is reachable only through the lab's tailnet: sshd accepts
connections on the Tailscale interface (and on interfaces the operator listed
in `ssh_extra_allowed_interfaces`), with keys only, for members of the `pokus`
group. Every service of the lab listens on `127.0.0.1` of the host; you reach
it by SSH port forwarding.

!!! note "Prerequisites"
    - A researcher account on the lab: the operator added you with your SSH
      public key (see [How to add, remove or lock a researcher](manage-researchers.md)).
    - Your computer in the lab's tailnet, with the Tailscale client running
      (the tailnet administrator invites you), and the tailnet policy allowing
      members to reach the lab's tag on port 22 (see
      [Security](../security.md#tailnet-policy)).
    - The lab's name in the tailnet, `<lab-host>` below (the machine name the
      operator chose, or its tailnet address).

## Log in over SSH

1. Connect with your own account:

    <!-- qa:skip reason="runs on a researcher's computer" -->
    ```bash
    ssh <user>@<lab-host>
    ```

    `<user>` is your account name on the lab, for example `researcher1`, and
    `<lab-host>` the lab's name in the tailnet. Passwords are never accepted; if the key is refused, ask the operator to
    check your public key.

2. On the lab, check your groups:

    <!-- qa:run env=E2 -->
    ```bash
    id -nG
    ```

    The output contains `pokus`; admins also see `pokus-admin`. Researchers
    are never in `docker` or `incus-admin`.

## Forward a lab service to your computer

Forward the port of the service with `-L`, then open it on your own computer:

<!-- qa:skip reason="runs on a researcher's computer" -->
```bash
ssh -N -L <port>:127.0.0.1:<port> <user>@<lab-host>
```

`<port>` is the port of the service in the table below; `<user>` and
`<lab-host>` are as above. `-N` keeps the connection open without a shell;
stop it with ++ctrl+c++.

| Service | `<port>` | Then open | Details |
| --- | --- | --- | --- |
| Grafana | 18700 | `http://127.0.0.1:18700/` | [How to reach Grafana and the lab database](reach-grafana.md) |
| LLM gateway, ad-hoc endpoint | 18705 | an OpenAI-compatible client on `127.0.0.1:18705` | the platform documents the path and the personal key (`pokus llm key`) |
| Documentation | 18706 | `http://127.0.0.1:18706/` | served by `pokus-lab docs serve`, below |

Grafana can also be published on the tailnet by the operator
(`grafana_tailscale_serve: true`); then no forwarding is needed.

## Read the documentation on the lab

The lab can serve its own documentation, so it is available without internet
access. The operator extracts the documentation tarball of the lab's release
into `/usr/local/share/pokus/docs/` once. Then anyone with an account starts
the server on the lab:

<!-- qa:run env=E2 -->
```bash
pokus-lab docs serve
```

It serves the files read-only on `127.0.0.1:18706` until you press
++ctrl+c++; forward port 18706 as above to read it on your computer. If the
port is taken (someone else serves it already), use that server or choose
another port with `--port`. A site built from a checkout with `make docs`
can be served with `--dir <checkout>/infra/site`.

## Verify the connection

You should now be logged in to the lab, and a forwarded service answers on
your computer:

<!-- qa:skip reason="runs on a researcher's computer with an open forwarding" -->
```bash
curl --silent --output /dev/null --write-out '%{http_code}\n' http://127.0.0.1:18700/api/health
```

The output is `200` while the Grafana forwarding is open.
