# firewall

One effective firewall policy with three backends (BUILD_PROMPT §7.5, §19.6),
selected by `platform_facts` (`firewall_backend: auto`: firewalld if active,
else ufw if active, else nftables):

- input: established traffic, loopback, ICMP essentials, Tailscale's
  WireGuard port, SSH (and the Grafana serve port, if enabled) only on
  `tailscale0` and `ssh_extra_allowed_interfaces`, DHCP and DNS from Incus
  bridges to the host; everything else is dropped;
- forward: nothing leaves a run network (`r-*`) and nothing from outside
  starts a connection into one.

| Backend | Implementation |
| --- | --- |
| `nftables` | own table `inet pokus_filter` in `/etc/pokus/nftables/pokus.nft`, loaded by `pokus-nftables.service`; the distribution's nftables configuration is untouched |
| `firewalld` | zone `pokus-tailnet` for `tailscale0`; the build bridge in the `trusted` zone; Docker's zone untouched |
| `ufw` | `allow in on tailscale0` rules; ufw's defaults untouched |

Docker sets the FORWARD policy to DROP, which breaks Incus bridges:
`pokus-docker-incus-compat.service` (after `docker.service` and
`incus.service`) adds ACCEPT rules for the Incus bridges to `DOCKER-USER`.

Pokus never flushes or rewrites tables owned by Docker, Incus, firewalld or
ufw. Doctor verifies the effective policy with connection tests, not by
reading rules.

Run networks are created at run time; firewalld and ufw cannot name them in
advance. With those backends only the build bridge is configured (to be
resolved with the Incus network design in implementation step 6).

## Variables

See `meta/argument_specs.yml`.

## Feature flags

None.

## Idempotency

The nftables file is rendered and validated with `nft -c` before it is
loaded; the backend modules are declarative; the compatibility script only
inserts missing rules.
