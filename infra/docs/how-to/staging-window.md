---
title: How to run a QA maintenance window on staging
type: how-to
audience: [operator, qa]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: Provide a staging lab for the QA team's verification of a release candidate, kept apart from the production lab, and close it again.
---

# How to run a QA maintenance window on staging

The QA team verifies a release candidate on a *staging* lab before it is
attested for production. The QA procedure itself belongs to the QA team; this
guide covers the lab host side: building a staging lab that is separate from
production in every way that matters, deploying the candidate on it, and
closing the window.

A staging lab is a normal Pokus host with `lab_mode: staging`. It has its own
vault, its own research space, its own tailnet identity (`tag:pinka-qa`) and
its own run-manifest signing key; it never pairs with a production host and
never writes to one. Runs on staging are never official.

!!! note "Prerequisites"
    - A machine for staging that preflight accepts (it may be a VM:
      [Virtual machine hosts](../providers/vm.md)); never the production host.
    - The release candidate's commits of infra, platform and catalog.
    - A tailnet policy that allows `tag:pinka-qa` (see
      [Security](../security.md#tailnet-policy)).

## Prepare the staging configuration

1. Create the host_vars file of the staging machine from the example and set
   the mode:

    <!-- qa:skip reason="configuration fragment, not a command" -->
    ```yaml
    machine_name: <staging-name>
    lab_mode: staging
    ```

    `<staging-name>` is the staging machine's name in the tailnet. Use the
    installer instead if you prefer; its Machine screen sets the same value.

2. Create a separate vault for staging with its own passwords. Never copy the
   production vault: a shared secret would connect the two labs.

3. Set the candidate's versions: `platform_ref` and `catalog_ref` (with
   `platform_enabled` and `catalog_enabled`), and check out the candidate's
   infra commit. For the QA team's scripted model server, set
   `platform_inference: scripted` and `platform_scripted_api_url`; this is
   accepted only on staging.

## Install the staging lab

<!-- qa:skip reason="provisions the host" -->
```bash
sudo ./bootstrap/bootstrap.sh --machine <staging-name>
```

`<staging-name>` is the staging machine's name from its host_vars file. The
tailscale role joins the tailnet with `tag:pinka-qa`. The QA team can now
run its verification. QA keys and attestations are installed only on the
production lab ([How to install QA trusted keys and attestations](install-attestation.md)).

## Close the window

When the QA team has finished, stop the staging lab so it does not run
unattended:

<!-- qa:run env=E2 -->
```bash
sudo pinka stop --timeout 0
```

A timeout of 0 stops without waiting for runs, which is fine on staging. Keep
the staging host for the next window, or reinstall it from scratch.

## Verify the separation

On the staging host:

<!-- qa:run env=E2 -->
```bash
tailscale status --json | jq '.Self.Tags'
jq '.lab_mode' /etc/pinka/lab.json
```

The tags list `tag:pinka-qa` and `lab.json` says `"staging"`. Doctor
on the production lab still reports its own machine name and `production`.
