---
title: How to serve a documentation release on the lab
type: how-to
audience: [operator]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: Install a release's documentation tarball so the lab's docs service serves it offline, switch back to the previous version, and publish it on the tailnet.
---

# How to serve a documentation release on the lab

The lab's docs service (`pinka-docs.service`) serves one installed
documentation release read-only on `127.0.0.1:18706`, so researchers can read
the documentation without internet access. Releases are kept in
`/usr/local/share/pinka/docs/<version>/`; the link `current` names the served
one and switches in one step.

!!! note "Prerequisites"
    - Root on the lab host.
    - A release's documentation tarball and the SHA-256 published with it.

## Let bootstrap install it

Set the release in the machine's host_vars and run bootstrap:

<!-- qa:skip reason="configuration fragment, not a command" -->
```yaml
docs_release:
  url: https://example.com/releases/pinka-docs-v1.2.0.tar.gz
  sha256: <sha256 published with the tarball>
```

`url` is an `https://` URL or an absolute path on the lab host. Bootstrap
downloads the tarball with its checksum, installs it as `pinka docs install`
does and starts the service. With `docs_release: null` (the default) the
service stays off.

## Install a release by hand

<!-- qa:skip reason="changes the installed documentation on the lab host" -->
```bash
sudo pinka docs install pinka-docs-v1.2.0.tar.gz --sha256 <sum>
```

`<sum>` is the tarball's published SHA-256. The command refuses a checksum
mismatch and any archive member with an absolute path, `..`, a link that
leaves the version directory, or a special file. It unpacks into a new version
directory named after the tarball (`--version NAME` chooses another name),
switches `current` and keeps the three most recent versions. The service
follows the switch without a restart.

## Switch back to the previous version

<!-- qa:skip reason="changes the installed documentation on the lab host" -->
```bash
sudo pinka docs rollback
```

## Publish it on the tailnet

Set `docs_tailscale_serve: true` (with a `docs_release`) and run bootstrap:
the tailscale role publishes the service with `tailscale serve` on HTTPS port
10000, tailnet only; Funnel is never enabled. Otherwise researchers forward
port 18706 over SSH ([How to connect to the lab](connect.md)).

## Verify the documentation service

`pinka status` shows the served version, and doctor's
[`docs.service`](fix-doctor-checks.md#check-docs-service) check passes:

<!-- qa:run env=E2 -->
```bash
pinka status | grep '^docs:'
```
