# Build Prompt: `pokus-infra` — Infrastructure-as-Code for the Pokus Research Lab

> Paste this whole file into Claude Code as the initial task, or commit it as `docs/BUILD_PROMPT.md` and tell Claude Code: "Read docs/BUILD_PROMPT.md completely and build the repository it describes."

---

## 0. How you (Claude Code) must work on this task

You are building a production-quality infrastructure-as-code repository for a long-lived academic research lab. A small research team will depend on it, it will be published for anyone to use, and academic publications will cite how the lab was built. Correctness, safety, idempotency, and auditability matter more than speed.

Working rules:

1. **Read this entire document before writing any file.** Then write `docs/IMPLEMENTATION_NOTES.md` containing your understanding of the scope, the list of files you intend to create, and every open question. Ask me the open questions before implementing anything that depends on them. Do not guess on: Tailscale tag names, researcher usernames, Git remote URLs, backup destination, or anything that would destroy data.
2. **Never invent version numbers, image digests, package versions, checksums, or download URLs.** Look them up (official release pages, apt repositories, container registries) at build time and record them in `versions.lock.yml` with a `verified_on` date and `source` URL. If you cannot verify something, write `TODO(verify)` and list it in `docs/IMPLEMENTATION_NOTES.md`. A `make lock-check` target must fail while any `TODO(verify)` remains.
3. **Do not run provisioning against the machine you are working on** unless I explicitly ask. Building the repo means writing code, tests, and docs. You may run linters, unit tests, `ansible-playbook --syntax-check`, and the preflight tool in `--root <fixture>` mode freely. Never run `bootstrap.sh`, `ansible-playbook` without `--check`, `mkfs`, `wipefs`, `incus admin init`, `tailscale up`, or anything touching `/etc`, disks, or network config on the real host without asking first.
4. **Work in small, reviewable commits** using Conventional Commits (`feat(preflight): ...`, `fix(incus): ...`, `docs: ...`). Each commit must pass `make lint test`.
5. **Idempotency is mandatory.** Running the full bootstrap twice in a row must produce zero changes on the second run (Ansible `changed=0`), except for explicitly volatile tasks, which must be tagged `volatile` and documented.
6. **Create a `CLAUDE.md`** at the repo root summarizing the invariants in sections 2, 13 and 14 of this document, so future Claude Code sessions follow them without re-reading this prompt.
7. When this document and reality disagree (e.g. a tool's flag was renamed), follow reality, explain the deviation in the commit message, and record it in `docs/IMPLEMENTATION_NOTES.md`.
8. **Documentation follows `docs/DOCS_SPEC.md`** (the Pokus Documentation Specification, committed next to this file). Write documentation **alongside** the code in every step, not at the end: required pages, page conventions, docstrings/argument specs, generated references, and the docs quality gates in that spec are part of every step's definition of done.

---

## 1. Project context (why this repository exists)

The lab is a shared research environment for benchmarking an **AI diagnostic agent** (a locally hosted open-source LLM) on **reproducible, injected Linux faults**. The full system consists of three repositories:

| Repository | Owns | Built by this prompt? |
|---|---|---|
| `pokus-infra` | Host provisioning, preflight/environment detection, lab services, target sandbox infrastructure (Incus), golden image recipes, observability stack, backup/restore, doctor, environment fingerprinting | **Yes** |
| `pokus-platform` | Orchestrator, agent runner, LLM gateway, policy bundle (OPA), executor, injector, verifier, evaluator, DB migrations, `pokus` CLI | No — only the integration contract (§12) |
| `pokus-catalog` | Scenario definitions, ground truth, taxonomy, target blueprints' scenario-facing metadata | No — only the integration contract (§12) |

`pokus-infra` must be fully usable on its own: with `platform_enabled: false` and `catalog_enabled: false` it produces a working host with all shared services, the target sandbox, observability, inference, backups and doctor.

Key requirements carried over from the lab design:

- Linux-only. The lab must not depend on Windows in any way. WSL2 is supported only as "a Linux host that happens to run on Windows".
- **Portable across mainstream Linux distributions** (Debian/Ubuntu, Fedora/RHEL-compatible, openSUSE/SLES, Arch, and their derivatives), across bare metal, VMs and WSL2, and across x86_64 hosts with or without a GPU. Section §19 defines how; it takes precedence over any distro-specific wording elsewhere in this document.
- Portable: a new machine (bare metal, VM, or WSL2) is built with one command from this repo, and existing lab state can be restored onto it.
- A small team of human researchers (the reference setup has three) with separate Linux accounts and SSH keys, a shared `pokus` group, shared `/srv/pokus` research space, and dedicated service accounts per trust boundary.
- Remote access only via Tailscale. No public ports, no router port forwarding, never Tailscale Funnel.
- Test targets run in **Incus** (system containers by default, VMs for kernel-level scenarios), never in the lab's own Docker daemon. The AI agent must never be able to harm the host, lab services, or personal files.
- Local inference with **vLLM** on a single NVIDIA GPU (reference: RTX 4070 12 GB). No external model APIs.
- Observability: OpenTelemetry Collector, Prometheus, Loki, Tempo, Grafana.
- Experiment database: PostgreSQL. Artifacts: content-addressed files under `/srv/pokus/artifacts`.
- Academic reproducibility: everything pinned, every lab session fingerprinted, results auditable.
- No Kubernetes, Slurm, Ray, Nomad, or GPU schedulers. Single machine, clarity over enterprise complexity.

**Reference test profile** (used to choose defaults such as the `ada-12g` vLLM profile and for hardware testing; it describes a class of machine, not any particular installation): x86_64, 24 logical CPUs, 32 GB RAM, one 12 GB NVIDIA Ada-generation GPU (e.g. RTX 4070 class), Ubuntu 24.04 under WSL2 on a workstation that is regularly powered off. Deployments may use any hardware and environment the support matrix allows; the design must tolerate hosts that are not always on, and labs must be movable between machines (e.g. from WSL2 to bare metal).

---

## 2. Supported environments and the preflight gate (core feature)

Before anything is installed, a **preflight tool** must detect what kind of machine it is running on, decide whether the lab is supported there, decide which optional features are available, and either continue, continue in degraded mode with explicit consent, ask the operator to take an action, or abort.

### 2.1 Implementation constraints

- Location: `preflight/pokus_preflight/` (Python package) with entry point `preflight/pokus-preflight` (thin wrapper script).
- **Python standard library only**, and source-compatible with **Python ≥ 3.9** (no `match`, no 3.10+ only syntax; use `from __future__ import annotations`) so `pokus-lab preflight` can also run on an old system interpreter. During bootstrap it runs on the uv-managed CPython (§19.3). No third-party imports.
- Every filesystem and command access goes through an abstraction that accepts a `--root <path>` (fake filesystem root) and a command-runner that can be replaced by fixtures. This makes detection fully unit-testable without the real hardware (see §11).
- Output:
  - human-readable summary to stderr (colored only when stderr is a TTY),
  - machine-readable JSON report to stdout or `--output <file>`,
  - on a real run (not `--root`), the report is written to `/etc/pokus/host-profile.json` (0644 root:root) and archived as `/var/lib/pokus/preflight/<UTC-timestamp>.json`.
- The JSON report must validate against `preflight/schema/host-profile.schema.json` (JSON Schema draft 2020-12). Include `schema_version`.

### 2.2 What to detect

**Operating system**

- Parse `/etc/os-release`: `ID`, `ID_LIKE`, `VERSION_ID`, `VERSION_CODENAME`, `PRETTY_NAME`.
- Architecture via `uname -m` / `platform.machine()`. `x86_64` is the reference architecture; `aarch64` is experimental (§19.2).
- Kernel release and version string (`/proc/sys/kernel/osrelease`, `/proc/version`).
- Init system: PID 1 must be systemd (`/proc/1/comm` == `systemd`). Record `systemctl is-system-running` state.

**Environment class** — exactly one of: `baremetal`, `wsl2`, `wsl1`, `vm`, `container`, `unknown`.

Use multiple signals, cross-check them, and record every signal and its value in the report (`environment.signals`), plus a `confidence` (`high` / `medium` / `low`) and any conflicts.

| Signal | Source |
|---|---|
| WSL kernel string | `/proc/sys/kernel/osrelease` contains `microsoft` (case-insensitive). `WSL2` substring or a 5.x/6.x kernel → WSL2. A `4.4.0-*-Microsoft` style kernel → WSL1. |
| WSL GPU device | presence of `/dev/dxg` |
| WSL libraries | presence of `/usr/lib/wsl/lib/` |
| WSL interop | `/proc/sys/fs/binfmt_misc/WSLInterop*` (record whether interop is enabled) |
| WSL config | parse `/etc/wsl.conf` if present (systemd, automount, interop, appendWindowsPath, hostname) |
| systemd-detect-virt | `systemd-detect-virt --vm`, `systemd-detect-virt --container`, and plain `systemd-detect-virt` |
| CPU hypervisor flag | `hypervisor` in `/proc/cpuinfo` flags |
| DMI | `/sys/class/dmi/id/{sys_vendor,product_name,board_vendor,bios_vendor}` |
| Container markers | `/.dockerenv`, `/run/.containerenv`, `container=` in `/proc/1/environ`, cgroup paths in `/proc/1/cgroup` |

**Important gotcha:** on WSL2, recent systemd versions report `wsl` from `systemd-detect-virt` (and from `--container`). WSL detection must therefore run **before** container classification, and `wsl` must never be classified as `container`.

For `vm`, also determine `hypervisor`: one of `kvm`, `qemu`, `microsoft` (Hyper-V), `vmware`, `oracle` (VirtualBox), `xen`, `parallels`, `amazon`, `google`, `other`. Use `systemd-detect-virt --vm` as primary and DMI as secondary.

**Hardware and capabilities**

| Capability | How to detect |
|---|---|
| CPU | model name, logical CPUs (`os.cpu_count()` and `/sys/fs/cgroup/cpu.max` if constrained), `vmx`/`svm` flags |
| Memory | `MemTotal`, `SwapTotal` from `/proc/meminfo` |
| Disk | free space and filesystem type for `/var/lib` (Incus/Docker) and for the configured `/srv/pokus` parent; block devices from `/sys/block` (for informational listing only) |
| cgroup v2 | `/sys/fs/cgroup/cgroup.controllers` exists and `stat -f` type is `cgroup2fs`; record available controllers (cpu, cpuset, memory, io, pids must be present) |
| Kernel features | `/proc/filesystems` (`btrfs`, `overlay`), `/proc/modules` or `/sys/module/<name>` and `modprobe -n` dry-run for: `br_netfilter`, `overlay`, `nf_tables`, `veth`, `tun`, `vhost_net`, `vhost_vsock`, `kvm`, `kvm_intel`/`kvm_amd`, `btrfs`, `dm_thin_pool`. On WSL many are built in, so check `/sys/module/<name>` as well as `/proc/modules` and treat built-in as present. |
| `/dev/net/tun` | exists and is a character device (Tailscale) |
| KVM | `/dev/kvm` exists, is a char device, and can be opened read-write by root; record nested virtualization parameter (`/sys/module/kvm_intel/parameters/nested` or `kvm_amd`) |
| GPU (see below) | vendor, model, VRAM, driver, CUDA path, compute capability |
| Time sync | `timedatectl show` (`NTPSynchronized`, `TimeUSec`), record offset if available |
| Network reachability | DNS resolution and HTTPS HEAD/GET (with short timeout) to every host listed in `preflight/endpoints.yml` (the detected distribution's package mirrors and every third-party repository the component source matrix selects for it (§19.4), container registries, Hugging Face, GitHub). Report each result; do not fail hard on optional endpoints. Respect `--offline`. |
| Secure Boot (baremetal) | `mokutil --sb-state` if available, else `/sys/firmware/efi/efivars/SecureBoot-*` |
| Nouveau | `nouveau` loaded (baremetal/vm) |

**GPU detection by environment**

- `wsl2`: GPU is available only via the Windows driver. Check `/dev/dxg`, `/usr/lib/wsl/lib/libcuda.so*`, and run `/usr/lib/wsl/lib/nvidia-smi --query-gpu=name,memory.total,driver_version,compute_cap --format=csv,noheader`. **Never** suggest installing a Linux NVIDIA kernel driver on WSL. If `/proc/driver/nvidia` exists or `nvidia-dkms-*` is installed inside WSL, report a **blocking conflict** with remediation text.
- `baremetal`: find NVIDIA devices via `/sys/bus/pci/devices/*/vendor == 0x10de` with class `0x0300`/`0x0302`. Then determine driver state: not installed / installed but not loaded / loaded (`/proc/driver/nvidia/version`). If loaded, run `nvidia-smi` for name, VRAM, driver, compute capability.
- `vm`: same as baremetal, but an NVIDIA PCI device is present only with PCI passthrough. Absence is normal and must be reported as "no GPU passthrough", not as an error.
- `container`/`wsl1`/`unknown`: do not probe GPU beyond reporting.

**Conflicting software** (each reported as `conflict` with severity `blocking` or `warning` and a remediation string):

| Conflict | Detection | Severity |
|---|---|---|
| Docker Desktop WSL integration | `/mnt/wsl/docker-desktop*` exists, or `docker` on PATH resolves into `/mnt/wsl/` or `docker context ls` shows `desktop-linux`, or `/var/run/docker.sock` is a symlink into `/mnt/wsl` | blocking |
| Snap Docker | `snap list docker` succeeds | blocking |
| LXD installed | `snap list lxd` or `lxd` binary/`lxdbr0` | blocking (Incus migration is out of scope) |
| Host firewall manager active (ufw, firewalld) | `systemctl is-active ufw/firewalld` | info — the firewall role integrates with it instead of replacing it (§19.6); report which backend will be used |
| Image-based / immutable OS | `/run/ostree-booted` (Fedora Atomic, CoreOS), `/etc/NIXOS`, transactional-update with read-only root (openSUSE MicroOS/Aeon), Ubuntu Core (`/writable` + snapd-only) | blocking — remediation: run a supported distro in a VM on this host (`vm` provider) |
| Non-systemd init or musl libc | PID 1 not systemd (Alpine/OpenRC, Devuan, Void, Gentoo OpenRC), musl via `ldd --version` / `/lib/ld-musl-*` | blocking — same VM remediation |
| SELinux / AppArmor state | `getenforce` or `/sys/fs/selinux/enforce`; `/sys/kernel/security/apparmor` | info — recorded; the `mac` role adapts (§19.7). SELinux in enforcing mode is supported and must never be disabled by the repo |
| Package manager lock / unfinished transaction | apt/dpkg locks, `dnf` history incomplete, zypper lock, pacman `db.lck` | needs_action |
| Linux NVIDIA driver inside WSL | see above | blocking |
| Podman socket active | `systemctl is-active podman.socket` | warning |
| Existing non-managed Docker daemon config | `/etc/docker/daemon.json` exists without the repo's managed header comment | warning (will be replaced; backup taken) |
| Existing Incus with non-managed config | `incus` installed and `/var/lib/incus` not empty and no `/etc/pokus/managed-incus` marker | blocking unless `--adopt-incus` |

### 2.3 The decision: `support-matrix.yml`

The decision logic must be **data-driven** from `preflight/support-matrix.yml`, not hard-coded `if` chains. Python code evaluates the matrix; the matrix is human-readable and documented. Also generate `docs/support-matrix.md` from it (`make docs`).

Structure (adapt as needed, keep it declarative):

```yaml
schema_version: 1

families:                       # implementation units; every distro-specific role has vars/<family>.yml
  debian:    {pkg: apt}
  redhat:    {pkg: dnf}
  suse:      {pkg: zypper}
  archlinux: {pkg: pacman}

os:                              # first match wins; versions in quotes are examples to verify at build time
  # supported: full provisioning tested in VMs on every release AND on the reference GPU hardware
  - {match: {id: ubuntu, version_id: "24.04"}, family: debian, tier: supported, release_model: fixed}
  - {match: {id: debian, version_id: "13"},    family: debian, tier: supported, release_model: fixed}
  # tested: full provisioning + doctor --smoke tested in VMs in CI (§19.9); GPU paths not hardware-tested
  - {match: {id: ubuntu, version_id: "22.04"}, family: debian, tier: tested, release_model: fixed}
  - {match: {id: debian, version_id: "12"},    family: debian, tier: tested, release_model: fixed}
  - {match: {id: fedora, version_id_in: "@versions.lock:fedora_supported_releases"}, family: redhat, tier: tested, release_model: fast}
  - {match: {id_in: [rhel, almalinux, rocky, centos], version_major_in: ["9", "10"]}, family: redhat, tier: tested, release_model: fixed}
  - {match: {id: opensuse-leap, version_id_in: ["15.6", "16.0"]}, family: suse, tier: tested, release_model: fixed}
  - {match: {id: opensuse-tumbleweed}, family: suse, tier: tested, release_model: rolling}
  - {match: {id: sles, version_major_in: ["15", "16"]}, family: suse, tier: experimental, release_model: fixed}
  - {match: {id: arch}, family: archlinux, tier: tested, release_model: rolling}
  # experimental: derivatives mapped to their parent through ID_LIKE (Mint, Pop!_OS, Zorin, elementary,
  # Kali, Oracle Linux, Manjaro, EndeavourOS, ...). Third-party repos use the PARENT release identity
  # (UBUNTU_CODENAME / DEBIAN_CODENAME / VERSION_ID mapping), never the derivative's own codename.
  - {match: {id_like_contains: ubuntu}, family: debian, tier: experimental, parent_from: UBUNTU_CODENAME}
  - {match: {id_like_contains: debian}, family: debian, tier: experimental, parent_from: DEBIAN_CODENAME}
  - {match: {id_like_contains_any: [rhel, fedora, centos]}, family: redhat, tier: experimental}
  - {match: {id_like_contains: suse}, family: suse, tier: experimental}
  - {match: {id_like_contains: arch}, family: archlinux, tier: experimental, release_model: rolling}
  - match: {any: true}
    tier: unsupported
    reason: "Distribution family not implemented. Run a supported distribution in a VM on this host (vm provider), or add a family (docs/distros/adding-a-family.md)"

arch:
  - match: {machine: x86_64}
    tier: supported
  - match: {machine: aarch64}
    tier: experimental           # core lab only; gpu_inference requires verified arm64 vLLM images; golden images built per arch
  - match: {any: true}
    tier: unsupported

environment:
  baremetal: {tier: supported, provider: baremetal}
  wsl2:      {tier: supported, provider: wsl2}
  vm:        {tier: supported, provider: vm}
  wsl1:      {tier: unsupported, reason: "WSL1 has no real Linux kernel; convert with 'wsl --set-version <distro> 2'"}
  container: {tier: unsupported, reason: "The lab needs its own kernel features, systemd as PID 1, and nested container/VM capabilities"}
  unknown:   {tier: unsupported, reason: "Could not determine the environment reliably"}

minimums:
  memory_gib:  {required: 16, recommended: 24}
  cpu_threads: {required: 8,  recommended: 16}
  free_disk_gib:
    var_lib:   {required: 150, recommended: 400}
    srv_pokus: {required: 100, recommended: 500}

core_requirements:            # failing any of these → unsupported (or needs_action)
  - systemd_pid1
  - cgroup2_unified
  - cgroup2_controllers: [cpu, cpuset, memory, io, pids]
  - dev_net_tun
  - kernel_features: [overlay, br_netfilter, nf_tables, veth]
  - no_blocking_conflicts

features:                     # optional; each becomes a boolean feature flag
  gpu_inference:
    requires: [nvidia_gpu_usable, gpu_vram_gib_min: 8, gpu_compute_capability_min: "7.0"]
    on_missing: degrade       # lab installs without vLLM
  vm_targets:
    requires: [kvm_usable]
    on_missing: degrade       # kernel-level scenarios unavailable
  btrfs_pool:
    requires: [btrfs_available]
    on_missing: fallback      # fallback: lvm_thin_pool if dm_thin_pool available, else unsupported
  lvm_thin_pool:
    requires: [dm_thin_pool_available]
    on_missing: degrade
  dcgm_metrics:
    requires: [environment_in: [baremetal, vm], nvidia_driver_loaded]
    on_missing: fallback      # fallback: nvidia-smi based exporter

provider_rules:
  wsl2:
    needs_action:
      - when: {wsl_conf_systemd: false}
        action: "Enable systemd in /etc/wsl.conf (the bootstrap can write it), then run 'wsl --terminate <distro>' from Windows and re-run bootstrap"
    warnings:
      - when: {wsl_interop_enabled: true}
        message: "Windows interop is enabled; the wsl2 provider will disable it"
      - when: {wsl_automount_enabled: true}
        message: "Windows drive automount is enabled; the wsl2 provider will disable it"
  baremetal:
    needs_action:
      - when: {secure_boot: enabled, nvidia_driver_installed: false, nvidia_gpu_present: true}
        action: "Secure Boot is on: the NVIDIA DKMS module must be signed and a MOK enrolled at next boot. See docs/providers/baremetal.md"
  vm:
    warnings:
      - when: {hypervisor_in: [microsoft, vmware, oracle], kvm_usable: false}
        message: "Nested virtualization unavailable in this hypervisor; VM-class targets disabled"
```

Note: storage must end up as either `btrfs_pool` or `lvm_thin_pool`. If neither is available, the result is `unsupported`, because a `dir` storage backend cannot enforce disk quotas and disk-exhaustion scenarios would fill the host disk.

### 2.4 Outcomes and exit codes

| Exit code | Outcome | Meaning | Bootstrap behavior |
|---|---|---|---|
| 0 | `supported` | All core requirements met; features computed | continue |
| 10 | `degraded` | Core OK, one or more optional features unavailable | continue only with `--accept-degraded` or interactive confirmation listing exactly which features are disabled and what that means for research (e.g. "no GPU: vLLM will not be installed; inference campaigns cannot run on this host") |
| 20 | `unsupported` | Core requirement failed, unsupported OS/arch/environment, or blocking conflict | abort; `--force-unsupported` continues but marks the host profile `override.force_unsupported=true`, which is carried into every environment fingerprint |
| 30 | `needs_action` | Fixable by operator action (reboot, `wsl --terminate`, enroll MOK, remove conflict) | print exact remediation steps and abort; re-run after action |
| 40 | `preflight_error` | Detection itself failed unexpectedly | abort with traceback in log |

Also report `tier` separately (`supported` / `tested` / `experimental`) and `release_model` (`fixed` / `fast` / `rolling`). `supported` and `tested` continue without extra consent (`tested` prints a note that GPU paths are not hardware-tested on this distro). `experimental` requires `--accept-experimental` (or interactive confirmation). `rolling` prints a reproducibility warning (§19.5). All of it is recorded in the profile.

### 2.5 Host profile JSON (abbreviated example)

```json
{
  "schema_version": 1,
  "generated_at": "2026-10-05T09:12:44Z",
  "preflight_version": "1.0.0",
  "repo_commit": "abc1234",
  "outcome": "degraded",
  "tier": "supported",
  "provider": "wsl2",
  "os": {"id": "ubuntu", "version_id": "24.04", "codename": "noble", "arch": "x86_64"},
  "kernel": {"release": "6.6.87.2-microsoft-standard-WSL2", "version": "..."},
  "environment": {
    "class": "wsl2", "hypervisor": "microsoft", "confidence": "high",
    "signals": {"osrelease_microsoft": true, "dev_dxg": true, "systemd_detect_virt": "wsl"},
    "conflicts_between_signals": []
  },
  "wsl": {"systemd": true, "automount": false, "interop": false, "append_windows_path": false, "hostname": "pokus-lab"},
  "hardware": {"cpu_model": "...", "cpu_threads": 20, "memory_gib": 22.0, "swap_gib": 8.0},
  "gpu": {"present": true, "usable": true, "name": "NVIDIA GeForce RTX 4070", "vram_gib": 12.0,
          "driver": "...", "compute_capability": "8.9", "path": "wsl-dxg"},
  "capabilities": {"cgroup2": true, "kvm_usable": false, "btrfs": true, "dm_thin_pool": false, "tun": true},
  "features": {"gpu_inference": true, "vm_targets": false, "btrfs_pool": true, "lvm_thin_pool": false, "dcgm_metrics": false},
  "storage_backend": "btrfs",
  "conflicts": [],
  "warnings": ["vm_targets disabled: /dev/kvm not available (enable nestedVirtualization on the Windows side)"],
  "needs_action": [],
  "overrides": {"accept_degraded": true, "accept_experimental": false, "force_unsupported": false}
}
```

### 2.6 Re-validation

- Ansible's first role (`preflight_gate`) must re-run preflight in JSON mode and **fail** if the outcome, provider, or feature flags differ from `/etc/pokus/host-profile.json` without `--refresh-profile`. This prevents provisioning a host whose capabilities silently changed (e.g. GPU disappeared, KVM lost).
- `pokus-lab doctor` re-runs preflight too (§9).

---

## 3. Provider model

Everything host-specific lives in exactly one provider role. All other roles must be provider-agnostic and read feature flags from the host profile, never re-detect on their own.

### 3.1 `provider_wsl2`

- Manage `/etc/wsl.conf` (managed header comment) with: `[boot] systemd=true`, `[automount] enabled=false`, `mountFsTab=false`, `[interop] enabled=false`, `appendWindowsPath=false`, `[network] hostname=<machine_name>`, `generateHosts=true`, `generateResolvConf=true` (configurable).
- If `wsl.conf` changed in a way that requires a restart, or systemd is not PID 1: stop the play with a clear message and **exit code 75 from bootstrap** telling the operator to run, on the Windows side, `wsl --terminate <distro>` (the distro name is read from `WSL_DISTRO_NAME` if present, else printed as a placeholder), then re-run bootstrap. Bootstrap cannot do this itself because interop is disabled by design.
- Never install NVIDIA kernel drivers. Ensure `/usr/lib/wsl/lib` is in the dynamic linker path for the NVIDIA Container Toolkit (follow NVIDIA's current CUDA-on-WSL documentation; verify).
- Storage default: loop-backed btrfs Incus pool inside the distro filesystem (`incus_pool_source: loop`, size from host_vars).
- `.wslconfig` lives on Windows and cannot be managed from Linux. The provider must:
  - verify effective values (memory, CPU count, swap, `/dev/kvm` presence) against `host_vars` expectations and emit warnings in doctor if they differ,
  - render a recommended `.wslconfig` into `/srv/pokus/services/contrib/wslconfig.recommended` for the operator to copy manually.
- Power/keep-alive: the WSL VM lifetime is controlled from Windows. Ship **optional** Windows helpers only in `contrib/windows/` (a PowerShell profile function that wraps `wsl` to start a hidden keep-alive process `wsl.exe -d <distro> --exec /usr/bin/sleep infinity` and calls `SetThreadExecutionState(ES_CONTINUOUS|ES_SYSTEM_REQUIRED)` while the lab runs; plus the `.wslconfig` template). These are documented in `docs/providers/wsl2.md`, are never executed by bootstrap, and nothing in the Linux code depends on them.
- GPU metrics exporter: `nvidia-smi`-based exporter (DCGM is not supported on WSL; verify current status and document).

### 3.2 `provider_baremetal`

- NVIDIA driver installation (only if `nvidia_gpu_present`): `install_nvidia_driver: auto | manual | skip`. `auto` uses the per-family method in §19.8; `manual` means the operator installs the driver and preflight must then detect it as loaded. Blacklist nouveau via a managed modprobe file and rebuild the initramfs with the family's tool (`update-initramfs`, `dracut`, `mkinitcpio`).
- Secure Boot: if enabled, follow the DKMS + MOK flow; detect pending MOK enrollment; stop with `needs_action` and exit code 76 ("reboot required, enroll MOK at the blue MOK Manager screen").
- Reboot handling: if a driver or kernel change requires reboot, stop with exit code 76 and print instructions. Never reboot automatically unless `--allow-reboot` is passed.
- Power policy (`power_policy` in host_vars):
  - `never_sleep`: mask `sleep.target suspend.target hibernate.target hybrid-sleep.target`.
  - `lab_session_inhibit` (default for personal machines): `pokus-lab.target` pulls in `pokus-sleep-inhibit.service`, which holds a `systemd-inhibit --what=sleep:idle --mode=block` lock for as long as the lab is up.
  - `os_default`: do nothing.
- Storage: dedicated block device or partition for the Incus pool (`incus_pool_source: device`), with the safety rule in §13. Loop pool allowed as fallback.
- GPU metrics: DCGM exporter if `dcgm_metrics` feature is on, else `nvidia-smi` exporter.

### 3.3 `provider_vm`

- Same as baremetal for drivers and storage, but GPU only if passthrough was detected.
- Install the hypervisor's guest agent where applicable: `qemu-guest-agent` (kvm/qemu), `open-vm-tools` (vmware), `hyperv-daemons`/`linux-cloud-tools` equivalents (microsoft) — verify package names per distro.
- Warn that latency/resource measurements include virtualization overhead; record hypervisor in the fingerprint.

---

## 4. Repository layout

Create this structure (add files as needed, but keep the top-level shape):

```
pokus-infra/
├── README.md
├── CLAUDE.md
├── LICENSE
├── Makefile                          # lint, test, docs, lock-check, check (ansible --check), fixtures
├── .editorconfig
├── .gitattributes                    # *.sh, *.py, *.j2, *.yml → text eol=lf
├── .gitignore
├── .pre-commit-config.yaml
├── .github/workflows/ci.yml          # lint + unit tests + syntax-check + docs drift check
├── versions.lock.yml                 # every external artifact pinned (see §10)
├── models.catalog.yml                # curated, approved candidate models (see §7.12)
├── models.lock.yml                   # models selected for this lab, pinned (see §7.12)
├── bootstrap/
│   ├── bootstrap.sh                  # single entry point on any supported Linux host
│   └── lib/                          # small bash helpers (logging, args, exit codes)
├── preflight/
│   ├── pokus-preflight               # wrapper script
│   ├── pokus_preflight/              # stdlib-only Python package
│   │   ├── __init__.py
│   │   ├── __main__.py
│   │   ├── cli.py
│   │   ├── fs.py                     # root-relative filesystem access
│   │   ├── runner.py                 # command runner (real + fixture)
│   │   ├── detect/
│   │   │   ├── os_release.py
│   │   │   ├── environment.py        # baremetal/wsl2/wsl1/vm/container
│   │   │   ├── hardware.py
│   │   │   ├── kernel.py
│   │   │   ├── gpu.py
│   │   │   ├── network.py
│   │   │   ├── conflicts.py
│   │   │   └── timesync.py
│   │   ├── matrix.py                 # evaluates support-matrix.yml (bundle a tiny YAML subset parser
│   │   │                             #   OR ship the matrix as JSON generated from YAML at build time;
│   │   │                             #   stdlib has no YAML parser — choose and document)
│   │   ├── report.py
│   │   └── render.py                 # human summary
│   ├── support-matrix.yml
│   ├── endpoints.yml
│   ├── schema/host-profile.schema.json
│   └── tests/
│       ├── fixtures/                 # fake roots: see §11
│       └── test_*.py
├── installer/                        # Textual TUI front-end for bootstrap (see §18)
│   ├── pyproject.toml, uv.lock       # pinned, hash-locked
│   ├── schema/answers.schema.json
│   ├── pokus_installer/
│   │   ├── app.py, state.py, answers.py, validators.py, secrets.py
│   │   ├── screens/                  # one module per screen (§18.4)
│   │   ├── widgets/                  # device picker, SSH key input, progress tree, log pane
│   │   ├── runner.py                 # invokes preflight / ansible / doctor, consumes events
│   │   └── theme.tcss
│   └── tests/                        # pilot-driven flow tests + snapshot tests
├── installer-answers/                # <machine>.yml non-secret answers exported by the TUI
├── ansible/
│   ├── ansible.cfg
│   ├── requirements.yml              # pinned collections
│   ├── component-sources.yml         # where each component comes from, per distro (§19.4)
│   ├── site.yml
│   ├── restore.yml
│   ├── rotate.yml                    # secret/cert rotation
│   ├── inventory/
│   │   ├── hosts.yml                 # localhost, connection: local
│   │   └── host_vars/
│   │       └── example-machine.yml   # fully commented example
│   ├── group_vars/all/
│   │   ├── main.yml                  # users, layout, service config, ports
│   │   ├── versions.yml              # generated from versions.lock.yml — do not edit
│   │   └── vault.yml.example         # documented shape; real vault.yml is encrypted
│   ├── callback_plugins/pokus_events.py   # JSON event stream for the TUI (§18.5)
│   ├── filter_plugins/ (if needed)
│   └── roles/
│       ├── platform_facts/           # family/distro facts; the only place that inspects the distro (§19.1)
│       ├── pkg/                      # package-manager abstraction if module respawn is unreliable (§19.3)
│       ├── preflight_gate/
│       ├── provider_wsl2/
│       ├── provider_baremetal/
│       ├── provider_vm/
│       ├── base/
│       ├── mac/                      # SELinux / AppArmor handling (§19.7)
│       ├── inference_control/        # only for node_role: inference (§19.10)
│       ├── users_groups/
│       ├── ssh/
│       ├── tailscale/
│       ├── firewall/
│       ├── docker/
│       ├── nvidia_container_toolkit/
│       ├── incus/
│       ├── layout/
│       ├── postgres/
│       ├── observability/
│       ├── opa/
│       ├── vllm/
│       ├── models/
│       ├── images/
│       ├── catalog/
│       ├── platform/
│       ├── backup/
│       ├── lab_session/
│       └── doctor/
├── compose/
│   ├── core/compose.yml              # postgres, opa
│   ├── observability/compose.yml     # otel-collector, prometheus, loki, tempo, grafana, exporters
│   └── inference/compose.yml         # vllm
├── config/
│   ├── otel/gateway.yaml.j2
│   ├── prometheus/prometheus.yml.j2
│   ├── loki/loki.yaml.j2
│   ├── tempo/tempo.yaml.j2
│   └── grafana/
│       ├── provisioning/{datasources,dashboards}/
│       └── dashboards/*.json         # host, GPU/vLLM, Incus targets, run explorer (run_id variable)
├── incus/
│   ├── preseed.yaml.j2
│   ├── profiles/*.yaml.j2
│   └── projects/*.yaml.j2
├── images/                           # golden image recipes (see §7.7)
│   ├── _base/
│   └── <blueprint>/{image.yaml,cloud-init.yaml,provision.yml,files/,tests/}
├── vllm/profiles/*.yml               # serving profiles per GPU class
├── tools/
│   └── pokus-lab/                    # operator CLI (Python, stdlib-only or uv-managed; see §9)
├── users/                            # <username>.pub (one or more keys per file)
├── contrib/windows/                  # optional WSL helpers, never executed by bootstrap
└── docs/
    ├── BUILD_PROMPT.md               # this file
    ├── IMPLEMENTATION_NOTES.md
    ├── architecture.md
    ├── support-matrix.md             # generated
    ├── operations.md
    ├── security.md
    ├── reproducibility.md
    ├── contracts.md                  # integration contract with platform/catalog (§12)
    ├── providers/{wsl2,baremetal,vm}.md
    ├── distros/{debian,redhat,suse,archlinux,adding-a-family}.md
    ├── node-roles.md
    └── adr/0001-*.md ...
```

---

## 5. Bootstrap entry point (`bootstrap/bootstrap.sh`)

POSIX-compatible bash, `set -Eeuo pipefail`, ShellCheck-clean, `trap` for errors with the failing line, all output tee'd to `/var/log/pokus-bootstrap/<UTC-timestamp>.log` (0600 root).

### 5.1 Usage

```
sudo ./bootstrap/bootstrap.sh --machine <name> [options]

Options:
  --machine NAME           Required. Selects ansible/inventory/host_vars/NAME.yml
  --ref REF                Git ref of pokus-infra being applied (recorded; default: current HEAD)
  --restore                After provisioning, run restore.yml (prompts for snapshot)
  --tags T1,T2             Pass through to ansible-playbook
  --skip-tags T1,T2        e.g. images,models for a fast first run
  --check                  Ansible check mode (dry run); preflight still runs
  --accept-degraded        Continue when optional features are missing
  --accept-experimental    Continue on an experimental-tier OS
  --force-unsupported      Continue on an unsupported host (recorded in every fingerprint)
  --refresh-profile        Accept a changed host profile
  --allow-reboot           Allow automatic reboot on bare metal / VM
  --adopt-incus            Adopt an existing Incus installation (see §2.2)
  --allow-community-repos  Permit non-vendor, non-distro repositories (COPR, OBS, RPM Fusion) (§19.4)
  --offline                Skip network reachability checks (for testing only)
  --non-interactive        Never prompt; fail where a prompt would be needed
  --vault-password-file F  Use a file instead of prompting
  --answers FILE           Non-secret answers file (as exported by the installer TUI, §18.6)
  --tui | --no-tui         Force or disable the TUI (default: TUI when stdin and stdout are TTYs)
  -h, --help
```

### 5.2 Steps

1. Require root (re-exec hint for sudo), require running from a git checkout of this repo (check `.git` and record `git rev-parse HEAD`, dirty-tree flag; refuse a dirty tree unless `--allow-dirty` — record it).
2. Run `bootstrap/lib/prepreflight.sh` (POSIX sh, no bashisms): root, systemd as PID 1, glibc, supported architecture, `curl` or `wget` present, writable `/opt`. If `curl`/`wget` is missing, print the exact install command for the detected package manager and exit 30. Then install the pinned `uv` (checksum-verified) and the pinned CPython via `uv python install` into `/opt/pokus/python` (§19.3). Everything after this step uses that interpreter, never the distro's Python.
3. Run preflight. Handle exit codes per §2.4. Interactive confirmation lists disabled features and their research impact.
4. Install `uv` (pinned version, checksum-verified from `versions.lock.yml`) to `/opt/pokus/bin/uv`.
5. Create `/opt/pokus/ansible-venv` with the pinned Python version via uv and install the pinned `ansible-core` plus Python deps (`jmespath`, etc.) from a lockfile (`bootstrap/requirements.lock`, generated with `uv pip compile --generate-hashes`).
6. Install pinned Ansible collections from `ansible/requirements.yml` into `/opt/pokus/ansible-collections`.
7. Generate `ansible/group_vars/all/versions.yml` from `versions.lock.yml` (or verify it is in sync; fail if drifted).
8. Run `ansible-playbook -i ansible/inventory/hosts.yml ansible/site.yml -e machine=<name> -e @/etc/pokus/host-profile.json` (pass the profile as structured vars under `host_profile`), with vault password handling.
9. Map Ansible-signaled conditions to bootstrap exit codes:
   - `75` — WSL restart required (`wsl --terminate <distro>` on Windows side, then re-run)
   - `76` — reboot required (bare metal/VM), or MOK enrollment pending
   - `77` — operator action required (message printed)
   Use a well-defined mechanism: the role writes `/run/pokus/bootstrap-signal.json` with `{code, message}` and then fails with a recognizable message; bootstrap reads the file.
10. If `--restore`, run `restore.yml`.
11. Run `pokus-lab doctor` and print the summary. Exit with doctor's exit code.
12. Write `/etc/pokus/bootstrap-state.json` with repo commit, ref, timestamp, tags used, overrides, and result.

Re-running bootstrap after any exit (including 75/76/77) must resume cleanly.

### 5.3 Interactive front-end

When bootstrap runs on a TTY without `--no-tui`/`--non-interactive`, it performs only the minimal sanity checks of `prepreflight.sh` (step 2 of §5.2), installs the pinned `uv`, the pinned CPython and the hash-locked installer environment under `/opt/pokus/`, and hands control to the installer TUI (§18). The TUI runs the full preflight, collects every input, writes the configuration files, and then drives the steps of §5.2 by invoking exactly the same commands. If preflight ends in an abort, the TUI offers to remove `/opt/pokus/` again so the host is left untouched.

There must be **exactly one provisioning code path**: the TUI contains no provisioning logic, and a non-interactive run with the exported answers file must produce an identical result.

---

## 6. Accounts, groups, layout

### 6.1 Groups and accounts (from `group_vars/all/main.yml`)

```yaml
pokus_groups:
  - {name: pokus,       system: false}   # all researchers
  - {name: pokus-admin, system: false}   # sudo-capable researchers
  - {name: pokus-gt,    system: true}    # may read ground truth / catalog
  - {name: pokus-svc,   system: true}    # all service accounts
  - {name: pokus-exec-clients, system: true}  # may call the executor socket (agent, orch)
  - {name: pokus-gw-clients,   system: true}  # may call the gateway's agent socket (agent only)

researchers:                              # names are placeholders; ask the operator
  - {name: researcher1, admin: true,  shell: /bin/bash, keys_file: users/researcher1.pub}
  - {name: researcher2, admin: false, shell: /bin/bash, keys_file: users/researcher2.pub}
  - {name: researcher3, admin: false, shell: /bin/bash, keys_file: users/researcher3.pub}

service_accounts:
  - {name: pokus-orch,   groups: [pokus-svc, pokus-gt, pokus-exec-clients]}
  - {name: pokus-agent,  groups: [pokus-svc, pokus-exec-clients, pokus-gw-clients]}  # NO pokus-gt, NO incus, NO docker, NO DB
  - {name: pokus-gw,     groups: [pokus-svc]}               # LLM gateway
  - {name: pokus-exec,   groups: [pokus-svc]}
  - {name: pokus-inject, groups: [pokus-svc, pokus-gt]}
  - {name: pokus-verify, groups: [pokus-svc, pokus-gt]}
  - {name: pokus-eval,   groups: [pokus-svc, pokus-gt]}
```

Rules:

- Researchers: member of `pokus` and `incus` (Incus's restricted per-user project group). Admins additionally `pokus-admin` with sudo via `/etc/sudoers.d/90-pokus-admin` (validated with `visudo -cf`). **No researcher is ever added to `docker` or `incus-admin`** by default; an explicit `break_glass_docker: [user]` list may exist for admins and is logged loudly.
- Service accounts: system users, `/usr/sbin/nologin`, home under `/var/lib/pokus/<name>`, no SSH keys, no password.
- `authorized_keys` managed exclusively from `users/*.pub` (`exclusive: true`). Removing a key from the repo removes it from the host.
- Removed researchers (listed under `researchers_removed`) are locked (`usermod -L`, shell `nologin`), not deleted; their data is preserved.

### 6.2 `/srv/pokus` layout (`layout` role)

Base path configurable (`pokus_root: /srv/pokus`). Implement with explicit owner/group/mode and default ACLs where noted. The layout spec must live in one data structure in `group_vars` and be rendered both into tasks and into `docs/architecture.md` (generated table) so docs never drift.

| Path | Owner:Group | Mode | Notes |
|---|---|---|---|
| `/srv/pokus` | root:pokus | 2775 | |
| `deploy/` | root:root | 0755 | read-only deployments of platform/catalog tags |
| `catalog/` | root:pokus-gt | 0750 | scenario catalog incl. ground truth |
| `models/` | pokus-orch:pokus | 0755 | weights 0444 after verification |
| `datasets/` | root:pokus | 2775 | default ACL g:pokus:rwX |
| `artifacts/` | pokus-orch:pokus | 0750 | content-addressed, write-once 0440 |
| `reports/` | pokus-eval:pokus | 2775 | |
| `images/` | root:pokus-admin | 0750 | exported golden image tarballs + sha256 |
| `shared/` | root:pokus | 2770 | default ACL g:pokus:rwX |
| `users/<name>/` | <name>:<name> | 0700 | per researcher |
| `services/` | root:pokus-admin | 0750 | rendered compose/config, never secrets in plaintext world-readable |
| `state/<service>/` | per container UID | 0700 | postgres, prometheus, loki, tempo, grafana data |
| `backups/` | root:root | 0700 | local staging only |

Also `/etc/pokus/` (0755 root) for host profile, bootstrap state, rendered non-secret config; `/etc/pokus/secrets/` (0750 root:pokus-svc, files 0640 or per-service 0600) for rendered secrets; `/run/pokus/` (runtime sockets via systemd `RuntimeDirectory=`).

---

## 7. Roles (responsibilities and acceptance criteria)

Every role must have: `README.md` (purpose, variables, feature flags consumed, idempotency notes), `defaults/main.yml` with every variable documented, `meta/main.yml`, and tags equal to the role name. Use `ansible.builtin` modules wherever possible; `command`/`shell` only with `creates`/`changed_when`/`failed_when` that make them idempotent.

### 7.1 `preflight_gate`
Re-run preflight, compare with `/etc/pokus/host-profile.json` (§2.6), expose `host_profile` and `features` facts. Fail fast with remediation.

### 7.2 `base`
- Packages are requested by **logical name** (`ca_certificates`, `curl`, `gpg`, `jq`, `git`, `acl`, `btrfs_tools`, `lvm2`, `thin_provisioning_tools`, `nftables`, `time_sync`, `rsync`, `tmux`, `htop`, `iotop`, `sysstat`, `sudo`, …) and mapped to real package names per family in `ansible/roles/base/vars/<family>.yml` (§19.4). Time sync: keep whatever the distro uses (`systemd-timesyncd` or `chronyd`) and only ensure it is enabled and synchronized.
- System timezone **UTC** (researchers may set their own `TZ`).
- journald persistent storage with size limits.
- Automatic updates per family (§19.5): security-only, **never** an automatic reboot, and held packages excluded (Docker, containerd, NVIDIA, Incus, Tailscale, and the kernel on bare metal unless `allow_kernel_updates: true`).
- Version holds per family (§19.5) using the versions from `versions.lock.yml`.
- Third-party repository keys are always imported explicitly after verifying the key fingerprint against `versions.lock.yml` (§19.4). Never `apt-key`, never `zypper --gpg-auto-import-keys`, never `gpgcheck=0`.
- sysctl drop-in (`/etc/sysctl.d/60-pokus.conf`): inotify limits, `net.ipv4.ip_forward=1` (needed by Incus/Docker; firewall handles the rest), documented.

### 7.3 `ssh`
- Drop-in `/etc/ssh/sshd_config.d/10-pokus.conf`: `PasswordAuthentication no`, `KbdInteractiveAuthentication no`, `PermitRootLogin no`, `AllowGroups pokus`, `PubkeyAuthentication yes`, `X11Forwarding no`, `AllowTcpForwarding local` (researchers need local forwards for Grafana), `ClientAliveInterval 60`.
- Ensure `sshd_config` contains `Include /etc/ssh/sshd_config.d/*.conf` as its first directive (add a managed line if a distro ships without it). The service is `ssh` on Debian-family and `sshd` elsewhere (from family vars).
- Validate with `sshd -t` before reload.
- Exposure is restricted by the firewall role (only `tailscale0` plus optional `ssh_extra_allowed_interfaces`).

### 7.4 `tailscale`
- Source per family from the component source matrix (§19.4): Tailscale's official deb/rpm repositories, or the distro package where Tailscale recommends it (e.g. Arch). Pinned and held.
- Join with `tailscale up --hostname=<machine_name> --advertise-tags=<tag> --accept-dns=false --ssh=false` using an auth key from vault if present; otherwise print the interactive login URL and wait (or fail in `--non-interactive`). Skip if already logged in with the same hostname and tags.
- `--accept-dns=false` by default (do not let MagicDNS alter host resolution); configurable.
- Assert Funnel is **off** (`tailscale funnel status`) and fail the play if any funnel config exists. `tailscale serve` for Grafana is optional (`grafana_tailscale_serve: true`), tailnet-only.
- Document required tailnet ACL snippet in `docs/security.md` (only the researchers' identities may reach `tag:pokus-lab:22` and the Grafana serve port).

### 7.5 `firewall`
- Firewall **backend** is selected per host (§19.6): `nftables` (own table, loaded by a dedicated `pokus-nftables.service` from `/etc/pokus/nftables/pokus.nft`, so no distro nftables config file is edited), `firewalld` (zones), or `ufw` (rules). All three must produce the same effective policy below, verified by doctor. Never flush tables owned by Docker, Incus, firewalld or ufw.
- Input policy: drop by default; allow established/related, loopback, ICMP essentials, `tailscale0` → tcp/22 (+ Grafana serve port if enabled), DHCP/DNS on Incus bridges from instances to the host **only** where needed for Incus's dnsmasq.
- Docker coexistence: Docker sets FORWARD to DROP and loads `br_netfilter`, which breaks Incus bridge traffic. Add a systemd-managed hook (`pokus-docker-incus-compat.service`, `After=docker.service incus.service`) that inserts `DOCKER-USER` ACCEPT rules for Incus bridge interfaces (pattern from `incus_bridge_prefix`). Verify the current recommended approach in Incus docs and implement exactly that.
- Doctor check: list all listening sockets and fail if anything listens on `0.0.0.0`/`::` other than sshd (which is firewalled) — every lab service binds to `127.0.0.1`, a Unix socket, or an internal Docker network.

### 7.6 `docker` and `nvidia_container_toolkit`
- Docker Engine from the source the component matrix selects (§19.4): Docker's official repositories where Docker publishes them for the distro (Ubuntu, Debian, Fedora, RHEL-compatible), otherwise the distribution's own package (openSUSE, Arch). Required: Engine with Compose v2 plugin and buildx; versions pinned and held where the source allows; the actually installed versions always recorded in the fingerprint.
- `/etc/docker/daemon.json` (managed header): `log-driver: local` with size limits, `live-restore: true`, `default-address-pools` that **do not overlap** with Incus bridges, Tailscale CGNAT `100.64.0.0/10`, or common home LANs (`192.168.0.0/16`) — use e.g. `172.30.0.0/16` split into /24s; configurable. `data-root` configurable.
- NVIDIA Container Toolkit (only if `features.gpu_inference`): from NVIDIA's deb/rpm/zypper repositories or the distro package (Arch), pinned. Prefer **CDI** for GPU access (`nvidia-ctk cdi generate`, using `--mode=wsl` on WSL2; Docker with CDI enabled; services request `nvidia.com/gpu=all`) because it works the same on every distro; fall back to `nvidia-ctk runtime configure --runtime=docker` only where CDI is not usable, and record which method is active. Restart Docker only if its config changed.
- Verification task: run a pinned CUDA base image **by digest** with `--gpus all` and `nvidia-smi`; record output into `/var/lib/pokus/verify/gpu-docker.txt`.
- Lab services run as Compose projects managed by systemd units: `pokus-core.service`, `pokus-observability.service`, `pokus-inference.service` (`Type=oneshot` + `RemainAfterExit=yes` calling `docker compose up -d --wait` / `down`, or a better pattern if you find one — document it). All images referenced **by digest**.

### 7.7 `incus`
- Install Incus LTS (6.0.x) from the source the component matrix selects per distro (§19.4): distro package, Zabbly repository (Debian/Ubuntu), EPEL/COPR (RHEL-compatible), OBS (openSUSE Leap), or the Arch package. Verify availability per distro at build time; where only a non-LTS version exists (rolling distros), record it and mark the host `incus_channel: non_lts` in the profile. Group names (`incus`, `incus-admin`) and the `incus-user` daemon availability must be verified per distro and mapped in family vars.
- Initialize from `incus/preseed.yaml.j2`:
  - storage pool `pokus-targets`: `btrfs` (loop with `incus_pool_size`, or `device`) or `lvm` thin (fallback);
  - **no** default `incusbr0` with NAT for targets; create:
    - `pokus-build0`: NAT enabled, used only by the `build` project for image builds;
    - per-run networks are created by the platform at runtime; infra creates the network **template conventions** (prefix `r-`, no NAT, no upstream) and Incus network ACL `pokus-run-default` (deny all egress except intra-network);
  - projects:
    - `build` — image builds, internet allowed;
    - `bench` — official benchmark runs; `restricted=true` with explicit allowances (nesting allowed, VMs allowed only if `features.vm_targets`, limits enforced);
    - personal per-user projects via Incus's `incus` group / `incus-user` daemon (verify mechanism and limits; set conservative per-user limits via `restricted.*` and `limits.*`).
  - profiles: `target-container` (limits template, `security.nesting` per blueprint, root disk size, guest API disabled), `target-vm` (only if `vm_targets`), `build`, `probe-client`.
- Trust and certificates: generate a client certificate per service account (`pokus-orch`, `pokus-exec`, `pokus-inject`, `pokus-verify`), stored in `/etc/pokus/incus/<svc>/` (0600 owned by the service account), added to Incus trust as **restricted** to project `bench` (orch also `build`). Prometheus gets a metrics-only certificate (`type: metrics`).
- Enable the Incus HTTPS listener on `127.0.0.1` only (for restricted TLS clients and metrics).
- Incus lifecycle/exec events: ship a small unit (`pokus-incus-events.service`) that runs `incus monitor --type=lifecycle --type=logging --format=json` (verify flags) and writes JSON lines to journald with a stable identifier, picked up by the collector's journald receiver. This feeds the platform's integrity check ("only the executor touched targets during the agent phase").
- Marker file `/etc/pokus/managed-incus`.

### 7.8 `postgres`
- Container, pinned digest, data in `/srv/pokus/state/postgres`, `initdb` with `--data-checksums`.
- Listen **only** on a Unix socket directory bind-mounted at `/run/pokus/postgres` (group `pokus-svc`, plus a separate read-only socket path or `pg_hba` rule for the `pokus` group's reader role). Grafana connects over the internal Docker network with the reader role only.
- Bootstrap SQL (idempotent): database `pokus`; roles `pokus_migrator` (owner, used only by platform migrations), `pokus_orch`, `pokus_gw`, `pokus_exec`, `pokus_inject`, `pokus_verify`, `pokus_eval`, `pokus_reader`, `pokus_grafana`; schemas `public` and `gt`; `gt` usage revoked from `PUBLIC`, `pokus_reader` and `pokus_grafana`. Table-level grants are the platform's responsibility (migrations); infra documents the expectation in `docs/contracts.md`.
- Settings: `fsync=on`, `synchronous_commit=on`, `full_page_writes=on` (assert none are disabled), sensible memory for the host (from host_vars), `log_line_prefix` with timestamps.
- `postgres_exporter` for Prometheus.

### 7.9 `observability`
- **OpenTelemetry Collector (contrib)** gateway, pinned digest, listening on `127.0.0.1:4317/4318` (targets reach it via Incus proxy devices, §12). Pipelines:
  - logs: `otlp`, `journald` (host) → `memory_limiter` → `resource` → `batch` → Loki (OTLP endpoint, verify Loki version supports native OTLP ingestion) ;
  - metrics: `otlp` → Prometheus (remote-write or Prometheus exporter scraped — choose and document);
  - traces: `otlp` → Tempo.
- **Prometheus**: scrape node_exporter, cAdvisor, Incus metrics (TLS metrics cert), Postgres exporter, OTel collector self-metrics, vLLM `/metrics` (if inference), GPU exporter (DCGM or nvidia-smi exporter per feature flag). Retention configurable (default 90d). Bind 127.0.0.1.
- **Loki** and **Tempo**: single-binary, filesystem storage under `/srv/pokus/state`, retention configured, bind 127.0.0.1.
- **Grafana**: provisioned datasources (Prometheus, Loki, Tempo, Postgres reader) and dashboards from `config/grafana/dashboards/`: host overview, GPU & vLLM, Incus targets, lab services health, and a **run explorer** dashboard with a `run_id` variable linking Postgres rows (if platform tables exist), Loki logs filtered by `pokus.run_id`, Prometheus panels, and Tempo traces by `trace_id`. Admin password from vault; researcher Grafana accounts provisioned from `researchers`; anonymous access off; bind 127.0.0.1; optional `tailscale serve`.
- Exporters: node_exporter and cAdvisor bound to 127.0.0.1 or internal network only.

### 7.10 `opa`
- OPA container, pinned digest, bound to a Unix socket or 127.0.0.1; loads the policy bundle from the deployed platform path (`/srv/pokus/deploy/platform/current/policy/`). If platform is disabled, ship a deny-all placeholder bundle and mark OPA healthy-but-placeholder in doctor. Decision logs → OTel collector.

### 7.11 `vllm` (only if `features.gpu_inference`)
- Compose project `pokus-inference`, pinned `vllm/vllm-openai` digest.
- Serving profiles in `vllm/profiles/<gpu-class>.yml` (e.g. `ada-12g.yml`) define defaults: `--gpu-memory-utilization`, `--max-model-len`, `--max-num-seqs`, dtype/quantization constraints, tool-call parser options, `--enable-lora` limits, `--otlp-traces-endpoint`, `--seed`. Host vars select `vllm_profile`.
- **Deployments.** A deployment spec lives in `/etc/pokus/vllm/deployments/<id>.yml`: `{model_id (from models.lock.yml), profile, overrides (max_model_len, quantization, max_num_seqs, enable_lora, lora_modules...), sampling_defaults}`. The active one is the symlink `current.yml`, rendered to `/etc/pokus/vllm/current.env` and consumed by the compose project.
- **Bootstrap must end with vLLM actually serving a model** when `features.gpu_inference` is on and a default model was selected (the TUI asks; non-interactive runs use `vllm_default_deployment`). If no model is selected, the unit is installed but disabled, and doctor reports `inference: no deployment` as a warning, not a failure.
- **Serving verification.** After start, wait for `/health`, list `/v1/models`, send one deterministic chat completion and one tool-call request (a dummy `get_time` tool) and assert that a well-formed tool call comes back. Save results to `/var/lib/pokus/verify/vllm-<deployment>.json`. If the tool-call check fails, point to the tool-call parser / chat template configuration as the likely cause.
- The platform's orchestrator later swaps deployments through the helper in §12; document this contract.
- Models mounted read-only from `/srv/pokus/models`; `HF_HUB_OFFLINE=1`; bound to an internal Docker network plus `127.0.0.1:<port>` for the LLM gateway; never public.
- Health check and `/metrics` scraped by Prometheus.

### 7.12 `models`
- Input: `models.lock.yml` entries `{id, hf_repo, revision (full commit hash), files: [{path, sha256}], license, gated: bool, quantization, notes}`.
- Download with a pinned `huggingface_hub` CLI in an isolated uv environment into `/srv/pokus/models/<hf_repo>/<revision>/`, verify every sha256, set 0444, write `MANIFEST.json` (id, revision, files, hashes, download time, tool version). HF token for gated models from vault.
- Tag: `models`. Skippable. Never re-download verified files.
- **Curated starter catalog `models.catalog.yml`.** 3–5 candidate open-weight instruct models with reliable tool calling in vLLM that fit each serving profile (for `ada-12g`: weights + KV cache within 12 GB, using AWQ/GPTQ 4-bit or FP8). Per entry: HF repo, pinned revision, quantization, license, gated flag, expected VRAM, recommended `max_model_len`, tool-call parser, chat-template notes. Research current options at build time and **do not finalize the list without my approval**.
- The TUI offers the catalog entries that fit the detected profile, plus "custom (HF repo + revision)" and "none". A selection is added to `models.lock.yml` with per-file sha256 resolved from Hugging Face file metadata (LFS sha256) *before* download, then verified after download.

### 7.13 `images` (golden image pipeline)
- Each blueprint directory: `image.yaml` (base image: remote, alias, **pinned fingerprint**; instance type container/vm; packages with pinned versions where it matters; preloaded Docker images **by digest** for Docker-in-target blueprints; services to enable; telemetry agent config), `cloud-init.yaml`, `provision.yml` (Ansible run inside the build instance), `files/`, `tests/` (post-build assertions).
- Build flow (in project `build`, network `pokus-build0`): launch from pinned base → provision → run tests → **leakage lint** → clean (apt caches, shell histories, logs, cloud-init artifacts, machine-id, SSH host keys regenerated on first boot) → stop → `incus publish` with alias `<blueprint>/<version>` and properties (`pokus.recipe_commit`, `pokus.built_at`, `pokus.base_fingerprint`) → `incus image export` to `/srv/pokus/images/<blueprint>/<version>/` with `sha256sum` file.
- Base images from public image servers rotate and old builds disappear: export and keep the **base** image tarball too, so rebuilds stay possible.
- Leakage lint (must fail the build): no files or strings matching scenario ID patterns (`[A-Z]+-[0-9]{3}`), no `fault`, `inject`, `ground_truth`, `scenario` strings in `/etc`, `/opt`, `/srv`, `/usr/local`, unit names, hostnames, or environment files; no pokus repo checkouts inside the image. Make the pattern list configurable.
- Provide at least these blueprint recipes (minimal but real): `_base` (Ubuntu 24.04 + OTel agent + common tooling), `web-nginx-flask` (nginx + gunicorn + Flask app), `pg-primary` (PostgreSQL), `redis-cache` (Redis), `docker-host` (Docker Engine inside target, nesting), `probe-client` (curl, dig, load generator). Each with tests.
- In-target OTel agent config sends to `127.0.0.1:4317` **inside** the instance (reached via proxy device, §12) and must not contain run or scenario identifiers (these are attached host-side).
- Tag: `images`. Skippable.

### 7.14 `catalog` and `platform` (only if enabled)
- `catalog`: clone `pokus-catalog` at `catalog_ref` into `/srv/pokus/catalog/<ref>/` (root:pokus-gt 0750), verify signed tag if `require_signed_tags: true`, update `current` symlink atomically.
- Install the pinned Quarto CLI (from `versions.lock.yml`) for the evaluator's HTML/PDF reports (PDF via the bundled Typst engine, no TeX).
- `platform`: clone `pokus-platform` at `platform_ref` into `/srv/pokus/deploy/platform/<ref>/`, `uv sync --frozen`, atomic `current` symlink, render systemd units from templates in this repo with the hardening below, run DB migrations as `pokus_migrator` (command provided by platform per contract), restart changed units only.
- Systemd hardening baseline for every platform unit (adjust per service; document deviations):

```
User=<svc>  Group=<svc>  SupplementaryGroups=<as needed>
NoNewPrivileges=yes  ProtectSystem=strict  ProtectHome=yes  PrivateTmp=yes
PrivateDevices=yes  ProtectKernelTunables=yes  ProtectKernelModules=yes
ProtectControlGroups=yes  RestrictSUIDSGID=yes  LockPersonality=yes
RestrictRealtime=yes  MemoryDenyWriteExecute=yes (unless incompatible)
CapabilityBoundingSet=  AmbientCapabilities=
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
RuntimeDirectory=pokus/<svc>  StateDirectory=pokus/<svc>
ReadWritePaths=<explicit list>  ReadOnlyPaths=<explicit list>
InaccessiblePaths=<explicit list>
```

  `pokus-agent` must have `InaccessiblePaths=/srv/pokus/catalog /srv/pokus/artifacts /srv/pokus/services /etc/pokus/secrets /etc/pokus/incus /var/lib/incus /run/docker.sock` and only the gateway and executor sockets reachable. Add a doctor check that verifies these effective properties via `systemctl show`.

### 7.15 `backup`
- restic (pinned binary, checksum-verified). Repository URL and password from vault (destination typically another machine over Tailscale via SFTP or rest-server; ask the operator).
- Timers with `Persistent=true` (the host is not always on):
  - `pokus-pgdump.timer` — `pg_dump -Fc` to `/srv/pokus/backups/pg/` (via container), keep N local;
  - `pokus-backup.timer` — restic backup of `/srv/pokus` (include `catalog`, `artifacts`, `reports`, `images`, `datasets`, `shared`, `users`, `backups/pg`; exclude `models/*/` weights by default, `state/prometheus`, `state/loki`, `state/tempo` configurable) and `/etc/pokus` (excluding secrets unless `backup_include_secrets: true` with restic encryption acknowledged);
  - `pokus-backup-prune.timer` — retention policy from vars.
- `restore.yml`: stops services, restores a chosen snapshot, restores Postgres from the dump, re-imports golden images from exported tarballs (`incus image import`) preserving aliases and verifying fingerprints match the recorded ones, re-verifies model hashes (re-downloading if missing), restarts services, runs doctor.
- Backup status metrics exposed to Prometheus (textfile collector).

### 7.16 `lab_session`
- `pokus-lab.target` groups all lab units. Starting the target is the "lab is up" state.
- `pokus-session-start.service` (part of the target, runs once per boot): records a **session record** to `/var/lib/pokus/sessions/<boot_id>.json`: boot ID (`/proc/sys/kernel/random/boot_id`), start time, environment fingerprint (§10.2), preflight outcome. The platform imports these into its DB (contract).
- Startup gating: services that run experiments (platform) must not start until `pokus-health-gate.service` passes: time synchronized (NTP synced and, on WSL, wall clock within a threshold of an NTP reference if reachable), Postgres healthy, collector healthy, Incus healthy, GPU healthy if `gpu_inference`.
- Sleep inhibition per provider/power policy (§3.2).
- Ordered shutdown helper `pokus-lab stop` (§9).

### 7.17 `doctor`
- Installs `/usr/local/bin/pokus-lab` (see §9) and doctor check definitions.

---

## 8. Configuration model

### 8.1 `host_vars/<machine>.yml` (example to ship, fully commented)

```yaml
machine_name: pokus-lab
lab_mode: production               # production | staging (QA environments; see pokus-qa instructions §4)
lab_provider: auto                 # auto | baremetal | wsl2 | vm (auto = from host profile; explicit must match)
power_policy: lab_session_inhibit  # never_sleep | lab_session_inhibit | os_default (baremetal/vm only)

expected:                          # doctor warns if effective values differ (important on WSL)
  memory_gib: 22
  cpu_threads: 20

cpuset_services: "0-5"             # lab services pinned here
cpuset_targets: "6-19"             # Incus targets pinned here

incus_pool_source: loop            # loop | device
incus_pool_size: 300GiB            # for loop
incus_pool_device: ""              # /dev/disk/by-id/... for device
incus_pool_device_confirm: ""      # must equal incus_pool_device exactly, or nothing is formatted

pokus_root: /srv/pokus
docker_data_root: /var/lib/docker

node_role: full                    # full | lab | inference (§19.10)
firewall_backend: auto             # auto | nftables | firewalld | ufw (§19.6)
install_nvidia_driver: auto        # auto | manual | skip (baremetal/vm; ignored on wsl2) (§19.8)
allow_community_repos: false       # COPR / OBS / RPM Fusion need explicit consent (§19.4)
vllm_profile: ada-12g
vllm_default_deployment:           # null = install vLLM but serve nothing
  model_id: "<id from models.lock.yml>"
  start_after_install: true
postgres_shared_buffers: 2GB

grafana_tailscale_serve: false
ssh_extra_allowed_interfaces: []   # e.g. [enp5s0] to allow LAN SSH on bare metal
```

### 8.2 Secrets (`group_vars/all/vault.yml`, Ansible Vault encrypted)

Keys (document in `vault.yml.example`): `vault_postgres_passwords.<role>`, `vault_grafana_admin_password`, `vault_grafana_user_passwords.<user>`, `vault_restic_repository`, `vault_restic_password`, `vault_tailscale_authkey` (optional), `vault_hf_token` (optional), `vault_git_deploy_key_platform`/`_catalog` (optional).

Rules:
- Every task handling secrets uses `no_log: true`.
- Rendered secret files: 0600/0640 with explicit owners; never under world-readable paths; never in container image layers.
- **Per-machine identities are generated on the host, never stored in the repo or vault**: SSH host keys, Tailscale node identity, Incus server and client certificates, the run-manifest signing key (`/etc/pokus/signing/` ed25519, generated once; its public key is published to `/srv/pokus/shared/signing-keys/<machine>-<date>.pub` and kept forever so old runs remain verifiable).

---

## 9. Operator CLI: `pokus-lab`

Python (stdlib-only preferred so it works even when uv/venv is broken), installed to `/usr/local/bin/pokus-lab`. Subcommands:

| Command | Purpose |
|---|---|
| `pokus-lab preflight [--json]` | Run preflight (read-only) |
| `pokus-lab doctor [--json] [--smoke]` | Full health and security check (below) |
| `pokus-lab status` | Units, containers, Incus projects/instances summary, GPU, disk, last backup |
| `pokus-lab start` / `stop` | Start/stop `pokus-lab.target`. `stop` first asks the platform orchestrator to drain (if present, via its socket), waits up to `--timeout`, then stops units in reverse dependency order |
| `pokus-lab fingerprint [--json]` | Print current environment fingerprint and its hash |
| `pokus-lab backup now` / `backup list` | Trigger backup / list snapshots |
| `pokus-lab restore` | Wrapper around `restore.yml` |
| `pokus-lab images list|build <blueprint>|export` | Golden image helpers |
| `pokus-lab user add|remove|keys` | Thin wrappers that edit repo files and remind to commit + re-run bootstrap (never mutate the host directly) |
| `pokus-lab wsl-helpers` | Print paths and instructions for `contrib/windows/` (wsl2 provider only) |

### Doctor checks (each: id, description, severity, result, remediation)

- Preflight re-run outcome and drift vs host profile.
- Units in `pokus-lab.target` active; containers healthy; images running match pinned digests.
- No unexpected listening sockets (only sshd on non-loopback; sshd only reachable via allowed interfaces per nftables).
- nftables `inet pokus_filter` present; DOCKER-USER compat rules present.
- Tailscale: logged in, hostname and tags as expected, **Funnel off**.
- Docker GPU test (if `gpu_inference`), vLLM health (if a deployment is active).
- Incus: pool backend and free space, projects, profiles, restricted certs present with correct ownership, metrics endpoint scrapeable, events unit active.
- `/srv/pokus` tree ownership/modes/ACLs match the layout spec exactly.
- Researchers not in `docker`/`incus-admin` (except break-glass list); service accounts not in `docker`.
- systemd hardening properties of `pokus-agent` and other platform units (if installed).
- Postgres roles exist; `gt` schema not readable by reader/grafana roles; fsync/synchronous_commit on.
- Observability round-trip: emit a test log record via OTLP with a unique ID and confirm it is queryable in Loki within N seconds; same for a test span in Tempo and a test metric in Prometheus.
- Time sync state and clock offset.
- Backup: last successful backup age within threshold; restic repository reachable.
- WSL provider: `wsl.conf` values; effective memory/CPU vs `expected`; `/dev/kvm` presence; warn if interop/automount enabled.
- `--smoke`: launch a throwaway instance from each golden image in project `bench` with a fresh no-NAT network and proxy device; assert systemd running, blueprint services healthy, telemetry arrives at the gateway tagged with the throwaway run ID, no egress to the internet, no route to lab services; destroy everything; leave no trace.

Doctor exit codes: 0 all pass, 1 warnings only, 2 failures.

---

## 10. Reproducibility

### 10.1 `versions.lock.yml`

Single source of truth for every external artifact. Example shape:

```yaml
schema_version: 1
verified_on: 2026-10-05
apt:
  docker-ce:            {version: "<verify>", repo: docker, source: "<url>"}
  containerd.io:        {version: "<verify>", repo: docker}
  nvidia-container-toolkit: {version: "<verify>", repo: nvidia}
  incus:                {version: "<verify>", repo: "zabbly-lts-6.0|distro", source: "<url>"}
  tailscale:            {version: "<verify>", repo: tailscale}
apt_keys:
  docker:   {url: "<verify>", fingerprint: "<verify>"}
  nvidia:   {url: "<verify>", fingerprint: "<verify>"}
  zabbly:   {url: "<verify>", fingerprint: "<verify>"}
  tailscale:{url: "<verify>", fingerprint: "<verify>"}
containers:
  postgres:        {image: "docker.io/library/postgres", tag: "<verify>", digest: "sha256:<verify>"}
  otel_collector:  {image: "...opentelemetry-collector-contrib", tag: "<verify>", digest: "sha256:<verify>"}
  prometheus:      {...}
  loki:            {...}
  tempo:           {...}
  grafana:         {...}
  node_exporter:   {...}
  cadvisor:        {...}
  postgres_exporter: {...}
  gpu_exporter:    {...}     # nvidia-smi based
  dcgm_exporter:   {...}
  opa:             {...}
  vllm:            {...}
  cuda_base_test:  {...}
binaries:
  uv:     {version: "<verify>", url: "<verify>", sha256: "<verify>"}
  restic: {version: "<verify>", url: "<verify>", sha256: "<verify>"}
  quarto: {version: "<verify>", url: "<verify>", sha256: "<verify>"}   # evaluator reports (HTML + Typst PDF)
python:
  ansible_core: "<verify>"
  python_version: "3.12"
ansible_collections: {community.general: "<verify>", community.docker: "<verify>", ansible.posix: "<verify>"}
incus_base_images:
  ubuntu-24.04: {remote: images, alias: "ubuntu/24.04/cloud", fingerprint: "<verify at build>"}
```

- `make lock-check`: schema validation, no `TODO(verify)`/`<verify>`, every container digest resolvable (`docker buildx imagetools inspect` or registry API), every binary URL+sha256 consistent (download to temp and hash), every apt key fingerprint matches.
- `make lock-update`: documented manual procedure (do not auto-bump).

### 10.2 Environment fingerprint

Generated by `pokus-lab fingerprint` and at every session start. Canonical JSON (sorted keys, no volatile fields) hashed with sha256. Include:

- host profile essentials: provider, environment class, hypervisor, OS id/version, kernel release, arch, support tier, overrides;
- hardware: CPU model, threads, memory, GPU name, VRAM, driver version, CUDA/compute capability, GPU path (`wsl-dxg`/`native`/`passthrough`);
- software: actual installed versions of Docker, containerd, NVIDIA toolkit, Incus, Tailscale, Python, ansible-core; running container image digests; golden image fingerprints by alias;
- config: `pokus-infra` commit + dirty flag, `versions.lock.yml` sha256, host_vars sha256, platform/catalog refs if enabled;
- WSL extras: `wsl.conf` effective values, kernel string (Windows build is unknowable from Linux with interop off — record `null` and document);
- `fingerprint_schema_version`.

Volatile fields (timestamps, boot_id) are recorded alongside but excluded from the hash.

Document in `docs/reproducibility.md` that latency/resource comparisons are valid only within the same fingerprint hash (or a documented compatible subset), while accuracy comparisons require identical model + serving profile + agent config.

---

## 11. Testing and quality

- **Preflight unit tests** (pytest; also runnable with plain `python3 -m unittest` since preflight is stdlib-only): fixture roots under `preflight/tests/fixtures/<name>/` containing fake `/etc/os-release`, `/proc/...`, `/sys/...`, `/dev` markers, and a `commands.json` mapping command lines to stdout/exit codes. Required fixtures, each with an expected outcome/provider/features assertion:
  - `ubuntu2404-baremetal-nvidia-driver-loaded` → supported, baremetal, gpu_inference, vm_targets, dcgm
  - `ubuntu2404-baremetal-nvidia-no-driver-secureboot` → needs_action (MOK)
  - `ubuntu2404-baremetal-no-gpu` → degraded (no gpu_inference)
  - `ubuntu2404-wsl2-gpu-nokvm` → degraded or supported per matrix (vm_targets off), provider wsl2
  - `ubuntu2404-wsl2-gpu-kvm` → supported, wsl2, vm_targets on
  - `ubuntu2404-wsl2-systemd-off` → needs_action
  - `ubuntu2404-wsl2-docker-desktop-integration` → unsupported (blocking conflict)
  - `ubuntu2404-wsl2-linux-nvidia-driver-installed` → unsupported (blocking conflict)
  - `wsl1` → unsupported
  - `ubuntu2404-kvm-vm-gpu-passthrough` → supported, vm, hypervisor kvm
  - `ubuntu2404-hyperv-vm-no-gpu` → degraded, vm, hypervisor microsoft
  - `ubuntu2404-vmware-vm-no-gpu` → degraded, vm, hypervisor vmware
  - `docker-container` → unsupported (container)
  - `systemd-detect-virt-reports-wsl` → must classify as wsl2, not container
  - `debian13-baremetal` → experimental tier
  - `fedora-baremetal-nvidia` → tested tier, family redhat, provider baremetal
  - `almalinux9-kvm-vm` → tested tier, family redhat, provider vm
  - `opensuse-tumbleweed-baremetal` → tested tier, family suse, release_model rolling (warning)
  - `arch-baremetal` → tested tier, family archlinux, rolling
  - `linuxmint22-baremetal` → experimental, family debian, parent release taken from `UBUNTU_CODENAME`
  - `manjaro-baremetal` → experimental, family archlinux
  - `oraclelinux9` → experimental, family redhat
  - `fedora-silverblue` → unsupported (immutable), remediation mentions the vm provider
  - `nixos` → unsupported (immutable)
  - `alpine` → unsupported (non-systemd, musl)
  - `devuan` → unsupported (non-systemd)
  - `rhel8-cgroup-v1` → needs_action (enable unified cgroup hierarchy kernel argument) or unsupported per matrix — decide and document
  - `fedora-selinux-enforcing-firewalld` → supported path with backend firewalld and mac selinux recorded
  - `ubuntu2404-ufw-active` → backend ufw recorded, no conflict
  - `ubuntu2404-aarch64-no-gpu` → experimental arch, degraded (no gpu_inference)
  - `ubuntu2404-no-btrfs-no-dmthin` → unsupported (no quota-capable storage)
  - `ubuntu2404-cgroup-v1` → unsupported
  - `ubuntu2404-low-memory` → unsupported (below required minimum)
  - conflicting-signal fixture → confidence `low` + warning
- Snapshot tests for the human-readable summary.
- Schema validation tests for every generated host profile.
- **Lint**: `shellcheck`, `shfmt -d`, `ruff`, `ruff format --check`, `mypy --strict` for preflight and CLI, `yamllint`, `ansible-lint` (production profile), JSON Schema validation of `support-matrix.yml` and `versions.lock.yml`.
- **Ansible**: `ansible-playbook --syntax-check` for all playbooks; check-mode tests for provider-agnostic roles (`base`, `users_groups`, `layout`, `ssh`) in a disposable test environment. Provide `make test-roles` that runs these roles inside a throwaway Incus system container or VM **only when explicitly invoked**, and document that hardware-dependent roles (GPU, Incus-in-Incus storage) are validated by doctor on real hosts.
- **Docs drift**: CI regenerates `docs/support-matrix.md` and the layout table and fails if they differ from committed versions.
- **CI** (GitHub Actions): all of the above except role tests that need privileged virtualization.

---

## 12. Integration contract with `pokus-platform` and `pokus-catalog`

Write this into `docs/contracts.md` and treat it as a versioned interface (`contract_version: 1`). Infra provides, platform consumes:

| Item | Value |
|---|---|
| Deployed platform path | `/srv/pokus/deploy/platform/current` (symlink to `<ref>`) |
| Deployed catalog path | `/srv/pokus/catalog/current` |
| Platform entry points expected | console scripts `pokus-orchestrator`, `pokus-agent-runner`, `pokus-llm-gateway`, `pokus-executor`, `pokus-injector`, `pokus-verifier`, `pokus-evaluator`, `pokus-reconcile`, `pokus-migrate`, CLI `pokus` |
| Units infra renders | `pokus-orchestrator.service`, `pokus-agent-runner.service`, `pokus-llm-gateway.service`, `pokus-executor.service`, `pokus-injector.service`, `pokus-verifier.service`, `pokus-reconcile.service` (oneshot, `Before=pokus-orchestrator.service`), `pokus-evaluator.service` (worker polling evaluation jobs). Users: orchestrator and reconcile run as `pokus-orch`, gateway as `pokus-gw`, the others as their namesake accounts |
| Sockets | as specified in the pokus-platform BUILD_PROMPT §4: orchestrator `api.sock` (group `pokus`) and `svc.sock` (group `pokus-svc`); executor `api.sock` (group `pokus-exec-clients`); gateway `agent.sock` (group `pokus-gw-clients`) and `svc.sock`; injector and verifier sockets reachable only by `pokus-orch`. The gateway additionally listens on `127.0.0.1:<port>` for researchers' ad-hoc use with per-user API keys |
| Postgres | socket dir `/run/pokus/postgres`, db `pokus`, roles per §7.8, password files `/etc/pokus/secrets/postgres/<role>` |
| Incus credentials | `/etc/pokus/incus/<svc>/{client.crt,client.key,server.crt}`; projects `bench`, `build` |
| Incus network conventions | per-run networks named `r-<id>`, bridge prefix for firewall compat, ACL `pokus-run-default` |
| Telemetry entry for targets | platform attaches an Incus proxy device to each target instance: `listen=tcp:127.0.0.1:4317` (inside instance) → `connect=tcp:127.0.0.1:4317` (host collector), `bind=instance`; for VMs a documented alternative (verify proxy device support for VMs; else an ACL-restricted route to a dedicated collector listener) |
| Telemetry identity | the platform sets `OTEL_RESOURCE_ATTRIBUTES=pokus.run_id=<ULID>,pokus.node=<alias>` in each target instance's environment at launch; the in-image OTel agent must honor this variable. `run_id` is opaque and carries no scenario information. Target profiles set the Incus guest API off (`security.guestapi=false`, verify the key name) so instance config cannot be read from inside. No scenario identifiers ever enter the instance. |
| OTLP for platform services | `127.0.0.1:4317` |
| vLLM deployment control | `/etc/pokus/vllm/current.env` + `systemctl restart pokus-inference.service`; group-writable by `pokus-orch` only via a narrow sudoers rule or a root-owned helper (`/usr/local/libexec/pokus/vllm-deploy <deployment-file>`) with argument validation — prefer the helper |
| Session records | `/var/lib/pokus/sessions/*.json` (read by platform) |
| Fingerprint | `pokus-lab fingerprint --json` (callable by `pokus-orch`) |
| Signing key | `/etc/pokus/signing/` readable only by `pokus-orch` |
| Golden images | aliases `<blueprint>/<version>` in Incus with properties `pokus.recipe_commit`, `pokus.built_at`, `pokus.base_fingerprint` |

---

## 13. Safety invariants (non-negotiable; put them in `CLAUDE.md`)

1. Nothing is ever formatted, wiped, or repartitioned unless `incus_pool_source: device` **and** `incus_pool_device_confirm` equals `incus_pool_device` exactly **and** the device is not mounted, not part of any existing filesystem/LVM/RAID signature (check with `lsblk -f`/`wipefs -n`) unless `incus_pool_device_force_wipe: true` is also set. Print the device's model/size/serial and require interactive confirmation unless `--non-interactive` with all vars set.
2. Never install a Linux NVIDIA kernel driver on WSL. Never modify anything on the Windows side. Nothing in the core may call Windows executables.
3. Never enable Tailscale Funnel. Never bind lab services to non-loopback interfaces (except sshd, firewalled).
4. Humans are never added to `docker` or `incus-admin` implicitly. The agent service account never gets Docker, Incus, catalog, artifacts, or secrets access.
5. Secrets never committed in plaintext, never logged (`no_log`), never baked into images.
6. Every external artifact pinned and verified (§10). No `latest` tags. No `curl | bash`.
7. No automatic reboots without `--allow-reboot`. No automatic kernel or driver upgrades.
8. Idempotent: second run reports `changed=0`.
9. Do not flush or rewrite firewall tables owned by Docker or Incus.
10. Hosts with `lab_mode: staging` use their own vault, `/srv/pokus`, Tailscale identity (QA tag) and signing key, and are never paired with or allowed to write to a production host. Infra installs QA trusted public keys into `/etc/pokus/qa/trusted-keys/` from `qa_trusted_keys` in group_vars, and provides `/etc/pokus/qa/attestations/` (root-owned, readable by `pokus-orch`) where a human places published QA attestations.
11. Every override (`--force-unsupported`, `--accept-degraded`, `--accept-experimental`, `--allow-dirty`, break-glass groups) is recorded in the host profile and environment fingerprint.
12. **No personal or installation-specific data in the repository.** No real names, usernames, e-mail addresses, organization or institution names, hostnames, domains, IP or MAC addresses, hardware serials/UUIDs, SSH keys, Tailscale identities, or anything else that identifies a person or a specific installation — in code, configuration, examples, fixtures, tests, documentation, screenshots or generated files. Use neutral placeholders: `researcher1`, `researcher2`, …; `example-org`; `example.com` / `example.org` / `example.net` (RFC 2606); documentation IP ranges `192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24`, `2001:db8::/32` (private RFC 1918 and CGNAT ranges are allowed only as technical defaults such as address pools); MACs from `00:00:5e:00:53:00/24` (RFC 7042); zeroed serials and UUIDs. Real deployment data (host_vars of real machines, `users/*.pub`, installer answers, vaults) belongs only in the deploying lab's own private fork or configuration repository: `.gitignore` excludes it from the public project, the installer's commit offer warns when the target remote is the public upstream, and fixtures captured from real hosts pass a documented sanitizer first. A CI **privacy scan** enforces this (e-mail addresses outside RFC 2606 domains, public IP addresses, MAC addresses outside the documentation range, SSH/PGP key material outside clearly marked test fixtures, and an optional maintainer-supplied denylist provided as a CI secret, never committed). This rule is about data tied to a **person or a specific installation**; it does **not** restrict references to public services, vendors and projects (GitHub, GitHub Actions, Docker Hub, Hugging Face, Tailscale, distribution mirrors, upstream documentation, model names) or to the Pokus project's own public repository and organization once it is published. Pokus is an open-source project meant to be deployed and customized by anyone, so every person- or installation-specific value is supplied by the deployer at deploy time and never baked into the project.

## 14. Code style and conventions

- Bash: `set -Eeuo pipefail`, functions with `local`, quoted expansions, `readonly` constants, a single logging helper (`log_info`, `log_warn`, `log_error`) with UTC timestamps.
- Python: type hints everywhere, dataclasses for report structures, no global state, pure detection functions taking `(fs, runner)`; `ruff` + `mypy --strict` clean.
- Ansible: FQCN modules, `name:` on every task, handlers for restarts, `block/rescue` for multi-step operations, no `ignore_errors: true` without justification comment, variables prefixed by role name in `defaults` (`incus_pool_size`, `docker_data_root`, ...).
- Templates carry a header: `# Managed by pokus-infra (<template path>). Local changes will be overwritten.`
- All timestamps UTC ISO-8601 with `Z`.
- Documentation in English; clear, concise, with copy-pasteable commands.

## 15. Documentation deliverables

Follow `docs/DOCS_SPEC.md`. The list below is the infra-specific minimum; the spec's required pages (operator tutorials and how-tos, troubleshooting by exit code and doctor check, generated references for CLI, Ansible variables via `meta/argument_specs.yml`, schemas, support matrix, versions, filesystem/accounts/sockets), page conventions, quality gates (strict build, docs coverage, drift check) and definition of done all apply.

- `README.md`: what this is, supported environments table (generated excerpt), prerequisites, quickstart (`git clone`, create host_vars, create vault, `sudo ./bootstrap/bootstrap.sh --machine <name>`), exit codes table, where to look when something fails.
- `docs/architecture.md`: trust zones (Windows/host OS boundary when relevant, lab host, control plane, benchmark sandbox, personal sandboxes), component diagram (Mermaid), data flows (telemetry path via proxy devices, executor path via Incus API), generated layout table.
- `docs/operations.md`: daily start/stop, adding/removing a researcher, rotating secrets and certs (`rotate.yml`), building and exporting golden images, adding a model, switching vLLM deployments manually (for debugging), backup/restore, **migrating to a new machine** (fresh build + restore; tailnet node replacement; signing key handover), troubleshooting by exit code.
- `docs/security.md`: threat model (misbehaving agent, buggy fault injection, compromised target, researcher mistakes), mitigations mapped to mechanisms, Tailscale ACL snippet, what is explicitly out of scope.
- `docs/reproducibility.md`: pinning policy, fingerprint, what makes two runs comparable, lock update procedure.
- `docs/providers/wsl2.md`: WSL specifics, `.wslconfig` recommendations (memory, processors, swap, `nestedVirtualization`, `localhostForwarding=false`), the optional `contrib/windows` keep-alive helper, `wsl --terminate` restart flow, known limitations (no DCGM, nested KVM caveats, Windows build not observable).
- `docs/providers/baremetal.md`: NVIDIA driver + Secure Boot/MOK, dedicated pool disk, power policies.
- `docs/providers/vm.md`: GPU passthrough requirements per hypervisor, nested virtualization, measurement caveats.
- ADRs: Incus for targets (vs Docker/VMs only), native Docker Engine (vs Docker Desktop), provider model, family-based portability with a data-driven component source matrix, uv-managed Python instead of distro Python, firewall backend integration instead of replacement, stdlib-only preflight, Postgres socket-only access, OTel-based telemetry, btrfs/LVM-thin requirement (no `dir` backend), no orchestration frameworks.

## 16. Implementation order (within this single build)

1. Repo skeleton, tooling (`Makefile`, linters, pre-commit, CI), `CLAUDE.md`, `docs/IMPLEMENTATION_NOTES.md` with open questions → **stop and ask me the open questions**.
2. Preflight: fs/runner abstractions, detectors, matrix evaluator, report + schema, CLI, all fixtures and tests.
3. `bootstrap.sh` with exit-code handling (Ansible stubs allowed temporarily).
4. `versions.lock.yml` populated with verified values + `make lock-check`.
5. Ansible: `platform_facts` (§19.1), the component source matrix and family vars, `preflight_gate`, providers, `base`, `mac`, `users_groups`, `ssh`, `layout`, `firewall` (all three backends), `tailscale` — implemented for **all four families from the start**, not retrofitted.
6. `docker`, `nvidia_container_toolkit`, `incus`.
7. `postgres`, `observability`, `opa`, `vllm`, `models`.
8. `images` with all blueprints, tests, and leakage lint.
9. `lab_session`, `backup`, `restore.yml`, `rotate.yml`.
10. `pokus-lab` CLI with doctor (incl. `--smoke`) and fingerprint.
11. `catalog` and `platform` roles against the contract (with `*_enabled: false` defaults).
12. `make test-distros` harness (§19.9) and the generated support matrix with test dates.
13. Installer TUI (§18): screens, validators, answers file, resume state, Ansible event callback, tests.
14. Documentation and ADRs; regenerate generated docs; final `make lint test lock-check`.

After each numbered step: write or update the documentation for what the step built (per `docs/DOCS_SPEC.md`), run `make lint test docs`, commit, and give me a short summary of what was built, what was verified, and what remains `TODO(verify)`.

## 17. Definition of done

- `make lint test lock-check` passes with zero `TODO(verify)` items (or each remaining one explicitly approved by me and listed).
- All preflight fixtures produce the expected outcome, provider, and features.
- `sudo ./bootstrap/bootstrap.sh --machine example-machine --check` runs end-to-end in check mode on an Ubuntu 24.04 test host without errors.
- `make test-distros` (§19.9) passes on every `supported` and `tested` distribution: full bootstrap without GPU, a second run with `changed=0`, and `pokus-lab doctor --smoke` exit 0.
- On a real supported host (run by me): a full bootstrap completes, a second run reports `changed=0`, and `pokus-lab doctor --smoke` exits 0.
- Every role has a README and `meta/argument_specs.yml`; every variable is documented; generated docs are in sync.
- `make docs` builds in strict mode with zero warnings; the docs coverage check reports 100 %; every infra page required by `docs/DOCS_SPEC.md` §A.3 exists, and every tutorial and how-to has `qa:run` markers on its executable blocks.
- `CLAUDE.md` captures the invariants so future sessions keep them.
- The TUI completes every flow in §18.9 in pilot tests; snapshot tests pass at 80×24 and 120×40; an install done through the TUI and a replay with `--answers` + `--non-interactive` produce byte-identical config files (excluding the encrypted vault) and the same Ansible result.
- On a real host: a fresh install through the TUI ends with vLLM serving the selected model and passing the tool-call verification.

---

## 18. Installer TUI (`installer/`)

### 18.1 Goals and principles

- A polished, keyboard-first terminal UI that works over SSH and inside WSL terminals, with an 80×24 minimum size, optional mouse support, `NO_COLOR` support, and a high-contrast theme.
- **Every input the lab needs is collected here.** Nothing later prompts on raw stdin: Ansible prompts are disabled, and things like the Tailscale login URL are shown inside the TUI.
- **Front-end only.** The TUI writes configuration files and invokes `pokus-preflight`, `ansible-playbook` (via bootstrap's own functions) and `pokus-lab doctor` exactly as a non-interactive run would.
- **Resumable.** Progress is persisted, so the install survives `wsl --terminate`, reboots, SSH disconnects and crashes, and continues at the right step when bootstrap is started again.
- **Safe.** Secrets are masked and never written to answers files or logs; destructive choices require typed confirmation; nothing is applied before the Review screen.
- **Re-runnable.** On an already installed host, the TUI opens in *modify* mode, pre-filled from the existing host_vars and answers file, and shows a diff of every file before applying.

### 18.2 Technology

- Python 3.12, **Textual** (pinned), **ruamel.yaml** (round-trip, preserves comments in host_vars) in a hash-locked uv environment under `/opt/pokus/installer-venv` (it may share a lockfile with the Ansible environment; decide and document).
- Launched by `bootstrap.sh` under sudo. Every file written into the repo checkout is chowned to `SUDO_UID:SUDO_GID`, so the repository never becomes root-owned.
- If the terminal is unsuitable (`TERM=dumb`, no TTY, too small), refuse to start the TUI and print how to use `--answers` mode instead.

### 18.3 Files the TUI writes

| File | Content | Committed to git? |
|---|---|---|
| `ansible/inventory/host_vars/<machine>.yml` | all non-secret machine settings | yes |
| `ansible/group_vars/all/main.yml` (researchers section only, round-trip edit) | lab-wide researcher list | yes |
| `users/<name>.pub` | researchers' SSH public keys | yes |
| `ansible/group_vars/all/vault.yml` | all secrets, Ansible Vault encrypted | yes (encrypted only) |
| `models.lock.yml` | selected models with pinned revisions and hashes | yes |
| `installer-answers/<machine>.yml` | every non-secret choice, for `--answers` replay | yes |
| `/var/lib/pokus/installer/state.json` | resume state: current screen, completed phases, timestamps (0600 root) | no |

At the end, offer to create a git commit of these files on branch `machine/<name>` with the invoking user as author. Never push automatically; remind the operator to push.

### 18.4 Screen flow

Every screen has Back / Next, inline validation messages next to the field, contextual help (F1), and a progress indicator ("step 6/15 · Storage"). Sections not applicable to the host (e.g. NVIDIA driver on WSL) are shown greyed out with a one-line explanation, not hidden silently.

1. **Welcome** — repo commit, ref, dirty flag, log file path, keyboard help. If `state.json` exists: "Resume previous install (step X, started at …)" or "Start over".
2. **Preflight** — runs the detector with a live spinner, then shows cards: Environment (class, hypervisor, confidence, expandable signals), OS & kernel, CPU/RAM/disk, GPU, Virtualization (KVM, nested), Storage backends, Network endpoints, Conflicts. An outcome banner on top. Actions: Re-check, Raw JSON, Continue / Abort.
   - `degraded` → consent screen listing each disabled feature with its research impact (e.g. "No KVM: VM-class scenarios (kernel-level faults) cannot run on this host"); one checkbox per feature.
   - experimental tier → consent screen.
   - `needs_action` → step-by-step remediation and a Re-check button.
   - `unsupported` → Abort, or "Force (recorded in every fingerprint)", which requires typing `FORCE UNSUPPORTED`.
3. **Install mode** — Fresh install / Restore from backup / Modify existing (offered only when an existing install is detected).
4. **Machine** — machine name (hostname rules, must be unique among host_vars), provider (read-only from preflight; override only if detection confidence is low), power policy (bare metal/VM only), `/srv/pokus` location (checks filesystem type and free space), Docker data root.
5. **Resources** — detected CPU/RAM. On WSL, explain that effective limits come from the Windows-side `.wslconfig` and capture the expected values. Suggest a cpuset split between lab services and targets with a visual bar; validate ranges and no overlap. Suggest Postgres memory.
6. **Storage** — backend from features (btrfs, or LVM-thin fallback). Source: *loop file* (size input with free-space check and a reserve) or *dedicated disk*: a device table (name, by-id path, model, size, serial, existing signatures, mount points). Mounted or system disks are disabled. Selecting a disk opens a red danger dialog that requires typing the full by-id path; if signatures exist, an extra `WIPE` confirmation. Writes `incus_pool_device` and `incus_pool_device_confirm`.
7. **Researchers** — table with add/edit/remove. Fields: username (Linux username rules, not reserved, no clash with service accounts), display name (Grafana), admin flag (at least one admin required), SSH public keys (paste multi-line or file path; validated with `ssh-keygen -lf`; private keys rejected explicitly; fingerprints shown), Grafana account toggle. Removed users go to `researchers_removed`. Warn if the invoking user is not in the list.
8. **Remote access** — Tailscale hostname (default: machine name), tag (default `tag:pokus-lab`), login method (masked auth key, or interactive login whose URL and QR code are shown during install), accept-DNS toggle (default off), Grafana via `tailscale serve` toggle. Extra SSH interfaces: multi-select of detected NICs (disabled on WSL with an explanation). Show the required tailnet ACL snippet with a copy action.
9. **GPU & inference** (only if a GPU is present)
   - Bare metal/VM: install NVIDIA driver? Branch selection (options from `versions.lock.yml`). If Secure Boot is on, explain the MOK flow and ask for a one-time MOK password (masked, entered twice), passed only to `mokutil --import` via stdin and never stored.
   - vLLM serving profile, auto-selected from GPU name and VRAM, overridable.
   - Default model: table from `models.catalog.yml` filtered by profile fit (VRAM, license, gated, tool-call support), plus "custom (HF repo + 40-hex revision)" and "none".
   - Hugging Face token if a gated model is chosen (masked; validated with the HF `whoami` API when the network is available).
   - Toggle: start serving after install (default on).
10. **Data & backups** — restic repository type: SFTP (host on the tailnet, user, path), REST server (URL), local path (warned: not a real backup), or "configure later" (doctor will warn). A *Test connection* action. restic password: generate (shown once; the operator must tick "I stored this safely") or enter. Schedule time, retention (daily/weekly/monthly), include model weights toggle, include secrets toggle (with warning).
11. **Restore** (restore mode only) — list snapshots from the configured repository (time, host, size), pick one, preview what will be restored.
12. **Platform & catalog** — enable each; repo URL; ref (tag); authentication: generate a deploy key (show the public key to add to the Git host), use an existing key path, or masked HTTPS token; require signed tags toggle; *Test access* action (`git ls-remote`).
13. **Golden images** — checklist of blueprints with estimated build time and disk usage; build now or later; VM variants offered only if `vm_targets`.
14. **Secrets & vault** — all service passwords generated automatically by default (listed by name, values not shown); Grafana admin password generate/enter; vault password entered twice with a strength meter, or a path to an existing vault password file (must be 0600). Optional root-only storage of the vault password in `/etc/pokus/secrets/vault-pass`, with the trade-off explained.
15. **Review** — every setting grouped by section, each with a jump-back link; a diff view for modified files; the list of files to be written; estimates for downloads (container images, model GB), disk usage and duration; destructive actions highlighted in red. Buttons: Back, Export answers only, Install.
16. **Install progress** — a phase tree (preflight gate → provider → base → … → doctor) with per-role status icons, current task, elapsed time, and ok/changed/failed counts; a collapsible live log pane (filtered; full log path shown); the Tailscale login URL and QR code when needed.
    - On failure: failing task, stderr excerpt, remediation hint, buttons Retry phase / Open log / Abort.
    - On signal 75 (WSL restart): a full-screen instruction — "In Windows PowerShell run `wsl --terminate <distro>`, then open WSL and run `sudo ./bootstrap/bootstrap.sh` again; the installer resumes here."
    - On signal 76 (reboot / MOK): reboot instructions including the MOK Manager steps.
    - State is saved *before* these screens are shown.
17. **Doctor** — results grouped by category with pass/warn/fail and remediation; an optional *Run smoke test* action.
18. **Finish** — connection info per researcher (`ssh <user>@<tailscale-hostname>`), how to reach Grafana, the vLLM endpoint and the model being served, files to commit (with the commit offer), log locations, how to start/stop the lab.

### 18.5 Ansible progress integration

- A custom callback plugin `ansible/callback_plugins/pokus_events.py` emits JSON lines (play start, role start, task start/end with status, changed flag and duration, warnings, failures with message and truncated stderr) to `/run/pokus/installer/events.jsonl` or a passed file descriptor. It must respect `no_log`: never emit args or results of `no_log` tasks.
- The regular human-readable output still goes to the bootstrap log file.
- Bootstrap signals (§5.2 step 9) also appear as events.
- Non-TUI runs may enable the same plugin (useful for CI logs).

### 18.6 Answers file

- Schema: `installer/schema/answers.schema.json`. Contains every non-secret choice, including *which* secrets are generated versus provided, but never their values.
- Replay: `sudo ./bootstrap/bootstrap.sh --answers installer-answers/<machine>.yml --non-interactive --vault-password-file <file>` reproduces the install.
- For a first non-interactive install with no vault yet, secrets come from environment variables `POKUS_SECRET_<NAME>` or are generated and written into a new vault whose password comes from `--vault-password-file`.

### 18.7 Secret handling

- Masked inputs (paste allowed), never echoed, never logged; a log redaction filter acts as a second line of defense.
- Secrets live in memory only until written into the vault through Ansible's vault API. Generated secrets use `secrets.token_urlsafe`.
- "Shown once" secrets require an explicit acknowledgement; the screen is cleared afterwards. Warn about terminal scrollback and tmux logging.
- The MOK password is passed to `mokutil` via stdin only.

### 18.8 Validation

All rules live in `validators.py` and are unit-tested: hostname, Linux username, absolute paths, cpusets, sizes, URLs, SSH public keys, HF repo IDs and 40-hex revisions, git refs, restic repository URLs, Tailscale tags.

### 18.9 Tests

- Textual pilot tests (`App.run_test()`) for complete flows: fresh WSL2 with GPU; bare metal with a dedicated pool disk; degraded host without GPU; restore mode; modify mode; validation error paths; resume after a simulated signal 75.
- Snapshot tests (pytest-textual-snapshot) for every screen at 80×24 and 120×40, dark and light themes.
- Runner tests using fake preflight JSON and a recorded `events.jsonl` replay.
- Answers round-trip: answers → files → reload → identical answers.

### 18.10 Look and feel (guidance, not a pixel spec)

```
┌ Pokus Lab Installer ────────────────────────── step 6/15 · Storage ┐
│ Target sandbox storage pool        backend: btrfs (quota-capable ✔) │
│                                                                      │
│  ( ) Loop file inside this system     size: [ 300 ] GiB  free: 812   │
│  (•) Dedicated disk                                                  │
│      ┌──────────────────────────────────────────────────────────┐   │
│      │ ▸ nvme1n1  Samsung 990 PRO  1.8 TiB  no signatures       │   │
│      │   nvme0n1  WD SN850X        1.8 TiB  ext4, mounted /  ✖  │   │
│      └──────────────────────────────────────────────────────────┘   │
│  ⚠ All data on nvme1n1 will be destroyed.                            │
│    Type the device path to confirm: [/dev/disk/by-id/nvme-Sams…]     │
│                                                                      │
│ [◂ Back]                                    [F1 Help]   [Next ▸]     │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 19. Portability across distributions, environments and hardware

Goal: the lab installs and behaves the same on any mainstream **systemd + glibc** Linux distribution, on bare metal, VMs and WSL2, with or without a GPU. Where something cannot be made identical, the difference is detected, explained, and recorded in the fingerprint — never silently ignored.

### 19.1 Where distro differences are allowed to live

Only in these places:

1. `preflight/support-matrix.yml` (which distros, which tier).
2. `ansible/component-sources.yml` — the **component source matrix** (§19.4).
3. `ansible/roles/<role>/vars/<family>.yml` — package names, service names, file paths, tool names.
4. Small family-specific task files `tasks/<family>.yml` **only** when a module differs (e.g. repository setup); everything else is shared tasks.

A `platform_facts` role runs first and sets: `pokus_family`, `pokus_distro`, `pokus_release`, `pokus_parent_release` (for derivatives), `pokus_release_model`, `pokus_pkg_manager`, `pokus_service_names` (ssh, time sync, …), `pokus_initramfs_tool`, `pokus_firewall_backend`, `pokus_mac` (`selinux` / `apparmor` / `none`), `pokus_version_hold_method`. No other role may branch on `ansible_distribution` directly — a lint rule (custom ansible-lint rule or grep check in CI) enforces this.

Everything else is distro-independent by construction:

- Lab services run as **containers pinned by digest** — identical on every host.
- Platform services run in the **uv-managed Python environment** — identical on every host.
- **Target environments are Incus instances built from our own golden images** — the userland of a target is the same no matter which distro the host runs. Only the host kernel differs, and it is recorded in the fingerprint.

### 19.2 Supported matrix (summary; the YAML in §2.3 is authoritative)

| Tier | Distributions | Meaning |
|---|---|---|
| supported | Ubuntu 24.04 LTS, Debian 13 | Full CI VM tests + reference GPU hardware |
| tested | Ubuntu 22.04, Debian 12, Fedora (current and previous), RHEL / AlmaLinux / Rocky / CentOS Stream 9 and 10, openSUSE Leap 15.6 / 16, openSUSE Tumbleweed, Arch Linux | Full CI VM tests (`make test-distros`); GPU paths not hardware-tested |
| experimental | Derivatives via `ID_LIKE` (Linux Mint, Pop!_OS, Zorin, elementary, Kali, Oracle Linux, Manjaro, EndeavourOS, …), SLES, `aarch64` hosts | Uses the parent family; consent required |
| unsupported | Non-systemd (Alpine, Devuan, Void, Gentoo/OpenRC, Slackware), musl, image-based/immutable (Fedora Atomic/CoreOS, openSUSE MicroOS/Aeon, Ubuntu Core, NixOS), containers, WSL1, other architectures | Clear message + the **VM escape hatch**: run a supported distro in a VM on that host (`vm` provider; GPU via passthrough if available) |

Verify every listed release at build time (EOL status, availability of Docker, Incus, NVIDIA toolkit, Tailscale) and adjust tiers in the matrix, telling me what changed and why.

### 19.3 Python independence

- Bootstrap installs **uv** (static binary, checksum from `versions.lock.yml`) and then a **pinned CPython** via `uv python install` into `/opt/pokus/python`. Ansible, the TUI, `pokus-lab` and the platform use it. The distro's Python is never required for our code.
- Ansible package modules that need distro bindings (apt, dnf) rely on ansible-core's respawn-to-system-interpreter mechanism; verify it per family. Where it is unreliable, implement a small `pkg` abstraction (in `roles/pkg`) that calls the package manager CLI with `argv` and proper `changed_when` logic. Minimal hosts without a system Python (some RHEL minimal images expose only `platform-python`) must work.

### 19.4 Component source matrix (`ansible/component-sources.yml`)

Declarative mapping, per component and per distro/release (with family defaults), of **where** a component comes from and **how** to pin it:

```yaml
docker:
  default: {source: distro}
  ubuntu:   {source: vendor_repo, repo: docker-deb,  packages: [docker-ce, docker-ce-cli, containerd.io, docker-compose-plugin, docker-buildx-plugin]}
  debian:   {source: vendor_repo, repo: docker-deb,  packages: [...]}
  fedora:   {source: vendor_repo, repo: docker-rpm-fedora, packages: [...]}
  rhel_like:{source: vendor_repo, repo: docker-rpm-centos, packages: [...]}
  opensuse: {source: distro, packages: [docker, docker-compose, docker-buildx]}   # verify names
  arch:     {source: distro, packages: [docker, docker-compose, docker-buildx]}
incus:        {...}   # distro | zabbly | epel/copr | obs | distro(arch)
nvidia_ctk:   {...}   # nvidia deb/rpm/zypper repos | distro(arch)
tailscale:    {...}
nvidia_driver:{...}   # see §19.8
```

Rules:

- Every `vendor_repo` entry references a repository definition with base URL template (using the **parent** release identity for derivatives), key URL, and key fingerprint from `versions.lock.yml`.
- Keys are fetched, fingerprint-checked, then installed the family's way: `signed-by` keyrings (apt), `rpm --import` + `gpgkey=`/`gpgcheck=1`/`repo_gpgcheck` where supported (dnf), explicit `rpm --import` before `zypper addrepo` (zypper). Arch uses the distro keyring only; AUR is **never** used.
- Third-party repos that are not from the upstream vendor or the distro itself (COPR, OBS home projects, RPM Fusion) require explicit operator consent in the TUI / `--allow-community-repos`, and are recorded in the fingerprint.
- `versions.lock.yml` pins package versions **per distro release** (`packages.<component>.<distro>-<release>`). For `rolling` distros, pinning is replaced by a hold after install plus mandatory recording of actual versions (§19.5).

### 19.5 Updates, holds and rolling releases

| Family | Version hold | Automatic security updates (optional, default on for fixed releases) |
|---|---|---|
| debian | apt preferences pins in `/etc/apt/preferences.d/pokus-pins` | `unattended-upgrades`, security origin only, no reboot |
| redhat | `dnf versionlock` (plugin installed by the role) | `dnf-automatic` with `upgrade_type = security`, no reboot |
| suse | `zypper addlock` | systemd timer running `zypper --non-interactive patch --category security` (verify), no reboot |
| archlinux | `IgnorePkg` in a managed `pacman.conf` include | none — rolling updates are manual via `pokus-lab update-host` |

- `pokus-lab update-host` (all families): drains the lab, updates the OS with the family's tool while keeping holds, runs bootstrap (idempotent), runs doctor, and prints the fingerprint diff. This is the only supported way to update a host.
- On `rolling` distros, doctor warns when the running kernel or held components drift from the session fingerprint, and reports must show the fingerprint diff when a campaign spans an update.

### 19.6 Firewall backends

Selected automatically by `platform_facts`: `firewalld` if active, else `ufw` if active, else `nftables`. Override with `firewall_backend` in host_vars. Required effective policy (identical for all backends, verified by doctor with connection tests, not by reading rules):

- Inbound SSH allowed only on `tailscale0` (+ `ssh_extra_allowed_interfaces`).
- Grafana serve port allowed only on `tailscale0` if enabled.
- Incus bridges: DHCP/DNS from instances to the host's Incus dnsmasq; forwarding between instances inside the same run network; no forwarding from run networks to anything else.
- Docker/Incus coexistence fixed (`DOCKER-USER` acceptance for Incus bridges, or the backend's equivalent).

Backend specifics:

- `firewalld`: a dedicated zone `pokus-tailnet` for `tailscale0` with the ssh service; Incus bridges placed in the `trusted` zone as Incus documents for firewalld hosts (verify); Docker's own `docker` zone left intact.
- `ufw`: `ufw allow in on tailscale0 to any port 22 proto tcp`; route/allow rules for Incus bridges as Incus documents for ufw (verify); Docker coexistence noted.
- `nftables`: own `inet pokus_filter` table via `pokus-nftables.service`.

### 19.7 Mandatory access control (`mac` role)

- **SELinux** (Fedora, RHEL-compatible, possibly openSUSE): never set permissive or disabled. Label `/srv/pokus` paths with persistent `semanage fcontext` rules + `restorecon`; bind mounts into containers use `:z`/`:Z` (shared vs private) correctly; enable Docker's `selinux-enabled`; install required policy packages (`container-selinux` etc., per family vars). Verify Incus's behavior on SELinux-enforcing hosts and document limitations. Doctor's smoke test fails on new AVC denials attributed to Pokus components during the test window.
- **AppArmor** (Ubuntu, Debian, openSUSE Leap): rely on Docker's and Incus's own profiles; nothing to disable.
- The detected MAC system and mode are part of the fingerprint.

### 19.8 GPU portability

- **Driver installation methods by family** (`install_nvidia_driver: auto`): Ubuntu — pinned driver branch packages; Debian — `nvidia-driver` from non-free per release docs; Fedora — RPM Fusion `akmod-nvidia` (community repo consent, akmods Secure Boot key enrollment); RHEL-compatible — NVIDIA's official repository with precompiled or DKMS modules (verify current recommendation); openSUSE — NVIDIA's official repository (open kernel module packages; verify naming); Arch — `nvidia-open` or `nvidia-open-dkms`. Only Ubuntu and Debian `auto` installs are `supported`; all others are `tested`/`experimental` and the TUI recommends `manual` if the operator prefers the distro's own documented procedure.
- **Secure Boot:** MOK flow (Ubuntu, Debian, openSUSE, RHEL-compatible with DKMS), akmods key flow (Fedora), manual `sbctl` signing on Arch (`needs_action` with docs). Never disable Secure Boot.
- **Container GPU access:** CDI first (§7.6), identical on all distros and on WSL2.
- **GPU vendors:** NVIDIA is implemented. The `gpu` detector and the `vllm` role are written behind a `gpu_backend` interface (`nvidia`, `none`) with a documented, unimplemented slot for `amd` (ROCm vLLM image, `/dev/kfd` + `/dev/dri`). AMD GPUs are reported by preflight as present-but-unsupported.
- **No GPU at all:** the lab installs fully (degraded: no local inference). See §19.10 for using a GPU elsewhere.

### 19.9 Proving portability: `make test-distros`

- A harness that launches **disposable Incus VMs** (on a developer machine or the lab host, explicitly invoked, never in the middle of experiments) for every `supported` and `tested` distro image, copies the repo in, runs `bootstrap.sh --non-interactive --answers tests/answers/ci.yml --accept-degraded`, then runs bootstrap a second time (must be `changed=0`), then `pokus-lab doctor --smoke` (nested Incus containers inside the VM), and collects logs and the host profile.
- Results are written to `docs/support-matrix.md` (generated) with the test date, distro release, and pass/fail per stage. A distro whose last test failed or is older than 90 days is shown as such.
- Variants: one VM per firewall backend (nftables, firewalld, ufw), one SELinux-enforcing run (Fedora + Alma), one derivative (if an image is available).
- Fast lane for CI without nested virtualization: `ansible-playbook --check` against systemd-enabled containers per family for the provider-agnostic roles, plus all preflight fixtures.

### 19.10 Hardware portability: split inference (`node_role`)

To make the lab usable on machines without a suitable GPU, a host can take one of three roles (`node_role` in host_vars):

- `full` (default): everything on one host.
- `lab`: everything except vLLM. The platform's LLM gateway points to a remote inference node over the tailnet.
- `inference`: only the inference stack — vLLM, GPU exporters, OTel forwarding to the lab node, and a small **inference control endpoint** (`pokus-inference-control`, bound to `tailscale0`, mutual TLS with certificates generated on both nodes during pairing, plus a tailnet ACL rule) that exposes exactly: activate deployment (by spec ID from the shared `models.lock.yml`/deployment specs), health, served model, GPU/vLLM metrics passthrough, verification call. Nothing else.

Rules:

- The TUI offers pairing: on the inference node it displays a one-time pairing code; on the lab node the operator enters it; both exchange certificates over the tailnet.
- Model weights live on the inference node; registry hashes are verified there and reported back.
- Both nodes' fingerprints are recorded for every run (`inference_fingerprint_hash`), and reports never compare latency across different inference placements.
- Still no external APIs: an inference node must be a Pokus-provisioned machine you control.

### 19.11 Portability documentation

- `docs/distros/<family>.md` for each family: sources used, holds, update policy, firewall and MAC notes, known issues, NVIDIA driver notes.
- `docs/distros/adding-a-family.md`: the exact checklist to add a new family (support-matrix entry, component sources, family vars for every role, fixtures, test-distros image).
- `docs/node-roles.md`: full / lab / inference, pairing, and measurement caveats.
