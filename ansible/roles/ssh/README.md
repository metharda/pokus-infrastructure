# ssh

sshd hardening through `/etc/ssh/sshd_config.d/10-pinka.conf`:
key-only login, no root login, `AllowGroups pinka`, no X11 forwarding, local
TCP forwarding only (researchers forward Grafana and the gateway's ad-hoc
endpoint), `ClientAliveInterval 60`.

The settings cover the whole host (`ssh_policy_scope: host`) on a dedicated
host. On a shared host they apply in a `Match Group pinka` block without
`AllowGroups` (`group`), so accounts outside `pinka` keep the host's own
policy. A dedicated host keeps the `group`
form too while the install's access path is open for an account that is not
a researcher, so that account is not locked out before the safeguard closes
the path (firewall role, `tasks/install_path.yml`, which applies this role
again when it closes the path). OpenSSH restores the `Match` state after an
`Include`, so the block ends with the drop-in.

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
