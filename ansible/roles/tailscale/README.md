# tailscale

Tailscale (BUILD_PROMPT §7.4).

**A node logged in before pinka** (preflight's `components`, §2.2) is
adopted as it is: no repository, package, pin, hold, login, host name, tag or
setting changes. Preflight has already refused one with Funnel or Tailscale
SSH on (`needs_action`). Doctor compares it with what preflight recorded, and
reports a missing `tag:pinka-lab` or an expiring key as warnings. `tailscale
serve` for Grafana or the docs needs `tailscale_serve_consent: true` as well
(`pinka_tailscale_serve_allowed`, which the firewall role uses for the serve
ports). **A Tailscale that is installed but not logged in** is not in use:
pinka joins it as below and leaves its package as it is.

Otherwise:

- Installs Tailscale from the source of the component source matrix
  (Tailscale's repositories, or the distribution's package on Arch Linux),
  pinned to `versions.lock.yml` and held.
- Joins the tailnet as `machine_name` with the lab's tag (`tag:pinka-lab`,
  `tag:pinka-qa` on staging hosts), `--accept-dns=false`, `--ssh=false`.
  Already logged in with the same host name and tag: nothing to do. With
  `vault_tailscale_authkey`: non-interactive join (never logged). Otherwise
  the login URL is printed and the play waits; a non-interactive run without
  a key stops with signal 77.
- Fails the play if any Funnel configuration exists: lab services are never
  published to the internet.
- With `grafana_tailscale_serve: true` (and `docs_tailscale_serve`),
  publishes Grafana (and the docs service) on the tailnet only (`tailscale
  serve`).

The install record (§7.21) notes the join before `tailscale up` and every
serve entry before pinka adds it; uninstall logs out only a node pinka
joined and removes only those serve entries.

The tailnet ACL snippet that limits `tag:pinka-lab:22` and the serve port to
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
