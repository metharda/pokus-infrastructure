# docs

The documentation service:

- `pinka-docs.service` runs `pinka docs serve` on `127.0.0.1:18706` (the
  `docs_serve` port of `pinka_ports`) as a dynamic user with infra's
  hardening baseline, read-only access to `docs_dir` and no other network
  access; it serves the release that `docs_dir/current` names and follows
  the link on every request;
- with `docs_release` (`{url, sha256}`) set, the role downloads the tarball
  with its checksum (or takes a local path) and installs it with
  `pinka docs install`, which verifies the checksum again, unpacks into a new
  version directory refusing absolute paths, `..` and links that leave it,
  switches `current` in one step and keeps the three most recent versions;
- with `docs_release: null` the service is stopped and disabled;
- `docs_tailscale_serve: true` publishes it on the tailnet only (tailscale
  role); otherwise researchers use an SSH port forward.

`pinka docs rollback` switches back to the previous version; `pinka status`
and doctor (`docs.service`) show the served version.
