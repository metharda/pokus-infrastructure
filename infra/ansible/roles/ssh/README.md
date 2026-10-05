# ssh

sshd hardening (BUILD_PROMPT §7.3) through `/etc/ssh/sshd_config.d/10-pokus.conf`:
key-only login, no root login, `AllowGroups pokus`, no X11 forwarding, local
TCP forwarding only (researchers forward Grafana and the gateway's ad-hoc
endpoint), `ClientAliveInterval 60`.

The role makes `Include /etc/ssh/sshd_config.d/*.conf` the first directive of
`sshd_config` (added if a distribution ships without it), checks the result
with `sshd -t` and only then reloads the service (`ssh` on the Debian
family, `sshd` elsewhere). The firewall role limits where SSH is reachable:
`tailscale0` plus `ssh_extra_allowed_interfaces`.

Make sure your own account is one of the `researchers` (with a key file)
before you apply this role over an SSH connection.

## Variables

See `meta/argument_specs.yml`.

## Feature flags

None.

## Idempotency

Declarative; sshd is reloaded only when its configuration changed.
