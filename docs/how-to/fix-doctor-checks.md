---
title: How to fix a failed doctor check
type: how-to
audience: [operator]
applies_to: {infra: ">=0.1"}
last_verified: {date: null, rc: null, by: null}
description: What each failed pinka doctor check means and how to fix it, one section per check ID.
---

# How to fix a failed doctor check

`pinka doctor` prints one line per check with its ID, what it found and,
for failures, a remediation. This page has one section per check ID with
more detail. The checks themselves are listed in
[Doctor checks](../reference/doctor-checks.md). Most fixes end with running
bootstrap again: it converges everything it manages, and its last stage runs
doctor.

!!! note "Prerequisites"
    - A lab host provisioned by bootstrap.
    - An admin account on the host (member of `pinka-admin`).
    - The checkout bootstrap runs from and the host_vars file name
      (`<machine>` below).

## Run doctor and find the failed checks

1. Run doctor:

    <!-- qa:run env=E2 -->
    ```bash
    sudo pinka doctor
    ```

    Lines starting with `FAIL` are failed checks of severity error, `warn`
    failed warnings. Under each one, `->` gives the remediation.

2. For a machine-readable report, add `--json`.

3. Most fixes re-run bootstrap, either completely or for one role:

    <!-- qa:skip reason="provisions the host" -->
    ```bash
    sudo ./bootstrap/bootstrap.sh --machine <machine> --tags <role>
    ```

    `<machine>` is the host_vars file name, `<role>` the role named in the
    section below. Without `--tags` bootstrap converges everything.

## `install.record`: the install record is missing or damaged {#check-install-record}

`/var/lib/pinka/install-record.json` says what existed on the host before
pinka and what pinka added since; uninstall and every role that must leave a
pre-existing component alone read it (see
[Components that existed before pinka](../explanation/pre-existing-components.md)).
Doctor fails when it is missing, is not a JSON object, lacks one of its parts
(`created_at`, `legacy`, `preexisting`, `added`), or is not owned by root or
writable by anyone else.

- **Missing:** run bootstrap. On a host pinka provisioned before the record
  existed it writes a *legacy* record, which decides nothing: pinka's marks
  (its names, the managed `daemon.json` label, the `tag:pinka-*` tags) then
  say what is pinka's.
- **Damaged:** restore the file from the last backup of `/var/lib/pinka`.
  Never edit it by hand and never delete it: a new record would list
  everything pinka added as pre-existing, and uninstall would then keep it.
- **Wrong owner or mode:** `sudo chown root:root` and `sudo chmod 0600` the
  file.

## `preflight.outcome`: preflight no longer allows the lab {#check-preflight-outcome}

A fresh, read-only preflight decided `unsupported`, `needs_action` or failed.
Run `pinka preflight` to see why; its summary names every failed
requirement. Typical causes are a changed kernel or a new conflicting
package. See [How to check whether a machine can host the lab](check-a-host.md).

## `preflight.drift`: the host changed since bootstrap {#check-preflight-drift}

Outcome, provider or a feature flag differs from `/etc/pinka/host-profile.json`
(for example the GPU is no longer usable, so `gpu_inference` is off). If the
change is wanted, store the new profile with bootstrap's `--refresh-profile`;
otherwise restore the previous state (driver, hardware).

## `services.units`: a unit of the lab is not active {#check-services-units}

One of the units of `pinka-lab.target` failed or is stopped. Read its status
and log, fix the cause, then start the lab:

<!-- qa:skip reason="the unit name comes from the doctor output" -->
```bash
systemctl status <unit>
journalctl -u <unit> -b
sudo pinka start
```

`<unit>` is a unit named in the doctor output.

## `services.containers`: a container is missing, unhealthy or on another image {#check-services-containers}

A lab container does not run, its health check fails, or it runs an image
other than the digest pinned in `versions.lock.yml`. Look at the container
with `sudo docker ps --all` and `sudo docker logs <container>` (`<container>`
is the name from the doctor output). Bootstrap recreates containers from the
pinned digests: run it with the tag of the owning role (`postgres`, `opa` or
`observability`).

## `network.listeners`: something listens outside loopback {#check-network-listeners}

