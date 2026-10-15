# firewall

One effective firewall policy with three backends (BUILD_PROMPT §7.5, §19.6),
selected by `platform_facts` (`firewall_backend: auto`: firewalld if active,
else ufw if active, else nftables):

- input: established traffic, loopback, ICMP essentials, Tailscale's
  WireGuard port, SSH (and the Grafana serve port, if enabled) only on
  `tailscale0` and `ssh_extra_allowed_interfaces`, DHCP and DNS from Incus
  bridges to the host; everything else is dropped;
- forward: nothing leaves a run network (`r-*`) and nothing from outside
  starts a connection into one. Between two run networks, Incus's ACL
  `pinka-run-default` (incus role) drops traffic at the bridge boundary on
  every backend.

| Backend | Implementation |
| --- | --- |
| `nftables` | own table `inet pinka_filter` in `/etc/pinka/nftables/pinka.nft`, loaded by `pinka-nftables.service`; the distribution's nftables configuration is untouched |
| `firewalld` | zone `pinka-tailnet` for `tailscale0`; zone `pinka-run` for `r-+` with DHCP, DHCPv6 and DNS only; the build and personal bridges in the `trusted` zone; Docker's zone untouched |
| `ufw` | `allow in on tailscale0` rules; DHCP and DNS from `r-+`; allow and route rules for the build and personal bridges; ufw's defaults untouched |

The Incus bridge rules follow Incus's firewall guide
(`doc/howto/network_bridge_firewalld.md`, Incus 6.0.6). Run networks are
created at run time and matched with interface wildcards (`r-*` in nftables,
`r-+` in firewalld and ufw).

Docker sets the FORWARD policy to DROP, which breaks Incus bridges:
`pinka-docker-incus-compat.service` (after `docker.service` and
`incus.service`) adds ACCEPT rules for the Incus bridges to `DOCKER-USER`.

On every backend, the role also loads its guards, each an nftables table of
its own: the telemetry guard and the inference guard (UID allowlists on the
loopback, ADR-0016) and, on lab and inference nodes, the split guard
(`pinka-split-guard.service`, table `inet pinka_split_guard`): the
split-inference ports on `tailscale0` admit only the paired node's tailnet
address from `/etc/pinka/inference/peer.json`, nobody before a pairing;
`pinka pair show` opens the pairing port for the pairing only (a set element
with a timeout), and `pinka pair` loads the guard again with the new peer.

Pokus Infrastructure (pinka) never flushes or rewrites tables owned by Docker, Incus, firewalld or
ufw. Doctor verifies the effective policy with connection tests, not by
reading rules.

## Variables

See `meta/argument_specs.yml`.

## Feature flags

None.

## Idempotency

The nftables file is rendered and validated with `nft -c` before it is
loaded; the backend modules are declarative; the compatibility script only
inserts missing rules.
