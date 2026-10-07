# tailscale

Tailscale (BUILD_PROMPT §7.4).

- Installs Tailscale from the source of the component source matrix
  (Tailscale's repositories, or the distribution's package on Arch Linux),
  pinned to `versions.lock.yml` and held.
- Joins the tailnet as `machine_name` with the lab's tag (`tag:pokus-lab`,
  `tag:pokus-qa` on staging hosts), `--accept-dns=false`, `--ssh=false`.
  Already logged in with the same host name and tag: nothing to do. With
  `vault_tailscale_authkey`: non-interactive join (never logged). Otherwise
  the login URL is printed and the play waits; a non-interactive run without
  a key stops with signal 77.
- Fails the play if any Funnel configuration exists: lab services are never
  published to the internet.
- With `grafana_tailscale_serve: true`, publishes Grafana on the tailnet only
  (`tailscale serve`).

The tailnet ACL snippet that limits `tag:pokus-lab:22` and the serve port to
the researchers is documented in `docs/security.md`.

## Variables

See `meta/argument_specs.yml`.

## Feature flags

None.

## Idempotency

The join is skipped when the host is already logged in with the same name
and tag; the serve command runs only if the serve port is not configured yet.

## Check mode

Check mode verifies the source and the repository key, but cannot resolve
packages from a repository it did not really add. While Tailscale is not
installed (`tailscale` is missing when the play starts), the role ends after
its repository step in check mode.
