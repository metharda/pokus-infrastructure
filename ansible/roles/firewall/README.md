# firewall

One effective firewall policy with three backends,
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
| `nftables` | own table `inet pinka_filter` in `/etc/pinka/nftables/pinka.nft`, loaded by `pinka-nftables.service` (with the access guard); the distribution's nftables configuration is untouched |
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

Pre-existing access, from the install record: on a shared host the
listeners preflight found stay reachable as they were (the nftables backend
accepts each one's protocol and port on the address it listened on; with
firewalld or ufw the owner's zones and rules stay as they are). What the
owner consented to close (`firewall_close_listeners`) and, once the safeguard
held, the install's access path are dropped by the access guard, table
`inet pinka_access_guard` in `/etc/pinka/nftables/access-guard.nft`, the same
on every backend; each of its drop rules matches
only a new connection and never one on loopback; on `tailscale0` only SSH
is spared (accepted first), so what was closed is closed over the tailnet
as well; SSH on the extra interfaces is accepted first too, and it refers to
no other table (`ansible/tests/access_guard_probe.py` checks it against the
kernel).
`pinka-nftables.service` loads it on every backend, with `inet pinka_filter`
on the nftables backend. A listener that serves the install's path stays
open while the path is open. `tasks/install_path.yml` runs at the end of the
play: with `firewall_close_install_path: true` and a researcher's key login
from a tailnet address in sshd's journal since the run began, it records
`install_path` as closed and re-applies the sshd drop-in, pinka's table and
the guard; otherwise the path stays open and doctor warns. The install
record lists what is closed (`closed_access`): a listener the owner no
longer wants closed leaves it; the install's path, once closed, stays
closed.

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