A process listens on an address other than `127.0.0.1` or `::1`. Only sshd
(firewalled), Tailscale, Incus's DNS and DHCP server on its bridges, DHCP
clients and time daemons may. Find the process with `sudo ss -lntup`, then
stop it or bind it to `127.0.0.1`. If a host service must listen, add its
process name to `doctor_listener_processes` in the host_vars file and run
bootstrap with `--tags doctor`. Split inference adds listeners on the node's
tailnet address (`tailscale ip`): ports 18704 and 18710 on an inference node,
4317 on its paired lab node; anything else there is unexpected.

On a shared host, a listener the host had before pinka is expected when it
matches the install record exactly: the same protocol, address and port,
the same owning unit and the same process. The check names such listeners
with `kept` (the firewall keeps them reachable as found) or `closed by the
firewall` (closed with the owner's consent); pinka never stops them. A
listed listener is one that appeared after the install, or a recorded one
that moved to another address or now belongs to another unit or program.
pinka's own services never count as pre-existing, a dedicated host gets no
such exception, and none applies while the install record can be changed
by anyone but root (then `install.record` fails too). See
[Access to the host](install-next-to-existing-components.md#access-to-the-host).

## `network.install_path`: the install's access path is still open {#check-network-install_path}

A warning. The install came over SSH from outside the tailnet (from the LAN,
for example), and that path is still open: pinka closes it only with the
owner's consent (`firewall_close_install_path: true`, or the checkbox on the
installer's Remote access screen) and only after a researcher logged in with
a key over the tailnet in the same run (the safeguard of BUILD_PROMPT §7.5).
Give the consent if it is missing, run bootstrap again and, while it runs,
log in as a researcher with their key from another device on the tailnet
(`ssh researcher1@<machine name>`); a login from before the run, from the
host itself or without a key does not count. The run closes the path at its end; the session you started it from
stays open until you leave it. If the path is meant to stay open, the
warning stays.
The check is skipped when the install ran on the console or over the
tailnet, and where the install record is missing or legacy.

## `network.firewall`: firewall rules are missing {#check-network-firewall}

The nftables table `inet pinka_filter`, the firewalld zones `pinka-tailnet`
and `pinka-run` or ufw are missing or inactive, the access guard's table
`inet pinka_access_guard` (every backend; it drops what the owner consented
to close) is missing, or the `DOCKER-USER` rules that let Incus bridges pass
Docker's forwarding policy are missing. Run bootstrap with `--tags firewall`
and see [How to configure the firewall backend](firewall-and-selinux.md).

## `network.tailscale`: Tailscale is not as expected {#check-network-tailscale}

Tailscale is not logged in, the host name or tags differ from what is
expected, or Funnel or Tailscale SSH is on. What is expected depends on who
logged the node in:

- **pinka joined the node:** the host name and tag of the host_vars file.
  Log in again with bootstrap's Tailscale step (`--tags tailscale`).
- **The node was logged in before pinka** (an adopted node): the host name
  and tags the install record noted at the first install. pinka never logs
  such a node in or out and never changes its name or tags; restore them in
  the Tailscale admin console or with `tailscale set` yourself.

Funnel and Tailscale SSH must never be on. Remove them:

<!-- qa:skip reason="changes the Tailscale configuration" -->
```bash
sudo tailscale funnel reset
sudo tailscale set --ssh=false
```

## `network.tailscale_identity`: an adopted node lacks the lab's tag or its key expires {#check-network-tailscale_identity}

A warning, only on an adopted node. The node does not carry the lab's tag
(`tag:pinka-lab` by default), or its node key expires, as it does for a
node logged in with a personal account. Neither stops the lab, but:

- without the tag, the tailnet ACL has to name the node another way (its
  host name or address) to let researchers reach SSH and nothing else;
- when the key expires, the lab leaves the tailnet until the owner signs in
  again.

Tag the node or disable its key expiry in the Tailscale admin console, or
accept the warning. [Security model](../security.md) explains the trade-off.

## `gpu.docker`: no GPU visible in a container {#check-gpu-docker}

A container of the pinned CUDA image does not see the GPU. Check the driver
with `nvidia-smi` (on WSL `/usr/lib/wsl/lib/nvidia-smi`), then run bootstrap
with `--tags nvidia_container_toolkit`. After a driver update on bare metal a
reboot is often needed.

## `gpu.cdi`: Docker does not give containers the GPU through CDI {#check-gpu-cdi}

A GPU feature is on, and `docker info` lists no CDI spec directories. Docker
Engine 28.2 and later enable CDI unless `daemon.json` turns it off.

- **A Docker pinka installed:** run bootstrap with `--tags docker`. On a
  Docker without CDI support bootstrap records the NVIDIA runtime instead,
  and this check passes with that method.
- **A Docker that existed before pinka:** pinka never changes it. Remove
  `"features": {"cdi": false}` from `/etc/docker/daemon.json` (on Docker
  older than 28.2 add `"features": {"cdi": true}`), then restart Docker
  yourself at a time that suits its other containers.

Check the result:

<!-- qa:run env=E2 -->
```bash
docker info --format '{{json .CDISpecDirs}}'
```

It prints a list of directories, not `null` or `[]`.

## `gpu.driver_cuda`: the driver is too old for the vLLM image {#check-gpu-driver_cuda}

The NVIDIA driver supports an older CUDA version than the one the pinned vLLM
image is built on (`cuda` of `containers.images.vllm` in `versions.lock.yml`;
doctor compares major and minor), or `nvidia-smi --version` does not report
it. Preflight makes the same comparison and turns `gpu_inference` off.

- **Bare metal or a VM with passthrough:** install a driver branch that
  supports that CUDA version with your distribution's packages, then reboot.
  pinka never upgrades an installed driver on its own.
- **WSL2:** update the NVIDIA driver on Windows. Never install a Linux NVIDIA
  driver inside WSL.

Check the result with `nvidia-smi --version` (on WSL
`/usr/lib/wsl/lib/nvidia-smi --version`): its `CUDA Version` line must be at
least the image's major and minor version.

## `docs.service`: the docs service does not serve the release {#check-docs-service}

A documentation release is configured (`docs_release`), but no release is
installed in `/usr/local/share/pinka/docs` or `pinka-docs.service` is not
active. Install the release (`sudo pinka docs install TARBALL --sha256 SUM`,
or run bootstrap with `docs_release` set), then start the service with
`sudo systemctl start pinka-docs.service` and read its log with
`journalctl -u pinka-docs.service -b`. See
[How to serve a documentation release on the lab](install-docs-release.md).
Skipped while `docs_release` is null.

## `gpu.vllm`: vLLM does not answer {#check-gpu-vllm}

A deployment is active but vLLM's `/health` does not answer 200. Read the
log with `journalctl -u pinka-inference.service -b` and
`sudo docker logs pinka-vllm`. Loading a model can take minutes; check again
before acting. See [How to add a model](add-a-model.md) for activating a
deployment again.

## `inference.pairing`: a split-inference node is not paired as configured {#check-inference-pairing}

Lab and inference nodes only (`node_role: lab` or `inference`). One of these
is wrong:

- `/etc/pinka/inference/self.crt` or `self.key` is missing: run bootstrap
  with `--tags inference_control`.
- `self.key` has the wrong owner or mode: it is `root:root 0600` on a lab
  node (only the root-run forwarder uses it) and `root:pinka-inference 0640`
  on an inference node (the control endpoint's account reads it). Run
  bootstrap with `--tags inference_control`, which sets them.
- The node is paired, but its host_vars file does not name the peer: set
  `inference_lab_node` (inference node) or `inference_remote_node` (lab node)
  to the node in `/etc/pinka/inference/peer.json` and apply it as
  [How to pair an inference node](pair-inference-node.md) describes.
- The host_vars name another node than the pairing, or `peer.crt` is missing:
  pair the nodes again.
- A certificate expires within `doctor_certificate_renew_days` (30): delete
  `self.crt` on that node, run bootstrap with `--tags inference_control`
  (it issues a new certificate) and pair the nodes again.

The check is skipped on a node that is neither paired nor configured (the
key's owner and mode are checked even then).

## `inference.split_guard`: another tailnet node can reach the split-inference ports {#check-inference-split_guard}

Lab and inference nodes only. The table `inet pinka_split_guard` admits
only the paired node's tailnet address to the split-inference ports on
`tailscale0` (an inference node: the vLLM API and the control port; a lab
node: OTLP gRPC); before a pairing it admits nobody. One of these is wrong:

- The table is not loaded: run bootstrap with `--tags firewall`, or reload it:

    <!-- qa:skip reason="changes the firewall of a split node" -->
    ```bash
    sudo systemctl restart pinka-split-guard.service
    ```

- The pairing record has no tailnet address (a pairing made before the
  guard existed): pair the nodes again, as
  [How to pair an inference node](pair-inference-node.md) describes.
- The guard admits another address than the record's: reload it as above.
  If the peer's tailnet address changed (the node joined the tailnet
  again), pair the nodes again.
- The pairing port is open: `pinka pair show` is waiting, or was stopped
  less than a minute ago; the port closes by itself.

## `gpu.arbiter`: the GPU arbiter's socket, mode or log is wrong {#check-gpu-arbiter}

One of these is wrong:

- `/run/pinka/gpu.sock` is missing, or it does not belong to the active
  platform's controller (`root` before the platform is deployed,
  `pinka-inference` on an inference node) with mode 0600.
- `/var/lib/pinka/gpu/state.json` holds no valid mode.
- The GPU is `training` a run whose unit `pinka-gpu-job@<run>.service` is
  not running.
- `/var/lib/pinka/gpu/transitions.jsonl` has a line that is not JSON.

Read the arbiter's log, then install the arbiter again; the boot unit
settles a run whose unit is gone:

<!-- qa:skip reason="changes the lab host" -->
```bash
journalctl -u 'pinka-gpu-*' -b
sudo ./bootstrap/bootstrap.sh --machine <machine> --tags gpu_arbiter
sudo systemctl restart pinka-gpu-arbiter.service
```

`<machine>` is the name of this host's host_vars file. Never edit
`state.json` or `transitions.jsonl` by hand. See
[The GPU arbiter](../explanation/gpu-arbiter.md). Skipped on a `lab` node,
which has no arbiter.

## `training.containers`: a training job's container has other options {#check-training-containers}

A running `pinka-job-<run>` container has a network, a writable root file
system, a capability, no `no-new-privileges`, an unconfined profile, a host
namespace, runs as root, or has a mount other than the base model and
datasets (read-only) and the run's `output/` and `checkpoints/`. The arbiter
never starts such a container: something else started or changed it. Stop
the run and install the arbiter again:

<!-- qa:skip reason="stops a training run on the lab host" -->
```bash
sudo pinka gpu idle
sudo ./bootstrap/bootstrap.sh --machine <machine> --tags gpu_arbiter
```

`<machine>` is the name of this host's host_vars file. Treat the run's
adapter as untrusted: it is not registered when the run did not end
`succeeded`. [GPU arbiter requests](../reference/gpu-arbiter.md#the-jobs-container)
lists the options. Skipped while no run is running.

## `training.adapters`: a registered adapter is damaged {#check-training-adapters}

An adapter directory below `/srv/pinka/models/adapters/<name>/<sha256>/`
is not root's with mode 0555, has no `MANIFEST.json` or `LINEAGE.json`, its
manifest names another hash, or its files no longer match the hash in its
name. A registered adapter is never changed; remove the damaged directory
and train it again (a deployment that names it is refused until then):

<!-- qa:skip reason="removes an adapter on the lab host" -->
```bash
sudo rm -rf /srv/pinka/models/adapters/<name>/<sha256>
```

`<name>` and `<sha256>` are the adapter's name and hash from doctor's
message. [How to run a training job](run-training-job.md) shows how to train
it again. Skipped while no adapter is registered.

## `training.datasets`: a published dataset is damaged or lists test instances {#check-training-datasets}

A dataset below `/srv/pinka/training/datasets/<sha256>/` has no
`PROVENANCE.json`, its provenance names another hash, it lists an instance
of the `test` split, its files no longer match its hash, or it is not
`root:pinka-datasets` 0750. The arbiter refuses to mount it either way.
Remove it and have the platform publish it again (a dataset with test
instances is a platform defect: report it to the platform's developers):

<!-- qa:skip reason="removes a dataset on the lab host" -->
```bash
sudo rm -rf /srv/pinka/training/datasets/<sha256>
```

`<sha256>` is the dataset's hash from doctor's message. Skipped while no
dataset is published, and on hosts that do not train.

## `training.caps`: a training job exceeds this host's caps {#check-training-caps}

A warning: a job of the active platform asks for more memory, CPUs or run
time than this host's `training_caps` allow, so the GPU arbiter refuses
every run of it (bootstrap printed the same warning when the platform was
deployed). Either raise the caps in the machine's host_vars and apply them,
or deploy a release whose jobs ask for less:

<!-- qa:skip reason="changes the lab host" -->
```bash
sudo ./bootstrap/bootstrap.sh --machine <machine> --tags gpu_arbiter,doctor
```

`<machine>` is the name of this host's host_vars file, in which
`training_caps` holds the new values. Skipped on hosts that do not train and
while the platform declares no job.

## `training.availability`: whether this host trains {#check-training-availability}

A notice, not an error: training runs only on a `full` node. On a `lab` or
`inference` node the arbiter refuses `train` and `publish-dataset`, and
`pinka status` says so. Training on a lab and inference pair is future work;
to train, use a `full` node ([Node roles](../node-roles.md)).

## `inference.isolation`: the inference guard admits the wrong accounts {#check-inference-isolation}

Only root, infra's scrapers (`pinka_inference_clients`) and the active
platform's controller and inference broker reach the inference API on
`127.0.0.1` (vLLM, or on a lab node the inference forwarder); only root and
the controller reach the forwarder's control port. Doctor checks that the
nftables table `inet pinka_inference_guard` is loaded and that its sets
`inference_clients` and `inference_control_clients` admit exactly the UIDs
of those accounts on their ports. The guard resolves the platform's accounts
when it loads, so a deployment that did not reload it leaves the old ones.
Load the guard again:

<!-- qa:skip reason="changes the firewall of the lab host" -->
```bash
sudo systemctl restart pinka-inference-guard.service
sudo ./bootstrap/bootstrap.sh --machine <machine> --tags firewall,platform
```

`<machine>` is the name of this host's host_vars file; run bootstrap when
the restart does not help. `systemctl status pinka-inference-guard.service`
shows why the guard did not load. See
[ADR-0016](../adr/0016-telemetry-query-isolation.md) for why accounts, not
vLLM's API key, decide.

## `incus.pool`: wrong backend or little free space {#check-incus-pool}

The Incus pool uses another driver than preflight chose, or has less free
space than `doctor_min_pool_free_gib`. Delete unused instances and images
(`incus list --all-projects`, `incus image list`) or grow the pool. The pool
is never recreated by bootstrap.

## `incus.objects`: a project or profile is missing {#check-incus-objects}

The `bench` or `build` project or one of the lab's profiles is missing. Run
bootstrap with `--tags incus`; it creates missing objects and never deletes
any.

## `incus.certificates`: a client certificate is missing or untrusted {#check-incus-certificates}

A service's client certificate or key under `/etc/pinka/incus/` is missing,
has the wrong owner or mode, or Incus does not trust it. Run bootstrap with
`--tags incus`; to replace the certificates, see
[How to rotate secrets and certificates](rotate-secrets.md).

## `incus.metrics`: Prometheus does not scrape Incus {#check-incus-metrics}

Prometheus has no `incus` target or it is down. Check the metrics
certificate (`/etc/pinka/incus/metrics/`) and the target page through
[Grafana and Prometheus](reach-grafana.md); run bootstrap with
`--tags incus,observability`.

## `incus.events`: the Incus event feed is not running {#check-incus-events}

`pinka-incus-events.service` copies Incus lifecycle events into the journal.
Restart it with `sudo systemctl restart pinka-incus-events.service` and read
its log if it fails again.

## `layout.tree`: a directory has the wrong owner, mode or ACL {#check-layout-tree}

A directory of the research space or of `/etc/pinka` differs from the layout
in [Lab host architecture](../architecture.md). Run bootstrap with
`--tags layout`; it restores owners, modes and default ACLs.

## `accounts.groups`: an account is in a privileged group {#check-accounts-groups}

A researcher is in `docker` or `incus-admin`, or a service account is in
`docker`. Pokus Infrastructure (pinka) never adds anyone there; remove the membership:

<!-- qa:skip reason="changes group memberships" -->
```bash
sudo gpasswd -d <user> <group>
```

`<user>` and `<group>` are named in the doctor output. An admin who needs
Docker on purpose goes into `break_glass_docker` in the host_vars file
instead (logged loudly).

## `deploy.provenance`: a deployed path has `.git` or restricted content outside the restricted area {#check-deploy-provenance}

A release or content package under `/srv/pinka/platforms/<platform>/` or
`/srv/pinka/restricted/<platform>/` contains a `.git` directory, or the
provenance record (`DEPLOYED_FROM.json`) of a restricted content package
appears outside `/srv/pinka/restricted/<platform>/` (resolution B2,
[Restricted data](../contracts.md#restricted-data)). `<platform>` is the
platform's name. Remove that deployment and deploy again from the mirror
with bootstrap.

## `platform.manifest`: the active release's manifest changed or no longer validates {#check-platform-manifest}

The manifest of the active release (`platform-manifest.yaml` in the release
directory `active.json` names) must have the SHA-256 that
`/etc/pinka/platform/active.json` and the release's `DEPLOYED_FROM.json`
record, and must still pass `pinka-platform validate` against the installed
contract. A changed hash means someone edited a deployed release; a refusal
after an infra update means the release needs a version that follows the
contract. Never edit a deployed release: deploy it again from its mirror with
bootstrap (`--tags platform`), or deploy a newer release. Skipped when no
platform is active.

## `systemd.hardening`: a platform unit lacks its hardening {#check-systemd-hardening}

A platform service's systemd properties differ from what its profile
requires: the account, the baseline of `NoNewPrivileges`, `ProtectSystem` and
the rest, `PrivateNetwork` and `RestrictAddressFamilies` of the agent, or the
`InaccessiblePaths` that hide infra's internals. The expected values are in
`hardening` of `/etc/pinka/platform/rendered.json`, written when the platform
is rendered; compare with `systemctl show UNIT`. A drop-in under
`/etc/systemd/system/UNIT.d/` that overrides a property is the usual cause:
remove it, then run bootstrap with `--tags platform`. On an inference node the
check covers `pinka-inference-control.service`; run bootstrap with
`--tags inference_control` there. Skipped when neither applies.

## `sockets.permissions`: a platform socket has the wrong owner, mode or ACL {#check-sockets-permissions}

A platform socket differs from its rendering (`sockets` of
`/etc/pinka/platform/rendered.json`: owner, group, mode and ACL entries).
With ACL entries, `ls -l` shows the ACL mask in the group bits;
`getfacl SOCKET` shows the entries. The check's result lists the dedicated
agent sockets (`untrusted_clients`). Restart the owning socket unit, which sets
them again when it starts; if that does not help, run bootstrap with
`--tags platform`. A socket that fell back to 0666 is reported by
[`sockets.acl_fallback`](#check-sockets-acl_fallback) instead. Skipped until
the platform is deployed.

## `sockets.agent_view`: the agent sees beyond its allowlist {#check-sockets-agent_view}

Doctor walks the running `untrusted-agent` service's file system through
`/proc/<pid>/root`, which resolves paths in the agent's own mount namespace,
and lists every socket it finds. The agent may see only its dedicated sockets,
the agent telemetry socket and sockets in its own runtime and state
directories (`agent_view` in `hardening` of
`/etc/pinka/platform/rendered.json`). Doctor also lists what the agent sees
of the release: only its own package and its own environment
(`release_paths`); a manifest, another package, migrations or the shared
`.venv` in that list fail the check too. Anything else means its allowlist leaks:
run bootstrap with `--tags platform` and restart the platform
(`sudo pinka stop && sudo pinka start`); if the socket is still visible,
report it as a bug, because the agent must never reach it. Skipped while no
agent runs.

## `sockets.acl_fallback`: a platform socket fell back to mode 0666 {#check-sockets-acl_fallback}

A platform socket that researchers or admins may use gets ACL entries for
their groups (`pinka`, `pinka-admin`) when its unit starts. Where `/run` has
no ACL support, the helper makes the socket 0666 instead, records it in
`socket_acl_fallback` of `/etc/pinka/platform/rendered.json`, and the
platform's peer-credential check alone separates researchers, admins and
services. Doctor warns about each such socket, recorded or found at mode
0666. Use a kernel whose tmpfs supports POSIX ACLs (`CONFIG_TMPFS_POSIX_ACL`),
then restart the platform:

<!-- qa:skip reason="restarts the lab on the lab host" -->
```bash
sudo pinka stop && sudo pinka start
```

At the next start the helper sets the ACL entries and removes the socket
from the record. Skipped until the platform is deployed, or when no socket
has researchers or admins among its clients.

## `telemetry.isolation`: the telemetry guard, the query link or a telemetry socket is wrong {#check-telemetry-isolation}

Only infra's own clients (`pinka_telemetry_clients`: root, the collector,
Prometheus, Loki, Tempo and the query proxy) reach the telemetry stores
directly, and only root and Grafana (`pinka_query_port_clients`) the query
proxy's loopback port; everyone else queries through the query proxy and
sends to the collector. Doctor checks that the nftables table
`inet pinka_telemetry_guard` is loaded, that its sets `telemetry_clients`
and `query_port_clients` admit exactly the UIDs of those accounts and that
each set's rule covers its ports; that `/run/pinka/query.sock` is a
link to `query/query.sock`, whose directory `/run/pinka/query` is
`pinka-query-proxy:pinka-query` 0750; that `/run/pinka/query/query.sock` is
0660 with group `pinka-query`; and that the agent OTLP socket exists. Load
the guard and recreate the sockets:

<!-- qa:skip reason="changes the firewall of the lab host" -->
```bash
sudo ./bootstrap/bootstrap.sh --machine <machine> --tags firewall,observability
```

`<machine>` is the name of this host's host_vars file.
`systemctl status pinka-telemetry-guard.service pinka-otlp-agent.socket` and
`docker logs pinka-query-proxy` show why one of them is not running. See
[How telemetry flows through the lab](../explanation/telemetry.md).

## `postgres.roles`: database roles or settings are wrong {#check-postgres-roles}

A database role is missing, the reader or Grafana role can use the ground
truth schema `gt`, or a durability setting is off. Run bootstrap with
`--tags postgres`; it recreates roles and revokes. Never turn off `fsync`
or `synchronous_commit`.

## `opa.authz`: OPA accepts writes or does not answer {#check-opa-authz}

OPA must answer `/health` and reject policy and data writes (resolution C6).
Run bootstrap with `--tags opa`, which installs the authorization policy and
restarts OPA.

## `telemetry.roundtrip`: the telemetry probe did not arrive {#check-telemetry-roundtrip}

Doctor sent a log record, a span and a metric to the collector and at least
one store did not show it in time. Read `journalctl -u pinka-observability.service -b`
and `sudo docker logs pinka-otel-gateway`; check that Loki, Tempo and
Prometheus run (`services.containers`). A just-started stack can need a
minute; check again before acting.

## `time.sync`: the clock is not synchronized {#check-time-sync}

The clock is not NTP-synchronized or its offset is too large. Check
`timedatectl status` and the time daemon (systemd-timesyncd or chrony). On
WSL the clock often jumps after Windows resumes from sleep; run
`wsl --shutdown` on Windows and start the distribution again.

## `backup.recent`: backups are missing or old {#check-backup-recent}

No backup repository is configured, the last successful backup is older than
`doctor_backup_max_age_hours`, or the repository does not answer. See
[How to back up the lab](backup-and-restore.md); the backup log is in
`journalctl -u pinka-backup.service`.

## `backup.platform_state`: a platform state directory is not backed up {#check-backup-platform_state}

The platform's manifest declares state directories for its services, and
lists in `backup.paths` those that backups include. Doctor names every
state directory that `backup.paths` leaves out. If its data matter, add it
to `backup.paths` in the platform's manifest and deploy the release; if not
(a cache, for example), the warning stays as a reminder.

## `lab.window`: a maintenance window is recorded {#check-lab-window}

On production, `/etc/pinka/maintenance-window.json` exists: a staging lab may
be using this machine's hardware, and production stays stopped (also after the
window's end) until an admin runs `sudo pinka window close` and then
`sudo pinka start`. On staging with `staging_shared_hardware: true`, the check
fails when the recorded window is not open now, and notes when none is
recorded: staging starts only inside an open window. See
[How to run a QA maintenance window on staging](staging-window.md#staging-on-productions-hardware).

## `lab.health_gate`: the startup health gate did not pass {#check-lab-health_gate}

The health gate has not run since boot or one of its checks failed; the
failing checks are named. Read `journalctl -u pinka-health-gate.service -b`,
fix the cause, then `sudo pinka start`. See
[How the lab comes up](../explanation/lab-session.md).

## `lab.session`: this boot has no complete session record {#check-lab-session}

The session record of this boot is missing or was written without some
fields (its `errors` say why, for example the fingerprint could not be
computed). Fix the cause, then write the record again:

<!-- qa:skip reason="rewrites this boot's session record" -->
```bash
sudo systemctl restart pinka-session-start.service
```

## `lab.rolling_drift`: the system changed during the session {#check-lab-rolling_drift}

On a rolling release the environment fingerprint differs from the one
recorded at session start, usually because packages were updated. Results
before and after the change are not comparable for latency and resources.
Update only with [`pinka update-host`](update-host.md), which records the
change, and start a new session by restarting the lab.

## `resources.cpusets`: lab services are not on their CPUs {#check-resources-cpusets}

`cpuset_services` or `cpuset_targets` (host_vars) names CPUs that this host
does not have, `pinka-services.slice` runs on other CPUs than
`cpuset_services`, or the two threads of one core are in different sets. On WSL the number of CPUs comes from `processors=` in
`.wslconfig`. Set both lists to CPUs the host has (they must not overlap),
run bootstrap, then restart the lab so that the services and containers start
in the slice again:

<!-- qa:skip reason="restarts the lab" -->
```bash
sudo pinka stop
sudo pinka start
```

"No lab service is running" means the lab is stopped; start it first.

"SMT siblings of one core are split" names the cores whose two threads are
in different sets: targets on one thread share the core with lab services
on the other. Move both threads of each named core into the same set (the
pairs are in `/sys/devices/system/cpu/cpu<n>/topology/thread_siblings_list`),
run bootstrap and restart the lab as above. See
[How benchmark targets are isolated](../explanation/target-isolation.md#cpu-sets).

## `wsl.config`: WSL settings differ from the expectation {#check-wsl-config}

`/etc/wsl.conf` has interop or automount enabled or systemd disabled, or the
memory, CPUs or `/dev/kvm` differ from host_vars `expected`. Bootstrap writes
`wsl.conf`; memory, CPUs and nested virtualization come from `.wslconfig` on
Windows. Copy the recommended file (`pinka wsl-helpers` shows where it
is), then run `wsl --shutdown` on Windows. See [WSL2 hosts](../providers/wsl2.md).

## `smoke.<blueprint>`: a throwaway target failed {#check-smoke}

With `--smoke`, doctor launches the newest image of every blueprint. A
failure names the image and the problem: systemd degraded or a service
inactive (inspect the image with
[How to build golden images](build-golden-images.md)), no log line in Loki
(the image's telemetry agent or the proxy device), the internet or the lab
host reachable from the target (the run ACL `pinka-run-default` and the
firewall; run bootstrap with `--tags incus,firewall`). `<blueprint>` is the
blueprint's name.

## Verify the fix

1. Run doctor again:

    <!-- qa:run env=E2 -->
    ```bash
    sudo pinka doctor
    ```

    The check now shows `ok` (or `skip` where it does not apply). Doctor exits
    with 0, or 1 if only warnings remain.
