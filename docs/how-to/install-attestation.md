---
title: How to install QA trusted keys and attestations
type: how-to
audience: [operator, qa]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: Trust the QA team's public keys and place a published QA attestation where the platform's official check finds it.
---

# How to install QA trusted keys and attestations

A QA attestation states that a release candidate (an infra, platform and
catalog commit triple) passed the QA verification suites. The platform marks
runs as *official* only when a valid attestation for exactly the deployed
commits is installed on the host. Infra's part is to put the QA team's public
keys and the attestation files in place; the attestation format and the
official check are described in the
[integration contract](../contracts.md#external-verification-hooks-d1-d2).

!!! note "Prerequisites"
    - A production lab with the platform deployed (`platform_enabled: true`),
      and admin access to it.
    - The QA team's public key (PEM) and the published attestation of the
      release candidate you run: `<file>.json` and its signature
      `<file>.json.sig`, obtained from the QA team through a channel you trust.

## Trust the QA team's keys

1. Add each key to `qa_trusted_keys` in `ansible/group_vars/all/main.yml` (or
   in the lab's host_vars file):

    <!-- qa:skip reason="configuration fragment, not a command" -->
    ```yaml
    qa_trusted_keys:
      - name: <key-name>
        pem: |
          -----BEGIN PUBLIC KEY-----
          <base64 lines of the key>
          -----END PUBLIC KEY-----
    ```

    `<key-name>` names the file `/etc/pinka/qa/trusted-keys/<key-name>.pem`.

2. Apply it:

    <!-- qa:skip reason="provisions the host" -->
    ```bash
    sudo ./bootstrap/bootstrap.sh --machine <machine> --tags platform
    ```

    `<machine>` is the lab's machine name. The platform role writes one PEM
    file per key (0644 root).

## Place an attestation

Copy both files into `/etc/pinka/qa/attestations/`, owned by root and
readable by the active platform's controller:

<!-- qa:run env=E2 -->
```bash
sudo install -o root -g <controller> -m 0640 <file>.json <file>.json.sig /etc/pinka/qa/attestations/
```

`<file>` is the attestation's file name and `<controller>` the controller's
account, `<platform>-<service>` (`account` of the `controller` service in
`/etc/pinka/platform/principals.json`), which is also the directory's
group. Attestations are never edited on the
host: the signature covers the canonical JSON, and any change makes it
invalid. Old attestations may stay; the platform uses the one that matches
the deployed commits.

## Verify the installation

You should now see the key and the attestation on the host:

<!-- qa:run env=E2 -->
```bash
ls -l /etc/pinka/qa/trusted-keys/ /etc/pinka/qa/attestations/
```

The key file and both attestation files are listed. Whether the attestation
makes runs official is reported by the platform; a mismatch (other commits
deployed, an unknown key, a bad signature) leaves runs exploratory and is
reported with the reason.
